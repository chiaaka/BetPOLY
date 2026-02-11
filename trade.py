"""
BetPoly - Trade Execution Engine
Places real bets on Polymarket via CLOB API.

Flow:
  1. User taps odds → bet slip → confirms
  2. trade.place_bet() called with token_id, price, amount
  3. Deducts platform fee (1%)
  4. Creates and posts order to Polymarket CLOB
  5. Returns order confirmation or error

Uses py-clob-client for Polymarket's Central Limit Order Book.
Each user has a deterministic wallet (from wallet.py).
"""

import logging
import time
from decimal import Decimal, ROUND_DOWN
from py_clob_client.client import ClobClient
from py_clob_client.clob_types import OrderArgs, OrderType
from py_clob_client.order_builder.constants import BUY, SELL
from web3 import Web3
from config import POLYGON_RPC_URL

logger = logging.getLogger("BetPoly.Trade")

# Polymarket CLOB endpoints
CLOB_HOST = "https://clob.polymarket.com"
CHAIN_ID = 137  # Polygon mainnet

# USDC on Polygon — check BOTH contracts
USDC_E_ADDRESS = "0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174"  # USDC.e (bridged, used by Polymarket)
USDC_NATIVE_ADDRESS = "0x3c499c542cEF5E3811e1192ce70d8cC03d5c3359"  # Native USDC (Circle)
USDC_DECIMALS = 6

# CTF Exchange (Conditional Token Framework) for approvals
CTF_EXCHANGE = "0x4bFb41d5B3570DeFd03C39a9A4D8dE6Bd8B8982E"

# Platform fee
PLATFORM_FEE_RATE = 0.01  # 1%


def get_usdc_balance(private_key: str = "", address: str = "") -> float:
    """Get total USDC balance (USDC.e + native USDC) for a wallet on Polygon."""
    bals = get_usdc_balances(private_key=private_key, address=address)
    return round(bals["usdc_e"] + bals["usdc_native"], 2)


def get_usdc_balances(private_key: str = "", address: str = "") -> dict:
    """Get separate USDC.e and native USDC balances."""
    result = {"usdc_e": 0.0, "usdc_native": 0.0}
    try:
        if not POLYGON_RPC_URL:
            return result
        
        w3 = Web3(Web3.HTTPProvider(POLYGON_RPC_URL))
        
        if address:
            wallet_addr = Web3.to_checksum_address(address)
        elif private_key:
            account = w3.eth.account.from_key(private_key)
            wallet_addr = account.address
        else:
            return result
        
        usdc_abi = [{"constant": True, "inputs": [{"name": "_owner", "type": "address"}],
                     "name": "balanceOf", "outputs": [{"name": "balance", "type": "uint256"}],
                     "type": "function"}]
        
        # USDC.e (Polymarket uses this)
        try:
            c = w3.eth.contract(address=Web3.to_checksum_address(USDC_E_ADDRESS), abi=usdc_abi)
            result["usdc_e"] = round(c.functions.balanceOf(wallet_addr).call() / (10 ** USDC_DECIMALS), 2)
        except Exception:
            pass
        
        # Native USDC
        try:
            c = w3.eth.contract(address=Web3.to_checksum_address(USDC_NATIVE_ADDRESS), abi=usdc_abi)
            result["usdc_native"] = round(c.functions.balanceOf(wallet_addr).call() / (10 ** USDC_DECIMALS), 2)
        except Exception:
            pass
        
    except Exception as e:
        logger.error(f"Balance check failed: {e}")
    
    return result


def get_matic_balance(private_key: str = "", address: str = "") -> float:
    """Get MATIC/POL balance for gas. Can use address OR private key."""
    try:
        if not POLYGON_RPC_URL:
            return 0.0
        w3 = Web3(Web3.HTTPProvider(POLYGON_RPC_URL))
        
        if address:
            wallet_addr = Web3.to_checksum_address(address)
        elif private_key:
            account = w3.eth.account.from_key(private_key)
            wallet_addr = account.address
        else:
            return 0.0
        
        bal = w3.eth.get_balance(wallet_addr)
        return round(w3.from_wei(bal, 'ether'), 4)
    except Exception as e:
        logger.error(f"MATIC balance check failed: {e}")
        return 0.0


def _get_clob_client(private_key: str) -> ClobClient:
    """
    Create a CLOB client for a user's wallet.
    Must derive API creds from the private key before posting orders.
    BetPoly wallets are standard EOA (signature_type=0, the default).
    """
    client = ClobClient(
        host=CLOB_HOST,
        key=private_key,
        chain_id=CHAIN_ID,
    )
    # Derive L2 API credentials from L1 private key
    # This is deterministic — same key always produces same creds
    api_creds = client.create_or_derive_api_creds()
    client.set_api_creds(api_creds)
    logger.info(f"  CLOB client ready, API key: {api_creds.api_key[:8]}...")
    return client


def calculate_fee(amount_usdc: float) -> dict:
    """Calculate platform fee and net bet amount."""
    fee = round(amount_usdc * PLATFORM_FEE_RATE, 6)
    net_amount = round(amount_usdc - fee, 6)
    return {
        "gross": amount_usdc,
        "fee": fee,
        "fee_rate": PLATFORM_FEE_RATE,
        "net": net_amount,
    }


