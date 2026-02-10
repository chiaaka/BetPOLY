"""
BetPoly - Polymarket API
Correct endpoints per docs.polymarket.com:
  GET /sports → series_id per league
  GET /events?series_id={id}&active=true&closed=false → events with markets
  Each market has: outcomes (JSON), outcomePrices (JSON), clobTokenIds (JSON)
"""
import httpx
import json
import re
import logging
from datetime import datetime

logger = logging.getLogger("BetPoly.API")

GAMMA_API = "https://gamma-api.polymarket.com"

# Real series IDs from GET /sports (Feb 10 2026)
LEAGUES = {
    # ===== FOOTBALL - TOP =====
    "epl":  {"name": "🏴󠁧󠁢󠁥󠁮󠁧󠁿 Premier League",  "series": "10188", "emoji": "⚽", "sport": "soccer", "cat": "top"},
    "lal":  {"name": "🇪🇸 La Liga",           "series": "10193", "emoji": "⚽", "sport": "soccer", "cat": "top"},
    "sea":  {"name": "🇮🇹 Serie A",            "series": "10203", "emoji": "⚽", "sport": "soccer", "cat": "top"},
    "bun":  {"name": "🇩🇪 Bundesliga",         "series": "10194", "emoji": "⚽", "sport": "soccer", "cat": "top"},
    "fl1":  {"name": "🇫🇷 Ligue 1",            "series": "10195", "emoji": "⚽", "sport": "soccer", "cat": "top"},
    # ===== FOOTBALL - CUPS =====
    "ucl":  {"name": "🏆 Champions League",    "series": "10204", "emoji": "⚽", "sport": "soccer", "cat": "cups"},
    "uel":  {"name": "🏆 Europa League",       "series": "10209", "emoji": "⚽", "sport": "soccer", "cat": "cups"},
    "efa":  {"name": "🏴󠁧󠁢󠁥󠁮󠁧󠁿 FA Cup",         "series": "10307", "emoji": "⚽", "sport": "soccer", "cat": "cups"},
    "cdr":  {"name": "🇪🇸 Copa del Rey",       "series": "10316", "emoji": "⚽", "sport": "soccer", "cat": "cups"},
    "itc":  {"name": "🇮🇹 Coppa Italia",       "series": "10287", "emoji": "⚽", "sport": "soccer", "cat": "cups"},
    # ===== FOOTBALL - MORE =====
    "efl":  {"name": "🏴󠁧󠁢󠁥󠁮󠁧󠁿 EFL Championship","series": "10230", "emoji": "⚽", "sport": "soccer", "cat": "more"},
    "ere":  {"name": "🇳🇱 Eredivisie",         "series": "10286", "emoji": "⚽", "sport": "soccer", "cat": "more"},
    "tur":  {"name": "🇹🇷 Süper Lig",          "series": "10292", "emoji": "⚽", "sport": "soccer", "cat": "more"},
    "mls":  {"name": "🇺🇸 MLS",                "series": "10189", "emoji": "⚽", "sport": "soccer", "cat": "more"},
    "mex":  {"name": "🇲🇽 Liga MX",            "series": "10290", "emoji": "⚽", "sport": "soccer", "cat": "more"},
    "arg":  {"name": "🇦🇷 Primera División",   "series": "10285", "emoji": "⚽", "sport": "soccer", "cat": "more"},
    "lib":  {"name": "🏆 Copa Libertadores",   "series": "10289", "emoji": "⚽", "sport": "soccer", "cat": "more"},
    "sud":  {"name": "🏆 Copa Sudamericana",   "series": "10291", "emoji": "⚽", "sport": "soccer", "cat": "more"},
    "rus":  {"name": "🇷🇺 Russian Premier",    "series": "10306", "emoji": "⚽", "sport": "soccer", "cat": "more"},
    # ===== FOOTBALL - INTERNATIONAL =====
    "caf":  {"name": "🌍 Africa WC Qualifiers","series": "10240", "emoji": "⚽", "sport": "soccer", "cat": "intl"},
    "uef":  {"name": "🇪🇺 Europe WC Qualifiers","series":"10243", "emoji": "⚽", "sport": "soccer", "cat": "intl"},
    "cof":  {"name": "🌎 CONCACAF Qualifiers", "series": "10244", "emoji": "⚽", "sport": "soccer", "cat": "intl"},
    "con":  {"name": "🌎 CONMEBOL Qualifiers", "series": "10246", "emoji": "⚽", "sport": "soccer", "cat": "intl"},
    "afc":  {"name": "🌏 Asia WC Qualifiers",  "series": "10241", "emoji": "⚽", "sport": "soccer", "cat": "intl"},
    "acn":  {"name": "🌍 Africa Cup of Nations","series":"10786",  "emoji": "⚽", "sport": "soccer", "cat": "intl"},
    "fif":  {"name": "🌐 FIFA Friendlies",     "series": "10238", "emoji": "⚽", "sport": "soccer", "cat": "intl"},
    # ===== BASKETBALL =====
    "nba":  {"name": "🇺🇸 NBA",                "series": "10345", "emoji": "🏀", "sport": "basketball", "cat": "main"},
    "ncaab":{"name": "🇺🇸 NCAA Basketball",    "series": "39",    "emoji": "🏀", "sport": "basketball", "cat": "main"},
    "cwbb": {"name": "🇺🇸 NCAA Women's BBall", "series": "10471", "emoji": "🏀", "sport": "basketball", "cat": "main"},
    # ===== TENNIS =====
    "atp":  {"name": "🎾 ATP (Men)",           "series": "10365", "emoji": "🎾", "sport": "tennis", "cat": "main"},
    "wta":  {"name": "🎾 WTA (Women)",         "series": "10366", "emoji": "🎾", "sport": "tennis", "cat": "main"},
    # ===== CRICKET =====
    "t20":  {"name": "🏏 T20 International",   "series": "10445", "emoji": "🏏", "sport": "cricket", "cat": "main"},
    "odi":  {"name": "🏏 ODI International",   "series": "10451", "emoji": "🏏", "sport": "cricket", "cat": "main"},
    "ipl":  {"name": "🏏 IPL",                 "series": "44",    "emoji": "🏏", "sport": "cricket", "cat": "main"},
    "abb":  {"name": "🏏 Big Bash League",     "series": "10449", "emoji": "🏏", "sport": "cricket", "cat": "main"},
    # ===== AMERICAN FOOTBALL =====
    "nfl":  {"name": "🇺🇸 NFL",                "series": "10187", "emoji": "🏈", "sport": "american_football", "cat": "main"},
    "cfb":  {"name": "🇺🇸 NCAA Football",      "series": "10210", "emoji": "🏈", "sport": "american_football", "cat": "main"},
    # ===== HOCKEY =====
    "nhl":  {"name": "🇺🇸 NHL",                "series": "10346", "emoji": "🏒", "sport": "hockey", "cat": "main"},
    # ===== MMA =====
    "mma":  {"name": "🥊 UFC / MMA",           "series": "10500", "emoji": "🥊", "sport": "mma", "cat": "main"},
    # ===== ESPORTS =====
    "cs2":  {"name": "🎮 CS2",                 "series": "10310", "emoji": "🎮", "sport": "esports", "cat": "main"},
    "lol":  {"name": "🎮 League of Legends",   "series": "10311", "emoji": "🎮", "sport": "esports", "cat": "main"},
    "val":  {"name": "🎮 Valorant",            "series": "10369", "emoji": "🎮", "sport": "esports", "cat": "main"},
    "dota2":{"name": "🎮 Dota 2",              "series": "10309", "emoji": "🎮", "sport": "esports", "cat": "main"},
}

