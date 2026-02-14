"""
BetPoly - Gasless Redemption (Safe Wallet)

Uses PolymarketGaslessWeb3Client with signature_type=2 (Gnosis Safe).
Works because wallet was properly onboarded via wallet.onboard_wallet():
  - Safe deployed on-chain
  - All ERC-1155 + ERC-20 approvals set
  - Polymarket relayer pays gas → free for user

Requires: pip install polymarket-apis
"""
import logging

logger = logging.getLogger("BetPoly.Redeem")


async def redeem_position(
    private_key: str,
    condition_id: str,
    token_id: str,
    size: float,
    neg_risk: bool = False,
) -> dict:
    """
    Redeem a resolved Polymarket position via the gasless relayer.
    
    Args:
        private_key: User's EOA private key (controls the Safe)
        condition_id: Market condition ID
        token_id: Token ID of the winning position
        size: Number of shares to redeem
        neg_risk: Whether this is a neg_risk market
    
    Returns:
        {success, tx_hash, error}
    """
    result = {"success": False, "error": None, "tx_hash": None}

    try:
        from polymarket_apis import PolymarketGaslessWeb3Client

        # signature_type=2 = Gnosis Safe (deployed + approved via wallet.py)
        web3_client = PolymarketGaslessWeb3Client(
            private_key=private_key,
            signature_type=2,
            chain_id=137,
        )

        logger.info(
            "Redeem: condition=%s..., neg_risk=%s, size=%s",
            condition_id[:16], neg_risk, size,
        )

        if neg_risk:
            raw_amount = int(size * 1_000_000)
            redeem_result = web3_client.redeem_position(
                condition_id=condition_id,
                amounts=[raw_amount, 0],
                neg_risk=True,
            )
        else:
            redeem_result = web3_client.redeem_position(
                condition_id=condition_id,
                neg_risk=False,
            )

        logger.info("Redeem raw result: %s", redeem_result)

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

            result["success"] = True
            result["tx_hash"] = tx_hash
            logger.info("Redeem SUCCESS: tx=%s", tx_hash)
        else:
            result["error"] = "Relayer returned empty response."

    except ImportError:
        result["error"] = "Missing package: polymarket-apis. Add to requirements.txt."

    except Exception as e:
        error_msg = str(e)
        logger.error("Redeem FAILED: %s", error_msg)

        if "429" in error_msg or "quota exceeded" in error_msg.lower():
            result["error"] = "Rate limited. Try again in a few minutes."
        elif "operator approval" in error_msg.lower():
            result["error"] = "Approvals not set. Wallet needs re-onboarding."
        elif "revert" in error_msg.lower():
            result["error"] = "Transaction reverted: " + error_msg[:150]
        else:
            result["error"] = "Redemption failed: " + error_msg[:200]

    return result
