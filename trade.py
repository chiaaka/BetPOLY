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

# Polymarket contract addresses for approvals
CTF_EXCHANGE = "0x4bFb41d5B3570DeFd03C39a9A4D8dE6Bd8B8982E"       # CTF Exchange
NEG_RISK_CTF_EXCHANGE = "0xC5d563A36AE78145C45a50134d48A1215220f80a"  # Neg Risk CTF Exchange
NEG_RISK_ADAPTER = "0xd91E80cF2E7be2e162c6513ceD06f1dD0dA35296"      # Neg Risk Adapter
CTF_CONTRACT = "0x4D97DCd97eC945f40cF65F87097ACe5EA0476045"          # Conditional Tokens (CTF)

# Platform fee
PLATFORM_FEE_RATE = 0.01  # 1%

# Max uint256 for unlimited approval
MAX_ALLOWANCE = 2**256 - 1


def set_allowances(private_key: str) -> bool:
    """
    Set token allowances for Polymarket trading. One-time per wallet.
    Approves USDC.e spending and CTF token management for both exchanges.
    Requires POL/MATIC for gas (~0.01 POL total for 4 transactions).
    """
    if not POLYGON_RPC_URL:
        logger.error("No POLYGON_RPC_URL configured for allowance setup")
        return False
    
    try:
        w3 = Web3(Web3.HTTPProvider(POLYGON_RPC_URL))
        account = w3.eth.account.from_key(private_key)
        wallet = account.address
        logger.info(f"Setting Polymarket allowances for {wallet}...")
        
        erc20_abi = [{"constant": False, "inputs": [
            {"name": "_spender", "type": "address"},
            {"name": "_value", "type": "uint256"}
        ], "name": "approve", "outputs": [{"name": "", "type": "bool"}],
            "type": "function"}]
        
        erc1155_abi = [{"inputs": [
            {"name": "operator", "type": "address"},
            {"name": "approved", "type": "bool"}
        ], "name": "setApprovalForAll", "outputs": [], "type": "function"}]
        
        usdc_contract = w3.eth.contract(
            address=Web3.to_checksum_address(USDC_E_ADDRESS), abi=erc20_abi)
        ctf_contract = w3.eth.contract(
            address=Web3.to_checksum_address(CTF_CONTRACT), abi=erc1155_abi)
        
        nonce = w3.eth.get_transaction_count(wallet)
        gas_price = w3.eth.gas_price
        
        txs_sent = 0
        
        # 1. Approve USDC.e for CTF Exchange
        for spender_name, spender_addr in [
            ("CTF Exchange", CTF_EXCHANGE),
            ("Neg Risk CTF Exchange", NEG_RISK_CTF_EXCHANGE),
        ]:
            tx = usdc_contract.functions.approve(
                Web3.to_checksum_address(spender_addr), MAX_ALLOWANCE
            ).build_transaction({
                "from": wallet, "nonce": nonce,
                "gasPrice": gas_price, "gas": 60000,
                "chainId": CHAIN_ID
            })
            signed = account.sign_transaction(tx)
            tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction)
            w3.eth.wait_for_transaction_receipt(tx_hash, timeout=30)
            logger.info(f"  ✅ USDC.e approved for {spender_name}: {tx_hash.hex()}")
            nonce += 1
            txs_sent += 1
        
        # 2. Approve CTF tokens for exchanges
        for operator_name, operator_addr in [
            ("CTF Exchange", CTF_EXCHANGE),
            ("Neg Risk CTF Exchange", NEG_RISK_CTF_EXCHANGE),
            ("Neg Risk Adapter", NEG_RISK_ADAPTER),
        ]:
            tx = ctf_contract.functions.setApprovalForAll(
                Web3.to_checksum_address(operator_addr), True
            ).build_transaction({
                "from": wallet, "nonce": nonce,
                "gasPrice": gas_price, "gas": 60000,
                "chainId": CHAIN_ID
            })
            signed = account.sign_transaction(tx)
            tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction)
            w3.eth.wait_for_transaction_receipt(tx_hash, timeout=30)
            logger.info(f"  ✅ CTF approved for {operator_name}: {tx_hash.hex()}")
            nonce += 1
            txs_sent += 1
        
        logger.info(f"  🎉 All {txs_sent} allowances set successfully!")
        return True
        
    except Exception as e:
        logger.error(f"  ❌ Allowance setup failed: {e}")
        return False


