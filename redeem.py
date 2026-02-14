"""
redeem.py - Polymarket Gasless Redemption via Builder Relay

Uses poly-web3 PolyWeb3Service which handles approvals, neg_risk detection,
and relay submission internally.

poly-web3 API (discovered via introspection):
  service.redeem(condition_ids: str | list[str], batch_size: int = 10)
  service.redeem_all(batch_size: int = 10) -> list[dict]
  service.is_condition_resolved(condition_id: str) -> bool

Required env vars (set in Railway):
  - BUILDER_API_KEY
  - BUILDER_SECRET
  - BUILDER_PASSPHRASE
"""

import os
import logging

logger = logging.getLogger("redeem")

RELAYER_URL = "https://relayer-v2.polymarket.com"


def _init_service(private_key: str, funder_address: str):
    """Initialize the PolyWeb3Service with CLOB + Relay clients."""
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
        BuilderConfig(
            local_builder_creds=BuilderApiKeyCreds(
                key=builder_key,
                secret=builder_secret,
                passphrase=builder_passphrase,
            )
        ),
    )

    return PolyWeb3Service(
        clob_client=clob_client,
        relayer_client=relay_client,
    )


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

        # Check resolution status
        try:
            if not service.is_condition_resolved(condition_id):
                result["error"] = "Condition not yet resolved. Cannot redeem."
                logger.warning("Condition %s... not resolved", condition_id[:16])
                return result
        except Exception as e:
            logger.warning("Resolution check failed: %s (proceeding anyway)", str(e)[:100])

        # poly-web3 handles neg_risk, amounts, and approvals internally
        # Just pass the condition_id(s)
        redeem_result = service.redeem(condition_ids=condition_id)

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
            elif isinstance(redeem_result, list):
                # May return a list of tx results for batch
                logger.info("Redeem returned list of %d results", len(redeem_result))
                if redeem_result:
                    first = redeem_result[0]
                    if isinstance(first, dict):
                        tx_hash = (
                            first.get("transactionHash")
                            or first.get("transaction_hash")
                            or first.get("hash")
                            or first.get("tx_hash")
                        )
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
                "ERC1155 approval not set. The wallet may need onboarding. "
                "Check Builder credentials and proxy wallet address."
            )
        elif "revert" in error_msg.lower():
            result["error"] = "Transaction reverted: " + error_msg[:150]
        elif "nonce" in error_msg.lower():
            result["error"] = "Nonce error - retry in a moment."
        else:
            result["error"] = "Redemption failed: " + error_msg[:200]

    return result
