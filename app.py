
import streamlit as st
import streamlit.components.v1 as components
import csv
import base64
import mimetypes
import math
import re
from pathlib import Path

st.set_page_config(page_title="WKBL Fantasy", page_icon="🏀", layout="wide")

# =========================================================
# WKBL Fantasy Prototype
# Required files in the same folder:
#   app.py
#   players_2024_25.csv
#   game_results_2025_26.csv OR raw_game_results_2025_26.txt
#   images/
#
# Run:
#   python -m streamlit run app.py
# =========================================================

# Prices are in Korean 억원 units.
# Salary cap: 14억원
# Highest initial player price: 4.5억원
# Minimum initial player price: 0.3억원
MIN_PRICE = 0.3
MAX_PRICE = 4.5
BUDGET_CAP = 14.0
PRICE_RANGE = MAX_PRICE - MIN_PRICE

# Season price update rule
# A player can move at most 50% of the whole price range over a full 30-game season.
REGULAR_SEASON_GAMES_PER_TEAM = 30
SEASON_PRICE_MOVE_RATIO = 0.50
MAX_PRICE_CHANGE_PER_GAME = round(
    SEASON_PRICE_MOVE_RATIO * PRICE_RANGE / REGULAR_SEASON_GAMES_PER_TEAM,
    2,
)  # 0.07억원
PRICE_UPDATE_SIGMA = 10.0
EXPECTED_SCORE_ALPHA = 0.20

SIMULATION_TEAMS = ["KB스타즈", "하나은행", "삼성생명", "우리은행", "BNK썸", "신한은행"]

CSV_PATH = Path("players_2024_25.csv")
IMAGE_DIR = Path("images")
GAME_RESULTS_PATH = Path("game_results_2025_26.csv")
RAW_GAME_RESULTS_PATH = Path("raw_game_results_2025_26.txt")

ROSTER_SIZE = 10
ROSTER_BACK_COUNT = 5
ROSTER_FRONT_COUNT = 5
STARTERS_COUNT = 5
MAX_PLAYERS_PER_WKBL_TEAM = 2
FREE_TRANSFERS_PER_GAMEWEEK = 2

COLUMNS = [
    "name", "team_2025_26", "position", "games", "minutes",
    "2pm", "2pa", "3pm", "3pa", "ftm", "fta",
    "oreb", "dreb", "ast", "stl", "blk", "to", "pts"
]

# =========================
# Page State
# =========================
if "page" not in st.session_state:
    st.session_state.page = "Home"

if "selected_player_key" not in st.session_state:
    st.session_state.selected_player_key = None

if st.session_state.page == "Leagues":
    st.session_state.page = "Simulation"

if "simulation_started" not in st.session_state:
    st.session_state.simulation_started = False

if "simulation_user_team" not in st.session_state:
    st.session_state.simulation_user_team = None

if "simulation_game_no" not in st.session_state:
    st.session_state.simulation_game_no = 1

if "simulation_league" not in st.session_state:
    st.session_state.simulation_league = []

def go_to(page_name: str):
    st.session_state.page = page_name

def player_key(player):
    return f'{player.get("name", "")}__{player.get("team_2025_26", "")}'

def format_price(value):
    return f"{value:.2f}억원"

def start_simulation(user_team):
    league = []
    for team in SIMULATION_TEAMS:
        league.append({
            "Rank": 1,
            "Team": team,
            "Manager": "You" if team == user_team else "AI",
            "Points": 0.0,
            "Transfers": 0,
            "Budget": format_price(BUDGET_CAP),
        })
    st.session_state.simulation_user_team = user_team
    st.session_state.simulation_game_no = 1
    st.session_state.simulation_started = True
    st.session_state.simulation_league = league

# =========================
# Parsing / Calculation
# =========================
def clean(value):
    if value is None:
        return ""
    return str(value).strip()

def canonical_team(team):
    team = clean(team)
    compact = team.replace(" ", "")
    aliases = {
        "BNK썸": "BNK썸",
        "BNK": "BNK썸",
        "KB스타즈": "KB스타즈",
        "KBStars": "KB스타즈",
        "KBSTARS": "KB스타즈",
        "삼성생명": "삼성생명",
        "삼성": "삼성생명",
        "우리은행": "우리은행",
        "우리": "우리은행",
        "하나은행": "하나은행",
        "하나": "하나은행",
        "신한은행": "신한은행",
        "신한": "신한은행",
    }
    return aliases.get(compact, compact or team)

def get_any(row, names):
    """
    Read one value from a CSV row using multiple possible column names.
    This supports both our simplified headers such as dreb/oreb
    and WKBL-style headers such as DEF/OFF/2PM-A.
    """
    if row is None:
        return ""

    # exact match first
    for name in names:
        if name in row and clean(row.get(name)) != "":
            return clean(row.get(name))

    # case-insensitive / stripped match
    normalized = {clean(k).lower(): v for k, v in row.items() if k is not None}
    for name in names:
        key = clean(name).lower()
        if key in normalized and clean(normalized[key]) != "":
            return clean(normalized[key])

    return ""

def split_made_attempt(value):
    """
    Convert '175-419' into ('175', '419').
    Also tolerates spaces such as '175 - 419'.
    """
    value = clean(value)
    if "-" not in value:
        return "", ""
    made, attempt = value.split("-", 1)
    return clean(made), clean(attempt)

def normalize_shot_values(made_value, attempt_value, pair_value=""):
    """
    Return made/attempt values even when the CSV stores them as:
    - separate columns: 2p,2pa
    - separate columns: 2pm,2pa
    - combined column: 2PM-A = 175-419
    - accidentally in made column: 175-419
    """
    made_value = clean(made_value)
    attempt_value = clean(attempt_value)
    pair_value = clean(pair_value)

    if "-" in made_value and attempt_value == "":
        return split_made_attempt(made_value)

    if pair_value:
        pair_made, pair_attempt = split_made_attempt(pair_value)
    else:
        pair_made, pair_attempt = "", ""

    made = made_value or pair_made
    attempt = attempt_value or pair_attempt
    return made, attempt

def canonicalize_row(row):
    """
    Convert different CSV header styles into the canonical fields used by the app.
    """
    player = {}

    player["name"] = get_any(row, ["name", "선수", "player"])
    player["team_2025_26"] = canonical_team(get_any(row, ["team_2025_26", "team", "소속구단", "팀"]))
    player["position"] = get_any(row, ["position", "pos", "POS"])

    player["games"] = get_any(row, ["games", "G", "g", "출전경기"])
    player["minutes"] = get_any(row, ["minutes", "MIN", "min", "출전시간"])

    two_pair = get_any(row, ["2PM-A", "2pm-a", "2PMA", "2P M-A", "2P-A", "2p-a", "2점슛"])
    three_pair = get_any(row, ["3PM-A", "3pm-a", "3PMA", "3P M-A", "3P-A", "3p-a", "3점슛"])
    ft_pair = get_any(row, ["FTM-A", "ftm-a", "FTA-M", "FTMA", "FT-A", "ft-a", "자유투"])

    # Important:
    # Some manually made CSV files use 2p/3p for made shots,
    # not 2pm/3pm. The old version missed those columns and read made shots as 0.
    raw_2pm = get_any(row, ["2pm", "2PM", "2p", "2P", "2pmade", "2PMADE", "2p_made", "2P_MADE", "two_pm", "two_p_made", "2점성공", "2점슛성공"])
    raw_2pa = get_any(row, ["2pa", "2PA", "2a", "2A", "two_pa", "2점시도", "2점슛시도"])
    raw_3pm = get_any(row, ["3pm", "3PM", "3p", "3P", "3pmade", "3PMADE", "3p_made", "3P_MADE", "three_pm", "three_p_made", "3점성공", "3점슛성공"])
    raw_3pa = get_any(row, ["3pa", "3PA", "3a", "3A", "three_pa", "3점시도", "3점슛시도"])
    raw_ftm = get_any(row, ["ftm", "FTM", "ft", "FT", "ftmade", "FTMADE", "ft_made", "FT_MADE", "free_throw_made", "자유투성공"])
    raw_fta = get_any(row, ["fta", "FTA", "free_throw_attempt", "자유투시도"])

    player["2pm"], player["2pa"] = normalize_shot_values(raw_2pm, raw_2pa, two_pair)
    player["3pm"], player["3pa"] = normalize_shot_values(raw_3pm, raw_3pa, three_pair)
    player["ftm"], player["fta"] = normalize_shot_values(raw_ftm, raw_fta, ft_pair)

    player["oreb"] = get_any(row, ["oreb", "OREB", "off", "OFF", "offensive_rebounds", "공격리바운드"])
    player["dreb"] = get_any(row, ["dreb", "DREB", "def", "DEF", "defensive_rebounds", "수비리바운드"])

    # Safety fallback: if a CSV has total rebounds but not defensive rebounds,
    # calculate DREB = TOT - OREB.
    total_reb = get_any(row, ["tot", "TOT", "reb", "REB", "total_rebounds", "총리바운드"])
    if clean(player["dreb"]) == "" and clean(total_reb) != "" and clean(player["oreb"]) != "":
        player["dreb"] = str(max(to_int(total_reb) - to_int(player["oreb"]), 0))

    player["ast"] = get_any(row, ["ast", "AST", "어시스트"])
    player["stl"] = get_any(row, ["stl", "STL", "st", "ST", "스틸"])
    player["blk"] = get_any(row, ["blk", "BLK", "bs", "BS", "블록"])
    player["to"] = get_any(row, ["to", "TO", "turnover", "turnovers", "턴오버"])
    player["pts"] = get_any(row, ["pts", "PTS", "points", "득점"])

    return player

def to_int(value, default=0):
    value = clean(value)
    if value == "":
        return default
    try:
        return int(float(value))
    except ValueError:
        return default

def parse_minutes(value):
    """
    WKBL cumulative minutes appear as '1042:09' or sometimes '879:2'.
    Treat this as minutes:seconds.
    """
    value = clean(value)
    if not value:
        return 0.0
    if ":" in value:
        left, right = value.split(":", 1)
        minutes = to_int(left)
        seconds = to_int(right)
        return minutes + seconds / 60
    try:
        return float(value)
    except ValueError:
        return 0.0

def has_previous_data(player):
    return clean(player.get("games")) != "" and clean(player.get("minutes")) != ""

def fantasy_breakdown(player):
    if not has_previous_data(player):
        return {
            "score": 0.0,
            "has_data": False,
        }

    minutes = parse_minutes(player.get("minutes"))

    two_pm = to_int(player.get("2pm"))
    two_pa = to_int(player.get("2pa"))
    three_pm = to_int(player.get("3pm"))
    three_pa = to_int(player.get("3pa"))
    ftm = to_int(player.get("ftm"))
    fta = to_int(player.get("fta"))

    missed_2p = max(two_pa - two_pm, 0)
    missed_3p = max(three_pa - three_pm, 0)
    missed_ft = max(fta - ftm, 0)

    pts = to_int(player.get("pts"))
    stl = to_int(player.get("stl"))
    blk = to_int(player.get("blk"))
    dreb = to_int(player.get("dreb"))
    oreb = to_int(player.get("oreb"))
    ast = to_int(player.get("ast"))
    turnovers = to_int(player.get("to"))

    base = pts + stl + blk + dreb
    bonus = 1.5 * (oreb + ast)
    minute_score = minutes / 4
    penalty_to = 1.5 * turnovers
    penalty_2p = 1.0 * missed_2p
    penalty_3p = 0.9 * missed_3p
    penalty_ft = 0.8 * missed_ft

    score = base + bonus + minute_score - penalty_to - penalty_2p - penalty_3p - penalty_ft

    return {
        "has_data": True,
        "score": round(score, 2),
        "minutes": minutes,
        "pts": pts,
        "stl": stl,
        "blk": blk,
        "dreb": dreb,
        "oreb": oreb,
        "ast": ast,
        "to": turnovers,
        "2pm": two_pm,
        "2pa": two_pa,
        "3pm": three_pm,
        "3pa": three_pa,
        "ftm": ftm,
        "fta": fta,
        "missed_2p": missed_2p,
        "missed_3p": missed_3p,
        "missed_ft": missed_ft,
        "base": round(base, 2),
        "bonus": round(bonus, 2),
        "minute_score": round(minute_score, 2),
        "penalty_to": round(penalty_to, 2),
        "penalty_2p": round(penalty_2p, 2),
        "penalty_3p": round(penalty_3p, 2),
        "penalty_ft": round(penalty_ft, 2),
    }