CURRENCIES = {
    "USD": {"symbol": "$",    "rate": 1.0},
    "NGN": {"symbol": "₦",   "rate": 1550.0},
    "KES": {"symbol": "KES ", "rate": 130.0},
    "GHS": {"symbol": "GH₵", "rate": 16.0},
    "ZAR": {"symbol": "R",   "rate": 18.5},
    "TZS": {"symbol": "TSh", "rate": 2500.0},
    "UGX": {"symbol": "USh", "rate": 3800.0},
}


def price_to_odds(price: float) -> float:
    if price <= 0.01 or price >= 0.99:
        return 0.0
    return round(1.0 / price, 2)

def fmt_odds(price: float) -> str:
    odds = price_to_odds(price)
    return f"{odds:.2f}" if odds > 0 else "—"

def fmt_currency(usdc: float, currency: str = "USD") -> str:
    c = CURRENCIES.get(currency, CURRENCIES["USD"])
    val = usdc * c["rate"]
    if val >= 1_000_000: return f"{c['symbol']}{val/1_000_000:.1f}M"
    if val >= 1_000: return f"{c['symbol']}{val/1_000:.1f}k"
    return f"{c['symbol']}{val:.2f}"

def _jparse(val):
    if isinstance(val, str):
        try: return json.loads(val)
        except: return []
    return val if isinstance(val, list) else []


