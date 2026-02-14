"""
redeem.py - Polymarket Gasless Redemption via Builder Relay

Uses the Safe wallet pattern — all transactions go through the relayer.
No POL/MATIC needed for gas. Approvals were set during wallet onboarding.

Standard markets: CTF.redeemPositions(collateral, parent, condition, indexSets)
Neg-risk markets: NegRiskAdapter.redeemPositions(conditionId, amounts[])
"""

import os
import logging

logger = logging.getLogger("redeem")

CTF_CONTRACT = "0x4D97DCd97eC945f40cF65F87097ACe5EA0476045"
USDC_E = "0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174"
NEG_RISK_ADAPTER = "0xd91E80cF2E7be2e162c6513ceD06f1dD0dA35296"


def _get_relay_client(private_key: str):
    """Create a RelayClient with Builder credentials."""
    from py_builder_relayer_client.client import RelayClient
    from py_builder_signing_sdk.config import BuilderConfig
    from py_builder_signing_sdk.sdk_types import BuilderApiKeyCreds

    key = os.getenv("BUILDER_API_KEY", "")
    secret = os.getenv("BUILDER_SECRET", "")
    passphrase = os.getenv("BUILDER_PASSPHRASE", "")

    if not all([key, secret, passphrase]):
        raise RuntimeError("Missing Builder credentials (BUILDER_API_KEY/SECRET/PASSPHRASE)")

    config = BuilderConfig(
        local_builder_creds=BuilderApiKeyCreds(
            key=key, secret=secret, passphrase=passphrase,
        )
    )
    return RelayClient("https://relayer-v2.polymarket.com", 137, private_key, config)


def _encode_standard_redeem(condition_id: str) -> dict:
    """
    Encode CTF.redeemPositions(address collateral, bytes32 parent, bytes32 condition, uint256[] indexSets)
    """
    # redeemPositions(address,bytes32,bytes32,uint256[])
    selector = "5d1a3ffd"
    collateral = USDC_E.lower().replace("0x", "").zfill(64)
    parent = "0" * 64  # bytes32(0)
    cond = condition_id.lower().replace("0x", "").zfill(64)
    # Dynamic array offset: 4 params * 32 bytes = 128 = 0x80
    offset = "0" * 62 + "80"
    arr_len = "0" * 63 + "2"
    el_0 = "0" * 63 + "1"  # indexSet 1 (outcome 0)
    el_1 = "0" * 63 + "2"  # indexSet 2 (outcome 1)

    return {
        "to": CTF_CONTRACT,
        "data": "0x" + selector + collateral + parent + cond + offset + arr_len + el_0 + el_1,
        "value": "0",
    }


def _encode_neg_risk_redeem(condition_id: str) -> dict:
    """
    Encode NegRiskAdapter.redeemPositions(bytes32 conditionId, uint256[] amounts)
    Empty amounts array = redeem all.
    """
    # redeemPositions(bytes32,uint256[])
    selector = "ab0bcc41"
    cond = condition_id.lower().replace("0x", "").zfill(64)
    # Dynamic array offset: 2 params * 32 bytes = 64 = 0x40
    offset = "0" * 62 + "40"
    arr_len = "0" * 64  # length 0 = redeem all

    return {
        "to": NEG_RISK_ADAPTER,
        "data": "0x" + selector + cond + offset + arr_len,
        "value": "0",
    }


async def redeem_position(
    private_key: str,
    funder_address: str,
    condition_id: str,
    token_id: str,
    size: float,
    neg_risk: bool = False,
) -> dict:
    """
    Redeem a resolved Polymarket position via Builder Relay (gasless).
    
    The Safe wallet already has approvals set from onboarding.
    We encode the redeem calldata and submit through relay.execute().
    """
    result = {"success": False, "error": None, "tx_hash": None}

    try:
        relay = _get_relay_client(private_key)

        logger.info(
            "Redeem: condition=%s, neg_risk=%s, size=%s, safe=%s",
            condition_id[:16], neg_risk, size,
            (funder_address or "?")[:16],
        )

        # Encode the redeem transaction
        if neg_risk:
            tx = _encode_neg_risk_redeem(condition_id)
        else:
            tx = _encode_standard_redeem(condition_id)

        # Submit via relayer (gasless!)
        response = relay.execute([tx], "Redeem positions")

        # Wait for confirmation
        tx_result = response.wait() if hasattr(response, 'wait') else response

        # Extract tx hash from result
        tx_hash = None
        if tx_result:
            if isinstance(tx_result, dict):
                tx_hash = (
                    tx_result.get("transactionHash")
                    or tx_result.get("transaction_hash")
                    or tx_result.get("hash")
                )
                state = tx_result.get("state", "")
                if "FAILED" in str(state).upper() or "INVALID" in str(state).upper():
                    result["error"] = f"Relayer tx failed: {state}"
                    logger.error("Redeem failed state: %s", state)
                    return result
            elif hasattr(tx_result, 'transactionHash'):
                tx_hash = tx_result.transactionHash
            elif hasattr(tx_result, 'transaction_hash'):
                tx_hash = tx_result.transaction_hash
            elif isinstance(tx_result, str):
                tx_hash = tx_result

        if tx_hash or tx_result:
            result["success"] = True
            result["tx_hash"] = tx_hash
            logger.info("Redeem SUCCESS: tx=%s", tx_hash)
        else:
            result["error"] = "Relayer returned empty response."
            logger.error("Redeem: empty result")

    except ImportError as e:
        logger.error("Redeem import error: %s", e)
        result["error"] = "Missing package: py-builder-relayer-client"

    except Exception as e:
        error_msg = str(e)
        logger.error("Redeem FAILED: %s", error_msg[:300])

        if "429" in error_msg or "rate" in error_msg.lower():
            result["error"] = "Rate limited. Try again in a few minutes."
        elif "operator approval" in error_msg.lower():
            result["error"] = "Token approvals needed. Re-run wallet setup."
            try:
                import wallet as w_mod
                w_mod.set_approvals(private_key)
            except Exception:
                pass
        elif "revert" in error_msg.lower():
            result["error"] = "Transaction reverted. Market may not be fully resolved."
        elif "not deployed" in error_msg.lower():
            result["error"] = "Safe wallet not deployed. Contact support."
        else:
            result["error"] = f"Redemption failed: {error_msg[:200]}"

    return result
