"""
BetPoly - Polymarket API
Fetches sports markets and converts share prices to decimal odds.

Real data format (from Chelsea vs Leeds page):
  Moneyline: CHE 64¢ | Draw 22¢ | LEE 16¢
  Spreads: CHE -1.5 39¢ | LEE +1.5 63¢
  Totals: O3.5 36¢ | U3.5 65¢
  BTTS: Yes 56¢ | No 46¢
"""
import httpx
import logging

logger = logging.getLogger("BetPoly.API")

GAMMA_API = "https://gamma-api.polymarket.com"

# Supported leagues with Sportybet-style names
LEAGUES = {
    # Soccer
    "epl":        {"name": "🏴󠁧󠁢󠁥󠁮󠁧󠁿 England - Premier League", "emoji": "⚽", "sport": "soccer"},
    "laliga":     {"name": "🇪🇸 Spain - La Liga",           "emoji": "⚽", "sport": "soccer"},
    "sea":        {"name": "🇮🇹 Italy - Serie A",            "emoji": "⚽", "sport": "soccer"},
    "bundesliga": {"name": "🇩🇪 Germany - Bundesliga",       "emoji": "⚽", "sport": "soccer"},
    "ligue-1":    {"name": "🇫🇷 France - Ligue 1",           "emoji": "⚽", "sport": "soccer"},
    "ucl":        {"name": "🏆 Champions League",             "emoji": "⚽", "sport": "soccer"},
    "uel":        {"name": "🏆 Europa League",                "emoji": "⚽", "sport": "soccer"},
    "mls":        {"name": "🇺🇸 USA - MLS",                   "emoji": "⚽", "sport": "soccer"},
    "spl":        {"name": "🇸🇦 Saudi Pro League",            "emoji": "⚽", "sport": "soccer"},
    "por":        {"name": "🇵🇹 Portugal - Primeira Liga",    "emoji": "⚽", "sport": "soccer"},
    "ere":        {"name": "🇳🇱 Netherlands - Eredivisie",    "emoji": "⚽", "sport": "soccer"},
    # Basketball
    "nba":        {"name": "🇺🇸 NBA",                         "emoji": "🏀", "sport": "basketball"},
    "cbb":        {"name": "🇺🇸 NCAA Basketball",             "emoji": "🏀", "sport": "basketball"},
    # American Football
    "nfl":        {"name": "🇺🇸 NFL",                         "emoji": "🏈", "sport": "football"},
    # Hockey
    "nhl":        {"name": "🇺🇸 NHL",                         "emoji": "🏒", "sport": "hockey"},
    # MMA
    "ufc":        {"name": "🥊 UFC",                           "emoji": "🥊", "sport": "mma"},
}

# Local currency rates for display
CURRENCIES = {
    "USD": {"symbol": "$",    "rate": 1.0},
    "NGN": {"symbol": "₦",   "rate": 1550.0},
    "KES": {"symbol": "KES ", "rate": 130.0},
    "GHS": {"symbol": "GH₵", "rate": 16.0},
    "ZAR": {"symbol": "R",   "rate": 18.5},
    "TZS": {"symbol": "TSh", "rate": 2500.0},
    "UGX": {"symbol": "USh", "rate": 3800.0},
}

# Team abbreviations
TEAM_ABBR = {
    "chelsea": "CHE", "leeds united": "LEE", "leeds": "LEE",
    "arsenal": "ARS", "liverpool": "LIV",
    "manchester city": "MCI", "man city": "MCI",
    "manchester united": "MUN", "man utd": "MUN", "man united": "MUN",
    "tottenham": "TOT", "spurs": "TOT",
    "newcastle": "NEW", "aston villa": "AVL", "villa": "AVL",
    "brighton": "BRI", "west ham": "WHU",
    "brentford": "BRE", "fulham": "FUL",
    "crystal palace": "CRY", "bournemouth": "BOU",
    "wolves": "WOL", "wolverhampton": "WOL",
    "everton": "EVE", "nottingham forest": "NFO", "forest": "NFO",
    "burnley": "BUR", "sunderland": "SUN",
}


def price_to_odds(price: float) -> float:
    """$0.64 share → 1/0.64 = 1.56 decimal odds"""
    if price <= 0.01 or price >= 0.99:
        return 0.0
    return round(1.0 / price, 2)


def fmt_odds(price: float) -> str:
    odds = price_to_odds(price)
    return f"{odds:.2f}" if odds > 0 else "—"


