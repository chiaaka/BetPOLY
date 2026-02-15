"""
BetPoly - Configuration
Loads from environment variables (Railway) or .env file (local dev).
"""
import os
from dotenv import load_dotenv

load_dotenv()  # No-op on Railway, loads .env locally

# Telegram
BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")

# Polymarket Builder
BUILDER_API_KEY = os.getenv("BUILDER_API_KEY", "")
BUILDER_SECRET = os.getenv("BUILDER_SECRET", "")
BUILDER_PASSPHRASE = os.getenv("BUILDER_PASSPHRASE", "")

# Blockchain
POLYGON_RPC_URL = os.getenv("POLYGON_RPC_URL", "")

# Security
MASTER_SALT = os.getenv("MASTER_SALT", "")

# Database - Railway volume mount at /data
DATABASE_PATH = os.getenv("DATABASE_PATH", "betpoly.db")

# Fee collection
ADMIN_WALLET = os.getenv("ADMIN_WALLET", "0xf33e6a0606A0a654ded929cd095062135b3C1B3B")
PLATFORM_FEE_RATE = float(os.getenv("PLATFORM_FEE_RATE", "0.01"))  # 1%
ADMIN_TELEGRAM_ID = int(os.getenv("ADMIN_TELEGRAM_ID", "7193855492"))

# Ensure directory exists for DB
db_dir = os.path.dirname(DATABASE_PATH)
if db_dir:
    os.makedirs(db_dir, exist_ok=True)