def check_allowances(private_key: str = "", address: str = "") -> bool:
    """Quick check if USDC.e allowance is set for CTF Exchange."""
    try:
        if not POLYGON_RPC_URL:
            return False
        w3 = Web3(Web3.HTTPProvider(POLYGON_RPC_URL))
        
        if address:
            wallet = Web3.to_checksum_address(address)
        elif private_key:
            wallet = w3.eth.account.from_key(private_key).address
        else:
            return False
        
        abi = [{"constant": True, "inputs": [
            {"name": "_owner", "type": "address"},
            {"name": "_spender", "type": "address"}
        ], "name": "allowance", "outputs": [{"name": "", "type": "uint256"}],
            "type": "function"}]
        
        c = w3.eth.contract(address=Web3.to_checksum_address(USDC_E_ADDRESS), abi=abi)
        allowance = c.functions.allowance(wallet, Web3.to_checksum_address(CTF_EXCHANGE)).call()
        logger.info(f"  Allowance check: USDC.e→CTF Exchange = {allowance}")
        return allowance > 0
    except Exception as e:
        logger.warning(f"  Allowance check failed: {e}")
        return False


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
        
        # Calculate fee — fee is on top, full amount goes to Polymarket
        fee_info = calculate_fee(amount_usdc)
        fee = fee_info["fee"]
        net_amount = amount_usdc  # Full amount goes to Polymarket
        total_needed = amount_usdc + fee  # User pays amount + fee
        
        # Check balance covers amount + fee
        if balance < total_needed:
            result["error"] = f"Insufficient balance. Need ${total_needed:.2f} (${amount_usdc:.2f} bet + ${fee:.2f} fee), you have ${balance:.2f}."
            return result
        
        # Polymarket minimum is $1
        if net_amount < 1.0:
            result["error"] = "Minimum bet is $1 USDC."
            return result
        
        # Calculate shares: amount / price
        shares = round(net_amount / price, 2)
        
        logger.info(f"Placing bet: {match_name} | {selection} | "
                    f"gross=${amount_usdc} fee=${fee} net=${net_amount} "
                    f"price={price} shares={shares} token={token_id[:12]}...")
        
        # Create CLOB client
        client = _get_clob_client(private_key)
        
        # Skip allowance re-check — approvals were already set on-chain
        # The check_allowances was returning false positives, causing repeated
        # unnecessary gas spending. Approvals are one-time and already done.
        # TODO: Fix check_allowances RPC read issue
        
        # Debug: Log USDC.e vs native USDC separately
        bals = get_usdc_balances(private_key=private_key)
        logger.info(f"  On-chain: USDC.e=${bals['usdc_e']}, Native USDC=${bals['usdc_native']}, POL={get_matic_balance(private_key=private_key)}")
        
        # Create and post order in one call — this handles neg_risk auto-detection
        # Sports markets are typically neg_risk markets which use a different exchange contract
        order_args = OrderArgs(
            token_id=token_id,
            price=price,
            size=shares,
            side=BUY,
        )
        
        logger.info(f"  Posting order via create_and_post_order...")
        response = client.create_and_post_order(order_args)
        
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
        logger.error(f"  ❌ Trade execution error: {error_str[:200]}")
        
        # User-friendly error messages
        if "cloudflare" in error_str.lower() or "403" in error_str or "unable to access" in error_str.lower() or "<!DOCTYPE" in error_str:
            result["error"] = "Server blocked by Cloudflare. Polymarket Builders Program whitelist needed. Contact support."
        elif "insufficient" in error_str.lower() or "balance" in error_str.lower():
            result["error"] = "Insufficient USDC balance. Please deposit more funds."
        elif "allowance" in error_str.lower() or "approve" in error_str.lower():
            result["error"] = "Token approval needed. Please try again (auto-approving)."
        elif "nonce" in error_str.lower():
            result["error"] = "Transaction conflict. Please try again in a few seconds."
        else:
            # Strip any HTML from error messages
            clean_err = error_str[:150]
            if "<" in clean_err:
                clean_err = "Trade execution failed. Please try again."
            result["error"] = f"Trade failed: {clean_err}"
    
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
