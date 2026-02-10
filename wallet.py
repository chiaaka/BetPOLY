"""
BetPoly - Wallet
Deterministic wallet: hash(telegram_id + PIN + master_salt) = private key.
NOTHING stored. Same inputs = same wallet every time.
"""
import hashlib
from eth_account import Account
from config import MASTER_SALT


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
