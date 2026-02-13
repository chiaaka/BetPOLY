"""
redeem.py — Polymarket Gasless Redemption via Official Builder Relayer

Uses ONLY official Polymarket libraries:
  - py-builder-relayer-client  (Polymarket/py-builder-relayer-client)
  - py-builder-signing-sdk     (Polymarket/py-builder-signing-sdk)

Flow:
  1. Encode CTF.redeemPositions() or NegRiskAdapter.redeemPositions() call data
  2. Submit as Transaction via RelayClient.execute()
  3. Relayer handles gas — Polymarket pays
  4. User gets full $1.00/share as USDC.e

Required pip packages:
  py-builder-relayer-client
  py-builder-signing-sdk

Required env vars:
  BUILDER_KEY, BUILDER_SECRET, BUILDER_PASSPHRASE
"""

import os
import logging
from eth_abi import encode

logger = logging.getLogger(__name__)

# ── Contract addresses (Polygon mainnet) ──
CTF_ADDRESS = "0x4D97DCd97eC945f40cF65F87097ACe5EA0476045"
NEG_RISK_ADAPTER = "0xd91E80cF2E7be2e162c6513ceD06f1dD0dA35296"
USDC_E = "0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174"
ZERO_BYTES32 = "0x" + "00" * 32

# ── Relayer config ──
RELAYER_URL = "https://relayer-v2.polymarket.com/"
CHAIN_ID = 137

# ── Function selectors ──
# CTF.redeemPositions(address,bytes32,bytes32,uint256[])
CTF_REDEEM_SELECTOR = "0x01a9a068"  # keccak256("redeemPositions(address,bytes32,bytes32,uint256[])")[:4]

# NegRiskAdapter.redeemPositions(bytes32,uint256[])
NEG_RISK_REDEEM_SELECTOR = "0xb5a0e2d0"  # keccak256("redeemPositions(bytes32,uint256[])")[:4]


def _encode_ctf_redeem(condition_id: str) -> str:
    """
    Encode CTF.redeemPositions(collateralToken, parentCollectionId, conditionId, indexSets)
    
    For standard (non-neg-risk) binary markets:
    - collateralToken = USDC.e
    - parentCollectionId = bytes32(0) 
    - conditionId = the market's condition ID
    - indexSets = [1, 2] (both outcomes — the contract figures out which you hold)
    """
    from eth_abi import encode as abi_encode
    
    collateral = bytes.fromhex(USDC_E[2:].lower().zfill(40))
    parent = bytes(32)  # bytes32(0)
    cond_id = bytes.fromhex(condition_id[2:] if condition_id.startswith("0x") else condition_id)
    index_sets = [1, 2]  # Both outcome slots
    
    encoded_args = abi_encode(
        ["address", "bytes32", "bytes32", "uint256[]"],
        [USDC_E, parent, cond_id, index_sets]
    )
    
    return CTF_REDEEM_SELECTOR + encoded_args.hex()


def _encode_neg_risk_redeem(condition_id: str) -> str:
    """
    Encode NegRiskAdapter.redeemPositions(conditionId, amounts)
    
    For neg-risk markets:
    - conditionId = the market's condition ID
    - amounts = [] (empty array — adapter figures out amounts from balances)
    """
    from eth_abi import encode as abi_encode
    
    cond_id = bytes.fromhex(condition_id[2:] if condition_id.startswith("0x") else condition_id)
    amounts = []  # Empty — adapter reads balances
    
    encoded_args = abi_encode(
        ["bytes32", "uint256[]"],
        [cond_id, amounts]
    )
    
    return NEG_RISK_REDEEM_SELECTOR + encoded_args.hex()


