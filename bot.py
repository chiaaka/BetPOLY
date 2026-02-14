"""
BetPoly - Telegram Sports Betting Bot
Polymarket odds in Sportybet format for African bettors.
"""
import os
import sys
import logging
import asyncio
from dotenv import load_dotenv

load_dotenv()

from telegram import Update, InlineKeyboardButton as Btn, InlineKeyboardMarkup as Markup
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler,
    MessageHandler, ConversationHandler, filters, ContextTypes
)
from telegram.constants import ParseMode

from config import BOT_TOKEN
import database as db
import wallet
import trade
from polymarket_api import (
    LEAGUES, CURRENCIES, SPORT_LABELS, fetch_events, fetch_live, parse_event,
    format_match, price_to_odds, fmt_odds, fmt_currency, fmt_time, fmt_date_bold,
    refresh_currency_rates
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s"
)
logger = logging.getLogger("BetPoly")

# Conversation states
PIN_SET, PIN_CONFIRM, CURRENCY_SELECT = range(3)


def _extract_referrer(ctx) -> int:
    """Extract referrer telegram_id from deep link."""
    ref_code = ctx.user_data.pop("referrer_code", "")
    if ref_code.startswith("ref_"):
        try:
            return int(ref_code[4:])
        except ValueError:
            pass
    return None


# ========================================
# Keyboards
# ========================================

def kb_main():
    return Markup([
        [Btn("📅 TODAY'S MATCHES", callback_data="today")],
        [Btn("🔴 LIVE NOW", callback_data="live")],
        [Btn("⚽ Football", callback_data="sport_soccer"),
         Btn("🏀 Basketball", callback_data="sport_basketball")],
        [Btn("🎾 Tennis", callback_data="sport_tennis"),
         Btn("🏏 Cricket", callback_data="sport_cricket")],
        [Btn("🥊 UFC/MMA", callback_data="league_mma"),
         Btn("🎮 Esports", callback_data="sport_esports")],
        [Btn("🏟 More Sports", callback_data="more_sports")],
        [Btn("💰 My Bets", callback_data="my_bets"),
         Btn("👛 Wallet", callback_data="wallet_menu")],
        [Btn("📣 Referral", callback_data="referral"),
         Btn("⚙️ Settings", callback_data="settings")],
        [Btn("❓ Help", callback_data="help")],
    ])


def kb_onboard_currency():
    """Currency selection during onboarding (uses obc_ prefix)."""
    return Markup([
        [Btn("🇳🇬 Naira (₦)", callback_data="obc_NGN"),
         Btn("🇰🇪 Shilling (KES)", callback_data="obc_KES")],
        [Btn("🇬🇭 Cedi (GH₵)", callback_data="obc_GHS"),
         Btn("🇿🇦 Rand (R)", callback_data="obc_ZAR")],
        [Btn("🇹🇿 Shilling (TSh)", callback_data="obc_TZS"),
         Btn("🇺🇬 Shilling (USh)", callback_data="obc_UGX")],
        [Btn("🇺🇸 USD ($)", callback_data="obc_USD")],
    ])


def kb_soccer():
    """Football menu — organized like Sportybet: Top leagues, cups, more."""
    return Markup([
        # Top 5 leagues
        [Btn("🏴󠁧󠁢󠁥󠁮󠁧󠁿 Premier League", callback_data="league_epl")],
        [Btn("🇪🇸 La Liga", callback_data="league_lal"),
         Btn("🇮🇹 Serie A", callback_data="league_sea")],
        [Btn("🇩🇪 Bundesliga", callback_data="league_bun"),
         Btn("🇫🇷 Ligue 1", callback_data="league_fl1")],
        # Cups
        [Btn("🏆 Champions League", callback_data="league_ucl"),
         Btn("🏆 Europa League", callback_data="league_uel")],
        # Sub-menus
        [Btn("🏆 More Cups", callback_data="soccer_cups"),
         Btn("📋 More Leagues", callback_data="soccer_more")],
        [Btn("🌍 International", callback_data="soccer_intl")],
        [Btn("🔙 Back", callback_data="main_menu")],
    ])


def kb_soccer_cups():
    cups = [(k, v) for k, v in LEAGUES.items() if v["sport"] == "soccer" and v.get("cat") == "cups"]
    rows = [[Btn(v["name"], callback_data=f"league_{k}")] for k, v in cups]
    rows.append([Btn("🔙 Back", callback_data="sport_soccer")])
    return Markup(rows)


def kb_soccer_more():
    more = [(k, v) for k, v in LEAGUES.items() if v["sport"] == "soccer" and v.get("cat") == "more"]
    rows = []
    # Two per row for compact display
    for i in range(0, len(more), 2):
        row = [Btn(more[i][1]["name"], callback_data=f"league_{more[i][0]}")]
        if i + 1 < len(more):
            row.append(Btn(more[i+1][1]["name"], callback_data=f"league_{more[i+1][0]}"))
        rows.append(row)
    rows.append([Btn("🔙 Back", callback_data="sport_soccer")])
    return Markup(rows)


def kb_soccer_intl():
    intl = [(k, v) for k, v in LEAGUES.items() if v["sport"] == "soccer" and v.get("cat") == "intl"]
    rows = [[Btn(v["name"], callback_data=f"league_{k}")] for k, v in intl]
    rows.append([Btn("🔙 Back", callback_data="sport_soccer")])
    return Markup(rows)


def kb_basketball():
    leagues = [(k, v) for k, v in LEAGUES.items() if v["sport"] == "basketball"]
    rows = [[Btn(v["name"], callback_data=f"league_{k}")] for k, v in leagues]
    rows.append([Btn("🔙 Back", callback_data="main_menu")])
    return Markup(rows)


def kb_tennis():
    leagues = [(k, v) for k, v in LEAGUES.items() if v["sport"] == "tennis"]
    rows = [[Btn(v["name"], callback_data=f"league_{k}")] for k, v in leagues]
    rows.append([Btn("🔙 Back", callback_data="main_menu")])
    return Markup(rows)


def kb_cricket():
    leagues = [(k, v) for k, v in LEAGUES.items() if v["sport"] == "cricket"]
    rows = [[Btn(v["name"], callback_data=f"league_{k}")] for k, v in leagues]
    rows.append([Btn("🔙 Back", callback_data="main_menu")])
    return Markup(rows)


def kb_esports():
    leagues = [(k, v) for k, v in LEAGUES.items() if v["sport"] == "esports"]
    rows = [[Btn(v["name"], callback_data=f"league_{k}")] for k, v in leagues]
    rows.append([Btn("🔙 Back", callback_data="main_menu")])
    return Markup(rows)


def kb_more_sports():
    """NFL, NHL, Rugby, etc."""
    return Markup([
        [Btn("🏈 NFL", callback_data="league_nfl"),
         Btn("🏈 NCAA Football", callback_data="league_cfb")],
        [Btn("🏒 NHL", callback_data="league_nhl")],
        [Btn("🏉 Rugby", callback_data="sport_rugby")],
        [Btn("🔙 Back", callback_data="main_menu")],
    ])


def kb_rugby():
    leagues = [(k, v) for k, v in LEAGUES.items() if v["sport"] == "rugby"]
    rows = [[Btn(v["name"], callback_data=f"league_{k}")] for k, v in leagues]
    rows.append([Btn("🔙 Back", callback_data="more_sports")])
    return Markup(rows)


def kb_wallet():
    return Markup([
        [Btn("💰 Balance", callback_data="w_balance")],
        [Btn("📥 Deposit", callback_data="w_deposit")],
        [Btn("📤 Withdraw", callback_data="w_withdraw")],
        [Btn("🔑 Export Key", callback_data="w_export")],
        [Btn("🔙 Back", callback_data="main_menu")],
    ])


def kb_settings():
    return Markup([
        [Btn("💱 Change Currency", callback_data="set_currency")],
        [Btn("🔙 Back", callback_data="main_menu")],
    ])


def kb_currencies():
    rows = []
    labels = {
        "USD": "🇺🇸 USD ($)", "NGN": "🇳🇬 Naira (₦)", "KES": "🇰🇪 Shilling (KES)",
        "GHS": "🇬🇭 Cedi (GH₵)", "ZAR": "🇿🇦 Rand (R)",
        "TZS": "🇹🇿 Shilling (TSh)", "UGX": "🇺🇬 Shilling (USh)",
    }
    for code, label in labels.items():
        rows.append([Btn(label, callback_data=f"cur_{code}")])
    rows.append([Btn("🔙 Back", callback_data="settings")])
    return Markup(rows)


