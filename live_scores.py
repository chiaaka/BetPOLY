"""
BetPoly - Live Scores
Fetches real-time scores from ESPN's public API for soccer and NBA.
No API key needed. Scores are matched to Polymarket games by team name.
"""
import httpx
import logging
import re
import time
from datetime import datetime, timezone

logger = logging.getLogger("BetPoly.Scores")

# Simple in-memory cache: {cache_key: (timestamp, data)}
_score_cache: dict = {}
CACHE_TTL = 60  # seconds — scores refresh every 60s

# ESPN API endpoints - no auth required
ESPN_SOCCER_LEAGUES = {
    "epl": "eng.1",
    "lal": "esp.1",
    "sea": "ita.1",
    "bun": "ger.1",
    "fl1": "fra.1",
    "ucl": "uefa.champions",
    "uel": "uefa.europa",
    "uecl": "uefa.europa.conf",
    "mls": "usa.1",
    "erd": "ned.1",
    "lig": "por.1",
    "spl": "sco.1",
    "bel": "bel.1",
    "rpl": "rus.1",
    "sup": "tur.1",
    "csl": "chn.1",
}

ESPN_BASE = "https://site.api.espn.com/apis/site/v2/sports"

# Common team name aliases for matching
TEAM_ALIASES = {
    # EPL
    "man utd": ["manchester united", "man united"],
    "man city": ["manchester city"],
    "newcastle": ["newcastle united", "newcastle utd"],
    "wolves": ["wolverhampton", "wolverhampton wanderers"],
    "spurs": ["tottenham", "tottenham hotspur"],
    "brighton": ["brighton & hove albion", "brighton and hove"],
    "west ham": ["west ham united"],
    "nottm forest": ["nottingham forest", "nott'm forest"],
    "palace": ["crystal palace"],
    "villa": ["aston villa"],
    "bournemouth": ["afc bournemouth"],
    "leicester": ["leicester city"],
    "ipswich": ["ipswich town"],
    "saints": ["southampton"],
    # La Liga
    "atletico": ["atletico madrid", "atlético madrid", "atl. madrid", "atlético de madrid"],
    "real madrid": ["real madrid cf"],
    "barcelona": ["fc barcelona"],
    "betis": ["real betis"],
    "sociedad": ["real sociedad"],
    "athletic": ["athletic bilbao", "athletic club"],
    # Serie A
    "inter": ["inter milan", "internazionale"],
    "ac milan": ["milan"],
    "juve": ["juventus"],
    "napoli": ["ssc napoli"],
    "roma": ["as roma"],
    "lazio": ["ss lazio"],
    # UCL
    "psg": ["paris saint-germain", "paris sg", "paris saint germain"],
    "bayern": ["bayern munich", "bayern münchen", "fc bayern"],
    "dortmund": ["borussia dortmund"],
    "benfica": ["sl benfica"],
    "porto": ["fc porto"],
    # NBA
    "lakers": ["los angeles lakers", "la lakers"],
    "clippers": ["los angeles clippers", "la clippers"],
    "knicks": ["new york knicks"],
    "nets": ["brooklyn nets"],
    "warriors": ["golden state warriors"],
    "celtics": ["boston celtics"],
    "76ers": ["philadelphia 76ers", "sixers"],
    "heat": ["miami heat"],
    "bulls": ["chicago bulls"],
    "cavs": ["cleveland cavaliers", "cavaliers"],
    "thunder": ["oklahoma city thunder", "okc thunder", "okc"],
    "nuggets": ["denver nuggets"],
    "mavs": ["dallas mavericks", "mavericks"],
    "grizzlies": ["memphis grizzlies"],
    "bucks": ["milwaukee bucks"],
    "pistons": ["detroit pistons"],
    "spurs_nba": ["san antonio spurs"],
    "rockets": ["houston rockets"],
    "timberwolves": ["minnesota timberwolves", "wolves_nba"],
    "raptors": ["toronto raptors"],
    "suns": ["phoenix suns"],
    "kings": ["sacramento kings"],
    "hawks": ["atlanta hawks"],
    "magic": ["orlando magic"],
    "pacers": ["indiana pacers"],
    "hornets": ["charlotte hornets"],
    "wizards": ["washington wizards"],
    "blazers": ["portland trail blazers", "trail blazers"],
    "pelicans": ["new orleans pelicans"],
    "jazz": ["utah jazz"],
}


