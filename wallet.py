"""
BetPoly - Wallet (Polymarket Safe Wallet Pattern)

Follows the exact Polymarket onboarding flow:
  1. Derive EOA from telegram_id + PIN + salt (deterministic, unchanged)
  2. Deploy a Gnosis Safe via relayer (gasless)
  3. Set all token approvals via relayer (gasless, batched)
  4. Use Safe address as user's deposit/trading wallet

The Safe address is what users deposit USDC.e to.
The EOA private key is what signs CLOB orders + relay requests.

Environment variables needed:
  MASTER_SALT          - for deterministic key derivation
  BUILDER_API_KEY      - Polymarket Builder credentials
  BUILDER_SECRET       - Polymarket Builder credentials
  BUILDER_PASSPHRASE   - Polymarket Builder credentials
"""

import hashlib
import logging
import os

from eth_account import Account
from config import MASTER_SALT

logger = logging.getLogger("BetPoly.Wallet")

# Polymarket contracts on Polygon
RELAYER_URL = "https://relayer-v2.polymarket.com"
CHAIN_ID = 137
CTF_CONTRACT = "0x4D97DCd97eC945f40cF65F87097ACe5EA0476045"
USDC_E = "0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174"
CTF_EXCHANGE = "0x4bFb41d5B3570DeFd03C39a9A4D8dE6Bd8B8982E"
NEG_RISK_CTF_EXCHANGE = "0xC5d563A36AE78145C45a50134d48A1215220f80a"
NEG_RISK_ADAPTER = "0xd91E80cF2E7be2e162c6513ceD06f1dD0dA35296"

# Per-session cache: eoa_address -> {"safe_address": str, "approved": bool}
_cache = {}


# ============================================================
# Core: Derive EOA (unchanged — same deterministic derivation)
# ============================================================

def derive_wallet(telegram_id: int, pin: str) -> dict:
    """Derive EOA private key from telegram_id + PIN + salt. Deterministic."""
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


# ============================================================
# Builder RelayClient
# ============================================================

def _get_relay_client(private_key: str):
    """Create RelayClient with Builder credentials for gasless txs."""
    from py_builder_relayer_client.client import RelayClient
    from py_builder_signing_sdk.config import BuilderConfig
    from py_builder_signing_sdk.sdk_types import BuilderApiKeyCreds

    key = os.getenv("BUILDER_API_KEY", "")
    secret = os.getenv("BUILDER_SECRET", "")
    passphrase = os.getenv("BUILDER_PASSPHRASE", "")

    if not all([key, secret, passphrase]):
        raise RuntimeError(
            "Missing Builder credentials. Set BUILDER_API_KEY, "
            "BUILDER_SECRET, BUILDER_PASSPHRASE."
        )

    config = BuilderConfig(
        local_builder_creds=BuilderApiKeyCreds(
            key=key, secret=secret, passphrase=passphrase,
        )
    )
    return RelayClient(RELAYER_URL, CHAIN_ID, private_key, config)


# ============================================================
# Safe Deployment (gasless)
# ============================================================

def deploy_safe(private_key: str) -> str:
    """
    Deploy a Gnosis Safe for this EOA via Polymarket relayer (gasless).
    Returns the Safe address. Idempotent — safe to call multiple times.
    """
    eoa = Account.from_key(private_key).address
    cached = _cache.get(eoa, {})
    if cached.get("safe_address"):
        return cached["safe_address"]

    logger.info("Deploying Safe for EOA %s...", eoa[:12])
    relay = _get_relay_client(private_key)

    response = relay.deploy()

    # Wait for confirmation
    result = response.wait() if hasattr(response, 'wait') else response

    # Extract safe address from result
    safe_addr = _extract_address(result)
    if not safe_addr:
        raise RuntimeError(f"deploy() returned no Safe address: {result}")

    _cache[eoa] = {"safe_address": safe_addr, "approved": cached.get("approved", False)}
    logger.info("Safe deployed: %s (EOA: %s)", safe_addr, eoa[:12])
    return safe_addr


