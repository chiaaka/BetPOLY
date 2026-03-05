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
    refresh_currency_rates, get_mid_price
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
    
    # Back button routes to correct source
    if league == "live":
        back_cb = "live"
    elif league == "today":
        back_cb = "today"
    else:
        back_cb = f"league_{league}"
    
    rows.append([
        Btn("🔄 Refresh", callback_data=f"refresh_{league}_{idx}"),
        Btn("🔙 Back", callback_data=back_cb)
    ])
    return Markup(rows)


# ========================================

# ── Chat type guards ──
def _is_group(update: Update) -> bool:
    return update.effective_chat.type in ("group", "supergroup")

def _is_private(update: Update) -> bool:
    return update.effective_chat.type == "private"

async def _dm_only(update: Update) -> bool:
    """Redirect to DM if command used in group."""
    if _is_group(update):
        bot_me = await update.get_bot().get_me()
        await update.message.reply_text(
            f"This command only works in DM.\n"
            f"👉 @{bot_me.username}",
        )
        return False
    return True

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
    
    await db.create_user(user.id, user.username or "", w["address"], db.hash_pin(pin, user.id),
                         referred_by=_extract_referrer(ctx))
    
    # === ONBOARD NOW (gasless via Builder Relayer) ===
    # Deploy Safe + set approvals + derive API creds — all during signup
    await update.effective_chat.send_message(
        "⚙️ <b>Setting up your wallet...</b>\n\n"
        "Deploying your secure Polymarket wallet.\n"
        "This is gasless and takes ~15 seconds.",
        parse_mode=ParseMode.HTML
    )
    
    safe_addr = ""
    onboard_ok = False
    try:
        ob = wallet.onboard_wallet(w["private_key"])
        if ob["success"]:
            safe_addr = ob["safe_address"]
            await db.update_safe_address(user.id, safe_addr)
            onboard_ok = True
            logger.info(f"Onboarded user {user.id}: Safe={safe_addr}")
        else:
            logger.error(f"Onboarding failed for {user.id}: {ob.get('error')}")
            # Do NOT store safe_address — user is not onboarded
            await update.effective_chat.send_message(
                f"⚠️ <b>Wallet setup incomplete</b>\n\n"
                f"Error: {ob.get('error', 'Unknown')[:200]}\n\n"
                f"Don't worry — your account is created.\n"
                f"Use /retry to try wallet setup again.",
                parse_mode=ParseMode.HTML
            )
    except Exception as obe:
        logger.error(f"Onboarding exception for {user.id}: {obe}")
        await update.effective_chat.send_message(
            f"⚠️ <b>Wallet setup error</b>\n\n"
            f"Error: {str(obe)[:200]}\n\n"
            f"Your account is created. Use /retry to try again.",
            parse_mode=ParseMode.HTML
        )
    
    # The deposit address is the Safe (only if onboarding succeeded)
    deposit_addr = safe_addr if onboard_ok else w["address"]
    
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
            f"💡 Withdraw USDC on Polygon from any exchange\n\n"
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


