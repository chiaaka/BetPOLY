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

def _deduplicate_events(events: list) -> list:
    """
    Merge duplicate events (e.g. "Team A vs Team B" and "Team A vs Team B - More Markets").
    Combines all markets from both into a single event.
    """
    def _clean_title(t: str) -> str:
        for suffix in [" - More Markets", " - Game Lines", " - Props", " - Specials"]:
            t = t.replace(suffix, "")
        return t.strip().lower()
    
    merged = {}
    order = []
    for ev in events:
        key = _clean_title(ev.get("title", ""))
        if key in merged:
            # Merge markets into existing event
            existing = merged[key]
            existing_markets = existing.get("markets", [])
            new_markets = ev.get("markets", [])
            # Add markets that aren't already present (by question)
            existing_qs = {m.get("question", "").lower() for m in existing_markets}
            for m in new_markets:
                if m.get("question", "").lower() not in existing_qs:
                    existing_markets.append(m)
            existing["markets"] = existing_markets
        else:
            merged[key] = ev
            order.append(key)
    
    return [merged[k] for k in order]


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
                    # Filter out expired events
                    data = [ev for ev in data if not _is_expired(ev)]
                    
                    # Deduplicate: merge "More Markets" events into main event
                    data = _deduplicate_events(data)
                    
                    # Log ALL markets in first event for debugging
                    if data:
                        ev0 = data[0]
                        mkts = ev0.get("markets", [])
                        logger.info(f"  Event[0]: title={ev0.get('title','')[:60]} markets={len(mkts)}")
                        for j, m in enumerate(mkts[:8]):
                            q = m.get("question", "")[:55]
                            o = m.get("outcomes", "")[:50]
                            p = m.get("outcomePrices", "")[:50]
                            logger.info(f"    Mkt[{j}]: q={q} | o={o} | p={p}")
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

def _is_expired(event: dict) -> bool:
    """Check if event is in the past (skip old matches)."""
    t = event.get("startTime") or event.get("endDate", "")
    if not t:
        return False
    try:
        from datetime import timezone, timedelta
        wat = timezone(timedelta(hours=1))
        event_time = datetime.fromisoformat(t.replace("Z", "+00:00"))
        now = datetime.now(timezone.utc)
        # If event started more than 6 hours ago, consider it expired
        return (now - event_time).total_seconds() > 6 * 3600
    except:
        return False


def _price_valid(p: float) -> bool:
    """Check if price is tradeable (not settled at exactly 0 or 1)."""
    return 0.01 <= p <= 0.99


