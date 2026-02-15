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

import json
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


def _get_w3():
    """Create a Web3 instance with POA middleware for Polygon."""
    w3 = Web3(Web3.HTTPProvider(POLYGON_RPC_URL))
    try:
        from web3.middleware import ExtraDataToPOAMiddleware  # web3.py v7+
        w3.middleware_onion.inject(ExtraDataToPOAMiddleware, layer=0)
    except ImportError:
        try:
            from web3.middleware import geth_poa_middleware  # web3.py v5
            w3.middleware_onion.inject(geth_poa_middleware, layer=0)
        except ImportError:
            logger.warning("Could not load POA middleware — tx building may fail")
    return w3


def set_allowances(private_key: str) -> bool:
    """
    Set token allowances for Polymarket trading. One-time per wallet.
    Approves USDC.e spending and CTF token management for both exchanges.
    Requires POL/MATIC for gas (~0.01 POL total for 5 transactions).
    
    Uses the exact same approach as the official Polymarket gist:
    https://gist.github.com/poly-rodr/44313920481de58d5a3f6d1f8226bd5e
    """
    if not POLYGON_RPC_URL:
        logger.error("No POLYGON_RPC_URL configured for allowance setup")
        return False
    
    try:
        w3 = _get_w3()
        account = w3.eth.account.from_key(private_key)
        wallet = account.address
        logger.info(f"Setting Polymarket allowances for {wallet}...")
        
        # Use exact ABI from official gist
        erc20_abi = json.loads('[{"constant": false,"inputs": [{"name": "_spender","type": "address" },{ "name": "_value", "type": "uint256" }],"name": "approve","outputs": [{ "name": "", "type": "bool" }],"payable": false,"stateMutability": "nonpayable","type": "function"}]')
        erc1155_abi = json.loads('[{"inputs": [{ "internalType": "address", "name": "operator", "type": "address" },{ "internalType": "bool", "name": "approved", "type": "bool" }],"name": "setApprovalForAll","outputs": [],"stateMutability": "nonpayable","type": "function"}]')
        
        usdc_contract = w3.eth.contract(
            address=Web3.to_checksum_address(USDC_E_ADDRESS), abi=erc20_abi)
        ctf_contract = w3.eth.contract(
            address=Web3.to_checksum_address(CTF_CONTRACT), abi=erc1155_abi)
        
        nonce = w3.eth.get_transaction_count(wallet)
        
        # Use MAX_INT from web3 constants — same as official gist
        from web3.constants import MAX_INT
        max_approval = int(MAX_INT, 16)  # Convert hex string to int
        
        txs_sent = 0
        
        # 1. Approve USDC.e for CTF Exchange + Neg Risk CTF Exchange + Neg Risk Adapter
        for spender_name, spender_addr in [
            ("CTF Exchange", CTF_EXCHANGE),
            ("Neg Risk CTF Exchange", NEG_RISK_CTF_EXCHANGE),
            ("Neg Risk Adapter", NEG_RISK_ADAPTER),
        ]:
            tx = usdc_contract.functions.approve(
                Web3.to_checksum_address(spender_addr), max_approval
            ).build_transaction({
                "from": wallet, "nonce": nonce,
                "gas": 100000,  # Increased from 60000
                "chainId": CHAIN_ID
            })
            signed = account.sign_transaction(tx)
            tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction)
            receipt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=60)
            if receipt['status'] == 1:
                logger.info(f"  ✅ USDC.e approved for {spender_name}: {tx_hash.hex()}")
            else:
                logger.error(f"  ❌ USDC.e approve REVERTED for {spender_name}: {tx_hash.hex()}")
                return False
            nonce += 1
            txs_sent += 1
        
        # 2. Approve CTF tokens for exchanges + adapter
        for operator_name, operator_addr in [
            ("CTF Exchange", CTF_EXCHANGE),
            ("Neg Risk CTF Exchange", NEG_RISK_CTF_EXCHANGE),
            ("Neg Risk Adapter", NEG_RISK_ADAPTER),
        ]:
            tx = ctf_contract.functions.setApprovalForAll(
                Web3.to_checksum_address(operator_addr), True
            ).build_transaction({
                "from": wallet, "nonce": nonce,
                "gas": 100000,  # Increased from 60000
                "chainId": CHAIN_ID
            })
            signed = account.sign_transaction(tx)
            tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction)
            receipt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=60)
            if receipt['status'] == 1:
                logger.info(f"  ✅ CTF approved for {operator_name}: {tx_hash.hex()}")
            else:
                logger.error(f"  ❌ CTF approve REVERTED for {operator_name}: {tx_hash.hex()}")
                return False
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
        w3 = _get_w3()
        
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
        
        w3 = _get_w3()
        
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
        w3 = _get_w3()
        
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


