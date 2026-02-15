"""
BetPoly - Database
SQLite storage for users, bets, referrals, and fee tracking.
NO private keys stored. Only public wallet addresses.
"""
import aiosqlite
import hashlib
import time
from config import DATABASE_PATH

_DB = DATABASE_PATH


async def init_db():
    async with aiosqlite.connect(_DB) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS users (
                telegram_id INTEGER PRIMARY KEY,
                username TEXT,
                wallet_address TEXT,
                safe_address TEXT,
                onboarded INTEGER DEFAULT 0,
                pin_hash TEXT,
                currency TEXT DEFAULT 'USD',
                referred_by INTEGER DEFAULT NULL,
                referral_code TEXT,
                total_volume REAL DEFAULT 0,
                total_fees_paid REAL DEFAULT 0,
                total_referral_earned REAL DEFAULT 0,
                created_at REAL,
                last_active REAL
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS bets (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                telegram_id INTEGER,
                match_name TEXT,
                selection TEXT,
                token_id TEXT,
                order_id TEXT,
                amount_gross REAL,
                amount_net REAL,
                fee REAL,
                price REAL,
                odds REAL,
                shares REAL,
                potential_win REAL,
                status TEXT DEFAULT 'placed',
                settled_amount REAL DEFAULT 0,
                created_at REAL,
                settled_at REAL DEFAULT NULL
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS referral_earnings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                referrer_id INTEGER,
                from_user_id INTEGER,
                bet_id INTEGER,
                fee_share REAL,
                tier INTEGER DEFAULT 1,
                created_at REAL
            )
        """)
        await db.execute("CREATE INDEX IF NOT EXISTS idx_bets_user ON bets(telegram_id)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_bets_status ON bets(status)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_ref_referrer ON referral_earnings(referrer_id)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_users_refcode ON users(referral_code)")
        await db.commit()
    await _migrate()


async def _migrate():
    """Add columns that may not exist in older databases."""
    cols = [
        ("users", "referred_by", "ALTER TABLE users ADD COLUMN referred_by INTEGER DEFAULT NULL"),
        ("users", "referral_code", "ALTER TABLE users ADD COLUMN referral_code TEXT"),
        ("users", "safe_address", "ALTER TABLE users ADD COLUMN safe_address TEXT"),
        ("users", "onboarded", "ALTER TABLE users ADD COLUMN onboarded INTEGER DEFAULT 0"),
        ("users", "total_volume", "ALTER TABLE users ADD COLUMN total_volume REAL DEFAULT 0"),
        ("users", "total_fees_paid", "ALTER TABLE users ADD COLUMN total_fees_paid REAL DEFAULT 0"),
        ("users", "total_referral_earned", "ALTER TABLE users ADD COLUMN total_referral_earned REAL DEFAULT 0"),
        ("bets", "token_id", "ALTER TABLE bets ADD COLUMN token_id TEXT"),
        ("bets", "order_id", "ALTER TABLE bets ADD COLUMN order_id TEXT"),
        ("bets", "amount_gross", "ALTER TABLE bets ADD COLUMN amount_gross REAL"),
        ("bets", "amount_net", "ALTER TABLE bets ADD COLUMN amount_net REAL"),
        ("bets", "fee", "ALTER TABLE bets ADD COLUMN fee REAL"),
        ("bets", "price", "ALTER TABLE bets ADD COLUMN price REAL"),
        ("bets", "shares", "ALTER TABLE bets ADD COLUMN shares REAL"),
        ("bets", "settled_amount", "ALTER TABLE bets ADD COLUMN settled_amount REAL DEFAULT 0"),
        ("bets", "settled_at", "ALTER TABLE bets ADD COLUMN settled_at REAL DEFAULT NULL"),
    ]
    async with aiosqlite.connect(_DB) as db:
        for _, _, sql in cols:
            try:
                await db.execute(sql)
            except Exception:
                pass
        await db.commit()


# ==================== USER OPERATIONS ====================

async def get_user(telegram_id: int):
    async with aiosqlite.connect(_DB) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute("SELECT * FROM users WHERE telegram_id = ?", (telegram_id,))
        row = await cur.fetchone()
        return dict(row) if row else None


async def create_user(telegram_id: int, username: str, wallet_address: str,
                      pin_hash: str, referred_by: int = None):
    ref_code = f"ref_{telegram_id}"
    async with aiosqlite.connect(_DB) as db:
        await db.execute(
            """INSERT INTO users (telegram_id, username, wallet_address, pin_hash, 
               referral_code, referred_by, created_at, last_active) 
               VALUES (?,?,?,?,?,?,?,?)""",
            (telegram_id, username, wallet_address, pin_hash,
             ref_code, referred_by, time.time(), time.time())
        )
        await db.commit()


async def update_currency(telegram_id: int, currency: str):
    async with aiosqlite.connect(_DB) as db:
        await db.execute("UPDATE users SET currency=?, last_active=? WHERE telegram_id=?",
                         (currency, time.time(), telegram_id))
        await db.commit()


async def delete_user(telegram_id: int):
    """Delete user and all their data (for /reset command)."""
    async with aiosqlite.connect(_DB) as conn:
        await conn.execute("DELETE FROM bets WHERE telegram_id=?", (telegram_id,))
        await conn.execute("DELETE FROM referral_earnings WHERE referrer_id=? OR from_user_id=?",
                           (telegram_id, telegram_id))
        await conn.execute("DELETE FROM users WHERE telegram_id=?", (telegram_id,))
        await conn.commit()


async def set_safe_address(telegram_id: int, safe_address: str):
    """Store the user's computed Safe address (before deployment). Does NOT mark onboarded."""
    async with aiosqlite.connect(_DB) as db:
        await db.execute(
            "UPDATE users SET safe_address=?, last_active=? WHERE telegram_id=?",
            (safe_address, time.time(), telegram_id))
        await db.commit()


async def update_safe_address(telegram_id: int, safe_address: str):
    """Store Safe address AND mark onboarded (call after deploy_safe + set_approvals succeed)."""
    async with aiosqlite.connect(_DB) as db:
        await db.execute(
            "UPDATE users SET safe_address=?, onboarded=1, last_active=? WHERE telegram_id=?",
            (safe_address, time.time(), telegram_id))
        await db.commit()


async def is_onboarded(telegram_id: int) -> bool:
    """Check if user's Safe wallet has been deployed and approved."""
    async with aiosqlite.connect(_DB) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            "SELECT onboarded, safe_address FROM users WHERE telegram_id=?", (telegram_id,))
        row = await cur.fetchone()
        return bool(row and row["onboarded"] and row["safe_address"])


