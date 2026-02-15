"""
BetPoly - Wallet (Gnosis Safe via Builder Relayer)

Uses Polymarket's official py-builder-relayer-client for gasless onboarding.
Matches the 3-signature flow on polymarket.com:
  1. Deploy Safe (gasless)
  2. Set token approvals (gasless)  
  3. Create CLOB API credentials (off-chain signing)

All during signup. No gas needed. Polymarket pays.
"""
import hashlib
import logging
from eth_account import Account

from config import (
    MASTER_SALT, BUILDER_API_KEY, BUILDER_SECRET, BUILDER_PASSPHRASE,
    POLYGON_RPC_URL,
)

logger = logging.getLogger("BetPoly.Wallet")

# Polymarket contracts on Polygon
USDC_E = "0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174"
CTF = "0x4D97DCd97eC945f40cF65F87097ACe5EA0476045"
CTF_EXCHANGE = "0x4bFb41d5B3570DeFd03C39a9A4D8dE6Bd8B8982E"
NEG_RISK_EXCHANGE = "0xC5d563A36AE78145C45a50134d48A1215220f80a"
NEG_RISK_ADAPTER = "0xd91E80cF2E7be2e162c6513ceD06f1dD0dA35296"

RELAYER_URL = "https://relayer-v2.polymarket.com"
CHAIN_ID = 137
MAX_UINT256 = 2**256 - 1


# =============================================================================
# Core derivation (deterministic — same wallet every time)
# =============================================================================

def derive_wallet(telegram_id: int, pin: str) -> dict:
    """Deterministic EOA: same inputs = same wallet. Always."""
    if not MASTER_SALT:
        raise ValueError("MASTER_SALT not configured")
    seed_input = f"{telegram_id}:{pin}:{MASTER_SALT}"
    seed = hashlib.sha256(hashlib.sha256(seed_input.encode()).digest()).digest()
    acct = Account.from_key(seed)
    return {"address": acct.address, "private_key": acct.key.hex()}


def get_address(telegram_id: int, pin: str) -> str:
    return derive_wallet(telegram_id, pin)["address"]


def export_key(telegram_id: int, pin: str) -> str:
    return derive_wallet(telegram_id, pin)["private_key"]


# =============================================================================
# Builder Relayer client (the REAL API from py_builder_relayer_client)
# =============================================================================

def _get_builder_config():
    """Create BuilderConfig with local credentials."""
    from py_builder_signing_sdk.config import BuilderConfig
    return BuilderConfig(
        api_key=BUILDER_API_KEY,
        api_secret=BUILDER_SECRET,
        api_passphrase=BUILDER_PASSPHRASE,
    )


def _get_relay_client(private_key: str):
    """
    Create RelayClient using the actual py_builder_relayer_client API.
    
    The real constructor is:
        RelayClient(relayer_url, chain_id, private_key=str, builder_config=BuilderConfig)
    """
    from py_builder_relayer_client.client import RelayClient
    
    builder_config = _get_builder_config()
    client = RelayClient(
        relayer_url=RELAYER_URL,
        chain_id=CHAIN_ID,
        private_key=private_key,
        builder_config=builder_config,
    )
    return client


# =============================================================================
# Safe address derivation (deterministic, from the relayer client's derive)
# =============================================================================

def derive_safe_address(private_key: str) -> str:
    """
    Derive deterministic Safe address using the relayer client's built-in derive.
    Same EOA → same Safe. Always.
    """
    try:
        from py_builder_relayer_client.builder.derive import derive
        from py_builder_relayer_client.config import get_contract_config
        
        acct = Account.from_key(private_key)
        config = get_contract_config(CHAIN_ID)
        safe_addr = derive(acct.address, config.safe_factory)
        logger.info("Derived Safe address: %s for EOA: %s", safe_addr, acct.address)
        return safe_addr
    except Exception as e:
        logger.error("derive_safe_address error: %s", e)
        # Fallback: use ClobClient's derivation
        try:
            from py_clob_client.client import ClobClient
            temp = ClobClient(
                host="https://clob.polymarket.com",
                key=private_key, chain_id=CHAIN_ID,
                signature_type=2,
            )
            addr = temp.get_address()
            if addr:
                return addr
        except Exception as e2:
            logger.error("ClobClient fallback failed: %s", e2)
        raise RuntimeError(f"Cannot derive Safe address: {e}")


