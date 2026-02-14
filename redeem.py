"""
redeem.py - Polymarket Gasless Redemption with Approval Handling

The PolymarketGaslessWeb3Client can send relay transactions to the
Polymarket relayer for PROXY wallets. However, before neg_risk redemption
can work, the proxy wallet must approve the NegRiskAdapter as an ERC1155
operator on the CTF contract.

Strategy:
1. Inspect the gasless client to find its internal relay submission method
2. Use that same method to send a setApprovalForAll transaction
3. Then proceed with the normal redeem_position call

Requires: pip install polymarket-apis
"""

import logging
import json
import inspect

logger = logging.getLogger(__name__)

# Contract addresses on Polygon
CTF_ADDRESS = "0x4D97DCd97eC945f40cF65F87097ACe5EA0476045"
NEG_RISK_ADAPTER = "0xd91E80cF2E7be2e162c6513ceD06f1dD0dA35296"
CTF_EXCHANGE = "0x4bFb41d5B3570DeFd03C39a9A4D8dE6Bd8B8982E"
NEG_RISK_CTF_EXCHANGE = "0xC5d563A36AE78145C45a50134d48A1215220f80a"

_approvals_set = False
_client_inspected = False


def _build_approval_calldata(operator: str) -> str:
    """Build setApprovalForAll(operator, true) calldata."""
    # Function selector for setApprovalForAll(address,bool)
    selector = "a22cb465"
    # ABI encode: address (padded to 32 bytes) + bool true (padded to 32 bytes)
    addr = operator.lower().replace("0x", "").zfill(64)
    true_val = "0" * 63 + "1"
    return "0x" + selector + addr + true_val


def _inspect_client(web3_client):
    """Log all methods and attributes of the gasless web3 client for debugging."""
    global _client_inspected
    if _client_inspected:
        return
    _client_inspected = True

    # Public methods
    public = [m for m in dir(web3_client) if not m.startswith('_')]
    logger.info("GaslessClient public methods: %s", ", ".join(public))

    # Private methods (might contain the relay submission method)
    private = [m for m in dir(web3_client) if m.startswith('_') and not m.startswith('__')]
    logger.info("GaslessClient private methods: %s", ", ".join(private))

    # Check for common relay-related attributes
    for attr_name in ['relay', 'relayer', 'client', 'session', 'signer',
                      'proxy_address', 'address', 'chain_id', 'w3',
                      'web3', 'account', 'proxy_wallet']:
        if hasattr(web3_client, attr_name):
            val = getattr(web3_client, attr_name)
            logger.info("  .%s = %s (type: %s)", attr_name, repr(val)[:100], type(val).__name__)

    # Try to find the redeem_position source to understand how it sends transactions
    try:
        src = inspect.getsource(web3_client.redeem_position)
        # Log first 500 chars to understand the flow
        logger.info("redeem_position source (first 500 chars): %s", src[:500])
    except Exception as e:
        logger.info("Could not get redeem_position source: %s", e)


def _try_send_approval_via_client(web3_client, operator: str) -> bool:
    """
    Try to send setApprovalForAll through the gasless client's internal methods.
    Returns True if successful.
    """
    calldata = _build_approval_calldata(operator)

    # Strategy 1: Look for a generic execute/relay method
    for method_name in ['execute', 'relay', 'send_transaction', 'submit',
                        'execute_transaction', 'relay_transaction',
                        '_execute', '_relay', '_send_transaction',
                        '_submit_relay', '_relay_transaction',
                        '_execute_relay', '_gasless_execute']:
        if hasattr(web3_client, method_name):
            method = getattr(web3_client, method_name)
            if callable(method):
                try:
                    logger.info("Trying %s for approval...", method_name)
                    # Try different calling conventions
                    try:
                        result = method(CTF_ADDRESS, calldata)
                        logger.info("Approval via %s succeeded: %s", method_name, str(result)[:200])
                        return True
                    except TypeError:
                        # Maybe it takes a dict
                        result = method({"to": CTF_ADDRESS, "data": calldata, "value": "0"})
                        logger.info("Approval via %s (dict) succeeded: %s", method_name, str(result)[:200])
                        return True
                except Exception as e:
                    logger.info("  %s failed: %s", method_name, str(e)[:150])

    # Strategy 2: If the client has a redeem_position that works,
    # it must internally do: encode calldata -> sign -> submit to relayer
    # Try to find and reuse that chain by examining the class hierarchy
    try:
        # The gasless client likely inherits from or wraps the web3 client
        # Check if there's a method that takes (to, data) and submits to relayer
        cls = type(web3_client)
        for name, method in inspect.getmembers(cls, predicate=inspect.isfunction):
            sig = inspect.signature(method)
            params = list(sig.parameters.keys())
            if len(params) >= 3 and any(p in params for p in ['to', 'target', 'contract_address']):
                if any(p in params for p in ['data', 'calldata', 'tx_data']):
                    logger.info("Found potential relay method: %s%s", name, sig)
    except Exception as e:
        logger.info("Method inspection failed: %s", str(e)[:100])

    return False


def _ensure_approvals(web3_client):
    """
    Attempt to set ERC1155 operator approvals for the contracts that need
    to transfer tokens during redemption.
    """
    global _approvals_set
    if _approvals_set:
        return

    _inspect_client(web3_client)

    # The NegRiskAdapter is the one that needs approval for neg_risk redemption
    operators = [NEG_RISK_ADAPTER, CTF_EXCHANGE, NEG_RISK_CTF_EXCHANGE]

    for operator in operators:
        try:
            success = _try_send_approval_via_client(web3_client, operator)
            if success:
                logger.info("Approval set for %s", operator[:10])
            else:
                logger.warning("Could not find method to set approval for %s", operator[:10])
        except Exception as e:
            logger.warning("Approval attempt for %s failed: %s", operator[:10], str(e)[:200])

    _approvals_set = True


async def redeem_position(
    private_key: str,
    funder_address: str,
    condition_id: str,
    token_id: str,
    size: float,
    neg_risk: bool = False,
) -> dict:
    """Redeem a resolved Polymarket position via the gasless relayer."""
    result = {"success": False, "error": None, "tx_hash": None}

    try:
        from polymarket_apis import PolymarketGaslessWeb3Client

        web3_client = PolymarketGaslessWeb3Client(
            private_key=private_key,
            signature_type=1,
            chain_id=137,
        )

        # Try to set approvals (will log all available methods for debugging)
        _ensure_approvals(web3_client)

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
        result["error"] = "Missing package: polymarket-apis"

    except Exception as e:
        error_msg = str(e)
        logger.error("Redeem FAILED: %s", error_msg)

        if "429" in error_msg or "quota exceeded" in error_msg.lower():
            result["error"] = "Rate limited. Try again in a few minutes."
        elif "operator approval" in error_msg.lower():
            result["error"] = (
                "ERC1155 approval not set. The bot needs one-time approval setup. "
                "See logs for available methods on the gasless client."
            )
        elif "revert" in error_msg.lower():
            result["error"] = "Transaction reverted: " + error_msg[:150]
        else:
            result["error"] = "Redemption failed: " + error_msg[:200]

    return result
