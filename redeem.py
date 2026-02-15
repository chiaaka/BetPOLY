"""
BetPoly - Gasless Redemption via Builder Relayer

Uses Polymarket's Builder Relayer to redeem winning positions.
100% gasless — Polymarket pays all gas fees.

Requires: pip install py-builder-relayer-client
"""
import logging
from web3 import Web3

logger = logging.getLogger("BetPoly.Redeem")

# Contract addresses
USDC_E = "0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174"
CTF = "0x4D97DCd97eC945f40cF65F87097ACe5EA0476045"
NEG_RISK_ADAPTER = "0xd91E80cF2E7be2e162c6513ceD06f1dD0dA35296"

_w3 = Web3()


def _encode_redeem_positions(
    collateral_token: str,
    parent_collection_id: bytes,
    condition_id: str,
    index_sets: list,
) -> str:
    """Encode CTF redeemPositions calldata."""
    fn_sig = _w3.keccak(
        text="redeemPositions(address,bytes32,bytes32,uint256[])"
    )[:4]

    # ABI encode the 4 arguments
    from eth_abi import encode
    encoded_args = encode(
        ["address", "bytes32", "bytes32", "uint256[]"],
        [
            Web3.to_checksum_address(collateral_token),
            parent_collection_id,
            bytes.fromhex(condition_id[2:] if condition_id.startswith("0x") else condition_id),
            index_sets,
        ],
    )
    return "0x" + fn_sig.hex() + encoded_args.hex()


async def redeem_position(
    private_key: str,
    condition_id: str,
    token_id: str,
    size: float,
    neg_risk: bool = False,
) -> dict:
    """
    Redeem a resolved Polymarket position via the Builder Relayer (GASLESS).

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
        import wallet

        logger.info(
            "Redeem: condition=%s..., neg_risk=%s, size=%s",
            condition_id[:16], neg_risk, size,
        )

        relay_client = wallet.create_relay_client(private_key)

        if neg_risk:
            # Neg risk uses the NegRiskAdapter contract
            target = NEG_RISK_ADAPTER
            parent_collection_id = b"\x00" * 32
            index_sets = [1, 2]  # Both outcomes
        else:
            # Standard uses the CTF contract directly
            target = CTF
            parent_collection_id = b"\x00" * 32
            index_sets = [1, 2]  # Both outcomes

        redeem_data = _encode_redeem_positions(
            collateral_token=USDC_E,
            parent_collection_id=parent_collection_id,
            condition_id=condition_id,
            index_sets=index_sets,
        )

        redeem_tx = {"to": target, "data": redeem_data, "value": "0"}

        logger.info("Submitting redeem via relayer (gasless)...")
        response = relay_client.execute([redeem_tx], "Redeem winning position")

        tx_hash = None
        if hasattr(response, "wait"):
            res = response.wait()
            tx_hash = (
                getattr(res, "transactionHash", None)
                or getattr(res, "transaction_hash", None)
            )
        elif isinstance(response, dict):
            tx_hash = (
                response.get("transactionHash")
                or response.get("transaction_hash")
            )

        result["success"] = True
        result["tx_hash"] = tx_hash
        logger.info("Redeem SUCCESS: tx=%s", tx_hash)

    except ImportError as ie:
        result["error"] = f"Missing package: {ie}. Add py-builder-relayer-client to requirements.txt."

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