def parse_event(event: dict) -> dict:
    """
    Parse Polymarket event into unified betting structure.
    
    REAL Polymarket formats discovered from logs:
    
    FOOTBALL (La Liga etc) — 3 SEPARATE Yes/No markets:
      Market[0]: q="Will Levante UD win on 2025-12-14?" o=["Yes","No"] p=["0.24","0.76"]
      Market[1]: q="Will ... end in a draw?"             o=["Yes","No"] p=["0.31","0.69"]
      Market[2]: q="Will Villarreal CF win on ...?"       o=["Yes","No"] p=["0.52","0.48"]
    
    ESPORTS (CS2) — Single 2-outcome market:
      Market[0]: q="Counter-Strike: ex-GANK vs 777" o=["ex-GANK","777"] p=["0.505","0.495"]
    
    TENNIS — Has O/U markets + match market:
      Market[0]: q="Kopriva vs. Struff: Moneyline"   o=["Kopriva","Struff"] p=[...]
      Market[4]: q="Kopriva vs. Struff: Set 1 O/U 8.5" o=["Over","Under"] p=[...]
    
    NBA — Moneyline + Spread + Total Points:
      Market[0]: q="Grizzlies vs Lakers: Moneyline" o=["Grizzlies","Lakers"] p=[...]
    """
    title = event.get("title", "")
    markets = event.get("markets", [])
    home, away = _extract_teams(title)
    
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
    
    # First pass: collect all market data
    home_win_price = 0
    home_win_tid = ""
    away_win_price = 0
    away_win_tid = ""
    draw_price = 0
    draw_tid = ""
    home_win_name = home or ""
    away_win_name = away or ""
    
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
        
        # Skip markets with dead/settled prices
        if all(not _price_valid(p) for p in prices):
            continue
        
        outcome_names = [str(o).lower().strip() for o in outcomes]
        
        # ==============================================
        # PATTERN 1: "Will X win?" Yes/No (Football-style)
        # Also catches: "Will X win on DATE?"
        # ==============================================
        if "win" in q and len(outcomes) == 2 and outcome_names == ["yes", "no"]:
            yes_price = prices[0] if prices else 0
            yes_tid = tokens[0] if tokens else ""
            if _price_valid(yes_price):
                # Determine if this is home or away
                # Strategy: check full team name first, then unique words
                is_home = False
                is_away = False
                
                if home and away:
                    # Full name substring match (most reliable)
                    home_lower = home.lower()
                    away_lower = away.lower()
                    
                    # Check for full name or distinctive part
                    if home_lower in q:
                        is_home = True
                    elif away_lower in q:
                        is_away = True
                    else:
                        # Use words UNIQUE to each team (not shared)
                        home_words = set(w for w in home_lower.split() if len(w) > 2)
                        away_words = set(w for w in away_lower.split() if len(w) > 2)
                        unique_home = home_words - away_words  # words only in home
                        unique_away = away_words - home_words  # words only in away
                        
                        if unique_home and any(w in q for w in unique_home):
                            is_home = True
                        elif unique_away and any(w in q for w in unique_away):
                            is_away = True
                        elif not home_win_price:
                            is_home = True  # default first to home
                        else:
                            is_away = True  # second to away
                elif home:
                    home_lower = home.lower()
                    if home_lower in q or any(w in q for w in home_lower.split() if len(w) > 3):
                        is_home = True
                    elif not home_win_price:
                        is_home = True
                    else:
                        is_away = True
                
                if is_home and not home_win_price:
                    home_win_price = yes_price
                    home_win_tid = yes_tid
                    name = _extract_team_from_q(q)
                    if name: home_win_name = name
                elif is_away and not away_win_price:
                    away_win_price = yes_price
                    away_win_tid = yes_tid
                    name = _extract_team_from_q(q)
                    if name: away_win_name = name
                elif not home_win_price:
                    home_win_price = yes_price
                    home_win_tid = yes_tid
                    name = _extract_team_from_q(q)
                    if name: home_win_name = name
                elif not away_win_price:
                    away_win_price = yes_price
                    away_win_tid = yes_tid
                    name = _extract_team_from_q(q)
                    if name: away_win_name = name
        
        # ==============================================
        # PATTERN 2: "Will ... end in a draw?" Yes/No
        # ==============================================
        elif "draw" in q and len(outcomes) == 2 and outcome_names == ["yes", "no"]:
            yes_price = prices[0] if prices else 0
            if _price_valid(yes_price):
                draw_price = yes_price
                draw_tid = tokens[0] if tokens else ""
        
        # ==============================================
        # PATTERN 3: Moneyline — 2 or 3 team outcomes
        # ==============================================
        elif "moneyline" in q or "match result" in q or "winner" in q:
            if len(outcomes) == 2 and _price_valid(prices[0] if prices else 0):
                home_win_price = prices[0]
                home_win_tid = tokens[0] if tokens else ""
                home_win_name = str(outcomes[0])
                away_win_price = prices[1] if len(prices) > 1 else 0
                away_win_tid = tokens[1] if len(tokens) > 1 else ""
                away_win_name = str(outcomes[1]) if len(outcomes) > 1 else ""
            elif len(outcomes) == 3:
                home_win_price = prices[0]
                home_win_tid = tokens[0] if tokens else ""
                home_win_name = str(outcomes[0])
                draw_price = prices[1] if len(prices) > 1 else 0
                draw_tid = tokens[1] if len(tokens) > 1 else ""
                away_win_price = prices[2] if len(prices) > 2 else 0
                away_win_tid = tokens[2] if len(tokens) > 2 else ""
                away_win_name = str(outcomes[2]) if len(outcomes) > 2 else ""
        
        # ==============================================
        # SPREAD / HANDICAP
        # ==============================================
        elif "spread" in q or "handicap" in q:
            m = re.search(r'[+-]?\d+\.?\d*', q)
            line = m.group() if m else ""
            if len(prices) >= 2 and any(_price_valid(p) for p in prices[:2]):
                sp = {"line": line, "home": prices[0], "away": prices[1], "id": mid,
                      "home_tid": tokens[0] if tokens else "",
                      "away_tid": tokens[1] if len(tokens) > 1 else ""}
                parsed["spreads"].append(sp)
        
        # ==============================================
        # TOTALS / OVER-UNDER
        # ==============================================
        elif "o/u" in q or "over" in q or "under" in q or "total" in q:
            m = re.search(r'(\d+\.?\d*)', q)
            line = m.group(1) if m else ""
            tot = {"line": line, "over": 0, "under": 0, "id": mid}
            for i, o in enumerate(outcomes):
                n = str(o).lower()
                p = prices[i] if i < len(prices) else 0
                t = tokens[i] if i < len(tokens) else ""
                if "over" in n or (i == 0 and "yes" in n):
                    tot["over"], tot["over_tid"] = p, t
                elif "under" in n or (i == 1 and "no" in n):
                    tot["under"], tot["under_tid"] = p, t
            if tot["over"] == 0 and tot["under"] == 0 and len(prices) >= 2:
                tot["over"], tot["over_tid"] = prices[0], tokens[0] if tokens else ""
                tot["under"], tot["under_tid"] = prices[1], tokens[1] if len(tokens) > 1 else ""
            if _price_valid(tot["over"]) or _price_valid(tot["under"]):
                parsed["totals"].append(tot)
        
        # ==============================================
        # BOTH TEAMS TO SCORE
        # ==============================================
        elif "both" in q and "score" in q:
            if len(prices) >= 2 and _price_valid(prices[0]):
                parsed["btts"] = {
                    "yes": prices[0], "yes_tid": tokens[0] if tokens else "",
                    "no": prices[1] if len(prices) > 1 else 0,
                    "no_tid": tokens[1] if len(tokens) > 1 else "", "id": mid
                }
        
        # ==============================================
        # FALLBACK: 2-outcome market with team names (Esports, generic)
        # ==============================================
        elif not home_win_price and len(outcomes) == 2 and \
             outcome_names != ["yes", "no"] and \
             not any(k in q for k in ["prop", "mvp", "champion", "futures", "season", "award", "o/u", "over", "under"]):
            if _price_valid(prices[0] if prices else 0):
                home_win_price = prices[0]
                home_win_tid = tokens[0] if tokens else ""
                home_win_name = str(outcomes[0])
                away_win_price = prices[1] if len(prices) > 1 else 0
                away_win_tid = tokens[1] if len(tokens) > 1 else ""
                away_win_name = str(outcomes[1]) if len(outcomes) > 1 else ""
    
    # Build moneyline from collected data
    if home_win_price > 0 or away_win_price > 0:
        parsed["ml"] = {
            "home": home_win_price, "home_tid": home_win_tid, "home_name": home_win_name,
            "away": away_win_price, "away_tid": away_win_tid, "away_name": away_win_name,
            "draw": draw_price, "draw_tid": draw_tid, "draw_name": "Draw",
        }
        if not parsed["home"] and home_win_name:
            parsed["home"] = home_win_name
            parsed["home_s"] = _abbreviate(home_win_name)
        if not parsed["away"] and away_win_name:
            parsed["away"] = away_win_name
            parsed["away_s"] = _abbreviate(away_win_name)
    
    # ==============================================
    # LAST RESORT: If we have other markets but NO moneyline,
    # scan ALL markets again looking for ANY 2-outcome non-O/U market
    # ==============================================
    if not parsed["ml"]:
        for mkt in markets:
            q = (mkt.get("question") or "").lower()
            outcomes = _jp(mkt.get("outcomes", "[]"))
            prices_raw = _jp(mkt.get("outcomePrices", "[]"))
            tokens = _jp(mkt.get("clobTokenIds", "[]"))
            prices = []
            for p in prices_raw:
                try: prices.append(float(p))
                except: prices.append(0.0)
            
            outcome_names = [str(o).lower().strip() for o in outcomes]
            
            # Skip O/U, props, futures
            if any(k in q for k in ["o/u", "over", "under", "total", "prop", "mvp",
                                      "champion", "futures", "season", "award", "set 1",
                                      "half", "first", "second"]):
                continue
            
            # Any 2-outcome market with valid prices and non-Yes/No outcomes = moneyline
            if len(outcomes) == 2 and outcome_names != ["yes", "no"]:
                if len(prices) >= 2 and (_price_valid(prices[0]) or _price_valid(prices[1])):
                    parsed["ml"] = {
                        "home": prices[0], "home_tid": tokens[0] if tokens else "",
                        "home_name": str(outcomes[0]),
                        "away": prices[1] if len(prices) > 1 else 0,
                        "away_tid": tokens[1] if len(tokens) > 1 else "",
                        "away_name": str(outcomes[1]) if len(outcomes) > 1 else "",
                        "draw": 0, "draw_tid": "", "draw_name": "Draw",
                    }
                    if not parsed["home"]:
                        parsed["home"] = str(outcomes[0])
                        parsed["home_s"] = _abbreviate(str(outcomes[0]))
                    if not parsed["away"]:
                        parsed["away"] = str(outcomes[1]) if len(outcomes) > 1 else ""
                        parsed["away_s"] = _abbreviate(str(outcomes[1])) if len(outcomes) > 1 else "???"
                    logger.info(f"  FALLBACK ML: {outcomes[0]} @ {prices[0]} vs {outcomes[1]} @ {prices[1]}")
                    break
            
            # 2-outcome Yes/No that looks like a winner (not draw, not BTTS)
            if len(outcomes) == 2 and outcome_names == ["yes", "no"] and \
               "draw" not in q and "both" not in q and "score" not in q:
                if len(prices) >= 1 and _price_valid(prices[0]):
                    # Use this as one side; need to check if it's the only one
                    if not home_win_price and not away_win_price:
                        parsed["ml"] = {
                            "home": prices[0], "home_tid": tokens[0] if tokens else "",
                            "home_name": _extract_team_from_q(q) or parsed.get("home", "Home"),
                            "away": 1.0 - prices[0] if prices[0] < 1 else 0,
                            "away_tid": tokens[1] if len(tokens) > 1 else "",
                            "away_name": parsed.get("away", "Away"),
                            "draw": 0, "draw_tid": "", "draw_name": "Draw",
                        }
                        logger.info(f"  FALLBACK YES/NO ML: q={q[:40]} price={prices[0]}")
                        break
    
    parsed["volume"] = total_vol
    return parsed