def kb_game_odds(game: dict, idx: int, league: str):
    """Tappable odds buttons — clean Sportybet layout."""
    rows = []
    
    # 1X2 header + buttons
    if game.get("ml"):
        ml = game["ml"]
        r = []
        if ml["home"] > 0:
            r.append(Btn(f"1 │ {fmt_odds(ml['home'])}", callback_data=f"b_{league}_{idx}_ml_h"))
        if ml.get("draw", 0) > 0:
            r.append(Btn(f"X │ {fmt_odds(ml['draw'])}", callback_data=f"b_{league}_{idx}_ml_d"))
        if ml["away"] > 0:
            r.append(Btn(f"2 │ {fmt_odds(ml['away'])}", callback_data=f"b_{league}_{idx}_ml_a"))
        if r:
            rows.append(r)
    
    # Over/Under — filtered to relevant game totals only
    if game.get("totals"):
        from polymarket_api import _pick_main_total, _filter_totals_for_sport, _total_line_label, SPORT_LABELS
        sport = game.get("sport", "soccer")
        slabels = SPORT_LABELS.get(sport, SPORT_LABELS["soccer"])
        filtered = _filter_totals_for_sport(game["totals"], sport)
        if not filtered:
            filtered = game["totals"][:3]
        
        main = _pick_main_total(filtered, sport)
        if main:
            main_idx = game["totals"].index(main) if main in game["totals"] else 0
            
            # Main line button with sport-aware label
            try:
                main_line_val = float(main["line"])
            except:
                main_line_val = 0
            btn_prefix = _total_line_label(sport, main_line_val, slabels["total"], True).replace(f" O/U {main['line']}", "").replace(f" O/U {main_line_val:g}", "")
            
            r = []
            if main["over"] > 0:
                r.append(Btn(f"Ov {main['line']} │ {fmt_odds(main['over'])}", callback_data=f"b_{league}_{idx}_t_{main_idx}_o"))
            if main["under"] > 0:
                r.append(Btn(f"Un {main['line']} │ {fmt_odds(main['under'])}", callback_data=f"b_{league}_{idx}_t_{main_idx}_u"))
            if r:
                rows.append(r)
            
            # Other filtered lines
            for t in filtered:
                if t == main:
                    continue
                ti = game["totals"].index(t) if t in game["totals"] else 0
                try:
                    t_line_val = float(t["line"])
                except:
                    t_line_val = 0
                
                # Tennis: differentiate Sets vs Games in button
                if sport == "tennis" and t_line_val <= 3:
                    ov_label = f"Sets Ov {t['line']}"
                    un_label = f"Sets Un {t['line']}"
                else:
                    ov_label = f"Ov {t['line']}"
                    un_label = f"Un {t['line']}"
                
                r = []
                if t["over"] > 0:
                    r.append(Btn(f"{ov_label} │ {fmt_odds(t['over'])}", callback_data=f"b_{league}_{idx}_t_{ti}_o"))
                if t["under"] > 0:
                    r.append(Btn(f"{un_label} │ {fmt_odds(t['under'])}", callback_data=f"b_{league}_{idx}_t_{ti}_u"))
                if r:
                    rows.append(r)
    
    # GG/NG — soccer only
    sport = game.get("sport", "soccer")
    if game.get("btts") and sport == "soccer":
        bt = game["btts"]
        r = []
        if bt["yes"] > 0:
            r.append(Btn(f"GG │ {fmt_odds(bt['yes'])}", callback_data=f"b_{league}_{idx}_bt_y"))
        if bt["no"] > 0:
            r.append(Btn(f"NG │ {fmt_odds(bt['no'])}", callback_data=f"b_{league}_{idx}_bt_n"))
        if r:
            rows.append(r)
    
    # Handicap / Spread — filtered and deduped, correct +/-
    if game.get("spreads"):
        from polymarket_api import _filter_spreads
        sport_for_sp = game.get("sport", "soccer")
        filtered_sp = _filter_spreads(game["spreads"])
        
        # Sport-specific spread button prefix
        if sport_for_sp == "soccer":
            sp_prefix = "H"  # H1/H2 for handicap
        elif sport_for_sp == "tennis":
            sp_prefix = "G"  # G1/G2 for game spread
        else:
            sp_prefix = "S"  # S1/S2 for spread
        
        for sp in filtered_sp:
            si = game["spreads"].index(sp) if sp in game["spreads"] else 0
            h_line = sp.get("home_line", sp.get("line", ""))
            a_line = sp.get("away_line", "")
            if not a_line:
                line_num = sp.get("line_num", sp.get("line", "").lstrip("-+"))
                a_line = f"+{line_num}" if h_line.startswith("-") else f"-{line_num}"
            r = []
            if sp["home"] > 0:
                r.append(Btn(f"{sp_prefix}1 ({h_line}) │ {fmt_odds(sp['home'])}", callback_data=f"b_{league}_{idx}_sp_{si}_h"))
            if sp["away"] > 0:
                r.append(Btn(f"{sp_prefix}2 ({a_line}) │ {fmt_odds(sp['away'])}", callback_data=f"b_{league}_{idx}_sp_{si}_a"))
            if r:
                rows.append(r)
    
    rows.append([
        Btn("🔄 Refresh", callback_data=f"refresh_{league}_{idx}"),
        Btn("🔙 Back", callback_data=f"league_{league}")
    ])
    return Markup(rows)


# ========================================
# /start + Wallet Setup
# ========================================

async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    existing = await db.get_user(user.id)
    
    # Check for referral deep link: /start ref_12345
    ref_arg = ""
    if ctx.args and len(ctx.args) > 0:
        ref_arg = ctx.args[0]
        if ref_arg.startswith("ref_"):
            ctx.user_data["referrer_code"] = ref_arg
    
    if existing:
        await update.message.reply_text(
            f"👋 Welcome back, <b>{user.first_name}</b>!\n\n"
            f"⚽ <b>BetPoly</b> — Powered by Polymarket\n"
            f"The World's Largest Prediction Market\n\n"
            f"What are you betting on today? 👇",
            parse_mode=ParseMode.HTML, reply_markup=kb_main()
        )
        return ConversationHandler.END
    
    await update.message.reply_text(
        f"⚽🔥 <b>Welcome to BetPoly!</b>\n"
        f"<i>Powered by Polymarket — The World's Largest Prediction Market</i>\n\n"
        f"Bet on Football, Basketball, Tennis, Cricket & more.\n"
        f"Real-time odds from Polymarket's global liquidity pools.\n\n"
        f"✅ 50+ leagues — EPL, La Liga, NBA, UFC & more\n"
        f"✅ Bet in seconds — no sign-up forms\n"
        f"✅ Your wallet, your funds — fully self-custodial\n"
        f"✅ Automatic payouts after every match\n\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"🔐 <b>Step 1 of 3: Create your PIN</b>\n\n"
        f"Choose a <b>6-digit PIN</b> to secure your wallet.\n"
        f"You'll need this PIN to place bets and withdraw.\n\n"
        f"⬇️ Type your 6-digit PIN now:",
        parse_mode=ParseMode.HTML
    )
    return PIN_SET


