"""
redeem.py - Polymarket Gasless Redemption via Builder Relay

Sets ERC1155/ERC20 approvals through the relayer (gasless, one-time),
then redeems via poly-web3.

Required env vars:
  - BUILDER_API_KEY, BUILDER_SECRET, BUILDER_PASSPHRASE
"""

import os
import logging

logger = logging.getLogger("redeem")

RELAYER_URL = "https://relayer-v2.polymarket.com"

# Contract addresses on Polygon
CTF_CONTRACT = "0x4D97DCd97eC945f40cF65F87097ACe5EA0476045"
USDC_E = "0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174"
CTF_EXCHANGE = "0x4bFb41d5B3570DeFd03C39a9A4D8dE6Bd8B8982E"
NEG_RISK_CTF_EXCHANGE = "0xC5d563A36AE78145C45a50134d48A1215220f80a"
NEG_RISK_ADAPTER = "0xd91E80cF2E7be2e162c6513ceD06f1dD0dA35296"

MAX_UINT256 = "0x" + "f" * 64

# Track which wallets we've already set approvals for (per bot session)
_approved_wallets = set()


def _build_erc1155_approval_calldata(operator: str) -> str:
    """Encode setApprovalForAll(operator, true) for ERC1155."""
    selector = "a22cb465"
    addr = operator.lower().replace("0x", "").zfill(64)
    true_val = "0" * 63 + "1"
    return "0x" + selector + addr + true_val


def _build_erc20_approve_calldata(spender: str) -> str:
    """Encode approve(spender, MAX_UINT256) for ERC20."""
    selector = "095ea7b3"
    addr = spender.lower().replace("0x", "").zfill(64)
    amount = "f" * 64  # max uint256
    return "0x" + selector + addr + amount


def _init_clients(private_key: str, funder_address: str):
    """Initialize CLOB client, Relay client, and PolyWeb3Service."""
    from py_clob_client.client import ClobClient
    from py_builder_relayer_client.client import RelayClient
    from py_builder_signing_sdk.config import BuilderConfig
    from py_builder_signing_sdk.sdk_types import BuilderApiKeyCreds
    from poly_web3 import PolyWeb3Service

    builder_key = os.getenv("BUILDER_API_KEY", "")
    builder_secret = os.getenv("BUILDER_SECRET", "")
    builder_passphrase = os.getenv("BUILDER_PASSPHRASE", "")

    if not all([builder_key, builder_secret, builder_passphrase]):
        raise RuntimeError(
            "Missing Builder credentials. Set BUILDER_API_KEY, BUILDER_SECRET, "
            "BUILDER_PASSPHRASE in your Railway environment variables."
        )

    builder_config = BuilderConfig(
        local_builder_creds=BuilderApiKeyCreds(
            key=builder_key,
            secret=builder_secret,
            passphrase=builder_passphrase,
        )
    )

    clob_client = ClobClient(
        host="https://clob.polymarket.com",
        key=private_key,
        chain_id=137,
        signature_type=1,
        funder=funder_address,
    )
    clob_client.set_api_creds(clob_client.create_or_derive_api_creds())

    relay_client = RelayClient(
        RELAYER_URL,
        137,
        private_key,
        builder_config,
    )

    service = PolyWeb3Service(
        clob_client=clob_client,
        relayer_client=relay_client,
    )

    return clob_client, relay_client, service


