"""
BetPoly - Polymarket Gamma API
Correct flow: /events?series_id={id}&tag_id=100639&active=true&closed=false
tag_id=100639 filters for individual game bets (not futures/season props)

Sport-specific display:
  Football: 1X2 (3-way), Goals O/U, BTTS, Handicap
  Basketball: Winner (2-way), Points O/U, Spread
  Tennis/MMA: Winner (2-way)
  Cricket: Winner (2-way), various props
"""
import httpx
import json
import re
import logging
from datetime import datetime

logger = logging.getLogger("BetPoly.API")

GAMMA_API = "https://gamma-api.polymarket.com"
GAME_TAG = "100639"  # tag_id for individual game markets

# Real series IDs from GET /sports (Feb 2026)
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

# Sport type determines display format
SPORT_LABELS = {
    "soccer":           {"ml": "Match Result (1X2)", "total": "Goals", "has_draw": True},
    "basketball":       {"ml": "Winner",             "total": "Points", "has_draw": False},
    "american_football":{"ml": "Winner",             "total": "Points", "has_draw": False},
    "hockey":           {"ml": "Winner",             "total": "Goals",  "has_draw": False},
    "tennis":           {"ml": "Winner",             "total": "Games",  "has_draw": False},
    "cricket":          {"ml": "Winner",             "total": "Runs",   "has_draw": False},
    "mma":              {"ml": "Winner",             "total": "Rounds", "has_draw": False},
    "esports":          {"ml": "Winner",             "total": "Maps",   "has_draw": False},
    "rugby":            {"ml": "Winner",             "total": "Points", "has_draw": False},
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

# Live rate tracking
_last_rate_update = 0.0
RATE_REFRESH_SECONDS = 4 * 3600  # refresh every 4 hours


async def refresh_currency_rates():
    """Fetch live USD exchange rates from free API. Falls back to hardcoded."""
    global _last_rate_update
    import time
    
    now = time.time()
    if now - _last_rate_update < RATE_REFRESH_SECONDS:
        return  # Still fresh
    
    # Try multiple free APIs in order
    apis = [
        "https://open.er-api.com/v6/latest/USD",
        "https://api.exchangerate-api.com/v4/latest/USD",
    ]
    
    for url in apis:
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                r = await client.get(url)
                if r.status_code == 200:
                    data = r.json()
                    rates = data.get("rates", {})
                    updated = False
                    for code in CURRENCIES:
                        if code == "USD":
                            continue
                        if code in rates:
                            CURRENCIES[code]["rate"] = float(rates[code])
                            updated = True
                    if updated:
                        _last_rate_update = now
                        logger.info(f"💱 Currency rates updated: NGN={CURRENCIES['NGN']['rate']}, KES={CURRENCIES['KES']['rate']}, GHS={CURRENCIES['GHS']['rate']}")
                        return
        except Exception as e:
            logger.warning(f"Currency rate fetch failed ({url}): {e}")
    
    logger.warning("💱 Using fallback hardcoded rates")


def price_to_odds(price: float) -> float:
    """Convert Polymarket share price to decimal odds. $0.64 → 1.56"""
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

def fmt_time(iso_str: str) -> tuple:
    """
    Convert ISO time to (date_str, time_str) in UTC+1 / WAT, 12hr format.
    Returns: ("Mon 10 Feb", "7:30 PM") or ("", "")
    """
    if not iso_str:
        return "", ""
    try:
        from datetime import timedelta, timezone as tz
        utc_plus1 = tz(timedelta(hours=1))
        t = datetime.fromisoformat(iso_str.replace("Z", "+00:00")).astimezone(utc_plus1)
        date_str = t.strftime("%a %d %b")
        time_str = t.strftime("%I:%M %p").lstrip("0")  # 12hr, no leading zero
        return date_str, time_str
    except:
        return "", ""

def fmt_date_bold(iso_str: str) -> str:
    """Format date for section header: 'Mon 10 Feb 2026'"""
    if not iso_str:
        return ""
    try:
        from datetime import timedelta, timezone as tz
        utc_plus1 = tz(timedelta(hours=1))
        t = datetime.fromisoformat(iso_str.replace("Z", "+00:00")).astimezone(utc_plus1)
        return t.strftime("%A, %d %B %Y")  # "Monday, 10 February 2026"
    except:
        return ""

def _jp(val):
    """Parse JSON string fields from API."""
    if isinstance(val, str):
        try: return json.loads(val)
        except: return []
    return val if isinstance(val, list) else []


# =============================================================================
# API CALLS
# =============================================================================

async def fetch_events(league: str) -> list:
    info = LEAGUES.get(league)
    if not info:
        return []
    
    # Get all tags for this league from /sports data
    tags = info.get("tags", "")
    
    params = {
        "series_id": info["series"],
        "tag_id": GAME_TAG,  # Only individual games, not futures
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
                data = r.json()
                if isinstance(data, list):
                    # Log ALL markets in first event for debugging
                    if data:
                        ev0 = data[0]
                        logger.info(f"  Event[0]: title={ev0.get('title','')[:50]} markets={len(ev0.get('markets',[]))}")
                        for j, m in enumerate(ev0.get("markets", [])[:6]):
                            q = m.get("question", "")[:50]
                            o = m.get("outcomes", "")[:50]
                            p = m.get("outcomePrices", "")[:50]
                            logger.info(f"  Market[{j}]: q={q} | o={o} | p={p}")
                    return data
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
                data = r.json()
                return data if isinstance(data, list) else []
        except Exception as e:
            logger.error(f"fetch_live: {e}")
    return []


# =============================================================================
# PARSE EVENT → BETTING STRUCTURE
# =============================================================================

def parse_event(event: dict) -> dict:
    """
    Parse Polymarket event into unified betting structure.
    
    Polymarket market questions:
      "Grizzlies vs Lakers: Moneyline" → outcomes ["Grizzlies","Lakers"], prices ["0.62","0.38"]
      "Grizzlies vs Lakers: Spread -3.5" → outcomes ["Grizzlies -3.5","Lakers +3.5"], prices [...]
      "Grizzlies vs Lakers: Total Points Over/Under 228.5" → outcomes ["Over","Under"], prices [...]
      "Chelsea vs Leeds: Moneyline" → outcomes ["Chelsea","Draw","Leeds"], prices ["0.64","0.22","0.16"]
    """
    title = event.get("title", "")
    markets = event.get("markets", [])
    home, away = _extract_teams(title)
    
    # Detect sport from series
    sport = _detect_sport(event)
    
    parsed = {
        "title": title, "home": home, "away": away,
        "home_s": _abbreviate(home), "away_s": _abbreviate(away),
        "time": event.get("startTime", ""),
        "sport": sport,
        "volume": 0, "live": event.get("live", False),
        "ml": None, "spreads": [], "totals": [], "btts": None,
        "event_id": event.get("id", ""), "slug": event.get("slug", ""),
    }
    
    total_vol = 0
    
    for mkt in markets:
        q = (mkt.get("question") or mkt.get("groupItemTitle") or "").lower()
        outcomes = _jp(mkt.get("outcomes", "[]"))
        prices_raw = _jp(mkt.get("outcomePrices", "[]"))
        tokens = _jp(mkt.get("clobTokenIds", "[]"))
        
        prices = []
        for p in prices_raw:
            try: prices.append(float(p))
            except: prices.append(0.0)
        
        try: total_vol += float(mkt.get("volume", 0) or mkt.get("volumeNum", 0) or 0)
        except: pass
        
        mid = mkt.get("id", "")
        if not prices or all(p == 0 for p in prices):
            continue
        
        # ---- MONEYLINE / WINNER ----
        if "moneyline" in q or "match result" in q or "winner" in q:
            ml = {"home": 0, "draw": 0, "away": 0, "id": mid,
                  "home_name": "", "away_name": "", "draw_name": ""}
            for i, o in enumerate(outcomes):
                name = str(o).strip()
                p = prices[i] if i < len(prices) else 0
                t = tokens[i] if i < len(tokens) else ""
                
                if name.lower() in ["draw", "x"]:
                    ml["draw"], ml["draw_tid"], ml["draw_name"] = p, t, name
                elif i == 0:
                    ml["home"], ml["home_tid"], ml["home_name"] = p, t, name
                    if not home: home = name; parsed["home"] = name; parsed["home_s"] = _abbreviate(name)
                elif i == 1 and len(outcomes) == 2:
                    # 2-outcome (no draw) — basketball, tennis, MMA
                    ml["away"], ml["away_tid"], ml["away_name"] = p, t, name
                    if not away: away = name; parsed["away"] = name; parsed["away_s"] = _abbreviate(name)
                elif i == 1 and len(outcomes) == 3:
                    # 3-outcome (with draw) — football
                    ml["draw"], ml["draw_tid"], ml["draw_name"] = p, t, name
                elif i == 2:
                    ml["away"], ml["away_tid"], ml["away_name"] = p, t, name
                    if not away: away = name; parsed["away"] = name; parsed["away_s"] = _abbreviate(name)
            
            if ml["home"] > 0 or ml["away"] > 0:
                parsed["ml"] = ml
        
        # ---- SPREAD / HANDICAP ----
        elif "spread" in q or "handicap" in q:
            m = re.search(r'[+-]?\d+\.?\d*', q)
            line = m.group() if m else ""
            sp = {"line": line, "home": 0, "away": 0, "id": mid}
            if len(prices) >= 2:
                sp["home"], sp["home_tid"] = prices[0], tokens[0] if tokens else ""
                sp["away"], sp["away_tid"] = prices[1], tokens[1] if len(tokens) > 1 else ""
            if sp["home"] > 0 or sp["away"] > 0:
                parsed["spreads"].append(sp)
        
        # ---- TOTALS / OVER-UNDER ----
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
        
        # ---- BTTS (football only) ----
        elif "both" in q and "score" in q:
            bt = {"yes": 0, "no": 0, "id": mid}
            for i, o in enumerate(outcomes):
                n = str(o).lower()
                if "yes" in n: bt["yes"], bt["yes_tid"] = prices[i], tokens[i] if i < len(tokens) else ""
                elif "no" in n: bt["no"], bt["no_tid"] = prices[i], tokens[i] if i < len(tokens) else ""
            if bt["yes"] == 0 and bt["no"] == 0 and len(prices) >= 2:
                bt["yes"], bt["yes_tid"] = prices[0], tokens[0] if tokens else ""
                bt["no"], bt["no_tid"] = prices[1], tokens[1] if len(tokens) > 1 else ""
            if bt["yes"] > 0 or bt["no"] > 0:
                parsed["btts"] = bt
        
        # ---- FALLBACK: unrecognized 2-3 outcome market ----
        elif not parsed["ml"] and len(outcomes) in [2, 3] and not any(k in q for k in ["prop", "mvp", "champion", "futures", "season", "award"]):
            ml = {"home": 0, "draw": 0, "away": 0, "id": mid,
                  "home_name": str(outcomes[0]) if outcomes else "",
                  "away_name": str(outcomes[-1]) if outcomes else "",
                  "draw_name": ""}
            if len(outcomes) == 2:
                ml["home"], ml["home_tid"] = prices[0], tokens[0] if tokens else ""
                ml["away"], ml["away_tid"] = (prices[1] if len(prices) > 1 else 0), (tokens[1] if len(tokens) > 1 else "")
            elif len(outcomes) == 3:
                ml["home"], ml["home_tid"] = prices[0], tokens[0] if tokens else ""
                ml["draw"], ml["draw_tid"], ml["draw_name"] = (prices[1] if len(prices) > 1 else 0), (tokens[1] if len(tokens) > 1 else ""), str(outcomes[1])
                ml["away"], ml["away_tid"] = (prices[2] if len(prices) > 2 else 0), (tokens[2] if len(tokens) > 2 else "")
            if ml["home"] > 0 or ml["away"] > 0:
                parsed["ml"] = ml
                if not home:
                    parsed["home"] = ml["home_name"]
                    parsed["home_s"] = _abbreviate(ml["home_name"])
                if not away:
                    parsed["away"] = ml["away_name"]
                    parsed["away_s"] = _abbreviate(ml["away_name"])
    
    parsed["volume"] = total_vol
    return parsed


def _detect_sport(event: dict) -> str:
    """Detect sport type from event tags or series."""
    # Check event's series against our LEAGUES
    series_slug = event.get("seriesSlug", "")
    for key, info in LEAGUES.items():
        if info["series"] == str(event.get("seriesId", "")):
            return info["sport"]
        if series_slug and key == series_slug:
            return info["sport"]
    return "soccer"  # default


def _extract_teams(title: str) -> tuple:
    for sep in [" vs ", " vs. ", " v ", " - "]:
        if sep in title.lower():
            idx = title.lower().index(sep)
            return title[:idx].strip(), title[idx+len(sep):].strip()
    if ":" in title:
        return _extract_teams(title.split(":", 1)[1].strip())
    return title, ""


def _abbreviate(name: str) -> str:
    """Create 3-letter abbreviation."""
    if not name:
        return "???"
    # Common abbreviations
    ABBR = {
        "chelsea": "CHE", "arsenal": "ARS", "liverpool": "LIV",
        "manchester city": "MCI", "manchester united": "MUN",
        "tottenham": "TOT", "lakers": "LAL", "celtics": "BOS",
        "warriors": "GSW", "grizzlies": "MEM", "knicks": "NYK",
        "76ers": "PHI", "bucks": "MIL", "heat": "MIA",
        "thunder": "OKC", "nuggets": "DEN", "cavaliers": "CLE",
        "mavericks": "DAL", "suns": "PHX", "clippers": "LAC",
        "nets": "BKN", "hawks": "ATL", "bulls": "CHI",
        "pistons": "DET", "pacers": "IND", "raptors": "TOR",
        "magic": "ORL", "kings": "SAC", "rockets": "HOU",
        "spurs": "SAS", "trail blazers": "POR", "timberwolves": "MIN",
        "pelicans": "NOP", "jazz": "UTA", "hornets": "CHA",
        "wizards": "WAS",
    }
    lower = name.lower().strip()
    for k, v in ABBR.items():
        if k in lower:
            return v
    # Fallback: first 3 letters
    clean = re.sub(r'[^a-zA-Z]', '', name)
    return clean[:3].upper() if clean else "???"


# =============================================================================
# FORMAT MATCH — SPORT-AWARE DISPLAY
# =============================================================================

def format_match(g: dict, currency: str = "USD") -> str:
    sport = g.get("sport", "soccer")
    labels = SPORT_LABELS.get(sport, SPORT_LABELS["soccer"])
    
    lines = []
    
    # Title
    if g["home"] and g["away"]:
        lines.append(f"<b>{g['home']}</b>")
        lines.append(f"        <i>vs</i>")
        lines.append(f"<b>{g['away']}</b>")
    else:
        lines.append(f"<b>{g['title']}</b>")
    
    lines.append("━━━━━━━━━━━━━━━━━━━━━━")
    
    # Time + status (UTC+1 / WAT, 12hr format)
    date_str, time_str = fmt_time(g.get("time", ""))
    parts = []
    if date_str:
        parts.append(f"📅 <b>{date_str}</b>")
    if time_str:
        parts.append(f"⏰ <b>{time_str}</b> (WAT)")
    if g.get("live"):
        parts.append("🔴 <b>LIVE</b>")
    if parts:
        lines.append("  ".join(parts))
    lines.append("")
    
    # ── MONEYLINE / WINNER ──
    if g["ml"]:
        ml = g["ml"]
        h_odds = fmt_odds(ml["home"])
        a_odds = fmt_odds(ml["away"])
        h_name = ml.get("home_name") or g.get("home", "Home")
        a_name = ml.get("away_name") or g.get("away", "Away")
        
        lines.append(f"┌─── <b>{labels['ml']}</b> ───┐")
        lines.append(f"│")
        lines.append(f"│  <b>1</b>  {h_name[:14]:<14}  <code>{h_odds:>6}</code>")
        
        if labels["has_draw"] and ml.get("draw", 0) > 0:
            d_odds = fmt_odds(ml["draw"])
            lines.append(f"│  <b>X</b>  Draw            <code>{d_odds:>6}</code>")
        
        lines.append(f"│  <b>2</b>  {a_name[:14]:<14}  <code>{a_odds:>6}</code>")
        lines.append(f"│")
        lines.append(f"└{'─' * 30}┘")
        lines.append("")
    
    # ── TOTALS / OVER-UNDER ──
    if g["totals"]:
        # Pick the best line (not 2.5 for basketball!)
        main = _pick_main_total(g["totals"], sport)
        ov = fmt_odds(main["over"])
        un = fmt_odds(main["under"])
        
        lines.append(f"┌─── <b>{labels['total']} O/U {main['line']}</b> ───┐")
        lines.append(f"│")
        lines.append(f"│  ⬆  Over            <code>{ov:>6}</code>")
        lines.append(f"│  ⬇  Under           <code>{un:>6}</code>")
        lines.append(f"│")
        lines.append(f"└{'─' * 30}┘")
        
        if len(g["totals"]) > 1:
            other = [t["line"] for t in g["totals"] if t != main][:4]
            if other:
                lines.append(f"  <i>More lines: {', '.join(other)}</i>")
        lines.append("")
    
    # ── SPREAD / HANDICAP ──
    if g["spreads"]:
        sp = g["spreads"][0]
        h_odds = fmt_odds(sp["home"])
        a_odds = fmt_odds(sp["away"])
        spread_label = "Handicap" if sport == "soccer" else "Spread"
        
        lines.append(f"┌─── <b>{spread_label} ({sp['line']})</b> ───┐")
        lines.append(f"│")
        lines.append(f"│  H1  {g.get('home','Home')[:12]:<12}    <code>{h_odds:>6}</code>")
        lines.append(f"│  H2  {g.get('away','Away')[:12]:<12}    <code>{a_odds:>6}</code>")
        lines.append(f"│")
        lines.append(f"└{'─' * 30}┘")
        lines.append("")
    
    # ── BTTS (football only) ──
    if g["btts"] and sport == "soccer":
        y = fmt_odds(g["btts"]["yes"])
        n = fmt_odds(g["btts"]["no"])
        lines.append(f"┌─── <b>Both Teams to Score</b> ───┐")
        lines.append(f"│")
        lines.append(f"│  ✅  GG (Yes)        <code>{y:>6}</code>")
        lines.append(f"│  ❌  NG (No)         <code>{n:>6}</code>")
        lines.append(f"│")
        lines.append(f"└{'─' * 30}┘")
        lines.append("")
    
    # Volume
    if g.get("volume"):
        try:
            lines.append(f"💰 Volume: {fmt_currency(float(g['volume']), currency)}")
        except: pass
    
    lines.append("")
    lines.append("👇 <i>Tap an odds button below to bet</i>")
    return "\n".join(lines)


def _pick_main_total(totals: list, sport: str) -> dict:
    """Pick the most relevant O/U line for the sport."""
    if not totals:
        return totals[0] if totals else {}
    
    # For basketball/football: pick the highest line (200+)
    # For soccer: pick 2.5 or 3.5
    if sport in ("basketball", "american_football"):
        # Pick the line closest to typical game total
        best = totals[0]
        for t in totals:
            try:
                line = float(t["line"])
                if line > 100:  # Basketball-range total
                    best = t
                    break
            except: pass
        return best
    
    elif sport == "hockey":
        for t in totals:
            if str(t["line"]) in ["5.5", "6.5"]:
                return t
        return totals[0]
    
    else:  # soccer default
        for t in totals:
            if str(t["line"]) in ["2.5", "3.5"]:
                return t
        return totals[0]
