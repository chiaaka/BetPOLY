"""
redeem.py - Polymarket Gasless Redemption via Builder Relay

Uses poly-web3 + py-builder-relayer-client to redeem positions through
the Polymarket relayer. Includes introspection to discover the actual
method signature since poly-web3 versions may differ.

Required env vars (set in Railway):
  - BUILDER_API_KEY
  - BUILDER_SECRET
  - BUILDER_PASSPHRASE

Required packages:
  pip install poly-web3 py-clob-client py-builder-relayer-client py-builder-signing-sdk
"""

import os
import logging
import inspect

logger = logging.getLogger("redeem")

RELAYER_URL = "https://relayer-v2.polymarket.com"
_inspected = False


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

    service = PolyWeb3Service(
        clob_client=clob_client,
        relayer_client=relay_client,
    )

    return service


def _log_service_api(service):
    """One-time deep introspection to discover the actual API."""
    # Log redeem signature
    try:
        sig = inspect.signature(service.redeem)
        logger.info("poly-web3 redeem() signature: %s", sig)
    except Exception as e:
        logger.info("Could not inspect redeem signature: %s", e)

    # Log redeem source code
    try:
        src = inspect.getsource(service.redeem)
        logger.info("redeem() full source:\n%s", src[:1500])
    except Exception as e:
        logger.info("Could not get redeem source: %s", e)

    # Log all public methods
    methods = [m for m in dir(service) if not m.startswith('_')]
    logger.info("PolyWeb3Service attributes: %s", methods)

    # Log signatures of all callable methods
    for m in methods:
        obj = getattr(service, m, None)
        if callable(obj):
            try:
                s = inspect.signature(obj)
                logger.info("  %s%s", m, s)
            except Exception:
                logger.info("  %s (could not get sig)", m)


async def redeem_position(
    private_key: str,
    funder_address: str,
    condition_id: str,
    token_id: str,
    size: float,
    neg_risk: bool = False,
) -> dict:
    """Redeem a resolved Polymarket position via Builder Relay (gasless)."""
    global _inspected
    result = {"success": False, "error": None, "tx_hash": None}

    try:
        service = _init_service(private_key, funder_address)

        # Log the actual API once so we can see what's available
        if not _inspected:
            _log_service_api(service)
            _inspected = True

        logger.info(
            "Redeem: condition=%s..., neg_risk=%s, size=%s",
            condition_id[:16], neg_risk, size,
        )

        # Discover actual parameter names
        sig = inspect.signature(service.redeem)
        param_names = [p for p in sig.parameters.keys() if p != "self"]
        logger.info("redeem() param names: %s", param_names)

        # Build kwargs dynamically based on what the method actually accepts
        kwargs = {}
        raw_amount = int(size * 1_000_000) if neg_risk else 0

        # Map our data to possible parameter names
        param_mapping = {
            # condition_id variants
            "condition_id": condition_id,
            "conditionId": condition_id,
            "condition": condition_id,
            # neg_risk variants
            "neg_risk": neg_risk,
            "negRisk": neg_risk,
            "is_neg_risk": neg_risk,
            # amounts variants
            "redeem_amounts": [raw_amount, 0] if neg_risk else None,
            "amounts": [raw_amount, 0] if neg_risk else None,
            "redeem_amount": [raw_amount, 0] if neg_risk else None,
        }

        for pname in param_names:
            if pname in param_mapping and param_mapping[pname] is not None:
                kwargs[pname] = param_mapping[pname]

        logger.info("Calling service.redeem(%s)", kwargs)

        if kwargs:
            redeem_result = service.redeem(**kwargs)
        else:
            # No recognized params - try positional as last resort
            logger.info("No matching params found, trying positional: (%s, %s)", condition_id[:16], neg_risk)
            try:
                if neg_risk:
                    redeem_result = service.redeem(condition_id, True, [raw_amount, 0])
                else:
                    redeem_result = service.redeem(condition_id)
            except TypeError:
                redeem_result = service.redeem()

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
                "ERC1155 approval not set. Check Builder credentials and proxy wallet."
            )
        elif "revert" in error_msg.lower():
            result["error"] = "Transaction reverted: " + error_msg[:150]
        elif "nonce" in error_msg.lower():
            result["error"] = "Nonce error - retry in a moment."
        else:
            result["error"] = "Redemption failed: " + error_msg[:200]

    return result