async def _show_today_content(msg, cur: str, ctx, is_edit=True, page: int = 0):
    """Fetch today's games from top leagues and display grouped by sport."""
    import asyncio
    from datetime import datetime as dt, timezone, timedelta
    
    MATCHES_PER_PAGE = 25
    
    # Check if we already have cached today games (for pagination)
    all_games = ctx.user_data.get("g_today")
    
    if all_games is None:
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
        
        # Sort games by sport order then time
        sport_order = ["soccer", "basketball", "tennis", "hockey", "american_football", "mma", "cricket", "esports"]
        all_games.sort(key=lambda g: (
            sport_order.index(g["_sport"]) if g["_sport"] in sport_order else 99,
            g.get("time", "")
        ))
        
        ctx.user_data["g_today"] = all_games
    
    from datetime import datetime as dt, timezone, timedelta
    wat = timezone(timedelta(hours=1))
    now = dt.now(wat)
    
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
    
    total = len(all_games)
    total_pages = max(1, (total + MATCHES_PER_PAGE - 1) // MATCHES_PER_PAGE)
    page = max(0, min(page, total_pages - 1))
    start = page * MATCHES_PER_PAGE
    end = min(start + MATCHES_PER_PAGE, total)
    page_games = all_games[start:end]
    
    sport_names = {
        "soccer": "⚽ FOOTBALL", "basketball": "🏀 BASKETBALL",
        "tennis": "🎾 TENNIS", "hockey": "🏒 HOCKEY",
        "american_football": "🏈 AMERICAN FOOTBALL", "mma": "🥊 MMA",
        "cricket": "🏏 CRICKET", "esports": "🎮 ESPORTS",
    }
    
    page_label = f" (Page {page + 1}/{total_pages})" if total_pages > 1 else ""
    text = f"📅 <b>TODAY — {now.strftime('%A, %d %B')}</b>{page_label}\n"
    text += "━━━━━━━━━━━━━━━━━━━━━━\n\n"
    
    current_sport = ""
    current_league = ""
    rows = []
    
    for i, g in enumerate(page_games):
        global_idx = start + i
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
        
        # Button
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
        rows.append([Btn(btn, callback_data=f"game_today_{global_idx}")])
    
    # Pagination buttons
    nav_row = []
    if page > 0:
        nav_row.append(Btn("◀ Prev", callback_data=f"pgT_{page - 1}"))
    if page < total_pages - 1:
        nav_row.append(Btn("Next ▶", callback_data=f"pgT_{page + 1}"))
    if nav_row:
        rows.append(nav_row)
    
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
    if not await _dm_only(update): return
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
    if not await _dm_only(update): return
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
    if not await _dm_only(update): return
    user = await db.get_user(update.effective_user.id)
    if not user:
        await update.message.reply_text("Use /start first to create your wallet.")
        return
    await update.message.reply_text("⚙️ <b>Settings</b>", parse_mode=ParseMode.HTML, reply_markup=kb_settings())


ADMIN_TELEGRAM_ID = 7193855492

async def cmd_admin(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Admin dashboard — locked to owner."""
    if update.effective_user.id != ADMIN_TELEGRAM_ID:
        await update.message.reply_text("⛔ Unauthorized.")
        return
    
    import time
    
    # Total users
    async with aiosqlite.connect(db._DB) as conn:
        row = await conn.execute("SELECT COUNT(*) FROM users")
        total_users = (await row.fetchone())[0]
        
        # Users this week
        week_ago = time.time() - 7 * 86400
        row = await conn.execute("SELECT COUNT(*) FROM users WHERE created_at > ?", (week_ago,))
        users_week = (await row.fetchone())[0]
        
        # Users today
        day_ago = time.time() - 86400
        row = await conn.execute("SELECT COUNT(*) FROM users WHERE created_at > ?", (day_ago,))
        users_today = (await row.fetchone())[0]
        
        # Get all users for stats
        rows = await conn.execute_fetchall(
            "SELECT telegram_id, safe_address, created_at FROM users WHERE safe_address IS NOT NULL AND safe_address != ''"
        )
    
    await update.message.reply_text("🔄 <b>Loading admin dashboard...</b>", parse_mode=ParseMode.HTML)
    
    # Fetch per-user stats from Polymarket API
    total_volume = 0
    total_profit = 0
    user_stats = []
    
    for r in rows:
        tg_id, addr, created = r[0], r[1], r[2]
        try:
            stats = await trade.get_user_stats_from_api(addr)
            vol = stats.get("volume", 0)
            profit = stats.get("profit", 0)
            wins = stats.get("wins", 0)
            losses = stats.get("losses", 0)
            open_pos = stats.get("open_positions", 0)
            total_volume += vol
            total_profit += profit
            user_stats.append({
                "tg_id": tg_id,
                "addr": addr,
                "volume": vol,
                "profit": profit,
                "wins": wins,
                "losses": losses,
                "open": open_pos,
                "created": created,
            })
        except:
            user_stats.append({
                "tg_id": tg_id,
                "addr": addr,
                "volume": 0,
                "profit": 0,
                "wins": 0,
                "losses": 0,
                "open": 0,
                "created": created,
            })
    
    # Sort by volume descending
    user_stats.sort(key=lambda x: x["volume"], reverse=True)
    
    # Build leaderboard (top 20)
    lb_lines = []
    for i, u in enumerate(user_stats[:20], 1):
        short_addr = u["addr"][:6] + "..." + u["addr"][-4:]
        pnl_emoji = "📈" if u["profit"] >= 0 else "📉"
        lb_lines.append(
            f"{i}. <code>{short_addr}</code> | "
            f"Vol: ${u['volume']:.2f} | "
            f"{pnl_emoji} ${u['profit']:.2f} | "
            f"W{u['wins']}/L{u['losses']} | "
            f"Open: {u['open']}"
        )
    
    leaderboard = "\n".join(lb_lines) if lb_lines else "No active users yet."
    
    # Active users (placed at least 1 bet)
    active_users = sum(1 for u in user_stats if u["volume"] > 0)
    
    text = (
        "🔒 <b>BetPoly Admin Dashboard</b>\n\n"
        
        "━━ <b>USERS</b> ━━\n"
        f"👥 Total: <b>{total_users}</b>\n"
        f"✅ Active (traded): <b>{active_users}</b>\n"
        f"📅 This Week: <b>{users_week}</b>\n"
        f"🕐 Today: <b>{users_today}</b>\n\n"
        
        "━━ <b>PLATFORM</b> ━━\n"
        f"💰 Total Volume: <b>${total_volume:.2f}</b>\n"
        f"📊 Net User P&L: <b>${total_profit:.2f}</b>\n\n"
        
        "━━ <b>LEADERBOARD (by volume)</b> ━━\n"
        f"{leaderboard}\n\n"
        
        "💡 <i>Use /users for full address export</i>"
    )
    
    await update.message.reply_text(text, parse_mode=ParseMode.HTML)


async def cmd_users_export(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Export all user addresses — admin only."""
    if update.effective_user.id != ADMIN_TELEGRAM_ID:
        await update.message.reply_text("⛔ Unauthorized.")
        return
    
    async with aiosqlite.connect(db._DB) as conn:
        rows = await conn.execute_fetchall(
            "SELECT telegram_id, safe_address, created_at FROM users WHERE safe_address IS NOT NULL AND safe_address != '' ORDER BY created_at"
        )
    
    if not rows:
        await update.message.reply_text("No users with wallets.")
        return
    
    import time
    lines = ["telegram_id,safe_address,created_at"]
    addr_list = []
    for r in rows:
        ts = time.strftime("%Y-%m-%d", time.gmtime(r[2])) if r[2] else "?"
        lines.append(f"{r[0]},{r[1]},{ts}")
        addr_list.append(r[1])
    
    csv_text = "\n".join(lines)
    
    # Also plain address list for easy copy/paste into airdrop tools
    addr_text = "\n".join(addr_list)
    
    text = (
        f"📋 <b>User Export ({len(rows)} wallets)</b>\n\n"
        f"<b>Addresses (copy for airdrop):</b>\n"
        f"<code>{addr_text}</code>\n\n"
        f"<b>Full CSV:</b>\n"
        f"<code>{csv_text}</code>"
    )
    
    # Telegram has a 4096 char limit, split if needed
    if len(text) > 4000:
        await update.message.reply_text(
            f"📋 <b>User Export ({len(rows)} wallets)</b>\n\n"
            f"<b>Addresses:</b>\n<code>{addr_text}</code>",
            parse_mode=ParseMode.HTML,
        )
        await update.message.reply_text(
            f"<b>Full CSV:</b>\n<code>{csv_text}</code>",
            parse_mode=ParseMode.HTML,
        )
    else:
        await update.message.reply_text(text, parse_mode=ParseMode.HTML)


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
        "Buy USDC on any exchange\n"
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


async def cmd_admin_fees(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Admin only: Check fee stats and admin wallet balance."""
    from config import ADMIN_TELEGRAM_ID, ADMIN_WALLET
    
    if ADMIN_TELEGRAM_ID and update.effective_user.id != ADMIN_TELEGRAM_ID:
        await update.message.reply_text("⛔ Admin only command.")
        return
    
    if not ADMIN_WALLET:
        await update.message.reply_text("⚠️ ADMIN_WALLET not configured in environment variables.")
        return
    
    # Get admin wallet USDC balance
    try:
        balance = trade.get_usdc_balance(address=ADMIN_WALLET)
    except:
        balance = 0
    
    # Get total fees from DB
    total_fees = await db.get_total_fees()
    
    await update.message.reply_text(
        f"💰 <b>Fee Dashboard</b>\n\n"
        f"👛 Admin Wallet: <code>{ADMIN_WALLET}</code>\n"
        f"💵 Wallet Balance: ${balance:.2f} USDC\n\n"
        f"📊 Total Fees Earned: ${total_fees:.2f}\n"
        f"📈 Fee Rate: {int(trade.PLATFORM_FEE_RATE * 100)}%\n\n"
        f"Fees are auto-collected after each bet.",
        parse_mode=ParseMode.HTML,
    )


async def cmd_reset(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Reset user account."""
    """Delete user account completely so they can /start fresh."""
    user = update.effective_user
    existing = await db.get_user(user.id)
    if not existing:
        await update.message.reply_text("No account found. Use /start to create one.")
        return
    
    await db.delete_user(user.id)
    logger.info(f"User {user.id} reset their account")
    await update.message.reply_text(
        "🗑️ <b>Account deleted!</b>\n\n"
        "All data has been removed.\n"
        "Use /start to create a fresh account.",
        parse_mode=ParseMode.HTML
    )


async def cmd_retry(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Retry wallet onboarding for users whose setup failed."""
    user = update.effective_user
    existing = await db.get_user(user.id)
    if not existing:
        await update.message.reply_text("No account found. Use /start to create one.")
        return
    
    if existing.get("onboarded"):
        safe_addr = existing.get("safe_address", "")
        await update.message.reply_text(
            f"✅ Your wallet is already set up!\n\n"
            f"Safe address:\n<code>{safe_addr}</code>",
            parse_mode=ParseMode.HTML, reply_markup=kb_main()
        )
        return
    
    ctx.user_data["awaiting_retry_pin"] = True
    await update.message.reply_text(
        "🔐 Enter your 6-digit PIN to retry wallet setup:",
        parse_mode=ParseMode.HTML
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
    for flag in ["awaiting_stake", "awaiting_bet_pin", "awaiting_export", "awaiting_retry_pin",
                 "awaiting_sell_pin", "awaiting_redeem_pin",
                 "awaiting_withdraw_amount", "awaiting_withdraw_address", "awaiting_withdraw_pin"]:
        ctx.user_data.pop(flag, None)
    
    user = await db.get_user(q.from_user.id)
    cur = user.get("currency", "USD") if user else "USD"
    
    # Refresh currency rates if stale (non-blocking, cached 4hrs)
    await refresh_currency_rates()
    
    # --- Group Betting Callbacks ---
    if d.startswith("grp_match_"):
        idx = int(d.replace("grp_match_", ""))
        matches = ctx.bot_data.get(f"grp_matches_{q.message.chat_id}", [])
        if idx < len(matches):
            await _post_group_card(q.message, matches[idx], cur, ctx)
        return
    
    if d.startswith("grpbet_"):
        await _handle_group_bet(update, ctx)
        return
    
    if d == "grp_refresh":
        match = ctx.bot_data.get(f"grp_match_{q.message.chat_id}")
        if match:
            await _post_group_card(q.message, match, cur, ctx)
        else:
            await q.answer("Match data expired. Use /bet again.", show_alert=True)
        return
    
    # --- Navigation ---
    if d == "main_menu":
        await q.edit_message_text("⚽ <b>BetPoly</b> — Powered by Polymarket\n\nPick a sport 👇",
                                  parse_mode=ParseMode.HTML, reply_markup=kb_main())
    
    elif d == "today":
        ctx.user_data["g_today"] = None  # Force fresh fetch
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
        ctx.user_data[f"g_{league}"] = None  # Force fresh fetch
        await _show_league(q, league, cur, ctx)
    
    # --- Pagination: League ---
    elif d.startswith("pgL_"):
        parts = d.split("_")
        league = parts[1]
        page = int(parts[2])
        await _show_league(q, league, cur, ctx, page=page)
    
    # --- Pagination: Today ---
    elif d.startswith("pgT_"):
        page = int(d.split("_")[1])
        await _show_today_content(q.message, cur, ctx, is_edit=True, page=page)
    
    # --- Pagination: Live ---
    elif d.startswith("pgLive_"):
        page = int(d.split("_")[1])
        await _show_live(q, cur, ctx, page=page)
    
    # --- Refresh odds on confirmation screen ---
    elif d.startswith("refresh_confirm_"):
        stake = float(d.replace("refresh_confirm_", ""))
        bet = ctx.user_data.get("bet")
        if not bet or not bet.get("token_id"):
            await q.answer("⚠️ Bet data expired")
            return
        try:
            new_price = await get_mid_price(bet["token_id"])
            if new_price and 0.03 < new_price < 0.97:
                old_odds = bet["odds"]
                new_odds = round(1.0 / new_price, 2)
                bet["price"] = new_price
                bet["odds"] = new_odds
                ctx.user_data["bet"] = bet
                await _show_confirm(q, stake, bet, cur)
                if abs(new_odds - old_odds) > 0.01:
                    await q.answer(f"🔄 Odds updated: {old_odds:.2f} → {new_odds:.2f}")
                else:
                    await q.answer("🔄 Odds unchanged")
            else:
                await _show_confirm(q, stake, bet, cur)
                await q.answer("🔄 Odds unchanged")
        except Exception as e:
            if "not modified" in str(e):
                await q.answer("🔄 Odds unchanged")
            else:
                logger.error(f"Refresh confirm error: {e}")
                await q.answer("⚠️ Refresh failed")
    
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
        ctx.user_data["g_live"] = None  # Force fresh fetch
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
    
    # --- Confirm sell (must come before generic confirm_) ---
    elif d.startswith("confirm_sell_"):
        if d == "confirm_sell_onchain":
            ctx.user_data["awaiting_sell_pin"] = True
            ctx.user_data["sell_is_onchain"] = True
            await q.edit_message_text(
                "🔐 Enter your 6-digit PIN to confirm sell:",
                parse_mode=ParseMode.HTML,
                reply_markup=Markup([[Btn("❌ Cancel", callback_data="my_bets")]])
            )
        else:
            bet_id = int(d.replace("confirm_sell_", ""))
            ctx.user_data["pending_sell_bet_id"] = bet_id
            ctx.user_data["awaiting_sell_pin"] = True
            await q.edit_message_text(
                "🔐 Enter your 6-digit PIN to confirm sell:",
                parse_mode=ParseMode.HTML,
                reply_markup=Markup([[Btn("❌ Cancel", callback_data="my_bets")]])
            )
    
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
                    bal_text += f"  ⚠️ USDC: <b>${usdc_native:.2f}</b> <i>(needs conversion)</i>\n"
                    bal_text += f"\n⚠️ <b>You have native USDC.</b>\n"
                    bal_text += f"BetPoly uses USDC.e. Tap Convert below to swap (gasless)."
                
                bal_text += f"\n⛽ POL (gas): {matic_bal:.4f}"
                
                # Check onboarding status
                is_onboarded = bool(user.get("onboarded"))
                
                if total == 0:
                    bal_text += "\n\n💡 <i>No funds yet. Tap 📥 Deposit to fund.</i>"
                
                if not is_onboarded and total > 0:
                    bal_text += "\n\n⚙️ <i>Wallet setup pending. Place a bet to auto-setup (gasless).</i>"
                    
            except Exception as e:
                logger.error(f"Balance check error: {e}")
                bal_text = "💰 <i>Balance check failed. Is POLYGON_RPC_URL set?</i>"
                usdc_native = 0
            
            # Build buttons
            btns = [[Btn("🔄 Refresh", callback_data="w_balance")]]
            if usdc_native > 0:
                btns.append([Btn(f"🔄 Convert ${usdc_native:.2f} USDC → USDC.e", callback_data="w_convert")])
            btns.append([Btn("📥 Deposit", callback_data="w_deposit")])
            btns.append([Btn("🔙 Back", callback_data="wallet_menu")])
            
            await q.edit_message_text(
                f"👛 <b>Your Wallet</b>\n\n"
                f"📬 <code>{addr}</code>\n\n"
                f"{bal_text}",
                parse_mode=ParseMode.HTML,
                disable_web_page_preview=True,
                reply_markup=Markup(btns)
            )
    
    elif d == "w_convert":
        if user:
            addr = user.get("safe_address") or user.get("wallet_address", "")
            try:
                bals = trade.get_usdc_balances(address=addr)
                usdc_native = bals["usdc_native"]
            except:
                usdc_native = 0
            
            if usdc_native < 0.01:
                await q.answer("No native USDC to convert.")
                return
            
            ctx.user_data["pending_convert_amount"] = usdc_native
            ctx.user_data["awaiting_convert_pin"] = True
            await q.edit_message_text(
                f"🔄 <b>Convert USDC → USDC.e</b>\n\n"
                f"💵 Amount: <b>${usdc_native:.2f}</b>\n"
                f"📊 Rate: 1:1 (stablecoin swap)\n"
                f"⛽ Gas: <b>FREE</b> (gasless via relayer)\n"
                f"🔒 Slippage: &lt; 0.5%\n\n"
                f"🔐 Enter your 6-digit PIN to convert:",
                parse_mode=ParseMode.HTML,
                reply_markup=Markup([[Btn("❌ Cancel", callback_data="w_balance")]])
            )
    
    elif d == "w_deposit":
        if user:
            deposit_addr = user.get("safe_address") or user.get("wallet_address", "")
            
            await q.edit_message_text(
                f"📥 <b>Deposit</b>\n\n"
                f"Choose how to fund your account:\n",
                parse_mode=ParseMode.HTML,
                reply_markup=Markup([
                    [Btn("💳 Buy with Card (MoonPay)", callback_data="deposit_moonpay")],
                    [Btn("🔗 Send Crypto (Any Chain)", callback_data="deposit_bridge")],
                    [Btn("💰 Send USDC.e (Polygon)", callback_data="deposit_crypto")],
                    [Btn("🔙 Back", callback_data="wallet_menu")],
                ])
            )
    
    elif d == "deposit_moonpay":
        if user:
            deposit_addr = user.get("safe_address") or user.get("wallet_address", "")
            
            moonpay_url = (
                f"https://www.moonpay.com/buy/usdc"
                f"?walletAddress={deposit_addr}"
                f"&currencyCode=usdc_polygon"
            )
            
            await q.edit_message_text(
                "💳 <b>Buy USDC with MoonPay</b>\n\n"
                
                "<b>Step 1:</b> Tap the button below\n"
                "<b>Step 2:</b> Select your currency (USD, EUR, GBP, etc)\n"
                "<b>Step 3:</b> Choose payment — card, Apple Pay, "
                "bank transfer, Google Pay\n"
                "<b>Step 4:</b> First time? Quick ID check (~2 min)\n"
                "<b>Step 5:</b> Confirm purchase\n"
                "<b>Step 6:</b> USDC arrives in ~5 min, ready to bet!\n\n"
                
                "━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
                
                "⚠️ <b>Not available in your region?</b>\n"
                "Use \"Send Crypto (Any Chain)\" instead — \n"
                "buy USDC on Binance/Bybit, send on any\n"
                "cheap chain, we auto-convert it.\n\n"
                
                "📬 Your wallet is pre-filled in the link.",
                parse_mode=ParseMode.HTML,
                reply_markup=Markup([
                    [Btn("🌙 Buy on MoonPay →", url=moonpay_url)],
                    [Btn("🔗 Send Crypto (Any Chain)", callback_data="deposit_bridge")],
                    [Btn("🔙 Back", callback_data="w_deposit")],
                ])
            )
    
    elif d == "deposit_bridge":
        if user:
            deposit_addr = user.get("safe_address") or user.get("wallet_address", "")
            
            await q.edit_message_text(
                "⏳ Getting your deposit addresses...",
                parse_mode=ParseMode.HTML,
            )
            
            from polymarket_api import get_bridge_deposit_addresses
            bridge = await get_bridge_deposit_addresses(deposit_addr)
            
            if bridge and bridge.get("evm"):
                evm_addr = bridge["evm"]
                sol_addr = bridge.get("svm", "")
                btc_addr = bridge.get("btc", "")
                
                sol_section = ""
                if sol_addr:
                    sol_section = (
                        f"\n\n🟣 <b>Solana:</b>\n"
                        f"<code>{sol_addr}</code>\n"
                        f"   SOL, USDC, USDT ($2 min)"
                    )
                
                btc_section = ""
                if btc_addr:
                    btc_section = (
                        f"\n\n🟠 <b>Bitcoin:</b>\n"
                        f"<code>{btc_addr}</code>\n"
                        f"   BTC ($9 min)"
                    )
                
                await q.edit_message_text(
                    "🔗 <b>Send Crypto — Any Chain</b>\n\n"
                    
                    "Send to the address for your chain.\n"
                    "Auto-converts to USDC.e for betting!\n\n"
                    
                    "━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
                    
                    f"🔵 <b>EVM Chains</b> (one address for all):\n"
                    f"<code>{evm_addr}</code>\n\n"
                    
                    f"   <b>BSC</b> — BNB, USDC, USDT, BUSD ($2 min)\n"
                    f"   <b>Base</b> — ETH, USDC, USDT ($2 min)\n"
                    f"   <b>Arbitrum</b> — ETH, USDC, USDT, ARB ($2 min)\n"
                    f"   <b>Optimism</b> — ETH, USDC, OP ($2 min)\n"
                    f"   <b>Polygon</b> — USDC, POL, USDT ($2 min)\n"
                    f"   <b>Ethereum</b> — ETH, USDC, USDT ($7 min)"
                    
                    f"{sol_section}"
                    f"{btc_section}\n\n"
                    
                    "━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
                    
                    "⚡ <b>Cheapest options:</b>\n"
                    "BSC, Base, Arbitrum — gas under $0.01\n\n"
                    
                    "🇳🇬 <b>From Binance?</b>\n"
                    "Withdraw USDC → BSC (BEP20) → paste\n"
                    "the EVM address above. Done in ~2 min.\n\n"
                    
                    "⏱ Arrives in 2-10 min, auto-converted.",
                    parse_mode=ParseMode.HTML,
                    reply_markup=Markup([
                        [Btn("💳 Buy with Card instead", callback_data="deposit_moonpay")],
                        [Btn("🔙 Back", callback_data="w_deposit")],
                    ])
                )
            else:
                await q.edit_message_text(
                    "🔗 <b>Send Crypto</b>\n\n"
                    
                    "⚠️ Couldn't fetch multi-chain addresses.\n"
                    "You can still send USDC.e directly on Polygon:\n\n"
                    
                    f"<code>{deposit_addr}</code>\n\n"
                    
                    "Or try again in a moment.",
                    parse_mode=ParseMode.HTML,
                    reply_markup=Markup([
                        [Btn("🔄 Try Again", callback_data="deposit_bridge")],
                        [Btn("💰 Send USDC.e on Polygon", callback_data="deposit_crypto")],
                        [Btn("🔙 Back", callback_data="w_deposit")],
                    ])
                )
    
    elif d == "deposit_crypto":
        if user:
            deposit_addr = user.get("safe_address") or user.get("wallet_address", "")
            
            await q.edit_message_text(
                f"💰 <b>Send USDC.e on Polygon</b>\n\n"
                f"<code>{deposit_addr}</code>\n\n"
                f"━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
                f"✅ <b>USDC.e on Polygon</b> — instant, ready to bet\n\n"
                f"⚠️ <b>USDC (native) will NOT work here.</b>\n"
                f"Use \"Send Crypto (Any Chain)\" instead —\n"
                f"it auto-converts any token to USDC.e!\n\n"
                f"⚠️ <b>Only Polygon network!</b>\n"
                f"Sending on wrong chain = lost funds.\n\n"
                f"💡 Have crypto on another chain?\n"
                f"Use \"Send Crypto (Any Chain)\" — it auto-converts!\n\n"
                f"💰 Min bet: $1",
                parse_mode=ParseMode.HTML,
                reply_markup=Markup([
                    [Btn("🔗 Send from Any Chain", callback_data="deposit_bridge")],
                    [Btn("💳 Buy with Card", callback_data="deposit_moonpay")],
                    [Btn("🔙 Back", callback_data="w_deposit")],
                ])
            )
    
    elif d == "w_withdraw":
        if user:
            addr = user.get("safe_address") or user.get("wallet_address", "")
            try:
                bals = trade.get_usdc_balances(address=addr)
                balance = bals["usdc_e"]
            except:
                balance = 0
            
            if balance < 0.01:
                await q.edit_message_text(
                    "📤 <b>Withdraw</b>\n\n"
                    "❌ No USDC.e balance to withdraw.",
                    parse_mode=ParseMode.HTML, reply_markup=kb_wallet()
                )
                return
            
            ctx.user_data["withdraw_balance"] = balance
            await q.edit_message_text(
                f"📤 <b>Withdraw USDC</b>\n\n"
                f"💰 Available: <b>${balance:.2f}</b>\n\n"
                f"Enter amount to withdraw (or type <b>all</b>):",
                parse_mode=ParseMode.HTML,
                reply_markup=Markup([[Btn("❌ Cancel", callback_data="wallet_menu")]])
            )
            ctx.user_data["awaiting_withdraw_amount"] = True
    
    elif d == "w_export":
        await q.edit_message_text(
            "🔑 <b>Export Private Key</b>\n\n"
            "⚠️ Never share your key with anyone.\n\n"
            "Enter your 6-digit PIN:",
            parse_mode=ParseMode.HTML,
            reply_markup=Markup([[Btn("🔙 Cancel", callback_data="wallet_menu")]])
        )
        ctx.user_data["awaiting_export"] = True
    
    # --- Main Menu ---
    elif d == "main_menu":
        await q.edit_message_text(
            f"⚽ <b>BetPoly</b> — What are you betting on? 👇",
            parse_mode=ParseMode.HTML, reply_markup=kb_main()
        )
    
    # --- Sell On-chain Position ---
    elif d.startswith("sell_onchain_"):
        idx = int(d.replace("sell_onchain_", ""))
        
        # Re-fetch positions fresh (for refresh button)
        user = await db.get_user(q.from_user.id)
        if user:
            try:
                fresh_positions = await trade.get_positions_onchain(user["private_key"])
                if fresh_positions:
                    ctx.user_data["onchain_positions"] = fresh_positions
            except:
                pass
        
        positions = ctx.user_data.get("onchain_positions", [])
        if idx >= len(positions):
            await q.edit_message_text("❌ Position not found.", reply_markup=kb_main())
            return
        p = positions[idx]
        
        # Check if position is actually sellable
        if p.get("cur_price", 0) <= 0.01 and p.get("current_value", 0) < 0.01:
            await q.edit_message_text(
                "❌ <b>This market has resolved against you.</b>\n\n"
                f"📋 {p.get('title', '?')}\n"
                f"❌ {p.get('outcome', '?')}\n\n"
                "Your shares are worth $0. This position cannot be sold.",
                parse_mode=ParseMode.HTML,
                reply_markup=Markup([[Btn("🔙 Back", callback_data="my_bets")]]),
            )
            return
        
        if p.get("size", 0) < 0.01:
            await q.edit_message_text(
                "❌ No shares to sell.",
                reply_markup=Markup([[Btn("🔙 Back", callback_data="my_bets")]]),
            )
            return
        
        sell_value = round(p["size"] * p["cur_price"], 2)
        pnl = p.get("cash_pnl", 0)
        pnl_pct = p.get("percent_pnl", 0)
        max_win = round(p["size"] * 1.0, 2)
        
        loss_warning = ""
        if pnl < 0:
            loss_warning = (
                f"\n⚠️ <b>You will sell at a LOSS</b>\n"
                f"You bought at ${p['avg_price']:.2f}, current price is ${p['cur_price']:.2f}\n"
                f"Consider waiting — if your bet wins, you get ${max_win:.2f} (${1.00}/share)\n"
            )
        
        ctx.user_data["pending_sell_onchain"] = p
        await q.edit_message_text(
            f"📊 <b>Sell Position</b>\n\n"
            f"📋 {p.get('title', '?')}\n"
            f"✅ {p.get('outcome', '?')}\n\n"
            f"🔢 Shares: {p['size']:.2f}\n"
            f"💵 Avg Price: ${p['avg_price']:.2f}\n"
            f"📊 Current: ${p['cur_price']:.2f}\n\n"
            f"💰 Sell Value: <b>${sell_value:.2f}</b>\n"
            f"{'📈' if pnl >= 0 else '📉'} P&L: {'+' if pnl >= 0 else ''}{pnl_pct:.0f}%\n"
            f"🎯 Max Win (if held): ${max_win:.2f}"
            f"{loss_warning}\n\n"
            f"⚡ Sell at market price?\n"
            f"<i>Updated {__import__('datetime').datetime.now().strftime('%H:%M:%S')}</i>",
            parse_mode=ParseMode.HTML,
            reply_markup=Markup([
                [Btn(f"✅ Sell — ${sell_value:.2f}", callback_data="confirm_sell_onchain")],
                [Btn("🔄 Refresh Price", callback_data=f"sell_onchain_{idx}"),
                 Btn("❌ Cancel", callback_data="my_bets")],
            ])
        )
    
    # --- Redeem On-chain Position ---
    elif d.startswith("redeem_onchain_"):
        idx = int(d.replace("redeem_onchain_", ""))
        positions = ctx.user_data.get("onchain_positions", [])
        if idx >= len(positions):
            await q.edit_message_text("❌ Position not found.", reply_markup=kb_main())
            return
        p = positions[idx]
        
        payout = round(p["size"] * 1.0, 2)
        profit = round(payout - p.get("initial_value", 0), 2)
        
        ctx.user_data["pending_redeem_onchain"] = p
        ctx.user_data["awaiting_redeem_pin"] = True
        ctx.user_data["redeem_is_onchain"] = True
        await q.edit_message_text(
            f"🏆 <b>Redeem Winnings</b>\n\n"
            f"📋 {p.get('title', '?')}\n"
            f"✅ {p.get('outcome', '?')}\n\n"
            f"🔢 Shares: {p['size']:.2f} × $1.00\n"
            f"💰 Payout: <b>${payout:.2f}</b>\n"
            f"📈 Profit: <b>${profit:.2f}</b>\n\n"
            f"🔐 Enter your 6-digit PIN to redeem:",
            parse_mode=ParseMode.HTML,
            reply_markup=Markup([[Btn("❌ Cancel", callback_data="my_bets")]])
        )
    
    # --- Sell Position (from DB) ---
    elif d.startswith("sell_"):
        bet_id = int(d.replace("sell_", ""))
        bets = await db.get_user_bets(q.from_user.id)
        bet_row = next((b for b in bets if b["id"] == bet_id), None)
        if not bet_row:
            await q.edit_message_text("❌ Bet not found.", reply_markup=kb_main())
            return
        
        token_id = bet_row.get("token_id", "")
        shares = bet_row.get("shares", 0)
        if not token_id or not shares:
            await q.edit_message_text("❌ No position to sell.", reply_markup=kb_main())
            return
        
        # Get current market price to show sell value
        try:
            current_price = trade.get_current_price(token_id)
        except:
            current_price = bet_row.get("odds", 0.5)
        
        sell_value = round(shares * current_price, 2)
        original_stake = bet_row.get("amount_gross") or bet_row.get("amount_usdc", 0) or 0
        pnl = round(sell_value - original_stake, 2)
        pnl_pct = round((pnl / original_stake) * 100, 1) if original_stake > 0 else 0
        pnl_emoji = "📈" if pnl >= 0 else "📉"
        max_win = round(shares * 1.0, 2)
        buy_price = bet_row.get('price', 0)
        
        loss_warning = ""
        if pnl < 0:
            loss_warning = (
                f"\n⚠️ <b>You will sell at a LOSS</b>\n"
                f"You bought at ${buy_price:.2f}, current price is ${current_price:.2f}\n"
                f"Consider waiting — if your bet wins, you get {fmt_currency(max_win, cur)} ($1.00/share)\n"
            )
        
        ctx.user_data["pending_sell_bet_id"] = bet_id
        await q.edit_message_text(
            f"📊 <b>Sell Position</b>\n\n"
            f"📋 {bet_row.get('match_name', '?')}\n"
            f"✅ {bet_row.get('selection', '?')}\n\n"
            f"🔢 Shares: {shares:.2f}\n"
            f"💵 Avg Price: ${buy_price:.2f}\n"
            f"📊 Current Price: ${current_price:.2f}\n\n"
            f"💰 Sell Value: <b>{fmt_currency(sell_value, cur)}</b>\n"
            f"{pnl_emoji} P&L: {'+' if pnl >= 0 else ''}{fmt_currency(pnl, cur)} ({'+' if pnl_pct >= 0 else ''}{pnl_pct}%)\n"
            f"🎯 Max Winnings (if held): {fmt_currency(max_win, cur)}"
            f"{loss_warning}\n\n"
            f"⚡ Sell at current market price?\n"
            f"<i>Updated {__import__('datetime').datetime.now().strftime('%H:%M:%S')}</i>",
            parse_mode=ParseMode.HTML,
            reply_markup=Markup([
                [Btn(f"✅ Sell — {fmt_currency(sell_value, cur)}", callback_data=f"confirm_sell_{bet_id}")],
                [Btn("🔄 Refresh Price", callback_data=f"sell_{bet_id}"),
                 Btn("❌ Cancel", callback_data="my_bets")],
            ])
        )
    
    # --- Redeem Position (after market resolves) ---
    elif d.startswith("redeem_"):
        bet_id = int(d.replace("redeem_", ""))
        bets = await db.get_user_bets(q.from_user.id)
        bet_row = next((b for b in bets if b["id"] == bet_id), None)
        if not bet_row:
            await q.edit_message_text("❌ Bet not found.", reply_markup=kb_main())
            return
        
        status = bet_row.get("status", "")
        if status == "placed":
            await q.edit_message_text(
                "⏳ <b>Market not yet resolved</b>\n\n"
                "This market hasn't settled yet. You can:\n"
                "• <b>Sell</b> — sell at current market price\n"
                "• <b>Wait</b> — until the match ends and market resolves\n\n"
                "You'll get a notification when it's ready to redeem!",
                parse_mode=ParseMode.HTML,
                reply_markup=Markup([
                    [Btn("📊 Sell Instead", callback_data=f"sell_{bet_id}")],
                    [Btn("🔙 My Bets", callback_data="my_bets")],
                ])
            )
            return
        
        if status == "lost":
            await q.edit_message_text(
                "❌ <b>Position Lost</b>\n\n"
                "This market resolved against your position. Shares are worth $0.",
                parse_mode=ParseMode.HTML, reply_markup=kb_main()
            )
            return
        
        # Won or settled — proceed to redeem
        ctx.user_data["pending_redeem_bet_id"] = bet_id
        ctx.user_data["awaiting_redeem_pin"] = True
        shares = bet_row.get("shares", 0)
        payout = round(shares * 1.0, 2)
        original_stake = bet_row.get("amount_gross") or bet_row.get("amount_usdc", 0) or 0
        profit = round(payout - original_stake, 2)
        roi = round((profit / original_stake) * 100, 1) if original_stake > 0 else 0
        await q.edit_message_text(
            f"🏆 <b>Redeem Winnings</b>\n\n"
            f"📋 {bet_row.get('match_name', '?')}\n"
            f"✅ {bet_row.get('selection', '?')}\n\n"
            f"🔢 Shares: {shares:.2f} × $1.00\n"
            f"💰 Payout: <b>{fmt_currency(payout, cur)}</b>\n"
            f"💵 Original Stake: {fmt_currency(original_stake, cur)}\n"
            f"📈 Profit: <b>{fmt_currency(profit, cur)}</b> (+{roi}%)\n\n"
            f"🔐 Enter your 6-digit PIN to redeem:",
            parse_mode=ParseMode.HTML,
            reply_markup=Markup([[Btn("❌ Cancel", callback_data="my_bets")]])
        )
    
    # --- My Bets ---
    elif d == "my_bets":
        if user:
            safe_addr = user.get("safe_address", "")
            buttons = []
            
            # Get stats from Polymarket API (survives redeploys)
            stats = await trade.get_user_stats_from_api(safe_addr) if safe_addr else {"volume": 0, "wins": 0, "losses": 0, "profit": 0}
            
            text = "💰 <b>My Bets</b>\n\n"
            text += f"📊 Volume: ${stats['volume']:.2f}\n"
            text += f"🏆 Wins: {stats['wins']}  ❌ Losses: {stats['losses']}\n"
            if stats['profit'] >= 0:
                text += f"💵 Profit: +${stats['profit']:.2f}\n"
            else:
                text += f"💵 P&L: -${abs(stats['profit']):.2f}\n"
            text += "\n"
            
            # Fetch on-chain positions — only show active + redeemable
            if safe_addr:
                try:
                    onchain_positions = await trade.get_positions(
                        private_key="", safe_address=safe_addr
                    )
                    
                    # Store all for callback reference
                    ctx.user_data["onchain_positions"] = onchain_positions or []
                    
                    if onchain_positions:
                        # Filter using Polymarket Data API fields
                        MIN_SIZE = 0.05
                        active = [p for p in onchain_positions if p.get("status") == "active" and p.get("size", 0) >= MIN_SIZE]
                        # Only show redeem if Data API says redeemable AND size is meaningful
                        won = [p for p in onchain_positions if (p.get("status") == "won" or p.get("redeemable")) and p.get("size", 0) >= MIN_SIZE and p.get("curPrice", 0) > 0]
                        
                        if won:
                            for p in won:
                                idx = onchain_positions.index(p)
                                payout = round(p["size"] * 1.0, 2)
                                text += f"🏆 <b>{p.get('outcome', '?')}</b> — {p.get('title', '?')}\n"
                                text += f"   {p['size']:.2f} shares → ${payout:.2f}\n\n"
                                buttons.append([Btn(f"🏆 Redeem ${payout:.2f}", callback_data=f"redeem_onchain_{idx}")])
                        
                        if active:
                            for p in active:
                                idx = onchain_positions.index(p)
                                i = idx
                                pnl_pct = p.get("percent_pnl", 0)
                                pnl_emoji = "📈" if pnl_pct >= 0 else "📉"
                                text += f"⏳ <b>{p.get('outcome', '?')}</b> — {p.get('title', '?')}\n"
                                text += f"   {p['size']:.2f} @ ${p['avg_price']:.2f} → ${p['current_value']:.2f} {pnl_emoji}{'+' if pnl_pct >= 0 else ''}{pnl_pct:.0f}%\n\n"
                                buttons.append([Btn(f"📊 Sell", callback_data=f"sell_onchain_{i}")])
                        
                        if not active and not won:
                            text += "No open positions.\n"
                    else:
                        text += "No open positions.\n"
                except Exception as e:
                    logger.warning(f"Could not fetch on-chain positions: {e}")
                    text += "⚠️ Could not load positions.\n"
            else:
                text += "No wallet set up yet.\n"
            
            buttons.append([Btn("🏠 Menu", callback_data="main_menu")])
            await q.edit_message_text(text, parse_mode=ParseMode.HTML, reply_markup=Markup(buttons))
    
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
            "💰 <b>Deposit:</b> Withdraw USDC on Polygon from any exchange → Paste your address\n"
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
                f"  Share your link. When friends sign up and bet,\n"
                f"  you'll earn rewards when platform fees are active.\n\n"
                f"  Fees are currently <b>0%</b> during our launch period!\n"
                f"  Start building your referral network now."
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

async def _show_league(q, league: str, cur: str, ctx, page: int = 0):
    MATCHES_PER_PAGE = 10
    info = LEAGUES.get(league, {"name": league.upper(), "emoji": "🏟", "sport": "soccer"})
    
    # Check if we already have games cached (for pagination)
    games = ctx.user_data.get(f"g_{league}")
    
    if games is None:
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
    
    total = len(games)
    total_pages = max(1, (total + MATCHES_PER_PAGE - 1) // MATCHES_PER_PAGE)
    page = max(0, min(page, total_pages - 1))
    start = page * MATCHES_PER_PAGE
    end = min(start + MATCHES_PER_PAGE, total)
    page_games = games[start:end]
    
    sport = info.get("sport", "soccer")
    slabels = SPORT_LABELS.get(sport, SPORT_LABELS["soccer"])
    has_draw = slabels["has_draw"]
    
    page_label = f" (Page {page + 1}/{total_pages})" if total_pages > 1 else ""
    text = f"{info['emoji']} <b>{info['name']}</b>{page_label}\n"
    text += "━━━━━━━━━━━━━━━━━━━━━━\n\n"
    
    current_date = ""
    rows = []
    for i, g in enumerate(page_games):
        global_idx = start + i
        date_str, time_str = fmt_time(g.get("time", ""))
        
        if date_str and date_str != current_date:
            current_date = date_str
            text += f"📅 <b>{date_str}</b>\n"
            text += "─────────────────\n"
        
        home = g.get("home", "")[:13] or g.get("title", "")[:15]
        away = g.get("away", "")[:13]
        
        if g.get("live"):
            tag = "🔴 <b>LIVE</b>"
        elif time_str:
            tag = f"🕐 <b>{time_str}</b>"
        else:
            tag = ""
        
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
        
        emoji = info.get("emoji", "🏟")
        home_full = g.get("home", "")[:18] or g.get("home_s", "HOM")
        away_full = g.get("away", "")[:18] or g.get("away_s", "AWY")
        btn_label = f"{emoji} {home_full} vs {away_full}"
        if time_str:
            btn_label = f"🕐{time_str} {emoji} {home_full} vs {away_full}"
        if g.get("live"):
            btn_label = f"🔴 {emoji} {home_full} vs {away_full}"
        if len(btn_label) > 60:
            btn_label = btn_label[:57] + "..."
        rows.append([Btn(btn_label, callback_data=f"game_{league}_{global_idx}")])
    
    # Pagination buttons
    nav_row = []
    if page > 0:
        nav_row.append(Btn("◀ Prev", callback_data=f"pgL_{league}_{page - 1}"))
    if page < total_pages - 1:
        nav_row.append(Btn("Next ▶", callback_data=f"pgL_{league}_{page + 1}"))
    if nav_row:
        rows.append(nav_row)
    
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


async def _show_live(q, cur: str, ctx, page: int = 0):
    MATCHES_PER_PAGE = 25
    
    # Check cache for pagination
    games = ctx.user_data.get("g_live")
    
    if games is None:
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
    
    if not games:
        await q.edit_message_text(
            "🔴 <b>LIVE NOW</b>\n\nNo live matches right now.\nCheck upcoming games! 👇",
            parse_mode=ParseMode.HTML, reply_markup=kb_main()
        )
        return
    
    total = len(games)
    total_pages = max(1, (total + MATCHES_PER_PAGE - 1) // MATCHES_PER_PAGE)
    page = max(0, min(page, total_pages - 1))
    start = page * MATCHES_PER_PAGE
    end = min(start + MATCHES_PER_PAGE, total)
    page_games = games[start:end]
    
    # Group by sport
    sport_names = {
        "soccer": "⚽ FOOTBALL", "basketball": "🏀 BASKETBALL",
        "tennis": "🎾 TENNIS", "hockey": "🏒 HOCKEY",
        "american_football": "🏈 AMERICAN FOOTBALL", "mma": "🥊 MMA",
        "cricket": "🏏 CRICKET", "esports": "🎮 ESPORTS",
    }
    
    page_label = f" (Page {page + 1}/{total_pages})" if total_pages > 1 else ""
    text = f"🔴 <b>LIVE NOW</b>{page_label}\n"
    text += "━━━━━━━━━━━━━━━━━━━━━━\n\n"
    
    current_sport = ""
    rows = []
    for i, g in enumerate(page_games):
        global_idx = start + i
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
        
        sport_emojis = {"soccer": "⚽", "basketball": "🏀", "tennis": "🎾", 
                        "hockey": "🏒", "cricket": "🏏", "mma": "🥊", "esports": "🎮",
                        "american_football": "🏈"}
        se = sport_emojis.get(sport, "🏟")
        home_full = g.get("home", "")[:18] or g.get("home_s", "?")
        away_full = g.get("away", "")[:18] or g.get("away_s", "?")
        label = f"🔴 {se} {home_full} vs {away_full}"
        if len(label) > 60:
            label = label[:57] + "..."
        rows.append([Btn(label, callback_data=f"game_live_{global_idx}")])
    
    # Pagination buttons
    nav_row = []
    if page > 0:
        nav_row.append(Btn("◀ Prev", callback_data=f"pgLive_{page - 1}"))
    if page < total_pages - 1:
        nav_row.append(Btn("Next ▶", callback_data=f"pgLive_{page + 1}"))
    if nav_row:
        rows.append(nav_row)
    
    rows.append([Btn("🔄 Refresh", callback_data="live"), Btn("🔙 Back", callback_data="main_menu")])
    
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
    price = bet.get("price", 0)
    odds = bet.get("odds", 0)
    if price <= 0 and odds > 0:
        price = round(1.0 / odds, 4)
    if odds <= 0 and price > 0:
        odds = round(1.0 / price, 2)
    
    shares = round(stake / price, 2) if price > 0 else 0
    max_winnings = round(shares * 1.0, 2)
    profit = round(max_winnings - stake, 2)
    roi = round((profit / stake) * 100, 1) if stake > 0 else 0
    fee = round(stake * trade.PLATFORM_FEE_RATE, 2) if hasattr(trade, 'PLATFORM_FEE_RATE') else 0
    fee_line = f"💸 Fee ({int(trade.PLATFORM_FEE_RATE * 100)}%): {fmt_currency(fee, cur)}\n" if fee > 0 else ""
    
    await q.edit_message_text(
        f"🎫 <b>CONFIRM BET</b>\n\n"
        f"📋 {bet['match']}\n"
        f"✅ {bet['label']}\n"
        f"📊 Odds: <b>{odds:.2f}</b>\n\n"
        f"💰 Amount: <b>{fmt_currency(stake, cur)}</b>\n"
        f"{fee_line}"
        f"🔢 Est. Shares: {shares:.2f}\n"
        f"🎯 Max Winnings: <b>{fmt_currency(max_winnings, cur)}</b>\n"
        f"📈 Max ROI: <b>+{roi}%</b>\n\n"
        f"⏱ Settles after market resolves.",
        parse_mode=ParseMode.HTML,
        reply_markup=Markup([
            [Btn(f"✅ PLACE BET — {fmt_currency(stake, cur)}", callback_data=f"confirm_{stake}")],
            [Btn("🔄 Refresh Odds", callback_data=f"refresh_confirm_{stake}"),
             Btn("❌ Cancel", callback_data="main_menu")]
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
                reply_markup=Markup([
                    [Btn("🔄 Refresh Odds", callback_data=f"refresh_confirm_{stake}")],
                    [Btn("❌ Cancel", callback_data="main_menu")]
                ])
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
                        f"💡 Please try again in a few moments.",
                        parse_mode=ParseMode.HTML, reply_markup=kb_main()
                    )
                    return
            except Exception as obe:
                logger.error(f"Auto-onboard failed: {obe}")
                await q.edit_message_text(
                    f"❌ <b>Wallet setup failed</b>\n\n"
                    f"Error: {str(obe)[:150]}\n\n"
                    f"Please try again.",
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
                f"{'💸 Fee (' + str(int(trade.PLATFORM_FEE_RATE * 100)) + '%): ' + fmt_currency(fee_info['fee'], cur) + chr(10) if fee_info['fee'] > 0 else ''}"
                f"🎯 Max Payout: {fmt_currency(win, cur)}\n\n"
                f"🆔 Order: <code>{result['order_id'][:16]}...</code>\n\n"
                f"⏳ Settles after the match.\nCheck <b>My Bets</b> for updates.",
                parse_mode=ParseMode.HTML,
                reply_markup=Markup([
                    [Btn("📊 Sell", callback_data=f"sell_{bet_id}")],
                    [Btn("📋 My Bets", callback_data="my_bets"),
                     Btn("🏠 Menu", callback_data="main_menu")],
                ])
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
    user_id = update.effective_user.id
    
    # ── Group Bet PIN confirmation (DM only) ──
    if ctx.bot_data.get(f"grp_awaiting_pin_{user_id}") and update.effective_chat.type == "private":
        ctx.bot_data.pop(f"grp_awaiting_pin_{user_id}", None)
        pending = ctx.bot_data.get(f"grp_pending_{user_id}")
        if not pending:
            await update.message.reply_text("❌ Bet expired.", reply_markup=kb_main())
            return
        
        ctx.bot_data.pop(f"grp_pending_{user_id}", None)
        
        # Verify PIN
        user_obj = await db.get_user(user_id)
        if not user_obj:
            await update.message.reply_text("❌ No wallet found. Use /start", reply_markup=kb_main())
            return
        
        import hashlib
        pin_hash = hashlib.sha256(text.encode()).hexdigest()
        if pin_hash != user_obj.get("pin_hash", ""):
            await update.message.reply_text("❌ Wrong PIN.", reply_markup=kb_main())
            return
        
        # Delete the PIN message for security
        try:
            await update.message.delete()
        except:
            pass
        
        # Execute the bet
        await update.message.reply_text(
            f"⏳ Placing bet: ${pending['amount']:.2f} on {pending['label']}..."
        )
        
        try:
            import wallet
            pk = wallet.derive_private_key(user_obj["encrypted_key"], text)
            
            result = await trade.place_bet(
                private_key=pk,
                safe_address=user_obj["safe_address"],
                token_id=pending["token_id"],
                price=pending["price"],
                amount=pending["amount"],
                side="BUY",
            )
            
            if result.get("success"):
                await update.message.reply_text(
                    f"✅ <b>Bet placed!</b>\n\n"
                    f"📋 {pending['match']}\n"
                    f"✅ {pending['label']} @ {pending['odds']:.2f}\n"
                    f"💰 ${pending['amount']:.2f} → Win ${pending['potential_win']:.2f}",
                    parse_mode=ParseMode.HTML, reply_markup=kb_main()
                )
                
                # Post confirmation in group
                group_id = pending.get("group_chat_id")
                if group_id:
                    name = update.effective_user.first_name
                    try:
                        await ctx.bot.send_message(
                            chat_id=group_id,
                            text=(
                                f"🎰 <b>{name}</b> just bet "
                                f"<b>${pending['amount']:.2f}</b> on "
                                f"<b>{pending['label']}</b>\n"
                                f"📋 {pending['match']} @ {pending['odds']:.2f} odds"
                            ),
                            parse_mode=ParseMode.HTML
                        )
                        
                        # Track for leaderboard
                        grp_bets_key = f"grp_bets_{group_id}"
                        if grp_bets_key not in ctx.bot_data:
                            ctx.bot_data[grp_bets_key] = []
                        ctx.bot_data[grp_bets_key].append({
                            "user_id": update.effective_user.id,
                            "name": name,
                            "match": pending["match"],
                            "label": pending["label"],
                            "amount": pending["amount"],
                            "odds": pending["odds"],
                            "token_id": pending["token_id"],
                            "condition_id": pending.get("condition_id", ""),
                        })
                    except:
                        pass
            else:
                err = result.get("error", "Unknown error")
                await update.message.reply_text(
                    f"❌ Bet failed: {err}", reply_markup=kb_main()
                )
        except Exception as e:
            await update.message.reply_text(
                f"❌ Error: {str(e)[:100]}", reply_markup=kb_main()
            )
        return
    
    # Universal cancel — typing "cancel", "stop", "back", "menu" clears all pending states
    if text.lower() in ("cancel", "stop", "back", "menu", "/cancel", "/menu", "/start"):
        for flag in ["awaiting_stake", "awaiting_bet_pin", "awaiting_export",
                     "awaiting_retry_pin", "awaiting_sell_pin", "awaiting_redeem_pin", "awaiting_convert_pin",
                     "awaiting_withdraw_amount", "awaiting_withdraw_address", "awaiting_withdraw_pin",
                     "pending_confirm_stake", "pending_sell_bet_id", "pending_redeem_bet_id", "bet"]:
            ctx.user_data.pop(flag, None)
        await update.message.reply_text(
            "✅ Cancelled. Back to main menu 👇",
            reply_markup=kb_main()
        )
        return
    
    # Retry wallet onboarding PIN
    if ctx.user_data.get("awaiting_retry_pin"):
        ctx.user_data.pop("awaiting_retry_pin")
        pin = text
        try:
            await update.message.delete()
        except:
            pass
        
        user_obj = await db.get_user(update.effective_user.id)
        if not user_obj:
            await update.effective_chat.send_message("No account found. Use /start.")
            return
        
        pin_hash = db.hash_pin(pin, update.effective_user.id)
        if pin_hash != user_obj.get("pin_hash"):
            await update.effective_chat.send_message("❌ Wrong PIN. Use /retry to try again.")
            return
        
        await update.effective_chat.send_message(
            "⚙️ <b>Retrying wallet setup...</b>\n\n"
            "Deploying your Polymarket wallet (gasless, ~15 seconds).",
            parse_mode=ParseMode.HTML
        )
        
        try:
            w = wallet.derive_wallet(update.effective_user.id, pin)
            ob = wallet.onboard_wallet(w["private_key"])
            if ob["success"]:
                safe_addr = ob["safe_address"]
                await db.update_safe_address(update.effective_user.id, safe_addr)
                await update.effective_chat.send_message(
                    f"✅ <b>Wallet setup complete!</b>\n\n"
                    f"Safe address:\n<code>{safe_addr}</code>\n\n"
                    f"You can now deposit USDC.e and place bets! 👇",
                    parse_mode=ParseMode.HTML, reply_markup=kb_main()
                )
            else:
                await update.effective_chat.send_message(
                    f"❌ <b>Setup failed again</b>\n\n"
                    f"Error: {ob.get('error', 'Unknown')[:200]}\n\n"
                    f"Use /retry to try again or /reset to start fresh.",
                    parse_mode=ParseMode.HTML
                )
        except Exception as e:
            await update.effective_chat.send_message(
                f"❌ Error: {str(e)[:200]}\n\nUse /retry to try again.",
                parse_mode=ParseMode.HTML
            )
        return
    
    # Sell position PIN
    if ctx.user_data.get("awaiting_sell_pin"):
        ctx.user_data.pop("awaiting_sell_pin")
        is_onchain = ctx.user_data.pop("sell_is_onchain", False)
        pin = text
        try:
            await update.message.delete()
        except:
            pass
        
        user_obj = await db.get_user(update.effective_user.id)
        if not user_obj:
            await update.effective_chat.send_message("No account found.")
            return
        
        pin_hash = db.hash_pin(pin, update.effective_user.id)
        if pin_hash != user_obj.get("pin_hash"):
            await update.effective_chat.send_message("❌ Wrong PIN.")
            return
        
        # Get token_id and shares from either on-chain position or DB
        if is_onchain:
            p = ctx.user_data.pop("pending_sell_onchain", None)
            if not p:
                await update.effective_chat.send_message("❌ Position not found.")
                return
            token_id = p.get("token_id", "")
            shares = p.get("size", 0)
        else:
            bet_id = ctx.user_data.pop("pending_sell_bet_id", None)
            bets = await db.get_user_bets(update.effective_user.id)
            bet_row = next((b for b in bets if b["id"] == bet_id), None) if bet_id else None
            if not bet_row:
                await update.effective_chat.send_message("❌ Bet not found.")
                return
            token_id = bet_row.get("token_id", "")
            shares = bet_row.get("shares", 0)
        
        await update.effective_chat.send_message("📊 <b>Selling position...</b>", parse_mode=ParseMode.HTML)
        
        try:
            w = wallet.derive_wallet(update.effective_user.id, pin)
            safe_addr = user_obj.get("safe_address", "")
            
            sell_result = await trade.sell_position(
                private_key=w["private_key"],
                token_id=token_id,
                shares=shares,
                safe_address=safe_addr,
            )
            
            if sell_result.get("success"):
                if not is_onchain and bet_id:
                    await db.update_bet_status(bet_id, "sold", sell_result.get("amount", 0))
                await update.effective_chat.send_message(
                    f"✅ <b>Position sold!</b>\n\n"
                    f"Order: <code>{sell_result.get('order_id', 'N/A')[:16]}...</code>",
                    parse_mode=ParseMode.HTML, reply_markup=kb_main()
                )
            else:
                await update.effective_chat.send_message(
                    f"❌ Sell failed: {sell_result.get('error', 'Unknown')}",
                    parse_mode=ParseMode.HTML, reply_markup=kb_main()
                )
        except Exception as e:
            await update.effective_chat.send_message(f"❌ Error: {str(e)[:200]}", reply_markup=kb_main())
        return
    
    # Redeem position PIN
    if ctx.user_data.get("awaiting_redeem_pin"):
        ctx.user_data.pop("awaiting_redeem_pin")
        pin = text
        try:
            await update.message.delete()
        except:
            pass
        
        user_obj = await db.get_user(update.effective_user.id)
        if not user_obj:
            await update.effective_chat.send_message("No account found.")
            return
        
        pin_hash = db.hash_pin(pin, update.effective_user.id)
        if pin_hash != user_obj.get("pin_hash"):
            await update.effective_chat.send_message("❌ Wrong PIN.")
            return
        
        # Check if this is an on-chain redeem (from Data API positions)
        is_onchain = ctx.user_data.pop("redeem_is_onchain", False)
        onchain_pos = ctx.user_data.pop("pending_redeem_onchain", None)
        
        if is_onchain and onchain_pos:
            # On-chain redeem path
            await update.effective_chat.send_message("🏆 <b>Redeeming position...</b>", parse_mode=ParseMode.HTML)
            try:
                import redeem as _redeem
                w = wallet.derive_wallet(update.effective_user.id, pin)
                token_id = onchain_pos.get("token_id", "")
                condition_id = onchain_pos.get("condition_id", "")
                shares = onchain_pos.get("size", 0)
                neg_risk = onchain_pos.get("neg_risk", False)
                
                redeem_result = await _redeem.redeem_position(
                    private_key=w["private_key"],
                    condition_id=condition_id,
                    token_id=token_id,
                    size=shares,
                    neg_risk=neg_risk,
                )
                
                if redeem_result.get("success"):
                    # Mark as redeemed so it doesn't show again
                    cond_id = onchain_pos.get("conditionId", "")
                    if cond_id:
                        redeemed = ctx.user_data.get("redeemed_positions", set())
                        redeemed.add(cond_id)
                        ctx.user_data["redeemed_positions"] = redeemed
                    await update.effective_chat.send_message(
                        f"✅ <b>Position redeemed!</b>\n\n"
                        f"💰 Payout: ${shares:.2f}\n"
                        f"Tx: <code>{redeem_result.get('tx_hash', 'N/A')}</code>",
                        parse_mode=ParseMode.HTML, reply_markup=kb_main()
                    )
                else:
                    await update.effective_chat.send_message(
                        f"❌ Redeem failed: {redeem_result.get('error', 'Unknown')}",
                        parse_mode=ParseMode.HTML, reply_markup=kb_main()
                    )
            except Exception as e:
                logger.error(f"On-chain redeem error: {e}")
                await update.effective_chat.send_message(f"❌ Error: {str(e)[:200]}", reply_markup=kb_main())
            return
        
        # DB redeem path (legacy)
        bet_id = ctx.user_data.pop("pending_redeem_bet_id", None)
        bets = await db.get_user_bets(update.effective_user.id)
        bet_row = next((b for b in bets if b["id"] == bet_id), None) if bet_id else None
        if not bet_row:
            await update.effective_chat.send_message("❌ Bet not found.")
            return
        
        await update.effective_chat.send_message("🏆 <b>Redeeming position...</b>", parse_mode=ParseMode.HTML)
        
        try:
            import redeem as _redeem
            w = wallet.derive_wallet(update.effective_user.id, pin)
            token_id = bet_row.get("token_id", "")
            shares = bet_row.get("shares", 0)
            
            redeem_result = await _redeem.redeem_position(
                private_key=w["private_key"],
                condition_id=token_id,
                token_id=token_id,
                size=shares,
                neg_risk=True,
            )
            
            if redeem_result.get("success"):
                await db.update_bet_status(bet_id, "settled", shares)
                await db.mark_notified(update.effective_user.id, token_id, "redeemed")
                await update.effective_chat.send_message(
                    f"✅ <b>Position redeemed!</b>\n\n"
                    f"Tx: <code>{redeem_result.get('tx_hash', 'N/A')}</code>",
                    parse_mode=ParseMode.HTML, reply_markup=kb_main()
                )
            else:
                await update.effective_chat.send_message(
                    f"❌ Redeem failed: {redeem_result.get('error', 'Unknown')}",
                    parse_mode=ParseMode.HTML, reply_markup=kb_main()
                )
        except Exception as e:
            await update.effective_chat.send_message(f"❌ Error: {str(e)[:200]}", reply_markup=kb_main())
        return
    
    # Convert USDC → USDC.e
    if ctx.user_data.get("awaiting_convert_pin"):
        ctx.user_data.pop("awaiting_convert_pin")
        pin = text
        try:
            await update.message.delete()
        except:
            pass
        
        user_obj = await db.get_user(update.effective_user.id)
        if not user_obj:
            await update.effective_chat.send_message("No account found.")
            return
        
        pin_hash = db.hash_pin(pin, update.effective_user.id)
        if pin_hash != user_obj.get("pin_hash"):
            await update.effective_chat.send_message("❌ Wrong PIN.")
            return
        
        amount = ctx.user_data.pop("pending_convert_amount", 0)
        if amount < 0.01:
            await update.effective_chat.send_message("❌ No amount to convert.")
            return
        
        safe_addr = user_obj.get("safe_address") or user_obj.get("wallet_address", "")
        
        await update.effective_chat.send_message(
            f"🔄 <b>Converting ${amount:.2f} USDC → USDC.e...</b>\n\n"
            f"⏳ This may take a moment.",
            parse_mode=ParseMode.HTML
        )
        
        try:
            import swap as _swap
            w = wallet.derive_wallet(update.effective_user.id, pin)
            
            swap_result = await _swap.swap_usdc_to_usdc_e(
                private_key=w["private_key"],
                amount_usdc=amount,
                safe_address=safe_addr,
            )
            
            if swap_result.get("success"):
                await update.effective_chat.send_message(
                    f"✅ <b>Conversion complete!</b>\n\n"
                    f"💵 ${amount:.2f} USDC → USDC.e\n"
                    f"💰 Your USDC.e is ready to bet!\n\n"
                    f"Tx: <code>{swap_result.get('tx_hash', 'N/A')}</code>",
                    parse_mode=ParseMode.HTML, reply_markup=kb_main()
                )
            else:
                await update.effective_chat.send_message(
                    f"❌ Conversion failed: {swap_result.get('error', 'Unknown')}\n\n"
                    f"Your USDC is safe — nothing was lost.",
                    parse_mode=ParseMode.HTML, reply_markup=kb_main()
                )
        except Exception as e:
            logger.error(f"Convert error: {e}")
            await update.effective_chat.send_message(
                f"❌ Error: {str(e)[:200]}\n\nYour USDC is safe.",
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
    # --- Withdraw: Step 1 — Amount ---
    if ctx.user_data.get("awaiting_withdraw_amount"):
        ctx.user_data.pop("awaiting_withdraw_amount")
        balance = ctx.user_data.get("withdraw_balance", 0)
        
        if text.lower() == "all":
            amount = balance
        else:
            try:
                amount = float(text.replace("$", "").replace(",", ""))
            except ValueError:
                await update.message.reply_text(
                    "❌ Invalid amount. Enter a number or type <b>all</b>.",
                    parse_mode=ParseMode.HTML,
                    reply_markup=Markup([[Btn("❌ Cancel", callback_data="wallet_menu")]])
                )
                ctx.user_data["awaiting_withdraw_amount"] = True
                return
        
        if amount < 0.01:
            await update.message.reply_text("❌ Minimum withdrawal is $0.01.")
            ctx.user_data["awaiting_withdraw_amount"] = True
            return
        
        if amount > balance:
            await update.message.reply_text(
                f"❌ Insufficient balance. Available: ${balance:.2f}",
                reply_markup=Markup([[Btn("❌ Cancel", callback_data="wallet_menu")]])
            )
            ctx.user_data["awaiting_withdraw_amount"] = True
            return
        
        ctx.user_data["withdraw_amount"] = amount
        await update.message.reply_text(
            f"📤 <b>Withdraw ${amount:.2f}</b>\n\n"
            f"Enter destination wallet address\n"
            f"(Polygon network):",
            parse_mode=ParseMode.HTML,
            reply_markup=Markup([[Btn("❌ Cancel", callback_data="wallet_menu")]])
        )
        ctx.user_data["awaiting_withdraw_address"] = True
        return
    
    # --- Withdraw: Step 2 — Address ---
    if ctx.user_data.get("awaiting_withdraw_address"):
        ctx.user_data.pop("awaiting_withdraw_address")
        address = text.strip()
        
        # Basic address validation
        if not address.startswith("0x") or len(address) != 42:
            await update.message.reply_text(
                "❌ Invalid address. Must be a 0x... Polygon address (42 characters).",
                reply_markup=Markup([[Btn("❌ Cancel", callback_data="wallet_menu")]])
            )
            ctx.user_data["awaiting_withdraw_address"] = True
            return
        
        amount = ctx.user_data.get("withdraw_amount", 0)
        ctx.user_data["withdraw_address"] = address
        
        await update.message.reply_text(
            f"📤 <b>Confirm Withdrawal</b>\n\n"
            f"💵 Amount: <b>${amount:.2f}</b>\n"
            f"📬 To: <code>{address[:8]}...{address[-6:]}</code>\n"
            f"🔗 Network: Polygon\n\n"
            f"Enter your PIN to confirm:",
            parse_mode=ParseMode.HTML,
            reply_markup=Markup([[Btn("❌ Cancel", callback_data="wallet_menu")]])
        )
        ctx.user_data["awaiting_withdraw_pin"] = True
        return
    
    # --- Withdraw: Step 3 — PIN Confirm ---
    if ctx.user_data.get("awaiting_withdraw_pin"):
        ctx.user_data.pop("awaiting_withdraw_pin")
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
            ctx.user_data.pop("withdraw_amount", None)
            ctx.user_data.pop("withdraw_address", None)
            return
        
        amount = ctx.user_data.pop("withdraw_amount", 0)
        address = ctx.user_data.pop("withdraw_address", "")
        ctx.user_data.pop("withdraw_balance", None)
        
        if not amount or not address:
            await update.effective_chat.send_message("❌ Withdrawal expired. Try again.", reply_markup=kb_wallet())
            return
        
        msg = await update.effective_chat.send_message(
            f"⏳ Processing withdrawal of ${amount:.2f}...",
            parse_mode=ParseMode.HTML
        )
        
        try:
            pk = wallet.derive_wallet(update.effective_user.id, pin)["private_key"]
            safe_addr = user.get("safe_address", "")
            
            result = await trade.withdraw_usdc(pk, safe_addr, address, amount)
            
            if result["success"]:
                await msg.edit_text(
                    f"✅ <b>Withdrawal Successful!</b>\n\n"
                    f"💵 Amount: ${amount:.2f}\n"
                    f"📬 To: <code>{address[:8]}...{address[-6:]}</code>\n"
                    f"🔗 Tx: {result.get('tx_hash', 'submitted')}\n\n"
                    f"Funds will arrive shortly on Polygon.",
                    parse_mode=ParseMode.HTML,
                    reply_markup=kb_wallet()
                )
            else:
                await msg.edit_text(
                    f"❌ <b>Withdrawal Failed</b>\n\n{result.get('error', 'Unknown error')}",
                    parse_mode=ParseMode.HTML,
                    reply_markup=kb_wallet()
                )
        except Exception as e:
            logger.error(f"Withdrawal error: {e}")
            await msg.edit_text("❌ Withdrawal failed. Try again.", reply_markup=kb_wallet())
        return
    
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
                                f"❌ Wallet setup failed. Please try again.",
                                parse_mode=ParseMode.HTML, reply_markup=kb_main())
                            return
                    except Exception as obe:
                        await msg.edit_text(
                            f"❌ Wallet setup failed: {str(obe)[:100]}. Please try again.",
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
                        f"{'💸 Fee (' + str(int(trade.PLATFORM_FEE_RATE * 100)) + '%): ' + fmt_currency(fee_info['fee'], cur) + chr(10) if fee_info['fee'] > 0 else ''}"
                        f"🎯 Max Payout: {fmt_currency(win, cur)}\n\n"
                        f"🆔 Order: <code>{result['order_id'][:16]}...</code>",
                        parse_mode=ParseMode.HTML,
                        reply_markup=Markup([
                            [Btn("📊 Sell", callback_data=f"sell_{bet_id}")],
                            [Btn("📋 My Bets", callback_data="my_bets"),
                             Btn("🏠 Menu", callback_data="main_menu")],
                        ])
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

async def _check_settlements(context):
    """Background job: check on-chain positions for resolved markets.
    Uses Polymarket Data API — survives redeployment.
    Sends notification that auto-deletes after 3 minutes."""
    try:
        all_users = await db.get_all_users_with_safe()
        if not all_users:
            return
        
        for user_row in all_users:
            user_id = user_row.get("telegram_id")
            safe_addr = user_row.get("safe_address", "")
            if not safe_addr or not user_id:
                continue
            
            try:
                import requests as _req
                # Check ALL positions (not just redeemable) to detect losses too
                resp = _req.get(
                    "https://data-api.polymarket.com/positions",
                    params={"user": safe_addr, "sizeThreshold": 0, "limit": 50},
                    timeout=10,
                )
                if not resp.ok:
                    continue
                
                positions = resp.json()
                if not isinstance(positions, list):
                    continue
                
                for pos in positions:
                    token_id = pos.get("asset", "")
                    title = pos.get("title", "Unknown Market")
                    outcome = pos.get("outcome", "?")
                    size = float(pos.get("size", 0))
                    pnl = float(pos.get("cashPnl", 0))
                    redeemable = bool(pos.get("redeemable", False))
                    cur_price = float(pos.get("curPrice", 0))
                    initial_value = float(pos.get("initialValue", 0))
                    
                    # Check DB (persists across redeploys)
                    if await db.was_notified(user_id, token_id):
                        continue
                    
                    if redeemable and size > 0:
                        # WON — record and notify
                        await db.mark_notified(user_id, token_id, "won")
                        profit = round(size * 1.0 - initial_value, 2)
                        await db.record_win(user_id, max(profit, 0))
                        
                        payout = round(size * 1.0, 2)
                        msg = await context.bot.send_message(
                            chat_id=user_id,
                            text=f"🏆 <b>You Won!</b>\n\n"
                                 f"📋 {title}\n"
                                 f"✅ {outcome}\n"
                                 f"💰 Payout: ${payout:.2f}\n\n"
                                 f"Tap below to collect your winnings.",
                            parse_mode=ParseMode.HTML,
                            reply_markup=Markup([
                                [Btn("💰 My Bets", callback_data="my_bets")],
                            ])
                        )
                        # Auto-delete after 3 min
                        context.job_queue.run_once(
                            _auto_delete_msg, 180,
                            data={"chat_id": user_id, "msg_id": msg.message_id}
                        )
                    
                    elif cur_price <= 0.01 and size > 0.01:
                        # LOST — record and send brief notification
                        await db.mark_notified(user_id, token_id, "lost")
                        await db.record_loss(user_id, initial_value)
                        
                        msg = await context.bot.send_message(
                            chat_id=user_id,
                            text=f"❌ <b>Bet Lost</b>\n\n"
                                 f"📋 {title}\n"
                                 f"❌ {outcome} — resolved against you.",
                            parse_mode=ParseMode.HTML,
                        )
                        # Auto-delete loss notification after 1 min
                        context.job_queue.run_once(
                            _auto_delete_msg, 60,
                            data={"chat_id": user_id, "msg_id": msg.message_id}
                        )
                    
            except Exception as e:
                logger.debug(f"Settlement check error for user {user_id}: {e}")
    except Exception as e:
        logger.error(f"Settlement monitor error: {e}")


async def _auto_delete_msg(context):
    """Auto-delete a notification message."""
    try:
        data = context.job.data
        await context.bot.delete_message(chat_id=data["chat_id"], message_id=data["msg_id"])
    except:
        pass  # Message may already be deleted



# ========================================
# GROUP BETTING — Social betting in group chats
# ========================================

async def cmd_bet(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Post a betting card in a group chat.
    Usage: /bet  (shows today's matches to pick from)
    Usage: /bet Arsenal vs Chelsea  (search for specific match)
    """
    chat = update.effective_chat
    user = update.effective_user
    
    # Check if user has a wallet
    existing = await db.get_user(user.id)
    if not existing:
        # If in group, tell them to set up in DM first
        if chat.type in ("group", "supergroup"):
            await update.message.reply_text(
                f"👋 Hey {user.first_name}! Set up your wallet first.\n\n"
                f"DM me @{(await ctx.bot.get_me()).username} and send /start",
                parse_mode=ParseMode.HTML
            )
            return
        else:
            await update.message.reply_text("Use /start first to create your wallet.")
            return
    
    cur = existing.get("currency", "USD")
    
    # If they typed /bet with a search query
    query = " ".join(ctx.args).strip().lower() if ctx.args else ""
    
    # Fetch today's matches
    import asyncio
    from datetime import datetime as dt, timezone, timedelta
    
    today_leagues = ["epl", "lal", "sea", "bun", "fl1", "ucl", "uel", "nba", "mma"]
    tasks = [fetch_events(lg) for lg in today_leagues]
    results = await asyncio.gather(*tasks, return_exceptions=True)
    
    wat = timezone(timedelta(hours=1))
    now = dt.now(wat)
    today_str = now.strftime("%Y-%m-%d")
    
    matches = []
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
                        g["_league_emoji"] = info.get("emoji", "⚽")
                        matches.append(g)
                except:
                    pass
    
    if not matches:
        await update.message.reply_text("No matches found today. Check back later!")
        return
    
    # If search query, filter
    if query:
        filtered = [m for m in matches if query in m.get("home", "").lower() or query in m.get("away", "").lower() or query in m.get("title", "").lower()]
        if filtered:
            matches = filtered
    
    # Store matches for callback
    ctx.bot_data[f"grp_matches_{chat.id}"] = matches
    
    # If in group, show compact match list
    if chat.type in ("group", "supergroup"):
        # Show top 8 matches as buttons
        buttons = []
        for i, m in enumerate(matches[:8]):
            home = m.get("home", "?")
            away = m.get("away", "?")
            label = f"{m['_league_emoji']} {home} vs {away}"
            buttons.append([Btn(label, callback_data=f"grp_match_{i}")])
        
        buttons.append([Btn("📅 More matches", callback_data="grp_more")])
        
        await update.message.reply_text(
            "⚽ <b>Pick a match to bet on</b>\n\n"
            "Everyone in this group can bet 👇",
            parse_mode=ParseMode.HTML,
            reply_markup=Markup(buttons)
        )
    else:
        # In DM, show same as /today
        await cmd_today(update, ctx)


async def _post_group_card(message, match: dict, cur: str, ctx):
    """Post a betting card for a match that anyone in the group can bet on."""
    home = match.get("home", "?")
    away = match.get("away", "?")
    league_emoji = match.get("_league_emoji", "⚽")
    league_name = match.get("_league_name", "")
    
    # Build outcomes from moneyline data (ml dict)
    ml = match.get("ml", {})
    if not ml or (not ml.get("home") and not ml.get("away")):
        await message.reply_text("❌ No odds available for this match yet.")
        return
    
    outcomes = []
    if ml.get("home") and ml.get("home") > 0:
        h_name = ml.get("home_name", home)
        outcomes.append({"label": h_name, "price": ml["home"], "token_id": ml.get("home_tid", "")})
    if ml.get("draw") and ml.get("draw") > 0:
        outcomes.append({"label": "Draw", "price": ml["draw"], "token_id": ml.get("draw_tid", "")})
    if ml.get("away") and ml.get("away") > 0:
        a_name = ml.get("away_name", away)
        outcomes.append({"label": a_name, "price": ml["away"], "token_id": ml.get("away_tid", "")})
    
    if not outcomes:
        await message.reply_text("❌ No odds available for this match yet.")
        return
    
    # Store outcomes in match for callback reference
    match["_group_outcomes"] = outcomes
    
    # Build odds display
    odds_lines = []
    buttons = []
    amounts = [1, 5, 10, 25]
    
    for j, oc in enumerate(outcomes):
        label = oc["label"]
        price = oc["price"]
        odds = round(1.0 / price, 2) if price > 0 else 0
        odds_lines.append(f"  {label}: <b>{odds:.2f}</b>")
        row = []
        for amt in amounts:
            row.append(Btn(
                f"{label} ${amt}",
                callback_data=f"grpbet_{j}_{amt}"
            ))
        buttons.append(row)
    
    buttons.append([Btn("🔄 Refresh Odds", callback_data="grp_refresh")])
    
    # Time
    time_str = ""
    if match.get("time"):
        try:
            from datetime import datetime as dt, timezone, timedelta
            wat = timezone(timedelta(hours=1))
            gt = dt.fromisoformat(match["time"].replace("Z", "+00:00")).astimezone(wat)
            time_str = gt.strftime("%I:%M %p WAT")
        except:
            pass
    
    text = (
        f"{league_emoji} <b>{league_name}</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n\n"
        f"<b>{home}  vs  {away}</b>\n"
        f"🕐 {time_str}\n\n"
        f"📊 <b>Odds</b>\n"
        + "\n".join(odds_lines) + "\n\n"
        f"👇 <b>Tap to bet — anyone can play</b>"
    )
    
    # Store match data for bet callbacks
    ctx.bot_data[f"grp_match_{message.chat_id}"] = match
    
    sent = await message.reply_text(text, parse_mode=ParseMode.HTML, reply_markup=Markup(buttons))
    
    # Schedule auto-cleanup: remove buttons after kickoff
    if match.get("time"):
        try:
            from datetime import datetime as dt, timezone
            kick = dt.fromisoformat(match["time"].replace("Z", "+00:00"))
            now = dt.now(timezone.utc)
            secs_until_kick = (kick - now).total_seconds()
            
            if secs_until_kick > 0:
                import asyncio
                
                async def _cleanup_card():
                    await asyncio.sleep(secs_until_kick + 60)  # 1 min after kickoff
                    try:
                        await sent.edit_text(
                            f"{league_emoji} <b>{league_name}</b>\n\n"
                            f"<b>{home}  vs  {away}</b>\n\n"
                            f"⚽ <b>Match started — betting closed</b>",
                            parse_mode=ParseMode.HTML
                        )
                    except:
                        pass
                
                asyncio.create_task(_cleanup_card())
        except:
            pass


async def _handle_group_bet(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Handle a bet button tap in a group chat."""
    q = update.callback_query
    user = q.from_user
    d = q.data  # grpbet_{outcome_idx}_{amount}
    
    parts = d.split("_")
    outcome_idx = int(parts[1])
    amount = float(parts[2])
    
    # Check if user has wallet
    existing = await db.get_user(user.id)
    if not existing:
        await q.answer(
            f"You need a wallet first! DM @{(await ctx.bot.get_me()).username} and send /start",
            show_alert=True
        )
        return
    
    # Get match data
    match = ctx.bot_data.get(f"grp_match_{q.message.chat_id}")
    if not match:
        await q.answer("Match expired. Post a new one with /bet", show_alert=True)
        return
    
    outcomes = match.get("_group_outcomes", [])
    if outcome_idx >= len(outcomes):
        await q.answer("Invalid selection.", show_alert=True)
        return
    
    oc = outcomes[outcome_idx]
    label = oc.get("label", oc.get("outcome", "?"))
    price = oc.get("price", 0)
    odds = round(1.0 / price, 2) if price > 0 else 0
    token_id = oc.get("token_id", "")
    potential_win = round(amount / price, 2) if price > 0 else 0
    
    home = match.get("home", "?")
    away = match.get("away", "?")
    
    if not token_id:
        await q.answer("No trading data for this outcome yet.", show_alert=True)
        return
    
    # Check balance
    safe_addr = existing.get("safe_address", "")
    balance = existing.get("balance", 0)
    
    # Show confirmation popup — ONLY the tapper sees this
    await q.answer(
        f"Confirm: ${amount:.0f} on {label}\n"
        f"Odds: {odds:.2f} → Win ${potential_win:.2f}\n"
        f"{home} vs {away}",
        show_alert=True
    )
    
    # Store bet details for PIN confirmation in DM
    # We need to ask for PIN privately
    ctx.bot_data[f"grp_pending_{user.id}"] = {
        "match": f"{home} vs {away}",
        "label": label,
        "token_id": token_id,
        "price": price,
        "odds": odds,
        "amount": amount,
        "potential_win": potential_win,
        "condition_id": match.get("condition_id", ""),
        "group_chat_id": q.message.chat_id,
        "group_msg_id": q.message.message_id,
    }
    
    # Send DM to user asking for PIN
    try:
        await ctx.bot.send_message(
            chat_id=user.id,
            text=(
                f"🔐 <b>Confirm Group Bet</b>\n\n"
                f"📋 {home} vs {away}\n"
                f"✅ {label} @ {odds:.2f}\n"
                f"💰 ${amount:.2f} → Win ${potential_win:.2f}\n\n"
                f"⚠️ Slippage: Odds may change slightly by execution time.\n\n"
                f"Enter your 6-digit PIN to place this bet:"
            ),
            parse_mode=ParseMode.HTML
        )
        ctx.bot_data[f"grp_awaiting_pin_{user.id}"] = True
    except Exception:
        # Can't DM user — they haven't started the bot
        await q.answer(
            f"I can't DM you! Send /start to @{(await ctx.bot.get_me()).username} first.",
            show_alert=True
        )


# ── GROUP LEADERBOARD ──

async def cmd_leaderboard(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Show betting leaderboard for this group."""
    chat = update.effective_chat
    
    if chat.type not in ("group", "supergroup"):
        await update.message.reply_text("Leaderboards only work in group chats!")
        return
    
    # Get leaderboard data from bot_data
    lb_key = f"leaderboard_{chat.id}"
    lb = ctx.bot_data.get(lb_key, {})
    
    if not lb:
        await update.message.reply_text(
            "📊 <b>Group Leaderboard</b>\n\n"
            "No bets placed yet! Use /bet to get started.",
            parse_mode=ParseMode.HTML
        )
        return
    
    # Sort by profit
    sorted_lb = sorted(lb.items(), key=lambda x: x[1].get("profit", 0), reverse=True)
    
    text = "📊 <b>Group Leaderboard</b>\n\n"
    
    medals = ["🥇", "🥈", "🥉"]
    for i, (uid, stats) in enumerate(sorted_lb[:10]):
        medal = medals[i] if i < 3 else f"{i+1}."
        name = stats.get("name", "Unknown")
        bets = stats.get("bets", 0)
        wins = stats.get("wins", 0)
        profit = stats.get("profit", 0)
        streak = stats.get("streak", 0)
        
        pnl = f"+${profit:.2f}" if profit >= 0 else f"-${abs(profit):.2f}"
        streak_str = f" 🔥{streak}" if streak >= 3 else ""
        
        text += f"{medal} <b>{name}</b> — {pnl}{streak_str}\n"
        text += f"    {bets} bets · {wins} wins\n\n"
    
    await update.message.reply_text(text, parse_mode=ParseMode.HTML)


def _update_leaderboard(bot_data: dict, chat_id: int, user_id: int, name: str, won: bool, profit: float):
    """Update group leaderboard after a bet resolves."""
    lb_key = f"leaderboard_{chat_id}"
    if lb_key not in bot_data:
        bot_data[lb_key] = {}
    
    lb = bot_data[lb_key]
    uid_str = str(user_id)
    
    if uid_str not in lb:
        lb[uid_str] = {"name": name, "bets": 0, "wins": 0, "profit": 0.0, "streak": 0}
    
    entry = lb[uid_str]
    entry["name"] = name
    entry["bets"] += 1
    entry["profit"] += profit
    
    if won:
        entry["wins"] += 1
        entry["streak"] = entry.get("streak", 0) + 1
    else:
        entry["streak"] = 0



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
        
        # Start settlement monitor — checks every 5 minutes
        application.job_queue.run_repeating(
            _check_settlements, interval=300, first=60,
            name="settlement_monitor"
        )
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
    app.add_handler(CommandHandler("admin", cmd_admin))
    app.add_handler(CommandHandler("users", cmd_users_export))
    app.add_handler(CommandHandler("reset", cmd_reset))
    app.add_handler(CommandHandler("retry", cmd_retry))
    app.add_handler(CommandHandler("fees", cmd_admin_fees))
    app.add_handler(CommandHandler("bet", cmd_bet))
    app.add_handler(CommandHandler("leaderboard", cmd_leaderboard))
    
    # All button clicks
    app.add_handler(CallbackQueryHandler(on_button))
    
    # Text input (custom stakes, export PIN)
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text))
    
    # Global error handler — silently ignore "message not modified" errors
    async def error_handler(update, context):
        from telegram.error import BadRequest
        if isinstance(context.error, BadRequest) and "not modified" in str(context.error):
            return  # Silently ignore — happens when refresh doesn't change content
        logger.error("Unhandled error: %s", context.error)
    
    app.add_error_handler(error_handler)
    
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