def _get_clob_client(private_key: str, safe_address: str = "") -> ClobClient:
    """
    Create a CLOB client for a user's wallet.
    Uses signature_type=2 (Gnosis Safe) — the official Polymarket way.
    Safe address is deterministic from private key, but can be passed for speed.
    """
    if not safe_address:
        # Derive Safe address from private key if not provided
        try:
            import wallet as _wallet
            safe_address = _wallet.get_safe_address(private_key)
        except Exception:
            pass
    
    client = ClobClient(
        host=CLOB_HOST,
        key=private_key,
        chain_id=CHAIN_ID,
        signature_type=2,
        funder=safe_address,
    )
    # Derive L2 API credentials (deterministic — same key always produces same creds)
    api_creds = client.create_or_derive_api_creds()
    client.set_api_creds(api_creds)
    logger.info(f"  CLOB client ready (Safe), API key: {api_creds.api_key[:8]}...")
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
    safe_address: str = "",
) -> dict:
    """
    Place a bet on Polymarket.
    
    Args:
        private_key: User's EOA private key (controls the Safe)
        token_id: Polymarket CLOB token ID for the outcome
        price: Current price (probability, 0.01-0.99)
        amount_usdc: Gross amount in USDC (before fee)
        match_name: Human-readable match name for logging
        selection: Human-readable selection name
        safe_address: User's Gnosis Safe address (where USDC lives)
    
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
        
        # Check balance (from Safe address if available, otherwise EOA)
        bal_addr = safe_address if safe_address else ""
        balance = get_usdc_balance(private_key=private_key, address=bal_addr)
        
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
        # Polymarket requires size rounded to 2 decimals
        # CLOB computes fill cost as floor(size * price * 10^6) / 10^6 (USDC has 6 decimals)
        # We must ensure this floor result >= $1.00 (1000000 micro-USDC)
        import math
        
        # Price may also need rounding to tick_size for accurate cost calculation
        # Use the price as-is for the order (CLOB will handle tick alignment)
        # But for cost calculation, round DOWN to tick_size to model worst case
        calc_price = math.floor(price * 100) / 100  # Round down to 0.01 (common tick)
        if calc_price <= 0:
            calc_price = price  # safety fallback
        
        # Start with exact shares needed (using worst-case price)
        shares = math.ceil((net_amount / calc_price) * 100) / 100
        
        # Keep adding 0.01 shares until CLOB's floor calculation >= $1
        for _ in range(10):
            clob_cost_micro = math.floor(shares * calc_price * 1_000_000)
            if clob_cost_micro >= 1_000_000:
                break
            shares = round(shares + 0.01, 2)
        
        # Cost = shares * price (confirmed by Polymarket docs — same for both
        # standard and neg_risk markets)
        order_cost = clob_cost_micro / 1_000_000
        total_needed = order_cost + fee
        
        # Re-check balance with actual cost
        if balance < total_needed:
            result["error"] = f"Insufficient balance. Need ${total_needed:.2f}, you have ${balance:.2f}."
            return result
        
        logger.info(f"Placing bet: {match_name} | {selection} | "
                    f"gross=${amount_usdc} fee=${fee} net=${net_amount} "
                    f"price={price} calc_price={calc_price} shares={shares} "
                    f"cost=${order_cost} clob_micro={clob_cost_micro} token={token_id[:12]}...")
        
        # Create CLOB client (signature_type=2 with Safe)
        client = _get_clob_client(private_key, safe_address=safe_address)
        
        # CRITICAL: Cancel any stale open orders that may be reserving balance
        # The CLOB reserves balance for ALL unfilled orders:
        # maxOrderSize = balance - Σ(unfilled order amounts)
        try:
            open_orders = client.get_orders()
            if open_orders:
                logger.info(f"  ⚠️ Found {len(open_orders)} open orders reserving balance — cancelling all")
                client.cancel_all()
                logger.info(f"  ✅ Cancelled all open orders")
            else:
                logger.info(f"  No open orders (balance fully available)")
        except Exception as co_err:
            logger.warning(f"  Could not check/cancel open orders: {co_err}")
        
        # Refresh CLOB's cached view of our on-chain balance/allowance
        needs_approve = False
        try:
            from py_clob_client.clob_types import BalanceAllowanceParams, AssetType
            # Refresh COLLATERAL (USDC.e) 
            client.update_balance_allowance(BalanceAllowanceParams(
                asset_type=AssetType.COLLATERAL
            ))
            bal = client.get_balance_allowance(BalanceAllowanceParams(
                asset_type=AssetType.COLLATERAL
            ))
            logger.info(f"  CLOB COLLATERAL: {bal}")
            
            # Also refresh CONDITIONAL (CTF tokens) for this specific token
            client.update_balance_allowance(BalanceAllowanceParams(
                asset_type=AssetType.CONDITIONAL,
                token_id=token_id,
            ))
            cond_bal = client.get_balance_allowance(BalanceAllowanceParams(
                asset_type=AssetType.CONDITIONAL,
                token_id=token_id,
            ))
            logger.info(f"  CLOB CONDITIONAL: {cond_bal}")
            
            # Check if ANY USDC.e allowance is 0 (need all 3 contracts approved)
            allowances = bal.get("allowances", {})
            usdc_any_zero = any(int(v) == 0 for v in allowances.values()) if allowances else True
            
            # Check if ANY CTF allowance is 0
            cond_allowances = cond_bal.get("allowances", {})
            ctf_any_zero = any(int(v) == 0 for v in cond_allowances.values()) if cond_allowances else True
            
            if usdc_any_zero or ctf_any_zero:
                needs_approve = True
                logger.info(f"  ⚠️ Missing allowances (USDC_any_zero={usdc_any_zero}, CTF_any_zero={ctf_any_zero}), running approve...")
        except Exception as ube:
            logger.warning(f"  Could not check CLOB balance: {ube}")
        
        if needs_approve:
            # Set approvals via wallet.py (gasless via Builder Relayer)
            try:
                import wallet as _wallet
                appr = _wallet.set_approvals(private_key)
                if not appr.get("success"):
                    result["error"] = f"Failed to set approvals: {appr.get('error', 'unknown')}"
                    return result
            except Exception as ae:
                logger.error(f"  Approval fallback failed: {ae}")
                result["error"] = "Wallet not properly onboarded. Re-run setup."
                return result
            # Refresh caches after approval
            try:
                client.update_balance_allowance(BalanceAllowanceParams(asset_type=AssetType.COLLATERAL))
                client.update_balance_allowance(BalanceAllowanceParams(asset_type=AssetType.CONDITIONAL, token_id=token_id))
            except:
                pass
        
        # Query market info for neg_risk and tick_size
        neg_risk = False
        tick_size = "0.01"
        try:
            neg_risk = client.get_neg_risk(token_id)
            tick_size = client.get_tick_size(token_id)
            logger.info(f"  Market info: neg_risk={neg_risk}, tick_size={tick_size}")
        except Exception as mie:
            logger.warning(f"  Could not get market info: {mie}")
        
        # Debug: Log USDC.e vs native USDC separately
        bals = get_usdc_balances(private_key=private_key, address=bal_addr)
        logger.info(f"  On-chain: USDC.e=${bals['usdc_e']}, Native USDC=${bals['usdc_native']}, POL={get_matic_balance(private_key=private_key)}")
        
        # Use MARKET ORDER (FOK) — $1 minimum, no 5-share minimum like limit orders
        from py_clob_client.clob_types import MarketOrderArgs
        
        # Version-safe: newer py-clob-client requires side=BUY, older does not
        try:
            market_order = MarketOrderArgs(
                token_id=token_id,
                amount=round(net_amount, 2),
                side=BUY,
                order_type=OrderType.FOK,
            )
        except TypeError:
            # Older version without side parameter
            try:
                market_order = MarketOrderArgs(
                    token_id=token_id,
                    amount=round(net_amount, 2),
                    order_type=OrderType.FOK,
                )
            except TypeError:
                # Even older — just token_id and amount
                market_order = MarketOrderArgs(
                    token_id=token_id,
                    amount=round(net_amount, 2),
                )
        
        from py_clob_client.clob_types import PartialCreateOrderOptions
        options = PartialCreateOrderOptions(
            neg_risk=neg_risk,
            tick_size=tick_size,
        )
        
        logger.info(f"  Posting MARKET order (FOK): amount=${net_amount:.2f} neg_risk={neg_risk} tick_size={tick_size}")
        signed = client.create_market_order(market_order, options=options)
        response = client.post_order(signed, OrderType.FOK)
        
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
        elif "lower than the minimum" in error_str:
            # Parse: "Size (1.97) lower than the minimum: 5"
            import re
            m = re.search(r'minimum:\s*(\d+)', error_str)
            min_shares = int(m.group(1)) if m else 5
            min_cost = round(min_shares * price, 2)
            result["error"] = f"Minimum bet for this market is {min_shares} shares (${min_cost:.2f}). Please increase your stake."
        elif "invalid amount" in error_str and "min size" in error_str:
            # Parse: "invalid amount for a marketable BUY order ($0.996), min size: $1"
            result["error"] = "Bet amount too low after rounding. Please try a slightly higher amount."
        elif "insufficient" in error_str.lower() or ("balance" in error_str.lower() and "allowance" not in error_str.lower()):
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


async def get_positions(private_key: str, safe_address: str = "") -> list:
    """
    Fetch user's open positions from Polymarket.
    This is the on-chain recovery mechanism - positions survive redeployment.
    """
    try:
        client = _get_clob_client(private_key, safe_address=safe_address)
        
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
