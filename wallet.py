"""
BetPoly - Wallet (Gnosis Safe via Builder Relayer)

100% GASLESS wallet creation + onboarding via Polymarket's Builder Relayer.
No POL/MATIC needed. Polymarket pays all gas fees.

Flow:
  1. derive_wallet()       → deterministic EOA keypair (from PIN + salt)
  2. derive_safe_address() → deterministic Safe address (from EOA, CREATE2)
  3. onboard_wallet()      → deploy Safe + set all approvals (GASLESS via relayer)
  4. User deposits USDC.e to Safe address
  5. All trading + redeem is gasless forever

Security:
  - Private key never stored. Derived from PIN + salt each time.
  - Safe is controlled ONLY by the user's EOA key.
  - Builder credentials are server-side only.
  - Same inputs → same EOA → same Safe. Every time. Forever.
"""
import hashlib
import logging
from eth_account import Account
from web3 import Web3

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

RELAYER_URL = "https://relayer-v2.polymarket.com/"
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
# Safe address derivation (deterministic from EOA, no gas needed)
# =============================================================================

def derive_safe_address(private_key: str) -> str:
    """
    Derive the deterministic Gnosis Safe address from an EOA.
    Same EOA → same Safe address. Always. Even before deployment.
    Uses the same CREATE2 derivation as Polymarket.
    """
    try:
        from py_builder_relayer_client import RelayClient
        client = _get_relay_client(private_key)
        return client.address
    except Exception as e1:
        logger.debug("py_builder_relayer_client derivation failed: %s", e1)
        # Fallback to polymarket_apis
        try:
            from polymarket_apis import PolymarketWeb3Client
            w3c = PolymarketWeb3Client(
                private_key=private_key, signature_type=2, chain_id=CHAIN_ID
            )
            return w3c.address
        except Exception as e2:
            logger.error("Safe address derivation failed: %s / %s", e1, e2)
            raise RuntimeError(f"Cannot derive Safe address: {e1}")


def _get_relay_client(private_key: str):
    """Create a Builder Relayer client for gasless operations."""
    from py_builder_relayer_client import RelayClient
    from py_builder_relayer_client.signer import Signer

    signer = Signer(private_key=private_key, chain_id=CHAIN_ID)
    client = RelayClient(
        relayer_url=RELAYER_URL,
        chain_id=CHAIN_ID,
        signer=signer,
        builder_api_key=BUILDER_API_KEY,
        builder_secret=BUILDER_SECRET,
        builder_passphrase=BUILDER_PASSPHRASE,
    )
    return client


# =============================================================================
# ABI encoding helpers
# =============================================================================

_w3 = Web3()

def _encode_erc20_approve(spender: str) -> str:
    """Encode ERC-20 approve(spender, maxUint256) calldata."""
    fn_sig = _w3.keccak(text="approve(address,uint256)")[:4]
    spender_bytes = bytes.fromhex(spender[2:].lower().zfill(64))
    amount_bytes = MAX_UINT256.to_bytes(32, "big")
    return "0x" + fn_sig.hex() + spender_bytes.hex() + amount_bytes.hex()


def _encode_erc1155_set_approval(operator: str) -> str:
    """Encode ERC-1155 setApprovalForAll(operator, true) calldata."""
    fn_sig = _w3.keccak(text="setApprovalForAll(address,bool)")[:4]
    operator_bytes = bytes.fromhex(operator[2:].lower().zfill(64))
    true_bytes = (1).to_bytes(32, "big")
    return "0x" + fn_sig.hex() + operator_bytes.hex() + true_bytes.hex()


def _build_approval_txs() -> list:
    """
    Build ALL 7 approval transactions for Polymarket.
    4 ERC-20 (USDC.e approve) + 3 ERC-1155 (CTF setApprovalForAll).
    Batched into one gasless relay call.
    """
    txs = []
    # ERC-20: Approve USDC.e for 4 spenders
    for spender in [CTF, CTF_EXCHANGE, NEG_RISK_EXCHANGE, NEG_RISK_ADAPTER]:
        txs.append({"to": USDC_E, "data": _encode_erc20_approve(spender), "value": "0"})
    # ERC-1155: Approve CTF outcome tokens for 3 operators
    for operator in [CTF_EXCHANGE, NEG_RISK_EXCHANGE, NEG_RISK_ADAPTER]:
        txs.append({"to": CTF, "data": _encode_erc1155_set_approval(operator), "value": "0"})
    return txs


