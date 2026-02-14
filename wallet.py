"""
BetPoly - Wallet (Gnosis Safe)

Deterministic wallet: hash(telegram_id + PIN + master_salt) = private key (EOA).
From that EOA, a Gnosis Safe is deployed (deterministic address).
Same inputs = same EOA = same Safe address. Every time. Forever.

Flow:
  1. derive_wallet() → EOA keypair (unchanged from before)
  2. get_safe_address() → deterministic Safe from EOA
  3. onboard_wallet() → deploy Safe + set all approvals (one-time, needs ~0.1 POL)
  4. After onboard: users deposit USDC to Safe address
  5. All trading + redeem is gasless via Polymarket relayer

Security:
  - Private key never stored. Derived from PIN + salt each time.
  - Safe is controlled ONLY by the user's EOA key.
  - No one (including us) can move funds without the user's PIN.
"""
import hashlib
import logging
from eth_account import Account
from config import MASTER_SALT

logger = logging.getLogger("BetPoly.Wallet")


# =============================================================================
# Core derivation (UNCHANGED - same wallet every time)
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
# Gnosis Safe (deterministic from EOA)
# =============================================================================

def get_safe_address(private_key: str) -> str:
    """
    Get deterministic Gnosis Safe address for this EOA.
    Same EOA → same Safe. Always. Even before deployment.
    """
    from polymarket_apis import PolymarketWeb3Client
    w3 = PolymarketWeb3Client(private_key=private_key, signature_type=2, chain_id=137)
    return w3.address


def deploy_safe(private_key: str) -> dict:
    """
    Deploy the Gnosis Safe on-chain. One-time. Needs ~0.05 POL gas in EOA.
    If already deployed, returns success with a note.
    """
    from polymarket_apis import PolymarketWeb3Client
    try:
        w3 = PolymarketWeb3Client(private_key=private_key, signature_type=2, chain_id=137)
        logger.info("Deploying Safe at %s...", w3.address)
        result = w3.deploy_safe()
        logger.info("Deploy result: %s", result)
        return {"success": True, "safe_address": w3.address, "tx": str(result)}
    except Exception as e:
        err = str(e)
        if "already" in err.lower() or "deployed" in err.lower():
            return {"success": True, "safe_address": get_safe_address(private_key), "note": "already deployed"}
        logger.error("Deploy failed: %s", err[:300])
        return {"success": False, "error": err[:300]}


def set_approvals(private_key: str) -> dict:
    """
    Set ALL token approvals (USDC.e ERC-20 + CTF ERC-1155) for all Polymarket contracts.
    One-time. Needs ~0.05 POL gas in EOA.
    After this, all operations are gasless via Polymarket relayer.
    """
    from polymarket_apis import PolymarketWeb3Client
    try:
        w3 = PolymarketWeb3Client(private_key=private_key, signature_type=2, chain_id=137)
        logger.info("Setting all token approvals for Safe %s...", w3.address)
        w3.set_approvals()
        logger.info("All approvals set successfully!")
        return {"success": True}
    except Exception as e:
        err = str(e)
        if "already" in err.lower():
            return {"success": True, "note": "already approved"}
        logger.error("Approvals failed: %s", err[:300])
        return {"success": False, "error": err[:300]}


def onboard_wallet(private_key: str) -> dict:
    """
    Complete Polymarket onboarding in one call.
    Needs ~0.1 POL in EOA for one-time gas (deploy + approvals).
    
    Returns: {success, eoa_address, safe_address, api_creds, error}
    
    After this completes:
    - User deposits USDC to safe_address
    - All trading is gasless
    - Redeem/payout is gasless
    """
    result = {
        "success": False,
        "eoa_address": None,
        "safe_address": None,
        "api_creds": None,
        "error": None,
    }
    try:
        acct = Account.from_key(private_key)
        result["eoa_address"] = acct.address

        safe_addr = get_safe_address(private_key)
        result["safe_address"] = safe_addr
        logger.info("EOA=%s Safe=%s", acct.address, safe_addr)

        dep = deploy_safe(private_key)
        if not dep.get("success"):
            result["error"] = f"Safe deploy failed: {dep.get('error')}"
            return result
        logger.info("Safe deployed: %s", dep)

        appr = set_approvals(private_key)
        if not appr.get("success"):
            result["error"] = f"Approvals failed: {appr.get('error')}"
            return result
        logger.info("Approvals set: %s", appr)

        creds = derive_api_creds(private_key, safe_addr)
        result["api_creds"] = creds
        result["success"] = True
        logger.info("Onboarding complete! Safe=%s", safe_addr)

    except Exception as e:
        result["error"] = str(e)[:300]
        logger.error("Onboarding failed: %s", result["error"])
    return result


def derive_api_creds(private_key: str, safe_address: str) -> dict:
    """Derive CLOB API credentials using Safe wallet."""
    from py_clob_client.client import ClobClient
    client = ClobClient(
        host="https://clob.polymarket.com",
        key=private_key, chain_id=137,
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
    """
    Create ClobClient with Safe wallet config (signature_type=2).
    This is what trade.py uses for all buy/sell operations.
    """
    from py_clob_client.client import ClobClient
    from py_clob_client.clob_types import ApiCreds

    kwargs = dict(
        host="https://clob.polymarket.com",
        key=private_key, chain_id=137,
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


def create_gasless_client(private_key: str):
    """
    Create GaslessWeb3Client for redeem/split/merge.
    Uses signature_type=2 (Safe). All operations are free.
    """
    from polymarket_apis import PolymarketGaslessWeb3Client
    return PolymarketGaslessWeb3Client(
        private_key=private_key, signature_type=2, chain_id=137,
    )
