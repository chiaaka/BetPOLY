"""
redeem.py — Polymarket Gasless Redemption via poly-web3

Uses poly-web3 (pip install poly-web3), a Python SDK specifically designed
for Polymarket Proxy wallet redeem operations. It handles all the proxy wallet
signing, relay payload encoding, and relayer submission internally.

Flow:
  1. Initialize ClobClient (for wallet context)
  2. Initialize RelayClient (for gasless tx submission)  
  3. Create PolyWeb3Service (handles proxy wallet redeem logic)
  4. Call service.redeem(condition_id, neg_risk, redeem_amounts)
  5. User gets full $1.00/share as USDC.e, zero gas

Required pip packages:
  poly-web3
  py-clob-client
  py-builder-relayer-client
  py-builder-signing-sdk

Required env vars:
  PK (private key), POLYMARKET_PROXY_ADDRESS (funder),
  BUILDER_API_KEY, BUILDER_SECRET, BUILDER_PASSPHRASE
"""

import os
import logging

logger = logging.getLogger(__name__)

RELAYER_URL = "https://relayer-v2.polymarket.com/"
CLOB_HOST = "https://clob.polymarket.com"
CHAIN_ID = 137


async def redeem_position(
    private_key: str,
    funder_address: str,
    condition_id: str,
    token_id: str,
    size: float,
    neg_risk: bool = False,
) -> dict:
    """
    Redeem a winning position via poly-web3's PolyWeb3Service.
    
    Full $1.00/share payout. Zero gas fees (Polymarket pays via relayer).
    
    Args:
        private_key: User's EOA private key (hex)
        funder_address: User's proxy wallet address
        condition_id: Market condition ID
        token_id: ERC1155 token ID (unused by poly-web3 but kept for interface)
        size: Number of shares (used for neg_risk amounts)
        neg_risk: Whether this is a negative-risk market
    
    Returns:
        {"success": True, "tx_hash": "0x..."} or
        {"success": False, "error": "..."}
    """
    result = {"success": False, "error": None, "tx_hash": None}
    
    try:
        from py_clob_client.client import ClobClient
        from py_builder_relayer_client.client import RelayClient
        from py_builder_signing_sdk.config import BuilderConfig
        from py_builder_signing_sdk.sdk_types import BuilderApiKeyCreds
        from poly_web3 import PolyWeb3Service
        
        # 1. Builder credentials
        builder_key = os.getenv("BUILDER_API_KEY", "") or os.getenv("BUILDER_KEY", "")
        builder_secret = os.getenv("BUILDER_SECRET", "")
        builder_passphrase = os.getenv("BUILDER_PASSPHRASE", "")
        
        if not all([builder_key, builder_secret, builder_passphrase]):
            result["error"] = "Builder credentials not configured."
            return result
        
        # 2. Initialize ClobClient (Proxy wallet, signature_type=1)
        clob_client = ClobClient(
            CLOB_HOST,
            key=private_key,
            chain_id=CHAIN_ID,
            signature_type=1,  # Proxy wallet
            funder=funder_address,
        )
        clob_client.set_api_creds(clob_client.create_or_derive_api_creds())
        
        # 3. Initialize RelayClient
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
        
        # 4. Create PolyWeb3Service
        service = PolyWeb3Service(
            clob_client=clob_client,
            relayer_client=relay_client,
        )
        
        # 5. Execute redeem
        logger.info(f"Redeem: condition={condition_id[:16]}..., neg_risk={neg_risk}, size={size}")
        
        if neg_risk:
            # For neg_risk markets, pass amounts as raw token units (6 decimals)
            raw_amount = int(size * 1_000_000)
            redeem_amounts = [raw_amount, 0]  # [winning_amount, losing_amount]
            logger.info(f"Redeem: NegRisk with amounts={redeem_amounts}")
            
            redeem_result = service.redeem(
                condition_id=condition_id,
                neg_risk=True,
                redeem_amounts=redeem_amounts,
            )
        else:
            # Standard CTF redeem — poly-web3 handles indexSets automatically
            logger.info(f"Redeem: Standard CTF")
            redeem_result = service.redeem(
                condition_id=condition_id,
                neg_risk=False,
            )
        
        logger.info(f"Redeem result: {redeem_result}")
        
        # 6. Parse result
        if redeem_result:
            tx_hash = None
            if isinstance(redeem_result, dict):
                tx_hash = (
                    redeem_result.get("transactionHash")
                    or redeem_result.get("transaction_hash")
                    or redeem_result.get("hash")
                )
                state = redeem_result.get("state", "")
                if "FAIL" in str(state).upper():
                    result["error"] = f"Transaction failed on-chain: {state}"
                    result["tx_hash"] = tx_hash
                    logger.error(f"Redeem FAILED: state={state}, tx={tx_hash}")
                    return result
            elif isinstance(redeem_result, str):
                tx_hash = redeem_result
            elif hasattr(redeem_result, "transactionHash"):
                tx_hash = redeem_result.transactionHash
            elif hasattr(redeem_result, "transaction_hash"):
                tx_hash = redeem_result.transaction_hash
            
            result["success"] = True
            result["tx_hash"] = tx_hash
            logger.info(f"Redeem SUCCESS: tx={tx_hash}")
        else:
            result["error"] = "Redeem returned empty result."
            logger.error("Redeem: empty result from service.redeem()")
            
    except ImportError as e:
        result["error"] = f"Missing package: {e}. Run: pip install poly-web3"
        logger.error(f"Redeem import error: {e}")
    except Exception as e:
        error_msg = str(e)
        logger.error(f"Redeem FAILED: {error_msg}")
        
        if "quota exceeded" in error_msg.lower() or "rate limit" in error_msg.lower():
            result["error"] = "Rate limited. Try again in a few minutes."
        elif "not redeemable" in error_msg.lower() or "not resolved" in error_msg.lower():
            result["error"] = "Market not yet resolved. Please wait."
        elif "401" in error_msg or "403" in error_msg or "unauthorized" in error_msg.lower():
            result["error"] = "Builder authentication failed."
        elif "revert" in error_msg.lower():
            result["error"] = "Transaction reverted on-chain."
        else:
            result["error"] = f"Redemption failed: {error_msg[:200]}"
    
    return result