# =============================================================================
# Gasless onboarding (deploy Safe + approvals via Polymarket Relayer)
# =============================================================================

def deploy_safe(private_key: str) -> dict:
    """Deploy Gnosis Safe via Polymarket Builder Relayer. GASLESS."""
    try:
        client = _get_relay_client(private_key)
        logger.info("Deploying Safe via relayer (gasless)...")

        response = client.deploy()

        # Parse response (may vary by library version)
        safe_addr = None
        tx_hash = None
        if hasattr(response, 'wait'):
            result = response.wait()
            safe_addr = getattr(result, 'proxyAddress', None) or getattr(result, 'proxy_address', None)
            tx_hash = getattr(result, 'transactionHash', None) or getattr(result, 'transaction_hash', None)
        elif isinstance(response, dict):
            safe_addr = response.get("proxyAddress") or response.get("proxy_address")
            tx_hash = response.get("transactionHash") or response.get("transaction_hash")

        if not safe_addr:
            safe_addr = derive_safe_address(private_key)

        logger.info("Safe deployed: %s (tx: %s)", safe_addr, tx_hash)
        return {"success": True, "safe_address": safe_addr, "tx_hash": tx_hash}

    except Exception as e:
        err = str(e)
        if any(w in err.lower() for w in ["already", "deployed", "exists"]):
            safe_addr = derive_safe_address(private_key)
            return {"success": True, "safe_address": safe_addr, "note": "already deployed"}
        logger.error("Deploy failed: %s", err[:300])
        return {"success": False, "error": err[:300]}


def set_approvals(private_key: str) -> dict:
    """Set ALL token approvals via Builder Relayer. GASLESS. Batched."""
    try:
        client = _get_relay_client(private_key)
        approval_txs = _build_approval_txs()

        logger.info("Setting %d approvals via relayer (gasless)...", len(approval_txs))
        response = client.execute(approval_txs, "Set all token approvals")

        tx_hash = None
        if hasattr(response, 'wait'):
            result = response.wait()
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


def onboard_wallet(private_key: str) -> dict:
    """
    Complete Polymarket onboarding in one call. 100% GASLESS.

    1. Deploy Gnosis Safe (relayer — Polymarket pays gas)
    2. Set all token approvals (relayer — Polymarket pays gas)
    3. Derive CLOB API credentials (off-chain signing only)

    Returns: {success, eoa_address, safe_address, api_creds, error}
    """
    result = {
        "success": False, "eoa_address": None,
        "safe_address": None, "api_creds": None, "error": None,
    }
    try:
        acct = Account.from_key(private_key)
        result["eoa_address"] = acct.address

        # Step 1: Deploy Safe (gasless)
        dep = deploy_safe(private_key)
        if not dep.get("success"):
            result["error"] = f"Safe deploy failed: {dep.get('error')}"
            return result

        safe_addr = dep.get("safe_address") or derive_safe_address(private_key)
        result["safe_address"] = safe_addr
        logger.info("EOA=%s Safe=%s", acct.address, safe_addr)

        # Step 2: Set approvals (gasless)
        appr = set_approvals(private_key)
        if not appr.get("success"):
            result["error"] = f"Approvals failed: {appr.get('error')}"
            return result

        # Step 3: Derive API credentials (off-chain, no gas)
        creds = derive_api_creds(private_key, safe_addr)
        result["api_creds"] = creds
        result["success"] = True
        logger.info("Onboarding complete! Safe=%s", safe_addr)

    except Exception as e:
        result["error"] = str(e)[:300]
        logger.error("Onboarding failed: %s", result["error"])
    return result


# =============================================================================
# CLOB API credentials (off-chain, no gas)
# =============================================================================

def derive_api_creds(private_key: str, safe_address: str) -> dict:
    """Derive CLOB API credentials. Off-chain signing only, no gas."""
    from py_clob_client.client import ClobClient
    client = ClobClient(
        host="https://clob.polymarket.com",
        key=private_key, chain_id=CHAIN_ID,
        signature_type=2, funder=safe_address,
    )
    creds = client.create_or_derive_api_creds()
    client.set_api_creds(creds)
    return {
        "api_key": creds.api_key,
        "api_secret": creds.api_secret,
        "api_passphrase": creds.api_passphrase,
    }


# =============================================================================
# Client creation (for trading + redeem)
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
    """Create Builder Relayer client for gasless ops (redeem, transfer, etc)."""
    return _get_relay_client(private_key)


# Alias for backward compat
get_safe_address = derive_safe_address