def fantasy_score(player):
    return fantasy_breakdown(player)["score"]

def price_change_from_performance(game_score, expected_score):
    """
    Smooth price movement based on performance over/under expectation.

    Delta P = delta_max * tanh((S - E) / sigma)
    - S: single-game fantasy score
    - E: pre-game expected fantasy score
    - delta_max: mathematically set one-game movement cap, 0.07억원
    """
    gap = float(game_score) - float(expected_score)
    return MAX_PRICE_CHANGE_PER_GAME * math.tanh(gap / PRICE_UPDATE_SIGMA)

def update_expected_score(previous_expected, game_score):
    """
    Exponential moving average:
    next E = (1-alpha) * old E + alpha * current game score
    """
    return (1 - EXPECTED_SCORE_ALPHA) * float(previous_expected) + EXPECTED_SCORE_ALPHA * float(game_score)

def update_player_price(current_price, game_score, expected_score, played=True):
    """
    Returns (new_price, price_change, new_expected_score).
    If a player does not play, price and expectation stay unchanged.
    """
    current_price = float(current_price)
    expected_score = float(expected_score)

    if not played:
        return round(current_price, 2), 0.0, round(expected_score, 2)

    change = price_change_from_performance(game_score, expected_score)
    new_price = max(MIN_PRICE, min(MAX_PRICE, current_price + change))
    new_expected = update_expected_score(expected_score, game_score)
    return round(new_price, 2), round(change, 4), round(new_expected, 2)

def initialize_market_fields(player):
    """Attach current price and expected score fields used by the simulation."""
    player["current_price"] = player.get("initial_price", MIN_PRICE)
    games = max(to_int(player.get("games", 0)), 1)
    if player.get("previous_data", False):
        player["expected_score"] = round(player.get("fantasy_score", 0.0) / games, 2)
    else:
        player["expected_score"] = 0.0
    return player

def normalize_position(pos):
    pos = clean(pos).upper()
    if pos in ["B", "G", "GUARD", "BACK COURT", "BACKCOURT"]:
        return "Back Court"
    if pos in ["F", "C", "FORWARD", "CENTER", "FRONT COURT", "FRONTCOURT"]:
        return "Front Court"
    return "Not Set"

def position_short(pos):
    pos = normalize_position(pos)
    if pos == "Back Court":
        return "B"
    if pos == "Front Court":
        return "F"
    return "?"

def read_csv_with_fallback(path):
    """
    Tries UTF-8 first, then CP949 for Korean Windows CSV files.
    """
    encodings = ["utf-8-sig", "utf-8", "cp949"]
    last_error = None
    for enc in encodings:
        try:
            with open(path, "r", encoding=enc, newline="") as f:
                return list(csv.DictReader(f)), enc
        except UnicodeDecodeError as e:
            last_error = e
    raise last_error

def load_players():
    if not CSV_PATH.exists():
        return [], None

    rows, encoding_used = read_csv_with_fallback(CSV_PATH)
    players = []
    for row in rows:
        player = canonicalize_row(row)
        if player["name"]:
            player["position_label"] = normalize_position(player["position"])
            player["position_short"] = position_short(player["position"])
            player["previous_data"] = has_previous_data(player)
            player["fantasy_score"] = fantasy_score(player)
            players.append(player)

    max_score = max([p["fantasy_score"] for p in players if p["previous_data"]] or [0])
    for p in players:
        if not p["previous_data"] or max_score <= 0:
            p["initial_price"] = MIN_PRICE
        else:
            price = MIN_PRICE + PRICE_RANGE * (p["fantasy_score"] / max_score)
            p["initial_price"] = round(price, 2)
        initialize_market_fields(p)

    players.sort(key=lambda x: (x["team_2025_26"], x["name"]))
    return players, encoding_used

def sample_players():
    raw = [
        {"name": "김단비", "team_2025_26": "우리은행", "position": "F", "games": "29", "minutes": "1041:36", "2pm": "200", "2pa": "478", "3pm": "27", "3pa": "121", "ftm": "131", "fta": "175", "oreb": "92", "dreb": "224", "ast": "105", "stl": "60", "blk": "44", "to": "93", "pts": "612"},
        {"name": "강이슬", "team_2025_26": "KB스타즈", "position": "B", "games": "30", "minutes": "1062:35", "2pm": "74", "2pa": "181", "3pm": "64", "3pa": "223", "ftm": "84", "fta": "102", "oreb": "49", "dreb": "173", "ast": "51", "stl": "44", "blk": "18", "to": "72", "pts": "424"},
        {"name": "박지수", "team_2025_26": "KB스타즈", "position": "F", "games": "", "minutes": "", "2pm": "", "2pa": "", "3pm": "", "3pa": "", "ftm": "", "fta": "", "oreb": "", "dreb": "", "ast": "", "stl": "", "blk": "", "to": "", "pts": ""},
    ]
    for p in raw:
        p["position_label"] = normalize_position(p["position"])
        p["position_short"] = position_short(p["position"])
        p["previous_data"] = has_previous_data(p)
        p["fantasy_score"] = fantasy_score(p)
    max_score = max([p["fantasy_score"] for p in raw if p["previous_data"]] or [0])
    for p in raw:
        p["initial_price"] = MIN_PRICE if not p["previous_data"] else round(MIN_PRICE + PRICE_RANGE * (p["fantasy_score"] / max_score), 2)
        initialize_market_fields(p)
    return raw

players, csv_encoding = load_players()
csv_loaded = True
if not players:
    players = sample_players()
    csv_loaded = False

# =========================
# Image Handling
# =========================
def _norm_text(s):
    """Normalize Korean/ASCII names for filename matching."""
    if s is None:
        return ""
    s = str(s).strip()
    # unify common separators and remove spaces/punctuation
    s = s.replace("BNK 썸", "BNK썸")
    s = s.replace(" ", "")
    return re.sub(r"[^0-9A-Za-z가-힣]", "", s)

def _team_aliases(team):
    team = str(team or "").strip()
    base = _norm_text(team)
    aliases = {base}
    if base == "BNK썸":
        aliases.update({"BNK", "BNKSUM", "BNKSUM"})
    return {a for a in aliases if a}

def find_image(player):
    """
    Robust image finder.
    Priority:
    1. exact images/선수명_팀명.ext
    2. exact images/선수명.ext
    3. normalized stem match containing 선수명 + 팀명
    4. normalized stem match containing 선수명 only (for unique names)
    Notes:
    - Duplicate names like 김단비/김정은/박지수 prefer name+team files.
    - Supports png/jpg/jpeg/webp and searches recursively in images/.
    """
    name = player["name"]
    team = player["team_2025_26"]

    # 1) direct exact paths first
    candidates = []
    for ext in ["png", "jpg", "jpeg", "webp", "PNG", "JPG", "JPEG", "WEBP"]:
        candidates.append(IMAGE_DIR / f"{name}_{team}.{ext}")
        candidates.append(IMAGE_DIR / f"{name}.{ext}")
    for path in candidates:
        if path.exists():
            return path

    # 2) recursive search with normalized filename matching
    image_files = []
    if IMAGE_DIR.exists():
        for p in IMAGE_DIR.rglob("*"):
            if p.is_file() and p.suffix.lower() in [".png", ".jpg", ".jpeg", ".webp"]:
                image_files.append(p)

    norm_name = _norm_text(name)
    team_aliases = _team_aliases(team)

    # duplicate names in this dataset should require team-aware preference
    duplicate_names = {"김단비", "김정은", "박지수"}
    is_duplicate = name in duplicate_names

    # a) exact normalized stem == name+team or team+name
    for p in image_files:
        stem = _norm_text(p.stem)
        if any(stem == _norm_text(f"{name}{alias}") or stem == _norm_text(f"{name}_{alias}") or stem == _norm_text(f"{alias}{name}") for alias in team_aliases):
            return p

    # b) contains both player name and team alias somewhere
    for p in image_files:
        stem = _norm_text(p.stem)
        if norm_name and norm_name in stem and any(alias in stem for alias in team_aliases):
            return p

    # c) only if not duplicate name: stem startswith/contains player name
    if not is_duplicate:
        for p in image_files:
            stem = _norm_text(p.stem)
            if stem == norm_name or stem.startswith(norm_name) or norm_name in stem:
                return p

    return None

def image_data_url(path):
    if path is None:
        return None
    mime, _ = mimetypes.guess_type(path)
    mime = mime or "image/png"
    data = base64.b64encode(path.read_bytes()).decode("utf-8")
    return f"data:{mime};base64,{data}"

# =========================
# Main CSS
# =========================
st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=Oswald:wght@500;700&family=Noto+Sans+KR:wght@400;700;900&display=swap');

html, body, [class*="css"] {
    font-family: 'Noto Sans KR', sans-serif;
}

.block-container {
    padding-top: 1rem;
}

