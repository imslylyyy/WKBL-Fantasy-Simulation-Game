import base64
import csv
import math
import mimetypes
import re
import unicodedata
import urllib.request
from io import StringIO
from pathlib import Path
from urllib.parse import urlparse, parse_qs

import pandas as pd
import streamlit as st


# =========================================================
# WKBL Overall Lab / Rating Maker
# ---------------------------------------------------------
# Purpose:
#   A clean homepage for importing WKBL player records across seasons
#   and converting those records into 40~99 game-style overalls.
#
# Run:
#   python -m streamlit run app_v32_0.py
#
# Optional files in the same folder:
#   players_2024_25.csv
#   assets/team_logos/*.png
#   assets/hero.jpg or assets/splash_bg.jpg
#   images/선수명_팀명.png
# =========================================================

APP_VERSION = "v32.0 / WKBL Overall Lab only"
st.set_page_config(page_title="WKBL Overall Lab", page_icon="🏀", layout="wide", initial_sidebar_state="expanded")

BASE_DIR = Path(__file__).resolve().parent
CSV_PATH = BASE_DIR / "players_2024_25.csv"
IMAGE_DIR = BASE_DIR / "images"
ASSET_DIR = BASE_DIR / "assets"
TEAM_LOGO_DIR = ASSET_DIR / "team_logos"
HERO_IMAGE_PATH = ASSET_DIR / "hero.jpg"
SPLASH_BG_PATH = ASSET_DIR / "splash_bg.jpg"
WKBL_LOGO_PATH = ASSET_DIR / "wkbl_logo.png"

OVERALL_MIN = 40
OVERALL_MAX = 99
DEFAULT_SEASON_LABEL = "2024-25"

SIMULATION_TEAMS = ["KB스타즈", "하나은행", "삼성생명", "우리은행", "BNK썸", "신한은행"]

TEAM_LOGO_FILES = {
    "KB스타즈": "KB스타즈.png",
    "하나은행": "하나은행.png",
    "삼성생명": "삼성생명.png",
    "우리은행": "우리은행.PNG",
    "BNK썸": "BNK썸.PNG",
    "신한은행": "신한은행.png",
}
TEAM_YOUTUBE_LINKS = {
    "KB스타즈": "https://youtube.com/@kbstarsbasketball?si=6502xUMTciNX_dbe",
    "하나은행": "https://youtube.com/channel/UC9CSBrokovIRWjwY6TVATrw?si=otv18_qFfx9v_a8G",
    "삼성생명": "https://youtube.com/@goblueminx?si=3qUjdoHFhlwljlgz",
    "우리은행": "https://youtube.com/@wooribasketball?si=GyiWlpf7BjxFGa0R",
    "BNK썸": "https://youtube.com/@bnktv9784?si=HLrRzehxhQdTJx0L",
    "신한은행": "https://youtube.com/@shsbird?si=NtTwLY7lcb7rU1QW",
}

OVERALL_FIELDS = [
    "season", "name", "team", "position", "games", "minutes",
    "pts", "reb", "oreb", "dreb", "ast", "stl", "blk", "to",
    "2pm", "2pa", "3pm", "3pa", "ftm", "fta", "gooddef",
]

WKBL_RECORD_PART_STAT = {
    "score": "pts",
    "point": "pts",
    "points": "pts",
    "threepoint": "3pm",
    "3point": "3pm",
    "three": "3pm",
    "rebound": "reb",
    "reb": "reb",
    "assist": "ast",
    "ast": "ast",
    "steal": "stl",
    "stl": "stl",
    "block": "blk",
    "blk": "blk",
    "freethrow": "ftm",
    "free": "ftm",
    "ft": "ftm",
    "twopoint": "2pm",
    "2point": "2pm",
    "gooddef": "gooddef",
}


# =========================
# Basic helpers
# =========================
def clean(value) -> str:
    if value is None:
        return ""
    return str(value).strip()


def canonical_team(team) -> str:
    team = clean(team)
    compact = team.replace(" ", "")
    aliases = {
        "BNK썸": "BNK썸",
        "BNK": "BNK썸",
        "BNKSUM": "BNK썸",
        "KB스타즈": "KB스타즈",
        "KBStars": "KB스타즈",
        "KBSTARS": "KB스타즈",
        "KB": "KB스타즈",
        "삼성생명": "삼성생명",
        "삼성": "삼성생명",
        "블루밍스": "삼성생명",
        "우리은행": "우리은행",
        "우리": "우리은행",
        "우리WON": "우리은행",
        "하나은행": "하나은행",
        "하나": "하나은행",
        "신한은행": "신한은행",
        "신한": "신한은행",
        "에스버드": "신한은행",
    }
    return aliases.get(compact, compact or team)


def normalize_position(pos) -> str:
    pos = clean(pos).upper().replace(" ", "")
    if pos in ["B", "G", "PG", "SG", "GUARD", "BACKCOURT", "BACK"]:
        return "Back Court"
    if pos in ["F", "C", "PF", "SF", "CENTER", "FORWARD", "FRONTCOURT", "FRONT"]:
        return "Front Court"
    return "Not Set"


def position_short(pos) -> str:
    label = normalize_position(pos)
    if label == "Back Court":
        return "B"
    if label == "Front Court":
        return "F"
    return clean(pos) or "?"


def to_float(value, default=0.0):
    value = clean(value).replace(",", "")
    if value == "":
        return default
    value = re.sub(r"[^0-9.\-]", "", value)
    if value in ["", ".", "-", "-."]:
        return default
    try:
        return float(value)
    except Exception:
        return default


def parse_minutes(value):
    value = clean(value)
    if not value:
        return 0.0
    if ":" in value:
        left, right = value.split(":", 1)
        minutes = to_float(left, 0.0)
        seconds = to_float(right, 0.0)
        return minutes + seconds / 60
    return to_float(value, 0.0)


def split_pair(value):
    value = clean(value).replace(" ", "")
    if "-" not in value:
        return 0.0, 0.0
    a, b = value.split("-", 1)
    return to_float(a), to_float(b)


def safe_div(a, b, default=0.0):
    try:
        b = float(b)
        if abs(b) < 1e-12:
            return default
        return float(a) / b
    except Exception:
        return default


def read_csv_with_fallback(path_or_file):
    encodings = ["utf-8-sig", "utf-8", "cp949", "euc-kr"]
    last_error = None
    for enc in encodings:
        try:
            if isinstance(path_or_file, (str, Path)):
                return pd.read_csv(path_or_file, encoding=enc), enc
            path_or_file.seek(0)
            return pd.read_csv(path_or_file, encoding=enc), enc
        except UnicodeDecodeError as e:
            last_error = e
        except Exception as e:
            last_error = e
    raise last_error


def asset_data_url(path):
    path = Path(path)
    if not path.exists():
        return None
    mime, _ = mimetypes.guess_type(path)
    mime = mime or "image/png"
    data = base64.b64encode(path.read_bytes()).decode("utf-8")
    return f"data:{mime};base64,{data}"