async def fetch_events(league: str) -> list:
    info = LEAGUES.get(league)
    if not info:
        return []
    params = {
        "series_id": info["series"],
        "active": "true",
        "closed": "false",
        "limit": "50",
        "order": "startTime",
        "ascending": "true",
    }
    async with httpx.AsyncClient(timeout=15) as client:
        try:
            r = await client.get(f"{GAMMA_API}/events", params=params)
            logger.info(f"fetch({league}) series={info['series']}: {r.status_code}")
            if r.status_code == 200:
                return r.json() if isinstance(r.json(), list) else []
        except Exception as e:
            logger.error(f"fetch({league}): {e}")
    return []


async def fetch_live() -> list:
    params = {"active": "true", "closed": "false", "live": "true", "limit": "30"}
    async with httpx.AsyncClient(timeout=15) as client:
        try:
            r = await client.get(f"{GAMMA_API}/events", params=params)
            logger.info(f"fetch_live: {r.status_code}")
            if r.status_code == 200:
                return r.json() if isinstance(r.json(), list) else []
        except Exception as e:
            logger.error(f"fetch_live: {e}")
    return []


def parse_event(event: dict) -> dict:
    title = event.get("title", "")
    markets = event.get("markets", [])
    home, away = _extract_teams(title)
    
    parsed = {
        "title": title, "home": home, "away": away,
        "home_s": home[:3].upper() if home else "HOM",
        "away_s": away[:3].upper() if away else "AWY",
        "time": event.get("startTime", ""),
        "volume": 0, "live": event.get("live", False),
        "ml": None, "spreads": [], "totals": [], "btts": None,
        "event_id": event.get("id", ""), "slug": event.get("slug", ""),
    }
    
    total_vol = 0
    for mkt in markets:
        q = (mkt.get("question") or mkt.get("groupItemTitle") or "").lower()
        outcomes = _jparse(mkt.get("outcomes", "[]"))
        prices_raw = _jparse(mkt.get("outcomePrices", "[]"))
        tokens = _jparse(mkt.get("clobTokenIds", "[]"))
        prices = []
        for p in prices_raw:
            try: prices.append(float(p))
            except: prices.append(0.0)
        
        try: total_vol += float(mkt.get("volume", 0) or 0)
        except: pass
        
        mid = mkt.get("id", "")
        if not prices or all(p == 0 for p in prices):
            continue
        
        # MONEYLINE
        if "moneyline" in q or "match result" in q or "winner" in q:
            ml = {"home": 0, "draw": 0, "away": 0, "id": mid}
            for i, o in enumerate(outcomes):
                n = str(o).lower()
                p = prices[i] if i < len(prices) else 0
                t = tokens[i] if i < len(tokens) else ""
                if "draw" in n or n == "x":
                    ml["draw"], ml["draw_tid"] = p, t
                elif i == 0:
                    ml["home"], ml["home_tid"] = p, t
                elif i == 1 and len(outcomes) == 3:
                    ml["draw"], ml["draw_tid"] = p, t
                else:
                    ml["away"], ml["away_tid"] = p, t
            if ml["home"] > 0 or ml["away"] > 0:
                parsed["ml"] = ml
        
        # SPREAD
        elif "spread" in q or "handicap" in q:
            m = re.search(r'[+-]?\d+\.?\d*', q.split("spread")[-1] if "spread" in q else q)
            line = m.group() if m else ""
            sp = {"line": line, "home": 0, "away": 0, "id": mid}
            if len(prices) >= 2:
                sp["home"], sp["home_tid"] = prices[0], tokens[0] if tokens else ""
                sp["away"], sp["away_tid"] = prices[1], tokens[1] if len(tokens) > 1 else ""
            if sp["home"] > 0 or sp["away"] > 0:
                parsed["spreads"].append(sp)
        
        # TOTAL / OVER-UNDER
        elif "total" in q or "over" in q or "under" in q:
            m = re.search(r'\d+\.?\d*', q)
            line = m.group() if m else ""
            tot = {"line": line, "over": 0, "under": 0, "id": mid}
            for i, o in enumerate(outcomes):
                n = str(o).lower()
                if "over" in n or "yes" in n:
                    tot["over"] = prices[i] if i < len(prices) else 0
                    tot["over_tid"] = tokens[i] if i < len(tokens) else ""
                elif "under" in n or "no" in n:
                    tot["under"] = prices[i] if i < len(prices) else 0
                    tot["under_tid"] = tokens[i] if i < len(tokens) else ""
            if tot["over"] == 0 and tot["under"] == 0 and len(prices) >= 2:
                tot["over"], tot["over_tid"] = prices[0], tokens[0] if tokens else ""
                tot["under"], tot["under_tid"] = prices[1], tokens[1] if len(tokens) > 1 else ""
            if tot["over"] > 0 or tot["under"] > 0:
                parsed["totals"].append(tot)
        
        # BTTS
        elif "both" in q and "score" in q:
            bt = {"yes": 0, "no": 0, "id": mid}
            for i, o in enumerate(outcomes):
                n = str(o).lower()
                if "yes" in n:
                    bt["yes"] = prices[i] if i < len(prices) else 0
                    bt["yes_tid"] = tokens[i] if i < len(tokens) else ""
                elif "no" in n:
                    bt["no"] = prices[i] if i < len(prices) else 0
                    bt["no_tid"] = tokens[i] if i < len(tokens) else ""
            if bt["yes"] == 0 and bt["no"] == 0 and len(prices) >= 2:
                bt["yes"], bt["yes_tid"] = prices[0], tokens[0] if tokens else ""
                bt["no"], bt["no_tid"] = prices[1], tokens[1] if len(tokens) > 1 else ""
            if bt["yes"] > 0 or bt["no"] > 0:
                parsed["btts"] = bt
        
        # FALLBACK: 2-3 outcome market with no keyword = likely moneyline
        elif not parsed["ml"] and len(outcomes) in [2, 3] and not any(k in q for k in ["prop", "mvp", "champion", "futures", "season"]):
            ml = {"home": 0, "draw": 0, "away": 0, "id": mid}
            if len(outcomes) == 2:
                ml["home"], ml["home_tid"] = prices[0], tokens[0] if tokens else ""
                ml["away"], ml["away_tid"] = (prices[1] if len(prices) > 1 else 0), (tokens[1] if len(tokens) > 1 else "")
            elif len(outcomes) == 3:
                ml["home"], ml["home_tid"] = prices[0], tokens[0] if tokens else ""
                ml["draw"], ml["draw_tid"] = (prices[1] if len(prices) > 1 else 0), (tokens[1] if len(tokens) > 1 else "")
                ml["away"], ml["away_tid"] = (prices[2] if len(prices) > 2 else 0), (tokens[2] if len(tokens) > 2 else "")
            if ml["home"] > 0 or ml["away"] > 0:
                parsed["ml"] = ml
    
    parsed["volume"] = total_vol
    return parsed


