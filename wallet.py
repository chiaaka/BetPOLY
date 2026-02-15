"""
BetPoly - Wallet (Gnosis Safe via Builder Relayer)

Uses Polymarket's official py-builder-relayer-client for gasless onboarding.
Flow matching polymarket.com:
  1. Deploy Safe (gasless via relayer)
  2. Set token approvals (gasless via relayer, batched)
  3. Create CLOB API credentials (off-chain EIP-712 signing)

All during signup. No gas needed. Polymarket pays.
"""
import hashlib
import logging
from eth_account import Account

from config import (
    MASTER_SALT, BUILDER_API_KEY, BUILDER_SECRET, BUILDER_PASSPHRASE,
)

logger = logging.getLogger("BetPoly.Wallet")

# Polymarket contracts on Polygon (from official docs)
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
# Builder config — exact pattern from official SDK
# =============================================================================

def _get_builder_config():
    """Create BuilderConfig using BuilderApiKeyCreds (official pattern)."""
    from py_builder_signing_sdk.config import BuilderConfig
    from py_builder_signing_sdk.sdk_types import BuilderApiKeyCreds

    creds = BuilderApiKeyCreds(
        key=BUILDER_API_KEY,
        secret=BUILDER_SECRET,
        passphrase=BUILDER_PASSPHRASE,
    )
    return BuilderConfig(local_builder_creds=creds)


def _get_relay_client(private_key: str):
    """Create RelayClient."""
    from py_builder_relayer_client.client import RelayClient

    builder_config = _get_builder_config()
    return RelayClient(
        relayer_url=RELAYER_URL,
        chain_id=CHAIN_ID,
        private_key=private_key,
        builder_config=builder_config,
    )


# =============================================================================
# Safe address derivation
# =============================================================================

def derive_safe_address(private_key: str) -> str:
    """Derive deterministic Safe address from EOA."""
    try:
        from py_builder_relayer_client.builder.derive import derive
        from py_builder_relayer_client.config import get_contract_config

        acct = Account.from_key(private_key)
        config = get_contract_config(CHAIN_ID)
        # Try various attribute patterns
        factory = getattr(config, 'safe_factory', None) or getattr(config, 'SafeFactory', None)
        if not factory:
            sc = getattr(config, 'SafeContracts', None) or getattr(config, 'safe_contracts', None)
            if sc:
                factory = getattr(sc, 'SafeFactory', None) or getattr(sc, 'safe_factory', None)
        if not factory:
            factory = "0xaacFeEa03eb1561C4e67d661e40682Bd20E3541b"
            logger.warning("Using hardcoded SafeFactory: %s", factory)

        safe_addr = derive(acct.address, factory)
        logger.info("Derived Safe: %s from EOA: %s", safe_addr, acct.address)
        return safe_addr
    except Exception as e:
        logger.error("derive_safe_address error: %s", e)
        raise RuntimeError(f"Cannot derive Safe address: {e}")


get_safe_address = derive_safe_address


# =============================================================================
# ABI encoding helpers
# =============================================================================

def _encode_approve(spender: str) -> str:
    """Encode ERC20 approve(address,uint256) calldata."""
    from web3 import Web3
    fn_sig = Web3.keccak(text="approve(address,uint256)")[:4]
    return ("0x" + fn_sig.hex()
            + spender[2:].lower().zfill(64)
            + MAX_UINT256.to_bytes(32, "big").hex())


def _encode_set_approval_for_all(operator: str) -> str:
    """Encode ERC1155 setApprovalForAll(address,bool) calldata."""
    from web3 import Web3
    fn_sig = Web3.keccak(text="setApprovalForAll(address,bool)")[:4]
    return ("0x" + fn_sig.hex()
            + operator[2:].lower().zfill(64)
            + (1).to_bytes(32, "big").hex())


def _build_approval_txs():
    """
    Build 7 approval transactions matching polymarket.com.
    Uses SafeTransaction model with OperationType.Call enum.
    """
    from py_builder_relayer_client.models import SafeTransaction
    
    # Import OperationType enum - the execute() method calls operation.value
    try:
        from py_builder_relayer_client.models import OperationType
        op_call = OperationType.Call
    except ImportError:
        try:
            from py_builder_relayer_client import OperationType
            op_call = OperationType.Call
        except ImportError:
            # Create a minimal enum-like object with .value attribute
            class _OpCall:
                value = 0
            op_call = _OpCall()
    
    txs = []
    
    # 4 ERC20 approvals: USDC.e → CTF, CTF_EXCHANGE, NEG_RISK_EXCHANGE, NEG_RISK_ADAPTER
    for spender in [CTF, CTF_EXCHANGE, NEG_RISK_EXCHANGE, NEG_RISK_ADAPTER]:
        txs.append(SafeTransaction(
            to=USDC_E,
            data=_encode_approve(spender),
            value="0",
            operation=op_call,
        ))
    
    # 3 ERC1155 approvals: CTF → CTF_EXCHANGE, NEG_RISK_EXCHANGE, NEG_RISK_ADAPTER
    for operator in [CTF_EXCHANGE, NEG_RISK_EXCHANGE, NEG_RISK_ADAPTER]:
        txs.append(SafeTransaction(
            to=CTF,
            data=_encode_set_approval_for_all(operator),
            value="0",
            operation=op_call,
        ))
    
    return txs