# Backward compat alias
get_safe_address = derive_safe_address


# =============================================================================
# ABI encoding helpers (for approval transactions)
# =============================================================================

def _encode_approve(spender: str) -> str:
    """Encode ERC-20 approve(spender, maxUint256)."""
    from web3 import Web3
    w3 = Web3()
    fn_sig = w3.keccak(text="approve(address,uint256)")[:4]
    return "0x" + fn_sig.hex() + spender[2:].lower().zfill(64) + MAX_UINT256.to_bytes(32, "big").hex()


def _encode_set_approval_for_all(operator: str) -> str:
    """Encode ERC-1155 setApprovalForAll(operator, true)."""
    from web3 import Web3
    w3 = Web3()
    fn_sig = w3.keccak(text="setApprovalForAll(address,bool)")[:4]
    return "0x" + fn_sig.hex() + operator[2:].lower().zfill(64) + (1).to_bytes(32, "big").hex()


def _build_approval_txs():
    """
    Build 7 approval SafeTransactions for Polymarket.
    Uses the actual SafeTransaction model from py_builder_relayer_client.
    """
    from py_builder_relayer_client.models import SafeTransaction
    
    txs = []
    # ERC-20: Approve USDC.e for 4 spenders
    for spender in [CTF, CTF_EXCHANGE, NEG_RISK_EXCHANGE, NEG_RISK_ADAPTER]:
        txs.append(SafeTransaction(
            to=USDC_E,
            data=_encode_approve(spender),
            value="0",
        ))
    # ERC-1155: Approve CTF outcome tokens for 3 operators
    for op in [CTF_EXCHANGE, NEG_RISK_EXCHANGE, NEG_RISK_ADAPTER]:
        txs.append(SafeTransaction(
            to=CTF,
            data=_encode_set_approval_for_all(op),
            value="0",
        ))
    return txs


# =============================================================================
# Gasless onboarding: 3 steps matching Polymarket.com
# =============================================================================

def deploy_safe(private_key: str) -> dict:
    """
    Step 1: Deploy Gnosis Safe via Polymarket Builder Relayer.
    GASLESS — Polymarket pays gas. Just signing.
    """
    try:
        client = _get_relay_client(private_key)
        
        logger.info("Step 1: Deploying Safe via relayer (gasless)...")
        response = client.deploy()
        
        # Parse response
        safe_addr = None
        tx_hash = None
        if hasattr(response, 'wait'):
            result = response.wait()
            safe_addr = getattr(result, 'proxyAddress', None) or getattr(result, 'proxy_address', None)
            tx_hash = getattr(result, 'transactionHash', None) or getattr(result, 'transaction_hash', None)
        elif isinstance(response, dict):
            safe_addr = response.get("proxyAddress") or response.get("proxy_address") or response.get("address")
            tx_hash = response.get("transactionHash") or response.get("transaction_hash") or response.get("hash")
        elif hasattr(response, 'proxy_address'):
            safe_addr = response.proxy_address
            tx_hash = getattr(response, 'transaction_hash', None)
        
        if not safe_addr:
            safe_addr = derive_safe_address(private_key)
        
        logger.info("Safe deployed: %s (tx: %s)", safe_addr, tx_hash)
        return {"success": True, "safe_address": safe_addr, "tx_hash": tx_hash}
    
    except Exception as e:
        err = str(e)
        # "already deployed" is success
        if any(w in err.lower() for w in ["already", "deployed", "exists"]):
            safe_addr = derive_safe_address(private_key)
            logger.info("Safe already deployed: %s", safe_addr)
            return {"success": True, "safe_address": safe_addr, "note": "already deployed"}
        logger.error("Deploy failed: %s", err[:300])
        return {"success": False, "error": err[:300]}