def _norm_text(s):
    s = clean(s).replace(" ", "")
    s = unicodedata.normalize("NFKC", s)
    return re.sub(r"[^0-9A-Za-z가-힣]", "", s)


def team_logo_data_url(team):
    canon = canonical_team(team)
    filename = TEAM_LOGO_FILES.get(canon, "")
    candidates = []
    if filename:
        candidates.append(TEAM_LOGO_DIR / filename)
    for ext in [".png", ".PNG", ".jpg", ".JPG", ".jpeg", ".JPEG", ".webp"]:
        candidates.append(TEAM_LOGO_DIR / f"{canon}{ext}")
    for path in candidates:
        if path.exists():
            return asset_data_url(path)
    return None


def find_player_image(name, team=""):
    if not IMAGE_DIR.exists():
        return None
    name_norm = _norm_text(name)
    team_norm = _norm_text(canonical_team(team))
    image_files = [p for p in IMAGE_DIR.rglob("*") if p.is_file() and p.suffix.lower() in [".png", ".jpg", ".jpeg", ".webp"]]
    for p in image_files:
        stem = _norm_text(p.stem)
        if team_norm and name_norm and name_norm in stem and team_norm in stem:
            return p
    for p in image_files:
        stem = _norm_text(p.stem)
        if name_norm and (stem == name_norm or stem.startswith(name_norm) or name_norm in stem):
            return p
    return None


# =========================
# Data normalization
# =========================
def _norm_col(value):
    value = clean(value)
    if isinstance(value, tuple):
        value = "_".join([clean(x) for x in value if clean(x) and not str(x).startswith("Unnamed")])
    value = unicodedata.normalize("NFKC", str(value))
    value = value.lower().strip()
    value = re.sub(r"\s+", "", value)
    value = value.replace("/", "").replace("-", "").replace("_", "")
    return value


def flatten_columns(df):
    out = df.copy()
    out.columns = [clean("_".join([clean(x) for x in c if clean(x) and not str(x).startswith("Unnamed")])) if isinstance(c, tuple) else clean(c) for c in out.columns]
    return out


def find_col(df, candidates):
    normalized = {_norm_col(c): c for c in df.columns}
    for cand in candidates:
        key = _norm_col(cand)
        if key in normalized:
            return normalized[key]
    for c in df.columns:
        nc = _norm_col(c)
        for cand in candidates:
            key = _norm_col(cand)
            if key and (key in nc or nc in key):
                return c
    return None


