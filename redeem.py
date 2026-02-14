"""
redeem.py - Polymarket Gasless Redemption using polymarket-apis

Uses PolymarketGaslessWeb3Client which properly handles PROXY wallet
relay transactions (EIP-712 signing, proxy tx encoding, relayer submission).

The py-builder-relayer-client package only supports SAFE wallets.
polymarket-apis handles both PROXY and SAFE wallets correctly.

Requires: pip install polymarket-apis
"""

import os
import logging

logger = logging.getLogger(__name__)


async def redeem_position(
    private_key: str,
    funder_address: str,
    condition_id: str,
    token_id: str,
    size: float,
    neg_risk: bool = False,
) -> dict:
    """
    Redeem a resolved Polymarket position via the gasless relayer.

    Uses PolymarketGaslessWeb3Client which properly builds proxy wallet
    transactions and submits them to the Polymarket relayer.
    """
    result = {"success": False, "error": None, "tx_hash": None}

    try:
        from polymarket_apis import PolymarketGaslessWeb3Client

        # signature_type=1 for Magic/email proxy wallets
        web3_client = PolymarketGaslessWeb3Client(
            private_key=private_key,
            signature_type=1,
            chain_id=137,
        )

        logger.info(
            "Redeem: condition=%s..., neg_risk=%s, size=%s",
            condition_id[:16],
            neg_risk,
            size,
        )

        # Build amounts array for neg_risk redemption
        # For standard CTF redeem, amounts are not needed (the library handles it)
        # For neg_risk, amounts = [yes_amount, no_amount] in raw units (6 decimals)
        if neg_risk:
            # Convert size to raw USDC units (6 decimals)
            raw_amount = int(size * 1_000_000)
            # We pass the amount for the winning side; 0 for losing side
            # The library/contract figures out which side won
            redeem_result = web3_client.redeem_position(
                condition_id=condition_id,
                amounts=[raw_amount, 0],
                neg_risk=True,
            )
        else:
            # Standard CTF redeem - no amounts needed
            redeem_result = web3_client.redeem_position(
                condition_id=condition_id,
                neg_risk=False,
            )

        logger.info("Redeem raw result: %s", redeem_result)

        # Parse the result
        if redeem_result:
            tx_hash = None
            if isinstance(redeem_result, dict):
                tx_hash = (
                    redeem_result.get("transactionHash")
                    or redeem_result.get("transaction_hash")
                    or redeem_result.get("hash")
                    or redeem_result.get("tx_hash")
                )
                # Check for error in response
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
            "Missing package: polymarket-apis. "
            "Install with: pip install polymarket-apis"
        )

    except Exception as e:
        error_msg = str(e)
        logger.error("Redeem FAILED: %s", error_msg)

        if "429" in error_msg or "rate limit" in error_msg.lower() or "quota exceeded" in error_msg.lower():
            result["error"] = "Rate limited by relayer. Try again in a few minutes."
        elif "401" in error_msg or "403" in error_msg:
            result["error"] = "Authentication failed. Check builder credentials."
        elif "400" in error_msg or "bad request" in error_msg.lower():
            result["error"] = "Bad request to relayer: " + error_msg[:200]
        elif "revert" in error_msg.lower():
            result["error"] = "Transaction reverted on-chain."
        elif "not resolved" in error_msg.lower():
            result["error"] = "Market not yet resolved. Cannot redeem."
        else:
            result["error"] = "Redemption failed: " + error_msg[:200]

    return result