def _normalize(name: str) -> str:
    """Normalize team name for matching."""
    name = name.lower().strip()
    # Remove common suffixes
    for suffix in [" fc", " cf", " sc", " afc", " bc"]:
        name = name.replace(suffix, "")
    # Remove accents roughly
    name = name.replace("é", "e").replace("ü", "u").replace("ö", "o").replace("á", "a").replace("í", "i").replace("ó", "o")
    return name.strip()


def _teams_match(espn_name: str, poly_name: str) -> bool:
    """Check if ESPN team name matches Polymarket team name."""
    e = _normalize(espn_name)
    p = _normalize(poly_name)
    
    # Direct match
    if e == p or e in p or p in e:
        return True
    
    # Check aliases
    for key, aliases in TEAM_ALIASES.items():
        all_names = [key] + aliases
        all_names = [_normalize(n) for n in all_names]
        e_match = any(a in e or e in a for a in all_names)
        p_match = any(a in p or p in a for a in all_names)
        if e_match and p_match:
            return True
    
    # Check if main word matches (e.g. "Arsenal" in both)
    e_words = set(e.split())
    p_words = set(p.split())
    # If any significant word (>3 chars) matches
    common = e_words & p_words
    if any(len(w) > 3 for w in common):
        return True
    
    return False


async def fetch_soccer_scores(league_code: str) -> list:
    """Fetch live/today scores for a soccer league from ESPN.
    Returns list of {home, away, home_score, away_score, minute, status, detail}."""
    espn_league = ESPN_SOCCER_LEAGUES.get(league_code)
    if not espn_league:
        return []
    
    # Check cache
    cache_key = f"soccer_{league_code}"
    cached = _score_cache.get(cache_key)
    if cached and (time.time() - cached[0]) < CACHE_TTL:
        return cached[1]
    
    url = f"{ESPN_BASE}/soccer/{espn_league}/scoreboard"
    try:
        async with httpx.AsyncClient(timeout=8) as client:
            r = await client.get(url)
            if r.status_code != 200:
                logger.debug(f"ESPN soccer {league_code}: {r.status_code}")
                return []
            
            data = r.json()
            events = data.get("events", [])
            scores = []
            
            for ev in events:
                competitions = ev.get("competitions", [])
                if not competitions:
                    continue
                comp = competitions[0]
                
                status_obj = comp.get("status", {})
                status_type = status_obj.get("type", {})
                state = status_type.get("state", "")  # pre, in, post
                detail = status_type.get("detail", "")  # "45'+2'", "FT", "HT"
                clock = status_obj.get("displayClock", "")
                
                competitors = comp.get("competitors", [])
                if len(competitors) < 2:
                    continue
                
                home = away = None
                for c in competitors:
                    if c.get("homeAway") == "home":
                        home = c
                    else:
                        away = c
                
                if not home or not away:
                    continue
                
                scores.append({
                    "home": home.get("team", {}).get("displayName", ""),
                    "home_short": home.get("team", {}).get("shortDisplayName", ""),
                    "away": away.get("team", {}).get("displayName", ""),
                    "away_short": away.get("team", {}).get("shortDisplayName", ""),
                    "home_score": int(home.get("score", "0") or "0"),
                    "away_score": int(away.get("score", "0") or "0"),
                    "state": state,  # pre, in, post
                    "detail": detail,  # "45'+2'", "FT", "HT", "3:00 PM"
                    "clock": clock,
                })
            
            _score_cache[cache_key] = (time.time(), scores)
            return scores
    except Exception as e:
        logger.debug(f"ESPN soccer scores error: {e}")
        return []