async def redeem_position(
    private_key: str,
    funder_address: str,
    condition_id: str,
    token_id: str,
    size: float,
    neg_risk: bool = False,
) -> dict:
    """
    Redeem a winning position via Polymarket's official Builder Relayer.
    
    Full $1.00/share payout. Zero gas fees (Polymarket pays).
    
    Args:
        private_key: User's EOA private key (hex)
        funder_address: User's proxy/funder wallet address
        condition_id: Market condition ID (from Data API)
        token_id: ERC1155 token ID / asset (from Data API)
        size: Number of shares
        neg_risk: Whether this is a negative-risk market
    
    Returns:
        {"success": True, "tx_hash": "0x..."} or
        {"success": False, "error": "..."}
    """
    result = {"success": False, "error": None, "tx_hash": None}
    
    try:
        from py_builder_relayer_client.client import RelayClient
        from py_builder_signing_sdk.config import BuilderConfig
        from py_builder_signing_sdk.sdk_types import BuilderApiKeyCreds
        
        # 1. Build relay client with Builder credentials
        builder_key = os.getenv("BUILDER_API_KEY", "") or os.getenv("BUILDER_KEY", "")
        builder_secret = os.getenv("BUILDER_SECRET", "")
        builder_passphrase = os.getenv("BUILDER_PASSPHRASE", "")
        
        if not all([builder_key, builder_secret, builder_passphrase]):
            result["error"] = "Builder credentials not configured. Set BUILDER_KEY, BUILDER_SECRET, BUILDER_PASSPHRASE."
            return result
        
        builder_config = BuilderConfig(
            local_builder_creds=BuilderApiKeyCreds(
                key=builder_key,
                secret=builder_secret,
                passphrase=builder_passphrase,
            )
        )
        
        relay_client = RelayClient(
            RELAYER_URL,
            CHAIN_ID,
            private_key,
            builder_config,
        )
        
        # 2. Encode the redeem transaction
        if neg_risk:
            # NegRiskAdapter.redeemPositions(conditionId, amounts[])
            target = NEG_RISK_ADAPTER
            call_data = _encode_neg_risk_redeem(condition_id)
            logger.info(f"Redeem: NegRisk market, condition={condition_id[:16]}...")
        else:
            # CTF.redeemPositions(collateral, parent, conditionId, [1,2])
            target = CTF_ADDRESS
            call_data = _encode_ctf_redeem(condition_id)
            logger.info(f"Redeem: Standard CTF market, condition={condition_id[:16]}...")
        
        # 3. Build the transaction object
        #    RelayClient expects objects with .to, .data, .value attributes
        try:
            from py_builder_relayer_client.types import Transaction
            tx = Transaction(
                to=target,
                data="0x" + call_data if not call_data.startswith("0x") else call_data,
                value="0",
            )
        except ImportError:
            # Fallback: try different import paths or create a simple object
            try:
                from py_builder_relayer_client.model import Transaction
                tx = Transaction(
                    to=target,
                    data="0x" + call_data if not call_data.startswith("0x") else call_data,
                    value="0",
                )
            except ImportError:
                # Last resort: create a simple namespace object
                from types import SimpleNamespace
                tx = SimpleNamespace(
                    to=target,
                    data="0x" + call_data if not call_data.startswith("0x") else call_data,
                    value="0",
                )
        
        # 4. Submit via relayer (gasless!)
        logger.info(f"Redeem: submitting to relayer for {funder_address[:10]}...")
        response = relay_client.execute([tx], "BetPoly: Redeem winning position")
        
        # 5. Wait for confirmation
        if response:
            tx_result = response.wait()
            
            if tx_result:
                tx_hash = None
                # Extract tx hash from various possible response formats
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
                
                state = None
                if hasattr(tx_result, "state"):
                    state = tx_result.state
                elif isinstance(tx_result, dict):
                    state = tx_result.get("state")
                
                # Check for failure states
                if state and "FAIL" in str(state).upper():
                    result["error"] = f"Transaction failed on-chain. State: {state}"
                    result["tx_hash"] = tx_hash
                    logger.error(f"Redeem FAILED on-chain: state={state}, tx={tx_hash}")
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
        result["error"] = f"Missing package: {e}. Install py-builder-relayer-client and py-builder-signing-sdk."
        logger.error(f"Redeem import error: {e}")
    except Exception as e:
        error_msg = str(e)
        logger.error(f"Redeem FAILED: {error_msg}")
        
        # User-friendly error messages
        if "quota exceeded" in error_msg.lower() or "rate limit" in error_msg.lower():
            result["error"] = "Rate limited. Please try again in a few minutes."
        elif "not redeemable" in error_msg.lower():
            result["error"] = "Market not yet resolved. Please wait."
        elif "401" in error_msg or "403" in error_msg or "unauthorized" in error_msg.lower():
            result["error"] = "Builder authentication failed. Check BUILDER_KEY/SECRET/PASSPHRASE."
        elif "revert" in error_msg.lower():
            result["error"] = "Transaction reverted. Market may not be resolved yet."
        else:
            result["error"] = f"Redemption failed: {error_msg[:200]}"
    
    return result
