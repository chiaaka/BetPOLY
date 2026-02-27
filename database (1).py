"""
BetPoly - Database (PostgreSQL)
PostgreSQL storage for users, bets, referrals, and fee tracking.
NO private keys stored. Only public wallet addresses.
"""
import asyncpg
import hashlib
import time
import os
import logging

logger = logging.getLogger("BetPoly.DB")

DATABASE_URL = os.getenv("DATABASE_URL", "")

_pool = None


async def _get_pool():
    global _pool
    if _pool is None:
        _pool = await asyncpg.create_pool(DATABASE_URL, min_size=2, max_size=10)
        logger.info("PostgreSQL connection pool created")
    return _pool


async def init_db():
    pool = await _get_pool()
    async with pool.acquire() as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS users (
                telegram_id BIGINT PRIMARY KEY,
                username TEXT,
                wallet_address TEXT,
                safe_address TEXT,
                onboarded INTEGER DEFAULT 0,
                pin_hash TEXT,
                currency TEXT DEFAULT 'USD',
                referred_by BIGINT DEFAULT NULL,
                referral_code TEXT,
                total_volume DOUBLE PRECISION DEFAULT 0,
                total_fees_paid DOUBLE PRECISION DEFAULT 0,
                total_referral_earned DOUBLE PRECISION DEFAULT 0,
                total_wins INTEGER DEFAULT 0,
                total_losses INTEGER DEFAULT 0,
                total_profit DOUBLE PRECISION DEFAULT 0,
                created_at DOUBLE PRECISION,
                last_active DOUBLE PRECISION
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS bets (
                id SERIAL PRIMARY KEY,
                telegram_id BIGINT,
                match_name TEXT,
                selection TEXT,
                token_id TEXT,
                order_id TEXT,
                amount_gross DOUBLE PRECISION,
                amount_net DOUBLE PRECISION,
                fee DOUBLE PRECISION,
                price DOUBLE PRECISION,
                odds DOUBLE PRECISION,
                shares DOUBLE PRECISION,
                potential_win DOUBLE PRECISION,
                status TEXT DEFAULT 'placed',
                settled_amount DOUBLE PRECISION DEFAULT 0,
                created_at DOUBLE PRECISION,
                settled_at DOUBLE PRECISION DEFAULT NULL
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS referral_earnings (
                id SERIAL PRIMARY KEY,
                referrer_id BIGINT,
                from_user_id BIGINT,
                bet_id INTEGER,
                fee_share DOUBLE PRECISION,
                tier INTEGER DEFAULT 1,
                created_at DOUBLE PRECISION
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS settlement_notifications (
                id SERIAL PRIMARY KEY,
                telegram_id BIGINT,
                token_id TEXT,
                status TEXT,
                created_at DOUBLE PRECISION
            )
        """)
        await db.execute("CREATE INDEX IF NOT EXISTS idx_bets_user ON bets(telegram_id)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_bets_status ON bets(status)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_ref_referrer ON referral_earnings(referrer_id)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_users_refcode ON users(referral_code)")
    logger.info("Database tables initialized")


# ==================== USER OPERATIONS ====================

async def get_user(telegram_id: int):
    pool = await _get_pool()
    async with pool.acquire() as db:
        row = await db.fetchrow("SELECT * FROM users WHERE telegram_id = $1", telegram_id)
        return dict(row) if row else None


async def create_user(telegram_id: int, username: str, wallet_address: str,
                      pin_hash: str, referred_by: int = None):
    ref_code = f"ref_{telegram_id}"
    pool = await _get_pool()
    async with pool.acquire() as db:
        await db.execute(
            """INSERT INTO users (telegram_id, username, wallet_address, pin_hash,
               referral_code, referred_by, created_at, last_active)
               VALUES ($1,$2,$3,$4,$5,$6,$7,$8)
               ON CONFLICT (telegram_id) DO NOTHING""",
            telegram_id, username, wallet_address, pin_hash,
            ref_code, referred_by, time.time(), time.time()
        )


async def update_currency(telegram_id: int, currency: str):
    pool = await _get_pool()
    async with pool.acquire() as db:
        await db.execute("UPDATE users SET currency=$1, last_active=$2 WHERE telegram_id=$3",
                         currency, time.time(), telegram_id)


async def delete_user(telegram_id: int):
    """Delete user and all their data (for /reset command)."""
    pool = await _get_pool()
    async with pool.acquire() as db:
        await db.execute("DELETE FROM bets WHERE telegram_id=$1", telegram_id)
        await db.execute("DELETE FROM referral_earnings WHERE referrer_id=$1 OR from_user_id=$1",
                         telegram_id)
        await db.execute("DELETE FROM users WHERE telegram_id=$1", telegram_id)


async def set_safe_address(telegram_id: int, safe_address: str):
    """Store the user's computed Safe address (before deployment). Does NOT mark onboarded."""
    pool = await _get_pool()
    async with pool.acquire() as db:
        await db.execute(
            "UPDATE users SET safe_address=$1, last_active=$2 WHERE telegram_id=$3",
            safe_address, time.time(), telegram_id)


async def update_safe_address(telegram_id: int, safe_address: str):
    """Store Safe address AND mark onboarded (call after deploy_safe + set_approvals succeed)."""
    pool = await _get_pool()
    async with pool.acquire() as db:
        await db.execute(
            "UPDATE users SET safe_address=$1, onboarded=1, last_active=$2 WHERE telegram_id=$3",
            safe_address, time.time(), telegram_id)


async def is_onboarded(telegram_id: int) -> bool:
    """Check if user's Safe wallet has been deployed and approved."""
    pool = await _get_pool()
    async with pool.acquire() as db:
        row = await db.fetchrow(
            "SELECT onboarded, safe_address FROM users WHERE telegram_id=$1", telegram_id)
        return bool(row and row["onboarded"] and row["safe_address"])


async def get_user_by_ref_code(ref_code: str):
    pool = await _get_pool()
    async with pool.acquire() as db:
        row = await db.fetchrow("SELECT * FROM users WHERE referral_code = $1", ref_code)
        return dict(row) if row else None


# ==================== BET OPERATIONS ====================

async def log_bet(telegram_id: int, match_name: str, selection: str,
                  amount_gross: float, amount_net: float, fee: float,
                  price: float, odds: float, shares: float,
                  token_id: str = "", order_id: str = "") -> int:
    potential = round(shares * 1.0, 2) if shares > 0 else round(amount_net * odds, 2)
    pool = await _get_pool()
    async with pool.acquire() as db:
        row = await db.fetchrow(
            """INSERT INTO bets (telegram_id, match_name, selection, token_id, order_id,
               amount_gross, amount_net, fee, price, odds, shares, potential_win,
               status, created_at) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14)
               RETURNING id""",
            telegram_id, match_name, selection, token_id, order_id,
            amount_gross, amount_net, fee, price, odds, shares, potential,
            "placed", time.time()
        )
        bet_id = row["id"]
        await db.execute(
            "UPDATE users SET total_volume = total_volume + $1, total_fees_paid = total_fees_paid + $2, last_active = $3 WHERE telegram_id = $4",
            amount_gross, fee, time.time(), telegram_id
        )
        return bet_id


async def get_user_bets(telegram_id: int, limit: int = 10, status: str = None):
    pool = await _get_pool()
    async with pool.acquire() as db:
        if status:
            rows = await db.fetch(
                "SELECT * FROM bets WHERE telegram_id=$1 AND status=$2 ORDER BY created_at DESC LIMIT $3",
                telegram_id, status, limit)
        else:
            rows = await db.fetch(
                "SELECT * FROM bets WHERE telegram_id=$1 ORDER BY created_at DESC LIMIT $2",
                telegram_id, limit)
        return [dict(r) for r in rows]


async def update_bet_status(bet_id: int, status: str, settled_amount: float = 0):
    pool = await _get_pool()
    async with pool.acquire() as db:
        await db.execute(
            "UPDATE bets SET status=$1, settled_amount=$2, settled_at=$3 WHERE id=$4",
            status, settled_amount, time.time(), bet_id)


# ==================== REFERRAL OPERATIONS ====================

REFERRAL_TIERS = {1: 0.25, 2: 0.05, 3: 0.03}


async def process_referral_fee(telegram_id: int, bet_id: int, fee: float):
    if fee <= 0:
        return
    pool = await _get_pool()
    async with pool.acquire() as db:
        current_id = telegram_id
        for tier in range(1, 4):
            row = await db.fetchrow(
                "SELECT referred_by FROM users WHERE telegram_id = $1", current_id)
            if not row or not row["referred_by"]:
                break
            referrer_id = row["referred_by"]
            share = round(fee * REFERRAL_TIERS.get(tier, 0), 6)
            if share > 0:
                await db.execute(
                    """INSERT INTO referral_earnings
                       (referrer_id, from_user_id, bet_id, fee_share, tier, created_at)
                       VALUES ($1,$2,$3,$4,$5,$6)""",
                    referrer_id, telegram_id, bet_id, share, tier, time.time())
                await db.execute(
                    "UPDATE users SET total_referral_earned = total_referral_earned + $1 WHERE telegram_id = $2",
                    share, referrer_id)
            current_id = referrer_id


async def get_referral_stats(telegram_id: int) -> dict:
    pool = await _get_pool()
    async with pool.acquire() as db:
        row = await db.fetchrow(
            "SELECT COUNT(*) as cnt FROM users WHERE referred_by = $1", telegram_id)
        direct_refs = row["cnt"] if row else 0

        row = await db.fetchrow(
            "SELECT total_referral_earned FROM users WHERE telegram_id = $1", telegram_id)
        total_earned = row["total_referral_earned"] if row else 0

        rows = await db.fetch(
            "SELECT tier, SUM(fee_share) as total FROM referral_earnings WHERE referrer_id = $1 GROUP BY tier",
            telegram_id)
        tier_earnings = {}
        for row in rows:
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
    pool = await _get_pool()
    async with pool.acquire() as db:
        rows = await db.fetch("SELECT * FROM bets WHERE status = 'placed'")
        return [dict(r) for r in rows]


async def get_all_users_with_safe():
    """Get all users who have a Safe wallet deployed (for settlement monitoring)."""
    pool = await _get_pool()
    async with pool.acquire() as db:
        rows = await db.fetch(
            "SELECT telegram_id, safe_address FROM users WHERE safe_address IS NOT NULL AND safe_address != ''")
        return [dict(r) for r in rows]


async def get_total_fees():
    """Get total platform fees collected."""
    pool = await _get_pool()
    async with pool.acquire() as db:
        row = await db.fetchrow("SELECT COALESCE(SUM(fee), 0) as total FROM bets")
        return float(row["total"]) if row else 0.0


async def get_user_stats(telegram_id: int) -> dict:
    """Get user betting stats."""
    pool = await _get_pool()
    async with pool.acquire() as db:
        row = await db.fetchrow(
            "SELECT total_volume, total_wins, total_losses, total_profit, total_fees_paid FROM users WHERE telegram_id = $1",
            telegram_id)
        if row:
            return {
                "volume": float(row["total_volume"] or 0),
                "wins": int(row["total_wins"] or 0),
                "losses": int(row["total_losses"] or 0),
                "profit": float(row["total_profit"] or 0),
                "fees": float(row["total_fees_paid"] or 0),
            }
        return {"volume": 0, "wins": 0, "losses": 0, "profit": 0, "fees": 0}


async def record_win(telegram_id: int, profit: float):
    """Record a win."""
    pool = await _get_pool()
    async with pool.acquire() as db:
        await db.execute(
            "UPDATE users SET total_wins = total_wins + 1, total_profit = total_profit + $1 WHERE telegram_id = $2",
            profit, telegram_id)


async def record_loss(telegram_id: int, loss_amount: float):
    """Record a loss."""
    pool = await _get_pool()
    async with pool.acquire() as db:
        await db.execute(
            "UPDATE users SET total_losses = total_losses + 1, total_profit = total_profit - $1 WHERE telegram_id = $2",
            loss_amount, telegram_id)


async def was_notified(telegram_id: int, token_id: str) -> bool:
    """Check if user was already notified about this position settlement."""
    pool = await _get_pool()
    async with pool.acquire() as db:
        row = await db.fetchrow(
            "SELECT id FROM settlement_notifications WHERE telegram_id = $1 AND token_id = $2",
            telegram_id, token_id[:40])
        return bool(row)


async def mark_notified(telegram_id: int, token_id: str, status: str):
    """Mark that user was notified about this position."""
    pool = await _get_pool()
    async with pool.acquire() as db:
        await db.execute(
            "INSERT INTO settlement_notifications (telegram_id, token_id, status, created_at) VALUES ($1, $2, $3, $4)",
            telegram_id, token_id[:40], status, time.time())


async def was_redeemed(telegram_id: int, token_id: str) -> bool:
    """Check if user already redeemed this position."""
    pool = await _get_pool()
    async with pool.acquire() as db:
        row = await db.fetchrow(
            "SELECT id FROM settlement_notifications WHERE telegram_id = $1 AND token_id = $2 AND status = 'redeemed'",
            telegram_id, token_id[:40])
        return bool(row)