def _extract_teams(title: str) -> tuple:
    for sep in [" vs ", " vs. ", " v ", " - "]:
        if sep in title.lower():
            idx = title.lower().index(sep)
            return title[:idx].strip(), title[idx+len(sep):].strip()
    if ":" in title:
        return _extract_teams(title.split(":", 1)[1].strip())
    return title, ""


def format_match(g: dict, currency: str = "USD") -> str:
    lines = []
    
    # Title block
    if g["home"] and g["away"]:
        lines.append(f"<b>{g['home']}</b>")
        lines.append(f"        <i>vs</i>")
        lines.append(f"<b>{g['away']}</b>")
    else:
        lines.append(f"<b>{g['title']}</b>")
    
    lines.append("━━━━━━━━━━━━━━━━━━━━━━")
    
    # Time + status
    status_parts = []
    if g.get("time"):
        try:
            dt = datetime.fromisoformat(g["time"].replace("Z", "+00:00"))
            status_parts.append(f"📅 {dt.strftime('%a %d %b %Y')}")
            status_parts.append(f"⏰ {dt.strftime('%H:%M')} UTC")
        except: pass
    if g.get("live"):
        status_parts.append("🔴 <b>LIVE</b>")
    if status_parts:
        lines.append("  ".join(status_parts))
    
    lines.append("")
    
    # 1X2 MONEYLINE
    if g["ml"]:
        ml = g["ml"]
        h = fmt_odds(ml["home"])
        a = fmt_odds(ml["away"])
        lines.append("┌─── <b>Match Result (1X2)</b> ───┐")
        if ml.get("draw", 0) > 0:
            d = fmt_odds(ml["draw"])
            lines.append(f"│                              │")
            lines.append(f"│  <b>1</b>  Home     <code>{h:>6}</code>         │")
            lines.append(f"│  <b>X</b>  Draw     <code>{d:>6}</code>         │")
            lines.append(f"│  <b>2</b>  Away     <code>{a:>6}</code>         │")
        else:
            lines.append(f"│                              │")
            lines.append(f"│  <b>1</b>  Home     <code>{h:>6}</code>         │")
            lines.append(f"│  <b>2</b>  Away     <code>{a:>6}</code>         │")
        lines.append("└──────────────────────────────┘")
        lines.append("")
    
    # OVER / UNDER
    if g["totals"]:
        main = g["totals"][0]
        for t in g["totals"]:
            if str(t["line"]) in ["2.5", "3.5"]:
                main = t
                break
        ov = fmt_odds(main["over"])
        un = fmt_odds(main["under"])
        lines.append(f"┌── <b>Total Goals O/U {main['line']}</b> ──┐")
        lines.append(f"│                              │")
        lines.append(f"│  ⬆  Over     <code>{ov:>6}</code>         │")
        lines.append(f"│  ⬇  Under    <code>{un:>6}</code>         │")
        lines.append("└──────────────────────────────┘")
        # Show other lines
        if len(g["totals"]) > 1:
            other_lines = [f"{t['line']}" for t in g["totals"] if t != main]
            if other_lines:
                lines.append(f"  <i>More lines: {', '.join(other_lines[:4])}</i>")
        lines.append("")
    
    # HANDICAP / SPREAD
    if g["spreads"]:
        sp = g["spreads"][0]
        h = fmt_odds(sp["home"])
        a = fmt_odds(sp["away"])
        lines.append(f"┌──── <b>Handicap ({sp['line']})</b> ────┐")
        lines.append(f"│                              │")
        lines.append(f"│  H1  Home     <code>{h:>6}</code>        │")
        lines.append(f"│  H2  Away     <code>{a:>6}</code>        │")
        lines.append("└──────────────────────────────┘")
        lines.append("")
    
    # BOTH TEAMS TO SCORE
    if g["btts"]:
        y = fmt_odds(g["btts"]["yes"])
        n = fmt_odds(g["btts"]["no"])
        lines.append("┌── <b>Both Teams to Score</b> ──┐")
        lines.append(f"│                              │")
        lines.append(f"│  ✅  GG (Yes)  <code>{y:>6}</code>        │")
        lines.append(f"│  ❌  NG (No)   <code>{n:>6}</code>        │")
        lines.append("└──────────────────────────────┘")
        lines.append("")
    
    # Volume
    if g.get("volume"):
        try:
            lines.append(f"💰 Volume: {fmt_currency(float(g['volume']), currency)}")
        except: pass
    
    lines.append("")
    lines.append("👇 <i>Tap an odds button below to bet</i>")
    
    return "\n".join(lines)