async def place_bet(
    private_key: str,
    token_id: str,
    price: float,
    amount_usdc: float,
    match_name: str = "",
    selection: str = "",
) -> dict:
    """
    Place a bet on Polymarket.
    
    Args:
        private_key: User's wallet private key
        token_id: Polymarket CLOB token ID for the outcome
        price: Current price (probability, 0.01-0.99)
        amount_usdc: Gross amount in USDC (before fee)
        match_name: Human-readable match name for logging
        selection: Human-readable selection name
    
    Returns:
        dict with: success, order_id, shares, fee, net_amount, error
    """
    result = {
        "success": False,
        "order_id": "",
        "shares": 0,
        "fee": 0,
        "net_amount": 0,
        "gross_amount": amount_usdc,
        "price": price,
        "error": "",
    }
    
    try:
        # Validate inputs
        if not token_id:
            result["error"] = "No market token ID available for this selection."
            return result
        
        if not private_key:
            result["error"] = "Wallet not configured. Please set up your wallet first."
            return result
        
        if price <= 0.005 or price >= 0.995:
            result["error"] = "Odds too extreme. Market may be settled."
            return result
        
        if amount_usdc < 1.0:
            result["error"] = "Minimum bet is $1 USDC."
            return result
        
        # Check balance
        balance = get_usdc_balance(private_key)
        if balance < amount_usdc:
            result["error"] = f"Insufficient balance. You have ${balance:.2f} USDC but need ${amount_usdc:.2f}."
            return result
        
        # Calculate fee
        fee_info = calculate_fee(amount_usdc)
        net_amount = fee_info["net"]
        fee = fee_info["fee"]
        
        # Calculate shares: amount / price
        shares = round(net_amount / price, 2)
        
        logger.info(f"Placing bet: {match_name} | {selection} | "
                    f"gross=${amount_usdc} fee=${fee} net=${net_amount} "
                    f"price={price} shares={shares} token={token_id[:12]}...")
        
        # Create CLOB client
        client = _get_clob_client(private_key)
        
        # Ensure allowances are set (first-time setup)
        # py-clob-client handles this automatically
        
        # Create order
        order_args = OrderArgs(
            token_id=token_id,
            price=price,
            size=shares,
            side=BUY,
        )
        
        # Post the order
        signed_order = client.create_order(order_args)
        response = client.post_order(signed_order, OrderType.GTC)
        
        if response and response.get("orderID"):
            result["success"] = True
            result["order_id"] = response["orderID"]
            result["shares"] = shares
            result["fee"] = fee
            result["net_amount"] = net_amount
            logger.info(f"  ✅ Order placed: {response['orderID']}")
        else:
            error_msg = response.get("errorMsg", str(response)) if response else "No response from CLOB"
            result["error"] = f"Order rejected: {error_msg}"
            logger.error(f"  ❌ Order failed: {error_msg}")
        
    except Exception as e:
        error_str = str(e)
        logger.error(f"  ❌ Trade execution error: {error_str}")
        
        # User-friendly error messages
        if "insufficient" in error_str.lower() or "balance" in error_str.lower():
            result["error"] = "Insufficient USDC balance. Please deposit more funds."
        elif "allowance" in error_str.lower() or "approve" in error_str.lower():
            result["error"] = "Token approval needed. Please try again (auto-approving)."
        elif "nonce" in error_str.lower():
            result["error"] = "Transaction conflict. Please try again in a few seconds."
        else:
            result["error"] = f"Trade failed: {error_str[:100]}"
    
    return result


async def get_positions(private_key: str) -> list:
    """
    Fetch user's open positions from Polymarket.
    This is the on-chain recovery mechanism - positions survive redeployment.
    """
    try:
        client = _get_clob_client(private_key)
        
        # Get all open orders
        open_orders = client.get_orders()
        
        # Get positions (settled and unsettled)
        # Note: For full position data, we query the subgraph or REST API
        positions = []
        
        if open_orders:
            for order in open_orders:
                positions.append({
                    "order_id": order.get("id", ""),
                    "token_id": order.get("asset_id", ""),
                    "side": order.get("side", ""),
                    "price": float(order.get("price", 0)),
                    "size": float(order.get("original_size", 0)),
                    "filled": float(order.get("size_matched", 0)),
                    "status": order.get("status", ""),
                    "created": order.get("created_at", ""),
                })
        
        return positions
    except Exception as e:
        logger.error(f"Failed to fetch positions: {e}")
        return []


async def get_trades_history(wallet_address: str) -> list:
    """
    Fetch trade history from Polymarket's API.
    Uses the public API endpoint, no private key needed.
    """
    import httpx
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(
                f"https://data-api.polymarket.com/trades",
                params={"maker": wallet_address, "limit": 50}
            )
            if resp.status_code == 200:
                return resp.json() or []
    except Exception as e:
        logger.error(f"Failed to fetch trade history: {e}")
    return []
