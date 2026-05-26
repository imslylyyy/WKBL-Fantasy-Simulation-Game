
import streamlit as st
import streamlit.components.v1 as components
import csv
import base64
import json
import os
import hmac
import hashlib
import mimetypes
import math
import re
import random
import string
import unicodedata
from pathlib import Path
from datetime import datetime, timezone, timedelta
import calendar
import pandas as pd

st.set_page_config(page_title="WKBL Fantasy", page_icon="🏀", layout="wide", initial_sidebar_state="collapsed")

APP_VERSION = "Final Version v27.0 / in-app back-forward navigation"

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
ASSET_DIR = Path("assets")
TEAM_LOGO_DIR = ASSET_DIR / "team_logos"
HERO_IMAGE_PATH = ASSET_DIR / "hero.jpg"
WKBL_LOGO_PATH = ASSET_DIR / "wkbl_logo.png"
SPLASH_BG_PATH = ASSET_DIR / "splash_bg.jpg"
BGM_AUDIO_PATH = ASSET_DIR / "audio" / "bgm.mp3"

# Public subscriber mode settings.
# In this prototype, user progress is stored in a local JSON file.
# For a real public deployment with many channel subscribers, move this to Supabase/Firebase/PostgreSQL.
DATA_DIR = Path("data")
USER_DB_PATH = DATA_DIR / "wkbl_fantasy_users.json"
KST = timezone(timedelta(hours=9))
DEFAULT_PUBLIC_FIRST_GAME_START = datetime(2026, 5, 27, 6, 30, tzinfo=KST)
DEFAULT_PUBLIC_RESULT_DELAY_HOURS = 3.0
PUBLIC_GAME_INTERVAL = timedelta(days=1)
ADMIN_MANAGER_NAME = "관리자"
ADMIN_USER_ID = "__admin__"
DEFAULT_ADMIN_PASSWORD = "wkbl-admin-2026"

ROSTER_SIZE = 10
ROSTER_BACK_COUNT = 5
ROSTER_FRONT_COUNT = 5
STARTERS_COUNT = 5
MAX_PLAYERS_PER_WKBL_TEAM = ROSTER_SIZE  # current-gameday roster uses only the two playing teams
FREE_TRANSFERS_PER_GAMEWEEK = 999  # transfers are unlimited by rule
TRANSFER_PENALTY_POINTS = 0
STARTING_SCORE_MULTIPLIER = 1.00
BENCH_SCORE_MULTIPLIER = 0.50

COLUMNS = [
    "name", "team_2025_26", "position", "games", "minutes",
    "2pm", "2pa", "3pm", "3pa", "ftm", "fta",
    "oreb", "dreb", "ast", "stl", "blk", "to", "pts"
]

# =========================
# Page State
# =========================
# App phase: splash -> name_input -> main
if "app_phase" not in st.session_state:
    st.session_state.app_phase = "splash"

if "manager_name" not in st.session_state:
    st.session_state.manager_name = ""

if "current_user_id" not in st.session_state:
    st.session_state.current_user_id = ""

if "login_mode" not in st.session_state:
    st.session_state.login_mode = "login_or_register"

if "is_admin" not in st.session_state:
    st.session_state.is_admin = False

if "fantasy_team_names" not in st.session_state:
    st.session_state.fantasy_team_names = []

if "bgm_enabled" not in st.session_state:
    st.session_state.bgm_enabled = False

if "bgm_volume" not in st.session_state:
    st.session_state.bgm_volume = 42

if "page" not in st.session_state:
    st.session_state.page = "Home"

if "selected_player_key" not in st.session_state:
    st.session_state.selected_player_key = None

if "nav_back_stack" not in st.session_state:
    st.session_state.nav_back_stack = []

if "nav_forward_stack" not in st.session_state:
    st.session_state.nav_forward_stack = []

if "nav_last_location" not in st.session_state:
    st.session_state.nav_last_location = None

if "nav_skip_tracking" not in st.session_state:
    st.session_state.nav_skip_tracking = False

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


def current_app_location():
    return {
        "page": st.session_state.get("page", "Home"),
        "stage": st.session_state.get("main_flow_stage", ""),
        "selected_player_key": st.session_state.get("selected_player_key", None),
    }


def location_signature(loc):
    return json.dumps(loc or {}, ensure_ascii=False, sort_keys=True)


def apply_app_location(loc):
    if not loc:
        return
    st.session_state.page = loc.get("page", "Home")
    if "stage" in loc and loc.get("stage") is not None:
        st.session_state.main_flow_stage = loc.get("stage")
    if "selected_player_key" in loc:
        st.session_state.selected_player_key = loc.get("selected_player_key")


def sanitize_location_for_lock(loc):
    """Keep history navigation from reviving an editable lineup state after lock."""
    if not loc:
        return loc
    loc = dict(loc)
    try:
        if loc.get("page") == "My Team" and not is_lineup_editable_now():
            loc["stage"] = "locked_readonly"
    except NameError:
        pass
    return loc


def trim_history(stack, limit=30):
    if len(stack) > limit:
        del stack[:-limit]


def track_navigation_change():
    if st.session_state.get("app_phase") != "main":
        return
    current = current_app_location()
    last = st.session_state.get("nav_last_location")
    if last is None:
        st.session_state.nav_last_location = current
        return
    if st.session_state.get("nav_skip_tracking"):
        st.session_state.nav_skip_tracking = False
        st.session_state.nav_last_location = current
        return
    if location_signature(current) != location_signature(last):
        st.session_state.nav_back_stack.append(last)
        trim_history(st.session_state.nav_back_stack)
        st.session_state.nav_forward_stack = []
        st.session_state.nav_last_location = current


