"""
BetPoly - Gasless Redemption via Builder Relayer

Uses py_builder_relayer_client.client.RelayClient for gasless redeem.
"""
import logging

logger = logging.getLogger("BetPoly.Redeem")

USDC_E = "0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174"
CTF = "0x4D97DCd97eC945f40cF65F87097ACe5EA0476045"
NEG_RISK_ADAPTER = "0xd91E80cF2E7be2e162c6513ceD06f1dD0dA35296"


def _encode_redeem(condition_id: str, neg_risk: bool) -> dict:
    """Build redeem transaction."""
    from web3 import Web3
    from eth_abi import encode

    w3 = Web3()
    cond_bytes = bytes.fromhex(condition_id[2:] if condition_id.startswith("0x") else condition_id)

    fn_sig = w3.keccak(text="redeemPositions(address,bytes32,bytes32,uint256[])")[:4]
    args = encode(
        ["address", "bytes32", "bytes32", "uint256[]"],
        [
            Web3.to_checksum_address(USDC_E),
            b"\x00" * 32,
            cond_bytes,
            [1, 2],
        ],
    )
    target = NEG_RISK_ADAPTER if neg_risk else CTF
    return {"to": target, "data": "0x" + fn_sig.hex() + args.hex(), "value": "0"}


async def redeem_position(
    private_key: str,
    condition_id: str,
    token_id: str,
    size: float,
    neg_risk: bool = False,
) -> dict:
    """Redeem via Builder Relayer (GASLESS)."""
    result = {"success": False, "error": None, "tx_hash": None}

    try:
        import wallet
        from py_builder_relayer_client.models import SafeTransaction

        logger.info("Redeem: condition=%s neg_risk=%s size=%s", condition_id[:16], neg_risk, size)

        raw_tx = _encode_redeem(condition_id, neg_risk)
        redeem_tx = SafeTransaction(
            to=raw_tx["to"],
            data=raw_tx["data"],
            value=raw_tx["value"],
        )

        relay_client = wallet.create_relay_client(private_key)

        logger.info("Submitting redeem via relayer (gasless)...")
        response = relay_client.execute([redeem_tx], "Redeem winning position")

        tx_hash = None
        if hasattr(response, "wait"):
            res = response.wait()
            tx_hash = getattr(res, "transaction_hash", None) or getattr(res, "transactionHash", None)
        elif isinstance(response, dict):
            tx_hash = response.get("transactionHash") or response.get("transaction_hash")
        elif hasattr(response, "transaction_hash"):
            tx_hash = response.transaction_hash

        result["success"] = True
        result["tx_hash"] = tx_hash
        logger.info("Redeem SUCCESS: tx=%s", tx_hash)

    except Exception as e:
        error_msg = str(e)
        logger.error("Redeem FAILED: %s", error_msg)

        if "429" in error_msg:
            result["error"] = "Rate limited. Try again shortly."
        elif "operator approval" in error_msg.lower():
            result["error"] = "Approvals not set. Wallet needs re-onboarding."
        else:
            result["error"] = "Redemption failed: " + error_msg[:200]

    return result