def _extract_team_from_q(q: str) -> str:
    """Extract team name from 'Will TEAM win on DATE?' pattern."""
    q = q.strip()
    if q.startswith("will "):
        q = q[5:]
    # Remove "win on ..." or "win?"
    m = re.search(r'^(.+?)\s+win', q, re.IGNORECASE)
    if m:
        return m.group(1).strip().title()
    return ""


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
    # Strip common Polymarket suffixes
    for suffix in [" - More Markets", " - Game Lines", " - Props", " - Specials"]:
        if suffix in title:
            title = title.split(suffix)[0].strip()
    
    # Strip esports game prefixes
    for prefix in ["Counter-Strike: ", "CS2: ", "League of Legends: ", "LoL: ", 
                    "Valorant: ", "Dota 2: ", "DOTA2: "]:
        if title.startswith(prefix):
            title = title[len(prefix):]
    
    for sep in [" vs ", " vs. ", " v "]:
        if sep in title.lower():
            idx = title.lower().index(sep)
            home = title[:idx].strip()
            away = title[idx+len(sep):].strip()
            # Clean any remaining suffixes after away team
            for s in [" -", " ("]:
                if s in away:
                    away = away.split(s)[0].strip()
            return home, away
    if ":" in title:
        return _extract_teams(title.split(":", 1)[1].strip())
    if " - " in title:
        parts = title.split(" - ", 1)
        return parts[0].strip(), parts[1].strip()
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