def render_history_controls():
    back_disabled = len(st.session_state.get("nav_back_stack", [])) == 0
    forward_disabled = len(st.session_state.get("nav_forward_stack", [])) == 0
    st.markdown(
        """
        <style>
        .history-toolbar {
            display:flex;
            align-items:center;
            justify-content:flex-end;
            gap:8px;
            margin:2px 0 6px 0;
        }
        .history-hint {
            font-size:12px;
            color:#64748b;
            text-align:right;
            margin-top:-4px;
            margin-bottom:6px;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )
    spacer, back_col, fwd_col = st.columns([7, 1.25, 1.25])
    with back_col:
        if st.button("← 뒤로가기", key="history_back_button", use_container_width=True, disabled=back_disabled):
            current = current_app_location()
            prev = st.session_state.nav_back_stack.pop()
            st.session_state.nav_forward_stack.append(current)
            trim_history(st.session_state.nav_forward_stack)
            st.session_state.nav_skip_tracking = True
            apply_app_location(sanitize_location_for_lock(prev))
            st.rerun()
    with fwd_col:
        if st.button("앞으로가기 →", key="history_forward_button", use_container_width=True, disabled=forward_disabled):
            current = current_app_location()
            nxt = st.session_state.nav_forward_stack.pop()
            st.session_state.nav_back_stack.append(current)
            trim_history(st.session_state.nav_back_stack)
            st.session_state.nav_skip_tracking = True
            apply_app_location(sanitize_location_for_lock(nxt))
            st.rerun()
    st.markdown('<div class="history-hint">앱 내부 화면 이동 기록 기준으로 작동합니다.</div>', unsafe_allow_html=True)


def player_key(player):
    return f'{player.get("name", "")}__{player.get("team_2025_26", "")}'

def format_price(value):
    return f"{value:.2f}억원"

def start_simulation(user_team):
    league = []
    for team in get_fantasy_teams():
        league.append({
            "Rank": 1,
            "Team": team,
            "Manager": "나" if team == user_team else "AI",
            "Points": 0.0,
            "Transfers": "∞",
            "Budget": format_price(BUDGET_CAP),
        })
    st.session_state.simulation_user_team = user_team
    st.session_state.simulation_game_no = 1
    st.session_state.simulation_started = True
    st.session_state.simulation_league = league


def generate_ai_team_names(n=5):
    """Generate stable-looking random AI fantasy team names using letters, numbers, and symbols."""
    prefixes = ["NOVA", "RUSH", "BYTE", "VOLT", "LUNA", "R3X", "KAI", "ZETA", "FLUX", "MINT"]
    symbols = ["_", "#", "!", "*", "-", "$", "@"]
    names = set()
    while len(names) < n:
        name = f"{random.choice(prefixes)}{random.choice(symbols)}{random.randint(10, 99)}"
        names.add(name)
    return list(names)

def get_fantasy_teams():
    teams = list(st.session_state.get("fantasy_team_names", []))
    if teams:
        return teams
    # Safe fallback before the user starts the game.
    return ["나의 팀"] + generate_ai_team_names(5)

def user_key_from_name(name: str) -> str:
    # Normalize visually identical names so an existing 감독명 cannot be bypassed
    # by changing Unicode width/composition or extra spaces.
    normalized = unicodedata.normalize("NFKC", clean(name))
    normalized = re.sub(r"\s+", " ", normalized).strip()
    return normalized.casefold()

def is_admin_name(name: str) -> bool:
    return user_key_from_name(name) == user_key_from_name(ADMIN_MANAGER_NAME)

def get_admin_password() -> str:
    # Prefer Streamlit secrets or environment variables for deployment.
    try:
        value = st.secrets.get("ADMIN_PASSWORD") or st.secrets.get("admin_password")
        if value:
            return str(value)
    except Exception:
        pass
    return os.environ.get("WKBL_ADMIN_PASSWORD", DEFAULT_ADMIN_PASSWORD)

def ensure_data_dir():
    try:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass

def load_user_db():
    ensure_data_dir()
    if not USER_DB_PATH.exists():
        return {"users": {}, "settings": {}}
    try:
        data = json.loads(USER_DB_PATH.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return {"users": {}, "settings": {}}
        if "users" not in data or not isinstance(data.get("users"), dict):
            data["users"] = {}
        if "settings" not in data or not isinstance(data.get("settings"), dict):
            data["settings"] = {}
        return data
    except Exception:
        return {"users": {}, "settings": {}}

def save_user_db(db):
    ensure_data_dir()
    if "users" not in db or not isinstance(db.get("users"), dict):
        db["users"] = {}
    if "settings" not in db or not isinstance(db.get("settings"), dict):
        db["settings"] = {}
    tmp = USER_DB_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(db, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(USER_DB_PATH)

def parse_kst_datetime(value, fallback=None):
    fallback = fallback or DEFAULT_PUBLIC_FIRST_GAME_START
    try:
        if isinstance(value, str) and value.strip():
            dt = datetime.fromisoformat(value.strip())
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=KST)
            return dt.astimezone(KST)
    except Exception:
        pass
    return fallback

def get_public_settings():
    db = load_user_db()
    settings = db.get("settings", {}) if isinstance(db.get("settings", {}), dict) else {}
    first_start = parse_kst_datetime(settings.get("first_game_start"), DEFAULT_PUBLIC_FIRST_GAME_START)
    try:
        delay_hours = float(settings.get("result_delay_hours", DEFAULT_PUBLIC_RESULT_DELAY_HOURS))
    except Exception:
        delay_hours = DEFAULT_PUBLIC_RESULT_DELAY_HOURS
    if delay_hours < 0:
        delay_hours = DEFAULT_PUBLIC_RESULT_DELAY_HOURS
    return {"first_game_start": first_start, "result_delay_hours": delay_hours}

def save_public_settings(first_game_start: datetime, result_delay_hours: float):
    db = load_user_db()
    if first_game_start.tzinfo is None:
        first_game_start = first_game_start.replace(tzinfo=KST)
    db["settings"]["first_game_start"] = first_game_start.astimezone(KST).isoformat()
    db["settings"]["result_delay_hours"] = float(result_delay_hours)
    db["settings"]["updated_at"] = datetime.now(KST).isoformat()
    save_user_db(db)

def hash_password(password: str, salt_hex: str | None = None):
    if salt_hex is None:
        salt = os.urandom(16)
    else:
        salt = bytes.fromhex(salt_hex)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, 120_000)
    return salt.hex(), digest.hex()

def verify_password(password: str, salt_hex: str, password_hash: str) -> bool:
    _, test_hash = hash_password(password, salt_hex)
    return hmac.compare_digest(test_hash, password_hash)

SAVE_STATE_KEYS = [
    "manager_name", "fantasy_team_names", "simulation_started", "simulation_user_team",
    "simulation_game_no", "simulation_league", "user_roster_keys", "roster_working_keys",
    "user_starting_keys", "user_captain_key", "user_formation", "user_transfers",
    "user_transfer_penalty_points", "user_transfer_log", "current_transfer_gameweek",
    "chip_captain_active", "chip_allstar_available", "chip_allstar_active",
    "chip_allstar_active_gameweek", "chip_allstar_used_game_id", "chip_allstar_used_label",
    "chip_allstar_used_gameweeks", "chip_allstar_used_labels_by_gw", "simulation_game_index",
    "simulation_history", "simulation_team_scores", "simulation_team_rosters",
    "simulation_team_starting", "simulation_team_captains", "simulation_ai_ready",
    "price_history", "market_state", "auto_roster_seed", "pack_game_id", "pack_back_keys",
    "pack_front_keys", "pack_back_opened", "pack_front_opened", "main_flow_stage",
    "bgm_enabled", "bgm_volume", "page",
]

def _jsonable(value):
    try:
        json.dumps(value, ensure_ascii=False)
        return value
    except Exception:
        return str(value)

def snapshot_progress():
    return {key: _jsonable(st.session_state.get(key)) for key in SAVE_STATE_KEYS if key in st.session_state}

def restore_progress(progress: dict):
    if not isinstance(progress, dict):
        return
    for key, value in progress.items():
        if key in SAVE_STATE_KEYS:
            st.session_state[key] = value
    st.session_state.app_phase = "main"

def create_or_update_user_record(manager_name: str, password: str, progress: dict | None = None):
    db = load_user_db()
    user_id = user_key_from_name(manager_name)
    salt, pw_hash = hash_password(password)
    db["users"][user_id] = {
        "manager_name": manager_name.strip(),
        "salt": salt,
        "password_hash": pw_hash,
        "created_at": datetime.now(KST).isoformat(),
        "updated_at": datetime.now(KST).isoformat(),
        "progress": progress or {},
    }
    save_user_db(db)
    return user_id

def save_current_user_progress():
    user_id = st.session_state.get("current_user_id", "")
    if not user_id:
        return
    db = load_user_db()
    rec = db.get("users", {}).get(user_id)
    if not rec:
        return
    rec["manager_name"] = st.session_state.get("manager_name", rec.get("manager_name", ""))
    rec["updated_at"] = datetime.now(KST).isoformat()
    rec["progress"] = snapshot_progress()
    db["users"][user_id] = rec
    save_user_db(db)

def login_existing_user(manager_name: str, password: str):
    if is_admin_name(manager_name):
        if password != get_admin_password():
            return False, "잘못된 패스워드입니다."
        st.session_state.current_user_id = ADMIN_USER_ID
        st.session_state.manager_name = ADMIN_MANAGER_NAME
        st.session_state.is_admin = True
        st.session_state.app_phase = "main"
        st.session_state.page = "Admin"
        return True, "관리자 계정으로 로그인되었습니다."

    user_id = user_key_from_name(manager_name)
    db = load_user_db()
    rec = db.get("users", {}).get(user_id)
    if not rec:
        return False, "등록되지 않은 이름입니다."
    if not verify_password(password, rec.get("salt", ""), rec.get("password_hash", "")):
        return False, "잘못된 패스워드입니다."
    st.session_state.current_user_id = user_id
    st.session_state.manager_name = rec.get("manager_name", manager_name)
    st.session_state.is_admin = False
    restore_progress(rec.get("progress", {}))
    st.session_state.is_admin = False
    st.session_state.app_phase = "main"
    st.session_state.page = st.session_state.get("page") or "Home"
    return True, "로그인되었습니다."

def register_new_user(players_list, games_list, manager_name: str, password: str):
    if is_admin_name(manager_name):
        return False, "관리자 계정은 새 감독 등록으로 만들 수 없습니다. 로그인 / 이어하기를 이용해 주세요."
    user_id = user_key_from_name(manager_name)
    db = load_user_db()
    if user_id in db.get("users", {}):
        return False, "이미 등록된 감독명입니다. 새 감독 등록으로는 접속할 수 없습니다. 로그인 / 이어하기를 이용해 주세요."
    st.session_state.current_user_id = user_id
    st.session_state.is_admin = False
    begin_game_session(players_list, games_list, manager_name)
    create_or_update_user_record(manager_name, password, snapshot_progress())
    return True, "새 감독으로 등록되었습니다."

def global_leaderboard_rows(include_current=True):
    if include_current and st.session_state.get("current_user_id"):
        save_current_user_progress()
    db = load_user_db()
    rows = []
    for uid, rec in db.get("users", {}).items():
        progress = rec.get("progress", {}) if isinstance(rec.get("progress", {}), dict) else {}
        manager = rec.get("manager_name") or progress.get("manager_name") or uid
        team_scores = progress.get("simulation_team_scores", {}) if isinstance(progress.get("simulation_team_scores", {}), dict) else {}
        points = float(team_scores.get(manager, 0.0))
        game_index = int(progress.get("simulation_game_index", 0) or 0)
        rows.append({
            "Rank": 1,
            "Team": manager,
            "Manager": "You" if uid == st.session_state.get("current_user_id") else "Subscriber",
            "Points": round(points, 2),
            "Transfers": "∞",
            "Games": game_index,
            "Updated": rec.get("updated_at", ""),
        })
    rows.sort(key=lambda x: (-x["Points"], -x["Games"], str(x["Team"])))
    for i, row in enumerate(rows, start=1):
        row["Rank"] = i
    return rows


def delete_user_by_id(user_id: str):
    db = load_user_db()
    if user_id in db.get("users", {}):
        del db["users"][user_id]
        save_user_db(db)
        return True
    return False

def participant_admin_rows():
    db = load_user_db()
    rows = []
    for uid, rec in db.get("users", {}).items():
        progress = rec.get("progress", {}) if isinstance(rec.get("progress", {}), dict) else {}
        manager = rec.get("manager_name") or progress.get("manager_name") or uid
        scores = progress.get("simulation_team_scores", {}) if isinstance(progress.get("simulation_team_scores", {}), dict) else {}
        rows.append({
            "ID": uid,
            "감독명": manager,
            "누적 점수": round(float(scores.get(manager, 0.0)), 2),
            "진행 경기": int(progress.get("simulation_game_index", 0) or 0),
            "최근 접속/저장": rec.get("updated_at", ""),
        })
    rows.sort(key=lambda r: (-float(r["누적 점수"]), str(r["감독명"])))
    return rows

def public_game_start_time(game_index: int):
    settings = get_public_settings()
    return settings["first_game_start"] + PUBLIC_GAME_INTERVAL * int(game_index)

def public_game_result_time(game_index: int):
    settings = get_public_settings()
    return public_game_start_time(game_index) + timedelta(hours=float(settings.get("result_delay_hours", DEFAULT_PUBLIC_RESULT_DELAY_HOURS)))

def now_kst():
    return datetime.now(KST)

def format_kst(dt: datetime):
    return dt.astimezone(KST).strftime("%Y-%m-%d %H:%M KST")

def compact_remaining(delta: timedelta):
    seconds = max(0, int(delta.total_seconds()))
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    if h >= 24:
        d, h = divmod(h, 24)
        return f"{d}일 {h}시간 {m}분"
    if h:
        return f"{h}시간 {m}분"
    if m:
        return f"{m}분 {s}초"
    return f"{s}초"

def public_game_status(game_index: int, at: datetime | None = None):
    at = at or now_kst()
    start = public_game_start_time(game_index)
    result = public_game_result_time(game_index)
    if at < start:
        return {
            "code": "before_start",
            "label": "라인업 준비 가능",
            "start": start,
            "result": result,
            "message": f"경기 시작까지 {compact_remaining(start - at)} 남았습니다.",
            "can_edit": True,
            "can_reveal": False,
        }
    if at < result:
        return {
            "code": "in_progress",
            "label": "경기 진행 중 / 라인업 잠금",
            "start": start,
            "result": result,
            "message": f"결과 공개까지 {compact_remaining(result - at)} 남았습니다.",
            "can_edit": False,
            "can_reveal": False,
        }
    return {
        "code": "result_open",
        "label": "결과 확인 가능",
        "start": start,
        "result": result,
        "message": "결과가 공개되었습니다. 결과 확인을 누르면 점수와 가격이 반영됩니다.",
        "can_edit": False,
        "can_reveal": True,
    }

def user_fantasy_team_name():
    return st.session_state.get("manager_name", "나의 팀") or "나의 팀"

def audio_data_url(path):
    path = Path(path)
    if not path.exists():
        return None
    data = base64.b64encode(path.read_bytes()).decode("utf-8")
    mime = mimetypes.guess_type(str(path))[0] or "audio/mpeg"
    return f"data:{mime};base64,{data}"

def render_bgm_player():
    """Play one looping BGM after the START button has been clicked, if assets/audio/bgm.mp3 exists."""
    if not st.session_state.get("bgm_enabled"):
        return
    src = audio_data_url(BGM_AUDIO_PATH)
    if not src:
        return
    volume = max(0, min(100, int(st.session_state.get("bgm_volume", 42)))) / 100
    components.html(f"""
    <audio id="wkbl-bgm" autoplay loop playsinline>
        <source src="{src}" type="audio/mpeg">
    </audio>
    <script>
    const audio = document.getElementById('wkbl-bgm');
    if (audio) {{
        audio.volume = {volume:.2f};
        audio.loop = true;
        const p = audio.play();
        if (p !== undefined) {{ p.catch(() => {{}}); }}
    }}
    </script>
    """, height=0)

def render_music_controls():
    st.markdown("<div style='text-align:right;font-weight:900;color:#64748b;'>🎵 음악 설정</div>", unsafe_allow_html=True)
    c1, c2 = st.columns([1, 2])
    with c1:
        enabled = st.toggle("음악", value=bool(st.session_state.get("bgm_enabled", False)), key="bgm_toggle_home")
        st.session_state.bgm_enabled = enabled
    with c2:
        volume = st.slider("음량", 0, 100, int(st.session_state.get("bgm_volume", 42)), key="bgm_volume_slider")
        st.session_state.bgm_volume = volume

def begin_game_session(players_list, games_list, manager_name):
    """Create the user's fantasy league and enter the main game."""
    st.session_state.manager_name = manager_name.strip()
    st.session_state.fantasy_team_names = [st.session_state.manager_name] + generate_ai_team_names(5)
    st.session_state.page = "Home"

    # First gameday roster is generated only from the first game's two real WKBL teams.
    if games_list:
        start_allowed = game_allowed_teams(games_list[0])
        start_pool = [p for p in players_list if not start_allowed or p.get("team_2025_26") in start_allowed]
    else:
        start_pool = players_list

    st.session_state.user_roster_keys = generate_auto_roster(start_pool, seed=random.randint(100, 9999))
    st.session_state.roster_working_keys = list(st.session_state.user_roster_keys)
    st.session_state.user_starting_keys = auto_starting_keys(st.session_state.user_roster_keys, players_list, st.session_state.user_formation)
    st.session_state.user_captain_key = st.session_state.user_starting_keys[0] if st.session_state.user_starting_keys else None

    reset_simulation_runtime(players_list)
    start_simulation(st.session_state.manager_name)
    initialize_ai_managers(players_list)
    update_league_table_from_scores()
    st.session_state.simulation_started = True
    st.session_state.app_phase = "main"

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
    player["minutes"] = get_any(row, ["minutes", "MIN", "min", "출전시간", "minutes_float"])

    two_pair = get_any(row, ["2PM-A", "2pm-a", "2PMA", "2P M-A", "2P-A", "2p-a", "2점슛"])
    three_pair = get_any(row, ["3PM-A", "3pm-a", "3PMA", "3P M-A", "3P-A", "3p-a", "3점슛"])
    ft_pair = get_any(row, ["FTM-A", "ftm-a", "FTA-M", "FTMA", "FT-A", "ft-a", "자유투"])

    # Important:
    # Some manually made CSV files use 2p/3p for made shots,
    # not 2pm/3pm. The old version missed those columns and read made shots as 0.
    raw_2pm = get_any(row, ["2pm", "2PM", "2p", "2P", "2pmade", "2PMADE", "fg2m", "FG2M", "2p_made", "2P_MADE", "two_pm", "two_p_made", "2점성공", "2점슛성공"])
    raw_2pa = get_any(row, ["2pa", "2PA", "2a", "2A", "fg2a", "FG2A", "two_pa", "2점시도", "2점슛시도"])
    raw_3pm = get_any(row, ["3pm", "3PM", "3p", "3P", "3pmade", "3PMADE", "fg3m", "FG3M", "3p_made", "3P_MADE", "three_pm", "three_p_made", "3점성공", "3점슛성공"])
    raw_3pa = get_any(row, ["3pa", "3PA", "3a", "3A", "fg3a", "FG3A", "three_pa", "3점시도", "3점슛시도"])
    raw_ftm = get_any(row, ["ftm", "FTM", "ft", "FT", "ftmade", "FTMADE", "ft_made", "FT_MADE", "free_throw_made", "자유투성공"])
    raw_fta = get_any(row, ["fta", "FTA", "free_throw_attempt", "자유투시도"])

    player["2pm"], player["2pa"] = normalize_shot_values(raw_2pm, raw_2pa, two_pair)
    player["3pm"], player["3pa"] = normalize_shot_values(raw_3pm, raw_3pa, three_pair)
    player["ftm"], player["fta"] = normalize_shot_values(raw_ftm, raw_fta, ft_pair)

    player["oreb"] = get_any(row, ["oreb", "OREB", "off", "OFF", "off_reb", "OFF_REB", "offensive_rebounds", "공격리바운드"])
    player["dreb"] = get_any(row, ["dreb", "DREB", "def", "DEF", "def_reb", "DEF_REB", "defensive_rebounds", "수비리바운드"])

    # Safety fallback: if a CSV has total rebounds but not defensive rebounds,
    # calculate DREB = TOT - OREB.
    total_reb = get_any(row, ["tot", "TOT", "reb", "REB", "tot_reb", "TOT_REB", "total_rebounds", "총리바운드"])
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

def to_float(value, default=0.0):
    value = clean(value)
    if value == "":
        return default
    try:
        return float(value)
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


def asset_data_url(path):
    path = Path(path)
    if not path.exists():
        return None
    return image_data_url(path)

TEAM_LOGO_FILES = {
    "삼성생명": "삼성생명.png",
    "신한은행": "신한은행.png",
    "우리은행": "우리은행.PNG",
    "하나은행": "하나은행.png",
    "BNK썸": "BNK썸.PNG",
    "KB스타즈": "KB스타즈.png",
}

TEAM_LOGO_FALLBACK_EXTS = [".png", ".PNG", ".jpg", ".JPG", ".jpeg", ".JPEG"]

def team_logo_data_url(team):
    """Return the logo data URL from assets/team_logos using the current uploaded filenames.

    Primary filenames:
    삼성생명.png, 신한은행.png, 우리은행.PNG, 하나은행.png, BNK썸.PNG, KB스타즈.png
    """
    canon = canonical_team(team)
    filename = TEAM_LOGO_FILES.get(canon, "")
    if filename:
        logo_path = TEAM_LOGO_DIR / filename
        if logo_path.exists():
            return asset_data_url(logo_path)

    # Fallback: useful if the file extension case changes during upload/deploy.
    for ext in TEAM_LOGO_FALLBACK_EXTS:
        logo_path = TEAM_LOGO_DIR / f"{canon}{ext}"
        if logo_path.exists():
            return asset_data_url(logo_path)
    return None

def team_logo_img_html(team, size=44):
    data = team_logo_data_url(team)
    if not data:
        return f'<span class="team-logo-fallback">{team}</span>'
    return f'<img src="{data}" alt="{team}" style="width:{size}px;height:{size}px;object-fit:contain;">'


def team_logo_strip_html():
    """Render only the six club logos, with no text labels."""
    items = []
    for team in SIMULATION_TEAMS:
        logo = team_logo_data_url(team)
        if logo:
            items.append(f'<div class="club-logo" title="{team}"><img src="{logo}" alt="{team}"></div>')
        else:
            items.append(f'<div class="club-logo club-logo-text" title="{team}">{team}</div>')
    return ''.join(items)

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

section[data-testid="stSidebar"] {
    background: linear-gradient(180deg, #020617 0%, #111827 55%, #1e293b 100%);
}
section[data-testid="stSidebar"] * { color: #f8fafc; }
section[data-testid="stSidebar"] button { border-radius: 12px !important; font-weight: 900 !important; }

button[kind="secondary"] {
    font-family: 'Noto Sans KR', sans-serif;
}

.header {
    position: relative;
    overflow: hidden;
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


.header::before {
    content: "";
    position: absolute;
    inset: 0;
    background: radial-gradient(circle at 20% 20%, rgba(255,255,255,.75), transparent 28%), linear-gradient(90deg, rgba(255,255,255,.94), rgba(255,255,255,.72));
    z-index: 0;
}
.header > * { position: relative; z-index: 1; }
.header-main-row { display:flex; align-items:center; gap:18px; }
.wkbl-brand-logo { width:92px; max-height:70px; object-fit:contain; background:rgba(255,255,255,.78); border-radius:18px; padding:8px; box-shadow:0 10px 22px rgba(15,23,42,.14); }
.club-strip { display:flex; flex-wrap:wrap; gap:16px; align-items:center; margin-top:18px; padding:12px 14px; background:rgba(255,255,255,.80); border:1px solid rgba(255,255,255,.92); border-radius:22px; backdrop-filter: blur(4px); }
.club-logo { width:76px; height:76px; display:flex; align-items:center; justify-content:center; padding:6px; border-radius:22px; background:rgba(248,250,252,.92); box-shadow:0 8px 18px rgba(15,23,42,.10); }
.club-logo img { width:64px; height:64px; object-fit:contain; display:block; }
.club-logo-text { font-size:11px; font-weight:900; text-align:center; color:#334155; }
.team-logo-fallback { font-size:10px; font-weight:900; }

.quest-wrap { position:relative; min-height:560px; border-radius:30px; overflow:hidden; border:1px solid rgba(255,255,255,.22); box-shadow:0 22px 60px rgba(15,23,42,.28); margin-bottom:20px; background-size:cover; background-position:center; }
.quest-wrap::before { content:""; position:absolute; inset:0; background:linear-gradient(135deg, rgba(2,6,23,.82), rgba(6,78,164,.48), rgba(233,30,115,.34)); }
.quest-title { position:absolute; left:28px; top:24px; z-index:2; color:white; font-family:'Oswald', sans-serif; font-size:42px; font-weight:900; text-shadow:0 6px 24px rgba(0,0,0,.45); }
.korea-map { position:absolute; inset:90px 30px 28px 30px; z-index:2; border-radius:26px; border:1px solid rgba(255,255,255,.18); background:rgba(2,6,23,.32); backdrop-filter:blur(3px); }
.map-line { position:absolute; inset:8%; border:2px solid rgba(255,255,255,.16); border-radius:38% 62% 52% 48% / 38% 41% 59% 62%; transform:rotate(-12deg); }
.quest-node { position:absolute; width:122px; min-height:112px; transform:translate(-50%,-50%); border-radius:20px; padding:10px; text-align:center; color:white; background:rgba(15,23,42,.78); border:2px solid rgba(255,255,255,.22); box-shadow:0 10px 26px rgba(0,0,0,.30); }
.quest-node.current { border-color:#facc15; box-shadow:0 0 0 4px rgba(250,204,21,.22), 0 0 32px rgba(250,204,21,.60); }
.quest-node.done { opacity:.70; filter:saturate(.7); }
.quest-node.locked { opacity:.38; filter:grayscale(1); }
.quest-node img { width:50px; height:50px; object-fit:contain; display:block; margin:0 auto 5px; }
.quest-status { font-size:11px; font-weight:900; color:#facc15; }
.quest-match { font-size:11px; font-weight:800; line-height:1.25; margin-top:4px; }
.pack-zone { border-radius:32px; padding:26px; margin:16px 0 24px; background-size:cover; background-position:center; position:relative; overflow:hidden; border:1px solid rgba(255,255,255,.20); }
.pack-zone::before { content:""; position:absolute; inset:0; background:linear-gradient(135deg, rgba(2,6,23,.86), rgba(6,78,164,.56), rgba(233,30,115,.36)); }
.pack-zone > * { position:relative; z-index:1; }
.pack-card { min-height:190px; border-radius:26px; display:flex; flex-direction:column; align-items:center; justify-content:center; color:white; text-align:center; border:2px solid rgba(255,255,255,.28); box-shadow:0 20px 50px rgba(0,0,0,.28); background:linear-gradient(145deg, rgba(17,24,39,.84), rgba(6,78,164,.70)); }
.pack-card.pink { background:linear-gradient(145deg, rgba(17,24,39,.84), rgba(233,30,115,.70)); }
.pack-name { font-family:'Oswald', sans-serif; font-size:34px; font-weight:900; letter-spacing:.5px; }
.swap-panel { background:#f8fafc; border:1px solid #e5e7eb; border-radius:20px; padding:16px; margin:12px 0 18px; }
.hero { background-size:cover !important; background-position:center !important; position:relative; overflow:hidden; min-height:280px; display:flex; flex-direction:column; justify-content:flex-end; }
.hero::after { content:""; position:absolute; inset:auto -12% -35% auto; width:420px; height:420px; background:rgba(233,30,115,.42); filter:blur(80px); }
.roster-market { background:#f8fafc; border:1px solid #e5e7eb; border-radius:24px; padding:18px; margin:14px 0 18px; }
.roster-market-title { font-weight:900; color:#111827; margin-bottom:8px; }
.card-hint { color:#64748b; font-size:13px; margin-bottom:14px; }
.selected-card-button .stButton>button { background:#111827; color:white; border:0; font-weight:900; border-radius:999px; }
.available-card-button .stButton>button { background:white; color:#064EA4; border:1px solid #bfdbfe; font-weight:900; border-radius:999px; }
.reveal-note { color:#64748b; font-size:13px; margin:-4px 0 14px; }

.data-badge {
    display:inline-block;
    padding: 3px 8px;
    border-radius: 999px;
    font-size: 12px;
    font-weight: 900;
}
.has-data { background:#dcfce7; color:#166534; }
.no-data { background:#fee2e2; color:#991b1b; }


/* v21 dashboard home */
.nav-wrap { background: rgba(2,6,23,.82); border:1px solid rgba(255,255,255,.12); box-shadow:0 12px 30px rgba(2,6,23,.18); }
.v21-home { position:relative; overflow:hidden; border-radius:30px; min-height:500px; padding:34px 36px 22px; color:white; border:1px solid rgba(255,255,255,.16); box-shadow:0 26px 70px rgba(2,6,23,.28); background-size:cover; background-position:center 36%; }
.v21-home:before { content:""; position:absolute; inset:0; background:linear-gradient(90deg, rgba(2,6,23,.94) 0%, rgba(2,6,23,.78) 36%, rgba(2,6,23,.38) 67%, rgba(2,6,23,.72) 100%); }
.v21-home:after { content:""; position:absolute; left:-160px; bottom:-160px; width:460px; height:460px; border:2px solid rgba(233,30,115,.55); border-radius:50%; box-shadow:0 0 70px rgba(233,30,115,.35); }
.v21-home > * { position:relative; z-index:2; }
.v21-topbar { display:flex; align-items:center; justify-content:space-between; gap:18px; margin-bottom:42px; }
.v21-brand { display:flex; align-items:center; gap:14px; font-weight:900; }
.v21-brand img { width:105px; max-height:72px; object-fit:contain; filter:drop-shadow(0 8px 18px rgba(0,0,0,.35)); }
.v21-manager { display:flex; gap:10px; align-items:center; background:rgba(255,255,255,.10); border:1px solid rgba(255,255,255,.20); border-radius:18px; padding:10px 14px; font-weight:900; }
.v21-title { font-family:'Oswald',sans-serif; font-size:82px; line-height:.90; font-weight:900; letter-spacing:-2px; text-shadow:0 10px 34px rgba(0,0,0,.48); }
.v21-title .pink { color:#ff4fa3; display:block; }
.v21-sub { margin-top:16px; font-size:18px; line-height:1.35; color:rgba(255,255,255,.88); font-weight:700; max-width:440px; }
.v21-cta-row { display:flex; flex-wrap:wrap; gap:12px; margin-top:22px; }
.v21-cta { display:inline-flex; align-items:center; gap:10px; border-radius:14px; padding:12px 20px; font-weight:900; color:white; background:linear-gradient(135deg,#ff4fa3,#e91e73); box-shadow:0 0 24px rgba(233,30,115,.45); }
.v21-ghost { background:rgba(255,255,255,.08); border:1px solid rgba(255,255,255,.30); box-shadow:none; }
.v21-home-grid { display:grid; grid-template-columns:repeat(6, minmax(0,1fr)); gap:14px; margin-top:16px; }
.v21-tile { min-height:158px; border-radius:18px; padding:16px; background:linear-gradient(145deg, rgba(15,23,42,.84), rgba(30,64,175,.45)); border:1px solid rgba(255,255,255,.16); box-shadow:0 14px 30px rgba(2,6,23,.25); color:white; position:relative; overflow:hidden; }
.v21-tile h3 { margin:0 0 10px; color:white; font-size:17px; font-weight:900; }
.v21-tile p { margin:0; color:rgba(255,255,255,.76); font-size:13px; line-height:1.45; }
.v21-tile .tile-icon { font-size:26px; margin-bottom:10px; color:#ff4fa3; }
.v21-next-logos { display:flex; align-items:center; justify-content:center; gap:10px; margin:10px 0; }
.v21-next-logos img { width:54px; height:54px; object-fit:contain; background:rgba(255,255,255,.90); border-radius:14px; padding:5px; }
.v21-club-footer { margin-top:24px; display:flex; align-items:center; gap:18px; background:rgba(2,6,23,.76); border:1px solid rgba(255,255,255,.16); border-radius:26px; padding:18px 22px; box-shadow:0 0 30px rgba(233,30,115,.16); }
.v21-club-footer-title { font-family:'Oswald',sans-serif; font-size:24px; font-style:italic; font-weight:900; color:white; padding-right:20px; border-right:1px solid rgba(255,255,255,.25); }
.v21-club-footer .club-logo { background:transparent; box-shadow:none; width:90px; height:68px; border-radius:0; }
.v21-club-footer .club-logo img { width:76px; height:62px; filter:drop-shadow(0 7px 12px rgba(0,0,0,.25)); }
.v21-action-card { margin-top:10px; }
.v21-action-card .stButton > button { border-radius:999px; font-weight:900; border:1px solid rgba(255,255,255,.22); background:#E91E73; color:white; }
.v21-action-card .stButton > button:hover { background:#ff4fa3; color:white; border-color:#ff4fa3; }
@media(max-width:1100px){ .v21-home-grid{grid-template-columns:repeat(2,1fr);} .v21-title{font-size:58px;} }
</style>
""", unsafe_allow_html=True)

# =========================
# Helpers
# =========================

def player_card(p, priority=None, captain=False, allstar=False, compact=False):
    """
    Earlier clean WKBL fantasy card style.
    Back Court = blue card, Front Court = pink card.
    Team logo is shown in the top-left badge.
    """
    price = p.get("current_price", p.get("initial_price", MIN_PRICE))
    team = p.get("team_2025_26", "")
    is_back = p.get("position_label") == "Back Court"
    top_class = "card-top-blue" if is_back else "card-top-pink"
    name_class = "name-blue" if is_back else "name-pink"
    pos_text = "BACK COURT" if is_back else "FRONT COURT"
    pos_short = "B" if is_back else "F"
    captain_html = '<div class="captain">👑</div>' if captain else ""
    priority_html = f'<div class="priority">#{priority}</div>' if priority else ""
    allstar_class = " allstar-glow" if allstar else ""
    initial = p["name"][0]

    img_path = find_image(p)
    data_url = image_data_url(img_path)
    logo_url = team_logo_data_url(team)

    if data_url:
        avatar_html = f'<div class="avatar"><img src="{data_url}" alt="{p["name"]}"></div>'
    else:
        avatar_html = f'<div class="avatar avatar-fallback">{initial}</div>'

    if logo_url:
        team_badge_html = f'<div class="team-badge"><img src="{logo_url}" alt="{team}"></div>'
    else:
        team_badge_html = f'<div class="team-badge team-badge-text">{team}</div>'

    fs_label = "NO DATA" if not p.get("previous_data") else f'FS {float(p.get("fantasy_score", 0)):.1f}'
    delay = 0.06 * ((int(priority) - 1) if isinstance(priority, int) else 0)
    card_height = 250 if compact else 292
    top_height = 112 if compact else 130
    avatar_size = 76 if compact else 92
    salary_size = 22 if compact else 28
    name_size = 14 if compact else 17

    html = f"""
    <!DOCTYPE html>
    <html>
    <head>
    <meta charset="utf-8">
    <style>
        @import url('https://fonts.googleapis.com/css2?family=Oswald:wght@500;700&family=Noto+Sans+KR:wght@400;700;900&display=swap');
        body {{ margin:0; padding:8px; font-family:'Noto Sans KR', sans-serif; background:transparent; }}
        .player-card {{
            position:relative;
            width:100%;
            height:{card_height - 16}px;
            box-sizing:border-box;
            background:white;
            border-radius:20px;
            overflow:hidden;
            border:1px solid #e5e7eb;
            box-shadow:0 10px 22px rgba(15,23,42,.16);
            text-align:center;
            animation:pop .42s cubic-bezier(.2,1.5,.4,1) both;
            animation-delay:{delay:.2f}s;
        }}
        @keyframes pop {{
            0% {{ transform:translateY(18px) scale(.86); opacity:0; filter:blur(2px); }}
            100% {{ transform:translateY(0) scale(1); opacity:1; filter:blur(0); }}
        }}
        .allstar-glow {{
            border:3px solid #facc15;
            box-shadow:0 0 0 4px rgba(250,204,21,.24), 0 0 34px rgba(250,204,21,.70), 0 10px 22px rgba(15,23,42,.18);
        }}
        .card-top-blue {{
            position:relative;
            height:{top_height}px;
            padding-top:10px;
            background:linear-gradient(135deg,#bfdbfe 0%,#38bdf8 34%,#064EA4 100%);
        }}
        .card-top-pink {{
            position:relative;
            height:{top_height}px;
            padding-top:10px;
            background:linear-gradient(135deg,#fbcfe8 0%,#f472b6 34%,#E91E73 100%);
        }}
        .card-top-blue::after, .card-top-pink::after {{
            content:"";
            position:absolute;
            inset:0;
            background:radial-gradient(circle at 62% 15%,rgba(255,255,255,.36),transparent 32%), linear-gradient(120deg,rgba(255,255,255,.20),transparent 42%);
            pointer-events:none;
        }}
        .team-badge {{
            position:absolute;
            top:12px;
            left:12px;
            width:42px;
            height:42px;
            border-radius:12px;
            background:rgba(255,255,255,.94);
            display:flex;
            align-items:center;
            justify-content:center;
            box-shadow:0 4px 12px rgba(15,23,42,.18);
            z-index:4;
        }}
        .team-badge img {{ width:34px; height:34px; object-fit:contain; }}
        .team-badge-text {{ width:auto; max-width:72px; padding:0 6px; font-size:9px; font-weight:900; color:#111827; }}
        .pos-pill {{
            position:absolute;
            top:14px;
            right:12px;
            background:rgba(255,255,255,.94);
            border-radius:999px;
            padding:5px 9px;
            color:#111827;
            font-size:10px;
            font-weight:900;
            letter-spacing:.2px;
            z-index:4;
        }}
        .avatar {{
            position:relative;
            z-index:3;
            width:{avatar_size}px;
            height:{avatar_size}px;
            border-radius:999px;
            background:white;
            margin:{26 if compact else 30}px auto 0;
            border:4px solid white;
            display:flex;
            align-items:center;
            justify-content:center;
            overflow:hidden;
            font-size:32px;
            font-weight:900;
            color:#111827;
            box-shadow:0 8px 18px rgba(15,23,42,.22);
        }}
        .avatar img {{ width:100%; height:100%; object-fit:cover; }}
        .salary {{
            font-family:'Oswald', sans-serif;
            font-size:{salary_size}px;
            line-height:1.1;
            font-weight:900;
            color:#111827;
            padding:9px 4px 7px;
            background:white;
            white-space:nowrap;
        }}
        .name-blue, .name-pink {{
            color:white;
            font-weight:900;
            padding:7px 6px;
            font-size:{name_size}px;
            line-height:1.1;
            white-space:nowrap;
            overflow:hidden;
            text-overflow:ellipsis;
        }}
        .name-blue {{ background:#064EA4; }}
        .name-pink {{ background:#E91E73; }}
        .meta {{
            display:flex;
            justify-content:space-between;
            align-items:center;
            gap:8px;
            padding:8px 10px;
            background:#f8fafc;
            color:#475569;
            font-size:{11 if compact else 12}px;
            font-weight:900;
        }}
        .role {{
            min-width:28px;
            height:28px;
            border-radius:9px;
            background:#111827;
            color:white;
            display:flex;
            align-items:center;
            justify-content:center;
            font-family:'Oswald', sans-serif;
            font-size:18px;
            border:2px solid {'#38bdf8' if is_back else '#f472b6'};
            flex:0 0 auto;
        }}
        .fs {{ color:#111827; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }}
        .captain {{
            position:absolute;
            right:10px;
            top:{top_height - 18}px;
            width:35px;
            height:35px;
            border-radius:999px;
            background:#facc15;
            color:#111827;
            border:4px solid white;
            display:flex;
            align-items:center;
            justify-content:center;
            font-size:18px;
            font-weight:900;
            z-index:9;
            box-shadow:0 6px 14px rgba(0,0,0,.22);
        }}
        .priority {{
            position:absolute;
            left:10px;
            top:{top_height - 18}px;
            min-width:35px;
            height:35px;
            padding:0 5px;
            border-radius:999px;
            background:#facc15;
            color:#111827;
            border:4px solid white;
            display:flex;
            align-items:center;
            justify-content:center;
            font-size:13px;
            font-weight:900;
            z-index:9;
            box-shadow:0 6px 14px rgba(0,0,0,.22);
        }}
    </style>
    </head>
    <body>
        <div class="player-card{allstar_class}">
            {priority_html}{captain_html}
            <div class="{top_class}">
                {team_badge_html}
                <div class="pos-pill">{pos_text}</div>
                {avatar_html}
            </div>
            <div class="salary">{format_price(price)}</div>
            <div class="{name_class}">{p["name"]}</div>
            <div class="meta"><div class="role">{pos_short}</div><div class="fs">{fs_label}</div></div>
        </div>
    </body>
    </html>
    """
    components.html(html, height=card_height, scrolling=False)

def render_roster_card_selector(available_players, selected_keys, players_list):
    st.markdown('<div class="roster-market"><div class="roster-market-title">CARD PLAYER MARKET</div><div class="card-hint">카드 아래 버튼으로 선수를 추가/제외하세요. Back Court는 파란색, Front Court는 핑크색으로 표시됩니다.</div>', unsafe_allow_html=True)
    selected_set = set(selected_keys)
    ordered = sorted(
        available_players,
        key=lambda p: (
            0 if player_key(p) in selected_set else 1,
            p.get("position_label", ""),
            -float(p.get("current_price", p.get("initial_price", MIN_PRICE))),
            p.get("name", ""),
        ),
    )
    cols = st.columns(2)
    for idx, p in enumerate(ordered):
        k = player_key(p)
        selected = k in selected_set
        with cols[idx % 2]:
            player_card(p, compact=True)
            css_class = "selected-card-button" if selected else "available-card-button"
            st.markdown(f'<div class="{css_class}">', unsafe_allow_html=True)
            if selected:
                if st.button("✓ Selected · Remove", key=f"roster_card_remove_{k}", use_container_width=True):
                    st.session_state.roster_working_keys = [x for x in selected_keys if x != k]
                    st.rerun()
            else:
                disabled = len(selected_keys) >= ROSTER_SIZE
                if st.button("+ Add", key=f"roster_card_add_{k}", use_container_width=True, disabled=disabled):
                    st.session_state.roster_working_keys = list(selected_keys) + [k]
                    st.rerun()
            st.markdown('</div>', unsafe_allow_html=True)
    st.markdown('</div>', unsafe_allow_html=True)

def summary_card(label, value, icon="🏀", detail=""):
    st.markdown(f"""
    <div class="summary">
        <div class="summary-label">{icon} {label}</div>
        <div class="summary-value">{value} <span style="font-size:13px;color:#6b7280;">{detail}</span></div>
    </div>
    """, unsafe_allow_html=True)

def header():
    hero = asset_data_url(HERO_IMAGE_PATH)
    wkbl = asset_data_url(WKBL_LOGO_PATH)
    bg = f"background-image: linear-gradient(90deg, rgba(255,255,255,.96) 0%, rgba(255,255,255,.78) 46%, rgba(255,255,255,.16) 100%), url('{hero}'); background-size: cover; background-position:center;" if hero else ""
    logo_html = f'<img class="wkbl-brand-logo" src="{wkbl}" alt="WKBL logo">' if wkbl else ""
    st.markdown(f"""
    <div class="header" style="{bg}">
        <div class="header-main-row">
            {logo_html}
            <div>
                <div class="logo"><span class="blue">WKBL</span> <span class="pink">FANTASY</span></div>
                <div class="subtitle">SALARY CAP EDITION</div>
                <div style="margin-top:14px;color:#334155;font-weight:900;">🏀 Build your roster · Set your line-up · Climb the rankings</div>
            </div>
        </div>
        <div class="club-strip">{team_logo_strip_html()}</div>
    </div>
    """, unsafe_allow_html=True)

def nav():
    items = ["Home", "My Team", "Players", "Schedule", "Prices", "Results", "Simulation", "Help"]
    if st.session_state.get("is_admin"):
        items.append("Admin")
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


def render_dashboard_home(players_list, games_list):
    """Game-like home dashboard with actual navigation buttons."""
    update_league_table_from_scores()
    g = current_game()
    gw = g.get("gameweek", 1) if g else 1
    day = g.get("day", 1) if g else 1
    status = public_game_status(st.session_state.get("simulation_game_index", 0)) if g else None
    date = format_kst(status["start"]) if status else ""
    time = f"결과 공개: {format_kst(status['result'])}" if status else ""
    status_line = status["label"] if status else ""
    status_message = status["message"] if status else ""
    venue = g.get("venue", "") if g else ""
    home = g.get("home_team", "") if g else ""
    away = g.get("away_team", "") if g else ""
    home_logo = team_logo_data_url(home)
    away_logo = team_logo_data_url(away)
    wkbl = asset_data_url(WKBL_LOGO_PATH)
    bg = asset_data_url(SPLASH_BG_PATH) or asset_data_url(HERO_IMAGE_PATH)
    bg_style = f"background-image:url('{bg}');" if bg else "background:linear-gradient(135deg,#020617,#064EA4,#E91E73);"
    manager = st.session_state.get("manager_name", "WKBL FAN") or "WKBL FAN"
    user_team = st.session_state.get("simulation_user_team") or manager
    my_points = float(st.session_state.get("simulation_team_scores", {}).get(user_team, 0.0))
    league = sorted(st.session_state.get("simulation_league", []), key=lambda x: (-x.get("Points", 0), str(x.get("Team", ""))))
    standing_lines = ""
    for i, row in enumerate(league[:3], start=1):
        standing_lines += f"<div style='display:flex;justify-content:space-between;font-size:12px;margin:3px 0;'><span>{i} · {row.get('Team','')}</span><b>{row.get('Points',0):.1f}</b></div>"
    if not standing_lines:
        standing_lines = "<p>아직 순위가 없습니다.</p>"
    home_logo_html = f'<img src="{home_logo}" alt="{home}">' if home_logo else ''
    away_logo_html = f'<img src="{away_logo}" alt="{away}">' if away_logo else ''
    wkbl_logo_html = f'<img src="{wkbl}" alt="WKBL">' if wkbl else '<div style="font-size:34px;font-weight:900;">WKBL</div>'

    music_left, music_right = st.columns([4.4, 1.2])
    with music_right:
        render_music_controls()

    st.markdown(f"""
    <div class="v21-home" style="{bg_style}">
      <div class="v21-topbar">
        <div class="v21-brand">{wkbl_logo_html}</div>
        <div style="display:flex;gap:12px;align-items:center;">
          <div class="v21-manager">⭐ 실시간 포인트&nbsp; {my_points:.2f}</div>
          <div class="v21-manager">👤 {manager} 감독님, 환영합니다</div>
        </div>
      </div>
      <div class="v21-title"><span>WKBL</span><span class="pink">Fantasy</span></div>
      <div class="v21-sub">Build. Compete. Win.<br>카드를 뽑고, 라인업을 만들고, 매 경기 판타지 포인트로 순위를 올리세요.</div>
    </div>
    """, unsafe_allow_html=True)

    nav_a, nav_b, nav_c = st.columns([1, 1, 4])
    with nav_a:
        if st.button("경기 일정", key="dash_schedule", use_container_width=True):
            st.session_state.page = "Schedule"
            st.rerun()
    with nav_b:
        if st.button("가격 확인", key="dash_prices", use_container_width=True):
            st.session_state.page = "Prices"
            st.rerun()

    c1, c2, c3, c4, c5, c6 = st.columns(6)
    with c1:
        st.markdown("<div class='v21-tile'><div class='tile-icon'>🎴</div><h3>My Lineup</h3><p>Starting 5와 Bench를 관리합니다.</p></div>", unsafe_allow_html=True)
        st.markdown("<div class='v21-action-card'>", unsafe_allow_html=True)
        if st.button("Manage", key="dash_manage_lineup", use_container_width=True):
            st.session_state.page = "My Team"
            st.session_state.main_flow_stage = "lineup" if st.session_state.get("user_roster_keys") else "pack_lobby"
            st.rerun()
        st.markdown("</div>", unsafe_allow_html=True)
    with c2:
        st.markdown("<div class='v21-tile'><div class='tile-icon'>👑</div><h3>Captain Pick</h3><p>캡틴은 자동 2배 점수를 받습니다.</p></div>", unsafe_allow_html=True)
        st.markdown("<div class='v21-action-card'>", unsafe_allow_html=True)
        if st.button("Select Captain", key="dash_captain", use_container_width=True):
            st.session_state.page = "My Team"
            st.session_state.main_flow_stage = "lineup" if st.session_state.get("user_roster_keys") else "pack_lobby"
            st.rerun()
        st.markdown("</div>", unsafe_allow_html=True)
    with c3:
        st.markdown("<div class='v21-tile'><div class='tile-icon'>📊</div><h3>Results</h3><p>지금까지 시뮬레이션한 경기 결과와 당시 라인업을 확인합니다.</p></div>", unsafe_allow_html=True)
        st.markdown("<div class='v21-action-card'>", unsafe_allow_html=True)
        if st.button("View Results", key="dash_results", use_container_width=True):
            st.session_state.page = "Results"
            st.rerun()
        st.markdown("</div>", unsafe_allow_html=True)
    with c4:
        st.markdown("<div class='v21-tile'><div class='tile-icon'>🔁</div><h3>Transfers</h3><p>Gameday마다 무제한 이적. 단, 현재 경기 두 팀 선수만 선택.</p></div>", unsafe_allow_html=True)
        st.markdown("<div class='v21-action-card'>", unsafe_allow_html=True)
        if st.button("Go to Market", key="dash_market", use_container_width=True):
            st.session_state.page = "My Team"
            st.session_state.main_flow_stage = "pack_lobby"
            st.rerun()
        st.markdown("</div>", unsafe_allow_html=True)
    with c5:
        st.markdown(f"<div class='v21-tile'><div class='tile-icon'>🏆</div><h3>Standings</h3>{standing_lines}</div>", unsafe_allow_html=True)
        st.markdown("<div class='v21-action-card'>", unsafe_allow_html=True)
        if st.button("View", key="dash_standings", use_container_width=True):
            st.session_state.page = "Simulation"
            st.rerun()
        st.markdown("</div>", unsafe_allow_html=True)
    with c6:
        st.markdown(f"<div class='v21-tile'><div class='tile-icon'>📅</div><h3>Next Game</h3><div class='v21-next-logos'>{home_logo_html}<b>VS</b>{away_logo_html}</div><p>GW {gw} Day {day}<br>{date}<br>{time}<br>{venue}<br><b>{status_line}</b><br>{status_message}</p></div>", unsafe_allow_html=True)
        st.markdown("<div class='v21-action-card'>", unsafe_allow_html=True)
        if st.button("Play", key="dash_preview", use_container_width=True):
            st.session_state.page = "My Team"
            st.session_state.main_flow_stage = "pack_lobby"
            st.rerun()
        st.markdown("</div>", unsafe_allow_html=True)

    st.markdown(f"<div class='v21-club-footer'><div class='v21-club-footer-title'>🏀 WKBL CLUBS</div>{team_logo_strip_html()}</div>", unsafe_allow_html=True)


def sidebar_menu():
    labels = {
        "My Team": "🏀 메인 플레이",
        "Simulation": "🎮 시뮬레이션",
        "Players": "👥 선수 데이터",
        "Results": "📊 경기 결과",
        "Schedule": "📅 경기 일정",
        "Prices": "📈 가격 확인",
        "Help": "❓ 도움말",
        "Home": "🏠 홈",
    }
    with st.sidebar:
        st.markdown("### WKBL Fantasy")
        st.caption(f"감독: {st.session_state.get('manager_name','-')}")
        st.caption(f"버전: {APP_VERSION}")
        st.divider()
        for page_name in ["Home", "My Team", "Players", "Schedule", "Prices", "Results", "Simulation", "Help"]:
            if st.button(labels[page_name], key=f"side_{page_name}", use_container_width=True):
                st.session_state.page = page_name
                st.rerun()

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
        "user_transfer_penalty_points": 0.0,
        "user_transfer_log": [],
        "current_transfer_gameweek": 1,
        "chip_captain_active": False,
        "chip_wildcard_available": True,
        "chip_wildcard_active": False,
        "chip_allstar_available": True,
        "chip_allstar_active": False,
        "chip_allstar_active_gameweek": None,
        "chip_allstar_used_game_id": "",
        "chip_allstar_used_label": "",
        "chip_allstar_used_gameweeks": [],
        "chip_allstar_used_labels_by_gw": {},
        "simulation_game_index": 0,
        "simulation_history": [],
        "simulation_team_scores": {team: 0.0 for team in get_fantasy_teams()},
        "simulation_team_rosters": {},
        "simulation_team_starting": {},
        "simulation_team_captains": {},
        "simulation_ai_ready": False,
        "price_history": {},
        "auto_roster_seed": 0,
        "pack_game_id": "",
        "pack_back_keys": [],
        "pack_front_keys": [],
        "pack_back_opened": False,
        "pack_front_opened": False,
        "main_flow_stage": "pack_lobby",
    }
    for key, value in defaults.items():
        if key not in st.session_state:
            st.session_state[key] = value
    if not st.session_state.get("price_history"):
        record_price_snapshot(players_list, "Start")

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

def total_price_for_keys(keys, players_list):
    return sum(price_of_key(k, players_list) for k in keys)

def pack_total_budget(players_list):
    return total_price_for_keys(pack_roster_keys(), players_list) if "pack_back_keys" in st.session_state else 0.0

def record_price_snapshot(players_list, label):
    """Store a compact price-history point for every player after each simulated game."""
    hist = dict(st.session_state.get("price_history", {}))
    for pl in players_list:
        k = player_key(pl)
        price = float(pl.get("current_price", pl.get("initial_price", MIN_PRICE)))
        hist.setdefault(k, [])
        if not hist[k] or hist[k][-1].get("Label") != label:
            hist[k].append({"Label": label, "Price": round(price, 2)})
    st.session_state.price_history = hist

def lineup_snapshot_for_history(players_list):
    lookup = player_lookup(players_list)
    snapshots = {}
    for team in get_fantasy_teams():
        roster = list(st.session_state.simulation_team_rosters.get(team, []))
        starting = list(st.session_state.simulation_team_starting.get(team, []))
        captain = st.session_state.simulation_team_captains.get(team)
        bench = [k for k in roster if k not in starting]
        def label(k):
            p = lookup.get(k)
            if not p:
                return k
            mark = " 👑" if k == captain else ""
            return f"{p.get('name','')}({p.get('team_2025_26','')}, {p.get('position_label','')}){mark}"
        snapshots[team] = {
            "captain": label(captain) if captain else "-",
            "starting": [label(k) for k in starting],
            "bench": [label(k) for k in bench],
        }
    return snapshots

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
        errors.append("현재 규칙에서는 팀별 인원 제한을 사용하지 않습니다.")

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
    # Seeded jitter is intentionally visible so Auto-generate can make a different legal roster.
    jitter = ((hash(player_key(p) + str(seed)) % 1000) / 1000) * 0.35
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

    # Start from efficient players with small seed-based variation, then upgrade within cap.
    rng = random.Random(seed)
    for pos, needed in [("Back Court", ROSTER_BACK_COUNT), ("Front Court", ROSTER_FRONT_COUNT)]:
        pool = [p for p in candidates if p.get("position_label") == pos]
        pool = sorted(
            pool,
            key=lambda p: (
                -(player_value_score(p, seed) + rng.random() * 8.0),
                float(p.get("current_price", p.get("initial_price", MIN_PRICE))),
            ),
        )
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
    p["name"] = get_any(row, ["player", "name", "선수"]) or p.get("name", "")
    p["team_2025_26"] = canonical_team(get_any(row, ["team", "team_2025_26", "팀", "소속구단"]) or p.get("team_2025_26", ""))
    p["position_label"] = normalize_position(p.get("position", ""))
    p["position_short"] = position_short(p.get("position", ""))

    # Supports both the app template and the generated parser CSV.
    raw_date = get_any(row, ["date", "game_date", "날짜", "일자", "date_text"])
    p["date_text"] = raw_date
    p["date"] = parse_game_date(raw_date)
    p["time"] = get_any(row, ["time", "game_time", "시간", "tipoff"])
    p["gameweek"] = get_any(row, ["gameweek", "gw", "Gameweek", "GAMEWEEK"])
    p["day"] = get_any(row, ["day", "gameday", "Day", "DAY"])
    p["game_no"] = to_int(get_any(row, ["game_no", "game_number", "경기번호"]), 0)
    p["game_id"] = get_any(row, ["game_id", "game", "match_id", "경기ID"])
    if not p["game_id"] and p["game_no"]:
        p["game_id"] = f"G{p['game_no']:03d}"

    explicit_home = canonical_team(get_any(row, ["home_team", "home", "홈팀"]))
    explicit_away = canonical_team(get_any(row, ["away_team", "away", "원정팀"]))
    explicit_home_score = get_any(row, ["home_score", "홈점수"])
    explicit_away_score = get_any(row, ["away_score", "원정점수"])

    team = p["team_2025_26"]
    opponent = canonical_team(get_any(row, ["opponent", "상대팀"]))
    home_away = clean(get_any(row, ["home_away", "ha", "홈원정"])).lower()
    team_score = get_any(row, ["team_score", "팀점수"])
    opponent_score = get_any(row, ["opponent_score", "상대점수"])
    p["opponent"] = opponent
    p["home_away"] = home_away
    p["team_score"] = team_score
    p["opponent_score"] = opponent_score

    if explicit_home and explicit_away:
        p["home_team"] = explicit_home
        p["away_team"] = explicit_away
        p["home_score"] = explicit_home_score
        p["away_score"] = explicit_away_score
    elif team and opponent and home_away in ["home", "h", "홈"]:
        p["home_team"] = team
        p["away_team"] = opponent
        p["home_score"] = team_score
        p["away_score"] = opponent_score
    elif team and opponent and home_away in ["away", "a", "원정"]:
        p["home_team"] = opponent
        p["away_team"] = team
        p["home_score"] = opponent_score
        p["away_score"] = team_score
    else:
        p["home_team"] = explicit_home
        p["away_team"] = explicit_away
        p["home_score"] = explicit_home_score
        p["away_score"] = explicit_away_score

    p["venue"] = get_any(row, ["venue", "경기장"])
    direct_score = get_any(row, ["game_score", "fantasy_score", "Fantasy Score", "fantasy"])
    if direct_score != "":
        p["game_score"] = to_float(direct_score, fantasy_score(p))
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
        if "game_score" not in p:
            p["game_score"] = fantasy_score(p)
        parsed.append(p)

    # Sort and infer gameweek/day if not supplied.
    def _game_sort_key(r):
        game_no = to_int(r.get("game_no", 0), 0)
        return (
            game_no if game_no else 10**9,
            r.get("date", ""),
            r.get("time", ""),
            str(r.get("game_id", "")),
            r.get("row_order", 0),
        )
    parsed.sort(key=_game_sort_key)

    game_order = []
    groups = {}
    for r in parsed:
        gid = str(r["game_id"])
        if gid not in groups:
            groups[gid] = {
                "game_id": gid,
                "game_no": r.get("game_no", 0),
                "date": r.get("date", ""),
                "date_text": r.get("date_text", ""),
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
        for field in ["game_no", "date", "date_text", "time", "gameweek", "day", "home_team", "away_team", "home_score", "away_score", "venue"]:
            if not groups[gid].get(field) and r.get(field):
                groups[gid][field] = r.get(field)

    # Final safety fill for generated CSV files whose metadata is stored as team/opponent/home_away.
    for gid in game_order:
        group = groups[gid]
        rows_in_group = group.get("rows", [])
        if rows_in_group:
            first = rows_in_group[0]
            group["date"] = group.get("date") or first.get("date", "")
            group["date_text"] = group.get("date_text") or first.get("date_text", "")
            group["time"] = group.get("time") or first.get("time", "")
            group["venue"] = group.get("venue") or first.get("venue", "")
            group["game_no"] = group.get("game_no") or first.get("game_no", 0)

        if (not group.get("home_team") or not group.get("away_team")) and rows_in_group:
            # Prefer explicit home_away markers from the generated parser CSV.
            for rr in rows_in_group:
                if rr.get("home_away") in ["home", "h", "홈"] and rr.get("team_2025_26") and rr.get("opponent"):
                    group["home_team"] = rr.get("team_2025_26")
                    group["away_team"] = rr.get("opponent")
                    group["home_score"] = rr.get("team_score", "")
                    group["away_score"] = rr.get("opponent_score", "")
                    break
            # Fallback: infer from the two teams appearing in the game.
            if not group.get("home_team") or not group.get("away_team"):
                teams_seen = []
                for rr in rows_in_group:
                    t = rr.get("team_2025_26", "")
                    if t and t not in teams_seen:
                        teams_seen.append(t)
                if len(teams_seen) >= 2:
                    group["home_team"] = group.get("home_team") or teams_seen[0]
                    group["away_team"] = group.get("away_team") or teams_seen[1]

        # Last cleanup: if a row already has inferred home/away, copy it.
        if rows_in_group:
            first = rows_in_group[0]
            group["home_team"] = group.get("home_team") or first.get("home_team", "")
            group["away_team"] = group.get("away_team") or first.get("away_team", "")
            group["home_score"] = group.get("home_score") or first.get("home_score", "")
            group["away_score"] = group.get("away_score") or first.get("away_score", "")

    games = [groups[gid] for gid in game_order]

    dates = sorted({g["date"] for g in games if g.get("date")})
    first_date = dates[0] if dates else ""
    date_to_week = {}
    date_to_day = {}
    if first_date:
        from datetime import datetime, timezone, timedelta
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
        # Ignore incorrect sequential GW values from older/generated CSVs; infer from date whenever possible.
        if g.get("date") and g.get("date") in date_to_week:
            g["gameweek"] = date_to_week.get(g.get("date"), 1)
            g["day"] = date_to_day.get(g.get("date"), 1)
        else:
            g["gameweek"] = ((i - 1) // 7) + 1
            g["day"] = ((i - 1) % 7) + 1

    return parsed, games, encoding_used

def game_match_label(game, show_score=False):
    home = game.get("home_team", "") or ""
    away = game.get("away_team", "") or ""
    hs = clean(game.get("home_score", ""))
    a_s = clean(game.get("away_score", ""))
    if home and away:
        if show_score and hs != "" and a_s != "":
            return f"{home} {hs} vs {away} {a_s}"
        return f"{home} vs {away}"
    teams = []
    for row in game.get("rows", []):
        t = row.get("team_2025_26", "")
        if t and t not in teams:
            teams.append(t)
    if len(teams) >= 2:
        return f"{teams[0]} vs {teams[1]}"
    return "경기 정보 확인 필요"

def game_allowed_teams(game):
    teams = []
    for t in [game.get("home_team", ""), game.get("away_team", "")]:
        t = canonical_team(t)
        if t and t not in teams:
            teams.append(t)
    if len(teams) < 2:
        for row in game.get("rows", []):
            t = canonical_team(row.get("team_2025_26", ""))
            if t and t not in teams:
                teams.append(t)
            if len(teams) == 2:
                break
    return teams

def current_game():
    if not games_2025_26:
        return None
    idx = min(st.session_state.simulation_game_index, len(games_2025_26) - 1)
    return games_2025_26[idx]

def current_allowed_teams():
    g = current_game()
    return game_allowed_teams(g) if g else []

def gameday_lineup_report(game, players_list):
    """Check whether the user's roster and Starting 5 are legal for the next real game."""
    allowed = set(game_allowed_teams(game))
    roster_keys = list(st.session_state.user_roster_keys)
    starting_keys = list(st.session_state.user_starting_keys)
    roster_players = keys_to_players(roster_keys, players_list)
    starting_players = keys_to_players(starting_keys, players_list)
    errors = []

    if allowed:
        bad_roster = [p for p in roster_players if p.get("team_2025_26") not in allowed]
        bad_starting = [p for p in starting_players if p.get("team_2025_26") not in allowed]
        if bad_roster:
            names = ", ".join([f"{p['name']}({p['team_2025_26']})" for p in bad_roster])
            errors.append(f"로스터에 이번 경기 두 팀 소속이 아닌 선수가 있습니다: {names}")
        if bad_starting:
            names = ", ".join([f"{p['name']}({p['team_2025_26']})" for p in bad_starting])
            errors.append(f"Starting 5에 이번 경기 두 팀 소속이 아닌 선수가 있습니다: {names}")

    roster_status = roster_report(roster_keys, players_list)
    if not roster_status["valid"]:
        errors.extend(roster_status["errors"])

    starting_status = validate_starting(starting_keys, roster_keys, players_list, st.session_state.user_formation)
    if not starting_status["valid"]:
        errors.extend(starting_status["errors"])

    return {
        "valid": len(errors) == 0,
        "errors": errors,
        "allowed_teams": list(allowed),
        "roster_players": roster_players,
        "starting_players": starting_players,
    }


def reset_simulation_runtime(players_list):
    reset_market_state(players_list)
    st.session_state.simulation_game_index = 0
    st.session_state.simulation_history = []
    st.session_state.simulation_team_scores = {team: 0.0 for team in get_fantasy_teams()}
    st.session_state.simulation_team_rosters = {}
    st.session_state.simulation_team_starting = {}
    st.session_state.simulation_team_captains = {}
    st.session_state.price_history = {}
    record_price_snapshot(players_list, "Start")
    st.session_state.user_transfers = 0
    st.session_state.user_transfer_penalty_points = 0.0
    st.session_state.user_transfer_log = []
    st.session_state.current_transfer_gameweek = 1
    st.session_state.chip_captain_active = False  # legacy state; captain bonus is automatic now
    st.session_state.chip_wildcard_active = False
    st.session_state.chip_wildcard_available = True
    st.session_state.chip_allstar_active = False
    st.session_state.chip_allstar_available = True
    st.session_state.chip_allstar_active_gameweek = None
    st.session_state.chip_allstar_used_game_id = ""
    st.session_state.chip_allstar_used_label = ""
    st.session_state.chip_allstar_used_gameweeks = []
    st.session_state.chip_allstar_used_labels_by_gw = {}
    st.session_state.simulation_ai_ready = False

def initialize_ai_managers(players_list):
    user_team = st.session_state.simulation_user_team
    for idx, team in enumerate(get_fantasy_teams()):
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
            "Manager": "나" if team == user_team else "AI",
            "Points": 0.0,
            "Transfers": "∞",
            "Budget": format_price(BUDGET_CAP),
        }
        for team in get_fantasy_teams()
    ]

def update_league_table_from_scores():
    # Public subscriber leaderboard: every registered manager becomes one ranked team.
    rows = global_leaderboard_rows(include_current=True)
    if rows:
        st.session_state.simulation_league = [
            {
                "Rank": row["Rank"],
                "Team": row["Team"],
                "Manager": row["Manager"],
                "Points": row["Points"],
                "Transfers": "∞",
                "Budget": format_price(BUDGET_CAP),
                "Games": row.get("Games", 0),
            }
            for row in rows
        ]
        return

    # Fallback before anyone has registered.
    league = []
    for team in get_fantasy_teams():
        league.append({
            "Rank": 1,
            "Team": team,
            "Manager": "나" if team == st.session_state.simulation_user_team else "AI",
            "Points": round(float(st.session_state.simulation_team_scores.get(team, 0.0)), 2),
            "Transfers": "∞",
            "Budget": format_price(BUDGET_CAP),
        })
    st.session_state.simulation_league = league

def process_one_game(game, players_list, force_user_zero=False):
    if not st.session_state.simulation_ai_ready:
        initialize_ai_managers(players_list)
    sync_user_lineup_to_simulation()
    if force_user_zero:
        team = st.session_state.get("simulation_user_team")
        if team:
            st.session_state.simulation_team_rosters[team] = []
            st.session_state.simulation_team_starting[team] = []
            st.session_state.simulation_team_captains[team] = None

    lookup = player_lookup(players_list)
    row_scores = {}
    price_changes = []
    played_keys = set()

    # In this version each gameday is based only on the two teams that actually play.
    # AI managers also rebuild a valid roster from those two teams for the current game.
    allowed = set(game_allowed_teams(game))
    game_pool = [p for p in players_list if not allowed or p.get("team_2025_26") in allowed]
    for idx_team, fantasy_team in enumerate(get_fantasy_teams()):
        if fantasy_team != st.session_state.simulation_user_team:
            ai_roster = generate_auto_roster(game_pool, seed=1000 + st.session_state.simulation_game_index * 31 + idx_team)
            ai_starting = auto_starting_keys(ai_roster, players_list, "2 Back Court / 3 Front Court")
            st.session_state.simulation_team_rosters[fantasy_team] = ai_roster
            st.session_state.simulation_team_starting[fantasy_team] = ai_starting
            st.session_state.simulation_team_captains[fantasy_team] = ai_starting[0] if ai_starting else None

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

    lineups_snapshot = lineup_snapshot_for_history(players_list)

    game_team_points = {}
    for fantasy_team in get_fantasy_teams():
        roster = st.session_state.simulation_team_rosters.get(fantasy_team, [])
        starting = st.session_state.simulation_team_starting.get(fantasy_team, [])
        bench = [k for k in roster if k not in starting]
        captain = st.session_state.simulation_team_captains.get(fantasy_team)

        starter_points = sum(row_scores.get(k, 0.0) * STARTING_SCORE_MULTIPLIER for k in starting)
        bench_points = sum(row_scores.get(k, 0.0) * BENCH_SCORE_MULTIPLIER for k in bench)

        bonus_points = 0.0
        # Captain is no longer a manually activated chip. The selected captain always scores 2x.
        if captain and captain in starting:
            bonus_points += row_scores.get(captain, 0.0) * STARTING_SCORE_MULTIPLIER
        if fantasy_team == st.session_state.simulation_user_team and st.session_state.chip_allstar_active:
            bonus_points += starter_points * 0.20

        total = starter_points + bench_points + bonus_points
        st.session_state.simulation_team_scores[fantasy_team] = round(
            float(st.session_state.simulation_team_scores.get(fantasy_team, 0.0)) + total,
            2,
        )
        game_team_points[fantasy_team] = round(total, 2)

    st.session_state.chip_captain_active = False  # legacy state; captain bonus is automatic now
    allstar_was_active_for_history = bool(st.session_state.chip_allstar_active)
    if st.session_state.chip_allstar_active:
        used_gw = to_int(game.get("gameweek", st.session_state.current_transfer_gameweek), st.session_state.current_transfer_gameweek)
        used_label = f"{game.get('date', '')} {game_match_label(game, show_score=False)}".strip()
        st.session_state.chip_allstar_active = False
        st.session_state.chip_allstar_available = True
        st.session_state.chip_allstar_active_gameweek = None
        st.session_state.chip_allstar_used_game_id = game.get("game_id", "")
        st.session_state.chip_allstar_used_label = used_label
        used_list = list(st.session_state.get("chip_allstar_used_gameweeks", []))
        if used_gw not in used_list:
            used_list.append(used_gw)
        st.session_state.chip_allstar_used_gameweeks = used_list
        used_labels = dict(st.session_state.get("chip_allstar_used_labels_by_gw", {}))
        used_labels[str(used_gw)] = used_label
        st.session_state.chip_allstar_used_labels_by_gw = used_labels

    st.session_state.simulation_history.append({
        "game_id": game.get("game_id", ""),
        "date": game.get("date", ""),
        "time": game.get("time", ""),
        "gameweek": game.get("gameweek", ""),
        "day": game.get("day", ""),
        "match": game_match_label(game, show_score=True),
        "team_points": game_team_points,
        "lineups": lineups_snapshot,
        "allstar_applied": allstar_was_active_for_history,
        "price_changes": sorted(price_changes, key=lambda x: abs(float(x["Change"].replace("억원",""))), reverse=True)[:10],
    })
    apply_market_state(players_list)
    record_price_snapshot(players_list, f"G{st.session_state.simulation_game_index + 1}")

    st.session_state.simulation_game_index += 1
    st.session_state.simulation_game_no = st.session_state.simulation_game_index + 1
    if st.session_state.simulation_game_index < len(games_2025_26):
        st.session_state.current_transfer_gameweek = games_2025_26[st.session_state.simulation_game_index].get("gameweek", st.session_state.current_transfer_gameweek)

    update_league_table_from_scores()
    apply_market_state(players_list)

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
# v15 Quest Map / Player Pack Helpers
# =========================
VENUE_COORDS = {
    # Label-card positions on the large Korea map.  These are deliberately
    # spread out so quest boxes do not overlap, while still staying close to
    # each home city's real region.
    "인천도원체육관": (21, 28),
    "부천체육관": (26, 43),
    "용인실내체육관": (44, 31),
    "아산이순신체육관": (34, 58),
    "청주체육관": (55, 47),
    "부산사직실내체육관": (79, 78),
}

VENUE_DOT_COORDS = {
    # Smaller actual-location dots, with quest cards allowed to sit nearby.
    "인천도원체육관": (35, 33),
    "부천체육관": (38, 36),
    "용인실내체육관": (43, 40),
    "아산이순신체육관": (41, 52),
    "청주체육관": (50, 47),
    "부산사직실내체육관": (69, 77),
}

def venue_coord(venue):
    venue = clean(venue)
    for key, coord in VENUE_COORDS.items():
        if key in venue:
            return coord
    # Safe fallback: place unknown venue near the center of the stylized map.
    return (50, 50)

def venue_dot_coord(venue):
    venue = clean(venue)
    for key, coord in VENUE_DOT_COORDS.items():
        if key in venue:
            return coord
    return venue_coord(venue)

def current_game_id():
    g = current_game()
    return g.get("game_id", "") if g else ""

def current_game_status():
    """Public timing status for the game currently targeted by the user."""
    return public_game_status(st.session_state.get("simulation_game_index", 0))


def is_lineup_editable_now():
    """True only before the current game's official lock time."""
    g = current_game()
    if not g:
        return False
    return bool(current_game_status().get("can_edit", False))


def locked_message():
    status = current_game_status()
    g = current_game()
    label = game_match_label(g) if g else "현재 경기"
    return f"{label} · {status.get('label','라인업 잠금')} · {status.get('message','')}"


def block_locked_edit(action="라인업 수정"):
    """Return True when an edit should be blocked because the current game is locked."""
    if is_lineup_editable_now():
        return False
    st.error(f"이미 경기 시작 시간이 지나 {action}을 할 수 없습니다.")
    st.info("잠금 시간 이후에는 저장된 라인업을 확인만 할 수 있고, 카드 선택·팩 이동·선발 교체·캡틴/All-Star 변경·저장은 모두 막힙니다.")
    return True


def render_readonly_locked_lineup(players_list, game=None, status=None):
    """Show the submitted line-up in read-only mode after lock."""
    game = game or current_game()
    status = status or current_game_status()
    gw = game.get("gameweek", "-") if game else "-"
    day = game.get("day", "-") if game else "-"
    st.markdown(f"<div class='section-title'>WK {gw} LOCKED LINE-UP</div>", unsafe_allow_html=True)
    st.error("이미 경기 시작 시간이 지나 이 경기의 라인업은 잠겼습니다.")
    st.info(f"{game_match_label(game) if game else '현재 경기'} · {status.get('label','')} · {status.get('message','')}")

    roster_keys = list(st.session_state.get("user_roster_keys", []))
    starting_keys = list(st.session_state.get("user_starting_keys", []))
    bench_keys = [k for k in roster_keys if k not in starting_keys]
    captain_key = st.session_state.get("user_captain_key")
    allstar_active = bool(st.session_state.get("chip_allstar_active", False))

    if not roster_keys or not starting_keys:
        st.warning("이 경기 시작 전까지 유효한 라인업이 저장되지 않았습니다. 결과 공개 후 이 경기는 0점 처리됩니다.")
    else:
        r1, r2, r3, r4 = st.columns(4)
        with r1:
            summary_card("ROSTER", f"{len(roster_keys)}/10", "🃏")
        with r2:
            summary_card("STARTING", f"{len(starting_keys)}/5", "⭐")
        with r3:
            summary_card("BUDGET", format_price(total_price_for_keys(roster_keys, players_list)), "💰")
        with r4:
            summary_card("ALL-STAR", "ON" if allstar_active else "OFF", "✨")

        st.markdown('<div class="court"><b style="color:#064EA4;">LOCKED STARTING 5</b><div class="reveal-note">이 라인업은 읽기 전용입니다. 캡틴/선발/All-Star를 더 이상 바꿀 수 없습니다.</div>', unsafe_allow_html=True)
        cols = st.columns(5)
        for i, (col, p) in enumerate(zip(cols, keys_to_players(starting_keys, players_list)), start=1):
            with col:
                player_card(p, priority=i, captain=(player_key(p) == captain_key), allstar=allstar_active)
        st.markdown('</div>', unsafe_allow_html=True)

        st.markdown(f'<div class="bench"><b style="color:#E91E73;">LOCKED BENCH</b><div style="font-size:13px;color:#64748b;margin-top:4px;">벤치 선수는 실제로 해당 경기에서 뛰면 Fantasy Score의 {BENCH_SCORE_MULTIPLIER:.0%}만 반영됩니다.</div>', unsafe_allow_html=True)
        cols = st.columns(5)
        for i, (col, p) in enumerate(zip(cols, keys_to_players(bench_keys, players_list)), start=1):
            with col:
                player_card(p, priority=i, compact=True)
        st.markdown('</div>', unsafe_allow_html=True)

    c1, c2 = st.columns(2)
    with c1:
        if st.button("홈으로", key="locked_home", use_container_width=True):
            st.session_state.page = "Home"
            st.rerun()
    with c2:
        if st.button("Simulation에서 결과 확인", key="locked_simulation", use_container_width=True):
            st.session_state.page = "Simulation"
            st.rerun()

def ensure_pack_state_for_current_game():
    """Reset player packs whenever the next game changes."""
    gid = current_game_id()
    if st.session_state.get("pack_game_id") != gid:
        st.session_state.pack_game_id = gid
        st.session_state.pack_back_keys = []
        st.session_state.pack_front_keys = []
        st.session_state.pack_back_opened = False
        st.session_state.pack_front_opened = False
        st.session_state.main_flow_stage = "pack_lobby"

def pack_bg_style():
    splash = asset_data_url(SPLASH_BG_PATH) or asset_data_url(HERO_IMAGE_PATH)
    if splash:
        return f"background-image: url('{splash}');"
    return "background: linear-gradient(135deg,#020617,#064EA4,#E91E73);"

def render_pack_open_card(title, subtitle, pink=False):
    cls = "pack-card pink" if pink else "pack-card"
    st.markdown(f"""
    <div class="{cls}">
        <div style="font-size:44px;">{'💗' if pink else '🔷'}</div>
        <div class="pack-name">{title}</div>
        <div style="font-weight:800;color:rgba(255,255,255,.78);margin-top:6px;">{subtitle}</div>
    </div>
    """, unsafe_allow_html=True)

def pack_pool(players_list, pos_label):
    allowed = current_allowed_teams()
    return [
        p for p in players_list
        if p.get("position_label") == pos_label and (not allowed or p.get("team_2025_26") in allowed)
    ]

def render_position_pack(pos_label, target_count):
    if block_locked_edit("카드팩 수정"):
        return
    is_back = pos_label == "Back Court"
    key_name = "pack_back_keys" if is_back else "pack_front_keys"
    open_name = "pack_back_opened" if is_back else "pack_front_opened"
    title = "BACK COURT PLAYERS PACK" if is_back else "FRONT COURT PLAYERS PACK"
    selected = list(st.session_state.get(key_name, []))
    selected = [k for k in selected if any(player_key(p) == k for p in pack_pool(players, pos_label))]
    st.session_state[key_name] = selected

    if not st.session_state.get(open_name, False):
        render_pack_open_card(title, f"{target_count}장의 카드를 선택하세요", pink=not is_back)
        if st.button(f"OPEN {title}", key=f"open_pack_{key_name}", use_container_width=True):
            st.session_state[open_name] = True
            st.rerun()
        return

    st.markdown(f"### {title}")
    current_budget = pack_total_budget(players)
    st.caption(f"선택할 5장의 카드를 골라주세요. 현재 선택: {len(selected)}/{target_count} · 현재 예산: {format_price(current_budget)}/{format_price(BUDGET_CAP)}")
    st.info("카드 아래 버튼으로 선택/해제할 수 있습니다. 다른 팩에서 이미 고른 카드 가격도 현재 예산에 함께 합산됩니다.")
    pool = sorted(
        pack_pool(players, pos_label),
        key=lambda p: (-float(p.get("current_price", p.get("initial_price", MIN_PRICE))), p.get("name", ""))
    )

    cols = st.columns(2)
    for i, p in enumerate(pool):
        k = player_key(p)
        chosen = k in selected
        with cols[i % 2]:
            player_card(p, priority=i + 1 if i < 8 else None, compact=True)
            if chosen:
                if st.button("✓ 선택됨 · 빼기", key=f"pack_remove_{key_name}_{k}", use_container_width=True):
                    selected = [x for x in selected if x != k]
                    st.session_state[key_name] = selected
                    st.rerun()
            else:
                candidate_budget = pack_total_budget(players) + price_of_key(k, players)
                disabled = len(selected) >= target_count or candidate_budget > BUDGET_CAP
                if st.button("+ 카드 선택", key=f"pack_add_{key_name}_{k}", use_container_width=True, disabled=disabled):
                    selected.append(k)
                    st.session_state[key_name] = selected
                    st.rerun()

def pack_roster_keys():
    return list(st.session_state.get("pack_back_keys", [])) + list(st.session_state.get("pack_front_keys", []))

def save_pack_roster_if_valid(players_list):
    if not is_lineup_editable_now():
        return False, {"valid": False, "errors": ["이미 경기 시작 시간이 지나 라인업을 저장할 수 없습니다."], "players": [], "total_price": 0, "back_count": 0, "front_count": 0}
    selected_keys = pack_roster_keys()
    report = roster_report(selected_keys, players_list)
    if not report["valid"]:
        return False, report
    old_roster = list(st.session_state.user_roster_keys)
    transfer_count = count_transfers(old_roster, selected_keys)
    if st.session_state.simulation_started:
        st.session_state.user_transfers += transfer_count
        st.session_state.user_transfer_log.append({
            "GW": st.session_state.current_transfer_gameweek,
            "Transfers": transfer_count,
            "Penalty": 0,
            "Note": "Unlimited gameday transfers",
        })
    st.session_state.user_roster_keys = list(selected_keys)
    st.session_state.roster_working_keys = list(selected_keys)
    st.session_state.user_starting_keys = auto_starting_keys(st.session_state.user_roster_keys, players_list, st.session_state.user_formation)
    if st.session_state.user_captain_key not in st.session_state.user_starting_keys:
        st.session_state.user_captain_key = st.session_state.user_starting_keys[0] if st.session_state.user_starting_keys else None
    sync_user_lineup_to_simulation()
    update_league_table_from_scores()
    return True, report

def quest_map_html(games, current_index):
    if not games:
        return ""
    current_game_obj = games[min(current_index, len(games) - 1)]
    current_gw = current_game_obj.get("gameweek", 1)
    week_games = [g for g in games if g.get("gameweek") == current_gw]
    bg = asset_data_url(SPLASH_BG_PATH) or asset_data_url(HERO_IMAGE_PATH)
    bg_style = f"background-image:url('{bg}');" if bg else ""

    nodes = []
    lines = []
    dots = []
    used_positions = {}
    for g in week_games:
        gi = games.index(g)
        status = "done" if gi < current_index else ("current" if gi == current_index else "locked")
        status_label = "CLEAR" if status == "done" else ("NEXT" if status == "current" else "LOCKED")
        x, y = venue_coord(g.get("venue", ""))
        # If two games share a home venue in one GW, offset the card gently.
        count = used_positions.get((round(x), round(y)), 0)
        used_positions[(round(x), round(y))] = count + 1
        if count:
            x = min(90, x + 8 * count)
            y = min(88, y + 8 * count)
        dx, dy = venue_dot_coord(g.get("venue", ""))
        logo = team_logo_data_url(g.get("home_team", ""))
        logo_html = f'<img src="{logo}" alt="{g.get("home_team","")}">' if logo else ""
        match = game_match_label(g, show_score=(status == "done"))
        lock = "🔒" if status == "locked" else ""
        dot_class = f"map-dot {status}"
        dots.append(f'<div class="{dot_class}" style="left:{dx}%;top:{dy}%;"></div>')
        lines.append(f'<svg class="leader-line"><line x1="{dx}%" y1="{dy}%" x2="{x}%" y2="{y}%" /></svg>')
        node_inner = f"""
            {logo_html}
            <div class="quest-status">{status_label} {lock}</div>
            <div class="quest-match">G{gi+1:03d}<br>{match}</div>
        """
        if status == "current":
            href = f"?open_quest={g.get('game_id','')}"
            nodes.append(f'<a class="quest-link" href="{href}" target="_top"><div class="quest-node {status}" style="left:{x}%;top:{y}%;">{node_inner}</div></a>')
        else:
            nodes.append(f'<div class="quest-node {status}" style="left:{x}%;top:{y}%;">{node_inner}</div>')

    return f"""
    <!DOCTYPE html>
    <html><head><meta charset="utf-8"><style>
    body {{ margin:0; background:transparent; font-family: Arial, sans-serif; }}
    .quest-wrap {{ position:relative; min-height:720px; border-radius:34px; overflow:hidden; border:1px solid rgba(255,255,255,.22); box-shadow:0 22px 60px rgba(15,23,42,.28); background-size:cover; background-position:center; {bg_style} }}
    .quest-wrap:before {{ content:""; position:absolute; inset:0; background:linear-gradient(135deg, rgba(2,6,23,.84), rgba(6,78,164,.48), rgba(233,30,115,.30)); }}
    .quest-title {{ position:absolute; left:32px; top:26px; z-index:4; color:white; font-size:46px; font-weight:900; text-shadow:0 6px 24px rgba(0,0,0,.45); }}
    .korea-map {{ position:absolute; inset:92px 22px 24px 22px; z-index:2; border-radius:28px; border:1px solid rgba(255,255,255,.18); background:rgba(2,6,23,.34); backdrop-filter:blur(3px); overflow:hidden; }}
    .korea-map svg.base-map {{ position:absolute; inset:0; width:100%; height:100%; z-index:1; }}
    .map-shape {{ fill:rgba(255,255,255,.11); stroke:rgba(255,255,255,.30); stroke-width:4; filter:drop-shadow(0 16px 28px rgba(0,0,0,.28)); }}
    .map-island {{ fill:rgba(255,255,255,.08); stroke:rgba(255,255,255,.20); stroke-width:2; }}
    .leader-line {{ position:absolute; inset:0; width:100%; height:100%; z-index:2; pointer-events:none; }}
    .leader-line line {{ stroke:rgba(250,204,21,.42); stroke-width:2; stroke-dasharray:7 7; }}
    .map-dot {{ position:absolute; width:12px; height:12px; border-radius:999px; transform:translate(-50%,-50%); z-index:3; background:#f8fafc; border:2px solid #facc15; box-shadow:0 0 18px rgba(250,204,21,.75); }}
    .map-dot.locked {{ opacity:.45; filter:grayscale(1); }}
    .quest-link {{ text-decoration:none; color:inherit; }}
    .quest-node {{ position:absolute; width:148px; min-height:118px; transform:translate(-50%,-50%); border-radius:22px; padding:12px; text-align:center; color:white; background:rgba(15,23,42,.86); border:2px solid rgba(255,255,255,.24); box-shadow:0 10px 26px rgba(0,0,0,.30); cursor:pointer; transition:.18s ease; z-index:4; }}
    .quest-node:hover {{ transform:translate(-50%,-54%) scale(1.04); filter:brightness(1.14); }}
    .quest-node.current {{ border-color:#facc15; box-shadow:0 0 0 4px rgba(250,204,21,.22), 0 0 34px rgba(250,204,21,.64), 0 10px 26px rgba(0,0,0,.30); }}
    .quest-node.done {{ opacity:.70; filter:saturate(.7); }}
    .quest-node.locked {{ opacity:.45; filter:grayscale(1); }}
    .quest-node img {{ width:54px; height:54px; object-fit:contain; display:block; margin:0 auto 6px; }}
    .quest-status {{ font-size:12px; font-weight:900; color:#facc15; }}
    .quest-match {{ font-size:12px; font-weight:800; line-height:1.25; margin-top:4px; }}
    </style></head><body>
    <div class="quest-wrap">
      <div class="quest-title">GW {current_gw} QUEST MAP</div>
      <div class="korea-map">
        <svg class="base-map" viewBox="0 0 1000 720" preserveAspectRatio="xMidYMid meet">
          <path class="map-shape" d="M558 42 C627 70 672 118 682 176 C724 208 735 256 712 302 C741 348 725 401 681 431 C670 482 635 528 592 548 C582 604 536 652 477 662 C427 671 388 644 369 601 C321 580 291 537 297 489 C250 455 238 398 267 350 C247 299 260 242 304 209 C315 151 360 105 415 91 C451 45 508 22 558 42 Z"/>
          <path class="map-island" d="M324 626 C344 612 375 610 392 627 C376 650 344 654 324 626 Z"/>
          <path class="map-island" d="M705 566 C725 553 746 559 755 577 C737 588 719 586 705 566 Z"/>
        </svg>
        {''.join(lines)}
        {''.join(dots)}
        {''.join(nodes)}
      </div>
    </div></body></html>
    """

def render_lineup_swap_controls(roster_keys, starting_keys, players_list, key_prefix="lineup_swap"):
    if not is_lineup_editable_now():
        st.caption("라인업 잠금 상태라 선발/벤치 교체는 비활성화되었습니다.")
        return
    bench_keys = [k for k in roster_keys if k not in starting_keys]
    st.markdown('<div class="swap-panel"><b>🔁 선발 ↔ 벤치 교체</b><br><span style="color:#64748b;font-size:13px;">Streamlit 기본 환경에서는 안정적인 드래그 저장이 어려워, 같은 효과를 내는 교체 버튼 방식으로 구현했습니다.</span>', unsafe_allow_html=True)
    if not bench_keys or not starting_keys:
        st.caption("로스터와 Starting 5를 먼저 저장하세요.")
        st.markdown('</div>', unsafe_allow_html=True)
        return

    c1, c2, c3 = st.columns([1.2, 1.2, .8])
    with c1:
        bench_pick = st.selectbox("벤치에서 올릴 선수", bench_keys, format_func=lambda k: label_for_key(k, players_list), key=f"{key_prefix}_bench_pick")
    with c2:
        starter_pick = st.selectbox("벤치로 내릴 선발", starting_keys, format_func=lambda k: label_for_key(k, players_list), key=f"{key_prefix}_starter_pick")
    with c3:
        st.write("")
        st.write("")
        if st.button("교체", key=f"{key_prefix}_button", use_container_width=True):
            proposed = [bench_pick if k == starter_pick else k for k in starting_keys]
            status = validate_starting(proposed, roster_keys, players_list, st.session_state.user_formation)
            if status["valid"]:
                st.session_state.user_starting_keys = proposed
                if st.session_state.user_captain_key == starter_pick:
                    st.session_state.user_captain_key = bench_pick
                sync_user_lineup_to_simulation()
                st.success("선발과 벤치를 교체했습니다.")
                st.rerun()
            else:
                for err in status["errors"]:
                    st.error(err)
    st.markdown('</div>', unsafe_allow_html=True)

def _query_value(name, default=""):
    try:
        value = st.query_params.get(name, default)
        if isinstance(value, list):
            return value[0] if value else default
        return value
    except Exception:
        return default

def handle_query_actions():
    if st.session_state.get("app_phase") != "main":
        return
    open_quest = _query_value("open_quest")
    if open_quest and open_quest == current_game_id():
        st.session_state.page = "My Team"
        st.session_state.main_flow_stage = "pack_lobby"
        try:
            st.query_params.clear()
        except Exception:
            pass

# =========================
# Splash / Start Gate
# =========================
def render_splash_screen():
    splash = asset_data_url(SPLASH_BG_PATH) or asset_data_url(HERO_IMAGE_PATH)
    bg = f"background-image: linear-gradient(90deg, rgba(2,6,23,.76), rgba(2,6,23,.20), rgba(2,6,23,.56)), url('{splash}');" if splash else "background: radial-gradient(circle at 50% 40%, #1d4ed8 0%, #020617 70%);"
    st.markdown(f"""
    <style>
    .splash-screen {{
        min-height: 92vh;
        border-radius: 0;
        margin: -1rem -2.5rem 0 -2.5rem;
        background-size: cover;
        background-position: center 38%;
        position: relative;
        overflow: hidden;
        {bg}
    }}
    .splash-version {{
        position:absolute; top:24px; right:34px; color:rgba(255,255,255,.88);
        font-weight:800; letter-spacing:.5px; font-size:16px; text-shadow:0 3px 10px rgba(0,0,0,.35);
    }}
    .splash-title {{
        position:absolute; left:58px; top:92px; transform:none;
        font-family:'Oswald', sans-serif; color:white; font-size:72px; font-weight:900;
        text-shadow:0 6px 30px rgba(0,0,0,.60); letter-spacing:-1px; text-align:left; line-height:.92;
    }}
    .splash-title span {{ color:#E91E73; }}
    .start-guide {{
        position:absolute; left:50%; bottom:120px; transform:translateX(-50%);
        color:rgba(255,255,255,.82); font-weight:800; text-align:center;
    }}
    div[data-testid="stButton"] > button:has(div), .stButton > button {{
        font-weight:900;
    }}
    .start-button-wrap {{
        position: fixed; left: 50%; bottom: 64px; transform: translateX(-50%); z-index: 10;
        width: 260px;
    }}
    div[data-testid="stButton"] {{
        position: fixed; left: 50%; bottom: 70px; transform: translateX(-50%); z-index: 30; width: 260px;
    }}
    div[data-testid="stButton"] button {{
        background: linear-gradient(135deg, #111827, #020617) !important;
        color: #fff !important; border: 2px solid #facc15 !important; border-radius: 6px !important;
        font-size: 22px !important; letter-spacing: 1px; padding: 0.9rem 1rem !important;
        box-shadow: 0 0 24px rgba(250,204,21,.38), 0 12px 36px rgba(0,0,0,.4) !important;
    }}
    </style>
    <div class="splash-screen">
        <div class="splash-version">{APP_VERSION}</div>
        <div class="splash-title">WKBL<br><span>FANTASY</span></div>
        <div class="start-guide">START를 누르면 배경음악이 재생됩니다.</div>
    </div>
    """, unsafe_allow_html=True)
    _, mid, _ = st.columns([3, 1.2, 3])
    with mid:
        if st.button("START", use_container_width=True):
            st.session_state.bgm_enabled = True
            st.session_state.app_phase = "name_input"
            st.rerun()


def render_name_input_screen():
    render_bgm_player()
    splash = asset_data_url(SPLASH_BG_PATH) or asset_data_url(HERO_IMAGE_PATH)
    bg = f"background-image: linear-gradient(180deg, rgba(2,6,23,.72), rgba(2,6,23,.18) 42%, rgba(2,6,23,.78)), url('{splash}');" if splash else "background: linear-gradient(135deg,#020617,#064EA4,#E91E73);"
    st.markdown(f"""
    <style>
    .name-screen {{
        min-height: 78vh; margin: -1rem -2.5rem 0 -2.5rem; padding: 56px 24px 24px;
        background-size: cover; background-position:center 36%; {bg}; position:relative; overflow:hidden;
    }}
    .name-heading {{
        text-align:center; color:white; text-shadow:0 6px 24px rgba(0,0,0,.55); margin-top:4px;
    }}
    .name-title {{font-family:'Oswald',sans-serif;font-size:56px;font-weight:900;color:white;letter-spacing:-1px;}}
    .name-sub {{color:rgba(255,255,255,.86);font-weight:900;margin-top:8px;font-size:17px;}}
    .name-bottom-spacer {{height:44vh;}}
    </style>
    <div class="name-screen">
      <div class="name-heading">
        <div class="name-title">감독 로그인</div>
        <div class="name-sub">감독명과 패스워드로 진행 상황을 저장하고 이어서 플레이하세요.</div>
      </div>
      <div class="name-bottom-spacer"></div>
    </div>
    """, unsafe_allow_html=True)
    _, mid, _ = st.columns([1.25, 1.5, 1.25])
    with mid:
        with st.form("manager_login_form", clear_on_submit=False):
            name = st.text_input("감독명", value=st.session_state.manager_name, max_chars=16, placeholder="")
            password = st.text_input("패스워드", type="password", placeholder="")
            st.caption("처음 이용하는 감독명은 '새 감독 등록'으로만 만들 수 있습니다. 이미 등록한 감독명은 '로그인 / 이어하기'로만 접속합니다.")
            c_login, c_register = st.columns(2)
            login_submitted = c_login.form_submit_button("로그인 / 이어하기", use_container_width=True)
            register_submitted = c_register.form_submit_button("새 감독 등록", use_container_width=True)
            if login_submitted or register_submitted:
                if not name.strip():
                    st.warning("감독명을 입력해 주세요.")
                elif not password.strip():
                    st.warning("패스워드를 입력해 주세요.")
                elif register_submitted:
                    ok, msg = register_new_user(players, games_2025_26, name.strip(), password)
                    if ok:
                        st.success(msg)
                        st.rerun()
                    else:
                        st.error(msg)
                else:
                    ok, msg = login_existing_user(name.strip(), password)
                    if ok:
                        st.success(msg)
                        st.rerun()
                    else:
                        st.error(msg)


if st.session_state.app_phase == "splash":
    render_splash_screen()
    st.stop()
elif st.session_state.app_phase == "name_input":
    render_name_input_screen()
    st.stop()

render_bgm_player()
handle_query_actions()
track_navigation_change()
render_history_controls()
nav()
page = st.session_state.page

# Common derived values
players_with_data = [p for p in players if p["previous_data"]]
players_no_data = [p for p in players if not p["previous_data"]]
max_score = max([p["fantasy_score"] for p in players_with_data] or [0])
highest_player = max(players_with_data, key=lambda p: p["fantasy_score"], default=None)


def render_admin_page():
    if not st.session_state.get("is_admin"):
        st.error("관리자만 접근할 수 있습니다.")
        return

    st.markdown('<div class="section-title">ADMIN CONTROL CENTER</div>', unsafe_allow_html=True)
    settings = get_public_settings()
    current_start = settings["first_game_start"]
    current_delay = float(settings.get("result_delay_hours", DEFAULT_PUBLIC_RESULT_DELAY_HOURS))

    st.markdown("""
    <div class="panel">
        <div class="panel-title">공개 리그 시간 관리</div>
        <div style="line-height:1.7;color:#475569;">
            참가자 전체가 같은 경기 시간표를 사용합니다. 여기서 첫 경기 시작 시간을 바꾸면 모든 참가자의 라인업 마감/결과 공개 시간이 함께 바뀝니다.
        </div>
    </div>
    """, unsafe_allow_html=True)

    with st.form("admin_schedule_form"):
        c1, c2, c3 = st.columns([1.1, 1, 1])
        with c1:
            new_date = st.date_input("첫 경기 날짜", value=current_start.date())
        with c2:
            new_time = st.time_input("첫 경기 시작 시각 KST", value=current_start.time().replace(microsecond=0))
        with c3:
            new_delay = st.number_input("결과 공개 지연 시간", min_value=0.0, max_value=24.0, value=current_delay, step=0.5)
        submitted = st.form_submit_button("전체 리그 시간표 저장", use_container_width=True)
        if submitted:
            new_dt = datetime.combine(new_date, new_time).replace(tzinfo=KST)
            save_public_settings(new_dt, float(new_delay))
            st.success(f"첫 경기 시작 시간이 {format_kst(new_dt)}로 저장되었습니다. 결과 공개는 경기 시작 후 {new_delay:g}시간 뒤입니다.")
            st.rerun()

    updated = get_public_settings()
    st.info(f"현재 설정: 첫 경기 {format_kst(updated['first_game_start'])} · 결과 공개 지연 {updated['result_delay_hours']:g}시간")

    st.markdown("### 참가자 관리")
    rows = participant_admin_rows()
    if not rows:
        st.info("아직 등록된 참가자가 없습니다.")
    else:
        display_rows = [{k: v for k, v in row.items() if k != "ID"} for row in rows]
        st.markdown(table_html(display_rows, ["감독명", "누적 점수", "진행 경기", "최근 접속/저장"]), unsafe_allow_html=True)

        with st.expander("참가자 삭제"):
            manager_options = [row["감독명"] for row in rows]
            selected_manager = st.selectbox("삭제할 참가자", manager_options, key="admin_delete_manager_select")
            confirm_text = st.text_input("삭제하려면 감독명을 그대로 입력하세요", key="admin_delete_confirm")
            if st.button("참가자 삭제", use_container_width=True, type="primary"):
                target = next((row for row in rows if row["감독명"] == selected_manager), None)
                if not target:
                    st.error("선택한 참가자를 찾을 수 없습니다.")
                elif confirm_text.strip() != selected_manager:
                    st.error("확인용 감독명이 일치하지 않습니다.")
                else:
                    delete_user_by_id(target["ID"])
                    st.success(f"{selected_manager} 참가자를 삭제했습니다.")
                    st.rerun()

        with st.expander("전체 참가자 데이터 초기화", expanded=False):
            st.warning("이 작업은 모든 참가자 계정과 진행 상황을 삭제합니다. 테스트 중일 때만 사용하세요.")
            reset_confirm = st.text_input("전체 초기화를 원하면 RESET 입력", key="admin_reset_all_confirm")
            if st.button("모든 참가자 삭제", use_container_width=True):
                if reset_confirm.strip() == "RESET":
                    db = load_user_db()
                    db["users"] = {}
                    save_user_db(db)
                    st.success("모든 참가자 데이터가 삭제되었습니다.")
                    st.rerun()
                else:
                    st.error("RESET을 정확히 입력해야 합니다.")

    if st.button("관리자 로그아웃", use_container_width=True):
        st.session_state.current_user_id = ""
        st.session_state.manager_name = ""
        st.session_state.is_admin = False
        st.session_state.app_phase = "name_input"
        st.session_state.page = "Home"
        st.rerun()

# =========================
# Pages
# =========================
if page == "Home":
    render_dashboard_home(players, games_2025_26)

elif page == "My Team":
    ensure_pack_state_for_current_game()
    next_game_obj = current_game()
    current_idx = st.session_state.simulation_game_index
    manager_name = st.session_state.get("manager_name", "감독")
    stage = st.session_state.get("main_flow_stage", "pack_lobby")

    if not next_game_obj:
        st.warning("연결된 경기 일정이 없습니다. game_results_2025_26.csv를 먼저 확인해 주세요.")
    else:
        gw = next_game_obj.get("gameweek")
        day = next_game_obj.get("day")
        allowed_teams = current_allowed_teams()
        match_label = game_match_label(next_game_obj)
        time_status = public_game_status(current_idx)

        if not time_status["can_edit"]:
            render_readonly_locked_lineup(players, next_game_obj, time_status)
            st.stop()

        top_left, top_right = st.columns([3, 1.3])
        with top_left:
            st.markdown(f"<div class='section-title'>WK {gw} GAME PREVIEW</div>", unsafe_allow_html=True)
            st.markdown(f"<div style='font-size:18px;font-weight:900;color:#0f172a;margin-bottom:10px;'>감독 닉네임: <span style='color:#E91E73'>{manager_name}</span></div>", unsafe_allow_html=True)
        with top_right:
            st.info(f"현재 경기\n\nGW {gw} · Day {day}\n\n{match_label}")

        if stage in ("map", "dashboard"):
            st.session_state.main_flow_stage = "pack_lobby"
            st.rerun()

        elif stage == "pack_lobby":
            st.markdown('<div class="section-title">PLAYER PACK LOBBY</div>', unsafe_allow_html=True)
            st.info("FRONT/BACK 중 아무 팩이나 먼저 열 수 있습니다. 두 팩에서 각각 5장씩 골라 총 10장의 경기용 카드를 완성하세요.")
            front_keys = list(st.session_state.get("pack_front_keys", []))
            back_keys = list(st.session_state.get("pack_back_keys", []))
            front_budget = total_price_for_keys(front_keys, players)
            back_budget = total_price_for_keys(back_keys, players)
            total_budget = front_budget + back_budget
            p1, p2 = st.columns(2)
            with p1:
                render_pack_open_card("FRONT COURT PLAYERS PACK", f"선택 {len(front_keys)}/5 · 예산 {format_price(total_budget)}/{format_price(BUDGET_CAP)}", pink=True)
                if st.button("OPEN FRONT PACK", use_container_width=True):
                    st.session_state.pack_front_opened = True
                    st.session_state.main_flow_stage = "front_pack"
                    st.rerun()
                if len(front_keys) == 5:
                    st.success(f"선택 완료 5/5 · 예산 {format_price(total_budget)}/{format_price(BUDGET_CAP)}")
            with p2:
                render_pack_open_card("BACK COURT PLAYERS PACK", f"선택 {len(back_keys)}/5 · 예산 {format_price(total_budget)}/{format_price(BUDGET_CAP)}", pink=False)
                if st.button("OPEN BACK PACK", use_container_width=True):
                    st.session_state.pack_back_opened = True
                    st.session_state.main_flow_stage = "back_pack"
                    st.rerun()
                if len(back_keys) == 5:
                    st.success(f"선택 완료 5/5 · 예산 {format_price(total_budget)}/{format_price(BUDGET_CAP)}")
            b1, b2 = st.columns([1,1])
            with b1:
                if st.button("← 홈으로", use_container_width=True):
                    st.session_state.page = "Home"
                    st.rerun()
            with b2:
                can_go = len(front_keys) == 5 and len(back_keys) == 5
                if st.button("라인업 구성으로 이동", use_container_width=True, disabled=not can_go):
                    ok, report = save_pack_roster_if_valid(players)
                    if ok:
                        st.session_state.main_flow_stage = "lineup"
                        st.rerun()
                    else:
                        for err in report["errors"]:
                            st.error(err)


        elif stage == "front_pack":
            st.markdown('<div class="section-title">FRONT COURT PACK OPEN</div>', unsafe_allow_html=True)
            st.warning("선택할 5장의 카드를 골라주세요. 5/5를 채워야 다음 단계로 넘어갈 수 있습니다.")
            render_position_pack("Front Court", ROSTER_FRONT_COUNT)
            c1, c2 = st.columns([1,1])
            with c1:
                if st.button("← PACK LOBBY", use_container_width=True):
                    st.session_state.main_flow_stage = "pack_lobby"
                    st.rerun()
            with c2:
                if st.button("FRONT 선택 완료", use_container_width=True, disabled=len(st.session_state.get("pack_front_keys", [])) < 5):
                    st.session_state.main_flow_stage = "pack_lobby"
                    st.rerun()

        elif stage == "back_pack":
            st.markdown('<div class="section-title">BACK COURT PACK OPEN</div>', unsafe_allow_html=True)
            st.warning("선택할 5장의 카드를 골라주세요. 5/5를 채워야 다음 단계로 넘어갈 수 있습니다.")
            render_position_pack("Back Court", ROSTER_BACK_COUNT)
            c1, c2 = st.columns([1,1])
            with c1:
                if st.button("← PACK LOBBY", use_container_width=True):
                    st.session_state.main_flow_stage = "pack_lobby"
                    st.rerun()
            with c2:
                if st.button("BACK 선택 완료", use_container_width=True, disabled=len(st.session_state.get("pack_back_keys", [])) < 5):
                    st.session_state.main_flow_stage = "pack_lobby"
                    st.rerun()

        elif stage == "lineup":
            st.markdown('<div class="section-title">LINE-UP BUILDER</div>', unsafe_allow_html=True)
            st.info("제한사항: 총 10명 · Back Court 5명 + Front Court 5명 · 예산 14억원 · 선발 5명 구성 후 캡틴 1명 지정")

            selected_pack_keys = pack_roster_keys()
            pack_report = roster_report(selected_pack_keys, players)
            s1, s2, s3, s4 = st.columns(4)
            with s1:
                summary_card("CARDS", f'{len(pack_report["players"])}/10', "🃏")
            with s2:
                summary_card("BUDGET USED", format_price(pack_report["total_price"]), "💰")
            with s3:
                summary_card("BACK COURT", f'{pack_report["back_count"]}/5', "B")
            with s4:
                summary_card("FRONT COURT", f'{pack_report["front_count"]}/5', "F")

            if pack_report["errors"]:
                for err in pack_report["errors"]:
                    st.error(err)
            if not st.session_state.user_roster_keys:
                ok, report = save_pack_roster_if_valid(players)
                if not ok:
                    for err in report["errors"]:
                        st.error(err)

            if st.session_state.user_roster_keys:
                formation = st.radio(
                    "포메이션 선택",
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

                bc_selected = st.multiselect("선발 Back Court", options=back_options, default=default_b, format_func=lambda k: label_for_key(k, players))
                fc_selected = st.multiselect("선발 Front Court", options=front_options, default=default_f, format_func=lambda k: label_for_key(k, players))
                proposed_starting = list(bc_selected) + list(fc_selected)
                start_report = validate_starting(proposed_starting, roster_keys, players, st.session_state.user_formation)
                if start_report["errors"]:
                    for err in start_report["errors"]:
                        st.warning(err)
                if st.button("Starting 5 저장", use_container_width=True, disabled=not start_report["valid"]):
                    st.session_state.user_starting_keys = proposed_starting
                    if st.session_state.user_captain_key not in proposed_starting:
                        st.session_state.user_captain_key = proposed_starting[0] if proposed_starting else None
                    sync_user_lineup_to_simulation()
                    st.success("Starting 5를 저장했습니다.")
                    st.rerun()

                display_starting_keys = proposed_starting if start_report["valid"] else list(st.session_state.user_starting_keys)
                if display_starting_keys:
                    if st.session_state.user_captain_key not in display_starting_keys:
                        st.session_state.user_captain_key = display_starting_keys[0]
                    st.session_state.user_captain_key = st.selectbox(
                        "Captain 선택",
                        options=display_starting_keys,
                        index=display_starting_keys.index(st.session_state.user_captain_key),
                        format_func=lambda k: label_for_key(k, players),
                    )

                # All-Star is decided only after the line-up is effectively confirmed.
                current_gw = next_game_obj.get("gameweek", st.session_state.current_transfer_gameweek) if next_game_obj else st.session_state.current_transfer_gameweek
                used_gws = set(st.session_state.get("chip_allstar_used_gameweeks", []))
                active_allstar = bool(st.session_state.get("chip_allstar_active", False))
                used_allstar = current_gw in used_gws
                a1, a2 = st.columns([2.2, 1])
                with a1:
                    if active_allstar:
                        st.success("All-Star 활성화됨: 다음 시뮬레이션 경기에서 Starting 5 점수 +20%")
                    elif used_allstar:
                        st.info(f"GW {current_gw} All-Star는 이미 사용했습니다.")
                    else:
                        st.info("All-Star는 GW마다 1회 사용 가능합니다. 라인업 확정 후 필요하면 여기서 사용하세요.")
                with a2:
                    if active_allstar:
                        if st.button("All-Star 취소", key="lineup_allstar_cancel", use_container_width=True):
                            st.session_state.chip_allstar_active = False
                            st.session_state.chip_allstar_available = True
                            st.session_state.chip_allstar_active_gameweek = None
                            st.rerun()
                    else:
                        if st.button("All-Star 사용", key="lineup_allstar_activate", use_container_width=True, disabled=used_allstar or not start_report["valid"]):
                            st.session_state.chip_allstar_active = True
                            st.session_state.chip_allstar_available = False
                            st.session_state.chip_allstar_active_gameweek = current_gw
                            st.rerun()

                st.markdown('<div class="court"><b style="color:#064EA4;">STARTING 5</b><div class="reveal-note">선발은 100% 반영, 캡틴은 자동 2배, 벤치는 50% 반영됩니다.</div>', unsafe_allow_html=True)
                cols = st.columns(5)
                allstar_glow = bool(st.session_state.get("chip_allstar_active", False))
                starting_players_sorted = sorted(keys_to_players(display_starting_keys, players), key=lambda p: float(p.get("current_price", p.get("initial_price", MIN_PRICE))))
                for i, (col, p) in enumerate(zip(cols, starting_players_sorted), start=1):
                    with col:
                        player_card(p, priority=i, captain=(player_key(p) == st.session_state.user_captain_key), allstar=allstar_glow)
                st.markdown('</div>', unsafe_allow_html=True)

                bench_keys = [k for k in roster_keys if k not in display_starting_keys]
                bench_players_sorted = sorted(keys_to_players(bench_keys, players), key=lambda p: float(p.get("current_price", p.get("initial_price", MIN_PRICE))))
                st.markdown(f'<div class="bench"><b style="color:#E91E73;">BENCH</b><div style="font-size:13px;color:#64748b;margin-top:4px;">벤치 선수는 실제로 해당 경기에서 뛰면 Fantasy Score의 {BENCH_SCORE_MULTIPLIER:.0%}만 반영됩니다.</div>', unsafe_allow_html=True)
                cols = st.columns(5)
                for i, (col, p) in enumerate(zip(cols, bench_players_sorted), start=1):
                    with col:
                        player_card(p, priority=i, compact=True)
                st.markdown('</div>', unsafe_allow_html=True)

                render_lineup_swap_controls(roster_keys, list(display_starting_keys), players)

                c1, c2, c3 = st.columns(3)
                with c1:
                    if st.button("← PACK LOBBY", use_container_width=True):
                        st.session_state.main_flow_stage = "pack_lobby"
                        st.rerun()
                with c2:
                    if st.button("홈으로", use_container_width=True):
                        st.session_state.page = "Home"
                        st.rerun()
                with c3:
                    if st.button("시뮬레이션 메뉴로 이동", use_container_width=True):
                        st.session_state.page = "Simulation"
                        st.rerun()


elif page == "Schedule":
    st.markdown('<div class="section-title">경기 일정</div>', unsafe_allow_html=True)
    if not games_2025_26:
        st.warning("연결된 경기 일정이 없습니다.")
    else:
        games_by_date = {}
        for g in games_2025_26:
            d = g.get("date", "")
            if d:
                games_by_date.setdefault(d, []).append(g)
        months = sorted({d[:7] for d in games_by_date})
        selected_month = st.selectbox("월 선택", months, index=0, key="schedule_month_select")
        year, month = map(int, selected_month.split("-"))
        cal = calendar.Calendar(firstweekday=6)
        rows = []
        for week in cal.monthdatescalendar(year, month):
            row = []
            for day_obj in week:
                dstr = day_obj.strftime("%Y-%m-%d")
                if day_obj.month != month:
                    row.append("<div style='min-height:110px;color:#cbd5e1;'> </div>")
                    continue
                entries = []
                for g in games_by_date.get(dstr, []):
                    entries.append(f"<div style='margin-top:6px;padding:6px;border-radius:10px;background:#eff6ff;font-size:12px;font-weight:800;color:#064EA4;'>GW {g.get('gameweek')} Day {g.get('day')}<br>{game_match_label(g)}<br><span style='color:#64748b'>{g.get('time','')} · {g.get('venue','')}</span></div>")
                cell = f"<div style='min-height:120px;'><b>{day_obj.day}</b>{''.join(entries)}</div>"
                row.append(cell)
            rows.append(row)
        html = "<table style='width:100%;border-collapse:separate;border-spacing:8px;'>"
        html += "<tr>" + "".join([f"<th style='text-align:left;color:#64748b;padding:8px;'>{d}</th>" for d in ["일","월","화","수","목","금","토"]]) + "</tr>"
        for row in rows:
            html += "<tr>" + "".join([f"<td style='vertical-align:top;border:1px solid #e5e7eb;border-radius:16px;padding:10px;background:white;'>{cell}</td>" for cell in row]) + "</tr>"
        html += "</table>"
        st.markdown(html, unsafe_allow_html=True)

elif page == "Prices":
    st.markdown('<div class="section-title">가격 확인</div>', unsafe_allow_html=True)
    st.caption("시즌이 진행되면서 선수 가격이 어떻게 변했는지 주식 그래프처럼 확인합니다.")
    teams = ["All"] + sorted(set([p["team_2025_26"] for p in players if p["team_2025_26"]]))
    tcol, pcol = st.columns([1, 2])
    with tcol:
        team_filter = st.selectbox("팀", teams, key="price_team_filter")
    candidates = [p for p in players if team_filter == "All" or p.get("team_2025_26") == team_filter]
    candidates = sorted(candidates, key=lambda x: (-float(x.get("current_price", x.get("initial_price", MIN_PRICE))), x.get("name", "")))
    with pcol:
        chosen_key = st.selectbox("선수", [player_key(p) for p in candidates], format_func=lambda k: label_for_key(k, players), key="price_player_select")
    hist = st.session_state.get("price_history", {}).get(chosen_key, [])
    if not hist:
        p0 = player_lookup(players).get(chosen_key)
        hist = [{"Label": "현재", "Price": float(p0.get("current_price", p0.get("initial_price", MIN_PRICE))) if p0 else MIN_PRICE}]
    df = pd.DataFrame(hist)
    if not df.empty and "Label" in df and "Price" in df:
        st.line_chart(df.set_index("Label")[["Price"]], height=360)
    latest = hist[-1]
    st.info(f"현재 가격: {latest.get('Price', 0):.2f}억원 / 가격 변동 제한: 경기당 ±{MAX_PRICE_CHANGE_PER_GAME:.2f}억원")

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

    # Default order: highest current price first.
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
    header_cols[4].markdown("**Current Price**")
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
    st.markdown('<div class="section-title">QUEST PLAY</div>', unsafe_allow_html=True)

    st.markdown("""
    <div class="panel">
        <div class="panel-title">One Game at a Time</div>
        <div style="line-height:1.7;color:#475569;">
            한 번에 여러 경기를 돌리지 않고, 실제 경기 순서대로 하나씩 진행합니다.
            Home 화면의 Next Game 또는 Build My Team에서 선수팩을 열고 라인업을 짜세요.
        </div>
    </div>
    """, unsafe_allow_html=True)

    c1, c2, c3, c4 = st.columns(4)
    with c1:
        summary_card("SALARY CAP", format_price(BUDGET_CAP), "💰")
    with c2:
        summary_card("PRICE MOVE", f"±{MAX_PRICE_CHANGE_PER_GAME:.2f}억원", "📈")
    with c3:
        summary_card("GAME FILE", f"{len(games_2025_26)} games" if game_csv_loaded else "Not loaded", "📄")
    with c4:
        user_team = st.session_state.simulation_user_team or st.session_state.get("manager_name", "나의 팀")
        summary_card("MY POINTS", f'{st.session_state.simulation_team_scores.get(user_team, 0.0):.2f}', "⭐")

    if not game_csv_loaded:
        st.warning("아직 game_results_2025_26.csv가 없습니다. app.py와 같은 위치에 올리면 자동 연결됩니다.")
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
                    "Date": g.get("date", ""),
                    "Time": g.get("time", ""),
                    "GW": g["gameweek"],
                    "Day": g["day"],
                    "Match": game_match_label(g),
                    "Venue": g.get("venue", ""),
                })
            st.markdown(table_html(preview_rows, ["Game", "Date", "Time", "GW", "Day", "Match", "Venue"]), unsafe_allow_html=True)

    if not st.session_state.simulation_started:
        st.info("감독명과 패스워드로 로그인하면 구독자 공개 리그에 참가합니다.")
        if st.button("시작 화면으로 돌아가기", use_container_width=True):
            st.session_state.app_phase = "splash"
            st.rerun()
    else:
        current_idx = st.session_state.simulation_game_index
        finished = game_csv_loaded and current_idx >= len(games_2025_26)

        if game_csv_loaded and not finished:
            g = games_2025_26[current_idx]
            status = public_game_status(current_idx)
            st.markdown("### Next Game")
            st.caption("공개 리그 모드에서는 모든 감독이 같은 실제 시간표를 따릅니다. 경기 시작 후 3시간이 지나야 결과를 확인할 수 있습니다.")
            home_logo = team_logo_img_html(g.get("home_team", ""), 62)
            away_logo = team_logo_img_html(g.get("away_team", ""), 62)
            st.markdown(f"""
            <div class="panel" style="background:linear-gradient(135deg,#020617,#0f2e66);color:white;border-color:rgba(255,255,255,.15);">
              <div style="display:flex;align-items:center;justify-content:space-between;gap:16px;flex-wrap:wrap;">
                <div>
                  <div style="font-size:13px;color:rgba(255,255,255,.62);font-weight:900;">GAME {current_idx + 1} · GW {g.get('gameweek')} DAY {g.get('day')}</div>
                  <div style="font-size:24px;font-weight:900;margin-top:4px;">{game_match_label(g)}</div>
                  <div style="color:rgba(255,255,255,.75);margin-top:4px;">공개 시작: {format_kst(status['start'])}</div>
                  <div style="color:rgba(255,255,255,.75);margin-top:4px;">결과 공개: {format_kst(status['result'])}</div>
                  <div style="margin-top:8px;font-weight:900;color:#facc15;">{status['label']} · {status['message']}</div>
                  <div style="color:rgba(255,255,255,.75);margin-top:4px;">{g.get('venue','')}</div>
                </div>
                <div style="display:flex;align-items:center;gap:12px;">{home_logo}<b>VS</b>{away_logo}</div>
              </div>
            </div>
            """, unsafe_allow_html=True)
            allowed = game_allowed_teams(g)
            if allowed:
                st.info(f"현재 경기: {' vs '.join(allowed)}. 이 두 팀 선수팩만 열 수 있습니다.")

            col_a, col_b = st.columns([1, 1])
            with col_a:
                if st.button("라인업 짜러 가기", use_container_width=True, disabled=not status["can_edit"]):
                    st.session_state.page = "My Team"
                    st.session_state.main_flow_stage = "pack_lobby"
                    st.rerun()
                if not status["can_edit"]:
                    st.caption("경기 시작 후에는 라인업이 잠깁니다.")
            with col_b:
                readiness = gameday_lineup_report(g, players)
                can_reveal = status["can_reveal"]
                if st.button("결과 확인", use_container_width=True, disabled=not can_reveal):
                    if not readiness["valid"]:
                        # Public league rule: missed or invalid line-up is allowed to reveal, but scores 0 for that game.
                        team = st.session_state.get("simulation_user_team")
                        if team:
                            st.session_state.simulation_team_rosters[team] = []
                            st.session_state.simulation_team_starting[team] = []
                            st.session_state.simulation_team_captains[team] = None
                        st.session_state.chip_allstar_active = False
                    process_one_game(games_2025_26[st.session_state.simulation_game_index], players, force_user_zero=not readiness["valid"])
                    save_current_user_progress()
                    st.rerun()
                if not readiness["valid"]:
                    st.warning("라인업을 제출하지 않았거나 조건이 맞지 않습니다. 결과 공개 후 이 경기는 0점 처리됩니다.")
                    for msg in readiness["errors"]:
                        st.caption(msg)
                elif not status["can_reveal"]:
                    st.info(status["message"])

            with st.expander("Reset / restart simulation"):
                st.caption("처음부터 다시 시작해야 할 때만 사용하세요.")
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
        user_team = st.session_state.simulation_user_team
        league = sorted(st.session_state.simulation_league, key=lambda x: (-x["Points"], str(x["Team"])))
        rows = []
        for i, row in enumerate(league, start=1):
            manager_label = "You" if row["Team"] == user_team else row.get("Manager", "Subscriber")
            rows.append({
                "Rank": i,
                "Team": row["Team"],
                "Manager": manager_label,
                "Points": f'{row["Points"]:.2f}',
                "Games": row.get("Games", ""),
                "Transfers": row["Transfers"],
            })
        st.markdown(table_html(rows, ["Rank", "Team", "Manager", "Points", "Games", "Transfers"]), unsafe_allow_html=True)

elif page == "Results":
    st.markdown('<div class="section-title">GAME RESULTS</div>', unsafe_allow_html=True)

    st.markdown("""
    <div class="panel">
        <div class="panel-title">Completed Games Only</div>
        <div style="line-height:1.7;color:#475569;">
            이미 결과 확인을 끝낸 경기 결과, 내 선택 카드, 그리고 당시 라인업을 확인합니다.
        </div>
    </div>
    """, unsafe_allow_html=True)

    if not st.session_state.simulation_history:
        st.info("아직 완료된 경기가 없습니다. Simulation 탭에서 Simulate Next Game을 누르면 이곳에 경기 결과가 쌓입니다.")
    else:
        result_rows = []
        user_team = st.session_state.simulation_user_team
        for i, item in enumerate(st.session_state.simulation_history, start=1):
            user_points = ""
            if user_team:
                user_points = f"{item.get('team_points', {}).get(user_team, 0.0):.2f}"
            result_rows.append({
                "No.": i,
                "Date": item.get("date", ""),
                "Time": item.get("time", ""),
                "GW": item.get("gameweek", ""),
                "Day": item.get("day", ""),
                "Result": item.get("match", ""),
                "My Fantasy Points": user_points,
                "All-Star": "ON" if item.get("allstar_applied") else "-",
            })
        st.markdown(table_html(result_rows, ["No.", "Date", "Time", "GW", "Day", "Result", "My Fantasy Points", "All-Star"]), unsafe_allow_html=True)

        for i, item in enumerate(reversed(st.session_state.simulation_history), start=1):
            title = f"{item.get('date','')} · GW {item.get('gameweek')} Day {item.get('day')} · {item.get('match','')}"
            with st.expander(title, expanded=(i == 1)):
                point_rows = []
                for team, pts in sorted(item.get("team_points", {}).items(), key=lambda x: -x[1]):
                    point_rows.append({"Fantasy Team": team, "Game Points": f"{pts:.2f}"})
                st.markdown("#### Fantasy Points")
                st.markdown(table_html(point_rows, ["Fantasy Team", "Game Points"]), unsafe_allow_html=True)

                st.markdown("#### 라인업 구성 보기")
                lineup_rows = []
                for fantasy_team, snap in item.get("lineups", {}).items():
                    lineup_rows.append({
                        "Fantasy Team": fantasy_team,
                        "Captain": snap.get("captain", "-"),
                        "Starting 5": " / ".join(snap.get("starting", [])),
                        "Bench": " / ".join(snap.get("bench", [])),
                    })
                st.markdown(table_html(lineup_rows, ["Fantasy Team", "Captain", "Starting 5", "Bench"]), unsafe_allow_html=True)

                st.markdown("#### Price Changes")
                st.markdown(table_html(item.get("price_changes", []), ["Player", "Team", "Game Score", "Old Price", "Change", "New Price"]), unsafe_allow_html=True)


elif page == "Admin":
    render_admin_page()

elif page == "Help":
    st.markdown('<div class="section-title">HOW TO PLAY</div>', unsafe_allow_html=True)
    st.markdown("""
    ### Core Rules
    - Roster size: 10 players
    - Roster composition: 5 Back Court + 5 Front Court
    - Starting line-up: 5 players
    - Formation: 2 Back Court / 3 Front Court or 3 Back Court / 2 Front Court
    - Salary cap: 14억원
    - Gameday player pool: only the two WKBL teams playing the next real game
    - Transfers: unlimited every Gameday
    - Deadline: 각 경기 시작 시각 정각, KST. 경기 시작 후에는 해당 경기 라인업이 잠깁니다.
    - Game results are read from `game_results_2025_26.csv`
    - 공개 리그에서는 관리자 페이지의 첫 경기 시작 시간을 기준으로 모든 감독이 같은 시간표를 따릅니다. 결과는 관리자가 설정한 지연 시간 이후 공개됩니다.

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

    ### Chips / Transactions Guide
    - Captain은 My Team의 `SET YOUR LINE-UP` 아래 Captain 선택 박스에서 고릅니다.
    - Captain은 별도 Activate 버튼 없이 자동 적용됩니다. 선택한 Captain의 다음 경기 점수가 한 번 더 더해져 사실상 2배가 됩니다.
    - All-Star는 Home 메뉴가 아니라 Line-up Builder에서 최종 라인업을 확정한 뒤 사용할 수 있습니다. Gameweek마다 1회 사용할 수 있고, 다음으로 시뮬레이션하는 실제 경기 1경기에서 내 Starting 5 총점에 +20% 보너스가 붙습니다.
    - 점수 계수: Starting 5 = 100%, Bench = 50%.
    - Transfers are unlimited every Gameday. 이적 횟수 제한과 점수 페널티는 없습니다.
    - 단, 현재 Gameday에 경기하는 두 팀 선수만 로스터로 선택할 수 있습니다.

    ### Bench Role
    - Starting 5는 경기 Fantasy Score가 100% 반영됩니다.
    - Bench 선수는 실제로 그 경기에서 뛰면 Fantasy Score의 50%가 반영됩니다.
    - 따라서 Bench도 완전히 의미 없는 후보가 아니라, 낮은 계수로 점수를 보태는 보유 자원입니다.
    - 추후 원하면 DNP/부상/미출전 시 자동 교체 규칙을 추가할 수 있습니다.

    ### Important Simulation Detail
    - 각 실제 경기에서는 그 경기에 출전한 선수만 점수를 얻습니다.
    - 예를 들어 다음 경기가 BNK썸 vs 신한은행이면, My Team에서는 BNK썸과 신한은행 선수만 선택할 수 있습니다.
    - Simulation 탭에는 `Simulate Next Game`만 남겨두었습니다. 경기마다 Home → Build My Team에서 로스터와 Starting 5를 맞춘 뒤 한 경기씩 진행하는 방식입니다.
    """)

# Save progress once per rerun for logged-in managers.
if st.session_state.get("app_phase") == "main" and st.session_state.get("current_user_id") and not st.session_state.get("is_admin"):
    save_current_user_progress()

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
