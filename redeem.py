"""
BetPoly - Gasless Redemption via Builder Relayer

Two different redeem paths based on market type:

CTF (regular markets):
  redeemPositions(address collateralToken, bytes32 parentCollectionId, bytes32 conditionId, uint256[] indexSets)
  Target: 0x4D97DCd97eC945f40cF65F87097ACe5EA0476045

NegRiskAdapter (neg_risk markets):
  redeemPositions(bytes32 _conditionId, uint256[] _amounts)
  Target: 0xd91E80cF2E7be2e162c6513ceD06f1dD0dA35296
"""
import logging

logger = logging.getLogger("BetPoly.Redeem")

USDC_E = "0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174"
CTF = "0x4D97DCd97eC945f40cF65F87097ACe5EA0476045"
NEG_RISK_ADAPTER = "0xd91E80cF2E7be2e162c6513ceD06f1dD0dA35296"


def _get_operation_call():
    """Get OperationType.Call with fallback."""
    try:
        from py_builder_relayer_client.models import OperationType
        return OperationType.Call
    except ImportError:
        try:
            from py_builder_relayer_client import OperationType
            return OperationType.Call
        except ImportError:
            class _OpCall:
                value = 0
            return _OpCall()


def _encode_redeem(condition_id: str, neg_risk: bool):
    """Build redeem SafeTransaction with correct ABI per market type."""
    from web3 import Web3
    from eth_abi import encode
    from py_builder_relayer_client.models import SafeTransaction

    cond_bytes = bytes.fromhex(condition_id[2:] if condition_id.startswith("0x") else condition_id)
    op_call = _get_operation_call()

    if neg_risk:
        fn_sig = Web3.keccak(text="redeemPositions(bytes32,uint256[])")[:4]
        args = encode(
            ["bytes32", "uint256[]"],
            [cond_bytes, [1, 2]],
        )
        target = NEG_RISK_ADAPTER
    else:
        fn_sig = Web3.keccak(text="redeemPositions(address,bytes32,bytes32,uint256[])")[:4]
        args = encode(
            ["address", "bytes32", "bytes32", "uint256[]"],
            [
                Web3.to_checksum_address(USDC_E),
                b"\x00" * 32,
                cond_bytes,
                [1, 2],
            ],
        )
        target = CTF

    return SafeTransaction(
        to=target,
        data="0x" + fn_sig.hex() + args.hex(),
        value="0",
        operation=op_call,
    )


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

        # Auto-detect neg_risk if not sure
        if not neg_risk and token_id:
            try:
                from py_clob_client.client import ClobClient
                client = ClobClient(host="https://clob.polymarket.com", chain_id=137)
                detected = client.get_neg_risk(token_id)
                if detected:
                    neg_risk = True
                    logger.info("  Auto-detected neg_risk=True for token %s", token_id[:16])
            except Exception:
                pass

        logger.info("Redeem: condition=%s neg_risk=%s size=%s", condition_id[:16], neg_risk, size)

        redeem_tx = _encode_redeem(condition_id, neg_risk)
        relay_client = wallet.create_relay_client(private_key)

        logger.info("Submitting redeem via relayer (gasless)...")
        
        tx_hash = None
        tx_failed = False
        
        try:
            response = relay_client.execute([redeem_tx], "Redeem winning position")
            
            if hasattr(response, "wait"):
                try:
                    res = response.wait()
                    if res:
                        tx_hash = getattr(res, "transactionHash", None) or getattr(res, "transaction_hash", None)
                except Exception as wait_err:
                    err_str = str(wait_err).lower()
                    if "failed onchain" in err_str or "reverted" in err_str or "failed" in err_str:
                        tx_failed = True
                        logger.warning("Relayer: txn failed on-chain: %s", wait_err)
                    else:
                        raise
            elif isinstance(response, dict):
                tx_hash = response.get("transactionHash") or response.get("transaction_hash")
                
        except Exception as exec_err:
            err_str = str(exec_err).lower()
            if "failed onchain" in err_str or "reverted" in err_str:
                tx_failed = True
                logger.warning("Relayer execution failed: %s", exec_err)
            else:
                raise

        if tx_failed:
            result["success"] = False
            result["error"] = "Already redeemed or nothing to claim."
            logger.warning("Redeem FAILED: likely already redeemed (condition=%s)", condition_id[:16])
        else:
            result["success"] = True
            result["tx_hash"] = tx_hash
            logger.info("Redeem SUCCESS: tx=%s", tx_hash)

    except Exception as e:
        error_msg = str(e)
        logger.error("Redeem FAILED: %s", error_msg)

        if "failed onchain" in error_msg.lower() or "reverted" in error_msg.lower():
            result["error"] = "Already redeemed or nothing to claim."
        elif "429" in error_msg:
            result["error"] = "Rate limited. Try again shortly."
        elif "operator approval" in error_msg.lower():
            result["error"] = "Approvals not set. Wallet needs re-onboarding."
        else:
            result["error"] = "Redemption failed: " + error_msg[:200]

    return result