async def fetch_nba_scores() -> list:
    """Fetch live/today NBA + college basketball scores from ESPN."""
    # Check cache
    cache_key = "nba"
    cached = _score_cache.get(cache_key)
    if cached and (time.time() - cached[0]) < CACHE_TTL:
        return cached[1]
    
    all_scores = []
    
    # College basketball needs today's date or it returns old games
    from datetime import datetime, timezone
    today = datetime.now(timezone.utc).strftime("%Y%m%d")
    
    urls = [
        f"{ESPN_BASE}/basketball/nba/scoreboard",
        f"{ESPN_BASE}/basketball/mens-college-basketball/scoreboard?dates={today}",
    ]
    try:
        async with httpx.AsyncClient(timeout=8) as client:
            for url in urls:
                try:
                    r = await client.get(url)
                    if r.status_code != 200:
                        continue
                    
                    data = r.json()
                    events = data.get("events", [])
                    
                    for ev in events:
                        competitions = ev.get("competitions", [])
                        if not competitions:
                            continue
                        comp = competitions[0]
                        
                        status_obj = comp.get("status", {})
                        status_type = status_obj.get("type", {})
                        state = status_type.get("state", "")
                        detail = status_type.get("detail", "")
                        clock = status_obj.get("displayClock", "")
                        period = status_obj.get("period", 0)
                        
                        competitors = comp.get("competitors", [])
                        if len(competitors) < 2:
                            continue
                        
                        home = away = None
                        for c in competitors:
                            if c.get("homeAway") == "home":
                                home = c
                            else:
                                away = c
                        
                        if not home or not away:
                            continue
                        
                        all_scores.append({
                            "home": home.get("team", {}).get("displayName", ""),
                            "home_short": home.get("team", {}).get("shortDisplayName", ""),
                            "away": away.get("team", {}).get("displayName", ""),
                            "away_short": away.get("team", {}).get("shortDisplayName", ""),
                            "home_score": int(home.get("score", "0") or "0"),
                            "away_score": int(away.get("score", "0") or "0"),
                            "state": state,
                            "detail": detail,
                            "clock": clock,
                            "period": period,
                        })
                except Exception as e:
                    logger.debug(f"ESPN basketball fetch error for {url}: {e}")
            
            _score_cache[cache_key] = (time.time(), all_scores)
            return all_scores
    except Exception as e:
        logger.debug(f"ESPN NBA scores error: {e}")
        return []


def match_score_to_game(game: dict, scores: list) -> dict | None:
    """Try to match a Polymarket game to an ESPN score entry.
    Returns the matched score dict or None."""
    home = game.get("home", "")
    away = game.get("away", "")
    title = game.get("title", "")
    
    if not home and not away and not title:
        return None
    
    # For games without home/away parsed, try matching from title
    if not home or not away:
        if title:
            # Title like "Towson Tigers vs. Campbell Fighting Camels"
            for s in scores:
                e_home = _normalize(s["home"])
                e_away = _normalize(s["away"])
                t = _normalize(title)
                if (e_home in t or any(len(w) > 3 and w in t for w in e_home.split())) and \
                   (e_away in t or any(len(w) > 3 and w in t for w in e_away.split())):
                    logger.info(f"  Score matched (title): '{title}' → {s['home']} {s['home_score']}-{s['away_score']} {s['away']}")
                    return s
        return None
    
    logger.debug(f"  Matching: home='{home}' away='{away}' against {len(scores)} ESPN scores")
    
    for s in scores:
        home_match = (
            _teams_match(s["home"], home) or 
            _teams_match(s["home_short"], home)
        )
        away_match = (
            _teams_match(s["away"], away) or 
            _teams_match(s["away_short"], away)
        )
        
        if home_match and away_match:
            logger.info(f"  Score matched: '{home}' vs '{away}' → {s['home']} {s['home_score']}-{s['away_score']} {s['away']}")
            return s
        
        # Try reversed (Polymarket sometimes flips home/away)
        home_rev = (
            _teams_match(s["home"], away) or 
            _teams_match(s["home_short"], away)
        )
        away_rev = (
            _teams_match(s["away"], home) or 
            _teams_match(s["away_short"], home)
        )
        
        if home_rev and away_rev:
            # Swap scores for display consistency
            return {
                **s,
                "home": s["away"], "away": s["home"],
                "home_short": s["away_short"], "away_short": s["home_short"],
                "home_score": s["away_score"], "away_score": s["home_score"],
                "_reversed": True,
            }
    
    return None


def format_score_line(score: dict, sport: str = "soccer") -> str:
    """Format a score into a display line for the game screen."""
    state = score.get("state", "")
    detail = score.get("detail", "")
    hs = score["home_score"]
    as_ = score["away_score"]
    
    if state == "in":
        # Live game
        if sport == "basketball":
            return f"🔴 LIVE — {detail}\n🏀 {hs} - {as_}"
        else:
            return f"🔴 LIVE — {detail}\n⚽ {hs} - {as_}"
    
    elif state == "post":
        # Finished
        if "final" in detail.lower() or "ft" in detail.lower():
            return f"🏁 FULL TIME\n⚽ {hs} - {as_}" if sport != "basketball" else f"🏁 FINAL\n🏀 {hs} - {as_}"
        return f"🏁 {detail}\n{'⚽' if sport != 'basketball' else '🏀'} {hs} - {as_}"
    
    elif state == "pre":
        # Not started yet
        return ""
    
    return ""