def normalize_source_dataframe(df, season_label="", source_label="", part_hint=""):
    """Convert user CSV/WKBL table/current app CSV to canonical player-season rows."""
    if df is None or len(df) == 0:
        return []
    df = flatten_columns(df)

    name_col = find_col(df, ["name", "player", "선수", "선수명", "성명"])
    team_col = find_col(df, ["team", "team_2025_26", "소속", "소속팀", "팀", "팀명", "구단"])
    pos_col = find_col(df, ["position", "pos", "포지션", "위치"])
    season_col = find_col(df, ["season", "시즌", "연도"])
    games_col = find_col(df, ["games", "g", "gp", "경기", "경기수", "출전경기"])
    min_col = find_col(df, ["minutes", "min", "출전시간", "출장시간", "시간", "minutes_float"])

    stat_cols = {
        "pts": find_col(df, ["pts", "points", "득점", "점수", "평균득점"]),
        "reb": find_col(df, ["reb", "tot", "totalreb", "리바운드", "총리바운드", "평균리바운드"]),
        "oreb": find_col(df, ["oreb", "off", "offreb", "공격리바운드", "공격"]),
        "dreb": find_col(df, ["dreb", "def", "defreb", "수비리바운드", "수비"]),
        "ast": find_col(df, ["ast", "assist", "어시스트", "평균어시스트"]),
        "stl": find_col(df, ["stl", "steal", "스틸", "평균스틸"]),
        "blk": find_col(df, ["blk", "block", "bs", "블록", "블록슛", "평균블록"]),
        "to": find_col(df, ["to", "turnover", "turnovers", "턴오버", "실책"]),
        "2pm": find_col(df, ["2pm", "2p", "2pmade", "fg2m", "2점성공", "2점슛성공"]),
        "2pa": find_col(df, ["2pa", "2a", "fg2a", "2점시도", "2점슛시도"]),
        "3pm": find_col(df, ["3pm", "3p", "3pmade", "fg3m", "3점성공", "3점슛성공"]),
        "3pa": find_col(df, ["3pa", "3a", "fg3a", "3점시도", "3점슛시도"]),
        "ftm": find_col(df, ["ftm", "ft", "ftmade", "자유투성공"]),
        "fta": find_col(df, ["fta", "자유투시도"]),
        "gooddef": find_col(df, ["gooddef", "gd", "굿디펜스", "공헌도"]),
    }
    pair_2p_col = find_col(df, ["2PM-A", "2PMA", "2P-A", "2점슛"])
    pair_3p_col = find_col(df, ["3PM-A", "3PMA", "3P-A", "3점슛"])
    pair_ft_col = find_col(df, ["FTM-A", "FTMA", "FT-A", "자유투"])

    part_main_stat = part_hint if part_hint in OVERALL_FIELDS else ""
    numeric_candidate_cols = []
    for c in df.columns:
        nc = _norm_col(c)
        if any(x in nc for x in ["순위", "rank", "선수", "팀", "소속", "season", "시즌"]):
            continue
        sample_vals = [to_float(v, None) for v in df[c].head(12).tolist()]
        numeric_count = sum(v is not None for v in sample_vals)
        if numeric_count >= max(2, min(5, len(df) // 4)):
            numeric_candidate_cols.append(c)
    part_value_col = numeric_candidate_cols[-1] if part_main_stat and numeric_candidate_cols else None

    rows = []
    for _, raw in df.iterrows():
        name = clean(raw.get(name_col, "")) if name_col else ""
        if not name or name in ["선수", "선수명", "팀합계", "합계"]:
            continue
        if len(name) > 40 and not re.search(r"[가-힣A-Za-z]", name):
            continue

        rec = {field: 0.0 for field in OVERALL_FIELDS}
        rec.update({"season": clean(raw.get(season_col, "")) if season_col else clean(season_label), "name": name})
        rec["team"] = canonical_team(raw.get(team_col, "")) if team_col else ""
        rec["position"] = position_short(raw.get(pos_col, "")) if pos_col else ""
        rec["games"] = to_float(raw.get(games_col, 0)) if games_col else 0.0
        rec["minutes"] = raw.get(min_col, "") if min_col else ""

        for stat, col in stat_cols.items():
            rec[stat] = to_float(raw.get(col, 0)) if col else 0.0

        if pair_2p_col:
            made, att = split_pair(raw.get(pair_2p_col, ""))
            if made or att:
                rec["2pm"], rec["2pa"] = made, att
        if pair_3p_col:
            made, att = split_pair(raw.get(pair_3p_col, ""))
            if made or att:
                rec["3pm"], rec["3pa"] = made, att
        if pair_ft_col:
            made, att = split_pair(raw.get(pair_ft_col, ""))
            if made or att:
                rec["ftm"], rec["fta"] = made, att

        if not rec["reb"] and (rec["oreb"] or rec["dreb"]):
            rec["reb"] = float(rec["oreb"] or 0) + float(rec["dreb"] or 0)
        if not rec["dreb"] and rec["reb"] and rec["oreb"]:
            rec["dreb"] = max(float(rec["reb"]) - float(rec["oreb"]), 0.0)

        if part_main_stat and part_value_col:
            inferred = to_float(raw.get(part_value_col, 0))
            if inferred:
                rec[part_main_stat] = max(float(rec.get(part_main_stat, 0.0) or 0.0), inferred)

        if not rec["team"]:
            m = re.search(r"(.+?)\s*\[([^\]]+)\]", name)
            if m:
                rec["name"] = clean(m.group(1))
                rec["team"] = canonical_team(m.group(2))
        rec["season"] = rec["season"] or season_label or "Unknown"
        rec["source"] = source_label
        rows.append(rec)
    return rows


def merge_records(records):
    """Merge split WKBL ranking tables into one player-season row."""
    merged = {}
    numeric_fields = [f for f in OVERALL_FIELDS if f not in ["season", "name", "team", "position", "minutes"]]
    for rec in records:
        season = clean(rec.get("season", "")) or "Unknown"
        name = clean(rec.get("name", ""))
        team = canonical_team(rec.get("team", ""))
        if not name:
            continue
        key = (season, name, team)
        if key not in merged:
            merged[key] = {field: 0.0 for field in numeric_fields}
            merged[key].update({
                "season": season,
                "name": name,
                "team": team,
                "position": clean(rec.get("position", "")),
                "minutes": rec.get("minutes", ""),
                "sources": [],
            })
        base = merged[key]
        if clean(rec.get("position", "")) and not clean(base.get("position", "")):
            base["position"] = clean(rec.get("position", ""))
        if clean(rec.get("minutes", "")) and not clean(base.get("minutes", "")):
            base["minutes"] = rec.get("minutes", "")
        if rec.get("source") and rec.get("source") not in base["sources"]:
            base["sources"].append(rec.get("source"))
        for field in numeric_fields:
            val = rec.get(field, 0.0)
            try:
                val = float(val or 0.0)
            except Exception:
                val = 0.0
            # For split ranking pages, preserve the strongest recognized value.
            # For one full CSV, values usually appear once, so this is unchanged.
            if abs(val) > abs(float(base.get(field, 0.0) or 0.0)):
                base[field] = val
    return list(merged.values())


def current_players_to_records(season_label=DEFAULT_SEASON_LABEL):
    if not CSV_PATH.exists():
        return []
    df, _enc = read_csv_with_fallback(CSV_PATH)
    return merge_records(normalize_source_dataframe(df, season_label=season_label, source_label="players_csv"))


def sample_records():
    raw = [
        {"season": "2024-25", "name": "김단비", "team": "우리은행", "position": "F", "games": 29, "minutes": "1041:36", "pts": 612, "reb": 316, "oreb": 92, "dreb": 224, "ast": 105, "stl": 60, "blk": 44, "to": 93, "2pm": 200, "2pa": 478, "3pm": 27, "3pa": 121, "ftm": 131, "fta": 175},
        {"season": "2024-25", "name": "강이슬", "team": "KB스타즈", "position": "B", "games": 30, "minutes": "1062:35", "pts": 424, "reb": 222, "oreb": 49, "dreb": 173, "ast": 51, "stl": 44, "blk": 18, "to": 72, "2pm": 74, "2pa": 181, "3pm": 64, "3pa": 223, "ftm": 84, "fta": 102},
        {"season": "2024-25", "name": "박지수", "team": "KB스타즈", "position": "F", "games": 30, "minutes": "1050:00", "pts": 610, "reb": 450, "oreb": 120, "dreb": 330, "ast": 95, "stl": 38, "blk": 75, "to": 80, "2pm": 230, "2pa": 430, "3pm": 4, "3pa": 18, "ftm": 138, "fta": 190},
        {"season": "2024-25", "name": "안혜지", "team": "BNK썸", "position": "B", "games": 30, "minutes": "990:00", "pts": 280, "reb": 118, "oreb": 18, "dreb": 100, "ast": 210, "stl": 49, "blk": 4, "to": 92, "2pm": 80, "2pa": 190, "3pm": 27, "3pa": 85, "ftm": 39, "fta": 52},
        {"season": "2024-25", "name": "신이슬", "team": "신한은행", "position": "B", "games": 28, "minutes": "760:00", "pts": 245, "reb": 78, "oreb": 12, "dreb": 66, "ast": 72, "stl": 29, "blk": 5, "to": 56, "2pm": 57, "2pa": 132, "3pm": 37, "3pa": 116, "ftm": 20, "fta": 25},
    ]
    return raw


# =========================
# Official WKBL URL import
# =========================
def looks_like_wkbl_url(url):
    try:
        parsed = urlparse(url)
        host = parsed.netloc.lower()
        return host == "wkbl.or.kr" or host == "www.wkbl.or.kr" or host.endswith(".wkbl.or.kr")
    except Exception:
        return False


def infer_record_part_from_url(url):
    try:
        qs = parse_qs(urlparse(url).query)
        part = clean((qs.get("part") or [""])[0]).lower()
        return part, WKBL_RECORD_PART_STAT.get(part, "")
    except Exception:
        return "", ""


@st.cache_data(show_spinner=False, ttl=60 * 60)
def fetch_official_html_cached(url):
    if not looks_like_wkbl_url(url):
        raise ValueError("보안을 위해 wkbl.or.kr 공식 URL만 불러올 수 있습니다.")
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 WKBL-Overall-Lab/2.0",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        },
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        raw = resp.read()
        charset = resp.headers.get_content_charset()
    for enc in [charset, "utf-8-sig", "utf-8", "cp949", "euc-kr"]:
        if not enc:
            continue
        try:
            return raw.decode(enc)
        except Exception:
            pass
    return raw.decode("utf-8", errors="replace")


def read_html_tables_safely(html):
    try:
        return pd.read_html(StringIO(html))
    except Exception:
        return []


def load_records_from_urls(urls, season_label):
    all_records = []
    messages = []
    for idx, url in enumerate(urls):
        url = clean(url)
        if not url:
            continue
        if not looks_like_wkbl_url(url):
            messages.append(f"URL {idx + 1}: 공식 WKBL URL이 아니어서 건너뜀")
            continue
        part, stat_hint = infer_record_part_from_url(url)
        try:
            html = fetch_official_html_cached(url)
            tables = read_html_tables_safely(html)
            if not tables:
                messages.append(f"URL {idx + 1}: HTML 표를 찾지 못함")
                continue
            found = 0
            for t_idx, table in enumerate(tables):
                rows = normalize_source_dataframe(
                    table,
                    season_label=season_label,
                    source_label=f"official:{part or 'url'}#{idx + 1}.{t_idx + 1}",
                    part_hint=stat_hint,
                )
                if rows:
                    all_records.extend(rows)
                    found += len(rows)
            messages.append(f"URL {idx + 1}: 표 {len(tables)}개 중 {found}개 선수행 인식")
        except Exception as e:
            messages.append(f"URL {idx + 1}: 불러오기 실패 - {e}")
    return merge_records(all_records), messages


# =========================
# Overall calculation
# =========================
def stat_pg(row, field, mode):
    games = max(float(row.get("games", 0.0) or 0.0), 0.0)
    value = float(row.get(field, 0.0) or 0.0)
    if mode == "경기당 평균":
        return value
    if mode == "누적 기록":
        return safe_div(value, games) if games else value

    # Automatic heuristic: large basketball counting values are usually totals.
    plausible_pg_limits = {
        "pts": 45, "reb": 25, "oreb": 10, "dreb": 18, "ast": 15,
        "stl": 6, "blk": 7, "to": 9, "2pm": 18, "2pa": 35,
        "3pm": 10, "3pa": 22, "ftm": 14, "fta": 18, "gooddef": 15,
    }
    limit = plausible_pg_limits.get(field, 30)
    if games and value > limit:
        return value / games
    return value


def minutes_pg(row, mode):
    raw = row.get("minutes", 0.0)
    value = parse_minutes(raw)
    games = max(float(row.get("games", 0.0) or 0.0), 0.0)
    if mode == "경기당 평균":
        return value
    if mode == "누적 기록":
        return safe_div(value, games) if games else value
    if games and value > 60:
        return value / games
    return value


def robust_scale(series, low=OVERALL_MIN, high=OVERALL_MAX, invert=False):
    s = pd.to_numeric(series, errors="coerce").fillna(0.0)
    if len(s) == 0:
        return pd.Series([], dtype=float)
    if s.nunique() <= 1:
        return pd.Series([60.0] * len(s), index=s.index)
    q5 = s.quantile(0.05)
    q95 = s.quantile(0.95)
    if abs(q95 - q5) < 1e-9:
        q5, q95 = s.min(), s.max()
    if abs(q95 - q5) < 1e-9:
        scaled = pd.Series([60.0] * len(s), index=s.index)
    else:
        scaled = (s.clip(q5, q95) - q5) / (q95 - q5)
        if invert:
            scaled = 1.0 - scaled
        scaled = low + scaled * (high - low)
    return scaled.round(2)


def grade_from_overall(ovr):
    ovr = int(round(float(ovr)))
    if ovr >= 95:
        return "MVP"
    if ovr >= 90:
        return "STAR"
    if ovr >= 85:
        return "ELITE"
    if ovr >= 75:
        return "STARTER"
    if ovr >= 65:
        return "ROTATION"
    if ovr >= 55:
        return "DEVELOPING"
    return "PROSPECT"


def calculate_overall_ratings(records, stat_mode="자동 판단"):
    if not records:
        return pd.DataFrame()
    df = pd.DataFrame(records).copy()
    for field in OVERALL_FIELDS:
        if field not in df.columns:
            df[field] = 0.0 if field not in ["season", "name", "team", "position", "minutes"] else ""

    rows = []
    for _, row in df.iterrows():
        games = float(row.get("games", 0.0) or 0.0)
        pts_pg = stat_pg(row, "pts", stat_mode)
        reb_pg = stat_pg(row, "reb", stat_mode)
        oreb_pg = stat_pg(row, "oreb", stat_mode)
        dreb_pg = stat_pg(row, "dreb", stat_mode)
        if not reb_pg and (oreb_pg or dreb_pg):
            reb_pg = oreb_pg + dreb_pg
        ast_pg = stat_pg(row, "ast", stat_mode)
        stl_pg = stat_pg(row, "stl", stat_mode)
        blk_pg = stat_pg(row, "blk", stat_mode)
        tov_pg = stat_pg(row, "to", stat_mode)
        pm2_pg = stat_pg(row, "2pm", stat_mode)
        pa2_pg = stat_pg(row, "2pa", stat_mode)
        pm3_pg = stat_pg(row, "3pm", stat_mode)
        pa3_pg = stat_pg(row, "3pa", stat_mode)
        ftm_pg = stat_pg(row, "ftm", stat_mode)
        fta_pg = stat_pg(row, "fta", stat_mode)
        gooddef_pg = stat_pg(row, "gooddef", stat_mode)
        min_pg = minutes_pg(row, stat_mode)

        fga_pg = pa2_pg + pa3_pg
        fg_pct = safe_div(pm2_pg + pm3_pg, fga_pg)
        three_pct = safe_div(pm3_pg, pa3_pg)
        ft_pct = safe_div(ftm_pg, fta_pg)
        ts_pct = safe_div(pts_pg, 2 * (fga_pg + 0.44 * fta_pg))
        fantasy_like = pts_pg + stl_pg + blk_pg + dreb_pg + 1.5 * (oreb_pg + ast_pg) + min_pg / 4 - 1.5 * tov_pg
        usage_proxy = fga_pg + 0.44 * fta_pg + tov_pg

        rows.append({
            "Season": clean(row.get("season", "")) or "Unknown",
            "Player": clean(row.get("name", "")),
            "Team": canonical_team(row.get("team", "")),
            "Position": normalize_position(row.get("position", "")),
            "Pos": position_short(row.get("position", "")),
            "Games": games,
            "MIN/G": round(min_pg, 2),
            "PTS/G": round(pts_pg, 2),
            "REB/G": round(reb_pg, 2),
            "AST/G": round(ast_pg, 2),
            "STL/G": round(stl_pg, 2),
            "BLK/G": round(blk_pg, 2),
            "TO/G": round(tov_pg, 2),
            "3PM/G": round(pm3_pg, 2),
            "FG%": round(fg_pct * 100, 1),
            "3P%": round(three_pct * 100, 1),
            "FT%": round(ft_pct * 100, 1),
            "TS%": round(ts_pct * 100, 1),
            "scoring_metric": pts_pg + 1.3 * pm3_pg + 0.35 * ftm_pg + 4.0 * ts_pct,
            "rebounding_metric": reb_pg + 0.65 * oreb_pg,
            "playmaking_metric": ast_pg - 0.55 * tov_pg + 0.08 * pts_pg,
            "defense_metric": 1.35 * stl_pg + 1.35 * blk_pg + 0.18 * dreb_pg + 0.55 * gooddef_pg,
            "efficiency_metric": 38 * ts_pct + 7 * fg_pct + 3 * three_pct + 2 * ft_pct - 0.7 * tov_pg,
            "impact_metric": fantasy_like + 0.22 * min_pg + 0.18 * usage_proxy,
        })
    out = pd.DataFrame(rows)
    if out.empty:
        return out

    # Season-relative scaling prevents older/lower-scoring seasons from being unfairly compared directly.
    scaled_frames = []
    for season, g in out.groupby("Season", dropna=False):
        g = g.copy()
        g["Scoring"] = robust_scale(g["scoring_metric"])
        g["Rebounding"] = robust_scale(g["rebounding_metric"])
        g["Playmaking"] = robust_scale(g["playmaking_metric"])
        g["Defense"] = robust_scale(g["defense_metric"])
        g["Efficiency"] = robust_scale(g["efficiency_metric"])
        g["Impact"] = robust_scale(g["impact_metric"])

        season_max_games = max(float(g["Games"].max() or 0.0), 1.0)
        availability = (g["Games"] / season_max_games).clip(0, 1)
        reliability = (g["Games"].clip(0, season_max_games) / max(season_max_games * 0.65, 1)).pow(0.5).clip(0.35, 1.0)
        g["Availability"] = (40 + availability * 59).round(1)
        g["Reliability"] = reliability.round(3)
        scaled_frames.append(g)
    out = pd.concat(scaled_frames, ignore_index=True)

    def weighted_overall(r):
        pos = r.get("Position", "")
        if pos == "Back Court":
            weights = {"Scoring": 0.29, "Rebounding": 0.05, "Playmaking": 0.24, "Defense": 0.15, "Efficiency": 0.17, "Impact": 0.10}
        elif pos == "Front Court":
            weights = {"Scoring": 0.22, "Rebounding": 0.23, "Playmaking": 0.06, "Defense": 0.22, "Efficiency": 0.15, "Impact": 0.12}
        else:
            weights = {"Scoring": 0.25, "Rebounding": 0.15, "Playmaking": 0.17, "Defense": 0.18, "Efficiency": 0.15, "Impact": 0.10}
        raw = sum(float(r[k]) * w for k, w in weights.items())
        # Shrink small-sample players toward 60, then add a light availability signal.
        rel = float(r.get("Reliability", 1.0))
        avail = float(r.get("Availability", 60.0))
        adjusted = 60 + (raw - 60) * rel + (avail - 70) * 0.08
        return max(OVERALL_MIN, min(OVERALL_MAX, adjusted))

    out["Overall"] = out.apply(weighted_overall, axis=1).round().astype(int)
    out["Grade"] = out["Overall"].apply(grade_from_overall)
    for col in ["Scoring", "Rebounding", "Playmaking", "Defense", "Efficiency", "Impact"]:
        out[col] = out[col].round().astype(int)
    out = out.sort_values(["Overall", "Impact", "Player"], ascending=[False, False, True]).reset_index(drop=True)
    out["Rank"] = range(1, len(out) + 1)
    return out


# =========================
# UI components
# =========================
st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=Oswald:wght@500;700&family=Noto+Sans+KR:wght@400;700;900&display=swap');
html, body, [class*="css"] { font-family:'Noto Sans KR', sans-serif; }
.block-container { padding-top: 1.0rem; }
section[data-testid="stSidebar"] { background: linear-gradient(180deg,#020617 0%,#0f172a 60%,#1e293b 100%); }
section[data-testid="stSidebar"] * { color:#f8fafc; }
.header { position:relative; overflow:hidden; border-radius:30px; padding:34px; margin-bottom:22px; color:white; background:linear-gradient(135deg,#020617,#064EA4 55%,#E91E73); box-shadow:0 22px 60px rgba(15,23,42,.22); }
.header:before { content:""; position:absolute; inset:0; background:radial-gradient(circle at 80% 15%,rgba(255,255,255,.22),transparent 28%), linear-gradient(90deg,rgba(2,6,23,.45),rgba(2,6,23,.18)); }
.header > * { position:relative; z-index:1; }
.logo { font-family:'Oswald', sans-serif; font-size:68px; line-height:.92; font-weight:900; letter-spacing:-1px; }
.logo .pink { color:#ff4fa3; display:block; }
.subtitle { margin-top:14px; max-width:680px; color:rgba(255,255,255,.86); font-size:17px; line-height:1.55; font-weight:700; }
.club-strip { display:flex; flex-wrap:wrap; gap:14px; align-items:center; margin-top:24px; }
.club-logo { width:74px; height:74px; display:flex; align-items:center; justify-content:center; padding:7px; border-radius:22px; background:rgba(255,255,255,.92); box-shadow:0 10px 22px rgba(0,0,0,.18); transition:.16s ease; }
.club-logo:hover { transform:translateY(-3px) scale(1.04); box-shadow:0 14px 30px rgba(233,30,115,.30); }
.club-logo img { width:62px; height:62px; object-fit:contain; display:block; }
.club-logo-text { color:#111827; font-weight:900; font-size:11px; text-align:center; }
.section-title { font-family:'Oswald', sans-serif; font-size:40px; font-weight:900; font-style:italic; margin:18px 0 14px; color:#111827; }
.panel { background:white; border:1px solid #e5e7eb; border-radius:22px; padding:20px; box-shadow:0 8px 24px rgba(15,23,42,.08); margin-bottom:18px; }
.panel-title { font-size:20px; font-weight:900; color:#111827; margin-bottom:8px; }
.summary { background:white; border:1px solid #e5e7eb; border-radius:18px; padding:18px; box-shadow:0 8px 24px rgba(15,23,42,.08); min-height:106px; }
.summary-label { font-size:12px; font-weight:900; color:#64748b; letter-spacing:.4px; text-transform:uppercase; }
.summary-value { font-size:32px; font-weight:900; color:#111827; margin-top:6px; }
.card-grid { display:grid; grid-template-columns:repeat(auto-fit,minmax(230px,1fr)); gap:16px; margin-top:12px; }
.player-rating-card { border-radius:26px; overflow:hidden; border:1px solid #e5e7eb; background:#fff; box-shadow:0 14px 36px rgba(15,23,42,.12); }
.card-top { min-height:150px; padding:16px; color:white; background:linear-gradient(135deg,#064EA4,#E91E73); position:relative; }
.card-top.front { background:linear-gradient(135deg,#E91E73,#7c2d12); }
.card-top.back { background:linear-gradient(135deg,#064EA4,#0f172a); }
.ovr { font-family:'Oswald',sans-serif; font-size:64px; font-weight:900; line-height:.9; }
.grade { display:inline-block; border-radius:999px; padding:5px 10px; background:rgba(255,255,255,.18); border:1px solid rgba(255,255,255,.32); font-weight:900; font-size:12px; }
.player-name { font-size:22px; font-weight:900; margin-top:10px; }
.player-meta { color:rgba(255,255,255,.82); font-size:13px; font-weight:800; margin-top:4px; }
.card-body { padding:14px 16px 16px; }
.stat-line { display:flex; justify-content:space-between; gap:12px; font-size:13px; font-weight:800; padding:5px 0; border-bottom:1px solid #f1f5f9; }
.stat-line span:first-child { color:#64748b; }
.badge { display:inline-block; border-radius:999px; padding:5px 9px; font-size:12px; font-weight:900; background:#eff6ff; color:#064EA4; }
</style>
""", unsafe_allow_html=True)


def logo_strip_html():
    items = []
    for team in SIMULATION_TEAMS:
        logo = team_logo_data_url(team)
        link = TEAM_YOUTUBE_LINKS.get(team, "#")
        if logo:
            inner = f'<img src="{logo}" alt="{team}">'
        else:
            inner = f'<div class="club-logo-text">{team}</div>'
        items.append(f'<a class="club-logo" href="{link}" target="_blank" rel="noopener noreferrer" title="{team} YouTube">{inner}</a>')
    return "".join(items)


def render_header():
    bg = asset_data_url(SPLASH_BG_PATH) or asset_data_url(HERO_IMAGE_PATH)
    bg_style = f"background-image:linear-gradient(135deg,rgba(2,6,23,.88),rgba(6,78,164,.70),rgba(233,30,115,.45)),url('{bg}'); background-size:cover; background-position:center;" if bg else ""
    st.markdown(f"""
    <div class="header" style="{bg_style}">
        <div class="logo"><span>WKBL</span><span class="pink">Overall Lab</span></div>
        <div class="subtitle">여러 시즌 선수 기록을 불러와 40~99 오버롤, 세부 능력치, 성장 그래프, 선수 비교를 생성하는 기록 기반 Rating Maker입니다.</div>
        <div class="club-strip">{logo_strip_html()}</div>
    </div>
    """, unsafe_allow_html=True)


def summary_card(label, value, icon="🏀", detail=""):
    st.markdown(f"""
    <div class="summary">
        <div class="summary-label">{icon} {label}</div>
        <div class="summary-value">{value} <span style="font-size:13px;color:#64748b;">{detail}</span></div>
    </div>
    """, unsafe_allow_html=True)


def rating_card_html(row):
    pos_class = "back" if row.get("Position") == "Back Court" else "front" if row.get("Position") == "Front Court" else ""
    team = row.get("Team", "")
    logo = team_logo_data_url(team)
    logo_html = f'<img src="{logo}" style="position:absolute;right:16px;top:16px;width:56px;height:56px;object-fit:contain;background:rgba(255,255,255,.92);border-radius:16px;padding:6px;">' if logo else ""
    return f"""
    <div class="player-rating-card">
        <div class="card-top {pos_class}">
            {logo_html}
            <div class="grade">{row.get('Grade','')}</div>
            <div class="ovr">{int(row.get('Overall',0))}</div>
            <div class="player-name">{row.get('Player','')}</div>
            <div class="player-meta">{row.get('Season','')} · {team} · {row.get('Position','')}</div>
        </div>
        <div class="card-body">
            <div class="stat-line"><span>Scoring</span><b>{int(row.get('Scoring',0))}</b></div>
            <div class="stat-line"><span>Rebounding</span><b>{int(row.get('Rebounding',0))}</b></div>
            <div class="stat-line"><span>Playmaking</span><b>{int(row.get('Playmaking',0))}</b></div>
            <div class="stat-line"><span>Defense</span><b>{int(row.get('Defense',0))}</b></div>
            <div class="stat-line"><span>Efficiency</span><b>{int(row.get('Efficiency',0))}</b></div>
            <div class="stat-line"><span>Impact</span><b>{int(row.get('Impact',0))}</b></div>
        </div>
    </div>
    """


def render_cards(df, limit=12):
    html = '<div class="card-grid">'
    for _, row in df.head(limit).iterrows():
        html += rating_card_html(row)
    html += '</div>'
    st.markdown(html, unsafe_allow_html=True)


def get_rating_df():
    return st.session_state.get("rating_df", pd.DataFrame())


def get_records():
    return st.session_state.get("records", [])


def set_records(records, source_message=""):
    st.session_state.records = records
    st.session_state.source_message = source_message
    mode = st.session_state.get("stat_mode", "자동 판단")
    st.session_state.rating_df = calculate_overall_ratings(records, mode)


def ensure_initial_data():
    if "records" in st.session_state and "rating_df" in st.session_state:
        return
    records = current_players_to_records(DEFAULT_SEASON_LABEL)
    if records:
        set_records(records, f"기본 players_2024_25.csv에서 {len(records)}개 선수 기록을 불러왔습니다.")
    else:
        set_records(sample_records(), "샘플 데이터로 시작했습니다. CSV 또는 WKBL 공식 URL을 불러오면 교체됩니다.")


# =========================
# Pages
# =========================
def page_home():
    render_header()
    df = get_rating_df()
    records = get_records()
    if df.empty:
        st.warning("아직 오버롤을 계산할 데이터가 없습니다. Data Import에서 CSV 또는 WKBL 공식 URL을 불러와 주세요.")
        return
    c1, c2, c3, c4 = st.columns(4)
    with c1:
        summary_card("PLAYER-SEASONS", len(df), "📄")
    with c2:
        summary_card("SEASONS", df["Season"].nunique(), "📅")
    with c3:
        summary_card("TOP OVR", int(df["Overall"].max()), "⭐")
    with c4:
        summary_card("AVG OVR", f"{df['Overall'].mean():.1f}", "📊")

    st.markdown('<div class="section-title">Top Rated Cards</div>', unsafe_allow_html=True)
    render_cards(df, limit=8)

    st.markdown('<div class="section-title">What this homepage does</div>', unsafe_allow_html=True)
    st.markdown("""
    <div class="panel">
        <div class="panel-title">기록을 게임식 능력치로 바꾸는 WKBL Rating Maker</div>
        <div style="color:#475569;line-height:1.8;font-weight:700;">
        이 앱은 시즌별 선수 기록을 그대로 나열하는 대신, 같은 시즌 안에서의 상대적 위치와 포지션 역할을 반영해
        <b>Overall, Scoring, Rebounding, Playmaking, Defense, Efficiency, Impact</b>를 계산합니다.
        CSV 업로드로 여러 시즌을 비교할 수 있고, WKBL 공식 선수기록 URL에서 표를 자동으로 불러오는 기능도 포함했습니다.
        </div>
    </div>
    """, unsafe_allow_html=True)


def page_data_import():
    st.markdown('<div class="section-title">Data Import</div>', unsafe_allow_html=True)
    st.info("시뮬레이션 기능은 제거했습니다. 이 앱은 선수 기록을 불러와 오버롤을 계산하는 전용 홈페이지입니다.")

    mode = st.radio("데이터 소스", ["기본 CSV", "CSV 업로드", "WKBL 공식 URL"], horizontal=True)
    if mode == "기본 CSV":
        season = st.text_input("시즌 라벨", value=DEFAULT_SEASON_LABEL, key="default_season_label")
        if st.button("players_2024_25.csv 불러오기", use_container_width=True):
            records = current_players_to_records(season)
            if records:
                set_records(records, f"기본 CSV에서 {len(records)}개 선수 기록을 불러왔습니다.")
                st.success(st.session_state.source_message)
            else:
                st.error("players_2024_25.csv를 찾지 못했습니다. CSV 업로드를 사용해 주세요.")
    elif mode == "CSV 업로드":
        st.caption("권장 컬럼: season, name, team, position, games, minutes, pts, reb, oreb, dreb, ast, stl, blk, to, 2pm, 2pa, 3pm, 3pa, ftm, fta")
        uploaded_files = st.file_uploader("여러 시즌 CSV 업로드", type=["csv"], accept_multiple_files=True)
        season_fallback = st.text_input("season 컬럼이 없을 때 사용할 기본 시즌 라벨", value="Uploaded")
        if uploaded_files and st.button("업로드 CSV로 오버롤 계산", use_container_width=True):
            all_records = []
            previews = []
            for f in uploaded_files:
                try:
                    df, enc = read_csv_with_fallback(f)
                    all_records.extend(normalize_source_dataframe(df, season_label=season_fallback, source_label=f"uploaded:{f.name}"))
                    previews.append((f.name, enc, df.head(10)))
                except Exception as e:
                    st.error(f"{f.name} 읽기 실패: {e}")
            records = merge_records(all_records)
            if records:
                set_records(records, f"업로드 CSV에서 {len(records)}개 선수/시즌 기록을 인식했습니다.")
                st.success(st.session_state.source_message)
                with st.expander("업로드 미리보기", expanded=False):
                    for name, enc, preview in previews:
                        st.write(f"**{name}** · encoding: {enc}")
                        st.dataframe(preview, use_container_width=True)
            else:
                st.warning("인식된 선수 기록이 없습니다. 컬럼명을 확인해 주세요.")
    else:
        st.caption("공식 선수기록 페이지 또는 부문별 선수순위 URL을 줄 단위로 넣으세요. wkbl.or.kr 도메인만 허용됩니다.")
        season_label = st.text_input("시즌 라벨", value="2025-26", key="url_season_label")
        default_urls = "\n".join([
            "https://www.wkbl.or.kr/game/player_record.asp?part=score&season_gu=046",
            "https://www.wkbl.or.kr/game/player_record.asp?part=rebound&season_gu=046",
            "https://www.wkbl.or.kr/game/player_record.asp?part=assist&season_gu=046",
            "https://www.wkbl.or.kr/game/player_record.asp?part=steal&season_gu=046",
            "https://www.wkbl.or.kr/game/player_record.asp?part=block&season_gu=046",
            "https://www.wkbl.or.kr/game/player_record.asp?part=threepoint&season_gu=046",
        ])
        url_text = st.text_area("WKBL 공식 URL 목록", value=default_urls, height=180)
        if st.button("공식 페이지에서 기록 불러오기", use_container_width=True):
            urls = [line.strip() for line in url_text.splitlines() if line.strip()]
            with st.spinner("WKBL 공식 페이지에서 표를 읽는 중..."):
                records, messages = load_records_from_urls(urls, season_label)
            st.session_state.fetch_messages = messages
            if records:
                set_records(records, f"공식 URL에서 {len(records)}개 선수/시즌 기록을 인식했습니다.")
                st.success(st.session_state.source_message)
            else:
                st.warning("공식 페이지에서 인식된 기록이 없습니다. 사이트 구조가 바뀐 경우 CSV 업로드 방식을 사용해 주세요.")
        if st.session_state.get("fetch_messages"):
            with st.expander("불러오기 로그", expanded=True):
                for msg in st.session_state.fetch_messages:
                    st.write("- " + msg)

    st.divider()
    st.markdown("### 현재 데이터 상태")
    df = get_rating_df()
    if df.empty:
        st.warning("현재 계산된 데이터가 없습니다.")
    else:
        st.write(st.session_state.get("source_message", ""))
        st.dataframe(df[["Rank", "Season", "Player", "Team", "Position", "Games", "Overall", "Grade"]].head(50), use_container_width=True, hide_index=True)


def page_overall_lab():
    st.markdown('<div class="section-title">Overall Lab</div>', unsafe_allow_html=True)
    records = get_records()
    if not records:
        st.warning("먼저 Data Import에서 데이터를 불러와 주세요.")
        return
    current_mode = st.session_state.get("stat_mode", "자동 판단")
    new_mode = st.selectbox("기록 단위 처리", ["자동 판단", "누적 기록", "경기당 평균"], index=["자동 판단", "누적 기록", "경기당 평균"].index(current_mode))
    if new_mode != current_mode:
        st.session_state.stat_mode = new_mode
        st.session_state.rating_df = calculate_overall_ratings(records, new_mode)
        st.rerun()

    df = get_rating_df()
    c1, c2, c3 = st.columns(3)
    with c1:
        seasons = ["All"] + sorted(df["Season"].dropna().unique().tolist())
        season_filter = st.selectbox("시즌", seasons)
    base = df if season_filter == "All" else df[df["Season"] == season_filter]
    with c2:
        teams = ["All"] + sorted([x for x in base["Team"].dropna().unique().tolist() if clean(x)])
        team_filter = st.selectbox("팀", teams)
    with c3:
        positions = ["All"] + sorted([x for x in base["Position"].dropna().unique().tolist() if clean(x)])
        pos_filter = st.selectbox("포지션", positions)

    filtered = df.copy()
    if season_filter != "All":
        filtered = filtered[filtered["Season"] == season_filter]
    if team_filter != "All":
        filtered = filtered[filtered["Team"] == team_filter]
    if pos_filter != "All":
        filtered = filtered[filtered["Position"] == pos_filter]

    st.markdown("### Ranking Table")
    display_cols = ["Rank", "Season", "Player", "Team", "Position", "Games", "MIN/G", "PTS/G", "REB/G", "AST/G", "Overall", "Grade", "Scoring", "Rebounding", "Playmaking", "Defense", "Efficiency", "Impact"]
    st.dataframe(filtered[display_cols], use_container_width=True, hide_index=True)

    st.markdown("### Rating Cards")
    render_cards(filtered, limit=12)


def page_player_cards():
    st.markdown('<div class="section-title">Player Cards</div>', unsafe_allow_html=True)
    df = get_rating_df()
    if df.empty:
        st.warning("먼저 데이터를 불러와 주세요.")
        return
    search = st.text_input("선수명 검색", value="")
    filtered = df.copy()
    if search.strip():
        filtered = filtered[filtered["Player"].str.contains(search.strip(), case=False, na=False)]
    count = st.slider("표시할 카드 수", min_value=4, max_value=40, value=16, step=4)
    render_cards(filtered, limit=count)


def page_compare():
    st.markdown('<div class="section-title">Player Compare</div>', unsafe_allow_html=True)
    df = get_rating_df()
    if df.empty:
        st.warning("먼저 데이터를 불러와 주세요.")
        return
    df = df.copy()
    df["Label"] = df["Player"] + " · " + df["Season"] + " · " + df["Team"].fillna("")
    labels = df["Label"].tolist()
    c1, c2 = st.columns(2)
    with c1:
        left_label = st.selectbox("선수 A", labels, index=0)
    with c2:
        right_label = st.selectbox("선수 B", labels, index=min(1, len(labels)-1))
    left = df[df["Label"] == left_label].iloc[0]
    right = df[df["Label"] == right_label].iloc[0]
    c1, c2 = st.columns(2)
    with c1:
        st.markdown(rating_card_html(left), unsafe_allow_html=True)
    with c2:
        st.markdown(rating_card_html(right), unsafe_allow_html=True)

    compare_cols = ["Overall", "Scoring", "Rebounding", "Playmaking", "Defense", "Efficiency", "Impact", "PTS/G", "REB/G", "AST/G", "STL/G", "BLK/G", "TS%"]
    comp = pd.DataFrame({
        "Metric": compare_cols,
        left["Player"]: [left[c] for c in compare_cols],
        right["Player"]: [right[c] for c in compare_cols],
        "Diff(A-B)": [round(float(left[c]) - float(right[c]), 2) for c in compare_cols],
    })
    st.markdown("### Detailed Comparison")
    st.dataframe(comp, use_container_width=True, hide_index=True)


def page_growth():
    st.markdown('<div class="section-title">Growth Graph</div>', unsafe_allow_html=True)
    df = get_rating_df()
    if df.empty:
        st.warning("먼저 데이터를 불러와 주세요.")
        return
    labels_df = df.assign(Label=df["Player"] + " (" + df["Team"].fillna("") + ")")[["Player", "Team", "Label"]].drop_duplicates().sort_values("Label")
    selected = st.selectbox("선수 선택", labels_df["Label"].tolist())
    row = labels_df[labels_df["Label"] == selected].iloc[0]
    p_df = df[(df["Player"] == row["Player"]) & (df["Team"] == row["Team"])].sort_values("Season")
    if len(p_df) == 1:
        st.info("현재 이 선수는 1개 시즌 기록만 있습니다. 여러 시즌 CSV를 넣으면 성장 그래프가 더 의미 있게 표시됩니다.")
    chart_cols = ["Overall", "Scoring", "Rebounding", "Playmaking", "Defense", "Efficiency", "Impact"]
    chart_df = p_df[["Season"] + chart_cols].set_index("Season")
    st.line_chart(chart_df, use_container_width=True)
    st.dataframe(p_df[["Season", "Player", "Team", "Games", "Overall", "Grade"] + chart_cols], use_container_width=True, hide_index=True)


def page_rankings():
    st.markdown('<div class="section-title">Season Rankings</div>', unsafe_allow_html=True)
    df = get_rating_df()
    if df.empty:
        st.warning("먼저 데이터를 불러와 주세요.")
        return
    metric = st.selectbox("랭킹 기준", ["Overall", "Scoring", "Rebounding", "Playmaking", "Defense", "Efficiency", "Impact", "PTS/G", "REB/G", "AST/G", "TS%"])
    season = st.selectbox("시즌", ["All"] + sorted(df["Season"].unique().tolist()))
    base = df if season == "All" else df[df["Season"] == season]
    top_n = st.slider("Top N", 5, 50, 20)
    rank = base.sort_values(metric, ascending=False).head(top_n).copy()
    rank["Metric Value"] = rank[metric]
    st.dataframe(rank[["Season", "Player", "Team", "Position", "Games", "Metric Value", "Overall", "Grade"]], use_container_width=True, hide_index=True)
    chart = rank.set_index("Player")[[metric]]
    st.bar_chart(chart, use_container_width=True)


def page_model():
    st.markdown('<div class="section-title">About Model</div>', unsafe_allow_html=True)
    st.markdown("""
    <div class="panel">
        <div class="panel-title">오버롤 산정 원리</div>
        <div style="color:#475569;line-height:1.85;font-weight:700;">
        이 모델은 공식 WKBL 지표가 아니라, 입력된 선수 기록을 게임식 능력치로 바꾸기 위한 커스텀 모델입니다.
        단순 누적 득점 순위가 아니라 <b>시즌 내부 정규화</b>, <b>포지션별 역할 가중치</b>, <b>출전 경기 수 신뢰도 보정</b>을 반영합니다.
        </div>
    </div>
    """, unsafe_allow_html=True)
    st.markdown("""
    #### 1. 기록 단위 변환
    누적 기록 CSV는 경기 수로 나누어 경기당 기록으로 바꾸고, 이미 경기당 평균인 표는 그대로 사용할 수 있습니다. 자동 판단 모드는 값의 크기를 보고 누적/평균을 추정합니다.

    #### 2. 시즌별 robust scaling
    같은 시즌 안에서 각 지표의 5~95% 분위수를 기준으로 40~99 범위로 변환합니다. 그래서 다른 시즌의 리그 환경 차이가 어느 정도 줄어듭니다.

    #### 3. 포지션별 가중치
    Back Court는 득점·플레이메이킹 비중을 더 크게 보고, Front Court는 리바운드·수비 비중을 더 크게 봅니다.

    #### 4. 표본 수 보정
    출전 경기가 적은 선수는 극단적인 기록이 바로 높은 오버롤로 이어지지 않도록 60점 근처로 일부 수축합니다.

    #### 5. 등급 기준
    - 95~99: MVP
    - 90~94: STAR
    - 85~89: ELITE
    - 75~84: STARTER
    - 65~74: ROTATION
    - 55~64: DEVELOPING
    - 40~54: PROSPECT
    """)


def sidebar_nav():
    with st.sidebar:
        st.markdown("## 🏀 WKBL Overall Lab")
        st.caption(APP_VERSION)
        st.divider()
        page = st.radio(
            "메뉴",
            ["Home", "Data Import", "Overall Lab", "Player Cards", "Player Compare", "Growth Graph", "Season Rankings", "About Model"],
            label_visibility="collapsed",
        )
        st.divider()
        df = get_rating_df()
        if not df.empty:
            st.metric("Top Overall", int(df["Overall"].max()))
            st.metric("Player-Seasons", len(df))
        return page


ensure_initial_data()
page = sidebar_nav()

if page == "Home":
    page_home()
elif page == "Data Import":
    page_data_import()
elif page == "Overall Lab":
    page_overall_lab()
elif page == "Player Cards":
    page_player_cards()
elif page == "Player Compare":
    page_compare()
elif page == "Growth Graph":
    page_growth()
elif page == "Season Rankings":
    page_rankings()
elif page == "About Model":
    page_model()

st.markdown("<div style='height:24px'></div>", unsafe_allow_html=True)
st.caption("WKBL Overall Lab is a fan-made analytical rating tool. It does not provide official WKBL ratings.")
