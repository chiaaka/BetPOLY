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
from polymarket_api import (
    LEAGUES, CURRENCIES, fetch_events, fetch_live, parse_event,
    format_match, price_to_odds, fmt_odds, fmt_currency
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s"
)
logger = logging.getLogger("BetPoly")

# Conversation states
PIN_SET, PIN_CONFIRM = range(2)


# ========================================
# Keyboards
# ========================================

def kb_main():
    return Markup([
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
        [Btn("⚙️ Settings", callback_data="settings"),
         Btn("❓ Help", callback_data="help")],
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
    """Tappable odds buttons for a match."""
    rows = []
    
    # 1X2
    if game.get("ml"):
        ml = game["ml"]
        r = []
        if ml["home"] > 0:
            r.append(Btn(f"1  {fmt_odds(ml['home'])}", callback_data=f"b_{league}_{idx}_ml_h"))
        if ml.get("draw", 0) > 0:
            r.append(Btn(f"X  {fmt_odds(ml['draw'])}", callback_data=f"b_{league}_{idx}_ml_d"))
        if ml["away"] > 0:
            r.append(Btn(f"2  {fmt_odds(ml['away'])}", callback_data=f"b_{league}_{idx}_ml_a"))
        if r:
            rows.append(r)
    
    # Over/Under
    if game.get("totals"):
        main = game["totals"][0]
        for t in game["totals"]:
            if str(t["line"]) in ["2.5", "3.5"]:
                main = t
                break
        ti = game["totals"].index(main)
        r = []
        if main["over"] > 0:
            r.append(Btn(f"⬆ Ov {main['line']}  {fmt_odds(main['over'])}", callback_data=f"b_{league}_{idx}_t_{ti}_o"))
        if main["under"] > 0:
            r.append(Btn(f"⬇ Un {main['line']}  {fmt_odds(main['under'])}", callback_data=f"b_{league}_{idx}_t_{ti}_u"))
        if r:
            rows.append(r)
    
    # GG/NG
    if game.get("btts"):
        bt = game["btts"]
        r = []
        if bt["yes"] > 0:
            r.append(Btn(f"GG  {fmt_odds(bt['yes'])}", callback_data=f"b_{league}_{idx}_bt_y"))
        if bt["no"] > 0:
            r.append(Btn(f"NG  {fmt_odds(bt['no'])}", callback_data=f"b_{league}_{idx}_bt_n"))
        if r:
            rows.append(r)
    
    # Handicap
    if game.get("spreads"):
        sp = game["spreads"][0]
        r = []
        if sp["home"] > 0:
            r.append(Btn(f"H1 ({sp['line']})  {fmt_odds(sp['home'])}", callback_data=f"b_{league}_{idx}_sp_0_h"))
        if sp["away"] > 0:
            r.append(Btn(f"H2 (+{sp['line']})  {fmt_odds(sp['away'])}", callback_data=f"b_{league}_{idx}_sp_0_a"))
        if r:
            rows.append(r)
    
    rows.append([
        Btn("📊 More Lines", callback_data=f"more_{league}_{idx}"),
        Btn("🔙 Back", callback_data=f"league_{league}")
    ])
    return Markup(rows)


# ========================================
# /start + Wallet Setup
# ========================================

async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    existing = await db.get_user(user.id)
    
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
        f"🔐 <b>Step 1 of 2: Create your PIN</b>\n\n"
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
        "✅ Got it!\n\n🔐 <b>Step 2 of 2: Confirm your PIN</b>\n\nType the same PIN again:",
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
    
    await db.create_user(user.id, user.username or "", w["address"], db.hash_pin(pin, user.id))
    
    # Show key in self-destructing message
    key_msg = await update.effective_chat.send_message(
        f"🔐 <b>YOUR RECOVERY KEY — SAVE NOW!</b>\n\n"
        f"📬 Wallet Address:\n<code>{w['address']}</code>\n\n"
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
        pass  # If job queue unavailable, message stays (user can delete manually)
    
    # Welcome guide
    await update.effective_chat.send_message(
        f"🎉 <b>You're all set, {user.first_name}!</b>\n\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"📖 <b>HOW TO USE BETPOLY</b>\n\n"
        f"<b>1️⃣ Fund your wallet</b>\n"
        f"Send USDC on Polygon network to:\n"
        f"<code>{w['address']}</code>\n"
        f"💡 Buy USDC on Binance → Withdraw on Polygon\n\n"
        f"<b>2️⃣ Pick a match</b>\n"
        f"Tap ⚽ Football or 🏀 Basketball below\n"
        f"Browse leagues → tap a match\n\n"
        f"<b>3️⃣ Tap the odds to bet</b>\n"
        f"<code>1 = Home Win  |  X = Draw  |  2 = Away</code>\n"
        f"<code>GG = Both Score | NG = Not Both</code>\n"
        f"<code>Over/Under = Total Goals</code>\n\n"
        f"<b>4️⃣ Collect your winnings</b>\n"
        f"Payouts are automatic within 1 hour of full time\n\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"Ready to bet? Pick a sport 👇",
        parse_mode=ParseMode.HTML, reply_markup=kb_main()
    )
    ctx.user_data.pop("pin", None)
    return ConversationHandler.END


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
        if g["ml"]:
            h, a = fmt_odds(g["ml"]["home"]), fmt_odds(g["ml"]["away"])
            label = f"🔴 {g['home_s']} {h} | {g['away_s']} {a}"
        else:
            label = f"🔴 {g['home_s']} vs {g['away_s']}"
        rows.append([Btn(label, callback_data=f"game_live_{i}")])
    rows.append([Btn("🔙 Back", callback_data="main_menu")])
    await msg.edit_text("🔴 <b>LIVE NOW</b>\n\nTap for full odds 👇",
                        parse_mode=ParseMode.HTML, reply_markup=Markup(rows))


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
    await update.message.reply_text(
        f"👛 <b>Your Wallet</b>\n\n"
        f"📬 Address:\n<code>{user['wallet_address']}</code>\n\n"
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
    
    user = await db.get_user(q.from_user.id)
    cur = user.get("currency", "USD") if user else "USD"
    
    # --- Navigation ---
    if d == "main_menu":
        await q.edit_message_text("⚽ <b>BetPoly</b> — Powered by Polymarket\n\nPick a sport 👇",
                                  parse_mode=ParseMode.HTML, reply_markup=kb_main())
    
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
            await q.edit_message_text(
                f"👛 <b>Your Wallet</b>\n\n"
                f"📬 Address:\n<code>{user['wallet_address']}</code>\n\n"
                f"💰 Balance: <i>Send USDC on Polygon to fund</i>",
                parse_mode=ParseMode.HTML, reply_markup=kb_wallet()
            )
    
    elif d == "w_deposit":
        if user:
            await q.edit_message_text(
                f"📥 <b>Deposit</b>\n\n"
                f"Send <b>USDC</b> on <b>Polygon</b> to:\n\n"
                f"<code>{user['wallet_address']}</code>\n\n"
                f"⚠️ Only USDC on Polygon network.\n\n"
                f"💡 Buy USDC on Binance → Withdraw to Polygon → Paste address above",
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
                    text += f"• {b['selection']} @ {b['odds']:.2f}\n"
                    text += f"  Stake: {fmt_currency(b['amount_usdc'], cur)} → Win: {fmt_currency(b['potential_win'], cur)}\n"
                    text += f"  Status: {b['status']}\n\n"
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
            "Browse Football, Basketball, or Live\n\n"
            "<b>2. Tap the odds</b>\n"
            "1 = Home  |  X = Draw  |  2 = Away\n"
            "GG = Both Score  |  NG = Not Both\n"
            "Over/Under = Total Goals\n\n"
            "<b>3. Enter your stake</b>\n"
            "Min bet: $1 USDC\n\n"
            "<b>4. Win and collect</b>\n"
            "Payouts are automatic. Usually within 1hr of full time.\n\n"
            "💰 <b>Deposit:</b> Binance → Withdraw USDC on Polygon → Paste your address\n"
            "🔑 <b>Your wallet is yours:</b> Export key in Settings → import to MetaMask",
            parse_mode=ParseMode.HTML, reply_markup=kb_main()
        )


# ========================================
# League / Game / Live Views
# ========================================

async def _show_league(q, league: str, cur: str, ctx):
    info = LEAGUES.get(league, {"name": league.upper(), "emoji": "🏟"})
    
    await q.edit_message_text(
        f"{info['emoji']} <b>{info['name']}</b>\n\n⏳ Loading...",
        parse_mode=ParseMode.HTML
    )
    
    try:
        raw = await fetch_events(league)
    except Exception as e:
        logger.error(f"League fetch error: {e}")
        raw = []
    
    if not raw:
        await q.edit_message_text(
            f"{info['emoji']} <b>{info['name']}</b>\n\nNo matches found. Check back later!",
            parse_mode=ParseMode.HTML,
            reply_markup=Markup([[Btn("🔙 Back", callback_data="main_menu")]])
        )
        return
    
    games = [parse_event(g) for g in raw]
    ctx.user_data[f"g_{league}"] = games
    
    rows = []
    for i, g in enumerate(games[:15]):
        if g["ml"]:
            ml = g["ml"]
            h, a = fmt_odds(ml["home"]), fmt_odds(ml["away"])
            d_str = f" | X {fmt_odds(ml['draw'])}" if ml.get("draw", 0) > 0 else ""
            label = f"{g['home_s']} {h}{d_str} | {g['away_s']} {a}"
        else:
            label = f"{g['home_s']} vs {g['away_s']}"
        rows.append([Btn(label, callback_data=f"game_{league}_{i}")])
    
    rows.append([Btn("🔙 Back", callback_data="main_menu")])
    
    await q.edit_message_text(
        f"{info['emoji']} <b>{info['name']}</b>\n\nTap a match for full odds 👇",
        parse_mode=ParseMode.HTML, reply_markup=Markup(rows)
    )


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
    
    rows = []
    for i, g in enumerate(games[:10]):
        if g["ml"]:
            h, a = fmt_odds(g["ml"]["home"]), fmt_odds(g["ml"]["away"])
            label = f"🔴 {g['home_s']} {h} | {g['away_s']} {a}"
        else:
            label = f"🔴 {g['home_s']} vs {g['away_s']}"
        rows.append([Btn(label, callback_data=f"game_live_{i}")])
    
    rows.append([Btn("🔙 Back", callback_data="main_menu")])
    await q.edit_message_text("🔴 <b>LIVE NOW</b>\n\nTap for full odds 👇",
                              parse_mode=ParseMode.HTML, reply_markup=Markup(rows))


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
    
    if mtype == "ml":
        side = parts[4]
        ml = g["ml"]
        if side == "h":
            odds = price_to_odds(ml["home"])
            label = f"1 ({g['home']})"
        elif side == "d":
            odds = price_to_odds(ml["draw"])
            label = "X (Draw)"
        elif side == "a":
            odds = price_to_odds(ml["away"])
            label = f"2 ({g['away']})"
    
    elif mtype == "t":
        ti, side = int(parts[4]), parts[5]
        t = g["totals"][ti]
        if side == "o":
            odds = price_to_odds(t["over"])
            label = f"Over {t['line']}"
        else:
            odds = price_to_odds(t["under"])
            label = f"Under {t['line']}"
    
    elif mtype == "bt":
        side = parts[4]
        if side == "y":
            odds = price_to_odds(g["btts"]["yes"])
            label = "GG (Both Teams Score)"
        else:
            odds = price_to_odds(g["btts"]["no"])
            label = "NG (Not Both Score)"
    
    elif mtype == "sp":
        si, side = int(parts[4]), parts[5]
        sp = g["spreads"][si]
        if side == "h":
            odds = price_to_odds(sp["home"])
            label = f"H1 ({g['home_s']} {sp['line']})"
        else:
            odds = price_to_odds(sp["away"])
            label = f"H2 ({g['away_s']} +{sp['line']})"
    
    if odds <= 0:
        return
    
    match_name = f"{g['home']} vs {g['away']}"
    ctx.user_data["bet"] = {
        "league": league, "idx": idx, "label": label,
        "odds": odds, "match": match_name, "cb": data
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
    
    # TODO Phase 2: Execute real trade via Polymarket CLOB API with builder attribution
    # For now, log and show success
    
    user = await db.get_user(q.from_user.id)
    if user:
        await db.log_bet(q.from_user.id, bet["match"], bet["label"], stake, bet["odds"])
    
    win = round(stake * bet["odds"], 2)
    await q.edit_message_text(
        f"✅ <b>BET PLACED!</b>\n\n"
        f"📋 {bet['match']}\n"
        f"✅ {bet['label']}\n"
        f"📊 Odds: {bet['odds']:.2f}\n"
        f"💰 Stake: {fmt_currency(stake, cur)}\n"
        f"🎯 To Win: {fmt_currency(win, cur)}\n\n"
        f"⏳ Settles after the match.\nCheck <b>My Bets</b> for updates.",
        parse_mode=ParseMode.HTML, reply_markup=kb_main()
    )
    ctx.user_data.pop("bet", None)


# ========================================
# Text Input Handler
# ========================================

async def on_text(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    
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
            ("live", "🔴 Live matches now"),
            ("football", "⚽ Football leagues"),
            ("basketball", "🏀 Basketball leagues"),
            ("wallet", "👛 Wallet & balance"),
            ("bets", "💰 My active bets"),
            ("settings", "⚙️ Change currency & preferences"),
            ("help", "❓ How to use BetPoly"),
        ])
        await db.init_db()
        logger.info("🚀 BetPoly is running!")
    
    app.post_init = post_init
    
    # PIN setup conversation
    app.add_handler(ConversationHandler(
        entry_points=[CommandHandler("start", cmd_start)],
        states={
            PIN_SET: [MessageHandler(filters.TEXT & ~filters.COMMAND, pin_set)],
            PIN_CONFIRM: [MessageHandler(filters.TEXT & ~filters.COMMAND, pin_confirm)],
        },
        fallbacks=[CommandHandler("start", cmd_start)],
    ))
    
    # Quick command shortcuts
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