def _ensure_approvals(relay_client, funder_address: str):
    """
    Set all ERC1155 + ERC20 approvals via the relayer (gasless).
    Only runs once per wallet per bot session.
    """
    if funder_address in _approved_wallets:
        return

    logger.info("Setting token approvals for %s...", funder_address[:16])

    # ERC1155 approvals: CTF contract approves operators
    erc1155_operators = [CTF_EXCHANGE, NEG_RISK_CTF_EXCHANGE, NEG_RISK_ADAPTER]

    # ERC20 approvals: USDC.e approves spenders
    erc20_spenders = [CTF_CONTRACT, CTF_EXCHANGE, NEG_RISK_CTF_EXCHANGE, NEG_RISK_ADAPTER]

    transactions = []

    # Build ERC1155 setApprovalForAll transactions
    for operator in erc1155_operators:
        transactions.append({
            "to": CTF_CONTRACT,
            "data": _build_erc1155_approval_calldata(operator),
            "value": "0",
        })

    # Build ERC20 approve transactions
    for spender in erc20_spenders:
        transactions.append({
            "to": USDC_E,
            "data": _build_erc20_approve_calldata(spender),
            "value": "0",
        })

    try:
        # Submit all approvals in one batched relay transaction
        response = relay_client.execute(transactions, "Set token approvals")
        logger.info("Approval tx submitted: %s", str(response)[:200])

        # Wait for confirmation if the response has a wait method
        if hasattr(response, 'wait'):
            receipt = response.wait()
            logger.info("Approval tx confirmed: %s", str(receipt)[:200])

        _approved_wallets.add(funder_address)
        logger.info("All approvals set for %s", funder_address[:16])

    except Exception as e:
        error_msg = str(e)
        # If approvals are already set, the tx might still succeed or
        # we might get a "already approved" type response - that's fine
        if "already" in error_msg.lower() or "noop" in error_msg.lower():
            logger.info("Approvals already set for %s", funder_address[:16])
            _approved_wallets.add(funder_address)
        else:
            logger.error("Failed to set approvals: %s", error_msg[:300])
            raise


async def redeem_position(
    private_key: str,
    funder_address: str,
    condition_id: str,
    token_id: str,
    size: float,
    neg_risk: bool = False,
) -> dict:
    """Redeem a resolved Polymarket position via Builder Relay (gasless)."""
    result = {"success": False, "error": None, "tx_hash": None}

    try:
        clob_client, relay_client, service = _init_clients(private_key, funder_address)

        # Set approvals first (one-time, gasless via relayer)
        _ensure_approvals(relay_client, funder_address)

        logger.info(
            "Redeem: condition=%s..., neg_risk=%s, size=%s",
            condition_id[:16], neg_risk, size,
        )

        # Check resolution
        try:
            if not service.is_condition_resolved(condition_id):
                result["error"] = "Condition not yet resolved."
                return result
        except Exception as e:
            logger.warning("Resolution check failed: %s", str(e)[:100])

        # Redeem - poly-web3 handles neg_risk/amounts internally
        redeem_result = service.redeem(condition_ids=condition_id)

        logger.info("Redeem raw result: %s", str(redeem_result)[:300])

        if redeem_result:
            tx_hash = None
            if isinstance(redeem_result, list) and redeem_result:
                first = redeem_result[0]
                if isinstance(first, dict):
                    tx_hash = (
                        first.get("transactionHash")
                        or first.get("transaction_hash")
                        or first.get("hash")
                        or first.get("tx_hash")
                    )
            elif isinstance(redeem_result, dict):
                tx_hash = (
                    redeem_result.get("transactionHash")
                    or redeem_result.get("transaction_hash")
                    or redeem_result.get("hash")
                    or redeem_result.get("tx_hash")
                )
            elif isinstance(redeem_result, str):
                tx_hash = redeem_result

            result["success"] = True
            result["tx_hash"] = tx_hash
            logger.info("Redeem SUCCESS: tx=%s", tx_hash)
        else:
            result["error"] = "Relayer returned empty response."
            logger.error("Redeem: empty result")

    except ImportError as e:
        logger.error("Redeem import error: %s", e)
        result["error"] = "Missing package: poly-web3 or dependencies"

    except Exception as e:
        error_msg = str(e)
        logger.error("Redeem FAILED: %s", error_msg)

        if "429" in error_msg or "quota exceeded" in error_msg.lower():
            result["error"] = "Rate limited. Try again in a few minutes."
        elif "operator approval" in error_msg.lower():
            # Approvals didn't take effect yet - clear cache so we retry
            _approved_wallets.discard(funder_address)
            result["error"] = "Approval tx may still be confirming. Try again in ~30 seconds."
        elif "revert" in error_msg.lower():
            result["error"] = "Transaction reverted: " + error_msg[:150]
        else:
            result["error"] = "Redemption failed: " + error_msg[:200]

    return result
