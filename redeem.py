"""
redeem.py - Polymarket Gasless Redemption via Builder Relay

Uses poly-web3 + py-builder-relayer-client to redeem positions through
the Polymarket relayer. This handles approvals and redemption properly
for proxy/Safe wallets using Builder credentials.

Required env vars (set in Railway):
  - BUILDER_KEY
  - BUILDER_SECRET
  - BUILDER_PASSPHRASE

Required packages:
  pip install poly-web3 py-clob-client py-builder-relayer-client py-builder-signing-sdk
"""

import os
import logging

logger = logging.getLogger("redeem")

# poly-web3 relayer URL
RELAYER_URL = "https://relayer-v2.polymarket.com"


def _init_service(private_key: str, funder_address: str):
    """
    Initialize the PolyWeb3Service with CLOB + Relay clients.
    This is the proper way to interact with proxy wallets.
    """
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

    # 1. CLOB client (for API creds / market queries)
    clob_client = ClobClient(
        host="https://clob.polymarket.com",
        key=private_key,
        chain_id=137,
        signature_type=1,        # proxy wallet
        funder=funder_address,
    )
    clob_client.set_api_creds(clob_client.create_or_derive_api_creds())

    # 2. Relay client (submits txs through Polymarket relayer - gasless)
    relay_client = RelayClient(
        RELAYER_URL,
        137,
        private_key,
        BuilderConfig(
            local_builder_creds=BuilderApiKeyCreds(
                key=builder_key,
                secret=builder_secret,
                passphrase=builder_passphrase,
            )
        ),
    )

    # 3. PolyWeb3Service handles approval + redeem through relay
    service = PolyWeb3Service(
        clob_client=clob_client,
        relayer_client=relay_client,
    )

    return service


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
        service = _init_service(private_key, funder_address)

        logger.info(
            "Redeem: condition=%s..., neg_risk=%s, size=%s",
            condition_id[:16], neg_risk, size,
        )

        # Check if condition is actually resolved first
        try:
            is_resolved = service.is_condition_resolved(condition_id)
            if not is_resolved:
                result["error"] = "Condition not yet resolved. Cannot redeem."
                logger.warning("Condition %s... not resolved yet", condition_id[:16])
                return result
        except Exception as e:
            # Don't block redemption if check fails - just log and proceed
            logger.warning("Could not check resolution status: %s", str(e)[:100])

        # Execute redemption
        if neg_risk:
            raw_amount = int(size * 1_000_000)
            redeem_result = service.redeem(
                condition_id=condition_id,
                neg_risk=True,
                redeem_amounts=[raw_amount, 0],
            )
        else:
            redeem_result = service.redeem(
                condition_id=condition_id,
            )

        logger.info("Redeem raw result: %s", str(redeem_result)[:300])

        # Parse result
        if redeem_result:
            tx_hash = None
            if isinstance(redeem_result, dict):
                tx_hash = (
                    redeem_result.get("transactionHash")
                    or redeem_result.get("transaction_hash")
                    or redeem_result.get("hash")
                    or redeem_result.get("tx_hash")
                )
                if redeem_result.get("error"):
                    result["error"] = str(redeem_result["error"])
                    result["tx_hash"] = tx_hash
                    return result
            elif isinstance(redeem_result, str):
                tx_hash = redeem_result
            elif hasattr(redeem_result, "transaction_hash"):
                tx_hash = redeem_result.transaction_hash
            elif hasattr(redeem_result, "transactionHash"):
                tx_hash = redeem_result.transactionHash

            result["success"] = True
            result["tx_hash"] = tx_hash
            logger.info("Redeem SUCCESS: tx=%s", tx_hash)
        else:
            result["error"] = "Relayer returned empty response."
            logger.error("Redeem: empty result")

    except ImportError as e:
        logger.error("Redeem import error: %s", e)
        result["error"] = (
            "Missing package. Run: pip install poly-web3 "
            "py-clob-client py-builder-relayer-client py-builder-signing-sdk"
        )

    except Exception as e:
        error_msg = str(e)
        logger.error("Redeem FAILED: %s", error_msg)

        if "429" in error_msg or "quota exceeded" in error_msg.lower():
            result["error"] = "Rate limited by relayer. Try again in a few minutes."
        elif "operator approval" in error_msg.lower():
            result["error"] = (
                "ERC1155 approval not set. This should be handled by poly-web3. "
                "Check Builder credentials and proxy wallet address."
            )
        elif "revert" in error_msg.lower():
            result["error"] = "Transaction reverted: " + error_msg[:150]
        elif "nonce" in error_msg.lower():
            result["error"] = "Nonce error - retry in a moment."
        else:
            result["error"] = "Redemption failed: " + error_msg[:200]

    return result