def fmt_currency(usdc: float, currency: str = "USD") -> str:
    c = CURRENCIES.get(currency, CURRENCIES["USD"])
    val = usdc * c["rate"]
    if val >= 1_000_000:
        return f"{c['symbol']}{val/1_000_000:.1f}M"
    if val >= 1_000:
        return f"{c['symbol']}{val/1_000:.1f}k"
    return f"{c['symbol']}{val:.2f}"


def short_name(full_name: str) -> str:
    """Get 3-letter team abbreviation."""
    lower = full_name.lower().replace(" fc", "").replace(" afc", "").strip()
    for key, abbr in TEAM_ABBR.items():
        if key in lower:
            return abbr
    return lower[:3].upper()


async def fetch_games(league: str) -> list:
    """Fetch games for a league from Polymarket Gamma API."""
    async with httpx.AsyncClient(timeout=15) as client:
        try:
            r = await client.get(f"{GAMMA_API}/sports/{league}/games")
            if r.status_code == 200:
                data = r.json()
                return data if isinstance(data, list) else data.get("games", [])
        except Exception as e:
            logger.error(f"fetch_games({league}): {e}")
    return []


async def fetch_live() -> list:
    """Fetch all live games."""
    async with httpx.AsyncClient(timeout=15) as client:
        try:
            r = await client.get(f"{GAMMA_API}/sports/live")
            if r.status_code == 200:
                data = r.json()
                return data if isinstance(data, list) else data.get("games", [])
        except Exception as e:
            logger.error(f"fetch_live: {e}")
    return []


def parse_game(game: dict) -> dict:
    """
    Parse raw Polymarket game into clean structure.
    
    Based on real data from Chelsea vs Leeds:
    - Moneyline section: home/draw/away prices
    - Spreads section: line + home/away prices
    - Totals section: line + over/under prices
    - BTTS section: yes/no prices
    """
    home = game.get("homeTeam", game.get("home_team", game.get("home", "")))
    away = game.get("awayTeam", game.get("away_team", game.get("away", "")))
    
    # Clean up FC suffixes for display
    home_clean = home.replace(" FC", "").replace(" AFC", "").strip() if home else "Home"
    away_clean = away.replace(" FC", "").replace(" AFC", "").strip() if away else "Away"
    
    parsed = {
        "home": home_clean,
        "away": away_clean,
        "home_s": short_name(home),
        "away_s": short_name(away),
        "time": game.get("startTime", game.get("start_time", game.get("gameTime", ""))),
        "volume": game.get("volume", game.get("totalVolume", 0)),
        "status": game.get("status", game.get("gameStatus", "")),
        "ml": None,        # Moneyline / 1X2
        "spreads": [],     # Handicap lines
        "totals": [],      # Over/Under lines
        "btts": None,      # Both Teams to Score
        "raw": game,
    }
    
    markets = game.get("markets", game.get("subMarkets", []))
    
    for mkt in markets:
        mtype = str(mkt.get("type", mkt.get("marketType", mkt.get("label", "")))).lower()
        outcomes = mkt.get("outcomes", mkt.get("options", []))
        
        # MONEYLINE → 1X2
        if any(k in mtype for k in ["moneyline", "winner", "match_result", "result"]):
            ml = {"home": 0, "draw": 0, "away": 0, "id": mkt.get("id", "")}
            for o in outcomes:
                name = str(o.get("name", o.get("outcome", o.get("label", "")))).lower()
                price = _get_price(o)
                tid = o.get("token_id", o.get("tokenId", o.get("clobTokenId", "")))
                
                if any(x in name for x in [home.lower()[:4], "home", home_clean.lower()[:4]]) and "draw" not in name:
                    ml["home"] = price
                    ml["home_tid"] = tid
                elif "draw" in name or name == "x":
                    ml["draw"] = price
                    ml["draw_tid"] = tid
                elif any(x in name for x in [away.lower()[:4], "away", away_clean.lower()[:4]]):
                    ml["away"] = price
                    ml["away_tid"] = tid
            
            if ml["home"] > 0 or ml["away"] > 0:
                parsed["ml"] = ml
        
        # SPREADS → Handicap
        elif any(k in mtype for k in ["spread", "handicap"]):
            line = mkt.get("line", mkt.get("spread", mkt.get("point", "")))
            sp = {"line": line, "home": 0, "away": 0, "id": mkt.get("id", "")}
            for o in outcomes:
                price = _get_price(o)
                name = str(o.get("name", o.get("outcome", ""))).lower()
                tid = o.get("token_id", o.get("tokenId", o.get("clobTokenId", "")))
                if any(x in name for x in [home.lower()[:4], "home", "-"]):
                    sp["home"] = price
                    sp["home_tid"] = tid
                else:
                    sp["away"] = price
                    sp["away_tid"] = tid
            if sp["home"] > 0 or sp["away"] > 0:
                parsed["spreads"].append(sp)
        
        # TOTALS → Over/Under
        elif any(k in mtype for k in ["total", "over", "under", "goals"]):
            line = mkt.get("line", mkt.get("total", mkt.get("point", "")))
            tot = {"line": line, "over": 0, "under": 0, "id": mkt.get("id", "")}
            for o in outcomes:
                price = _get_price(o)
                name = str(o.get("name", o.get("outcome", ""))).lower()
                tid = o.get("token_id", o.get("tokenId", o.get("clobTokenId", "")))
                if any(x in name for x in ["over", "yes", "o "]):
                    tot["over"] = price
                    tot["over_tid"] = tid
                else:
                    tot["under"] = price
                    tot["under_tid"] = tid
            if tot["over"] > 0 or tot["under"] > 0:
                parsed["totals"].append(tot)
        
        # BTTS → GG/NG
        elif any(k in mtype for k in ["both", "btts", "bts"]):
            bt = {"yes": 0, "no": 0, "id": mkt.get("id", "")}
            for o in outcomes:
                price = _get_price(o)
                name = str(o.get("name", o.get("outcome", ""))).lower()
                tid = o.get("token_id", o.get("tokenId", o.get("clobTokenId", "")))
                if "yes" in name:
                    bt["yes"] = price
                    bt["yes_tid"] = tid
                else:
                    bt["no"] = price
                    bt["no_tid"] = tid
            if bt["yes"] > 0 or bt["no"] > 0:
                parsed["btts"] = bt
    
    return parsed