.header {
    background: linear-gradient(135deg, #ffffff 0%, #eef6ff 42%, #ffe3f1 100%);
    border-bottom: 8px solid #064EA4;
    padding: 26px 34px 18px 34px;
    border-radius: 0 0 24px 24px;
    margin-bottom: 18px;
}

.logo {
    font-family: 'Oswald', sans-serif;
    font-size: 62px;
    font-weight: 700;
    line-height: .95;
    letter-spacing: -1px;
}
.logo .blue { color: #064EA4; }
.logo .pink { color: #E91E73; }

.subtitle {
    font-family: 'Oswald', sans-serif;
    font-size: 19px;
    letter-spacing: 2px;
    color: #111827;
}

.nav-wrap {
    background: #064EA4;
    border-radius: 12px;
    padding: 0.45rem;
    margin-bottom: 18px;
}

.nav-links {
    display: grid;
    grid-template-columns: repeat(6, 1fr);
    gap: 12px;
}
.nav-link {
    display: block;
    text-align: center;
    text-decoration: none !important;
    border-radius: 9px;
    background: white;
    color: #111827 !important;
    font-weight: 900;
    padding: 0.75rem 0.5rem;
    font-size: 1rem;
    border: 1px solid #e5e7eb;
}
.nav-link:hover {
    background: #eaf3ff;
    color: #064EA4 !important;
}
.nav-link.active {
    background: #E91E73;
    color: white !important;
    border-color: #E91E73;
}

.nav-wrap div[data-testid="column"] {
    padding: 0 !important;
}

.nav-wrap .stButton > button {
    width: 100%;
    border: 0;
    border-radius: 9px;
    background: #064EA4;
    color: white;
    font-weight: 900;
    padding: 0.75rem 0.5rem;
    font-size: 1rem;
}

.nav-wrap .stButton > button:hover {
    background: #0b63ca;
    color: white;
    border: 0;
}

.nav-wrap .stButton > button:focus {
    box-shadow: none;
    color: white;
    border: 0;
}

.active-tab {
    background: #E91E73;
    color: white;
    border-radius: 9px;
    padding: 0.75rem 0.5rem;
    text-align: center;
    font-weight: 900;
    font-size: 1rem;
    margin-top: 0.13rem;
}

.hero {
    background: linear-gradient(135deg, #064EA4 0%, #0f172a 54%, #E91E73 100%);
    color: white;
    border-radius: 28px;
    padding: 42px;
    box-shadow: 0 12px 30px rgba(15, 23, 42, .18);
}

.hero-title {
    font-family: 'Oswald', sans-serif;
    font-size: 58px;
    line-height: 1;
    font-weight: 700;
    margin-bottom: 12px;
}

.feature {
    background: white;
    border: 1px solid #e5e7eb;
    border-radius: 18px;
    padding: 22px;
    min-height: 170px;
    box-shadow: 0 3px 14px rgba(15, 23, 42, 0.08);
}

.feature-title {
    font-family: 'Oswald', sans-serif;
    font-size: 25px;
    font-weight: 700;
    font-style: italic;
    color: #111827;
    margin-bottom: 10px;
}

.summary {
    background: white;
    border: 1px solid #e5e7eb;
    border-radius: 16px;
    padding: 18px 20px;
    box-shadow: 0 3px 14px rgba(15, 23, 42, 0.08);
    min-height: 104px;
}

.summary-label {
    font-size: 12px;
    font-weight: 900;
    color: #6b7280;
    letter-spacing: .5px;
    text-transform: uppercase;
}

.summary-value {
    font-size: 31px;
    font-weight: 900;
    color: #111827;
    margin-top: 4px;
}

.section-title {
    font-family: 'Oswald', sans-serif;
    font-size: 38px;
    font-weight: 700;
    font-style: italic;
    margin: 20px 0 14px 0;
    color: #111827;
}

.court {
    background:
        radial-gradient(circle at 50% 50%, rgba(255,255,255,.55) 0 13%, transparent 14%),
        linear-gradient(90deg, #f1d1a5 0%, #f8e3c4 50%, #f1d1a5 100%);
    border: 3px solid #E91E73;
    border-radius: 22px;
    padding: 22px;
    box-shadow: inset 0 0 0 2px rgba(255,255,255,.55);
    margin-bottom: 16px;
}

.bench {
    background: #fff7fb;
    border: 2px solid #f9a8d4;
    border-radius: 22px;
    padding: 22px;
    margin-bottom: 16px;
}

.panel {
    background: white;
    border: 1px solid #e5e7eb;
    border-radius: 18px;
    padding: 18px;
    box-shadow: 0 3px 14px rgba(15, 23, 42, 0.08);
    margin-bottom: 18px;
}

.panel-title {
    font-family: 'Oswald', sans-serif;
    font-size: 24px;
    font-weight: 700;
    color: #111827;
    margin-bottom: 12px;
    border-bottom: 1px solid #e5e7eb;
    padding-bottom: 8px;
}

.chip-row {
    display: flex;
    justify-content: space-between;
    align-items: center;
    border: 1px solid #e5e7eb;
    border-radius: 14px;
    padding: 10px 12px;
    margin-bottom: 8px;
    font-weight: 900;
}

.league-row {
    display: flex;
    justify-content: space-between;
    padding: 9px 10px;
    border-radius: 9px;
    font-weight: 700;
}

.league-me {
    background: #fce7f3;
    color: #E91E73;
    font-weight: 900;
}

.footer {
    margin-top: 32px;
    padding: 24px 10px;
    border-top: 1px solid #e5e7eb;
    color: #475569;
    font-size: 14px;
}

.data-badge {
    display:inline-block;
    padding: 3px 8px;
    border-radius: 999px;
    font-size: 12px;
    font-weight: 900;
}
.has-data { background:#dcfce7; color:#166534; }
.no-data { background:#fee2e2; color:#991b1b; }
</style>
""", unsafe_allow_html=True)

# =========================
# Helpers
# =========================
def player_card(p, priority=None, captain=False):
    """
    Uses components.html so the card is rendered as HTML,
    not printed as raw tag text.
    """
    price = p.get("current_price", p.get("initial_price", MIN_PRICE))
    team = p.get("team_2025_26", "")
    top_class = "card-top-pink" if team in ["BNK썸", "KB스타즈", "삼성생명"] else "card-top-blue"
    name_class = "name-pink" if team in ["BNK썸", "KB스타즈", "삼성생명"] else "name-blue"
    captain_html = '<div class="captain">C</div>' if captain else ""
    priority_html = f'<div class="priority">{priority}</div>' if priority else ""
    initial = p["name"][0]
    img_path = find_image(p)
    data_url = image_data_url(img_path)

    if data_url:
        avatar_html = f'<div class="avatar"><img src="{data_url}" alt="{p["name"]}"></div>'
    else:
        avatar_html = f'<div class="avatar">{initial}</div>'

    ppg_label = "No prev. data" if not p["previous_data"] else f'Fantasy {p["fantasy_score"]:.2f}'

    html = f"""
    <!DOCTYPE html>
    <html>
    <head>
    <meta charset="utf-8">
    <style>
        @import url('https://fonts.googleapis.com/css2?family=Oswald:wght@500;700&family=Noto+Sans+KR:wght@400;700;900&display=swap');
        body {{
            margin: 0;
            padding: 8px;
            font-family: 'Noto Sans KR', sans-serif;
            background: transparent;
        }}
        .player-card {{
            position: relative;
            background: white;
            border-radius: 18px;
            overflow: hidden;
            box-shadow: 0 8px 18px rgba(15, 23, 42, 0.18);
            border: 1px solid #e5e7eb;
            text-align: center;
            width: 100%;
            box-sizing: border-box;
        }}
        .card-top-blue {{
            background: linear-gradient(135deg, #bfdbfe 0%, #064EA4 100%);
            height: 128px;
            padding-top: 8px;
        }}
        .card-top-pink {{
            background: linear-gradient(135deg, #fbcfe8 0%, #E91E73 100%);
            height: 128px;
            padding-top: 8px;
        }}
        .avatar {{
            width: 88px;
            height: 88px;
            border-radius: 999px;
            background: white;
            margin: 18px auto 0 auto;
            border: 4px solid white;
            display: flex;
            align-items: center;
            justify-content: center;
            font-size: 30px;
            font-weight: 900;
            color: #111827;
            overflow: hidden;
        }}
        .avatar img {{
            width: 100%;
            height: 100%;
            object-fit: cover;
        }}
        .pos {{
            position: absolute;
            top: 14px;
            right: 14px;
            background: white;
            border-radius: 8px;
            padding: 4px 7px;
            font-size: 12px;
            font-weight: 900;
            color: #111827;
        }}
        .team-badge {{
            position: absolute;
            top: 14px;
            left: 14px;
            background: rgba(255,255,255,0.92);
            border-radius: 8px;
            padding: 4px 7px;
            font-size: 10px;
            font-weight: 900;
            color: #111827;
        }}
        .salary {{
            font-family: 'Oswald', sans-serif;
            font-size: 28px;
            font-weight: 700;
            color: #111827;
            padding: 4px 0;
            background: white;
        }}
        .name-blue {{
            background: #064EA4;
            color: white;
            font-weight: 900;
            padding: 6px 0;
        }}
        .name-pink {{
            background: #E91E73;
            color: white;
            font-weight: 900;
            padding: 6px 0;
        }}
        .ppg {{
            background: #f8fafc;
            font-size: 12px;
            font-weight: 800;
            color: #475569;
            padding: 7px 0;
        }}
        .captain {{
            position: absolute;
            right: 10px;
            top: 104px;
            width: 32px;
            height: 32px;
            border-radius: 999px;
            background: #064EA4;
            color: white;
            border: 4px solid white;
            display: flex;
            align-items: center;
            justify-content: center;
            font-weight: 900;
        }}
        .priority {{
            position: absolute;
            left: 10px;
            top: 104px;
            width: 30px;
            height: 30px;
            border-radius: 999px;
            background: #E91E73;
            color: white;
            border: 4px solid white;
            display: flex;
            align-items: center;
            justify-content: center;
            font-weight: 900;
            z-index: 5;
        }}
    </style>
    </head>
    <body>
        <div class="player-card">
            {priority_html}
            <div class="{top_class}">
                <div class="team-badge">{team}</div>
                <div class="pos">{p["position_short"]}</div>
                {avatar_html}
            </div>
            {captain_html}
            <div class="salary">{format_price(price)}</div>
            <div class="{name_class}">{p["name"]}</div>
            <div class="ppg">{ppg_label}</div>
        </div>
    </body>
    </html>
    """
    components.html(html, height=292, scrolling=False)

def summary_card(label, value, icon="🏀", detail=""):
    st.markdown(f"""
    <div class="summary">
        <div class="summary-label">{icon} {label}</div>
        <div class="summary-value">{value} <span style="font-size:13px;color:#6b7280;">{detail}</span></div>
    </div>
    """, unsafe_allow_html=True)

def header():
    st.markdown("""
    <div class="header">
        <div class="logo"><span class="blue">WKBL</span> <span class="pink">FANTASY</span></div>
        <div class="subtitle">SALARY CAP EDITION</div>
        <div style="margin-top:14px;color:#475569;font-weight:900;">🏀 Build your roster · Set your line-up · Climb the rankings</div>
    </div>
    """, unsafe_allow_html=True)

def nav():
    items = ["Home", "My Team", "Players", "Simulation", "Help"]
    st.markdown('<div class="nav-wrap">', unsafe_allow_html=True)
    cols = st.columns(len(items))

    for col, item in zip(cols, items):
        with col:
            if item == st.session_state.page:
                st.markdown(f'<div class="active-tab">{item}</div>', unsafe_allow_html=True)
            else:
                if st.button(item, key=f"topnav_{item}", use_container_width=True):
                    go_to(item)
                    st.rerun()

    st.markdown('</div>', unsafe_allow_html=True)

def table_html(rows, columns):
    html = "<table style='width:100%;border-collapse:collapse;background:white;border-radius:14px;overflow:hidden;'>"
    html += "<tr>"
    for c in columns:
        html += f"<th style='text-align:left;padding:12px;border-bottom:1px solid #e5e7eb;background:#f8fafc;'>{c}</th>"
    html += "</tr>"
    for r in rows:
        html += "<tr>"
        for c in columns:
            html += f"<td style='padding:11px;border-bottom:1px solid #e5e7eb;'>{r.get(c, '')}</td>"
        html += "</tr>"
    html += "</table>"
    return html


def player_detail_panel(player):
    st.markdown("### Selected Player Data")
    c1, c2 = st.columns([1, 2])

    with c1:
        player_card(player)

    with c2:
        st.markdown(f"#### {player['name']} [{player['team_2025_26']}]")
        current_price = player.get("current_price", player.get("initial_price", MIN_PRICE))
        st.markdown(
            f"**Position:** {player['position_label']}  \n"
            f"**Initial Price:** {format_price(player['initial_price'])}  \n"
            f"**Current Price:** {format_price(current_price)}"
        )

        if not player["previous_data"]:
            st.info("전 시즌 기록이 없는 선수입니다. 초기 가격은 최저가 0.3억원으로 처리됩니다.")
        else:
            b = fantasy_breakdown(player)
            st.markdown(f"**Fantasy Score:** {b['score']:.2f}")

            rows = [
                {"Item": "PTS + STL + BLK + DREB", "Value": f"{b['pts']} + {b['stl']} + {b['blk']} + {b['dreb']} = {b['base']:.2f}"},
                {"Item": "1.5 × (OREB + AST)", "Value": f"1.5 × ({b['oreb']} + {b['ast']}) = {b['bonus']:.2f}"},
                {"Item": "MIN / 4", "Value": f"{b['minutes']:.2f} / 4 = {b['minute_score']:.2f}"},
                {"Item": "1.5 × TO", "Value": f"1.5 × {b['to']} = -{b['penalty_to']:.2f}"},
                {"Item": "missed 2PT", "Value": f"{b['2pa']} - {b['2pm']} = {b['missed_2p']} → -{b['penalty_2p']:.2f}"},
                {"Item": "0.9 × missed 3PT", "Value": f"0.9 × ({b['3pa']} - {b['3pm']}) = -{b['penalty_3p']:.2f}"},
                {"Item": "0.8 × missed FT", "Value": f"0.8 × ({b['fta']} - {b['ftm']}) = -{b['penalty_ft']:.2f}"},
            ]
            st.markdown(table_html(rows, ["Item", "Value"]), unsafe_allow_html=True)

        if st.button("Close Data", key=f"close_detail_{player_key(player)}"):
            st.session_state.selected_player_key = None
            st.rerun()



# =========================
# Fantasy Game State / Simulation Helpers
# =========================
def player_lookup(players_list):
    return {player_key(p): p for p in players_list}

def initialize_fantasy_state(players_list):
    if "market_state" not in st.session_state:
        st.session_state.market_state = {}
    for p in players_list:
        k = player_key(p)
        if k not in st.session_state.market_state:
            st.session_state.market_state[k] = {
                "current_price": float(p.get("initial_price", MIN_PRICE)),
                "expected_score": float(p.get("expected_score", 0.0)),
                "last_change": 0.0,
                "games_played_2025_26": 0,
            }

    defaults = {
        "user_roster_keys": [],
        "user_starting_keys": [],
        "user_captain_key": None,
        "user_formation": "2 Back Court / 3 Front Court",
        "user_transfers": 0,
        "chip_captain_active": False,
        "chip_wildcard_available": True,
        "chip_wildcard_active": False,
        "chip_allstar_available": True,
        "chip_allstar_active": False,
        "simulation_game_index": 0,
        "simulation_history": [],
        "simulation_team_scores": {team: 0.0 for team in SIMULATION_TEAMS},
        "simulation_team_rosters": {},
        "simulation_team_starting": {},
        "simulation_team_captains": {},
        "simulation_ai_ready": False,
    }
    for key, value in defaults.items():
        if key not in st.session_state:
            st.session_state[key] = value

def apply_market_state(players_list):
    for p in players_list:
        k = player_key(p)
        state = st.session_state.market_state.get(k)
        if state:
            p["current_price"] = round(float(state.get("current_price", p.get("initial_price", MIN_PRICE))), 2)
            p["expected_score"] = round(float(state.get("expected_score", p.get("expected_score", 0.0))), 2)
            p["last_price_change"] = round(float(state.get("last_change", 0.0)), 4)

def reset_market_state(players_list):
    st.session_state.market_state = {}
    for p in players_list:
        k = player_key(p)
        st.session_state.market_state[k] = {
            "current_price": float(p.get("initial_price", MIN_PRICE)),
            "expected_score": float(p.get("fantasy_score", 0.0)) / max(to_int(p.get("games", 0)), 1) if p.get("previous_data") else 0.0,
            "last_change": 0.0,
            "games_played_2025_26": 0,
        }
    apply_market_state(players_list)

def price_of_key(key, players_list):
    p = player_lookup(players_list).get(key)
    if not p:
        return MIN_PRICE
    return float(p.get("current_price", p.get("initial_price", MIN_PRICE)))

def position_of_key(key, players_list):
    p = player_lookup(players_list).get(key)
    return p.get("position_label", "Not Set") if p else "Not Set"

def team_of_key(key, players_list):
    p = player_lookup(players_list).get(key)
    return p.get("team_2025_26", "") if p else ""

def label_for_key(key, players_list):
    p = player_lookup(players_list).get(key)
    if not p:
        return key
    price = p.get("current_price", p.get("initial_price", MIN_PRICE))
    data = "record" if p.get("previous_data") else "no prev."
    return f'{p["name"]} | {p["team_2025_26"]} | {p["position_label"]} | {format_price(price)} | {data}'

def keys_to_players(keys, players_list):
    lookup = player_lookup(players_list)
    return [lookup[k] for k in keys if k in lookup]

def formation_requirements(formation):
    if str(formation).startswith("3 Back"):
        return 3, 2
    return 2, 3

def roster_report(keys, players_list):
    selected = keys_to_players(keys, players_list)
    total_price = sum(float(p.get("current_price", p.get("initial_price", MIN_PRICE))) for p in selected)
    back_count = sum(1 for p in selected if p.get("position_label") == "Back Court")
    front_count = sum(1 for p in selected if p.get("position_label") == "Front Court")
    team_counts = {}
    for p in selected:
        team_counts[p["team_2025_26"]] = team_counts.get(p["team_2025_26"], 0) + 1

    errors = []
    if len(selected) != ROSTER_SIZE:
        errors.append(f"로스터는 정확히 {ROSTER_SIZE}명이어야 합니다.")
    if back_count != ROSTER_BACK_COUNT:
        errors.append(f"Back Court는 정확히 {ROSTER_BACK_COUNT}명이어야 합니다.")
    if front_count != ROSTER_FRONT_COUNT:
        errors.append(f"Front Court는 정확히 {ROSTER_FRONT_COUNT}명이어야 합니다.")
    if total_price > BUDGET_CAP + 1e-9:
        errors.append(f"예산 초과: {format_price(total_price)} / {format_price(BUDGET_CAP)}")
    over_teams = [team for team, count in team_counts.items() if count > MAX_PLAYERS_PER_WKBL_TEAM]
    if over_teams:
        errors.append("한 WKBL 팀에서 최대 2명까지만 선택할 수 있습니다: " + ", ".join(over_teams))

    return {
        "players": selected,
        "total_price": round(total_price, 2),
        "back_count": back_count,
        "front_count": front_count,
        "team_counts": team_counts,
        "errors": errors,
        "valid": len(errors) == 0,
    }

def count_transfers(old_keys, new_keys):
    old_set = set(old_keys)
    new_set = set(new_keys)
    return len(new_set - old_set)

def player_value_score(p, seed=0):
    expected = float(p.get("expected_score", 0.0))
    cumulative = float(p.get("fantasy_score", 0.0))
    games = max(to_int(p.get("games", 0)), 1)
    per_game = expected if expected > 0 else cumulative / games
    price = max(float(p.get("current_price", p.get("initial_price", MIN_PRICE))), MIN_PRICE)
    jitter = ((hash(player_key(p) + str(seed)) % 1000) / 1000) * 0.03
    return per_game / price + jitter

def generate_auto_roster(players_list, seed=0):
    candidates = [p for p in players_list if p.get("position_label") in ["Back Court", "Front Court"]]
    selected = []
    selected_keys = set()
    team_counts = {}

    def can_add(p):
        if player_key(p) in selected_keys:
            return False
        if team_counts.get(p["team_2025_26"], 0) >= MAX_PLAYERS_PER_WKBL_TEAM:
            return False
        return True

    # Start from efficient, low-risk players, then upgrade within cap.
    for pos, needed in [("Back Court", ROSTER_BACK_COUNT), ("Front Court", ROSTER_FRONT_COUNT)]:
        pool = [p for p in candidates if p.get("position_label") == pos]
        pool = sorted(pool, key=lambda p: (-player_value_score(p, seed), float(p.get("current_price", p.get("initial_price", MIN_PRICE)))))
        for p in pool:
            if sum(1 for x in selected if x.get("position_label") == pos) >= needed:
                break
            if can_add(p):
                selected.append(p)
                selected_keys.add(player_key(p))
                team_counts[p["team_2025_26"]] = team_counts.get(p["team_2025_26"], 0) + 1

    # If over budget, replace the lowest value expensive players with cheaper same-position players.
    def total_price():
        return sum(float(p.get("current_price", p.get("initial_price", MIN_PRICE))) for p in selected)

    safety = 0
    while total_price() > BUDGET_CAP and safety < 200:
        safety += 1
        selected_sorted = sorted(selected, key=lambda p: (player_value_score(p, seed), -float(p.get("current_price", p.get("initial_price", MIN_PRICE)))))
        replaced = False
        for out_p in selected_sorted:
            out_pos = out_p.get("position_label")
            out_key = player_key(out_p)
            team_counts[out_p["team_2025_26"]] -= 1
            selected_keys.remove(out_key)
            pool = [
                p for p in candidates
                if p.get("position_label") == out_pos
                and player_key(p) not in selected_keys
                and team_counts.get(p["team_2025_26"], 0) < MAX_PLAYERS_PER_WKBL_TEAM
                and float(p.get("current_price", p.get("initial_price", MIN_PRICE))) < float(out_p.get("current_price", out_p.get("initial_price", MIN_PRICE)))
            ]
            pool = sorted(pool, key=lambda p: (float(p.get("current_price", p.get("initial_price", MIN_PRICE))), -player_value_score(p, seed)))
            if pool:
                in_p = pool[0]
                selected.remove(out_p)
                selected.append(in_p)
                selected_keys.add(player_key(in_p))
                team_counts[in_p["team_2025_26"]] = team_counts.get(in_p["team_2025_26"], 0) + 1
                replaced = True
                break
            selected_keys.add(out_key)
            team_counts[out_p["team_2025_26"]] = team_counts.get(out_p["team_2025_26"], 0) + 1
        if not replaced:
            break

    report = roster_report([player_key(p) for p in selected], players_list)
    if report["valid"]:
        return [player_key(p) for p in selected]

    # Fallback: cheapest legal roster.
    selected = []
    selected_keys = set()
    team_counts = {}
    for pos, needed in [("Back Court", ROSTER_BACK_COUNT), ("Front Court", ROSTER_FRONT_COUNT)]:
        pool = sorted([p for p in candidates if p.get("position_label") == pos], key=lambda p: float(p.get("current_price", p.get("initial_price", MIN_PRICE))))
        for p in pool:
            if sum(1 for x in selected if x.get("position_label") == pos) >= needed:
                break
            if player_key(p) not in selected_keys and team_counts.get(p["team_2025_26"], 0) < MAX_PLAYERS_PER_WKBL_TEAM:
                selected.append(p)
                selected_keys.add(player_key(p))
                team_counts[p["team_2025_26"]] = team_counts.get(p["team_2025_26"], 0) + 1
    return [player_key(p) for p in selected]

def auto_starting_keys(roster_keys, players_list, formation):
    lookup = player_lookup(players_list)
    req_b, req_f = formation_requirements(formation)
    roster_players = [lookup[k] for k in roster_keys if k in lookup]
    backs = sorted([p for p in roster_players if p.get("position_label") == "Back Court"], key=lambda p: player_value_score(p), reverse=True)
    fronts = sorted([p for p in roster_players if p.get("position_label") == "Front Court"], key=lambda p: player_value_score(p), reverse=True)
    return [player_key(p) for p in backs[:req_b] + fronts[:req_f]]

def validate_starting(starting_keys, roster_keys, players_list, formation):
    req_b, req_f = formation_requirements(formation)
    selected = keys_to_players(starting_keys, players_list)
    errors = []
    if len(selected) != STARTERS_COUNT:
        errors.append(f"Starting 5는 정확히 {STARTERS_COUNT}명이어야 합니다.")
    if not set(starting_keys).issubset(set(roster_keys)):
        errors.append("Starting 5는 반드시 내 로스터 안에서만 선택해야 합니다.")
    back_count = sum(1 for p in selected if p.get("position_label") == "Back Court")
    front_count = sum(1 for p in selected if p.get("position_label") == "Front Court")
    if back_count != req_b or front_count != req_f:
        errors.append(f"현재 포메이션은 Back Court {req_b}명, Front Court {req_f}명이 필요합니다.")
    return {"valid": len(errors) == 0, "errors": errors, "back_count": back_count, "front_count": front_count}

def parse_game_date(value):
    value = clean(value)
    if not value:
        return ""
    value = re.sub(r"\([^)]*\)", "", value).strip()
    value = value.replace(".", "-").replace("/", "-")
    parts = value.split("-")
    if len(parts) == 3:
        y, m, d = parts
        if len(y) == 2:
            y = "20" + y
        return f"{int(y):04d}-{int(m):02d}-{int(d):02d}"
    if len(parts) == 2:
        m, d = parts
        year = 2025 if int(m) >= 11 else 2026
        return f"{year:04d}-{int(m):02d}-{int(d):02d}"
    return value

def canonicalize_game_row(row):
    p = canonicalize_row(row)
    p["name"] = get_any(row, ["player", "name", "선수"])
    p["team_2025_26"] = canonical_team(get_any(row, ["team", "team_2025_26", "팀", "소속구단"]))
    p["position_label"] = normalize_position(p.get("position", ""))
    p["position_short"] = position_short(p.get("position", ""))
    p["date"] = parse_game_date(get_any(row, ["date", "game_date", "날짜", "일자"]))
    p["time"] = get_any(row, ["time", "game_time", "시간"])
    p["gameweek"] = get_any(row, ["gameweek", "gw", "Gameweek", "GAMEWEEK"])
    p["day"] = get_any(row, ["day", "gameday", "Day", "DAY"])
    p["game_id"] = get_any(row, ["game_id", "game", "match_id", "경기ID"])
    p["home_team"] = canonical_team(get_any(row, ["home_team", "home", "홈팀"]))
    p["away_team"] = canonical_team(get_any(row, ["away_team", "away", "원정팀"]))
    p["home_score"] = get_any(row, ["home_score", "홈점수"])
    p["away_score"] = get_any(row, ["away_score", "원정점수"])
    p["venue"] = get_any(row, ["venue", "경기장"])
    return p


GAME_RESULT_FIELDNAMES = [
    "game_id", "date", "time", "gameweek", "day", "home_team", "away_team", "home_score", "away_score", "venue",
    "player", "team", "pos", "min", "2pmade", "2pa", "3pmade", "3pa", "ftm", "fta", "oreb", "dreb", "ast", "stl", "blk", "to", "pts"
]

RAW_PLAYER_ROW_RE = re.compile(
    r"^(.+?)\s+([GFC])\s+"
    r"(\d{1,3}:\d{1,2})\s+"
    r"(\d+\s*-\s*\d+)\s+"
    r"(\d+\s*-\s*\d+)\s+"
    r"(\d+\s*-\s*\d+)\s+"
    r"(\d+)\s+(\d+)\s+(\d+)\s+"
    r"(\d+)\s+(\d+)\s+(\d+)\s+(\d+)\s+(\d+)\s+(\d+)\s*$"
)

RAW_TEAM_TOTAL_RE = re.compile(
    r"^팀합계\s+(\d{1,3}:\d{2})\s+"
    r"(\d+\s*-\s*\d+)\s+"
    r"(\d+\s*-\s*\d+)\s+"
    r"(\d+\s*-\s*\d+)\s+"
    r"(\d+)\s+(\d+)\s+(\d+)\s+"
    r"(\d+)\s+(\d+)\s+(\d+)\s+(\d+)\s+(\d+)\s+(\d+)\s*$"
)

def split_pair(raw_pair):
    raw_pair = clean(raw_pair).replace(" ", "")
    if "-" not in raw_pair:
        return "0", "0"
    a, b = raw_pair.split("-", 1)
    return clean(a), clean(b)

def raw_line_team_candidate(line):
    """Return a canonical team if a raw line is exactly, or ends with, a WKBL team name."""
    raw = clean(line)
    compact = raw.replace(" ", "")
    for team in SIMULATION_TEAMS:
        t = team.replace(" ", "")
        if compact == t or compact.endswith(t):
            return team
    return ""

def normalize_raw_results_text(raw_text):
    raw_text = raw_text.replace("\r", "\n").replace("\t", "\n")
    # Sometimes a date is glued to the previous numeric value, e.g. ...0 11/17(월).
    raw_text = re.sub(r"(?<!\n)(?=(?:1[0-2]|[1-9])/\d{1,2}\([^)]*\))", "\n", raw_text)
    # Put likely team-table starts on their own line when they are glued to venue/time text.
    for team in ["BNK 썸", "BNK썸", "신한은행", "하나은행", "우리은행", "삼성생명", "KB스타즈"]:
        raw_text = re.sub(rf"(?<!\n)({re.escape(team)})\s*\n\s*선수", rf"\n\1\n선수", raw_text)
    return raw_text

def parse_raw_game_results_text(raw_text):
    """
    Parse the copied WKBL box-score text into app-ready game-result rows.

    Expected row pattern:
    선수 POS MIN 2PM-A 3PM-A FTM-A OFF DEF TOT AST PF ST TO BS PTS
    Team total rows are used only to infer the final score.
    """
    raw_text = normalize_raw_results_text(raw_text)
    pieces = re.split(r"(?m)^\s*((?:1[0-2]|[1-9])/\d{1,2}\([^)]*\))\s*", raw_text)
    parsed_rows = []
    game_id = 1
    seen_game_signatures = set()

    # pieces[0] is text before the first date marker.
    for i in range(1, len(pieces), 2):
        date_token = pieces[i]
        block = pieces[i + 1] if i + 1 < len(pieces) else ""
        date = parse_game_date(date_token)
        lines = [clean(x) for x in block.split("\n") if clean(x)]
        if not lines:
            continue

        # Extract game time only from the pre-table area, not player minutes.
        first_player_header = next((idx for idx, line in enumerate(lines) if "선수" in line and "POS" in line.upper()), len(lines))
        header_text = " ".join(lines[:first_player_header])
        times = re.findall(r"\b\d{1,2}:\d{2}\b", header_text)
        game_time = times[-1] if times else ""

        team_blocks = []
        current = None
        active = False

        for idx, line in enumerate(lines):
            next_lines = " ".join(lines[idx + 1:idx + 4])
            team_candidate = raw_line_team_candidate(line)
            if team_candidate and "선수" in next_lines and "POS" in next_lines.upper():
                current = {"team": canonical_team(team_candidate), "rows": [], "score": ""}
                team_blocks.append(current)
                active = True
                continue

            if not active or current is None:
                continue
            if line.startswith("선수") or line in ["OFF", "DEF", "TOT"] or line == "OFF DEF TOT":
                continue

            total_match = RAW_TEAM_TOTAL_RE.match(line)
            if total_match:
                current["score"] = total_match.group(13)
                continue

            row_match = RAW_PLAYER_ROW_RE.match(line)
            if not row_match:
                continue

            name, pos, minute, two_pair, three_pair, ft_pair, off, deff, tot, ast, pf, st, to, bs, pts = row_match.groups()
            two_m, two_a = split_pair(two_pair)
            three_m, three_a = split_pair(three_pair)
            ft_m, ft_a = split_pair(ft_pair)
            current["rows"].append({
                "player": clean(name),
                "team": current["team"],
                "pos": clean(pos),
                "min": clean(minute),
                "2pmade": two_m,
                "2pa": two_a,
                "3pmade": three_m,
                "3pa": three_a,
                "ftm": ft_m,
                "fta": ft_a,
                "oreb": off,
                "dreb": deff,
                "ast": ast,
                "stl": st,
                "blk": bs,
                "to": to,
                "pts": pts,
            })

        # Pair team tables into games. Usually each date block has exactly two team tables.
        for pair_start in range(0, len(team_blocks) - 1, 2):
            home = team_blocks[pair_start]
            away = team_blocks[pair_start + 1]
            if not home["rows"] or not away["rows"]:
                continue

            signature = (
                date,
                home["team"], away["team"],
                home.get("score", ""), away.get("score", ""),
                tuple((r["player"], r["pts"], r["min"]) for r in home["rows"]),
                tuple((r["player"], r["pts"], r["min"]) for r in away["rows"]),
            )
            if signature in seen_game_signatures:
                continue
            seen_game_signatures.add(signature)

            for source in [home, away]:
                for player_row in source["rows"]:
                    parsed_rows.append({
                        "game_id": str(game_id),
                        "date": date,
                        "time": game_time,
                        "gameweek": "",
                        "day": "",
                        "home_team": home["team"],
                        "away_team": away["team"],
                        "home_score": home.get("score", ""),
                        "away_score": away.get("score", ""),
                        "venue": "",
                        **player_row,
                    })
            game_id += 1

    return parsed_rows

def read_raw_game_results_if_available():
    if not RAW_GAME_RESULTS_PATH.exists():
        return []
    for enc in ["utf-8-sig", "utf-8", "cp949"]:
        try:
            raw_text = RAW_GAME_RESULTS_PATH.read_text(encoding=enc)
            return parse_raw_game_results_text(raw_text)
        except UnicodeDecodeError:
            continue
    return []

def game_rows_to_csv_text(rows):
    from io import StringIO
    output = StringIO()
    writer = csv.DictWriter(output, fieldnames=GAME_RESULT_FIELDNAMES, extrasaction="ignore")
    writer.writeheader()
    for row in rows:
        writer.writerow(row)
    return output.getvalue()

def load_game_results():
    if GAME_RESULTS_PATH.exists():
        rows, encoding_used = read_csv_with_fallback(GAME_RESULTS_PATH)
    else:
        rows = read_raw_game_results_if_available()
        encoding_used = "raw_game_results_2025_26.txt" if rows else None
        if not rows:
            return [], [], None

    parsed = []
    for idx, row in enumerate(rows):
        p = canonicalize_game_row(row)
        if not p["name"] or p["name"] == "팀합계":
            continue
        if not p["team_2025_26"]:
            continue
        if not p["game_id"]:
            base = "__".join([p.get("date", ""), p.get("time", ""), p.get("home_team", ""), p.get("away_team", "")]).strip("_")
            p["game_id"] = base if base else f"game_{idx+1}"
        p["row_order"] = idx
        p["game_score"] = fantasy_score(p)
        parsed.append(p)

    # Sort and infer gameweek/day if not supplied.
    parsed.sort(key=lambda r: (r.get("date", ""), r.get("time", ""), str(r.get("game_id", "")), r.get("row_order", 0)))

    game_order = []
    groups = {}
    for r in parsed:
        gid = str(r["game_id"])
        if gid not in groups:
            groups[gid] = {
                "game_id": gid,
                "date": r.get("date", ""),
                "time": r.get("time", ""),
                "gameweek": r.get("gameweek", ""),
                "day": r.get("day", ""),
                "home_team": r.get("home_team", ""),
                "away_team": r.get("away_team", ""),
                "home_score": r.get("home_score", ""),
                "away_score": r.get("away_score", ""),
                "venue": r.get("venue", ""),
                "rows": [],
            }
            game_order.append(gid)
        groups[gid]["rows"].append(r)
        for field in ["date", "time", "gameweek", "day", "home_team", "away_team", "home_score", "away_score", "venue"]:
            if not groups[gid].get(field) and r.get(field):
                groups[gid][field] = r.get(field)

    games = [groups[gid] for gid in game_order]

    dates = sorted({g["date"] for g in games if g.get("date")})
    first_date = dates[0] if dates else ""
    date_to_week = {}
    date_to_day = {}
    if first_date:
        from datetime import datetime
        first = datetime.strptime(first_date, "%Y-%m-%d")
        grouped_dates = {}
        for d in dates:
            cur = datetime.strptime(d, "%Y-%m-%d")
            week = (cur - first).days // 7 + 1
            grouped_dates.setdefault(week, []).append(d)
            date_to_week[d] = week
        for week, ds in grouped_dates.items():
            for i, d in enumerate(sorted(ds), start=1):
                date_to_day[d] = i

    for i, g in enumerate(games, start=1):
        if not g.get("gameweek"):
            g["gameweek"] = date_to_week.get(g.get("date"), i)
        else:
            g["gameweek"] = to_int(g.get("gameweek"), i)
        if not g.get("day"):
            g["day"] = date_to_day.get(g.get("date"), 1)
        else:
            g["day"] = to_int(g.get("day"), 1)

    return parsed, games, encoding_used

def reset_simulation_runtime(players_list):
    reset_market_state(players_list)
    st.session_state.simulation_game_index = 0
    st.session_state.simulation_history = []
    st.session_state.simulation_team_scores = {team: 0.0 for team in SIMULATION_TEAMS}
    st.session_state.simulation_team_rosters = {}
    st.session_state.simulation_team_starting = {}
    st.session_state.simulation_team_captains = {}
    st.session_state.user_transfers = 0
    st.session_state.chip_captain_active = False
    st.session_state.chip_wildcard_active = False
    st.session_state.chip_wildcard_available = True
    st.session_state.chip_allstar_active = False
    st.session_state.chip_allstar_available = True
    st.session_state.simulation_ai_ready = False

def initialize_ai_managers(players_list):
    user_team = st.session_state.simulation_user_team
    for idx, team in enumerate(SIMULATION_TEAMS):
        if team == user_team:
            roster = st.session_state.user_roster_keys
            formation = st.session_state.user_formation
            starting = st.session_state.user_starting_keys or auto_starting_keys(roster, players_list, formation)
            captain = st.session_state.user_captain_key or (starting[0] if starting else None)
        else:
            roster = generate_auto_roster(players_list, seed=idx + 17)
            formation = "2 Back Court / 3 Front Court"
            starting = auto_starting_keys(roster, players_list, formation)
            captain = starting[0] if starting else None
        st.session_state.simulation_team_rosters[team] = roster
        st.session_state.simulation_team_starting[team] = starting
        st.session_state.simulation_team_captains[team] = captain
    st.session_state.simulation_ai_ready = True

def sync_user_lineup_to_simulation():
    team = st.session_state.simulation_user_team
    if team:
        st.session_state.simulation_team_rosters[team] = list(st.session_state.user_roster_keys)
        st.session_state.simulation_team_starting[team] = list(st.session_state.user_starting_keys)
        st.session_state.simulation_team_captains[team] = st.session_state.user_captain_key

def start_simulation(user_team):
    st.session_state.simulation_user_team = user_team
    st.session_state.simulation_game_no = 1
    st.session_state.simulation_started = True
    st.session_state.simulation_league = [
        {
            "Rank": 1,
            "Team": team,
            "Manager": "You" if team == user_team else "AI",
            "Points": 0.0,
            "Transfers": 0,
            "Budget": format_price(BUDGET_CAP),
        }
        for team in SIMULATION_TEAMS
    ]

def update_league_table_from_scores():
    league = []
    for team in SIMULATION_TEAMS:
        league.append({
            "Rank": 1,
            "Team": team,
            "Manager": "You" if team == st.session_state.simulation_user_team else "AI",
            "Points": round(float(st.session_state.simulation_team_scores.get(team, 0.0)), 2),
            "Transfers": st.session_state.user_transfers if team == st.session_state.simulation_user_team else 0,
            "Budget": format_price(BUDGET_CAP),
        })
    st.session_state.simulation_league = league

def process_one_game(game, players_list):
    if not st.session_state.simulation_ai_ready:
        initialize_ai_managers(players_list)
    sync_user_lineup_to_simulation()

    lookup = player_lookup(players_list)
    row_scores = {}
    price_changes = []
    played_keys = set()

    for row in game["rows"]:
        k = player_key(row)
        played_keys.add(k)
        score = float(row.get("game_score", fantasy_score(row)))
        row_scores[k] = score
        state = st.session_state.market_state.get(k)
        if state is None:
            # New or unmatched player from the game file.
            state = {
                "current_price": MIN_PRICE,
                "expected_score": 0.0,
                "last_change": 0.0,
                "games_played_2025_26": 0,
            }
            st.session_state.market_state[k] = state

        old_price = float(state.get("current_price", MIN_PRICE))
        expected = float(state.get("expected_score", 0.0))
        new_price, change, new_expected = update_player_price(old_price, score, expected, played=True)
        state["current_price"] = new_price
        state["expected_score"] = new_expected
        state["last_change"] = change
        state["games_played_2025_26"] = int(state.get("games_played_2025_26", 0)) + 1

        display_name = row.get("name", k)
        display_team = row.get("team_2025_26", "")
        if k in lookup:
            display_name = lookup[k].get("name", display_name)
            display_team = lookup[k].get("team_2025_26", display_team)
        price_changes.append({
            "Player": display_name,
            "Team": display_team,
            "Game Score": round(score, 2),
            "Old Price": format_price(old_price),
            "Change": f'{change:+.2f}억원',
            "New Price": format_price(new_price),
        })

    game_team_points = {}
    for fantasy_team in SIMULATION_TEAMS:
        starting = st.session_state.simulation_team_starting.get(fantasy_team, [])
        captain = st.session_state.simulation_team_captains.get(fantasy_team)
        base_points = 0.0
        for k in starting:
            base_points += row_scores.get(k, 0.0)

        bonus_points = 0.0
        if fantasy_team == st.session_state.simulation_user_team:
            if st.session_state.chip_captain_active and captain:
                bonus_points += row_scores.get(captain, 0.0)
            if st.session_state.chip_allstar_active:
                bonus_points += base_points * 0.20

        total = base_points + bonus_points
        st.session_state.simulation_team_scores[fantasy_team] = round(
            float(st.session_state.simulation_team_scores.get(fantasy_team, 0.0)) + total,
            2,
        )
        game_team_points[fantasy_team] = round(total, 2)

    st.session_state.chip_captain_active = False
    if st.session_state.chip_allstar_active:
        st.session_state.chip_allstar_active = False

    st.session_state.simulation_history.append({
        "game_id": game.get("game_id", ""),
        "date": game.get("date", ""),
        "time": game.get("time", ""),
        "gameweek": game.get("gameweek", ""),
        "day": game.get("day", ""),
        "match": f'{game.get("home_team", "")} {game.get("home_score", "")} vs {game.get("away_team", "")} {game.get("away_score", "")}',
        "team_points": game_team_points,
        "price_changes": sorted(price_changes, key=lambda x: abs(float(x["Change"].replace("억원",""))), reverse=True)[:10],
    })

    st.session_state.simulation_game_index += 1
    st.session_state.simulation_game_no = st.session_state.simulation_game_index + 1
    update_league_table_from_scores()
    apply_market_state(players_list)

def process_next_gameweek(games, players_list):
    idx = st.session_state.simulation_game_index
    if idx >= len(games):
        return
    gw = games[idx].get("gameweek")
    while st.session_state.simulation_game_index < len(games) and games[st.session_state.simulation_game_index].get("gameweek") == gw:
        process_one_game(games[st.session_state.simulation_game_index], players_list)

def game_results_template():
    return (
        "game_id,date,time,gameweek,day,home_team,away_team,home_score,away_score,venue,"
        "player,team,pos,min,2pmade,2pa,3pmade,3pa,ftm,fta,oreb,dreb,ast,stl,blk,to,pts\n"
        "1,2025-11-16,14:25,1,1,BNK썸,신한은행,65,54,부산사직실내체육관,"
        "안혜지,BNK썸,G,35:02,5,10,0,1,0,0,0,1,5,0,0,2,10\n"
        "1,2025-11-16,14:25,1,1,BNK썸,신한은행,65,54,부산사직실내체육관,"
        "신이슬,신한은행,G,40:00,3,5,3,6,2,2,0,2,1,2,1,3,17"
    )

initialize_fantasy_state(players)
apply_market_state(players)
game_rows_2025_26, games_2025_26, game_csv_encoding = load_game_results()
game_csv_loaded = len(games_2025_26) > 0

# =========================
# Sidebar
# =========================
with st.sidebar:
    st.markdown("## 🏀 WKBL Fantasy")
    language = st.selectbox("Country / Language", ["한국어 (KR)", "English"])
    st.markdown("### Data")
    if csv_loaded:
        st.success(f"players_2024_25.csv loaded ({csv_encoding})")
    else:
        st.warning("players_2024_25.csv not found. Showing sample data.")

    if game_csv_loaded:
        if game_csv_encoding == "raw_game_results_2025_26.txt":
            st.success("raw_game_results_2025_26.txt parsed and connected")
            st.caption(f"Games: {len(games_2025_26)} / Player rows: {len(game_rows_2025_26)}")
            raw_rows_for_download = read_raw_game_results_if_available()
            if raw_rows_for_download:
                st.download_button(
                    "Download converted game_results_2025_26.csv",
                    data=game_rows_to_csv_text(raw_rows_for_download),
                    file_name="game_results_2025_26.csv",
                    mime="text/csv",
                    use_container_width=True,
                )
        else:
            st.success(f"game_results_2025_26.csv loaded ({game_csv_encoding})")
            st.caption(f"Games: {len(games_2025_26)} / Player rows: {len(game_rows_2025_26)}")
    else:
        st.info("game_results_2025_26.csv not connected yet. You can also upload raw_game_results_2025_26.txt.")

    st.caption("시간 기준: KST")
    st.caption("Prototype version")

header()
nav()
page = st.session_state.page

# Common derived values
players_with_data = [p for p in players if p["previous_data"]]
players_no_data = [p for p in players if not p["previous_data"]]
max_score = max([p["fantasy_score"] for p in players_with_data] or [0])
highest_player = max(players_with_data, key=lambda p: p["fantasy_score"], default=None)

# =========================
# Pages
# =========================
if page == "Home":
    st.markdown("""
    <div class="hero">
        <div class="hero-title">WKBL Fantasy Game</div>
        <div style="font-size:19px;max-width:760px;line-height:1.7;">
            2024-25 누적 기록으로 2025-26 시즌 초기 가격을 설정하고,
            굿디펜스를 제외한 WKBL 공헌도 기반 Fantasy Score로 경쟁하세요.
        </div>
    </div>
    """, unsafe_allow_html=True)

    st.write("")
    c1, c2, c3, c4 = st.columns(4)
    with c1:
        summary_card("TOTAL PLAYERS", len(players), "👥")
    with c2:
        summary_card("WITH PREV. DATA", len(players_with_data), "📊")
    with c3:
        summary_card("NO PREV. DATA", len(players_no_data), "🆕")
    with c4:
        if highest_player:
            summary_card("TOP BASE SCORE", f"{max_score:.2f}", "⭐", highest_player["name"])
        else:
            summary_card("TOP BASE SCORE", "0.00", "⭐")

    st.write("")
    cols = st.columns(4)
    features = [
        ("👥", "BUILD YOUR ROSTER", "14억원 예산 안에서 WKBL 선수 10명으로 나만의 로스터를 구성하세요."),
        ("🎮", "RUN SIMULATION", "2025-26 시즌 첫 경기부터 6개 팀 리그 시뮬레이션을 진행하세요."),
        ("📋", "SET YOUR LINE-UP", "다음 경기일에 출전할 선발 5명을 선택하고 실제 경기 기록으로 점수를 얻으세요."),
        ("🔁", "PRICE UPDATES", "경기별 공헌도 누적에 따라 선수 가격이 변동되도록 확장할 수 있습니다."),
    ]
    for col, (icon, title, body) in zip(cols, features):
        with col:
            st.markdown(f"""
            <div class="feature">
                <div style="font-size:34px;">{icon}</div>
                <div class="feature-title">{title}</div>
                <div style="color:#475569;line-height:1.65;">{body}</div>
            </div>
            """, unsafe_allow_html=True)

elif page == "My Team":
    st.markdown("You are logged in as <b style='color:#E91E73;'>Chaeyoung Song</b>.", unsafe_allow_html=True)

    user_team = st.session_state.simulation_user_team or "Not selected"
    total_points = 0.0
    if st.session_state.simulation_user_team:
        total_points = st.session_state.simulation_team_scores.get(st.session_state.simulation_user_team, 0.0)

    next_label = "No game file"
    if game_csv_loaded:
        idx = min(st.session_state.simulation_game_index, len(games_2025_26) - 1)
        g = games_2025_26[idx]
        next_label = f'{g.get("gameweek")} - Day {g.get("day")}'

    c1, c2, c3, c4, c5 = st.columns(5)
    with c1:
        summary_card("GAMEWEEK", next_label, "📅")
    with c2:
        summary_card("NEXT DEADLINE", "30 min before", "⏰", "KST")
    with c3:
        remaining_free = max(FREE_TRANSFERS_PER_GAMEWEEK - st.session_state.user_transfers, 0)
        summary_card("FREE TRANSFERS", remaining_free, "🔁")
    with c4:
        summary_card("TOTAL POINTS", f"{total_points:.2f}", "⭐")
    with c5:
        summary_card("MY MANAGER TEAM", user_team, "📊")

    left, right = st.columns([4, 1.15])
    all_keys = [player_key(p) for p in sorted(players, key=lambda p: p.get("current_price", p.get("initial_price", MIN_PRICE)), reverse=True)]

    with left:
        st.markdown('<div class="section-title">BUILD YOUR ROSTER</div>', unsafe_allow_html=True)
        st.caption("조건: 총 10명, Back Court 5명 + Front Court 5명, 예산 14억원, 같은 WKBL 팀 최대 2명.")

        if st.button("Auto-generate valid roster", use_container_width=True):
            st.session_state.user_roster_keys = generate_auto_roster(players, seed=99)
            st.session_state.user_starting_keys = auto_starting_keys(st.session_state.user_roster_keys, players, st.session_state.user_formation)
            if st.session_state.user_starting_keys:
                st.session_state.user_captain_key = st.session_state.user_starting_keys[0]
            sync_user_lineup_to_simulation()
            st.rerun()

        selected_keys = st.multiselect(
            "Select 10 players",
            options=all_keys,
            default=[k for k in st.session_state.user_roster_keys if k in all_keys],
            format_func=lambda k: label_for_key(k, players),
            placeholder="선수 10명을 선택하세요.",
        )

        report = roster_report(selected_keys, players)
        s1, s2, s3, s4 = st.columns(4)
        with s1:
            summary_card("SELECTED", f'{len(report["players"])}/10', "👥")
        with s2:
            summary_card("BUDGET USED", format_price(report["total_price"]), "💰")
        with s3:
            summary_card("BACK COURT", f'{report["back_count"]}/5', "B")
        with s4:
            summary_card("FRONT COURT", f'{report["front_count"]}/5', "F")

        if report["errors"]:
            for err in report["errors"]:
                st.error(err)
        else:
            st.success("저장 가능한 로스터입니다.")

        if st.button("Save Roster", use_container_width=True, disabled=not report["valid"]):
            old_roster = list(st.session_state.user_roster_keys)
            if st.session_state.simulation_started and not st.session_state.chip_wildcard_active:
                st.session_state.user_transfers += count_transfers(old_roster, selected_keys)
            st.session_state.user_roster_keys = list(selected_keys)
            st.session_state.user_starting_keys = auto_starting_keys(st.session_state.user_roster_keys, players, st.session_state.user_formation)
            if st.session_state.user_captain_key not in st.session_state.user_starting_keys:
                st.session_state.user_captain_key = st.session_state.user_starting_keys[0] if st.session_state.user_starting_keys else None
            if st.session_state.chip_wildcard_active:
                st.session_state.chip_wildcard_active = False
            sync_user_lineup_to_simulation()
            st.success("Roster saved.")
            st.rerun()

        st.write("")
        st.markdown('<div class="section-title">SET YOUR LINE-UP</div>', unsafe_allow_html=True)

        formation = st.radio(
            "Formation",
            ["2 Back Court / 3 Front Court", "3 Back Court / 2 Front Court"],
            horizontal=True,
            index=0 if st.session_state.user_formation == "2 Back Court / 3 Front Court" else 1,
        )
        if formation != st.session_state.user_formation:
            st.session_state.user_formation = formation
            st.session_state.user_starting_keys = auto_starting_keys(st.session_state.user_roster_keys, players, formation)
            if st.session_state.user_starting_keys:
                st.session_state.user_captain_key = st.session_state.user_starting_keys[0]
            sync_user_lineup_to_simulation()
            st.rerun()

        req_b, req_f = formation_requirements(st.session_state.user_formation)
        roster_keys = list(st.session_state.user_roster_keys)
        roster_players = keys_to_players(roster_keys, players)
        back_options = [player_key(p) for p in roster_players if p.get("position_label") == "Back Court"]
        front_options = [player_key(p) for p in roster_players if p.get("position_label") == "Front Court"]

        default_start = st.session_state.user_starting_keys or auto_starting_keys(roster_keys, players, st.session_state.user_formation)
        default_b = [k for k in default_start if k in back_options][:req_b]
        default_f = [k for k in default_start if k in front_options][:req_f]

        bc_selected = st.multiselect(
            f"Starting Back Court {req_b}명",
            options=back_options,
            default=default_b,
            format_func=lambda k: label_for_key(k, players),
        )
        fc_selected = st.multiselect(
            f"Starting Front Court {req_f}명",
            options=front_options,
            default=default_f,
            format_func=lambda k: label_for_key(k, players),
        )

        proposed_starting = list(bc_selected) + list(fc_selected)
        start_report = validate_starting(proposed_starting, roster_keys, players, st.session_state.user_formation)
        if start_report["errors"]:
            for err in start_report["errors"]:
                st.warning(err)

        if st.button("Save Starting 5", use_container_width=True, disabled=not start_report["valid"]):
            st.session_state.user_starting_keys = proposed_starting
            if st.session_state.user_captain_key not in proposed_starting:
                st.session_state.user_captain_key = proposed_starting[0] if proposed_starting else None
            sync_user_lineup_to_simulation()
            st.success("Starting 5 saved.")
            st.rerun()

        if st.session_state.user_starting_keys:
            captain_options = st.session_state.user_starting_keys
            if st.session_state.user_captain_key not in captain_options:
                st.session_state.user_captain_key = captain_options[0]
            st.session_state.user_captain_key = st.selectbox(
                "Captain for Gameday Captain chip",
                options=captain_options,
                index=captain_options.index(st.session_state.user_captain_key),
                format_func=lambda k: label_for_key(k, players),
            )

        st.markdown('<div class="court"><b style="color:#064EA4;">STARTING 5</b>', unsafe_allow_html=True)
        cols = st.columns(5)
        for col, p in zip(cols, keys_to_players(st.session_state.user_starting_keys, players)):
            with col:
                player_card(p, captain=(player_key(p) == st.session_state.user_captain_key))
        st.markdown('</div>', unsafe_allow_html=True)

        bench_keys = [k for k in st.session_state.user_roster_keys if k not in st.session_state.user_starting_keys]
        st.markdown('<div class="bench"><b style="color:#E91E73;">BENCH</b>', unsafe_allow_html=True)
        cols = st.columns(5)
        for i, (col, p) in enumerate(zip(cols, keys_to_players(bench_keys, players)), start=1):
            with col:
                player_card(p, priority=i)
        st.markdown('</div>', unsafe_allow_html=True)

    with right:
        st.markdown('<div class="panel"><div class="panel-title">CHIPS</div>', unsafe_allow_html=True)
        st.markdown('</div>', unsafe_allow_html=True)

        if st.session_state.chip_captain_active:
            st.success("👑 Gameday Captain active for next simulated game.")
        else:
            if st.button("👑 Play Gameday Captain", use_container_width=True, disabled=not bool(st.session_state.user_starting_keys)):
                st.session_state.chip_captain_active = True
                st.rerun()

        if st.session_state.chip_wildcard_active:
            st.success("🛡️ Wildcard active. Your next roster save will not count transfers.")
        elif st.session_state.chip_wildcard_available:
            if st.button("🛡️ Play Wildcard", use_container_width=True):
                st.session_state.chip_wildcard_available = False
                st.session_state.chip_wildcard_active = True
                st.rerun()
        else:
            st.button("🛡️ Wildcard Used", use_container_width=True, disabled=True)

        if st.session_state.chip_allstar_active:
            st.success("⭐ All-Star active for next simulated game. Starting 5 get +20%.")
        elif st.session_state.chip_allstar_available:
            if st.button("⭐ Play All-Star", use_container_width=True, disabled=not bool(st.session_state.user_starting_keys)):
                st.session_state.chip_allstar_available = False
                st.session_state.chip_allstar_active = True
                st.rerun()
        else:
            st.button("⭐ All-Star Used", use_container_width=True, disabled=True)

        st.markdown('<div class="panel"><div class="panel-title">TRANSACTIONS</div>', unsafe_allow_html=True)
        st.markdown(f"<b>🔁 Used this gameweek: {st.session_state.user_transfers}</b>", unsafe_allow_html=True)
        if st.session_state.chip_wildcard_active:
            st.markdown("<b style='color:#E91E73;'>Wildcard active</b>", unsafe_allow_html=True)
        st.markdown('</div>', unsafe_allow_html=True)

        st.markdown('<div class="panel"><div class="panel-title">LEAGUE TABLE</div>', unsafe_allow_html=True)
        league_rows = sorted(
            [(team, st.session_state.simulation_team_scores.get(team, 0.0)) for team in SIMULATION_TEAMS],
            key=lambda x: (-x[1], x[0])
        )
        for rank, (team, points) in enumerate(league_rows, start=1):
            cls = "league-row league-me" if team == st.session_state.simulation_user_team else "league-row"
            st.markdown(f'<div class="{cls}"><span>{rank} &nbsp; {team}</span><span>{points:.2f}</span></div>', unsafe_allow_html=True)
        st.markdown('</div>', unsafe_allow_html=True)

elif page == "Players":
    st.markdown('<div class="section-title">PLAYER MARKET</div>', unsafe_allow_html=True)

    teams = ["All"] + sorted(set([p["team_2025_26"] for p in players if p["team_2025_26"]]))
    positions = ["All", "Back Court", "Front Court", "Not Set"]

    f1, f2, f3 = st.columns([2, 1, 1])
    with f1:
        search = st.text_input("Search player / team")
    with f2:
        team_filter = st.selectbox("Team", teams)
    with f3:
        pos_filter = st.selectbox("Position", positions)

    filtered = players
    if search:
        s = search.lower()
        filtered = [p for p in filtered if s in f'{p["name"]} {p["team_2025_26"]}'.lower()]
    if team_filter != "All":
        filtered = [p for p in filtered if p["team_2025_26"] == team_filter]
    if pos_filter != "All":
        filtered = [p for p in filtered if p["position_label"] == pos_filter]

    # Default order: highest initial price first.
    filtered = sorted(filtered, key=lambda p: p.get("current_price", p.get("initial_price", MIN_PRICE)), reverse=True)

    selected = None
    for p in players:
        if player_key(p) == st.session_state.selected_player_key:
            selected = p
            break

    if selected:
        player_detail_panel(selected)
        st.write("")

    st.markdown("### Player List")
    header_cols = st.columns([1.45, 1.05, 1.15, 1.1, 1.0, 0.65])
    header_cols[0].markdown("**Name**")
    header_cols[1].markdown("**Team**")
    header_cols[2].markdown("**Position**")
    header_cols[3].markdown("**Fantasy Score**")
    header_cols[4].markdown("**Initial Price**")
    header_cols[5].markdown("**Data**")
    st.markdown("<hr style='margin: 0.3rem 0 0.6rem 0;'>", unsafe_allow_html=True)

    for idx, p in enumerate(filtered):
        row_cols = st.columns([1.45, 1.05, 1.15, 1.1, 1.0, 0.65])
        row_cols[0].write(p["name"])
        row_cols[1].write(p["team_2025_26"])
        row_cols[2].write(p["position_label"])
        row_cols[3].write(f'{p["fantasy_score"]:.2f}' if p["previous_data"] else "-")
        row_cols[4].write(format_price(p.get("current_price", p.get("initial_price", MIN_PRICE))))
        label = "Data" if p["previous_data"] else "Card"
        if row_cols[5].button(label, key=f"data_btn_{idx}_{player_key(p)}"):
            st.session_state.selected_player_key = player_key(p)
            st.rerun()

    st.markdown("### Player Cards")
    cols = st.columns(5)
    for idx, p in enumerate(filtered[:30]):
        with cols[idx % 5]:
            player_card(p)

elif page == "Simulation":
    st.markdown('<div class="section-title">SIMULATION</div>', unsafe_allow_html=True)

    st.markdown("""
    <div class="panel">
        <div class="panel-title">2025-26 WKBL Fantasy Simulation</div>
        <div style="line-height:1.7;color:#475569;">
            전체 경기 결과 CSV를 연결하면 경기 순서대로 Fantasy Score, Gameweek/Day 점수,
            선수 가격 변동, 사용자 팀과 AI 5개 팀의 리그 점수를 자동 계산합니다.
        </div>
    </div>
    """, unsafe_allow_html=True)

    c1, c2, c3, c4 = st.columns(4)
    with c1:
        summary_card("SALARY CAP", format_price(BUDGET_CAP), "💰")
    with c2:
        summary_card("PRICE CAP", f"{format_price(MIN_PRICE)} ~ {format_price(MAX_PRICE)}", "⭐")
    with c3:
        summary_card("PRICE MOVE", f"±{MAX_PRICE_CHANGE_PER_GAME:.2f}억원", "📈")
    with c4:
        summary_card("GAME FILE", f"{len(games_2025_26)} games" if game_csv_loaded else "Not loaded", "📄")

    st.write("")

    if not game_csv_loaded:
        st.warning("아직 game_results_2025_26.csv가 GitHub에 없습니다. 아래 형식으로 파일을 만들어 app.py와 같은 위치에 올리면 자동 연결됩니다.")
        st.code(game_results_template(), language="csv")
        st.download_button(
            "Download game_results_2025_26.csv template",
            data=game_results_template().encode("utf-8-sig"),
            file_name="game_results_2025_26.csv",
            mime="text/csv",
            use_container_width=True,
        )
    else:
        with st.expander("Loaded schedule preview", expanded=False):
            preview_rows = []
            for g in games_2025_26[:15]:
                preview_rows.append({
                    "Game": g["game_id"],
                    "Date": g["date"],
                    "GW": g["gameweek"],
                    "Day": g["day"],
                    "Match": f'{g.get("home_team","")} {g.get("home_score","")} vs {g.get("away_team","")} {g.get("away_score","")}',
                    "Rows": len(g["rows"]),
                })
            st.markdown(table_html(preview_rows, ["Game", "Date", "GW", "Day", "Match", "Rows"]), unsafe_allow_html=True)

    if not st.session_state.simulation_started:
        selected_team = st.selectbox("내가 운영할 팀 선택", SIMULATION_TEAMS)
        st.caption("선택하지 않은 5개 팀은 AI가 자동으로 로스터와 선발을 구성합니다.")

        if st.button("Start 2025-26 Simulation", use_container_width=True):
            if not st.session_state.user_roster_keys:
                st.session_state.user_roster_keys = generate_auto_roster(players, seed=101)
                st.session_state.user_starting_keys = auto_starting_keys(st.session_state.user_roster_keys, players, st.session_state.user_formation)
                st.session_state.user_captain_key = st.session_state.user_starting_keys[0] if st.session_state.user_starting_keys else None
            reset_simulation_runtime(players)
            start_simulation(selected_team)
            initialize_ai_managers(players)
            update_league_table_from_scores()
            st.rerun()

    else:
        user_team = st.session_state.simulation_user_team
        current_idx = st.session_state.simulation_game_index
        finished = game_csv_loaded and current_idx >= len(games_2025_26)

        status_cols = st.columns(4)
        with status_cols[0]:
            summary_card("MY TEAM", user_team, "🏀")
        with status_cols[1]:
            summary_card("CURRENT GAME", "Finished" if finished else f"Game {current_idx + 1}", "📅")
        with status_cols[2]:
            summary_card("PROGRESS", f"{min(current_idx, len(games_2025_26))}/{len(games_2025_26)}" if game_csv_loaded else "0/0", "✅")
        with status_cols[3]:
            summary_card("MY POINTS", f'{st.session_state.simulation_team_scores.get(user_team, 0.0):.2f}', "⭐")

        if game_csv_loaded and not finished:
            g = games_2025_26[current_idx]
            st.markdown("### Next Game")
            st.markdown(
                f"**Game {current_idx + 1}** · GW {g.get('gameweek')} Day {g.get('day')} · "
                f"{g.get('date')} {g.get('time')} · "
                f"{g.get('home_team')} {g.get('home_score')} vs {g.get('away_team')} {g.get('away_score')}"
            )

            b1, b2, b3 = st.columns(3)
            with b1:
                if st.button("Simulate Next Game", use_container_width=True):
                    process_one_game(games_2025_26[st.session_state.simulation_game_index], players)
                    st.rerun()
            with b2:
                if st.button("Simulate Current Gameweek", use_container_width=True):
                    process_next_gameweek(games_2025_26, players)
                    st.rerun()
            with b3:
                if st.button("Reset Simulation", use_container_width=True):
                    st.session_state.simulation_started = False
                    st.session_state.simulation_user_team = None
                    st.session_state.simulation_game_no = 1
                    st.session_state.simulation_league = []
                    reset_simulation_runtime(players)
                    st.rerun()
        elif finished:
            st.success("전체 경기 시뮬레이션이 끝났습니다.")
            if st.button("Reset Simulation", use_container_width=True):
                st.session_state.simulation_started = False
                st.session_state.simulation_user_team = None
                st.session_state.simulation_game_no = 1
                st.session_state.simulation_league = []
                reset_simulation_runtime(players)
                st.rerun()

        st.markdown("### League Table")
        update_league_table_from_scores()
        league = sorted(st.session_state.simulation_league, key=lambda x: (-x["Points"], x["Transfers"], x["Team"]))
        rows = []
        for i, row in enumerate(league, start=1):
            manager_label = "You" if row["Team"] == user_team else "AI"
            rows.append({
                "Rank": i,
                "Team": row["Team"],
                "Manager": manager_label,
                "Points": f'{row["Points"]:.2f}',
                "Transfers": row["Transfers"],
            })
        st.markdown(table_html(rows, ["Rank", "Team", "Manager", "Points", "Transfers"]), unsafe_allow_html=True)

        if st.session_state.simulation_history:
            st.markdown("### Last Simulated Game")
            last = st.session_state.simulation_history[-1]
            st.markdown(
                f"**GW {last['gameweek']} Day {last['day']}** · {last['date']} {last['time']} · {last['match']}"
            )

            points_rows = []
            for team, pts in sorted(last["team_points"].items(), key=lambda x: -x[1]):
                points_rows.append({"Fantasy Team": team, "Game Points": f"{pts:.2f}"})
            st.markdown(table_html(points_rows, ["Fantasy Team", "Game Points"]), unsafe_allow_html=True)

            with st.expander("Top price changes from this game"):
                st.markdown(table_html(last["price_changes"], ["Player", "Team", "Game Score", "Old Price", "Change", "New Price"]), unsafe_allow_html=True)

elif page == "Help":
    st.markdown('<div class="section-title">HOW TO PLAY</div>', unsafe_allow_html=True)
    st.markdown("""
    ### Core Rules
    - Roster size: 10 players
    - Roster composition: 5 Back Court + 5 Front Court
    - Starting line-up: 5 players
    - Formation: 2 Back Court / 3 Front Court or 3 Back Court / 2 Front Court
    - Salary cap: 14억원
    - Max players per WKBL team: 2
    - Free transfers: 2 per Gameweek
    - Deadline: 30 minutes before the first game of the Gameday, KST
    - Game results are read from `game_results_2025_26.csv`

    ### Fantasy Score Formula
    Good Defense is excluded.

    `Fantasy Score = (PTS + STL + BLK + DREB) + 1.5 × (OREB + AST) + MIN/4 - 1.5 × TO - 1.0 × missed 2PT - 0.9 × missed 3PT - 0.8 × missed FT`

    ### Initial Price
    - Initial prices are based on 2024-25 cumulative Fantasy Score.
    - The top initial price is set to 4.5억원.
    - CSV made-shot headers such as 2pmade and 3pmade are supported.
    - Players without previous-season data start at the minimum price, 0.3억원.
    - Formula: `0.3억원 + 4.2억원 × (player score / league top score)`

    ### Season Price Update
    - Prices update from actual 2025-26 game performance.
    - Price range: 0.30억원 ~ 4.50억원.
    - One-game maximum movement: `δmax = 0.50 × (4.50 - 0.30) / 30 = 0.07억원`.
    - Update formula: `P_next = clip(P_current + 0.07 × tanh((S - E) / 10), 0.30, 4.50)`.
    - Expected score updates by moving average: `E_next = 0.8E_current + 0.2S`.
    - Players who do not play keep the same price for that game.

    ### Chips
    - Gameday Captain: selected captain's score is doubled for the next simulated game.
    - Wildcard: your next roster save does not count as transfers.
    - All-Star: Starting 5 receive a +20% bonus for the next simulated game.
    """)

# =========================
# Footer
# =========================
st.markdown("""
<div class="footer">
    <div style="display:flex;gap:34px;align-items:center;flex-wrap:wrap;">
        <div style="font-size:28px;font-weight:900;color:#064EA4;">WK<span style="color:#E91E73;">BL</span></div>
        <div>© 2026 WKBL Fantasy. All rights reserved.</div>
        <div>Privacy Policy</div>
        <div>Terms of Use</div>
        <div>Official Rules</div>
        <div>Customer Support</div>
        <div>Country / Language: 한국어 (KR)</div>
    </div>
</div>
""", unsafe_allow_html=True)