def set_approvals(private_key: str) -> dict:
    """
    Step 2: Set ALL token approvals via Builder Relayer.
    GASLESS — batches 7 approvals into one relay call. Polymarket pays gas.
    """
    try:
        client = _get_relay_client(private_key)
        approval_txs = _build_approval_txs()
        
        logger.info("Step 2: Setting %d approvals via relayer (gasless)...", len(approval_txs))
        response = client.execute(approval_txs, "Set all token approvals")
        
        tx_hash = None
        if hasattr(response, 'wait'):
            result = response.wait()
            tx_hash = getattr(result, 'transaction_hash', None) or getattr(result, 'transactionHash', None)
        elif isinstance(response, dict):
            tx_hash = response.get("transactionHash") or response.get("transaction_hash") or response.get("hash")
        elif hasattr(response, 'transaction_hash'):
            tx_hash = response.transaction_hash
        
        logger.info("All approvals set! tx: %s", tx_hash)
        return {"success": True, "tx_hash": tx_hash}
    
    except Exception as e:
        err = str(e)
        if "already" in err.lower():
            return {"success": True, "note": "already approved"}
        logger.error("Approvals failed: %s", err[:300])
        return {"success": False, "error": err[:300]}


def derive_api_creds(private_key: str, safe_address: str) -> dict:
    """
    Step 3: Create CLOB API credentials.
    Off-chain signing only — no gas needed.
    """
    from py_clob_client.client import ClobClient
    client = ClobClient(
        host="https://clob.polymarket.com",
        key=private_key, chain_id=CHAIN_ID,
        signature_type=2, funder=safe_address,
    )
    creds = client.create_or_derive_api_creds()
    client.set_api_creds(creds)
    logger.info("Step 3: API credentials derived")
    return {
        "api_key": creds.api_key,
        "api_secret": creds.api_secret,
        "api_passphrase": creds.api_passphrase,
    }


def onboard_wallet(private_key: str) -> dict:
    """
    Complete Polymarket onboarding — 3 gasless signatures.
    Matches polymarket.com exactly:
      1. Deploy Safe (gasless via relayer)
      2. Set all token approvals (gasless via relayer)
      3. Create CLOB API credentials (off-chain signing)

    Called during /start signup, NOT at bet time.
    """
    result = {
        "success": False, "eoa_address": None,
        "safe_address": None, "api_creds": None, "error": None,
    }
    try:
        acct = Account.from_key(private_key)
        result["eoa_address"] = acct.address
        
        # Step 1: Deploy Safe
        dep = deploy_safe(private_key)
        if not dep.get("success"):
            result["error"] = f"Safe deploy failed: {dep.get('error')}"
            return result
        
        safe_addr = dep.get("safe_address") or derive_safe_address(private_key)
        result["safe_address"] = safe_addr
        logger.info("EOA=%s Safe=%s", acct.address, safe_addr)
        
        # Step 2: Set approvals
        appr = set_approvals(private_key)
        if not appr.get("success"):
            result["error"] = f"Approvals failed: {appr.get('error')}"
            return result
        
        # Step 3: API credentials
        creds = derive_api_creds(private_key, safe_addr)
        result["api_creds"] = creds
        result["success"] = True
        logger.info("Onboarding complete! Safe=%s", safe_addr)
    
    except Exception as e:
        result["error"] = str(e)[:300]
        logger.error("Onboarding failed: %s", result["error"])
    return result


# =============================================================================
# Client creation (for trading)
# =============================================================================

def create_clob_client(private_key: str, safe_address: str, api_creds: dict = None):
    """Create ClobClient with Safe wallet (signature_type=2) for trading."""
    from py_clob_client.client import ClobClient
    from py_clob_client.clob_types import ApiCreds
    
    kwargs = dict(
        host="https://clob.polymarket.com",
        key=private_key, chain_id=CHAIN_ID,
        signature_type=2, funder=safe_address,
    )
    if api_creds:
        kwargs["creds"] = ApiCreds(
            api_key=api_creds["api_key"],
            api_secret=api_creds["api_secret"],
            api_passphrase=api_creds["api_passphrase"],
        )
    client = ClobClient(**kwargs)
    if not api_creds:
        creds = client.create_or_derive_api_creds()
        client.set_api_creds(creds)
    return client


def create_relay_client(private_key: str):
    """Create Builder Relayer client for gasless ops (redeem, transfer)."""
    return _get_relay_client(private_key)