async def pin_set(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    pin = update.message.text.strip()
    try:
        await update.message.delete()
    except:
        pass
    
    if not pin.isdigit() or len(pin) != 6:
        await update.effective_chat.send_message(
            "❌ PIN must be exactly <b>6 digits</b> (e.g. 482910)\n\nTry again:",
            parse_mode=ParseMode.HTML
        )
        return PIN_SET
    
    ctx.user_data["pin"] = pin
    await update.effective_chat.send_message(
        "✅ Got it!\n\n🔐 <b>Step 2 of 3: Confirm your PIN</b>\n\nType the same PIN again:",
        parse_mode=ParseMode.HTML
    )
    return PIN_CONFIRM


async def pin_confirm(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    pin = update.message.text.strip()
    try:
        await update.message.delete()
    except:
        pass
    
    if pin != ctx.user_data.get("pin"):
        ctx.user_data.pop("pin", None)
        await update.effective_chat.send_message(
            "❌ PINs don't match. Let's try again.\n\nType your 6-digit PIN:",
            parse_mode=ParseMode.HTML
        )
        return PIN_SET
    
    user = update.effective_user
    try:
        w = wallet.derive_wallet(user.id, pin)
    except Exception as e:
        await update.effective_chat.send_message(f"❌ Wallet error: {e}")
        return ConversationHandler.END
    
    # Get deterministic Safe address (this is free — no gas needed yet)
    try:
        safe_addr = wallet.get_safe_address(w["private_key"])
    except Exception as e:
        logger.error(f"Safe address derivation failed: {e}")
        safe_addr = ""  # Will be set during onboarding
    
    await db.create_user(user.id, user.username or "", w["address"], db.hash_pin(pin, user.id),
                         referred_by=_extract_referrer(ctx))
    
    # Store Safe address if we got it (NOT marking onboarded — that happens after deploy+approvals)
    if safe_addr:
        await db.set_safe_address(user.id, safe_addr)
    
    # The deposit address is the Safe (where USDC should go)
    deposit_addr = safe_addr or w["address"]
    
    # Show key in self-destructing message
    key_msg = await update.effective_chat.send_message(
        f"🔐 <b>YOUR RECOVERY KEY — SAVE NOW!</b>\n\n"
        f"📬 Deposit Address (Safe):\n<code>{deposit_addr}</code>\n\n"
        f"🔑 Private Key:\n<code>{w['private_key']}</code>\n\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"📸 Screenshot this or copy to a safe place.\n"
        f"You can import this key into MetaMask.\n\n"
        f"⚠️ <b>This message auto-deletes in 2 minutes!</b>",
        parse_mode=ParseMode.HTML
    )
    
    # Auto-delete after 120s
    try:
        ctx.job_queue.run_once(
            _delete_msg, 120,
            data={"chat_id": key_msg.chat_id, "msg_id": key_msg.message_id}
        )
    except:
        pass
    
    # Step 3: Currency selection
    await update.effective_chat.send_message(
        f"✅ <b>Wallet created!</b>\n\n"
        f"💱 <b>Step 3 of 3: Choose your display currency</b>\n\n"
        f"Odds and amounts will show in your preferred currency.\n"
        f"All bets settle in USDC on Polygon.\n\n"
        f"Select your currency 👇",
        parse_mode=ParseMode.HTML,
        reply_markup=kb_onboard_currency()
    )
    ctx.user_data.pop("pin", None)
    return CURRENCY_SELECT


async def onboard_currency(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Handle currency selection during onboarding."""
    q = update.callback_query
    await q.answer()
    d = q.data
    
    if d.startswith("obc_"):
        code = d[4:]
        user = update.effective_user
        await db.update_currency(user.id, code)
        
        u = await db.get_user(user.id)
        # Show Safe address for deposits (where USDC should be sent)
        addr = u.get("safe_address") or u.get("wallet_address", "") if u else ""
        
        await q.edit_message_text(
            f"🎉 <b>You're all set, {user.first_name}!</b>\n\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"📖 <b>HOW TO USE BETPOLY</b>\n\n"
            f"<b>1️⃣ Fund your wallet</b>\n"
            f"Send USDC (Polygon) to:\n"
            f"<code>{addr}</code>\n"
            f"💡 Binance → Withdraw USDC → Polygon network\n\n"
            f"<b>2️⃣ Tap 📅 Today</b>\n"
            f"See all today's games across every sport\n\n"
            f"<b>3️⃣ Tap the odds to bet</b>\n"
            f"Pick your stake → Confirm → Done!\n\n"
            f"<b>4️⃣ Collect winnings</b>\n"
            f"Automatic payouts after every match\n\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"Ready? 👇",
            parse_mode=ParseMode.HTML, reply_markup=kb_main()
        )
        return ConversationHandler.END
    
    return CURRENCY_SELECT


async def _delete_msg(ctx: ContextTypes.DEFAULT_TYPE):
    d = ctx.job.data
    try:
        await ctx.bot.delete_message(d["chat_id"], d["msg_id"])
    except:
        pass


# ========================================
# Command Shortcuts
# ========================================

async def cmd_live(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    user = await db.get_user(update.effective_user.id)
    if not user:
        await update.message.reply_text("Use /start first to create your wallet.")
        return
    cur = user.get("currency", "USD")
    msg = await update.message.reply_text("🔴 <b>LIVE NOW</b>\n\n⏳ Loading...", parse_mode=ParseMode.HTML)
    
    try:
        raw = await fetch_live()
    except:
        raw = []
    
    if not raw:
        await msg.edit_text(
            "🔴 <b>LIVE NOW</b>\n\nNo live matches right now.\nCheck upcoming games! 👇",
            parse_mode=ParseMode.HTML, reply_markup=kb_main()
        )
        return
    
    games = [parse_event(g) for g in raw]
    ctx.user_data["g_live"] = games
    rows = []
    for i, g in enumerate(games[:10]):
        sport = g.get("sport", "soccer")
        sport_emojis = {"soccer": "⚽", "basketball": "🏀", "tennis": "🎾", 
                        "hockey": "🏒", "cricket": "🏏", "mma": "🥊", "esports": "🎮",
                        "american_football": "🏈"}
        se = sport_emojis.get(sport, "🏟")
        home_full = g.get("home", "")[:18] or g.get("home_s", "?")
        away_full = g.get("away", "")[:18] or g.get("away_s", "?")
        label = f"🔴 {se} {home_full} vs {away_full}"
        if len(label) > 60:
            label = label[:57] + "..."
        rows.append([Btn(label, callback_data=f"game_live_{i}")])
    rows.append([Btn("🔙 Back", callback_data="main_menu")])
    await msg.edit_text("🔴 <b>LIVE NOW</b>\n\nTap for full odds 👇",
                        parse_mode=ParseMode.HTML, reply_markup=Markup(rows))


async def cmd_today(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Show all today's matches across popular leagues."""
    user = await db.get_user(update.effective_user.id)
    if not user:
        await update.message.reply_text("Use /start first to create your wallet.")
        return
    cur = user.get("currency", "USD")
    msg = await update.message.reply_text(
        "📅 <b>TODAY'S MATCHES</b>\n\n⏳ Loading across all leagues...",
        parse_mode=ParseMode.HTML
    )
    await _show_today_content(msg, cur, ctx, is_edit=True)


async def _show_today_content(msg, cur: str, ctx, is_edit=True):
    """Fetch today's games from top leagues and display grouped by sport."""
    import asyncio
    from datetime import datetime as dt, timezone, timedelta
    
    # Top leagues to check for today's games
    today_leagues = ["epl", "lal", "sea", "bun", "fl1", "ucl", "uel", "nba", "ncaab", "nhl", "atp", "wta", "mma", "cs2", "ipl"]
    
    # Fetch all in parallel
    tasks = [fetch_events(lg) for lg in today_leagues]
    results = await asyncio.gather(*tasks, return_exceptions=True)
    
    # Use UTC+1 (WAT) for "today" check
    wat = timezone(timedelta(hours=1))
    now = dt.now(wat)
    today_str = now.strftime("%Y-%m-%d")
    
    all_games = []
    for lg, raw in zip(today_leagues, results):
        if isinstance(raw, Exception) or not raw:
            continue
        info = LEAGUES.get(lg, {})
        for ev in raw:
            g = parse_event(ev)
            if g.get("time"):
                try:
                    gt = dt.fromisoformat(g["time"].replace("Z", "+00:00")).astimezone(wat)
                    if gt.strftime("%Y-%m-%d") == today_str:
                        g["_league"] = lg
                        g["_league_name"] = info.get("name", lg)
                        g["_sport"] = info.get("sport", "other")
                        g["_sport_emoji"] = info.get("emoji", "🏟")
                        all_games.append(g)
                except:
                    pass
    
    if not all_games:
        text = (
            f"📅 <b>TODAY — {now.strftime('%A, %d %B %Y')}</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"No matches scheduled for today.\n"
            f"Browse leagues for upcoming games 👇"
        )
        if is_edit:
            await msg.edit_text(text, parse_mode=ParseMode.HTML, reply_markup=kb_main())
        return
    
    # Group by sport, then by league
    from collections import OrderedDict
    sport_order = ["soccer", "basketball", "tennis", "hockey", "american_football", "mma", "cricket", "esports"]
    sport_names = {
        "soccer": "⚽ FOOTBALL", "basketball": "🏀 BASKETBALL",
        "tennis": "🎾 TENNIS", "hockey": "🏒 HOCKEY",
        "american_football": "🏈 AMERICAN FOOTBALL", "mma": "🥊 MMA",
        "cricket": "🏏 CRICKET", "esports": "🎮 ESPORTS",
    }
    
    # Sort games by sport order then time
    all_games.sort(key=lambda g: (
        sport_order.index(g["_sport"]) if g["_sport"] in sport_order else 99,
        g.get("time", "")
    ))
    
    ctx.user_data["g_today"] = all_games
    
    text = f"📅 <b>TODAY — {now.strftime('%A, %d %B')}</b>\n"
    text += "━━━━━━━━━━━━━━━━━━━━━━\n\n"
    
    current_sport = ""
    current_league = ""
    rows = []
    
    for i, g in enumerate(all_games[:20]):
        sport = g.get("_sport", "")
        lg_name = g.get("_league_name", "")
        
        # Sport header
        if sport != current_sport:
            current_sport = sport
            current_league = ""
            sname = sport_names.get(sport, sport.upper())
            text += f"<b>{sname}</b>\n"
            text += "─────────────────\n"
        
        # League sub-header
        if lg_name != current_league:
            current_league = lg_name
            text += f"  <b>{lg_name}</b>\n"
        
        # Time (WAT 12hr)
        _, time_str = fmt_time(g.get("time", ""))
        
        home = g.get("home", "")[:13] or g.get("title", "")[:15]
        away = g.get("away", "")[:13]
        
        if g.get("live"):
            tag = "🔴 LIVE"
        elif time_str:
            tag = f"🕐 {time_str}"
        else:
            tag = ""
        
        if g["ml"]:
            h = fmt_odds(g["ml"]["home"])
            a = fmt_odds(g["ml"]["away"])
            text += f"  {tag}\n"
            text += f"  {home} vs {away}\n"
            text += f"  <code>  {h:>5}  │  {a:>5}</code>\n\n"
        else:
            text += f"  {tag}  {home} vs {away}\n\n"
        
        # Button — full names + sport emoji
        sport_emoji = g.get("_sport_emoji", "🏟")
        home_full = g.get("home", "")[:18] or g.get("home_s", "?")
        away_full = g.get("away", "")[:18] or g.get("away_s", "?")
        if g.get("live"):
            btn = f"🔴 {sport_emoji} {home_full} vs {away_full}"
        elif time_str:
            btn = f"🕐{time_str} {sport_emoji} {home_full} vs {away_full}"
        else:
            btn = f"{sport_emoji} {home_full} vs {away_full}"
        if len(btn) > 60:
            btn = btn[:57] + "..."
        rows.append([Btn(btn, callback_data=f"game_today_{i}")])
    
    rows.append([Btn("🔙 Back", callback_data="main_menu")])
    
    if len(text) > 4000:
        text = text[:3950] + "\n\n<i>... tap matches below 👇</i>"
    
    if is_edit:
        await msg.edit_text(text, parse_mode=ParseMode.HTML, reply_markup=Markup(rows))


async def cmd_football(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    user = await db.get_user(update.effective_user.id)
    if not user:
        await update.message.reply_text("Use /start first to create your wallet.")
        return
    await update.message.reply_text("⚽ <b>Football</b> — Pick a league 👇",
                                     parse_mode=ParseMode.HTML, reply_markup=kb_soccer())


async def cmd_basketball(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    user = await db.get_user(update.effective_user.id)
    if not user:
        await update.message.reply_text("Use /start first to create your wallet.")
        return
    await update.message.reply_text("🏀 <b>Basketball</b> — Pick a league 👇",
                                     parse_mode=ParseMode.HTML, reply_markup=kb_basketball())


async def cmd_wallet(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    user = await db.get_user(update.effective_user.id)
    if not user:
        await update.message.reply_text("Use /start first to create your wallet.")
        return
    # Show Safe address (where USDC goes) not EOA
    addr = user.get("safe_address") or user.get("wallet_address", "")
    await update.message.reply_text(
        f"👛 <b>Your Wallet</b>\n\n"
        f"📬 Address:\n<code>{addr}</code>\n\n"
        f"💡 Send USDC on Polygon to deposit",
        parse_mode=ParseMode.HTML, reply_markup=kb_wallet()
    )


async def cmd_bets(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    user = await db.get_user(update.effective_user.id)
    if not user:
        await update.message.reply_text("Use /start first to create your wallet.")
        return
    cur = user.get("currency", "USD")
    bets = await db.get_user_bets(update.effective_user.id)
    if bets:
        text = "💰 <b>My Bets</b>\n\n"
        for b in bets[:10]:
            text += f"• <b>{b['selection']}</b> @ {b['odds']:.2f}\n"
            text += f"  Stake: {fmt_currency(b['amount_usdc'], cur)} → Win: {fmt_currency(b['potential_win'], cur)}\n"
            text += f"  Status: {b['status']}\n\n"
    else:
        text = "💰 <b>My Bets</b>\n\nNo bets yet. Pick a match to get started! 👇"
    await update.message.reply_text(text, parse_mode=ParseMode.HTML, reply_markup=kb_main())


async def cmd_settings(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    user = await db.get_user(update.effective_user.id)
    if not user:
        await update.message.reply_text("Use /start first to create your wallet.")
        return
    await update.message.reply_text("⚙️ <b>Settings</b>", parse_mode=ParseMode.HTML, reply_markup=kb_settings())


async def cmd_help(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "<b>❓ HOW BETPOLY WORKS</b>\n\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "<b>🎯 BETTING</b>\n"
        "1. Pick a sport → league → match\n"
        "2. Tap the odds button to select\n"
        "3. Choose your stake amount\n"
        "4. Confirm — bet is placed!\n\n"
        "<b>📊 READING ODDS</b>\n"
        "<code>1 = Home Win</code>\n"
        "<code>X = Draw</code>\n"
        "<code>2 = Away Win</code>\n"
        "<code>GG = Both Teams Score</code>\n"
        "<code>NG = Not Both Score</code>\n"
        "<code>Over/Under = Total Goals</code>\n"
        "<code>H1/H2 = Handicap</code>\n\n"
        "<b>💰 DEPOSITS</b>\n"
        "Buy USDC on Binance\n"
        "→ Withdraw to Polygon network\n"
        "→ Paste your BetPoly wallet address\n\n"
        "<b>🏆 PAYOUTS</b>\n"
        "Automatic! Within 1 hour of full time.\n"
        "Winnings go straight to your wallet.\n\n"
        "<b>🔑 YOUR WALLET</b>\n"
        "Fully self-custodial. You own the keys.\n"
        "Export anytime: /wallet → Export Key\n"
        "Import into MetaMask to use anywhere.\n\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "📩 Need help? Contact @BetPolySupport",
        parse_mode=ParseMode.HTML, reply_markup=kb_main()
    )


# ========================================
# Button Handler - Main Router
# ========================================

async def on_button(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    d = q.data
    
    # CRITICAL: Clear any pending text input flags when user taps a button.
    # This fixes the "stuck in input mode" bug.
    for flag in ["awaiting_stake", "awaiting_bet_pin", "awaiting_export"]:
        ctx.user_data.pop(flag, None)
    
    user = await db.get_user(q.from_user.id)
    cur = user.get("currency", "USD") if user else "USD"
    
    # Refresh currency rates if stale (non-blocking, cached 4hrs)
    await refresh_currency_rates()
    
    # --- Navigation ---
    if d == "main_menu":
        await q.edit_message_text("⚽ <b>BetPoly</b> — Powered by Polymarket\n\nPick a sport 👇",
                                  parse_mode=ParseMode.HTML, reply_markup=kb_main())
    
    elif d == "today":
        await q.edit_message_text("📅 <b>TODAY'S MATCHES</b>\n\n⏳ Loading...", parse_mode=ParseMode.HTML)
        await _show_today_content(q.message, cur, ctx, is_edit=True)
    
    elif d.startswith("game_today_"):
        idx = int(d.split("_")[2])
        games = ctx.user_data.get("g_today", [])
        if idx < len(games):
            g = games[idx]
            league = g.get("_league", "today")
            # Store in league cache for bet flow
            ctx.user_data[f"g_{league}"] = [g]
            info = LEAGUES.get(league, {"name": "Today", "emoji": "📅"})
            text = f"{info['emoji']} {info['name']}\n\n{format_match(g, cur)}"
            await q.edit_message_text(text, parse_mode=ParseMode.HTML, reply_markup=kb_game_odds(g, 0, league))
    
    elif d == "sport_soccer":
        await q.edit_message_text("⚽ <b>Football</b> — Pick a league 👇",
                                  parse_mode=ParseMode.HTML, reply_markup=kb_soccer())
    
    elif d == "soccer_cups":
        await q.edit_message_text("🏆 <b>Cup Competitions</b> 👇",
                                  parse_mode=ParseMode.HTML, reply_markup=kb_soccer_cups())
    
    elif d == "soccer_more":
        await q.edit_message_text("📋 <b>More Football Leagues</b> 👇",
                                  parse_mode=ParseMode.HTML, reply_markup=kb_soccer_more())
    
    elif d == "soccer_intl":
        await q.edit_message_text("🌍 <b>International Football</b> 👇",
                                  parse_mode=ParseMode.HTML, reply_markup=kb_soccer_intl())
    
    elif d == "sport_basketball":
        await q.edit_message_text("🏀 <b>Basketball</b> — Pick a league 👇",
                                  parse_mode=ParseMode.HTML, reply_markup=kb_basketball())
    
    elif d == "sport_tennis":
        await q.edit_message_text("🎾 <b>Tennis</b> — Pick a tour 👇",
                                  parse_mode=ParseMode.HTML, reply_markup=kb_tennis())
    
    elif d == "sport_cricket":
        await q.edit_message_text("🏏 <b>Cricket</b> — Pick a league 👇",
                                  parse_mode=ParseMode.HTML, reply_markup=kb_cricket())
    
    elif d == "sport_esports":
        await q.edit_message_text("🎮 <b>Esports</b> — Pick a game 👇",
                                  parse_mode=ParseMode.HTML, reply_markup=kb_esports())
    
    elif d == "more_sports":
        await q.edit_message_text("🏟 <b>More Sports</b> 👇",
                                  parse_mode=ParseMode.HTML, reply_markup=kb_more_sports())
    
    elif d == "sport_rugby":
        await q.edit_message_text("🏉 <b>Rugby</b> — Pick a competition 👇",
                                  parse_mode=ParseMode.HTML, reply_markup=kb_rugby())
    
    # --- League games ---
    elif d.startswith("league_"):
        league = d[7:]
        await _show_league(q, league, cur, ctx)
    
    # --- Refresh game odds ---
    elif d.startswith("refresh_"):
        parts = d.split("_")
        league = parts[1]
        idx = int(parts[2])
        # Re-fetch fresh data
        try:
            raw = await fetch_events(league)
            games = [parse_event(g) for g in raw]
            ctx.user_data[f"g_{league}"] = games
            if idx < len(games):
                await _show_game(q, league, idx, cur, ctx)
                await q.answer("🔄 Odds refreshed!")
            else:
                await q.answer("Match no longer available")
        except Exception as e:
            logger.error(f"Refresh error: {e}")
            await q.answer("⚠️ Refresh failed, try again")
    
    # --- Live ---
    elif d == "live":
        await _show_live(q, cur, ctx)
    
    # --- Game detail ---
    elif d.startswith("game_"):
        parts = d.split("_")
        await _show_game(q, parts[1], int(parts[2]), cur, ctx)
    
    # --- Bet selection ---
    elif d.startswith("b_"):
        await _handle_bet(q, d, cur, ctx)
    
    # --- Stake buttons ---
    elif d.startswith("stake_"):
        await _handle_stake(q, d, cur, ctx)
    
    # --- Confirm bet ---
    elif d.startswith("confirm_"):
        await _confirm_bet(q, d, cur, ctx)
    
    # --- More lines (placeholder) ---
    elif d.startswith("more_"):
        parts = d.split("_")
        await _show_game(q, parts[1], int(parts[2]), cur, ctx)
    
    # --- Wallet ---
    elif d == "wallet_menu":
        await q.edit_message_text("👛 <b>Wallet</b>", parse_mode=ParseMode.HTML, reply_markup=kb_wallet())
    
    elif d == "w_balance":
        if user:
            # Use Safe address for balance (that's where USDC lives)
            addr = user.get("safe_address") or user.get("wallet_address", "")
            eoa_addr = user.get("wallet_address", "")
            await q.answer("⏳ Checking balance...")
            try:
                bals = trade.get_usdc_balances(address=addr)
                matic_bal = trade.get_matic_balance(address=addr)
                usdc_e = bals["usdc_e"]
                usdc_native = bals["usdc_native"]
                total = usdc_e + usdc_native
                
                bal_text = f"💰 <b>Balance: ${total:.2f}</b> ({fmt_currency(total, cur)})\n\n"
                
                if usdc_e > 0:
                    bal_text += f"  ✅ USDC.e: <b>${usdc_e:.2f}</b> <i>(ready to bet)</i>\n"
                else:
                    bal_text += f"  ⬜ USDC.e: $0.00\n"
                
                if usdc_native > 0:
                    bal_text += f"  ⚠️ USDC: <b>${usdc_native:.2f}</b> <i>(needs swap)</i>\n"
                    bal_text += f"\n⚠️ <b>You have native USDC.</b>\n"
                    bal_text += f"Polymarket uses USDC.e. Swap on\n"
                    bal_text += f"<a href='https://quickswap.exchange/#/swap'>QuickSwap</a> or import key to MetaMask."
                
                bal_text += f"\n⛽ POL (gas): {matic_bal:.4f}"
                
                # Check onboarding status
                is_onboarded = bool(user.get("onboarded"))
                
                if total == 0:
                    bal_text += "\n\n💡 <i>No funds yet. Tap 📥 Deposit to fund.</i>"
                
                if not is_onboarded and total > 0:
                    # Need to onboard before first bet
                    eoa_pol = trade.get_matic_balance(address=eoa_addr) if eoa_addr != addr else matic_bal
                    if eoa_pol < 0.05:
                        bal_text += (f"\n\n⚠️ <i>One-time setup needed! Send ~0.1 POL to EOA:</i>\n"
                                    f"<code>{eoa_addr}</code>")
                    else:
                        bal_text += "\n\n⚙️ <i>Wallet setup pending. Place a bet to auto-setup.</i>"
                    
            except Exception as e:
                logger.error(f"Balance check error: {e}")
                bal_text = "💰 <i>Balance check failed. Is POLYGON_RPC_URL set?</i>"
            
            await q.edit_message_text(
                f"👛 <b>Your Wallet</b>\n\n"
                f"📬 <code>{addr}</code>\n\n"
                f"{bal_text}\n\n"
                f"📊 Total volume: {fmt_currency(user.get('total_volume', 0), cur)}\n"
                f"💸 Total fees: {fmt_currency(user.get('total_fees_paid', 0), cur)}",
                parse_mode=ParseMode.HTML,
                disable_web_page_preview=True,
                reply_markup=Markup([
                    [Btn("🔄 Refresh", callback_data="w_balance")],
                    [Btn("📥 Deposit", callback_data="w_deposit")],
                    [Btn("🔙 Back", callback_data="wallet_menu")]
                ])
            )
    
    elif d == "w_deposit":
        if user:
            # Deposit to Safe address (where USDC lives for trading)
            deposit_addr = user.get("safe_address") or user.get("wallet_address", "")
            eoa_addr = user.get("wallet_address", "")
            is_onboarded = bool(user.get("onboarded"))
            
            setup_note = ""
            if not is_onboarded:
                setup_note = (
                    f"\n<b>⚙️ First-time setup:</b>\n"
                    f"Also send ~0.1 POL to your EOA for one-time wallet activation:\n"
                    f"<code>{eoa_addr}</code>\n\n"
                )
            
            await q.edit_message_text(
                f"📥 <b>Deposit</b>\n\n"
                f"Send <b>USDC.e</b> on <b>Polygon</b> to:\n\n"
                f"<code>{deposit_addr}</code>\n\n"
                f"━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
                f"{setup_note}"
                f"<b>From Binance:</b>\n"
                f"Withdraw → USDC → Polygon network → Paste address\n\n"
                f"<b>From another wallet:</b>\n"
                f"Send USDC.e on Polygon (not Ethereum!)\n\n"
                f"⚠️ <b>Important:</b>\n"
                f"• Only Polygon network (not Ethereum/BSC)\n"
                f"• USDC.e preferred (Polymarket uses this)\n"
                f"• Min bet: $1 USDC",
                parse_mode=ParseMode.HTML, reply_markup=kb_wallet()
            )
    
    elif d == "w_withdraw":
        await q.edit_message_text(
            "📤 <b>Withdraw</b>\n\n"
            "Coming soon! For now, export your key and use MetaMask to withdraw.",
            parse_mode=ParseMode.HTML, reply_markup=kb_wallet()
        )
    
    elif d == "w_export":
        await q.edit_message_text(
            "🔑 <b>Export Private Key</b>\n\n"
            "⚠️ Never share your key with anyone.\n\n"
            "Enter your 6-digit PIN:",
            parse_mode=ParseMode.HTML,
            reply_markup=Markup([[Btn("🔙 Cancel", callback_data="wallet_menu")]])
        )
        ctx.user_data["awaiting_export"] = True
    
    # --- My Bets ---
    elif d == "my_bets":
        if user:
            bets = await db.get_user_bets(q.from_user.id)
            if bets:
                text = "💰 <b>My Bets</b>\n\n"
                for b in bets[:10]:
                    status_emoji = {"placed": "⏳", "won": "✅", "lost": "❌", "settled": "🏁"}.get(b.get("status", ""), "❓")
                    gross = b.get("amount_gross") or b.get("amount_usdc", 0) or 0
                    bet_odds = b.get("odds", 0) or 0
                    text += f"{status_emoji} <b>{b.get('selection', '?')}</b>\n"
                    text += f"  {b.get('match_name', '?')}\n"
                    text += f"  Stake: {fmt_currency(gross, cur)} @ {bet_odds:.2f}\n"
                    text += f"  Status: {b.get('status', 'unknown')}\n\n"
            else:
                text = "💰 <b>My Bets</b>\n\nNo bets yet. Pick a match to get started! 👇"
            await q.edit_message_text(text, parse_mode=ParseMode.HTML, reply_markup=kb_main())
    
    # --- Settings ---
    elif d == "settings":
        await q.edit_message_text("⚙️ <b>Settings</b>", parse_mode=ParseMode.HTML, reply_markup=kb_settings())
    
    elif d == "set_currency":
        await q.edit_message_text(
            "💱 <b>Display Currency</b>\n\nAll bets settle in USDC. This just changes the display.",
            parse_mode=ParseMode.HTML, reply_markup=kb_currencies()
        )
    
    elif d.startswith("cur_"):
        code = d[4:]
        if user:
            await db.update_currency(q.from_user.id, code)
        sym = CURRENCIES.get(code, {}).get("symbol", "$")
        await q.edit_message_text(
            f"✅ Currency: <b>{code}</b> ({sym})\n\nExample: 1 USDC = {fmt_currency(1.0, code)}",
            parse_mode=ParseMode.HTML, reply_markup=kb_main()
        )
    
    # --- Help ---
    elif d == "help":
        await q.edit_message_text(
            "<b>❓ How BetPoly Works</b>\n\n"
            "<b>1. Pick a match</b>\n"
            "Browse Football, Basketball, Tennis & more\n\n"
            "<b>2. Tap the odds</b>\n"
            "1 = Home  |  X = Draw (football)  |  2 = Away\n"
            "Over/Under = Total points, goals, or games\n"
            "GG = Both Score  |  H1/H2 = Handicap\n\n"
            "<b>3. Enter your stake</b>\n"
            "Min bet: $1 USDC\n\n"
            "<b>4. Win and collect</b>\n"
            "Payouts are automatic after the match.\n\n"
            "💰 <b>Deposit:</b> Binance → Withdraw USDC on Polygon → Paste your address\n"
            "🔑 <b>Your wallet is yours:</b> Export key in Settings → import to MetaMask",
            parse_mode=ParseMode.HTML, reply_markup=kb_main()
        )
    
    # --- Referral ---
    elif d == "referral":
        if user:
            stats = await db.get_referral_stats(q.from_user.id)
            bot_username = (await ctx.bot.get_me()).username
            ref_link = f"https://t.me/{bot_username}?start=ref_{q.from_user.id}"
            
            text = (
                f"📣 <b>INVITE FRIENDS — EARN USDC</b>\n\n"
                f"Your referral link:\n"
                f"<code>{ref_link}</code>\n\n"
                f"━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
                f"📊 <b>Your Stats</b>\n"
                f"  👥 Friends invited: <b>{stats['direct_referrals']}</b>\n"
                f"  💰 Total earned: <b>${stats['total_earned']:.4f}</b> USDC\n\n"
                f"💡 <b>How it works</b>\n"
                f"  You earn from every bet your friends place:\n"
                f"  • Level 1 (direct): <b>25%</b> of 1% fee\n"
                f"  • Level 2: <b>5%</b> of 1% fee\n"
                f"  • Level 3: <b>3%</b> of 1% fee\n\n"
                f"Example: Friend bets $100 → $1 fee → you get $0.25"
            )
            await q.edit_message_text(
                text, parse_mode=ParseMode.HTML,
                reply_markup=Markup([
                    [Btn("📋 Copy Link", callback_data="ref_copy")],
                    [Btn("🔙 Back", callback_data="main_menu")]
                ])
            )
    
    elif d == "ref_copy":
        await q.answer("📋 Tap and hold the link above to copy!", show_alert=True)


# ========================================
# League / Game / Live Views
# ========================================

async def _show_league(q, league: str, cur: str, ctx):
    info = LEAGUES.get(league, {"name": league.upper(), "emoji": "🏟", "sport": "soccer"})
    
    await q.edit_message_text(
        f"{info['emoji']} <b>{info['name']}</b>\n\n⏳ Loading matches...",
        parse_mode=ParseMode.HTML
    )
    
    try:
        raw = await fetch_events(league)
    except Exception as e:
        logger.error(f"League fetch error: {e}")
        raw = []
    
    if not raw:
        await q.edit_message_text(
            f"{info['emoji']} <b>{info['name']}</b>\n\n"
            f"No upcoming matches found.\nCheck back closer to matchday!",
            parse_mode=ParseMode.HTML,
            reply_markup=Markup([[Btn("🔙 Back", callback_data="main_menu")]])
        )
        return
    
    games = [parse_event(g) for g in raw]
    ctx.user_data[f"g_{league}"] = games
    
    sport = info.get("sport", "soccer")
    slabels = SPORT_LABELS.get(sport, SPORT_LABELS["soccer"])
    has_draw = slabels["has_draw"]
    
    text = f"{info['emoji']} <b>{info['name']}</b>\n"
    text += "━━━━━━━━━━━━━━━━━━━━━━\n\n"
    
    current_date = ""
    rows = []
    for i, g in enumerate(games[:15]):
        date_str, time_str = fmt_time(g.get("time", ""))
        
        # Bold date header when date changes
        if date_str and date_str != current_date:
            current_date = date_str
            text += f"📅 <b>{date_str}</b>\n"
            text += "─────────────────\n"
        
        home = g.get("home", "")[:13] or g.get("title", "")[:15]
        away = g.get("away", "")[:13]
        
        # Time tag
        if g.get("live"):
            tag = "🔴 <b>LIVE</b>"
        elif time_str:
            tag = f"🕐 <b>{time_str}</b>"
        else:
            tag = ""
        
        # Odds preview
        if g["ml"]:
            ml = g["ml"]
            h = fmt_odds(ml["home"])
            a = fmt_odds(ml["away"])
            if has_draw and ml.get("draw", 0) > 0:
                d = fmt_odds(ml["draw"])
                odds_str = f"<code>  {h:>5}  │  {d:>5}  │  {a:>5}</code>"
            elif has_draw:
                odds_str = f"<code>  {h:>5}  │    —   │  {a:>5}</code>"
            else:
                odds_str = f"<code>  {h:>5}  │  {a:>5}</code>"
        else:
            odds_str = ""
        
        text += f"  {tag}\n"
        text += f"  <b>{home}</b>  vs  <b>{away}</b>\n"
        if odds_str:
            text += f"  {odds_str}\n"
        text += "\n"
        
        # Button — full names + sport emoji
        emoji = info.get("emoji", "🏟")
        home_full = g.get("home", "")[:18] or g.get("home_s", "HOM")
        away_full = g.get("away", "")[:18] or g.get("away_s", "AWY")
        btn_label = f"{emoji} {home_full} vs {away_full}"
        if time_str:
            btn_label = f"🕐{time_str} {emoji} {home_full} vs {away_full}"
        if g.get("live"):
            btn_label = f"🔴 {emoji} {home_full} vs {away_full}"
        # Telegram limits callback button text, truncate if needed
        if len(btn_label) > 60:
            btn_label = btn_label[:57] + "..."
        rows.append([Btn(btn_label, callback_data=f"game_{league}_{i}")])
    
    rows.append([Btn("🔙 Back", callback_data="main_menu")])
    
    if len(text) > 4000:
        text = text[:3950] + "\n\n<i>... more matches below 👇</i>"
    
    await q.edit_message_text(text, parse_mode=ParseMode.HTML, reply_markup=Markup(rows))




async def _show_game(q, league: str, idx: int, cur: str, ctx):
    games = ctx.user_data.get(f"g_{league}", [])
    if idx >= len(games):
        await q.edit_message_text("❌ Match not found.", reply_markup=kb_main())
        return
    
    g = games[idx]
    info = LEAGUES.get(league, {"name": league, "emoji": "🏟"})
    text = f"{info['emoji']} {info['name']}\n\n{format_match(g, cur)}"
    
    await q.edit_message_text(text, parse_mode=ParseMode.HTML, reply_markup=kb_game_odds(g, idx, league))


async def _show_live(q, cur: str, ctx):
    await q.edit_message_text("🔴 <b>LIVE NOW</b>\n\n⏳ Loading...", parse_mode=ParseMode.HTML)
    
    try:
        raw = await fetch_live()
    except:
        raw = []
    
    if not raw:
        await q.edit_message_text(
            "🔴 <b>LIVE NOW</b>\n\nNo live matches right now.\nCheck upcoming games! 👇",
            parse_mode=ParseMode.HTML, reply_markup=kb_main()
        )
        return
    
    games = [parse_event(g) for g in raw]
    ctx.user_data["g_live"] = games
    
    # Group by sport
    sport_names = {
        "soccer": "⚽ FOOTBALL", "basketball": "🏀 BASKETBALL",
        "tennis": "🎾 TENNIS", "hockey": "🏒 HOCKEY",
        "american_football": "🏈 AMERICAN FOOTBALL", "mma": "🥊 MMA",
        "cricket": "🏏 CRICKET", "esports": "🎮 ESPORTS",
    }
    
    text = "🔴 <b>LIVE NOW</b>\n"
    text += "━━━━━━━━━━━━━━━━━━━━━━\n\n"
    
    current_sport = ""
    rows = []
    for i, g in enumerate(games[:15]):
        sport = g.get("sport", "soccer")
        
        if sport != current_sport:
            current_sport = sport
            sname = sport_names.get(sport, sport.upper())
            text += f"<b>{sname}</b>\n"
            text += "─────────────────\n"
        
        home = g.get("home", "")[:13] or g.get("title", "")[:15]
        away = g.get("away", "")[:13]
        
        if g["ml"]:
            h = fmt_odds(g["ml"]["home"])
            a = fmt_odds(g["ml"]["away"])
            text += f"  🔴 <b>{home}</b> vs <b>{away}</b>\n"
            text += f"  <code>  {h:>5}  │  {a:>5}</code>\n\n"
        else:
            text += f"  🔴 <b>{home}</b> vs <b>{away}</b>\n\n"
        
        # Button — full names with sport emoji
        sport_emojis = {"soccer": "⚽", "basketball": "🏀", "tennis": "🎾", 
                        "hockey": "🏒", "cricket": "🏏", "mma": "🥊", "esports": "🎮",
                        "american_football": "🏈"}
        se = sport_emojis.get(sport, "🏟")
        home_full = g.get("home", "")[:18] or g.get("home_s", "?")
        away_full = g.get("away", "")[:18] or g.get("away_s", "?")
        label = f"🔴 {se} {home_full} vs {away_full}"
        if len(label) > 60:
            label = label[:57] + "..."
        rows.append([Btn(label, callback_data=f"game_live_{i}")])
    
    rows.append([Btn("🔙 Back", callback_data="main_menu")])
    
    if len(text) > 4000:
        text = text[:3950] + "\n\n<i>... tap matches below 👇</i>"
    
    await q.edit_message_text(text, parse_mode=ParseMode.HTML, reply_markup=Markup(rows))


# ========================================
# Bet Flow
# ========================================

async def _handle_bet(q, data: str, cur: str, ctx):
    """Parse bet callback: b_{league}_{idx}_{type}_{side}"""
    parts = data.split("_")
    league = parts[1]
    idx = int(parts[2])
    mtype = parts[3]
    
    games = ctx.user_data.get(f"g_{league}", [])
    if idx >= len(games):
        return
    g = games[idx]
    
    label = ""
    odds = 0.0
    token_id = ""
    price = 0.0
    
    if mtype == "ml":
        side = parts[4]
        ml = g["ml"]
        if side == "h":
            odds = price_to_odds(ml["home"])
            price = ml["home"]
            token_id = ml.get("home_tid", "")
            label = f"1 ({g['home']})"
        elif side == "d":
            odds = price_to_odds(ml["draw"])
            price = ml["draw"]
            token_id = ml.get("draw_tid", "")
            label = "X (Draw)"
        elif side == "a":
            odds = price_to_odds(ml["away"])
            price = ml["away"]
            token_id = ml.get("away_tid", "")
            label = f"2 ({g['away']})"
    
    elif mtype == "t":
        ti, side = int(parts[4]), parts[5]
        t = g["totals"][ti]
        if side == "o":
            odds = price_to_odds(t["over"])
            price = t["over"]
            token_id = t.get("over_tid", "")
            label = f"Over {t['line']}"
        else:
            odds = price_to_odds(t["under"])
            price = t["under"]
            token_id = t.get("under_tid", "")
            label = f"Under {t['line']}"
    
    elif mtype == "bt":
        side = parts[4]
        if side == "y":
            odds = price_to_odds(g["btts"]["yes"])
            price = g["btts"]["yes"]
            token_id = g["btts"].get("yes_tid", "")
            label = "GG (Both Teams Score)"
        else:
            odds = price_to_odds(g["btts"]["no"])
            price = g["btts"]["no"]
            token_id = g["btts"].get("no_tid", "")
            label = "NG (Not Both Score)"
    
    elif mtype == "sp":
        si, side = int(parts[4]), parts[5]
        sp = g["spreads"][si]
        if side == "h":
            odds = price_to_odds(sp["home"])
            price = sp["home"]
            token_id = sp.get("home_tid", "")
            label = f"H1 ({g['home_s']} {sp['line']})"
        else:
            odds = price_to_odds(sp["away"])
            price = sp["away"]
            token_id = sp.get("away_tid", "")
            label = f"H2 ({g['away_s']} +{sp['line']})"
    
    if odds <= 0:
        return
    
    match_name = f"{g['home']} vs {g['away']}"
    ctx.user_data["bet"] = {
        "league": league, "idx": idx, "label": label,
        "odds": odds, "match": match_name, "cb": data,
        "token_id": token_id, "price": price,
    }
    
    # Stake selection
    stakes = [1, 5, 10, 25, 50]
    r1 = [Btn(f"{fmt_currency(s, cur)}", callback_data=f"stake_{s}") for s in stakes[:3]]
    r2 = [Btn(f"{fmt_currency(s, cur)}", callback_data=f"stake_{s}") for s in stakes[3:]]
    
    await q.edit_message_text(
        f"🎫 <b>BET SLIP</b>\n\n"
        f"📋 {match_name}\n"
        f"✅ <b>{label}</b>\n"
        f"📊 Odds: <b>{odds:.2f}</b>\n\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"How much? Tap or type amount 👇",
        parse_mode=ParseMode.HTML,
        reply_markup=Markup([
            r1, r2,
            [Btn("✏️ Custom Amount", callback_data="stake_custom")],
            [Btn("❌ Cancel", callback_data=f"game_{league}_{idx}")]
        ])
    )


async def _handle_stake(q, data: str, cur: str, ctx):
    bet = ctx.user_data.get("bet")
    if not bet:
        await q.edit_message_text("❌ No bet selected.", reply_markup=kb_main())
        return
    
    if data == "stake_custom":
        await q.edit_message_text(
            f"🎫 <b>{bet['label']}</b> @ <b>{bet['odds']:.2f}</b>\n\n"
            f"Type your stake in USDC (e.g. 5):",
            parse_mode=ParseMode.HTML
        )
        ctx.user_data["awaiting_stake"] = True
        return
    
    stake = float(data.replace("stake_", ""))
    await _show_confirm(q, stake, bet, cur)


async def _show_confirm(q, stake: float, bet: dict, cur: str):
    odds = bet["odds"]
    win = round(stake * odds, 2)
    profit = round(win - stake, 2)
    
    await q.edit_message_text(
        f"🎫 <b>CONFIRM BET</b>\n\n"
        f"📋 {bet['match']}\n"
        f"✅ {bet['label']}\n"
        f"📊 Odds: <b>{odds:.2f}</b>\n\n"
        f"💰 Stake: <b>{fmt_currency(stake, cur)}</b> ({stake} USDC)\n"
        f"🎯 To Win: <b>{fmt_currency(win, cur)}</b> ({win} USDC)\n"
        f"📈 Profit: <b>{fmt_currency(profit, cur)}</b>\n\n"
        f"⚠️ Settles within 1hr of full time.",
        parse_mode=ParseMode.HTML,
        reply_markup=Markup([
            [Btn(f"✅ PLACE BET — {fmt_currency(stake, cur)}", callback_data=f"confirm_{stake}")],
            [Btn("❌ Cancel", callback_data="main_menu")]
        ])
    )


async def _confirm_bet(q, data: str, cur: str, ctx):
    stake = float(data.replace("confirm_", ""))
    bet = ctx.user_data.get("bet")
    if not bet:
        await q.edit_message_text("❌ Bet expired.", reply_markup=kb_main())
        return
    
    user_obj = await db.get_user(q.from_user.id)
    if not user_obj:
        await q.edit_message_text("❌ Please set up your wallet first with /start", reply_markup=kb_main())
        return
    
    # Get token_id from the stored game data
    token_id = bet.get("token_id", "")
    price = bet.get("price", 0)
    
    if not token_id:
        await q.edit_message_text(
            "❌ This market doesn't have trade data yet.\n"
            "Try refreshing the match or pick another selection.",
            parse_mode=ParseMode.HTML, reply_markup=kb_main()
        )
        return
    
    # Show processing message
    await q.edit_message_text(
        f"⏳ <b>Placing your bet...</b>\n\n"
        f"📋 {bet['match']}\n"
        f"✅ {bet['label']}\n"
        f"💰 Stake: {fmt_currency(stake, cur)}",
        parse_mode=ParseMode.HTML
    )
    
    # Derive wallet
    # Note: User must enter PIN to confirm bet in production.
    # For MVP, we use stored PIN hash to verify identity.
    try:
        # Get private key from PIN (user already authenticated)
        pin = ctx.user_data.get("pin_cache", "")
        if not pin:
            # Ask for PIN before placing bet
            ctx.user_data["pending_confirm_stake"] = stake
            await q.edit_message_text(
                f"🔐 <b>Enter your 6-digit PIN to confirm bet:</b>\n\n"
                f"📋 {bet['match']}\n"
                f"✅ {bet['label']} @ {bet['odds']:.2f}\n"
                f"💰 Stake: {fmt_currency(stake, cur)}",
                parse_mode=ParseMode.HTML,
                reply_markup=Markup([[Btn("❌ Cancel", callback_data="main_menu")]])
            )
            ctx.user_data["awaiting_bet_pin"] = True
            return
        
        w = wallet.derive_wallet(q.from_user.id, pin)
        private_key = w["private_key"]
        
        # Get safe_address from DB, or derive it
        safe_addr = user_obj.get("safe_address", "")
        if not safe_addr:
            try:
                safe_addr = wallet.get_safe_address(private_key)
                await db.set_safe_address(q.from_user.id, safe_addr)
            except Exception as sae:
                logger.warning(f"Could not get Safe address: {sae}")
        
        # Auto-onboard if needed (one-time: deploy Safe + set approvals)
        if not user_obj.get("onboarded") and safe_addr:
            await q.edit_message_text(
                f"⚙️ <b>One-time wallet setup...</b>\n\n"
                f"Deploying your Polymarket Safe wallet.\n"
                f"This takes ~30 seconds.",
                parse_mode=ParseMode.HTML
            )
            try:
                ob_result = wallet.onboard_wallet(private_key)
                if ob_result["success"]:
                    await db.update_safe_address(q.from_user.id, ob_result["safe_address"])
                    safe_addr = ob_result["safe_address"]
                    logger.info(f"Auto-onboarded user {q.from_user.id}: Safe={safe_addr}")
                else:
                    await q.edit_message_text(
                        f"❌ <b>Wallet setup failed</b>\n\n"
                        f"{ob_result.get('error', 'Unknown error')}\n\n"
                        f"💡 Make sure you have ~0.1 POL in your EOA:\n"
                        f"<code>{w['address']}</code>",
                        parse_mode=ParseMode.HTML, reply_markup=kb_main()
                    )
                    return
            except Exception as obe:
                logger.error(f"Auto-onboard failed: {obe}")
                await q.edit_message_text(
                    f"❌ <b>Wallet setup failed</b>\n\n"
                    f"Send ~0.1 POL to your EOA address:\n"
                    f"<code>{w['address']}</code>\n\n"
                    f"Then try again.",
                    parse_mode=ParseMode.HTML, reply_markup=kb_main()
                )
                return
            
            # Show processing message again
            await q.edit_message_text(
                f"⏳ <b>Placing your bet...</b>\n\n"
                f"📋 {bet['match']}\n"
                f"✅ {bet['label']}\n"
                f"💰 Stake: {fmt_currency(stake, cur)}",
                parse_mode=ParseMode.HTML
            )
        
        # Execute trade with Safe address
        result = await trade.place_bet(
            private_key=private_key,
            token_id=token_id,
            price=price,
            amount_usdc=stake,
            match_name=bet["match"],
            selection=bet["label"],
            safe_address=safe_addr,
        )
        
        if result["success"]:
            # Log bet in database
            fee_info = trade.calculate_fee(stake)
            bet_id = await db.log_bet(
                telegram_id=q.from_user.id,
                match_name=bet["match"],
                selection=bet["label"],
                amount_gross=stake,
                amount_net=fee_info["net"],
                fee=fee_info["fee"],
                price=price,
                odds=bet["odds"],
                shares=result["shares"],
                token_id=token_id,
                order_id=result["order_id"],
            )
            
            # Process referral fees
            await db.process_referral_fee(q.from_user.id, bet_id, fee_info["fee"])
            
            win = round(result["shares"] * 1.0, 2)  # Max payout = shares * $1
            await q.edit_message_text(
                f"✅ <b>BET PLACED!</b>\n\n"
                f"📋 {bet['match']}\n"
                f"✅ {bet['label']}\n"
                f"📊 Odds: {bet['odds']:.2f}\n\n"
                f"💰 Stake: {fmt_currency(stake, cur)}\n"
                f"💸 Fee (1%): {fmt_currency(fee_info['fee'], cur)}\n"
                f"🎯 Max Payout: {fmt_currency(win, cur)}\n\n"
                f"🆔 Order: <code>{result['order_id'][:16]}...</code>\n\n"
                f"⏳ Settles after the match.\nCheck <b>My Bets</b> for updates.",
                parse_mode=ParseMode.HTML, reply_markup=kb_main()
            )
        else:
            await q.edit_message_text(
                f"❌ <b>Bet Failed</b>\n\n"
                f"{result['error']}\n\n"
                f"💡 Make sure you have enough USDC in your wallet.\n"
                f"Check 👛 Wallet → Balance",
                parse_mode=ParseMode.HTML, reply_markup=kb_main()
            )
        
    except Exception as e:
        logger.error(f"Bet execution error: {e}")
        await q.edit_message_text(
            f"❌ <b>Error placing bet</b>\n\n"
            f"Please try again. If the issue persists,\n"
            f"check your wallet balance.",
            parse_mode=ParseMode.HTML, reply_markup=kb_main()
        )
    
    ctx.user_data.pop("bet", None)
    ctx.user_data.pop("pin_cache", None)


# ========================================
# Text Input Handler
# ========================================

async def on_text(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    
    # Universal cancel — typing "cancel", "stop", "back", "menu" clears all pending states
    if text.lower() in ("cancel", "stop", "back", "menu", "/cancel", "/menu", "/start"):
        for flag in ["awaiting_stake", "awaiting_bet_pin", "awaiting_export",
                     "pending_confirm_stake", "bet"]:
            ctx.user_data.pop(flag, None)
        await update.message.reply_text(
            "✅ Cancelled. Back to main menu 👇",
            reply_markup=kb_main()
        )
        return
    
    # Custom stake
    if ctx.user_data.get("awaiting_stake"):
        ctx.user_data.pop("awaiting_stake")
        bet = ctx.user_data.get("bet")
        if not bet:
            await update.message.reply_text("❌ No bet selected.", reply_markup=kb_main())
            return
        try:
            stake = float(text)
            if stake < 1:
                await update.message.reply_text("❌ Min $1 USDC. Try again:")
                ctx.user_data["awaiting_stake"] = True
                return
            if stake > 10000:
                await update.message.reply_text("❌ Max $10,000 USDC. Try again:")
                ctx.user_data["awaiting_stake"] = True
                return
        except ValueError:
            await update.message.reply_text("❌ Enter a number (e.g. 5):")
            ctx.user_data["awaiting_stake"] = True
            return
        
        user = await db.get_user(update.effective_user.id)
        cur = user.get("currency", "USD") if user else "USD"
        odds = bet["odds"]
        win = round(stake * odds, 2)
        profit = round(win - stake, 2)
        
        await update.message.reply_text(
            f"🎫 <b>CONFIRM BET</b>\n\n"
            f"📋 {bet['match']}\n"
            f"✅ {bet['label']}\n"
            f"📊 Odds: <b>{odds:.2f}</b>\n\n"
            f"💰 Stake: <b>{fmt_currency(stake, cur)}</b> ({stake} USDC)\n"
            f"🎯 To Win: <b>{fmt_currency(win, cur)}</b> ({win} USDC)\n"
            f"📈 Profit: <b>{fmt_currency(profit, cur)}</b>\n\n"
            f"⚠️ Settles within 1hr of full time.",
            parse_mode=ParseMode.HTML,
            reply_markup=Markup([
                [Btn(f"✅ PLACE BET — {fmt_currency(stake, cur)}", callback_data=f"confirm_{stake}")],
                [Btn("❌ Cancel", callback_data="main_menu")]
            ])
        )
        return
    
    # Export key PIN
    if ctx.user_data.get("awaiting_export"):
        ctx.user_data.pop("awaiting_export")
        pin = text
        try:
            await update.message.delete()
        except:
            pass
        
        user = await db.get_user(update.effective_user.id)
        if not user:
            await update.effective_chat.send_message("❌ No wallet. Use /start")
            return
        
        if db.hash_pin(pin, update.effective_user.id) != user["pin_hash"]:
            await update.effective_chat.send_message("❌ Wrong PIN.", reply_markup=kb_wallet())
            return
        
        key = wallet.export_key(update.effective_user.id, pin)
        msg = await update.effective_chat.send_message(
            f"🔑 <b>Private Key</b>\n\n"
            f"<code>{key}</code>\n\n"
            f"Import into MetaMask:\nSettings → Import Account → Paste\n\n"
            f"⚠️ Deletes in 60 seconds.",
            parse_mode=ParseMode.HTML
        )
        ctx.job_queue.run_once(_delete_msg, 60, data={"chat_id": msg.chat_id, "msg_id": msg.message_id})
        return
    
    # Bet confirmation PIN
    if ctx.user_data.get("awaiting_bet_pin"):
        ctx.user_data.pop("awaiting_bet_pin")
        pin = text
        try:
            await update.message.delete()
        except:
            pass
        
        user = await db.get_user(update.effective_user.id)
        if not user:
            await update.effective_chat.send_message("❌ No wallet. Use /start")
            return
        
        if db.hash_pin(pin, update.effective_user.id) != user["pin_hash"]:
            await update.effective_chat.send_message("❌ Wrong PIN.", reply_markup=kb_main())
            ctx.user_data.pop("pending_confirm_stake", None)
            ctx.user_data.pop("bet", None)
            return
        
        # PIN correct — cache it briefly and re-trigger confirm
        ctx.user_data["pin_cache"] = pin
        stake = ctx.user_data.pop("pending_confirm_stake", 0)
        bet = ctx.user_data.get("bet")
        
        if bet and stake > 0:
            # Create a fake callback query-like call
            cur = user.get("currency", "USD")
            msg = await update.effective_chat.send_message("⏳ Placing bet...")
            
            # Execute trade directly
            try:
                w = wallet.derive_wallet(update.effective_user.id, pin)
                safe_addr = user.get("safe_address", "")
                
                # Auto-onboard if needed
                if not user.get("onboarded") and not safe_addr:
                    try:
                        safe_addr = wallet.get_safe_address(w["private_key"])
                        await db.set_safe_address(update.effective_user.id, safe_addr)
                    except:
                        pass
                
                if not user.get("onboarded"):
                    await msg.edit_text("⚙️ One-time wallet setup (~30s)...")
                    try:
                        ob = wallet.onboard_wallet(w["private_key"])
                        if ob["success"]:
                            safe_addr = ob["safe_address"]
                            await db.update_safe_address(update.effective_user.id, safe_addr)
                        else:
                            await msg.edit_text(
                                f"❌ Wallet setup failed. Send ~0.1 POL to:\n<code>{w['address']}</code>",
                                parse_mode=ParseMode.HTML, reply_markup=kb_main())
                            return
                    except Exception as obe:
                        await msg.edit_text(
                            f"❌ Wallet setup failed. Send ~0.1 POL to:\n<code>{w['address']}</code>",
                            parse_mode=ParseMode.HTML, reply_markup=kb_main())
                        return
                    await msg.edit_text("⏳ Placing bet...")
                
                result = await trade.place_bet(
                    private_key=w["private_key"],
                    token_id=bet.get("token_id", ""),
                    price=bet.get("price", 0),
                    amount_usdc=stake,
                    match_name=bet["match"],
                    selection=bet["label"],
                    safe_address=safe_addr,
                )
                
                if result["success"]:
                    fee_info = trade.calculate_fee(stake)
                    bet_id = await db.log_bet(
                        telegram_id=update.effective_user.id,
                        match_name=bet["match"],
                        selection=bet["label"],
                        amount_gross=stake,
                        amount_net=fee_info["net"],
                        fee=fee_info["fee"],
                        price=bet.get("price", 0),
                        odds=bet["odds"],
                        shares=result["shares"],
                        token_id=bet.get("token_id", ""),
                        order_id=result["order_id"],
                    )
                    await db.process_referral_fee(update.effective_user.id, bet_id, fee_info["fee"])
                    
                    win = round(result["shares"] * 1.0, 2)
                    await msg.edit_text(
                        f"✅ <b>BET PLACED!</b>\n\n"
                        f"📋 {bet['match']}\n"
                        f"✅ {bet['label']} @ {bet['odds']:.2f}\n\n"
                        f"💰 Stake: {fmt_currency(stake, cur)}\n"
                        f"💸 Fee (1%): {fmt_currency(fee_info['fee'], cur)}\n"
                        f"🎯 Max Payout: {fmt_currency(win, cur)}\n\n"
                        f"🆔 Order: <code>{result['order_id'][:16]}...</code>",
                        parse_mode=ParseMode.HTML, reply_markup=kb_main()
                    )
                else:
                    await msg.edit_text(
                        f"❌ <b>Bet Failed</b>\n\n{result['error']}",
                        parse_mode=ParseMode.HTML, reply_markup=kb_main()
                    )
            except Exception as e:
                logger.error(f"Bet PIN flow error: {e}")
                await msg.edit_text("❌ Error placing bet.", reply_markup=kb_main())
            
            ctx.user_data.pop("bet", None)
            ctx.user_data.pop("pin_cache", None)
        return


# ========================================
# Main
# ========================================

def main():
    if not BOT_TOKEN:
        logger.error("TELEGRAM_BOT_TOKEN not set!")
        sys.exit(1)
    
    app = Application.builder().token(BOT_TOKEN).build()
    
    # Set bot commands menu on startup
    async def post_init(application):
        await application.bot.set_my_commands([
            ("start", "🏠 Main menu"),
            ("today", "📅 Today's matches across all sports"),
            ("live", "🔴 Live matches now"),
            ("football", "⚽ Football leagues"),
            ("basketball", "🏀 Basketball leagues"),
            ("wallet", "👛 Wallet & balance"),
            ("bets", "💰 My active bets"),
            ("settings", "⚙️ Change currency & preferences"),
            ("help", "❓ How to use BetPoly"),
        ])
        await db.init_db()
        # Fetch live currency rates on startup
        await refresh_currency_rates()
        logger.info("🚀 BetPoly is running!")
    
    app.post_init = post_init
    
    # PIN setup + currency selection conversation
    app.add_handler(ConversationHandler(
        entry_points=[CommandHandler("start", cmd_start)],
        states={
            PIN_SET: [MessageHandler(filters.TEXT & ~filters.COMMAND, pin_set)],
            PIN_CONFIRM: [MessageHandler(filters.TEXT & ~filters.COMMAND, pin_confirm)],
            CURRENCY_SELECT: [CallbackQueryHandler(onboard_currency)],
        },
        fallbacks=[CommandHandler("start", cmd_start)],
    ))
    
    # Quick command shortcuts
    app.add_handler(CommandHandler("today", cmd_today))
    app.add_handler(CommandHandler("live", cmd_live))
    app.add_handler(CommandHandler("football", cmd_football))
    app.add_handler(CommandHandler("basketball", cmd_basketball))
    app.add_handler(CommandHandler("wallet", cmd_wallet))
    app.add_handler(CommandHandler("bets", cmd_bets))
    app.add_handler(CommandHandler("settings", cmd_settings))
    app.add_handler(CommandHandler("help", cmd_help))
    
    # All button clicks
    app.add_handler(CallbackQueryHandler(on_button))
    
    # Text input (custom stakes, export PIN)
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text))
    
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