def _extract_address(result) -> str:
    """Extract proxy/Safe address from various result formats."""
    if not result:
        return ""
    if isinstance(result, dict):
        return (result.get("proxyAddress") or result.get("proxy_address")
                or result.get("safeAddress") or result.get("safe_address") or "")
    for attr in ("proxyAddress", "proxy_address", "safeAddress", "safe_address"):
        if hasattr(result, attr):
            val = getattr(result, attr)
            if val:
                return str(val)
    if isinstance(result, str) and result.startswith("0x") and len(result) == 42:
        return result
    return ""


# ============================================================
# Token Approvals (gasless, batched)
# ============================================================

def set_approvals(private_key: str) -> bool:
    """
    Set all ERC20 + ERC1155 approvals via relayer (gasless, one batch).

    ERC20 approvals (USDC.e → spenders):
      CTF Contract, CTF Exchange, Neg Risk Exchange, Neg Risk Adapter

    ERC1155 approvals (CTF tokens → operators):
      CTF Exchange, Neg Risk CTF Exchange, Neg Risk Adapter
    """
    eoa = Account.from_key(private_key).address
    cached = _cache.get(eoa, {})
    if cached.get("approved"):
        return True

    logger.info("Setting token approvals for %s...", eoa[:12])
    relay = _get_relay_client(private_key)

    transactions = []

    # ERC20 approve(spender, MAX_UINT256)
    for spender in [CTF_CONTRACT, CTF_EXCHANGE, NEG_RISK_CTF_EXCHANGE, NEG_RISK_ADAPTER]:
        selector = "095ea7b3"
        addr = spender.lower().replace("0x", "").zfill(64)
        amount = "f" * 64
        transactions.append({
            "to": USDC_E,
            "data": "0x" + selector + addr + amount,
            "value": "0",
        })

    # ERC1155 setApprovalForAll(operator, true)
    for operator in [CTF_EXCHANGE, NEG_RISK_CTF_EXCHANGE, NEG_RISK_ADAPTER]:
        selector = "a22cb465"
        addr = operator.lower().replace("0x", "").zfill(64)
        true_val = "0" * 63 + "1"
        transactions.append({
            "to": CTF_CONTRACT,
            "data": "0x" + selector + addr + true_val,
            "value": "0",
        })

    try:
        response = relay.execute(transactions, "Set all token approvals")
        if hasattr(response, 'wait'):
            response.wait()

        if eoa in _cache:
            _cache[eoa]["approved"] = True
        else:
            _cache[eoa] = {"safe_address": "", "approved": True}

        logger.info("All approvals set for %s", eoa[:12])
        return True

    except Exception as e:
        error_msg = str(e).lower()
        if "already" in error_msg or "noop" in error_msg:
            logger.info("Approvals already set for %s", eoa[:12])
            if eoa in _cache:
                _cache[eoa]["approved"] = True
            return True
        logger.error("Approvals failed: %s", str(e)[:300])
        return False


# ============================================================
# Full Onboarding: Deploy + Approve
# ============================================================

def onboard_wallet(private_key: str) -> dict:
    """
    Full Polymarket wallet setup:
      1. Deploy Safe (gasless)
      2. Set all approvals (gasless)

    Returns: {"eoa_address": ..., "safe_address": ..., "success": bool, "error": ...}
    """
    result = {"eoa_address": "", "safe_address": "", "success": False, "error": None}

    try:
        eoa = Account.from_key(private_key).address
        result["eoa_address"] = eoa

        safe_addr = deploy_safe(private_key)
        result["safe_address"] = safe_addr

        approved = set_approvals(private_key)
        if not approved:
            result["error"] = "Safe deployed but approval tx failed. Retry later."
            # Still return success=True — Safe exists, user can deposit
            # Approvals will be retried on first trade

        result["success"] = True
        logger.info("Onboarded: EOA=%s → Safe=%s", eoa[:12], safe_addr[:12])

    except Exception as e:
        result["error"] = str(e)[:200]
        logger.error("Onboard failed: %s", result["error"])

    return result
