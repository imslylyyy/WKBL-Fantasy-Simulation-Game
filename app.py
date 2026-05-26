
import streamlit as st
import streamlit.components.v1 as components
import csv
import base64
import mimetypes
from pathlib import Path

st.set_page_config(page_title="WKBL Fantasy", page_icon="🏀", layout="wide")

# =========================================================
# WKBL Fantasy Prototype
# Required files in the same folder:
#   app.py
#   players_2024_25.csv
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

SIMULATION_TEAMS = ["KB스타즈", "하나은행", "삼성생명", "우리은행", "BNK썸", "신한은행"]

CSV_PATH = Path("players_2024_25.csv")
IMAGE_DIR = Path("images")

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
    player["team_2025_26"] = get_any(row, ["team_2025_26", "team", "소속구단", "팀"])
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

def normalize_position(pos):
    pos = clean(pos).upper()
    if pos == "B":
        return "Back Court"
    if pos == "F":
        return "Front Court"
    if pos in ["BACK COURT", "BACKCOURT"]:
        return "Back Court"
    if pos in ["FRONT COURT", "FRONTCOURT"]:
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
    return raw

players, csv_encoding = load_players()
csv_loaded = True
if not players:
    players = sample_players()
    csv_loaded = False

# =========================
# Image Handling
# =========================
def find_image(player):
    """
    Priority:
    1. images/선수명_팀명.png/jpg/jpeg/webp
    2. images/선수명.png/jpg/jpeg/webp
    """
    name = player["name"]
    team = player["team_2025_26"]
    candidates = []
    for ext in ["png", "jpg", "jpeg", "webp"]:
        candidates.append(IMAGE_DIR / f"{name}_{team}.{ext}")
    for ext in ["png", "jpg", "jpeg", "webp"]:
        candidates.append(IMAGE_DIR / f"{name}.{ext}")

    for path in candidates:
        if path.exists():
            return path
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
    price = p.get("initial_price", MIN_PRICE)
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

    ppg_label = "No prev. data" if not p["previous_data"] else f'Fantasy {p["fantasy_score"]:.1f}'

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
    cols = st.columns(len(items) + 1)
    for col, item in zip(cols[:-1], items):
        with col:
            if item == st.session_state.page:
                st.markdown(f'<div class="active-tab">{item}</div>', unsafe_allow_html=True)
            else:
                if st.button(item, key=f"topnav_{item}", use_container_width=True):
                    go_to(item)
                    st.rerun()
    with cols[-1]:
        st.button("Sign out", key="topnav_signout", use_container_width=True)
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
        st.markdown(f"**Position:** {player['position_short']}  \n**Initial Price:** {format_price(player['initial_price'])}")

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
# Sidebar
# =========================
with st.sidebar:
    st.markdown("## 🏀 WKBL Fantasy")
    language = st.selectbox("Country / Language", ["한국어 (KR)", "English"])
    st.markdown("### Data")
    if csv_loaded:
        st.success(f"players_2024_25.csv loaded ({csv_encoding})")
    else:
        st.warning("CSV not found. Showing sample data.")
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
            summary_card("TOP BASE SCORE", f"{max_score:.1f}", "⭐", highest_player["name"])
        else:
            summary_card("TOP BASE SCORE", "0.0", "⭐")

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

    c1, c2, c3, c4, c5 = st.columns(5)
    with c1:
        summary_card("GAMEWEEK", "1 - Day 1", "📅")
    with c2:
        summary_card("NEXT DEADLINE", "18:30", "⏰", "KST")
    with c3:
        summary_card("FREE TRANSFERS", "2", "🔁")
    with c4:
        summary_card("TOTAL POINTS", "0", "⭐")
    with c5:
        summary_card("OVERALL RANK", "-", "📊")

    left, right = st.columns([4, 1.15])

    sorted_by_price = sorted(players, key=lambda p: p["initial_price"], reverse=True)
    demo_roster = sorted_by_price[:10] if len(sorted_by_price) >= 10 else sorted_by_price
    starters = demo_roster[:5]
    bench = demo_roster[5:10]
    captain_key = f'{starters[0]["name"]}_{starters[0]["team_2025_26"]}' if starters else ""

    with left:
        st.markdown('<div class="section-title">SET YOUR LINE-UP</div>', unsafe_allow_html=True)
        formation = st.radio("Formation", ["2 Back Court / 3 Front Court", "3 Back Court / 2 Front Court"], horizontal=True)
        st.caption(f"Selected formation: {formation}")
        st.caption("현재 화면은 가격 상위 선수 기준의 데모 라인업입니다. 실제 팀 선택 기능은 다음 단계에서 연결하면 됩니다.")

        st.markdown('<div class="court"><b style="color:#064EA4;">STARTING 5</b>', unsafe_allow_html=True)
        cols = st.columns(5)
        for col, p in zip(cols, starters):
            with col:
                this_key = f'{p["name"]}_{p["team_2025_26"]}'
                player_card(p, captain=(this_key == captain_key))
        st.markdown('</div>', unsafe_allow_html=True)

        st.markdown('<div class="bench"><b style="color:#E91E73;">BENCH</b>', unsafe_allow_html=True)
        cols = st.columns(5)
        for i, (col, p) in enumerate(zip(cols, bench), start=1):
            with col:
                player_card(p, priority=i)
        st.markdown('</div>', unsafe_allow_html=True)

    with right:
        st.markdown('<div class="panel"><div class="panel-title">CHIPS</div>', unsafe_allow_html=True)
        st.markdown('<div class="chip-row">👑 Gameday Captain <span style="color:#E91E73;">PLAY</span></div>', unsafe_allow_html=True)
        st.markdown('<div class="chip-row">🛡️ Wildcard <span style="color:#64748b;">Available</span></div>', unsafe_allow_html=True)
        st.markdown('<div class="chip-row">⭐ All-Star <span style="color:#64748b;">Available</span></div>', unsafe_allow_html=True)
        st.markdown('</div>', unsafe_allow_html=True)

        st.markdown('<div class="panel"><div class="panel-title">TRANSACTIONS</div>', unsafe_allow_html=True)
        st.markdown("<b>🔁 2 free this week</b>", unsafe_allow_html=True)
        st.button("Make Changes", use_container_width=True)
        st.markdown('</div>', unsafe_allow_html=True)

        st.markdown('<div class="panel"><div class="panel-title">LEAGUE TABLE</div>', unsafe_allow_html=True)
        demo_rankings = [
            (1, "Seoul Shooters", 0),
            (2, "Blue Storm", 0),
            (3, "Dream Team", 0),
            (4, "Pink Panthers", 0),
            (5, "Court Queens", 0),
        ]
        for rank, team, points in demo_rankings:
            cls = "league-row league-me" if rank == 3 else "league-row"
            st.markdown(f'<div class="{cls}"><span>{rank} &nbsp; {team}</span><span>{points}</span></div>', unsafe_allow_html=True)
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

    filtered = sorted(filtered, key=lambda p: p["initial_price"], reverse=True)

    selected = None
    for p in players:
        if player_key(p) == st.session_state.selected_player_key:
            selected = p
            break

    if selected:
        player_detail_panel(selected)
        st.write("")

    st.markdown("### Player List")
    header_cols = st.columns([1.7, 1.2, 0.6, 1.2, 1.0, 0.8])
    headers = ["Name", "Team", "Pos", "Fantasy Score", "Initial Price", "Data"]
    for col, h in zip(header_cols, headers):
        col.markdown(f"**{h}**")
    st.markdown("<hr style='margin: 0.3rem 0 0.6rem 0;'>", unsafe_allow_html=True)

    for idx, p in enumerate(filtered):
        row_cols = st.columns([1.7, 1.2, 0.6, 1.2, 1.0, 0.8])
        row_cols[0].write(p["name"])
        row_cols[1].write(p["team_2025_26"])
        row_cols[2].write(p["position_short"])
        row_cols[3].write(f'{p["fantasy_score"]:.2f}' if p["previous_data"] else "-")
        row_cols[4].write(format_price(p["initial_price"]))
        label = "Data" if p["previous_data"] else "Card"
        if row_cols[5].button(label, key=f"data_btn_{idx}_{player_key(p)}"):
            st.session_state.selected_player_key = player_key(p)
            st.rerun()

    st.markdown("### Top 30 Player Cards")
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
            지금은 혼자 테스트하는 시뮬레이션 모드입니다. 
            사용자는 WKBL 6개 팀 중 하나를 선택하고, 나머지 5개 팀은 AI가 자동으로 운영합니다.
            경기 결과는 나중에 2025-26 시즌 첫 경기부터 차례대로 입력하면 됩니다.
        </div>
    </div>
    """, unsafe_allow_html=True)

    c1, c2, c3 = st.columns(3)
    with c1:
        summary_card("SALARY CAP", format_price(BUDGET_CAP), "💰")
    with c2:
        summary_card("MAX INITIAL PRICE", format_price(MAX_PRICE), "⭐")
    with c3:
        summary_card("MIN INITIAL PRICE", format_price(MIN_PRICE), "🆕")

    st.write("")

    if not st.session_state.simulation_started:
        selected_team = st.selectbox("내가 운영할 팀 선택", SIMULATION_TEAMS)
        st.caption("선택하지 않은 5개 팀은 AI가 자동으로 시뮬레이션합니다.")

        if st.button("Start 2025-26 Simulation", use_container_width=True):
            start_simulation(selected_team)
            st.rerun()

    else:
        user_team = st.session_state.simulation_user_team
        game_no = st.session_state.simulation_game_no

        status_cols = st.columns(3)
        with status_cols[0]:
            summary_card("MY TEAM", user_team, "🏀")
        with status_cols[1]:
            summary_card("CURRENT GAME", f"Game {game_no}", "📅")
        with status_cols[2]:
            summary_card("SIMULATION STATUS", "Ready", "✅")

        st.markdown("### League Table")
        league = sorted(st.session_state.simulation_league, key=lambda x: (-x["Points"], x["Transfers"], x["Team"]))
        for i, row in enumerate(league, start=1):
            row["Rank"] = i

        rows = []
        for row in league:
            manager_label = "You" if row["Team"] == user_team else "AI"
            rows.append({
                "Rank": row["Rank"],
                "Team": row["Team"],
                "Manager": manager_label,
                "Points": f'{row["Points"]:.1f}',
                "Transfers": row["Transfers"],
                "Budget": row["Budget"],
            })
        st.markdown(table_html(rows, ["Rank", "Team", "Manager", "Points", "Transfers", "Budget"]), unsafe_allow_html=True)

        st.markdown("### Next Step")
        st.info(
            f"Game {game_no} 결과를 받으면 이 화면에 입력 기능을 붙여서 선수별 Fantasy Score를 계산하고, "
            "사용자 팀과 AI 팀의 점수 및 선수 가격 변동을 업데이트하면 됩니다."
        )

        with st.expander("나중에 경기 결과를 입력할 때 필요한 형식 보기"):
            st.code(
                "date,player,team,minutes,2pmade,2pa,3pmade,3pa,ftm,fta,oreb,dreb,ast,stl,blk,to,pts\n"
                "2025-10-01,김단비,우리은행,34:20,8,17,2,6,5,6,3,7,5,2,1,3,27",
                language="csv",
            )

        reset_cols = st.columns([1, 3])
        with reset_cols[0]:
            if st.button("Reset Simulation"):
                st.session_state.simulation_started = False
                st.session_state.simulation_user_team = None
                st.session_state.simulation_game_no = 1
                st.session_state.simulation_league = []
                st.rerun()

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

    ### Fantasy Score Formula
    Good Defense is excluded.

    `Fantasy Score = (PTS + STL + BLK + DREB) + 1.5 × (OREB + AST) + MIN/4 - 1.5 × TO - 1.0 × missed 2PT - 0.9 × missed 3PT - 0.8 × missed FT`

    ### Initial Price
    - Initial prices are based on 2024-25 cumulative Fantasy Score.
    - The top initial price is set to 4.5억원.
    - CSV made-shot headers such as 2pmade and 3pmade are supported.
    - Players without previous-season data start at the minimum price, 0.3억원.
    - Formula: `0.3억원 + 4.2억원 × (player score / league top score)`
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