async def get_user_by_ref_code(ref_code: str):
    async with aiosqlite.connect(_DB) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute("SELECT * FROM users WHERE referral_code = ?", (ref_code,))
        row = await cur.fetchone()
        return dict(row) if row else None


# ==================== BET OPERATIONS ====================

async def log_bet(telegram_id: int, match_name: str, selection: str,
                  amount_gross: float, amount_net: float, fee: float,
                  price: float, odds: float, shares: float,
                  token_id: str = "", order_id: str = "") -> int:
    potential = round(shares * 1.0, 2) if shares > 0 else round(amount_net * odds, 2)
    async with aiosqlite.connect(_DB) as db:
        cur = await db.execute(
            """INSERT INTO bets (telegram_id, match_name, selection, token_id, order_id,
               amount_gross, amount_net, fee, price, odds, shares, potential_win, 
               status, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (telegram_id, match_name, selection, token_id, order_id,
             amount_gross, amount_net, fee, price, odds, shares, potential,
             "placed", time.time())
        )
        bet_id = cur.lastrowid
        await db.execute(
            "UPDATE users SET total_volume = total_volume + ?, total_fees_paid = total_fees_paid + ?, last_active = ? WHERE telegram_id = ?",
            (amount_gross, fee, time.time(), telegram_id)
        )
        await db.commit()
        return bet_id


async def get_user_bets(telegram_id: int, limit: int = 10, status: str = None):
    async with aiosqlite.connect(_DB) as db:
        db.row_factory = aiosqlite.Row
        if status:
            cur = await db.execute(
                "SELECT * FROM bets WHERE telegram_id=? AND status=? ORDER BY created_at DESC LIMIT ?",
                (telegram_id, status, limit))
        else:
            cur = await db.execute(
                "SELECT * FROM bets WHERE telegram_id=? ORDER BY created_at DESC LIMIT ?",
                (telegram_id, limit))
        return [dict(r) for r in await cur.fetchall()]


async def update_bet_status(bet_id: int, status: str, settled_amount: float = 0):
    async with aiosqlite.connect(_DB) as db:
        await db.execute(
            "UPDATE bets SET status=?, settled_amount=?, settled_at=? WHERE id=?",
            (status, settled_amount, time.time(), bet_id))
        await db.commit()


# ==================== REFERRAL OPERATIONS ====================

REFERRAL_TIERS = {1: 0.25, 2: 0.05, 3: 0.03}


async def process_referral_fee(telegram_id: int, bet_id: int, fee: float):
    if fee <= 0:
        return
    async with aiosqlite.connect(_DB) as db:
        db.row_factory = aiosqlite.Row
        current_id = telegram_id
        for tier in range(1, 4):
            cur = await db.execute(
                "SELECT referred_by FROM users WHERE telegram_id = ?", (current_id,))
            row = await cur.fetchone()
            if not row or not row["referred_by"]:
                break
            referrer_id = row["referred_by"]
            share = round(fee * REFERRAL_TIERS.get(tier, 0), 6)
            if share > 0:
                await db.execute(
                    """INSERT INTO referral_earnings 
                       (referrer_id, from_user_id, bet_id, fee_share, tier, created_at)
                       VALUES (?,?,?,?,?,?)""",
                    (referrer_id, telegram_id, bet_id, share, tier, time.time()))
                await db.execute(
                    "UPDATE users SET total_referral_earned = total_referral_earned + ? WHERE telegram_id = ?",
                    (share, referrer_id))
            current_id = referrer_id
        await db.commit()


async def get_referral_stats(telegram_id: int) -> dict:
    async with aiosqlite.connect(_DB) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            "SELECT COUNT(*) as cnt FROM users WHERE referred_by = ?", (telegram_id,))
        row = await cur.fetchone()
        direct_refs = row["cnt"] if row else 0
        
        cur = await db.execute(
            "SELECT total_referral_earned FROM users WHERE telegram_id = ?", (telegram_id,))
        row = await cur.fetchone()
        total_earned = row["total_referral_earned"] if row else 0
        
        cur = await db.execute(
            "SELECT tier, SUM(fee_share) as total FROM referral_earnings WHERE referrer_id = ? GROUP BY tier",
            (telegram_id,))
        tier_earnings = {}
        async for row in cur:
            tier_earnings[row["tier"]] = round(row["total"], 4)
        
        return {
            "direct_referrals": direct_refs,
            "total_earned": round(total_earned or 0, 4),
            "tier_earnings": tier_earnings,
            "referral_code": f"ref_{telegram_id}",
        }


def hash_pin(pin: str, telegram_id: int) -> str:
    return hashlib.sha256(f"{telegram_id}:{pin}".encode()).hexdigest()


async def get_active_bets():
    """Get all bets with status 'placed' (active/unsettled)."""
    async with aiosqlite.connect(_DB) as conn:
        conn.row_factory = aiosqlite.Row
        rows = await conn.execute_fetchall(
            "SELECT * FROM bets WHERE status = 'placed'"
        )
        return [dict(r) for r in rows]


async def get_all_users_with_safe():
    """Get all users who have a Safe wallet deployed (for settlement monitoring)."""
    async with aiosqlite.connect(_DB) as conn:
        conn.row_factory = aiosqlite.Row
        rows = await conn.execute_fetchall(
            "SELECT telegram_id, safe_address FROM users WHERE safe_address IS NOT NULL AND safe_address != ''"
        )
        return [dict(r) for r in rows]


async def get_total_fees():
    """Get total platform fees collected."""
    async with aiosqlite.connect(_DB) as conn:
        row = await conn.execute_fetchall(
            "SELECT COALESCE(SUM(fee), 0) as total FROM bets"
        )
        if row:
            return float(row[0][0])
        return 0.0
