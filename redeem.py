"""
redeem.py — Polymarket Gasless Redemption via Builder Relayer

Based on official Polymarket docs (docs.polymarket.com/developers/market-makers/inventory)
and verified working code from AleSZanello/poly-examples.

KEY DISCOVERY: Always use CTF contract directly with parentCollectionId=bytes32(0)
and indexSets=[1,2]. Do NOT use NegRiskAdapter — it computes different token IDs
and causes "SafeMath: subtraction overflow".

Verified on Polygon mainnet: https://polygonscan.com/tx/0x0b115de54cf1da04bcfdcb8ec6ebc30b89afaba1572c0b7f02ae2446ca68260a

Flow:
  1. Encode CTF.redeemPositions(USDC, bytes32(0), conditionId, [1,2])
  2. Submit as SafeTransaction via RelayClient.execute()
  3. Relayer handles gas — Polymarket pays
  4. User gets full $1.00/share as USDC.e
"""

import os
import logging
from eth_abi import encode

logger = logging.getLogger(__name__)

# ── Contract addresses (Polygon mainnet) ──
CTF_ADDRESS = "0x4D97DCd97eC945f40cF65F87097ACe5EA0476045"
USDC_ADDRESS = "0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174"

# ── Relayer config ──
RELAYER_URL = "https://relayer-v2.polymarket.com/"
CHAIN_ID = 137

# ── Function selector ──
# keccak256("redeemPositions(address,bytes32,bytes32,uint256[])")[:4] = 0x01a9a068
REDEEM_SELECTOR = bytes.fromhex("01a9a068")


def _build_redeem_data(condition_id: str) -> str:
    """
    Build calldata for CTF.redeemPositions(collateralToken, parentCollectionId, conditionId, indexSets)

    Always targets CTF contract directly (NOT NegRiskAdapter).
    parentCollectionId = bytes32(0) — this is the key for CLOB tokens.
    indexSets = [1, 2] — both outcomes (contract figures out which you hold).
    """
    cond_bytes = bytes.fromhex(
        condition_id[2:] if condition_id.startswith("0x") else condition_id
    )

    params = encode(
        ["address", "bytes32", "bytes32", "uint256[]"],
        [
            USDC_ADDRESS,
            bytes(32),      # parentCollectionId = 0 (KEY!)
            cond_bytes,
            [1, 2],         # indexSets for YES and NO
        ],
    )

    return "0x" + (REDEEM_SELECTOR + params).hex()


async def redeem_position(
    private_key: str,
    funder_address: str,
    condition_id: str,
    token_id: str,
    size: float,
    neg_risk: bool = False,
) -> dict:
    """
    Redeem a winning position via Polymarket's Builder Relayer.

    Full $1.00/share payout. Zero gas fees.
    Always uses CTF contract directly regardless of neg_risk flag.
    """
    result = {"success": False, "error": None, "tx_hash": None}

    try:
        from py_builder_relayer_client.client import RelayClient
        from py_builder_relayer_client.models import SafeTransaction, OperationType
        from py_builder_signing_sdk.config import BuilderConfig
        from py_builder_signing_sdk.sdk_types import BuilderApiKeyCreds

        # 1. Builder credentials
        builder_key = os.getenv("BUILDER_API_KEY", "") or os.getenv("BUILDER_KEY", "")
        builder_secret = os.getenv("BUILDER_SECRET", "")
        builder_passphrase = os.getenv("BUILDER_PASSPHRASE", "")

        if not all([builder_key, builder_secret, builder_passphrase]):
            result["error"] = "Builder credentials not configured."
            return result

        builder_config = BuilderConfig(
            local_builder_creds=BuilderApiKeyCreds(
                key=builder_key,
                secret=builder_secret,
                passphrase=builder_passphrase,
            )
        )

        # 2. Create relay client
        relay_client = RelayClient(
            RELAYER_URL,
            CHAIN_ID,
            private_key,
            builder_config,
        )

        # 3. Build calldata — always CTF direct, never NegRiskAdapter
        calldata = _build_redeem_data(condition_id)
        logger.info(
            f"Redeem: CTF direct, condition={condition_id[:16]}..., "
            f"target={CTF_ADDRESS}, size={size}"
        )

        # 4. Build transaction
        tx = SafeTransaction(
            to=CTF_ADDRESS,
            operation=OperationType.Call,
            data=calldata,
            value="0",
        )

        # 5. Submit via relayer (gasless)
        logger.info(f"Redeem: submitting to relayer for {funder_address[:10]}...")
        response = relay_client.execute([tx], metadata="BetPoly: Redeem")

        # 6. Wait for confirmation
        if response:
            tx_result = response.wait()

            if tx_result:
                # Extract tx hash
                tx_hash = None
                if hasattr(tx_result, "transactionHash"):
                    tx_hash = tx_result.transactionHash
                elif hasattr(tx_result, "transaction_hash"):
                    tx_hash = tx_result.transaction_hash
                elif isinstance(tx_result, dict):
                    tx_hash = (
                        tx_result.get("transactionHash")
                        or tx_result.get("transaction_hash")
                        or tx_result.get("hash")
                    )
                elif isinstance(tx_result, str):
                    tx_hash = tx_result

                # Check state
                state = None
                if hasattr(tx_result, "state"):
                    state = tx_result.state
                elif isinstance(tx_result, dict):
                    state = tx_result.get("state")

                if state and "FAIL" in str(state).upper():
                    result["error"] = f"Transaction failed on-chain: {state}"
                    result["tx_hash"] = tx_hash
                    logger.error(f"Redeem FAILED: state={state}, tx={tx_hash}")
                else:
                    result["success"] = True
                    result["tx_hash"] = tx_hash
                    logger.info(f"Redeem SUCCESS: tx={tx_hash}")
            else:
                result["error"] = "Relayer returned empty confirmation."
                logger.error("Redeem: empty wait() result")
        else:
            result["error"] = "Relayer returned no response."
            logger.error("Redeem: execute() returned None")

    except ImportError as e:
        result["error"] = f"Missing package: {e}"
        logger.error(f"Redeem import error: {e}")
    except Exception as e:
        error_msg = str(e)
        logger.error(f"Redeem FAILED: {error_msg}")

        if "quota exceeded" in error_msg.lower() or "rate limit" in error_msg.lower():
            result["error"] = "Rate limited. Try again in a few minutes."
        elif "401" in error_msg or "403" in error_msg or "unauthorized" in error_msg.lower():
            result["error"] = "Builder authentication failed."
        elif "revert" in error_msg.lower() or "subtraction overflow" in error_msg.lower():
            result["error"] = "Transaction reverted. Market may not be resolved yet."
        else:
            result["error"] = f"Redemption failed: {error_msg[:200]}"

    return result
