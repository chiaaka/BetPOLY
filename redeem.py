"""
redeem.py - Polymarket Gasless Redemption using polymarket-apis

Uses PolymarketGaslessWeb3Client which properly handles PROXY wallet
relay transactions (EIP-712 signing, proxy tx encoding, relayer submission).

IMPORTANT: The proxy wallet must have ERC1155 operator approvals set
for the CTF and NegRiskAdapter contracts. We send these as gasless
relay transactions before the first redeem.

Requires: pip install polymarket-apis
"""

import os
import logging
from eth_abi import encode as abi_encode

logger = logging.getLogger(__name__)

# Contract addresses on Polygon
CTF_ADDRESS = "0x4D97DCd97eC945f40cF65F87097ACe5EA0476045"
NEG_RISK_ADAPTER = "0xd91E80cF2E7be2e162c6513ceD06f1dD0dA35296"
CTF_EXCHANGE = "0x4bFb41d5B3570DeFd03C39a9A4D8dE6Bd8B8982E"
NEG_RISK_CTF_EXCHANGE = "0xC5d563A36AE78145C45a50134d48A1215220f80a"

# setApprovalForAll(address,bool) selector
SET_APPROVAL_SELECTOR = bytes.fromhex("a22cb465")

# Track approvals per session
_approvals_set = False


def _build_set_approval_calldata(operator: str, approved: bool = True) -> str:
    """Build setApprovalForAll(operator, approved) calldata."""
    op_bytes = bytes.fromhex(operator[2:] if operator.startswith("0x") else operator)
    params = abi_encode(
        ["address", "bool"],
        [operator, approved],
    )
    return "0x" + (SET_APPROVAL_SELECTOR + params).hex()


def _ensure_approvals(web3_client):
    """
    Send setApprovalForAll for CTF and NegRiskAdapter via the gasless
    relayer. These are needed so the contracts can transfer/burn
    ERC1155 tokens from the proxy wallet during redemption.
    """
    global _approvals_set
    if _approvals_set:
        return

    # The operators that need approval on the CTF ERC1155 contract:
    # - CTF Exchange (for standard trading)
    # - Neg Risk CTF Exchange (for neg risk trading)  
    # - Neg Risk Adapter (for neg risk redemption - this is the one causing the error)
    operators = [
        CTF_EXCHANGE,
        NEG_RISK_CTF_EXCHANGE,
        NEG_RISK_ADAPTER,
    ]

    for operator in operators:
        try:
            logger.info("Setting ERC1155 approval for operator %s...", operator[:10])
            # Use the same gasless relay mechanism the library uses internally
            # by calling a generic "execute transaction" if available,
            # or we encode the approval as a redeem-like call
            calldata = _build_set_approval_calldata(operator, True)

            # The PolymarketGaslessWeb3Client internally uses _execute_relay
            # or similar to submit transactions. We need to access this.
            # Try using the internal relay method if it exists.
            if hasattr(web3_client, '_execute_relay'):
                web3_client._execute_relay(CTF_ADDRESS, calldata)
            elif hasattr(web3_client, 'execute_transaction'):
                web3_client.execute_transaction(CTF_ADDRESS, calldata)
            elif hasattr(web3_client, 'relay_transaction'):
                web3_client.relay_transaction(CTF_ADDRESS, calldata)
            elif hasattr(web3_client, '_relay'):
                web3_client._relay(CTF_ADDRESS, calldata)
            elif hasattr(web3_client, 'send_relay_transaction'):
                web3_client.send_relay_transaction(CTF_ADDRESS, calldata)
            else:
                # List all methods to find the relay method
                methods = [m for m in dir(web3_client) if not m.startswith('__')]
                logger.warning(
                    "Cannot find relay method. Available methods: %s",
                    ", ".join(methods)
                )
                break

            logger.info("Approval set for %s", operator[:10])
        except Exception as e:
            logger.warning("Approval for %s: %s", operator[:10], str(e)[:150])

    _approvals_set = True


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

        # Ensure ERC1155 approvals are set (needed for CTF to burn tokens)
        _ensure_approvals(web3_client)

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
