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
    Merge duplicate events (e.g. "Team A vs Team B" and "Team A vs Team B - Total Corners").
    Combines all markets from both into a single event.
    
    IMPORTANT: Player Props events are EXCLUDED — their markets (scorer props, etc.)
    would corrupt the moneyline/totals parsing for the main match.
    """
    # Suffixes that indicate player prop / special events we should SKIP entirely
    SKIP_SUFFIXES = [" - Player Props", " - Props", " - Specials", " - Prop Bets",
                     " - Anytime Scorer", " - Goalscorer"]
    
    def _should_skip(title: str) -> bool:
        t_lower = title.lower()
        for s in SKIP_SUFFIXES:
            if s.lower() in t_lower:
                return True
        # Also skip if title contains prop-like keywords after the dash
        if " - " in title:
            suffix_part = title.split(" - ", 1)[1].lower()
            if any(k in suffix_part for k in ["prop", "scorer", "goal", "card", "corner",
                                               "booking", "assist", "shot", "foul"]):
                return True
        return False
    
    def _clean_title(t: str) -> str:
        # Strip everything after " - " if it's a sub-market suffix
        if " - " in t:
            base = t.split(" - ")[0].strip()
            # Only strip if the base looks like a match (contains vs/vs.)
            if any(sep in base.lower() for sep in [" vs ", " vs. ", " v "]):
                return base.lower()
        # Also handle known suffixes without the dash pattern
        for suffix in [" - More Markets", " - Game Lines"]:
            t = t.replace(suffix, "")
        return t.strip().lower()
    
    merged = {}
    order = []
    for ev in events:
        title = ev.get("title", "")
        
        # Skip player prop events entirely
        if _should_skip(title):
            logger.info(f"  dedup: SKIPPING prop event: {title[:60]}")
            continue
        
        key = _clean_title(title)
        if key in merged:
            # Merge markets into existing event, but filter out prop-like markets
            existing = merged[key]
            existing_markets = existing.get("markets", [])
            new_markets = ev.get("markets", [])
            existing_qs = {m.get("question", "").lower() for m in existing_markets}
            for m in new_markets:
                mq = (m.get("question") or "").lower()
                # Skip individual prop markets being merged in
                if any(k in mq for k in ["score", "scorer", "goal", "card", "booking",
                                          "assist", "shot", "foul", "corner", "penalty",
                                          "anytime", "first to", "last to", "hat trick",
                                          "man of the match", "mvp"]):
                    continue
                if mq not in existing_qs:
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
    params = {"active": "true", "closed": "false", "live": "true", "tag_id": GAME_TAG, "limit": "30"}
    async with httpx.AsyncClient(timeout=15) as client:
        try:
            r = await client.get(f"{GAMMA_API}/events", params=params)
            logger.info(f"fetch_live: {r.status_code}")
            if r.status_code == 200:
                data = r.json()
                if not isinstance(data, list):
                    return []
                # Deduplicate: keep first event per team pair
                seen = set()
                unique = []
                for ev in data:
                    title = ev.get("title", "")
                    # Extract base match name (before " - " suffix like "Total Corners")
                    base = title.split(" - ")[0].strip() if " - " in title else title
                    key = base.lower()
                    if key not in seen:
                        seen.add(key)
                        unique.append(ev)
                return unique
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
    
    # Extract tournament/event prefix if present (e.g. "Rotterdam Open" from "Rotterdam Open: X vs Y")
    tournament = ""
    if ": " in title:
        after_colon = title.split(": ", 1)[1]
        if any(sep in after_colon.lower() for sep in [" vs ", " vs. ", " v "]):
            tournament = title.split(": ", 1)[0].strip()
    
    sport = _detect_sport(event)
    logger.info(f"  parse_event: title={title[:40]} sport={sport} seriesId={event.get('seriesId','?')}")
    
    parsed = {
        "title": title, "home": home, "away": away,
        "home_s": _abbreviate(home), "away_s": _abbreviate(away),
        "tournament": tournament,
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
    
    # LOG Mkt[0] for debugging — what is the first market?
    if markets:
        mkt0_q = (markets[0].get("question") or markets[0].get("groupItemTitle") or "")
        mkt0_oc = markets[0].get("outcomes", "[]")
        logger.info(f"  Mkt[0] q={mkt0_q[:80]} | oc={str(mkt0_oc)[:60]}")
    
    # ══════════════════════════════════════════════════════════════
    # PRE-LOOP: For non-soccer sports, extract ML directly from Mkt[0]
    # Polymarket ALWAYS puts the match winner as the first market.
    # This bypasses all pattern matching for tennis, MMA, basketball, esports.
    # Soccer uses a different structure (separate Yes/No markets per team + draw)
    # so it goes through the full loop below.
    # ══════════════════════════════════════════════════════════════
    if sport != "soccer" and markets:
        mkt0 = markets[0]
        oc0 = _jp(mkt0.get("outcomes", "[]"))
        pr0 = _jp(mkt0.get("outcomePrices", "[]"))
        tk0 = _jp(mkt0.get("clobTokenIds", "[]"))
        prices0 = []
        for p in pr0:
            try: prices0.append(float(p))
            except: prices0.append(0.0)
        oc_names0 = [str(o).lower().strip() for o in oc0]
        
        # Mkt[0] should have 2 outcomes with player/team names (not Yes/No)
        if len(oc0) == 2 and oc_names0 != ["yes", "no"] and            len(prices0) >= 2 and (_price_valid(prices0[0]) or _price_valid(prices0[1])):
            home_win_name = str(oc0[0])
            home_win_price = prices0[0]
            home_win_tid = tk0[0] if tk0 else ""
            away_win_name = str(oc0[1]) if len(oc0) > 1 else ""
            away_win_price = prices0[1] if len(prices0) > 1 else 0
            away_win_tid = tk0[1] if len(tk0) > 1 else ""
            logger.info(f"  ML from Mkt[0] ({sport}): {home_win_name}@{home_win_price} vs {away_win_name}@{away_win_price}")
    
    for mkt_idx, mkt in enumerate(markets):
        q = (mkt.get("question") or mkt.get("groupItemTitle") or "").lower()
        outcomes = _jp(mkt.get("outcomes", "[]"))
        prices_raw = _jp(mkt.get("outcomePrices", "[]"))
        tokens = _jp(mkt.get("clobTokenIds", "[]"))
        
        prices = []
        for p in prices_raw:
            try: prices.append(float(p))
            except: prices.append(0.0)
        
        # RAW DUMP: Log every market so we can trace misplacements
        logger.debug(f"  RAW_MKT: q='{q[:80]}' outcomes={outcomes} prices={prices[:4]}")
        
        try: total_vol += float(mkt.get("volume", 0) or mkt.get("volumeNum", 0) or 0)
        except: pass
        
        mid = mkt.get("id", "")
        if not prices or all(p == 0 for p in prices):
            continue
        
        # Skip markets with dead/settled prices
        if all(not _price_valid(p) for p in prices):
            continue
        
        # Skip player prop markets — these corrupt moneyline parsing
        if any(k in q for k in ["score a goal", "to score", "scorer", "anytime",
                                  "first goal", "last goal", "hat trick",
                                  "booking", "card", "assist", "shot on target",
                                  "foul", "man of the match", "mvp",
                                  "corner", "penalty", "clean sheet",
                                  "player", "prop"]):
            continue
        
        outcome_names = [str(o).lower().strip() for o in outcomes]
        
        # ==============================================
        # PATTERN 1: "Will X win?" Yes/No (Football-style)
        # Each separate market = one team's win probability
        # The team name comes from the question text (API data)
        # outcomes[0]="Yes" with clobTokenIds[0] = token for YES
        # NO NAME GUESSING — we extract the name from the question
        # and assign to first empty slot (home_win or away_win)
        # ==============================================
        if "win" in q and len(outcomes) == 2 and outcome_names == ["yes", "no"]:
            yes_price = prices[0] if prices else 0
            yes_tid = tokens[0] if tokens else ""
            if _price_valid(yes_price):
                team_name = _extract_team_from_q(q) or f"Team ({mid[:8]})"
                
                # Assign to first empty slot — order is determined by
                # which market the API returns first, which is fine
                if not home_win_price:
                    home_win_price = yes_price
                    home_win_tid = yes_tid
                    home_win_name = team_name
                    logger.info(f"  P1 slot1: '{team_name}' @ {yes_price} (tid={yes_tid[:20]}...)")
                elif not away_win_price:
                    away_win_price = yes_price
                    away_win_tid = yes_tid
                    away_win_name = team_name
                    logger.info(f"  P1 slot2: '{team_name}' @ {yes_price} (tid={yes_tid[:20]}...)")
                else:
                    # Both slots full — skip (might be a 3rd "win" market from props)
                    logger.warning(f"  P1 SKIP extra win market: '{team_name}' @ {yes_price}")
        
        # ==============================================
        # PATTERN 2: "Will ... end in a draw?" Yes/No
        # ONLY fulltime draw — skip halftime/first half/second half
        # ==============================================
        elif "draw" in q and len(outcomes) == 2 and outcome_names == ["yes", "no"]:
            # Skip halftime / first half / second half draws
            is_halftime = any(k in q for k in ["half time", "halftime", "half-time",
                                                 "first half", "1st half", "second half",
                                                 "2nd half", "ht ", "h/t"])
            if not is_halftime:
                yes_price = prices[0] if prices else 0
                if _price_valid(yes_price):
                    draw_price = yes_price
                    draw_tid = tokens[0] if tokens else ""
                    logger.info(f"  Draw (fulltime): q='{q[:50]}' price={draw_price}")
            else:
                logger.info(f"  Draw SKIPPED (halftime): q='{q[:50]}'")
        
        # ==============================================
        # PATTERN 3: Moneyline — 2 or 3 team outcomes
        # "Moneyline", "Match Result", "Winner" markets
        # USE API DATA DIRECTLY — no name guessing
        # outcomes[i] ↔ clobTokenIds[i] ↔ outcomePrices[i]
        # ==============================================
        elif "moneyline" in q or "match result" in q or "winner" in q:
            if len(outcomes) >= 2 and _price_valid(prices[0] if prices else 0):
                # Separate draw from team outcomes, preserving API order
                draw_oc = None
                team_ocs = []
                for idx, oc_name in enumerate(outcomes):
                    entry = {
                        "name": str(oc_name),
                        "price": prices[idx] if idx < len(prices) else 0,
                        "tid": tokens[idx] if idx < len(tokens) else "",
                    }
                    if str(oc_name).lower().strip() in ("draw", "tie"):
                        draw_oc = entry
                    else:
                        team_ocs.append(entry)
                
                if draw_oc and _price_valid(draw_oc["price"]):
                    draw_price = draw_oc["price"]
                    draw_tid = draw_oc["tid"]
                
                # Assign first non-draw outcome as "home", second as "away"
                # Names come DIRECTLY from the API — no interpretation
                if len(team_ocs) >= 1 and _price_valid(team_ocs[0]["price"]):
                    home_win_price = team_ocs[0]["price"]
                    home_win_tid = team_ocs[0]["tid"]
                    home_win_name = team_ocs[0]["name"]
                if len(team_ocs) >= 2 and _price_valid(team_ocs[1]["price"]):
                    away_win_price = team_ocs[1]["price"]
                    away_win_tid = team_ocs[1]["tid"]
                    away_win_name = team_ocs[1]["name"]
                
                logger.info(f"  ML from API: outcomes={[oc['name'] for oc in team_ocs]} "
                           f"home='{home_win_name}'@{home_win_price} "
                           f"away='{away_win_name}'@{away_win_price} draw={draw_price}")
        
        # ==============================================
        # SPREAD / HANDICAP
        # Polymarket has separate markets: "Spread: Team A (-1.5)" and "Spread: Team B (-1.5)"
        # We combine them into one spread entry with home(-) and away(+)
        # ==============================================
        elif "spread" in q or "handicap" in q:
            m = re.search(r'[+-]?\d+\.?\d*', q)
            line_val = m.group() if m else ""
            # Clean line to always be positive number
            line_num = line_val.lstrip("-+") if line_val else ""
            
            if len(prices) >= 2 and any(_price_valid(p) for p in prices[:2]):
                # USE API DATA DIRECTLY
                # outcomes[0] = favored team name, outcomes[1] = underdog
                # prices[0] = fav price, prices[1] = dog price
                # No name guessing — just use API order
                fav_name = str(outcomes[0]) if outcomes else ""
                dog_name = str(outcomes[1]) if len(outcomes) > 1 else ""
                fav_price = prices[0]
                dog_price = prices[1] if len(prices) > 1 else 0
                fav_tid = tokens[0] if tokens else ""
                dog_tid = tokens[1] if len(tokens) > 1 else ""
                
                # Check if we already have a spread with this line
                existing_sp = None
                for sp in parsed["spreads"]:
                    if sp.get("line_num") == line_num:
                        existing_sp = sp
                        break
                
                if existing_sp is None:
                    # Store as home=fav, away=dog (API order)
                    # Button display will use the stored names
                    sp = {
                        "line_num": line_num, "id": mid,
                        "home": fav_price, "home_tid": fav_tid,
                        "home_name": fav_name,
                        "away": dog_price, "away_tid": dog_tid,
                        "away_name": dog_name,
                        "home_line": f"-{line_num}",
                        "away_line": f"+{line_num}",
                        "line": f"-{line_num}",
                    }
                    parsed["spreads"].append(sp)
                    logger.info(f"  Spread: {fav_name} -{line_num} @ {fav_price} vs {dog_name} +{line_num} @ {dog_price}")
        
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
        # FALLBACK: 2-outcome market with team names
        # DEFINITIVE FIX: Only accept the market whose question matches 
        # the event title. The ML market question always equals the event title.
        # "Set 1 Winner: X vs Y" never matches "BNP Paribas Open: X vs Y"
        # ==============================================
        elif not home_win_price and len(outcomes) == 2 and \
             outcome_names != ["yes", "no"]:
            if _price_valid(prices[0] if prices else 0):
                q_raw = (mkt.get("question") or mkt.get("groupItemTitle") or "").strip()
                title_raw = title.strip()
                
                # The ML market question matches the event title exactly or very closely
                # Compare normalized: lowercase, strip "- More Markets" suffix
                q_norm = q_raw.lower().replace(" - more markets", "").strip()
                t_norm = title_raw.lower().replace(" - more markets", "").strip()
                
                is_title_match = (q_norm == t_norm)
                is_first_market = (mkt_idx == 0)
                
                # For Mkt[0]: also accept if question contains the full event title
                if not is_title_match and is_first_market:
                    is_title_match = (t_norm in q_norm) or (q_norm in t_norm)
                
                if is_title_match:
                    home_win_price = prices[0]
                    home_win_tid = tokens[0] if tokens else ""
                    home_win_name = str(outcomes[0])
                    away_win_price = prices[1] if len(prices) > 1 else 0
                    away_win_tid = tokens[1] if len(tokens) > 1 else ""
                    away_win_name = str(outcomes[1]) if len(outcomes) > 1 else ""
                    logger.info(f"  ML from API: outcomes={[str(o) for o in outcomes]} "
                                f"home=\'{home_win_name}\'@{home_win_price} away=\'{away_win_name}\'@{away_win_price} draw=0")
                else:
                    logger.info(f"  SKIP non-title-match: q=\'{q_raw[:50]}\' != title=\'{title_raw[:50]}\'")
    
    # Build moneyline from collected data
    if home_win_price > 0 or away_win_price > 0:
        # LOG RAW DATA: So we can debug any misplacement from Railway logs
        logger.info(f"  ML_BUILD: home_name='{home_win_name}' home_price={home_win_price} "
                     f"away_name='{away_win_name}' away_price={away_win_price} "
                     f"draw_price={draw_price} title='{title}'"
                     )
        logger.info(f"  ML_TOKENS: home_tid={home_win_tid[:20] if home_win_tid else 'NONE'}... "
                     f"away_tid={away_win_tid[:20] if away_win_tid else 'NONE'}...")
        
        # SAFETY: If "draw" or "tie" leaked into home or away slot, fix it
        if home_win_name and home_win_name.lower().strip() in ("draw", "tie"):
            logger.warning(f"  SAFETY: Draw leaked into HOME slot — moving to draw, clearing home")
            if not draw_price:
                draw_price = home_win_price
                draw_tid = home_win_tid
            home_win_price = 0
            home_win_tid = ""
            home_win_name = ""
        if away_win_name and away_win_name.lower().strip() in ("draw", "tie"):
            logger.warning(f"  SAFETY: Draw leaked into AWAY slot — moving to draw, clearing away")
            if not draw_price:
                draw_price = away_win_price
                draw_tid = away_win_tid
            away_win_price = 0
            away_win_tid = ""
            away_win_name = ""
        
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
        # LAST RESORT: Use Mkt[0] directly if it has 2 player-name outcomes
        if markets:
            mkt0 = markets[0]
            q0 = (mkt0.get("question") or "").lower()
            oc0 = _jp(mkt0.get("outcomes", "[]"))
            pr0 = _jp(mkt0.get("outcomePrices", "[]"))
            tk0 = _jp(mkt0.get("clobTokenIds", "[]"))
            prices0 = []
            for p in pr0:
                try: prices0.append(float(p))
                except: prices0.append(0.0)
            oc_names0 = [str(o).lower().strip() for o in oc0]
            
            if len(oc0) == 2 and oc_names0 != ["yes", "no"] and                not any(n in ("draw", "tie") for n in oc_names0) and                len(prices0) >= 2 and (_price_valid(prices0[0]) or _price_valid(prices0[1])):
                h_name = str(oc0[0])
                a_name = str(oc0[1]) if len(oc0) > 1 else ""
                parsed["ml"] = {
                    "home": prices0[0], "home_tid": tk0[0] if tk0 else "",
                    "home_name": h_name,
                    "away": prices0[1] if len(prices0) > 1 else 0,
                    "away_tid": tk0[1] if len(tk0) > 1 else "",
                    "away_name": a_name,
                    "draw": 0, "draw_tid": "", "draw_name": "Draw",
                }
                if not parsed["home"]:
                    parsed["home"] = h_name
                    parsed["home_s"] = _abbreviate(h_name)
                if not parsed["away"]:
                    parsed["away"] = a_name
                    parsed["away_s"] = _abbreviate(a_name)
                logger.info(f"  LAST RESORT ML from Mkt[0]: '{h_name}'@{prices0[0]} vs '{a_name}'@{prices0[1] if len(prices0)>1 else 0}")
    
    parsed["volume"] = total_vol
    
    # ==============================================
    # REORDER: Ensure "home" slot = title's home team (1)
    #          and "away" slot = title's away team (2)
    #
    # The title "Leeds vs Man City" tells us Leeds=1, Man City=2
    # But the API might have returned them in any order.
    # We swap the SLOTS (not the data) so 1/2 display correctly.
    # Token_id ↔ price ↔ name stays paired — 100% safe.
    # ==============================================
    if parsed["ml"] and home and away:
        ml = parsed["ml"]
        ml_home_name = (ml.get("home_name") or "").lower().strip()
        title_home = home.lower().strip()
        title_away = away.lower().strip()
        
        # Check if the "home" slot actually contains the away team
        # Use word overlap — more robust than substring
        def _name_overlap(name_a: str, name_b: str) -> int:
            words_a = set(w for w in name_a.lower().split() if len(w) > 2)
            words_b = set(w for w in name_b.lower().split() if len(w) > 2)
            return len(words_a & words_b)
        
        home_matches_title_home = _name_overlap(ml_home_name, title_home)
        home_matches_title_away = _name_overlap(ml_home_name, title_away)
        
        if home_matches_title_away > home_matches_title_home and home_matches_title_away > 0:
            # Slot "home" actually has the away team — swap slots
            logger.info(f"  REORDER: '{ml.get('home_name')}' is away team, swapping 1↔2")
            parsed["ml"] = {
                "home": ml["away"], "home_tid": ml["away_tid"], "home_name": ml["away_name"],
                "away": ml["home"], "away_tid": ml["home_tid"], "away_name": ml["home_name"],
                "draw": ml["draw"], "draw_tid": ml["draw_tid"], "draw_name": ml.get("draw_name", "Draw"),
            }
            # Also update parsed home/away display names
            parsed["home"] = ml["away_name"]
            parsed["home_s"] = _abbreviate(ml["away_name"])
            parsed["away"] = ml["home_name"]
            parsed["away_s"] = _abbreviate(ml["home_name"])
    
    # Debug: log what was parsed for each event
    n_mkts = len(markets)
    ml_ok = "✓" if parsed["ml"] else "✗"
    tot_n = len(parsed["totals"])
    spr_n = len(parsed["spreads"])
    logger.info(f"  parse_result: {n_mkts} mkts, ML={ml_ok}, totals={tot_n}, spreads={spr_n}, sport={parsed['sport']}")
    if parsed["ml"]:
        ml = parsed["ml"]
        logger.info(f"  FINAL_ML: 1='{ml.get('home_name','')}' @{ml.get('home',0):.3f} | "
                     f"X=Draw @{ml.get('draw',0):.3f} | "
                     f"2='{ml.get('away_name','')}' @{ml.get('away',0):.3f}")
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
    """Detect sport type from event tags, series, or title keywords."""
    # Method 1: Check seriesId
    series_id = str(event.get("seriesId", "") or "")
    if series_id:
        for key, info in LEAGUES.items():
            if info["series"] == series_id:
                return info["sport"]
    
    # Method 2: Check seriesSlug
    series_slug = str(event.get("seriesSlug", "") or "")
    if series_slug:
        for key, info in LEAGUES.items():
            if key == series_slug or series_slug in info.get("name", "").lower():
                return info["sport"]
    
    # Method 3: Check tags
    tags = event.get("tags", [])
    if isinstance(tags, list):
        tag_labels = [str(t.get("label", "") or t.get("slug", "")).lower() for t in tags if isinstance(t, dict)]
        tag_str = " ".join(tag_labels)
        if any(w in tag_str for w in ["nba", "ncaa basketball", "basketball", "wnba"]):
            return "basketball"
        if any(w in tag_str for w in ["nfl", "ncaa football", "american football"]):
            return "american_football"
        if any(w in tag_str for w in ["epl", "premier league", "la liga", "serie a", "bundesliga", "ligue 1", "champions league", "europa", "soccer", "football", "mls", "copa"]):
            return "soccer"
        if any(w in tag_str for w in ["nhl", "hockey"]):
            return "hockey"
        if any(w in tag_str for w in ["atp", "wta", "tennis"]):
            return "tennis"
        if any(w in tag_str for w in ["ipl", "cricket", "t20", "odi", "big bash"]):
            return "cricket"
        if any(w in tag_str for w in ["ufc", "mma", "bellator"]):
            return "mma"
        if any(w in tag_str for w in ["cs2", "counter-strike", "esports", "valorant", "dota", "league of legends"]):
            return "esports"
    
    # Method 4: Check title keywords
    title = (event.get("title") or "").lower()
    if any(w in title for w in ["nba", "basketball", "celtics", "lakers", "knicks", "warriors", "bulls", "nets", "76ers", "bucks"]):
        return "basketball"
    if any(w in title for w in ["nfl", "touchdown", "patriots", "chiefs", "eagles"]):
        return "american_football"
    if any(w in title for w in ["nhl", "hockey", "bruins", "rangers", "penguins"]):
        return "hockey"
    if any(w in title for w in ["atp", "wta", "tennis", "slam", "open"]):
        return "tennis"
    if any(w in title for w in ["ufc", "mma"]):
        return "mma"
    if any(w in title for w in ["counter-strike", "cs2:", "valorant", "dota"]):
        return "esports"
    if any(w in title for w in ["cricket", "ipl", "t20"]):
        return "cricket"
    
    logger.debug(f"_detect_sport fallback to soccer: seriesId={series_id} title={title[:40]}")
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
    
    # Strip tournament/event prefixes (e.g. "Rotterdam Open: ", "UFC 315: ")
    # Only strip if there's still a vs/v after the colon
    if ": " in title:
        after_colon = title.split(": ", 1)[1]
        if any(sep in after_colon.lower() for sep in [" vs ", " vs. ", " v "]):
            title = after_colon
    
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


def _total_line_label(sport: str, line: float, base_label: str, is_main: bool = False) -> str:
    """
    Generate sport-specific O/U label for a given line.
    
    Tennis:  2.5 → "Sets O/U 2.5",  21.5 → "Games O/U 21.5"
    Basketball: 225.5 → "Points O/U 225.5"
    Soccer: 2.5 → "Goals O/U 2.5"
    Hockey: 5.5 → "Goals O/U 5.5"
    NFL: 44.5 → "Points O/U 44.5"
    Cricket: 280.5 → "Runs O/U 280.5"
    MMA: 2.5 → "Rounds O/U 2.5", 4.5 → "Rounds O/U 4.5"
    Esports: 2.5 → "Maps O/U 2.5"
    """
    if sport == "tennis":
        if line <= 3:
            return f"Sets O/U {line:g}"
        else:
            return f"Games O/U {line:g}"
    elif sport == "basketball":
        if line >= 190:
            return f"Points O/U {line:g}"
        else:
            return f"O/U {line:g}"  # shouldn't happen after filter but fallback
    elif sport == "american_football":
        return f"Points O/U {line:g}"
    elif sport == "hockey":
        return f"Goals O/U {line:g}"
    elif sport == "cricket":
        return f"Runs O/U {line:g}"
    elif sport == "mma":
        return f"Rounds O/U {line:g}"
    elif sport == "esports":
        return f"Maps O/U {line:g}"
    else:  # soccer
        return f"Goals O/U {line:g}"


# =============================================================================
# FORMAT MATCH — SPORT-AWARE DISPLAY
# =============================================================================

def format_match(g: dict, currency: str = "USD") -> str:
    sport = g.get("sport", "soccer")
    labels = SPORT_LABELS.get(sport, SPORT_LABELS["soccer"])
    logger.info(f"  format_match: sport={sport} ml_label={labels['ml']} total_label={labels['total']} totals={len(g.get('totals',[]))} spreads={len(g.get('spreads',[]))} ml={'yes' if g.get('ml') else 'no'}")
    
    lines = []
    
    # Tournament name (tennis, MMA, esports)
    if g.get("tournament"):
        lines.append(f"<i>{g['tournament']}</i>")
    
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
        filtered_totals = _filter_totals_for_sport(g["totals"], sport)
        if not filtered_totals:
            filtered_totals = g["totals"][:3]
        
        main = _pick_main_total(filtered_totals, sport)
        if main:
            ov = fmt_odds(main["over"])
            un = fmt_odds(main["under"])
            
            # Sport-specific main total label
            main_label = _total_line_label(sport, float(main["line"]), labels["total"], is_main=True)
            
            lines.append(f"┌─── <b>{main_label}</b> ───┐")
            lines.append(f"│")
            lines.append(f"│  ⬆  Over            <code>{ov:>6}</code>")
            lines.append(f"│  ⬇  Under           <code>{un:>6}</code>")
            lines.append(f"│")
            lines.append(f"└{'─' * 30}┘")
            
            # Other lines
            other_lines = [t for t in filtered_totals if t != main]
            for ot in other_lines[:4]:
                o_ov = fmt_odds(ot["over"])
                o_un = fmt_odds(ot["under"])
                ot_label = _total_line_label(sport, float(ot["line"]), labels["total"], is_main=False)
                lines.append(f"┌─── <b>{ot_label}</b> ───┐")
                lines.append(f"│  ⬆  Over  <code>{o_ov:>6}</code>  │  ⬇  Under  <code>{o_un:>6}</code>")
                lines.append(f"└{'─' * 30}┘")
            lines.append("")
    
    # ── SPREAD / HANDICAP ──
    if g["spreads"]:
        # Sport-specific spread label
        if sport == "soccer":
            spread_label = "Handicap"
        elif sport == "tennis":
            spread_label = "Game Spread"
        elif sport == "mma":
            spread_label = "Round Spread"
        elif sport == "esports":
            spread_label = "Map Spread"
        else:
            spread_label = "Spread"
        
        filtered_spreads = _filter_spreads(g["spreads"])
        for sp in filtered_spreads:
            h_odds = fmt_odds(sp["home"])
            a_odds = fmt_odds(sp["away"])
            h_line = sp.get("home_line", sp.get("line", ""))
            a_line = sp.get("away_line", "")
            if not a_line:
                line_num = sp.get("line_num", sp.get("line", "").lstrip("-+"))
                if h_line.startswith("-"):
                    a_line = f"+{line_num}"
                else:
                    a_line = f"-{line_num}"
            
            lines.append(f"┌─── <b>{spread_label}</b> ───┐")
            lines.append(f"│")
            h_name = sp.get("home_name", g.get("home", "Home"))[:12]
            a_name = sp.get("away_name", g.get("away", "Away"))[:12]
            lines.append(f"│  H1  {h_name:<12} ({h_line})  <code>{h_odds:>6}</code>")
            lines.append(f"│  H2  {a_name:<12} ({a_line})  <code>{a_odds:>6}</code>")
            lines.append(f"│")
            lines.append(f"└{'─' * 30}┘")
        lines.append("")
    
    # ── BTTS (soccer only) ──
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


def _filter_totals_for_sport(totals: list, sport: str) -> list:
    """
    Filter O/U lines to only show GAME totals, removing quarter/half/player props.
    """
    if not totals:
        return totals
    
    filtered = []
    seen_lines = set()
    
    for t in totals:
        try:
            line = float(t["line"])
        except:
            continue
        
        line_key = str(t["line"])
        if line_key in seen_lines:
            continue
        
        keep = False
        if sport == "basketball":
            keep = 190 <= line <= 280
        elif sport == "american_football":
            keep = 25 <= line <= 75
        elif sport == "soccer":
            keep = 0.5 <= line <= 6.5
        elif sport == "hockey":
            keep = 3.5 <= line <= 9.5
        elif sport == "tennis":
            keep = (15 <= line <= 45) or line == 2.5
        elif sport == "cricket":
            keep = line >= 50  # Cricket run totals are high
        elif sport == "mma":
            keep = 0.5 <= line <= 5.5  # Round totals
        elif sport == "esports":
            keep = 0.5 <= line <= 5.5  # Map totals
        else:
            keep = True
        
        if keep:
            filtered.append(t)
            seen_lines.add(line_key)
    
    filtered.sort(key=lambda t: float(t.get("line", 0)))
    return filtered[:5]


def _filter_spreads(spreads: list) -> list:
    """Deduplicate spreads and cap at 3 lines."""
    if not spreads:
        return spreads
    seen = set()
    filtered = []
    for sp in spreads:
        key = sp.get("line_num", sp.get("line", ""))
        if key not in seen:
            filtered.append(sp)
            seen.add(key)
    return filtered[:3]


def _pick_main_total(totals: list, sport: str) -> dict:
    """Pick the most relevant O/U line for the sport."""
    if not totals:
        return {}
    
    if sport == "basketball":
        # Pick the main game total (highest line in 190-280 range)
        best = None
        for t in totals:
            try:
                line = float(t["line"])
                if 190 <= line <= 280:
                    if best is None or line > float(best["line"]):
                        best = t
            except: pass
        return best or totals[0]
    
    elif sport == "american_football":
        # NFL game total (30-60 range)
        best = None
        for t in totals:
            try:
                line = float(t["line"])
                if 25 <= line <= 75:
                    if best is None:
                        best = t
            except: pass
        return best or totals[0]
    
    elif sport == "hockey":
        # Prefer 5.5 or 6.5
        for t in totals:
            if str(t["line"]) in ["5.5", "6.5"]:
                return t
        return totals[0]
    
    elif sport == "tennis":
        # Prefer match total games (15-45), not sets (2.5)
        for t in totals:
            try:
                line = float(t["line"])
                if 15 <= line <= 45:
                    return t
            except: pass
        return totals[0]
    
    elif sport == "cricket":
        # Cricket run totals are high (100+)
        best = None
        for t in totals:
            try:
                line = float(t["line"])
                if line >= 50:
                    if best is None:
                        best = t
            except: pass
        return best or totals[0]
    
    elif sport in ("mma", "esports"):
        # Pick middle round/map total (usually 2.5)
        for t in totals:
            if str(t["line"]) in ["2.5", "3.5"]:
                return t
        return totals[0]
    
    else:  # soccer default
        for t in totals:
            if str(t["line"]) in ["2.5", "3.5"]:
                return t
        return totals[0]


async def get_mid_price(token_id: str) -> float:
    """Fetch mid-market price for a token from the gamma API (same source as game list).
    Returns price like 0.52, or 0 on failure."""
    import httpx
    try:
        async with httpx.AsyncClient(timeout=8) as client:
            # Search markets by clob token ID
            r = await client.get(f"{GAMMA_API}/markets", params={
                "clob_token_ids": token_id,
                "closed": "false",
            })
            if r.status_code == 200:
                markets = r.json()
                if markets and len(markets) > 0:
                    mkt = markets[0]
                    prices_raw = _jp(mkt.get("outcomePrices", "[]"))
                    tokens = _jp(mkt.get("clobTokenIds", "[]"))
                    
                    # Find which index matches our token
                    for i, tid in enumerate(tokens):
                        if tid == token_id and i < len(prices_raw):
                            try:
                                return float(prices_raw[i])
                            except:
                                pass
                    
                    # Fallback: return first price if only 2 outcomes
                    if len(prices_raw) >= 1:
                        try:
                            return float(prices_raw[0])
                        except:
                            pass
    except Exception as e:
        logger.warning(f"get_mid_price failed for {token_id[:20]}...: {e}")
    return 0



CLOB_BASE = "https://clob.polymarket.com"


async def fetch_orderbook(token_id: str) -> dict:
    """Fetch full orderbook for a token from CLOB API.
    Returns {"bids": [{"price": "0.50", "size": "100"}, ...], "asks": [...]}"""
    try:
        async with httpx.AsyncClient(timeout=8) as client:
            r = await client.get(f"{CLOB_BASE}/book", params={"token_id": token_id})
            if r.status_code == 200:
                return r.json()
    except Exception as e:
        logger.debug(f"fetch_orderbook error: {e}")
    return {"bids": [], "asks": []}


def estimate_fill(orderbook: dict, side: str, amount_usdc: float) -> dict:
    """Simulate a market order fill against the orderbook.
    
    For BUY: we walk through asks (sellers), spending amount_usdc.
    For SELL: we walk through bids (buyers), selling shares.
    
    Returns {
        "avg_price": float,      # weighted average fill price
        "avg_odds": float,       # 1/avg_price
        "total_shares": float,   # shares received
        "total_cost": float,     # USDC spent
        "filled": bool,          # whether full amount could fill
        "depth_usdc": float,     # total available liquidity in USDC
    }
    """
    if side == "BUY":
        # Walk through asks (ascending price) — we're buying shares
        levels = orderbook.get("asks", [])
        # Sort asks by price ascending (cheapest first)
        levels = sorted(levels, key=lambda x: float(x.get("price", "999")))
    else:
        # Walk through bids (descending price) — we're selling shares
        levels = orderbook.get("bids", [])
        # Sort bids by price descending (best bid first)
        levels = sorted(levels, key=lambda x: float(x.get("price", "0")), reverse=True)
    
    remaining = amount_usdc
    total_shares = 0.0
    total_cost = 0.0
    
    for level in levels:
        try:
            price = float(level.get("price", "0"))
            size = float(level.get("size", "0"))
        except (ValueError, TypeError):
            continue
        
        if price <= 0 or size <= 0:
            continue
        
        if side == "BUY":
            # Cost to buy all shares at this level = price * size
            level_cost = price * size
            if level_cost <= remaining:
                # Take the whole level
                total_shares += size
                total_cost += level_cost
                remaining -= level_cost
            else:
                # Partial fill at this level
                shares_we_can_buy = remaining / price
                total_shares += shares_we_can_buy
                total_cost += remaining
                remaining = 0
                break
        else:
            # SELL: we have shares to sell
            if size <= remaining:
                total_cost += price * size
                total_shares += size
                remaining -= size
            else:
                total_cost += price * remaining
                total_shares += remaining
                remaining = 0
                break
    
    # Calculate total depth
    depth = sum(float(l.get("price", "0")) * float(l.get("size", "0")) for l in levels 
                if float(l.get("price", "0")) > 0)
    
    avg_price = total_cost / total_shares if total_shares > 0 else 0
    avg_odds = round(1.0 / avg_price, 2) if avg_price > 0.01 else 0
    
    return {
        "avg_price": round(avg_price, 4),
        "avg_odds": avg_odds,
        "total_shares": round(total_shares, 2),
        "total_cost": round(total_cost, 4),
        "filled": remaining < 0.01,
        "depth_usdc": round(depth, 2),
    }


# ── POLYMARKET BRIDGE API ──────────────────────────────────────────

BRIDGE_BASE = "https://bridge.polymarket.com"

async def get_bridge_deposit_addresses(wallet_address: str) -> dict:
    """Call Polymarket Bridge API to get multi-chain deposit addresses.
    
    Returns unique deposit addresses for EVM chains, Solana, and Bitcoin.
    Any supported token sent to these addresses is auto-bridged to USDC.e on Polygon.
    """
    import aiohttp
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                f"{BRIDGE_BASE}/deposit",
                json={"address": wallet_address},
                headers={"Content-Type": "application/json"},
                timeout=aiohttp.ClientTimeout(total=15),
            ) as resp:
                if resp.status in (200, 201):
                    data = await resp.json()
                    return {
                        "evm": data.get("address", {}).get("evm", ""),
                        "svm": data.get("address", {}).get("svm", ""),
                        "btc": data.get("address", {}).get("btc", ""),
                        "note": data.get("note", ""),
                    }
                else:
                    txt = await resp.text()
                    logger.error(f"Bridge API error {resp.status}: {txt}")
                    return None
    except Exception as e:
        logger.error(f"Bridge deposit address fetch failed: {e}")
        return None


async def get_bridge_supported_assets() -> list:
    """Get list of supported chains/tokens from Polymarket Bridge API."""
    import aiohttp
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                f"{BRIDGE_BASE}/supported-assets",
                timeout=aiohttp.ClientTimeout(total=15),
            ) as resp:
                if resp.status == 200:
                    return await resp.json()
                return []
    except Exception as e:
        logger.error(f"Bridge supported assets fetch failed: {e}")
        return []


# ── TOKEN VERIFICATION ──────────────────────────────────────────

async def verify_token_market(token_id: str) -> dict:
    """
    Look up what market/outcome a token_id actually belongs to.
    Returns the market question and outcome name so we can verify
    the user is betting on what they think they're betting on.
    
    This is a SAFETY CHECK to prevent the catastrophic bug where
    a player prop token gets displayed as a moneyline selection.
    """
    try:
        url = f"{GAMMA_API}/markets"
        params = {"clob_token_ids": token_id, "closed": "false"}
        logger.info(f"  🔍 Verifying token: {token_id[:20]}...")
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(url, params=params)
            if resp.status_code != 200:
                logger.warning(f"  Verify token: Gamma API returned {resp.status_code}")
                return None
            data = resp.json()
            if not data:
                logger.warning(f"  Verify token: No market found for token")
                return None
            
            mkt = data[0]
            question = mkt.get("question", "") or mkt.get("groupItemTitle", "")
            outcomes = _jp(mkt.get("outcomes", "[]"))
            tokens = _jp(mkt.get("clobTokenIds", "[]"))
            
            # Find which outcome this token corresponds to
            outcome_name = ""
            for i, tid in enumerate(tokens):
                if tid == token_id and i < len(outcomes):
                    outcome_name = str(outcomes[i])
                    break
            
            logger.info(f"  🔍 Token verified: q='{question[:60]}' outcome='{outcome_name}'")
            
            return {
                "question": question,
                "outcome": outcome_name,
                "market_id": mkt.get("id", ""),
                "market_name": question,
            }
    except Exception as e:
        logger.error(f"Token verification lookup failed: {e}")
        return None
