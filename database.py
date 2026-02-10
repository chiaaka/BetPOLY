"""
BetPoly - Database
SQLite storage for users, bets, and settings.
NO private keys stored. Only public wallet addresses.
"""
import aiosqlite
import hashlib
import time
from config import DATABASE_PATH


async def init_db():
    async with aiosqlite.connect(DATABASE_PATH) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS users (
                telegram_id INTEGER PRIMARY KEY,
                username TEXT,
                wallet_address TEXT,
                pin_hash TEXT,
                currency TEXT DEFAULT 'USD',
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
                amount_usdc REAL,
                odds REAL,
                potential_win REAL,
                status TEXT DEFAULT 'pending',
                created_at REAL,
                FOREIGN KEY (telegram_id) REFERENCES users(telegram_id)
            )
        """)
        await db.commit()


async def get_user(telegram_id: int):
    async with aiosqlite.connect(DATABASE_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute("SELECT * FROM users WHERE telegram_id = ?", (telegram_id,))
        row = await cur.fetchone()
        return dict(row) if row else None


async def create_user(telegram_id: int, username: str, wallet_address: str, pin_hash: str):
    async with aiosqlite.connect(DATABASE_PATH) as db:
        await db.execute(
            "INSERT INTO users (telegram_id, username, wallet_address, pin_hash, created_at, last_active) VALUES (?,?,?,?,?,?)",
            (telegram_id, username, wallet_address, pin_hash, time.time(), time.time())
        )
        await db.commit()


async def update_currency(telegram_id: int, currency: str):
    async with aiosqlite.connect(DATABASE_PATH) as db:
        await db.execute("UPDATE users SET currency=?, last_active=? WHERE telegram_id=?",
                         (currency, time.time(), telegram_id))
        await db.commit()


async def log_bet(telegram_id: int, match_name: str, selection: str, amount: float, odds: float):
    potential = round(amount * odds, 2)
    async with aiosqlite.connect(DATABASE_PATH) as db:
        await db.execute(
            "INSERT INTO bets (telegram_id, match_name, selection, amount_usdc, odds, potential_win, created_at) VALUES (?,?,?,?,?,?,?)",
            (telegram_id, match_name, selection, amount, odds, potential, time.time())
        )
        await db.commit()


async def get_user_bets(telegram_id: int, limit: int = 10):
    async with aiosqlite.connect(DATABASE_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            "SELECT * FROM bets WHERE telegram_id=? ORDER BY created_at DESC LIMIT ?",
            (telegram_id, limit)
        )
        rows = await cur.fetchall()
        return [dict(r) for r in rows]


def hash_pin(pin: str, telegram_id: int) -> str:
    return hashlib.sha256(f"{telegram_id}:{pin}".encode()).hexdigest()