# =============================================================================
# Gasless onboarding: matching polymarket.com flow
# =============================================================================

def deploy_safe(private_key: str) -> dict:
    """Step 1: Deploy Safe via relayer (gasless)."""
    try:
        client = _get_relay_client(private_key)
        logger.info("Step 1: Deploying Safe via relayer...")
        response = client.deploy()

        safe_addr = None
        tx_hash = None
        
        # Handle various response types
        if hasattr(response, 'wait'):
            result = response.wait()
            if result:
                safe_addr = getattr(result, 'proxyAddress', None) or getattr(result, 'proxy_address', None)
                tx_hash = getattr(result, 'transactionHash', None) or getattr(result, 'transaction_hash', None)
        elif isinstance(response, dict):
            safe_addr = response.get("proxyAddress") or response.get("proxy_address") or response.get("address")
            tx_hash = response.get("transactionHash") or response.get("transaction_hash")
        
        if hasattr(response, 'proxyAddress'):
            safe_addr = safe_addr or response.proxyAddress
        if hasattr(response, 'proxy_address'):
            safe_addr = safe_addr or response.proxy_address

        if not safe_addr:
            safe_addr = derive_safe_address(private_key)

        logger.info("Safe deployed: %s (tx: %s)", safe_addr, tx_hash)
        return {"success": True, "safe_address": safe_addr, "tx_hash": tx_hash}

    except Exception as e:
        err = str(e)
        if any(w in err.lower() for w in ["already", "deployed", "exists"]):
            safe_addr = derive_safe_address(private_key)
            logger.info("Safe already deployed: %s", safe_addr)
            return {"success": True, "safe_address": safe_addr, "note": "already deployed"}
        logger.error("Deploy failed: %s", err[:300])
        return {"success": False, "error": err[:300]}


def set_approvals(private_key: str) -> dict:
    """Step 2: Set all approvals via relayer (gasless, batched)."""
    try:
        client = _get_relay_client(private_key)
        approval_txs = _build_approval_txs()
        logger.info("Step 2: Setting %d approvals via relayer...", len(approval_txs))
        response = client.execute(approval_txs, "Set all token approvals")

        tx_hash = None
        if hasattr(response, 'wait'):
            result = response.wait()
            if result:
                tx_hash = getattr(result, 'transactionHash', None) or getattr(result, 'transaction_hash', None)
        elif isinstance(response, dict):
            tx_hash = response.get("transactionHash") or response.get("transaction_hash")

        logger.info("All approvals set! tx: %s", tx_hash)
        return {"success": True, "tx_hash": tx_hash}

    except Exception as e:
        err = str(e)
        if "already" in err.lower():
            return {"success": True, "note": "already approved"}
        logger.error("Approvals failed: %s", err[:300])
        return {"success": False, "error": err[:300]}


def derive_api_creds(private_key: str, safe_address: str) -> dict:
    """Step 3: Create CLOB API credentials (off-chain signing)."""
    from py_clob_client.client import ClobClient
    client = ClobClient(
        host="https://clob.polymarket.com",
        key=private_key, chain_id=CHAIN_ID,
        signature_type=2, funder=safe_address,
    )
    creds = client.create_or_derive_api_creds()
    client.set_api_creds(creds)
    logger.info("Step 3: API credentials derived for Safe=%s", safe_address)
    return {
        "api_key": creds.api_key,
        "api_secret": creds.api_secret,
        "api_passphrase": creds.api_passphrase,
    }


def onboard_wallet(private_key: str) -> dict:
    """
    Complete gasless onboarding — matching polymarket.com:
      1. Deploy Safe wallet
      2. Set token approvals (batched)
      3. Derive CLOB API credentials
    Called during /start signup after PIN confirmation.
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

        # Step 3: Derive API creds
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
    return _get_relay_client(private_key)
