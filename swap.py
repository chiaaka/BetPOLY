"""
BetPoly - USDC → USDC.e Swap via Uniswap V3

Gasless swap through Builder Relayer.
Uses Uniswap V3 SwapRouter exactInputSingle on Polygon.

USDC (native):  0x3c499c542cEF5E3811e1192ce70d8cC03d5c3359  (6 decimals)
USDC.e (bridged): 0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174  (6 decimals)
Uniswap V3 Router: 0xE592427A0AEce92De3Edee1F18E0157C05861564

Both are 6 decimals and pegged 1:1, so slippage is negligible.
We set 0.5% slippage tolerance as a safety net.
"""
import logging
import time

logger = logging.getLogger("BetPoly.Swap")

USDC_NATIVE = "0x3c499c542cEF5E3811e1192ce70d8cC03d5c3359"
USDC_E = "0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174"
UNISWAP_V3_ROUTER = "0xE592427A0AEce92De3Edee1F18E0157C05861564"

# Fee tier: 100 = 0.01% (stablecoin pairs typically use lowest fee tier)
POOL_FEE = 100


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


def _build_approve_tx(token: str, spender: str, amount_raw: int):
    """Build ERC-20 approve SafeTransaction."""
    from web3 import Web3
    from eth_abi import encode
    from py_builder_relayer_client.models import SafeTransaction

    fn_sig = Web3.keccak(text="approve(address,uint256)")[:4]
    args = encode(
        ["address", "uint256"],
        [Web3.to_checksum_address(spender), amount_raw],
    )
    return SafeTransaction(
        to=token,
        data="0x" + fn_sig.hex() + args.hex(),
        value="0",
        operation=_get_operation_call(),
    )


def _build_swap_tx(amount_raw: int, min_out_raw: int, recipient: str):
    """Build Uniswap V3 exactInputSingle SafeTransaction.
    
    exactInputSingle((
        address tokenIn,
        address tokenOut,
        uint24 fee,
        address recipient,
        uint256 deadline,
        uint256 amountIn,
        uint256 amountOutMinimum,
        uint160 sqrtPriceLimitX96
    ))
    """
    from web3 import Web3
    from eth_abi import encode
    from py_builder_relayer_client.models import SafeTransaction

    deadline = int(time.time()) + 600  # 10 minutes

    # Encode the tuple parameter
    fn_sig = Web3.keccak(
        text="exactInputSingle((address,address,uint24,address,uint256,uint256,uint256,uint160))"
    )[:4]
    
    args = encode(
        ["(address,address,uint24,address,uint256,uint256,uint256,uint160)"],
        [(
            Web3.to_checksum_address(USDC_NATIVE),    # tokenIn
            Web3.to_checksum_address(USDC_E),          # tokenOut
            POOL_FEE,                                   # fee (0.01%)
            Web3.to_checksum_address(recipient),        # recipient (Safe)
            deadline,                                    # deadline
            amount_raw,                                  # amountIn
            min_out_raw,                                 # amountOutMinimum
            0,                                           # sqrtPriceLimitX96 (0 = no limit)
        )],
    )
    
    return SafeTransaction(
        to=UNISWAP_V3_ROUTER,
        data="0x" + fn_sig.hex() + args.hex(),
        value="0",
        operation=_get_operation_call(),
    )


async def swap_usdc_to_usdc_e(
    private_key: str,
    amount_usdc: float,
    safe_address: str,
) -> dict:
    """Swap native USDC → USDC.e via Uniswap V3, gasless through relayer.
    
    Args:
        private_key: User's derived private key
        amount_usdc: Amount to swap (e.g. 100.0)
        safe_address: User's Safe address (recipient)
    
    Returns:
        {"success": bool, "error": str|None, "amount_out": float}
    """
    result = {"success": False, "error": None, "amount_out": 0}
    
    try:
        import wallet
        
        amount_raw = int(amount_usdc * 1_000_000)  # 6 decimals
        # 0.5% slippage tolerance (very generous for stablecoin pair)
        min_out_raw = int(amount_raw * 0.995)
        
        logger.info(
            "Swap: %s USDC → USDC.e (min out: %s) for %s",
            amount_usdc, min_out_raw / 1_000_000, safe_address[:10]
        )
        
        # Build transactions:
        # 1. Approve Uniswap router to spend USDC
        approve_tx = _build_approve_tx(USDC_NATIVE, UNISWAP_V3_ROUTER, amount_raw)
        # 2. Execute swap
        swap_tx = _build_swap_tx(amount_raw, min_out_raw, safe_address)
        
        # Execute both via relayer (gasless)
        relay_client = wallet.create_relay_client(private_key)
        
        logger.info("Submitting approve + swap via relayer (gasless)...")
        response = relay_client.execute(
            [approve_tx, swap_tx],
            "Convert USDC to USDC.e"
        )
        
        tx_hash = None
        if hasattr(response, "wait"):
            res = response.wait()
            if res:
                tx_hash = getattr(res, "transactionHash", None) or getattr(res, "transaction_hash", None)
        elif isinstance(response, dict):
            tx_hash = response.get("transactionHash") or response.get("transaction_hash")
        
        result["success"] = True
        result["amount_out"] = amount_usdc  # ~1:1 for stablecoins
        result["tx_hash"] = tx_hash
        logger.info("Swap SUCCESS: %s USDC → USDC.e tx=%s", amount_usdc, tx_hash)
        
    except Exception as e:
        error_msg = str(e)
        logger.error("Swap FAILED: %s", error_msg)
        
        if "429" in error_msg:
            result["error"] = "Rate limited. Try again shortly."
        elif "insufficient" in error_msg.lower():
            result["error"] = "Insufficient USDC balance."
        elif "STF" in error_msg:
            # Uniswap "STF" = SafeTransferFrom failed (usually approval issue)
            result["error"] = "Swap failed — approval issue. Try again."
        else:
            result["error"] = "Swap failed: " + error_msg[:200]
    
    return result