def _get_price(outcome: dict) -> float:
    """Extract price from outcome, handling different API formats."""
    for key in ["price", "probability", "lastPrice", "midpoint"]:
        val = outcome.get(key)
        if val is not None:
            try:
                p = float(val)
                # Handle percentage format (64 vs 0.64)
                return p / 100 if p > 1 else p
            except (ValueError, TypeError):
                continue
    return 0.0


def format_match(g: dict, currency: str = "USD") -> str:
    """Format parsed game as Sportybet-style Telegram message."""
    lines = []
    lines.append(f"<b>{g['home']}  vs  {g['away']}</b>")
    lines.append("")
    
    # 1X2
    if g["ml"]:
        ml = g["ml"]
        h, d, a = fmt_odds(ml["home"]), fmt_odds(ml["draw"]) if ml["draw"] > 0 else "", fmt_odds(ml["away"])
        lines.append("── <b>1X2</b> ──")
        if d:
            lines.append(f"  1️⃣ <b>{h}</b>     ✖️ <b>{d}</b>     2️⃣ <b>{a}</b>")
        else:
            lines.append(f"  1️⃣ <b>{h}</b>          2️⃣ <b>{a}</b>")
        lines.append("")
    
    # Over/Under - show best line
    if g["totals"]:
        # Prefer 2.5 or 3.5 for soccer, first available otherwise
        main = g["totals"][0]
        for t in g["totals"]:
            if str(t["line"]) in ["2.5", "3.5"]:
                main = t
                break
        lines.append(f"── <b>Over/Under {main['line']}</b> ──")
        lines.append(f"  ⬆️ Over <b>{fmt_odds(main['over'])}</b>     ⬇️ Under <b>{fmt_odds(main['under'])}</b>")
        if len(g["totals"]) > 1:
            other = " | ".join(str(t["line"]) for t in g["totals"])
            lines.append(f"  📊 Lines: {other}")
        lines.append("")
    
    # Handicap
    if g["spreads"]:
        sp = g["spreads"][0]
        lines.append(f"── <b>Handicap ({g['home_s']} {sp['line']})</b> ──")
        lines.append(f"  1️⃣ <b>{fmt_odds(sp['home'])}</b>          2️⃣ <b>{fmt_odds(sp['away'])}</b>")
        lines.append("")
    
    # GG/NG
    if g["btts"]:
        lines.append("── <b>Both Teams Score</b> ──")
        lines.append(f"  ✅ GG <b>{fmt_odds(g['btts']['yes'])}</b>     ❌ NG <b>{fmt_odds(g['btts']['no'])}</b>")
        lines.append("")
    
    # Volume
    vol = g.get("volume", 0)
    if vol:
        try:
            lines.append(f"💰 Volume: {fmt_currency(float(vol), currency)}")
        except:
            pass
    
    return "\n".join(lines)
