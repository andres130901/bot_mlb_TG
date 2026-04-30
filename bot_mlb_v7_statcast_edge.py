import os
import csv
import json
import math
import time
import requests
import telebot
from dotenv import load_dotenv
from datetime import datetime, date, timedelta
from functools import lru_cache
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton

try:
    from zoneinfo import ZoneInfo
except ImportError:
    ZoneInfo = None

# =========================================================
# PYBASEBALL / STATCAST EDGE OPCIONAL
# =========================================================
# El bot NO se cae si pybaseball no está instalado.
# Para activar la capa avanzada instala: pip install pybaseball pandas
try:
    import pandas as pd
    from pybaseball import cache as pybaseball_cache
    from pybaseball import statcast_pitcher
    PYBASEBALL_AVAILABLE = True
    try:
        pybaseball_cache.enable()
    except Exception:
        pass
except Exception:
    pd = None
    statcast_pitcher = None
    PYBASEBALL_AVAILABLE = False

# =========================================================
# VERSION
# =========================================================
BOT_VERSION = "V7_STATCAST_EDGE"

# =========================================================
# CONFIG
# =========================================================
load_dotenv()

TOKEN = os.getenv("TOKEN")
ODDS_API_KEY = os.getenv("ODDS_API_KEY", "").strip()

if not TOKEN:
    raise ValueError("Falta TOKEN en tu archivo .env")

bot = telebot.TeleBot(TOKEN, parse_mode=None)

MLB_BASE = "https://statsapi.mlb.com/api/v1"
HISTORIAL_FILE = "historial_parlays.json"
RESULTADOS_CSV = "resultados_apuestas.csv"
PARLEYS_DIARIOS_FILE = "parleys_diarios.json"
REQUEST_TIMEOUT = 20
ANALISIS_TTL_SEGUNDOS = 900
ODDS_TTL_SEGUNDOS = 300

# V7_STATCAST_EDGE
# Puedes apagar la capa avanzada con USE_STATCAST_EDGE=0 en el .env.
USE_STATCAST_EDGE = os.getenv("USE_STATCAST_EDGE", "1").strip() != "0"
try:
    STATCAST_DAYS_LOOKBACK = int(os.getenv("STATCAST_DAYS_LOOKBACK", "21"))
except Exception:
    STATCAST_DAYS_LOOKBACK = 21
STATCAST_MIN_PITCHES = 80

# Estadios cerrados o con techo retráctil. En estos casos se ignora clima.
VENUE_IDS_TECHO = {
    12,     # Tropicana Field
    2392,   # Globe Life Field
    2394,   # Rogers Centre
    680,    # Minute Maid Park
    3289,   # loanDepot park
    32,     # Chase Field
    2395,   # American Family Field
}

BOOKMAKERS_PRIORITARIOS = [
    "DraftKings",
    "FanDuel",
    "BetMGM",
    "Caesars",
    "PointsBet",
    "BetRivers",
    "Unibet",
    "William Hill",
]

# =========================================================
# PERSISTENCIA
# =========================================================
def cargar_historial():
    if not os.path.exists(HISTORIAL_FILE):
        return []
    try:
        with open(HISTORIAL_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            return data if isinstance(data, list) else []
    except Exception as e:
        print(f"[ERROR] cargar_historial: {e}")
        return []


def guardar_historial(historial):
    try:
        with open(HISTORIAL_FILE, "w", encoding="utf-8") as f:
            json.dump(historial, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"[ERROR] guardar_historial: {e}")


def inicializar_csv_resultados():
    if os.path.exists(RESULTADOS_CSV):
        return
    try:
        with open(RESULTADOS_CSV, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(
                [
                    "fecha",
                    "juego",
                    "tipo_apuesta",
                    "pick",
                    "cuota",
                    "prob_modelo",
                    "prob_implicita",
                    "edge",
                    "stake",
                    "grade",
                    "resultado",
                    "profit",
                ]
            )
    except Exception as e:
        print(f"[ERROR] inicializar_csv_resultados: {e}")


historial_parlays = cargar_historial()
inicializar_csv_resultados()


def cargar_parleys_diarios():
    if not os.path.exists(PARLEYS_DIARIOS_FILE):
        return []
    try:
        with open(PARLEYS_DIARIOS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            return data if isinstance(data, list) else []
    except Exception as e:
        print(f"[ERROR] cargar_parleys_diarios: {e}")
        return []


def guardar_parleys_diarios(data):
    try:
        with open(PARLEYS_DIARIOS_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"[ERROR] guardar_parleys_diarios: {e}")


def buscar_parley_del_dia(tipo, fecha=None):
    if fecha is None:
        fecha = hoy_str()
    for p in cargar_parleys_diarios():
        if p.get("fecha") == fecha and p.get("tipo") == tipo:
            return p
    return None


def registrar_parley_del_dia(tipo, legs, cuota_total=None, nivel="N/D", fecha=None):
    if fecha is None:
        fecha = hoy_str()
    existente = buscar_parley_del_dia(tipo, fecha)
    if existente:
        return existente

    data = cargar_parleys_diarios()
    nuevo = {
        "fecha": fecha,
        "tipo": tipo,
        "estado": "pendiente",
        "nivel": nivel,
        "cuota_total": cuota_total,
        "legs": legs,
    }
    data.append(nuevo)
    guardar_parleys_diarios(data)
    return nuevo


def actualizar_estado_parley(fecha, tipo, estado):
    data = cargar_parleys_diarios()
    actualizado = False
    for p in data:
        if p.get("fecha") == fecha and p.get("tipo") == tipo:
            p["estado"] = estado
            actualizado = True
            break
    if actualizado:
        guardar_parleys_diarios(data)
    return actualizado


def eliminar_parley_del_dia(tipo, fecha=None):
    if fecha is None:
        fecha = hoy_str()
    data = cargar_parleys_diarios()
    nuevo = [p for p in data if not (p.get("fecha") == fecha and p.get("tipo") == tipo)]
    borrado = len(nuevo) < len(data)
    if borrado:
        guardar_parleys_diarios(nuevo)
    return borrado

# =========================================================
# ESTILO VISUAL
# =========================================================
def header(title, icon="⚾"):
    return f"{icon} <b>{title}</b>\n━━━━━━━━━━━━━━━━━━\n\n"


def divider():
    return "──────────────────\n"


def card_game(title, lines):
    texto = f"⚾ <b>{title}</b>\n"
    for line in lines:
        texto += f"{line}\n"
    texto += divider() + "\n"
    return texto


def menu_markup():
    markup = InlineKeyboardMarkup(row_width=2)
    markup.add(
        InlineKeyboardButton("📅 Hoy", callback_data="cmd_hoy"),
        InlineKeyboardButton("🏆 Posiciones", callback_data="cmd_posiciones"),
        InlineKeyboardButton("💰 Apuestas", callback_data="cmd_apuestas"),
        InlineKeyboardButton("🎯 Parley", callback_data="cmd_parley"),
        InlineKeyboardButton("💎 Parley Mill.", callback_data="cmd_parley_millonario"),
        InlineKeyboardButton("🧢 Pitchers", callback_data="cmd_pitchers"),
        InlineKeyboardButton("📊 Pronósticos", callback_data="cmd_pronosticos"),
        InlineKeyboardButton("🚨 Lesionados", callback_data="cmd_lesionados"),
        InlineKeyboardButton("📈 ROI", callback_data="cmd_roi"),
        InlineKeyboardButton("📦 Exportar JSON", callback_data="cmd_exportar_json"),
        InlineKeyboardButton("📊 Stats Parlays", callback_data="cmd_stats_parlays"),
        InlineKeyboardButton("✅ Parley G", callback_data="cmd_parley_ganado"),
        InlineKeyboardButton("❌ Parley F", callback_data="cmd_parley_fallado"),
        InlineKeyboardButton("💎✅ Mill G", callback_data="cmd_millonario_ganado"),
        InlineKeyboardButton("💎❌ Mill F", callback_data="cmd_millonario_fallado"),
        InlineKeyboardButton("♻️ Reset Parley", callback_data="cmd_reset_parley"),
        InlineKeyboardButton("♻️ Reset Mill", callback_data="cmd_reset_millonario"),
    )
    return markup

# =========================================================
# UTILIDADES
# =========================================================
def safe_get(url, params=None, timeout=REQUEST_TIMEOUT):
    try:
        r = requests.get(url, params=params, timeout=timeout)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        print(f"[WARN] GET {url}: {e}")
        return {}


def temporada_actual():
    return date.today().year


def hoy_str():
    return date.today().strftime("%Y-%m-%d")


def clamp(value, low, high):
    return max(low, min(high, value))


def logistic(x):
    return 1 / (1 + math.exp(-x))


def dividir_mensaje(texto, max_len=3900):
    partes = []
    while len(texto) > max_len:
        corte = texto.rfind("\n", 0, max_len)
        if corte == -1:
            corte = max_len
        partes.append(texto[:corte])
        texto = texto[corte:].lstrip()
    if texto:
        partes.append(texto)
    return partes


def responder_largo(chat_id, texto, parse_mode=None, reply_markup=None):
    partes = dividir_mensaje(texto)
    for i, parte in enumerate(partes):
        if i == 0 and reply_markup is not None:
            bot.send_message(chat_id, parte, parse_mode=parse_mode, reply_markup=reply_markup)
        else:
            bot.send_message(chat_id, parte, parse_mode=parse_mode)


def parse_streak(streak_code):
    if not streak_code:
        return 0.0
    s = str(streak_code).upper().strip()
    try:
        if s.startswith("W"):
            return min(int(s[1:]) * 0.01, 0.05)
        if s.startswith("L"):
            return max(int(s[1:]) * -0.01, -0.05)
    except Exception:
        pass
    return 0.0


def confidence_label(prob):
    if prob >= 0.62:
        return "Alta"
    if prob >= 0.56:
        return "Media"
    return "Baja"


def moneyline_to_prob(moneyline):
    try:
        ml = int(moneyline)
        if ml > 0:
            return 100 / (ml + 100)
        return abs(ml) / (abs(ml) + 100)
    except Exception:
        return None


def american_to_decimal(american_odds):
    try:
        ml = float(american_odds)
        if ml > 0:
            return 1 + ml / 100
        return 1 + 100 / abs(ml)
    except Exception:
        return None


def calcular_ev(prob_model, american_odds):
    dec = american_to_decimal(american_odds)
    if dec is None:
        return None
    try:
        p = float(prob_model)
        return round(p * (dec - 1) - (1 - p), 4)
    except Exception:
        return None


def grade_por_ev(ev):
    if ev is None:
        return "D"
    if ev >= 0.08:
        return "A+"
    if ev >= 0.05:
        return "A"
    if ev >= 0.03:
        return "B"
    if ev >= 0.015:
        return "C"
    return "D"


def stake_por_ev(ev):
    if ev is None:
        return "0u"
    if ev >= 0.08:
        return "1.5u"
    if ev >= 0.05:
        return "1.0u"
    if ev >= 0.03:
        return "0.75u"
    if ev >= 0.015:
        return "0.5u"
    return "0u"


def extraer_unidades(stake_texto):
    try:
        return float(str(stake_texto).lower().replace("u", "").strip())
    except Exception:
        return 0.0


def safe_float(value, default=None):
    try:
        return float(value) if value is not None else default
    except Exception:
        return default


def safe_int(value, default=None):
    try:
        return int(value) if value is not None else default
    except Exception:
        return default


def abreviar_equipo(nombre):
    reemplazos = {
        "New York Yankees": "Yankees",
        "Boston Red Sox": "Red Sox",
        "Toronto Blue Jays": "Blue Jays",
        "Tampa Bay Rays": "Rays",
        "Baltimore Orioles": "Orioles",
        "Cleveland Guardians": "Guardians",
        "Chicago White Sox": "White Sox",
        "Kansas City Royals": "Royals",
        "Minnesota Twins": "Twins",
        "Detroit Tigers": "Tigers",
        "Houston Astros": "Astros",
        "Seattle Mariners": "Mariners",
        "Texas Rangers": "Rangers",
        "Los Angeles Angels": "Angels",
        "Athletics": "Athletics",
        "Philadelphia Phillies": "Phillies",
        "Atlanta Braves": "Braves",
        "New York Mets": "Mets",
        "Miami Marlins": "Marlins",
        "Washington Nationals": "Nationals",
        "Chicago Cubs": "Cubs",
        "Milwaukee Brewers": "Brewers",
        "St. Louis Cardinals": "Cardinals",
        "Cincinnati Reds": "Reds",
        "Pittsburgh Pirates": "Pirates",
        "Los Angeles Dodgers": "Dodgers",
        "San Diego Padres": "Padres",
        "San Francisco Giants": "Giants",
        "Arizona Diamondbacks": "D-backs",
        "Colorado Rockies": "Rockies",
    }
    return reemplazos.get(nombre, nombre)


def normalizar_matchup(away_team, home_team):
    away = (away_team or "").strip().lower()
    home = (home_team or "").strip().lower()
    return f"{away} @ {home}"


def _clave_desde_game(game_str):
    if " @ " in game_str:
        away, home = game_str.split(" @ ", 1)
        return normalizar_matchup(away, home)
    return str(game_str).strip().lower()


def filtrar_matchups_unicos(items):
    vistos = set()
    filtrados = []
    for item in items:
        clave = item.get("matchup_key") or _clave_desde_game(item.get("game", ""))
        if clave in vistos:
            continue
        vistos.add(clave)
        filtrados.append(item)
    return filtrados


def filtrar_candidatos_millonario(items):
    vistos = set()
    filtrados = []
    for item in items:
        mk = item.get("matchup_key") or _clave_desde_game(item.get("game", ""))
        tipo = item.get("tipo", "ML")
        clave = (mk, tipo)
        if clave in vistos:
            continue
        vistos.add(clave)
        filtrados.append(item)
    return filtrados


def obtener_carpeta_exportacion():
    carpeta = os.path.join("exports_tiktok", hoy_str())
    os.makedirs(carpeta, exist_ok=True)
    return carpeta


def guardar_json_tiktok(data):
    carpeta = obtener_carpeta_exportacion()
    ruta = os.path.join(carpeta, "mlb_contenido.json")
    try:
        with open(ruta, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        return ruta
    except Exception as e:
        print(f"[ERROR] guardar_json_tiktok: {e}")
        return None

# =========================================================
# MLB DATA
# =========================================================
def _extraer_split(team_record, tipo):
    for sr in team_record.get("splitRecords", []) or []:
        if sr.get("type") == tipo:
            return safe_int(sr.get("wins"), 0), safe_int(sr.get("losses"), 0)
    return None, None


def obtener_standings():
    url = f"{MLB_BASE}/standings"
    params = {
        "leagueId": "103,104",
        "season": temporada_actual(),
        "standingsTypes": "regularSeason",
        "hydrate": "team,league,division",
    }
    data = safe_get(url, params=params)
    equipos = {}

    for record in data.get("records", []):
        for t in record.get("teamRecords", []):
            name = t.get("team", {}).get("name")
            if not name:
                continue

            wins = safe_int(t.get("wins"), 0)
            losses = safe_int(t.get("losses"), 0)
            games = max(wins + losses, 1)

            hw, hl = _extraer_split(t, "home")
            aw, al = _extraer_split(t, "away")
            lw, ll = _extraer_split(t, "lastTen")

            if hw is None:
                hw = safe_int(t.get("homeWins"), wins // 2)
                hl = safe_int(t.get("homeLosses"), losses // 2)
            if aw is None:
                aw = safe_int(t.get("awayWins"), wins - (hw or 0))
                al = safe_int(t.get("awayLosses"), losses - (hl or 0))
            if lw is None:
                lw = safe_int(t.get("lastTenWins"), 5)
                ll = safe_int(t.get("lastTenLosses"), 5)

            home_games = max((hw or 0) + (hl or 0), 1)
            away_games = max((aw or 0) + (al or 0), 1)
            last10_games = max((lw or 0) + (ll or 0), 1)

            rs = safe_int(t.get("runsScored"), 0)
            ra = safe_int(t.get("runsAllowed"), 0)
            if rs == 0 and ra == 0 and games > 5:
                rs_pg = 4.50
                ra_pg = 4.50
            else:
                rs_pg = rs / games
                ra_pg = ra / games

            equipos[name] = {
                "wins": wins,
                "losses": losses,
                "win_pct": wins / games,
                "home_win_pct": (hw or 0) / home_games,
                "away_win_pct": (aw or 0) / away_games,
                "run_diff": rs - ra,
                "runs_scored": rs_pg,
                "runs_allowed": ra_pg,
                "last10_win_pct": (lw or 0) / last10_games,
                "streak": t.get("streakCode", ""),
                "last10_record": f"{lw}-{ll}",
            }

    return equipos


def obtener_juegos_del_dia():
    url = f"{MLB_BASE}/schedule"
    params = {"sportId": 1, "date": hoy_str(), "hydrate": "probablePitcher,venue"}
    data = safe_get(url, params=params)
    dates = data.get("dates", [])
    if not dates or not isinstance(dates, list) or not isinstance(dates[0], dict):
        return []
    return dates[0].get("games", [])


def obtener_transacciones_hoy():
    url = f"{MLB_BASE}/transactions"
    params = {
        "startDate": f"{temporada_actual()}-03-01",
        "endDate": hoy_str(),
        "sportId": 1,
    }
    data = safe_get(url, params=params)
    return data.get("transactions", [])


@lru_cache(maxsize=256)
def obtener_stats_pitcher_reales(person_id, season=None):
    base = {
        "era": 4.20,
        "whip": 1.30,
        "so9": 8.2,
        "bb9": 3.2,
        "hr9": 1.2,
        "fip": 4.20,
        "ip": 0.0,
        "sample_ok": False,
    }
    if not person_id:
        return base
    if season is None:
        season = temporada_actual()

    url = f"{MLB_BASE}/people/{person_id}/stats"
    params = {"stats": "season", "group": "pitching", "season": season, "gameType": "R"}
    data = safe_get(url, params=params)

    stats_list = data.get("stats", [])
    if not stats_list or not isinstance(stats_list[0], dict):
        return base

    splits = stats_list[0].get("splits", [])
    if not splits or not isinstance(splits[0], dict):
        return base

    stat = splits[0].get("stat", {})

    try:
        era = float(stat.get("era", 4.20) or 4.20)
    except Exception:
        era = 4.20
    try:
        whip = float(stat.get("whip", 1.30) or 1.30)
    except Exception:
        whip = 1.30
    try:
        ip = float(str(stat.get("inningsPitched", "0")).replace(",", ""))
    except Exception:
        ip = 0.0

    k = safe_int(stat.get("strikeOuts"), 0)
    bb = safe_int(stat.get("baseOnBalls"), 0)
    hr = safe_int(stat.get("homeRuns"), 0)

    so9 = (k * 9 / ip) if ip > 0 else 8.2
    bb9 = (bb * 9 / ip) if ip > 0 else 3.2
    hr9 = (hr * 9 / ip) if ip > 0 else 1.2
    fip_raw = ((13 * hr + 3 * bb - 2 * k) / ip + 3.20) if ip > 0 else 4.20
    fip = round(clamp(fip_raw, 1.50, 7.50), 2)

    return {
        "era": round(era, 2),
        "whip": round(whip, 2),
        "so9": round(so9, 2),
        "bb9": round(bb9, 2),
        "hr9": round(hr9, 2),
        "fip": fip,
        "ip": round(ip, 1),
        "sample_ok": ip >= 30,
    }



# =========================================================
# V7 STATCAST EDGE
# =========================================================
def _statcast_default():
    return {
        "statcast_ok": False,
        "pitches": 0,
        "xwoba_allowed": None,
        "avg_ev_allowed": None,
        "hard_hit_pct": None,
        "barrel_proxy_pct": None,
        "whiff_pct": None,
        "k_pitch_pct": None,
        "sample_level": "sin_statcast",
        "edge_note": "Statcast no disponible",
    }


def _mean_numeric(df, col):
    try:
        if df is None or col not in df.columns:
            return None
        serie = pd.to_numeric(df[col], errors="coerce").dropna()
        if len(serie) == 0:
            return None
        return float(serie.mean())
    except Exception:
        return None


@lru_cache(maxsize=256)
def obtener_statcast_pitcher_edge(person_id, pitcher_name="", days=None):
    """
    Capa avanzada opcional.
    Usa pybaseball.statcast_pitcher para medir calidad real de contacto permitido:
    xwOBA, exit velocity, hard-hit, barrel proxy y whiff%.
    Si pybaseball no está instalado o falla la descarga, devuelve fallback seguro.
    """
    base = _statcast_default()
    if not USE_STATCAST_EDGE or not PYBASEBALL_AVAILABLE or not person_id or statcast_pitcher is None:
        return base

    try:
        if days is None:
            days = STATCAST_DAYS_LOOKBACK
        end_dt = date.today()
        start_dt = end_dt - timedelta(days=max(7, int(days)))

        df = statcast_pitcher(
            start_dt.strftime("%Y-%m-%d"),
            end_dt.strftime("%Y-%m-%d"),
            int(person_id),
        )

        if df is None or len(df) == 0:
            return {**base, "edge_note": "Sin muestra Statcast reciente"}

        pitches = int(len(df))
        xwoba = _mean_numeric(df, "estimated_woba_using_speedangle")
        avg_ev = _mean_numeric(df, "launch_speed")

        hard_hit_pct = None
        barrel_proxy_pct = None
        try:
            batted = df[pd.to_numeric(df.get("launch_speed"), errors="coerce").notna()].copy()
            if len(batted) > 0:
                ev = pd.to_numeric(batted["launch_speed"], errors="coerce")
                hard_hit_pct = float((ev >= 95).mean() * 100)
                if "launch_angle" in batted.columns:
                    la = pd.to_numeric(batted["launch_angle"], errors="coerce")
                    barrel_proxy_pct = float(((ev >= 98) & (la >= 26) & (la <= 30)).mean() * 100)
        except Exception:
            pass

        whiff_pct = None
        k_pitch_pct = None
        try:
            desc = df.get("description")
            if desc is not None:
                desc = desc.astype(str).str.lower()
                swings = desc.str.contains("swing|foul|hit_into_play", regex=True, na=False)
                whiffs = desc.str.contains("swinging_strike|missed_bunt", regex=True, na=False)
                if swings.sum() > 0:
                    whiff_pct = float(whiffs.sum() / swings.sum() * 100)
                k_pitch_pct = float(desc.str.contains("strike", na=False).mean() * 100)
        except Exception:
            pass

        if pitches >= STATCAST_MIN_PITCHES:
            sample_level = "ok"
        elif pitches >= 35:
            sample_level = "pequena"
        else:
            sample_level = "muy_pequena"

        return {
            "statcast_ok": pitches >= 35,
            "pitches": pitches,
            "xwoba_allowed": round(xwoba, 3) if xwoba is not None else None,
            "avg_ev_allowed": round(avg_ev, 1) if avg_ev is not None else None,
            "hard_hit_pct": round(hard_hit_pct, 1) if hard_hit_pct is not None else None,
            "barrel_proxy_pct": round(barrel_proxy_pct, 1) if barrel_proxy_pct is not None else None,
            "whiff_pct": round(whiff_pct, 1) if whiff_pct is not None else None,
            "k_pitch_pct": round(k_pitch_pct, 1) if k_pitch_pct is not None else None,
            "sample_level": sample_level,
            "edge_note": "Statcast activo",
        }
    except Exception as e:
        print(f"[WARN] obtener_statcast_pitcher_edge {pitcher_name or person_id}: {e}")
        return {**base, "edge_note": "Statcast falló, usando MLB StatsAPI"}


def combinar_pitcher_stats_v7(base_stats, statcast_stats):
    stats = dict(base_stats or {})
    sc = statcast_stats or _statcast_default()
    stats.update({
        "statcast_ok": bool(sc.get("statcast_ok", False)),
        "statcast_pitches": sc.get("pitches", 0),
        "xwoba_allowed": sc.get("xwoba_allowed"),
        "avg_ev_allowed": sc.get("avg_ev_allowed"),
        "hard_hit_pct": sc.get("hard_hit_pct"),
        "barrel_proxy_pct": sc.get("barrel_proxy_pct"),
        "whiff_pct": sc.get("whiff_pct"),
        "k_pitch_pct": sc.get("k_pitch_pct"),
        "statcast_sample": sc.get("sample_level", "sin_statcast"),
        "statcast_note": sc.get("edge_note", "Statcast no disponible"),
    })
    return stats


def calcular_statcast_edge_pitcher(stats):
    """
    Score extra del pitcher basado en Statcast.
    Positivo = pitcher limita mejor contacto / induce más whiffs.
    Negativo = permite contacto peligroso.
    """
    if not stats or not stats.get("statcast_ok"):
        return 0.0

    score = 0.0
    xwoba = stats.get("xwoba_allowed")
    avg_ev = stats.get("avg_ev_allowed")
    hard = stats.get("hard_hit_pct")
    barrel = stats.get("barrel_proxy_pct")
    whiff = stats.get("whiff_pct")

    if xwoba is not None:
        if xwoba <= .285:
            score += 0.18
        elif xwoba <= .310:
            score += 0.10
        elif xwoba <= .335:
            score += 0.03
        elif xwoba >= .380:
            score -= 0.18
        elif xwoba >= .355:
            score -= 0.10

    if avg_ev is not None:
        if avg_ev <= 86.5:
            score += 0.08
        elif avg_ev <= 88.0:
            score += 0.04
        elif avg_ev >= 91.0:
            score -= 0.10
        elif avg_ev >= 89.8:
            score -= 0.05

    if hard is not None:
        if hard <= 32:
            score += 0.08
        elif hard <= 37:
            score += 0.04
        elif hard >= 45:
            score -= 0.10
        elif hard >= 41:
            score -= 0.05

    if barrel is not None:
        if barrel <= 3:
            score += 0.05
        elif barrel >= 9:
            score -= 0.06

    if whiff is not None:
        if whiff >= 32:
            score += 0.08
        elif whiff >= 27:
            score += 0.04
        elif whiff <= 18:
            score -= 0.06

    pitches = stats.get("statcast_pitches", 0) or 0
    mult = 1.0 if pitches >= STATCAST_MIN_PITCHES else 0.65
    return round(score * mult, 3)


def resumen_statcast_pitcher(stats):
    if not stats or not stats.get("statcast_ok"):
        return "Statcast: N/D"
    xwoba = stats.get("xwoba_allowed")
    hard = stats.get("hard_hit_pct")
    whiff = stats.get("whiff_pct")
    pitches = stats.get("statcast_pitches", 0)
    partes = [f"SC {pitches} pit"]
    if xwoba is not None:
        partes.append(f"xwOBA {xwoba}")
    if hard is not None:
        partes.append(f"HH {hard}%")
    if whiff is not None:
        partes.append(f"Whiff {whiff}%")
    return " | ".join(partes)


def statcast_status_text():
    if USE_STATCAST_EDGE and PYBASEBALL_AVAILABLE:
        return f"ON ({STATCAST_DAYS_LOOKBACK}d)"
    if USE_STATCAST_EDGE and not PYBASEBALL_AVAILABLE:
        return "OFF: instala pybaseball"
    return "OFF por .env"

@lru_cache(maxsize=128)
def obtener_venue_detalle(venue_id):
    if not venue_id:
        return {}
    data = safe_get(f"{MLB_BASE}/venues", params={"venueIds": str(venue_id)})
    venues = data.get("venues", [])
    return venues[0] if venues else {}


@lru_cache(maxsize=128)
def geocodificar_lugar(nombre_lugar):
    if not nombre_lugar:
        return None
    data = safe_get(
        "https://geocoding-api.open-meteo.com/v1/search",
        params={"name": nombre_lugar, "count": 1, "language": "en", "format": "json"},
    )
    results = data.get("results", [])
    if not results:
        return None
    r = results[0]
    return {"latitude": r.get("latitude"), "longitude": r.get("longitude")}


def extraer_coords_venue(venue):
    if not venue:
        return None
    loc = venue.get("location", {}) or {}
    default_coords = loc.get("defaultCoordinates", {}) or {}
    lat = default_coords.get("latitude")
    lon = default_coords.get("longitude")
    if lat is not None and lon is not None:
        return {"latitude": lat, "longitude": lon}

    query = ", ".join(
        [x for x in [venue.get("name", ""), loc.get("city", ""), loc.get("stateAbbrev", "") or loc.get("state", "")] if x]
    )
    return geocodificar_lugar(query)


def obtener_clima_partido(game):
    try:
        venue_id = game.get("venue", {}).get("id")
        if venue_id and int(venue_id) in VENUE_IDS_TECHO:
            return {"temp_c": None, "wind_kmh": None, "precip_mm": None, "techo": True}

        venue = obtener_venue_detalle(venue_id)
        coords = extraer_coords_venue(venue)
        if not coords:
            return {"temp_c": None, "wind_kmh": None, "precip_mm": None}

        game_date = game.get("gameDate")
        if not game_date:
            return {"temp_c": None, "wind_kmh": None, "precip_mm": None}

        dt_utc = datetime.fromisoformat(game_date.replace("Z", "+00:00"))
        data = safe_get(
            "https://api.open-meteo.com/v1/forecast",
            params={
                "latitude": coords["latitude"],
                "longitude": coords["longitude"],
                "hourly": "temperature_2m,precipitation,wind_speed_10m",
                "timezone": "auto",
                "forecast_days": 2,
            },
        )
        hourly = data.get("hourly", {})
        times = hourly.get("time", [])
        temps = hourly.get("temperature_2m", [])
        precs = hourly.get("precipitation", [])
        winds = hourly.get("wind_speed_10m", [])

        if not times:
            return {"temp_c": None, "wind_kmh": None, "precip_mm": None}

        best_idx = 0
        min_diff = None
        for i, t in enumerate(times):
            try:
                dt_local = datetime.fromisoformat(t)
                diff = abs((dt_local.replace(tzinfo=None) - dt_utc.replace(tzinfo=None)).total_seconds())
                if min_diff is None or diff < min_diff:
                    min_diff = diff
                    best_idx = i
            except Exception:
                continue

        return {
            "temp_c": temps[best_idx] if best_idx < len(temps) else None,
            "wind_kmh": winds[best_idx] if best_idx < len(winds) else None,
            "precip_mm": precs[best_idx] if best_idx < len(precs) else None,
        }
    except Exception as e:
        print(f"[WARN] obtener_clima_partido: {e}")
        return {"temp_c": None, "wind_kmh": None, "precip_mm": None}

# =========================================================
# MODELO
# =========================================================
def score_pitcher_real(stats):
    era = stats.get("era", 4.20)
    whip = stats.get("whip", 1.30)
    so9 = stats.get("so9", 8.2)
    fip = stats.get("fip", 4.20)
    bb9 = stats.get("bb9", 3.2)
    hr9 = stats.get("hr9", 1.2)
    ip = stats.get("ip", 0.0)

    score = 0.0

    if era <= 2.80:
        score += 0.30
    elif era <= 3.30:
        score += 0.20
    elif era <= 3.80:
        score += 0.10
    elif era <= 4.20:
        score += 0.02
    elif era > 5.00:
        score -= 0.18
    else:
        score -= 0.08

    if fip <= 2.90:
        score += 0.22
    elif fip <= 3.40:
        score += 0.15
    elif fip <= 3.90:
        score += 0.07
    elif fip > 5.00:
        score -= 0.15
    elif fip > 4.30:
        score -= 0.07

    if whip <= 1.00:
        score += 0.14
    elif whip <= 1.15:
        score += 0.09
    elif whip <= 1.28:
        score += 0.03
    elif whip > 1.45:
        score -= 0.12

    if so9 >= 11.0:
        score += 0.10
    elif so9 >= 9.5:
        score += 0.07
    elif so9 >= 8.0:
        score += 0.03
    elif so9 < 6.5:
        score -= 0.06

    if bb9 <= 2.0:
        score += 0.06
    elif bb9 <= 2.8:
        score += 0.03
    elif bb9 >= 4.5:
        score -= 0.08

    if hr9 <= 0.8:
        score += 0.04
    elif hr9 >= 1.8:
        score -= 0.07

    # V7_STATCAST_EDGE: calidad de contacto permitido + whiffs.
    score += calcular_statcast_edge_pitcher(stats)

    if ip >= 100:
        mult = 1.00
    elif ip >= 60:
        mult = 0.90
    elif ip >= 30:
        mult = 0.78
    elif ip >= 15:
        mult = 0.60
    else:
        mult = 0.38

    return round(score * mult, 3)


def ajuste_clima_total(weather, venue_id=None):
    if venue_id and safe_int(venue_id, 0) in VENUE_IDS_TECHO:
        return 0.0
    if not weather or weather.get("techo"):
        return 0.0

    temp_c = weather.get("temp_c")
    wind_kmh = weather.get("wind_kmh")
    precip_mm = weather.get("precip_mm")

    adj = 0.0
    if temp_c is not None:
        if temp_c >= 30:
            adj += 0.40
        elif temp_c >= 26:
            adj += 0.22
        elif temp_c >= 22:
            adj += 0.10
        elif temp_c <= 8:
            adj -= 0.35
        elif temp_c <= 14:
            adj -= 0.18

    if wind_kmh is not None:
        if wind_kmh >= 30:
            adj += 0.28
        elif wind_kmh >= 22:
            adj += 0.15
        elif wind_kmh >= 16:
            adj += 0.07

    if precip_mm is not None and precip_mm >= 1.0:
        adj -= 0.22

    return round(adj, 2)


def ajuste_clima_ml(weather, venue_id=None):
    if venue_id and safe_int(venue_id, 0) in VENUE_IDS_TECHO:
        return 0.0
    if not weather or weather.get("techo"):
        return 0.0

    adj = 0.0
    if (weather.get("precip_mm") or 0) >= 1.0:
        adj -= 0.012
    if (weather.get("temp_c") or 20) <= 6:
        adj -= 0.010
    return adj


def calcular_probabilidad_local_pro(
    away_team,
    home_team,
    standings,
    away_pitcher="TBD",
    home_pitcher="TBD",
    away_pitcher_stats=None,
    home_pitcher_stats=None,
    weather=None,
    venue_id=None,
):
    away = standings.get(away_team)
    home = standings.get(home_team)
    if not away or not home:
        return 0.50

    default_stats = {
        "era": 4.20,
        "whip": 1.30,
        "so9": 8.2,
        "bb9": 3.2,
        "hr9": 1.2,
        "fip": 4.20,
        "ip": 0.0,
        "sample_ok": False,
    }
    if away_pitcher_stats is None:
        away_pitcher_stats = default_stats
    if home_pitcher_stats is None:
        home_pitcher_stats = default_stats

    diff_win_pct = home["win_pct"] - away["win_pct"]
    diff_split = home["home_win_pct"] - away["away_win_pct"]
    diff_last10 = home["last10_win_pct"] - away["last10_win_pct"]
    diff_run_diff = (home["run_diff"] - away["run_diff"]) / 100.0
    diff_streak = parse_streak(home["streak"]) - parse_streak(away["streak"])
    diff_runs_scored = (home.get("runs_scored", 4.5) - away.get("runs_scored", 4.5)) / 10.0
    diff_runs_allowed = (away.get("runs_allowed", 4.5) - home.get("runs_allowed", 4.5)) / 10.0
    diff_pitcher = score_pitcher_real(home_pitcher_stats) - score_pitcher_real(away_pitcher_stats)

    score = 0.0
    score += diff_win_pct * 2.8
    score += diff_split * 1.9
    score += diff_last10 * 1.2
    score += diff_run_diff * 1.6
    score += diff_streak * 1.0
    score += diff_runs_scored * 0.9
    score += diff_runs_allowed * 0.9
    score += diff_pitcher * 1.8
    score += 0.09
    score += ajuste_clima_ml(weather, venue_id=venue_id)

    if away_pitcher == "TBD":
        score += 0.04
    if home_pitcher == "TBD":
        score -= 0.04

    prob = logistic(score)
    if 0.492 <= prob <= 0.508:
        prob = 0.518 if score >= 0 else 0.482

    return clamp(prob, 0.28, 0.72)


def obtener_pick_juego_pro(
    away_team,
    home_team,
    standings,
    away_pitcher="TBD",
    home_pitcher="TBD",
    away_pitcher_stats=None,
    home_pitcher_stats=None,
    weather=None,
    venue_id=None,
):
    prob_home = calcular_probabilidad_local_pro(
        away_team,
        home_team,
        standings,
        away_pitcher,
        home_pitcher,
        away_pitcher_stats,
        home_pitcher_stats,
        weather,
        venue_id=venue_id,
    )
    favorito = home_team if prob_home >= 0.5 else away_team
    prob_fav = prob_home if favorito == home_team else (1 - prob_home)
    avoid = away_pitcher == "TBD" or home_pitcher == "TBD"

    return {
        "favorite": favorito,
        "prob_home": prob_home,
        "prob_favorite": prob_fav,
        "confidence_pct": round(prob_fav * 100, 1),
        "confidence_label": confidence_label(prob_fav),
        "avoid": avoid,
    }


def estimar_total_juego_pro(
    away_team,
    home_team,
    standings,
    away_pitcher="TBD",
    home_pitcher="TBD",
    away_pitcher_stats=None,
    home_pitcher_stats=None,
    weather=None,
    venue_id=None,
):
    away = standings.get(away_team, {})
    home = standings.get(home_team, {})
    default_stats = {"era": 4.20, "whip": 1.30, "so9": 8.2, "fip": 4.20, "ip": 0.0}
    if away_pitcher_stats is None:
        away_pitcher_stats = default_stats
    if home_pitcher_stats is None:
        home_pitcher_stats = default_stats

    away_rs = away.get("runs_scored", 4.5)
    home_rs = home.get("runs_scored", 4.5)
    away_ra = away.get("runs_allowed", 4.5)
    home_ra = home.get("runs_allowed", 4.5)

    total = 8.6
    total += ((away_rs + home_rs) - 9.0) * 0.24
    total += ((away_ra + home_ra) - 9.0) * 0.20
    total += ((away.get("run_diff", 0) + home.get("run_diff", 0)) / 162.0) * 0.22

    ap_era = away_pitcher_stats.get("fip", away_pitcher_stats.get("era", 4.20))
    hp_era = home_pitcher_stats.get("fip", home_pitcher_stats.get("era", 4.20))
    total += (ap_era - 4.00) * 0.34
    total += (hp_era - 4.00) * 0.34
    total += (away_pitcher_stats.get("whip", 1.30) - 1.25) * 0.82
    total += (home_pitcher_stats.get("whip", 1.30) - 1.25) * 0.82
    total -= (away_pitcher_stats.get("so9", 8.2) - 8.5) * 0.10
    total -= (home_pitcher_stats.get("so9", 8.2) - 8.5) * 0.10

    # V7_STATCAST_EDGE: si los pitchers permiten contacto duro, sube el total;
    # si limitan xwOBA/contacto fuerte y generan whiffs, baja el total.
    for ps in [away_pitcher_stats, home_pitcher_stats]:
        if ps.get("statcast_ok"):
            xwoba = ps.get("xwoba_allowed")
            hard = ps.get("hard_hit_pct")
            whiff = ps.get("whiff_pct")
            if xwoba is not None:
                total += (xwoba - 0.330) * 3.8
            if hard is not None:
                total += (hard - 39.0) * 0.018
            if whiff is not None:
                total -= (whiff - 24.0) * 0.018

    if away_pitcher == "TBD":
        total += 0.45
    if home_pitcher == "TBD":
        total += 0.45

    last10 = away.get("last10_win_pct", 0.5) + home.get("last10_win_pct", 0.5)
    total += (last10 - 1.0) * 0.25
    total += ajuste_clima_total(weather, venue_id=venue_id)

    return round(clamp(total, 6.2, 12.8), 1)


def elegir_total_pick(total_proyectado, total_line):
    if total_line is None:
        return None
    diff = total_proyectado - total_line
    if diff >= 0.85:
        return {"pick": f"Over {total_line}", "edge": round(diff, 2), "strength": "Alta"}
    if diff >= 0.55:
        return {"pick": f"Over {total_line}", "edge": round(diff, 2), "strength": "Media"}
    if diff <= -0.85:
        return {"pick": f"Under {total_line}", "edge": round(abs(diff), 2), "strength": "Alta"}
    if diff <= -0.55:
        return {"pick": f"Under {total_line}", "edge": round(abs(diff), 2), "strength": "Media"}
    return None


def elegir_total_pick_fallback(total_proyectado, total_line_conocida=None):
    referencia = total_line_conocida if total_line_conocida is not None else 8.8
    diff = total_proyectado - referencia
    if diff >= 0.80:
        return {"pick": f"Over {referencia}", "edge": round(diff, 2), "strength": "Fallback"}
    if diff <= -0.80:
        return {"pick": f"Under {referencia}", "edge": round(abs(diff), 2), "strength": "Fallback"}
    return None


def calcular_prob_total_modelo(total_diff):
    magnitud = abs(total_diff)
    if magnitud < 0.55:
        return None
    return clamp(0.50 + min(magnitud, 1.8) * 0.055, 0.52, 0.64)


def score_pick_ml(analisis):
    conf_n = clamp(analisis["confidence_pct"] / 72.0, 0, 1)
    edge_n = clamp(analisis["ml_edge_pct"] / 12.0, -1, 1)
    ev_n = clamp(analisis["ev_ml_pct"] / 15.0, -1, 1)
    pitch_n = clamp((analisis["pitching_advantage"] + 0.5) / 1.0, 0, 1)
    form_n = clamp((analisis["recent_form_advantage"] + 0.5) / 1.0, 0, 1)

    score = conf_n * 35.0 + ev_n * 30.0 + edge_n * 20.0 + pitch_n * 10.0 + form_n * 5.0

    if analisis["risk_flags"].get("tbd_pitcher"):
        score -= 20.0
    if analisis["risk_flags"].get("sin_odds_ml"):
        score -= 8.0
    if analisis["risk_flags"].get("cuota_extrema_ml"):
        score -= 12.0
    if analisis["is_home_pick"]:
        score += 2.0

    return round(clamp(score, 0, 100), 2)


def score_pick_total(analisis):
    if not analisis.get("total_pick"):
        return 0.0

    edge_n = clamp(analisis["total_edge"] / 2.5, 0, 1)
    ev_n = clamp(analisis["ev_total_pct"] / 12.0, -1, 1)
    conf_n = clamp(analisis["confidence_pct"] / 72.0, 0, 1)
    score = ev_n * 40.0 + edge_n * 35.0 + conf_n * 25.0

    if analisis["total_pick"].get("strength") == "Alta":
        score += 8.0
    if analisis["risk_flags"].get("sin_odds_total"):
        score -= 10.0
    if analisis["risk_flags"].get("clima_extremo"):
        score -= 5.0
    if analisis["total_pick"].get("strength") == "Fallback":
        score -= 15.0

    return round(clamp(score, 0, 100), 2)

# =========================================================
# ODDS
# =========================================================
def normalizar_nombre_equipo_odds(team_name):
    mapping = {
        "St. Louis Cardinals": "St Louis Cardinals",
        "Athletics": "Oakland Athletics",
    }
    return mapping.get(team_name, team_name)


def team_key(texto):
    if not texto:
        return ""
    key = (
        str(texto)
        .lower()
        .replace(".", "")
        .replace("-", " ")
        .replace("'", "")
        .replace("los angeles", "la")
        .replace("st louis", "stlouis")
        .replace("new york", "ny")
    )
    return " ".join(key.split())


def score_team_match(a, b):
    ak = set(team_key(a).split())
    bk = set(team_key(b).split())
    if not ak or not bk:
        return 0
    inter = len(ak & bk)
    if inter == 0:
        return 0
    return int((inter / max(len(ak), len(bk))) * 100)


_odds_cache = {"data": None, "ts": 0.0}


def obtener_odds_snapshot(force=False):
    if not ODDS_API_KEY:
        return None
    ahora = time.time()
    if not force and _odds_cache["data"] is not None and (ahora - _odds_cache["ts"]) < ODDS_TTL_SEGUNDOS:
        return _odds_cache["data"]

    url = "https://api.the-odds-api.com/v4/sports/baseball_mlb/odds"
    params = {
        "apiKey": ODDS_API_KEY,
        "regions": "us",
        "markets": "h2h,totals",
        "oddsFormat": "american",
    }
    data = safe_get(url, params=params)
    if isinstance(data, list):
        _odds_cache["data"] = data
        _odds_cache["ts"] = ahora
        return data
    return None


def _extraer_odds_de_book(book, mapped_home, mapped_away):
    res = {
        "bookmaker": book.get("title", ""),
        "home_moneyline": None,
        "away_moneyline": None,
        "total_line": None,
        "over_price": None,
        "under_price": None,
    }
    for market in book.get("markets", []) or []:
        key = market.get("key")
        if key == "h2h":
            for o in market.get("outcomes", []) or []:
                if o.get("name") == mapped_home:
                    res["home_moneyline"] = o.get("price")
                elif o.get("name") == mapped_away:
                    res["away_moneyline"] = o.get("price")
        elif key == "totals":
            for o in market.get("outcomes", []) or []:
                if o.get("name") == "Over":
                    res["total_line"] = o.get("point")
                    res["over_price"] = o.get("price")
                elif o.get("name") == "Under":
                    res["under_price"] = o.get("price")
    return res


def obtener_odds_completas(away_team, home_team):
    data = obtener_odds_snapshot()
    if not isinstance(data, list):
        return None

    away_norm = normalizar_nombre_equipo_odds(away_team)
    home_norm = normalizar_nombre_equipo_odds(home_team)
    away_key = team_key(away_norm)
    home_key = team_key(home_norm)

    evento_match = None
    usar_swap = False

    for event in data:
        home_name = event.get("home_team", "")
        teams = event.get("teams", []) or []
        away_name = next((t for t in teams if t != home_name), "")
        direct = team_key(home_name) == home_key and team_key(away_name) == away_key
        swapped = team_key(home_name) == away_key and team_key(away_name) == home_key
        if direct or swapped:
            evento_match = event
            usar_swap = swapped
            break

    if evento_match is None:
        best_score = -1
        for event in data:
            home_name = event.get("home_team", "")
            teams = event.get("teams", []) or []
            away_name = next((t for t in teams if t != home_name), "")
            sd = score_team_match(home_name, home_norm) + score_team_match(away_name, away_norm)
            ss = score_team_match(home_name, away_norm) + score_team_match(away_name, home_norm)
            s = max(sd, ss)
            if s > best_score and s >= 120:
                best_score = s
                evento_match = event
                usar_swap = ss > sd

    if evento_match is None:
        return None

    home_name = evento_match.get("home_team", "")
    teams = evento_match.get("teams", []) or []
    away_name = next((t for t in teams if t != home_name), "")
    mapped_home = away_name if usar_swap else home_name
    mapped_away = home_name if usar_swap else away_name

    resultados = []
    for book in evento_match.get("bookmakers", []) or []:
        res = _extraer_odds_de_book(book, mapped_home, mapped_away)
        if res["home_moneyline"] is not None or res["away_moneyline"] is not None:
            resultados.append(res)

    if not resultados:
        return None

    def book_rank(res):
        name_bonus = 10 if res.get("bookmaker") in BOOKMAKERS_PRIORITARIOS else 0
        completeness = 0
        for key in ["home_moneyline", "away_moneyline", "total_line", "over_price", "under_price"]:
            if res.get(key) is not None:
                completeness += 1
        return (name_bonus, completeness)

    resultados.sort(key=book_rank, reverse=True)
    return resultados[0]

# =========================================================
# ANÁLISIS DE JUEGO
# =========================================================
def analizar_juego(game, standings):
    teams = game.get("teams", {})
    away_data = teams.get("away", {})
    home_data = teams.get("home", {})
    away = away_data.get("team", {}).get("name")
    home = home_data.get("team", {}).get("name")
    if not away or not home:
        return None

    venue_id = game.get("venue", {}).get("id")
    away_pitcher_obj = away_data.get("probablePitcher", {}) or {}
    home_pitcher_obj = home_data.get("probablePitcher", {}) or {}
    away_pitcher = away_pitcher_obj.get("fullName", "TBD")
    home_pitcher = home_pitcher_obj.get("fullName", "TBD")
    away_stats_base = obtener_stats_pitcher_reales(away_pitcher_obj.get("id"))
    home_stats_base = obtener_stats_pitcher_reales(home_pitcher_obj.get("id"))

    away_sc = obtener_statcast_pitcher_edge(away_pitcher_obj.get("id"), away_pitcher)
    home_sc = obtener_statcast_pitcher_edge(home_pitcher_obj.get("id"), home_pitcher)
    away_stats = combinar_pitcher_stats_v7(away_stats_base, away_sc)
    home_stats = combinar_pitcher_stats_v7(home_stats_base, home_sc)

    weather = obtener_clima_partido(game) or {"temp_c": None, "wind_kmh": None, "precip_mm": None}

    pred = obtener_pick_juego_pro(
        away,
        home,
        standings,
        away_pitcher,
        home_pitcher,
        away_stats,
        home_stats,
        weather,
        venue_id=venue_id,
    )
    total_proj = estimar_total_juego_pro(
        away,
        home,
        standings,
        away_pitcher,
        home_pitcher,
        away_stats,
        home_stats,
        weather,
        venue_id=venue_id,
    )
    odds = obtener_odds_completas(away, home)

    ml_odds = None
    total_line = None
    total_pick = None
    total_odds = None
    prob_total_model = None
    ev_ml = None
    ev_total = None
    ml_edge_pct = 0.0
    total_edge = 0.0

    if odds and isinstance(odds, dict):
        ml_odds = odds.get("home_moneyline") if pred["favorite"] == home else odds.get("away_moneyline")
        total_line = odds.get("total_line")
        total_pick = elegir_total_pick(total_proj, total_line)
        if total_pick:
            total_edge = total_pick["edge"]
            total_odds = odds.get("over_price") if "Over" in total_pick["pick"] else odds.get("under_price")
            prob_total_model = calcular_prob_total_modelo(total_proj - (total_line or total_proj))
            if prob_total_model is not None and total_odds is not None:
                ev_total = calcular_ev(prob_total_model, total_odds)

    if total_pick is None:
        total_pick = elegir_total_pick_fallback(total_proj, total_line)
        if total_pick:
            total_edge = total_pick["edge"]

    if ml_odds is not None:
        implied_ml = moneyline_to_prob(ml_odds)
        ev_ml = calcular_ev(pred["prob_favorite"], ml_odds)
        ml_edge_pct = round((pred["prob_favorite"] - (implied_ml or 0.5)) * 100, 2)
    else:
        ml_edge_pct = round((pred["prob_favorite"] - 0.5) * 100, 2)

    away_form = standings.get(away, {}).get("last10_win_pct", 0.5)
    home_form = standings.get(home, {}).get("last10_win_pct", 0.5)
    favorite_form = home_form if pred["favorite"] == home else away_form
    underdog_form = away_form if pred["favorite"] == home else home_form

    risk_flags = {
        "tbd_pitcher": pred["avoid"],
        "sin_odds_ml": ml_odds is None,
        "sin_odds_total": total_pick is not None and total_odds is None,
        "usando_fallback": ml_odds is None or total_odds is None,
        "cuota_extrema_ml": ml_odds is not None and (ml_odds <= -220 or ml_odds >= 170),
        "clima_extremo": (weather.get("wind_kmh") or 0) >= 30,
    }

    analisis = {
        "game": f"{away} @ {home}",
        "away": away,
        "home": home,
        "matchup_key": normalizar_matchup(away, home),
        "pitchers": {"away": away_pitcher, "home": home_pitcher},
        "pitcher_stats": {"away": away_stats, "home": home_stats},
        "statcast": {
            "status": statcast_status_text(),
            "away": resumen_statcast_pitcher(away_stats),
            "home": resumen_statcast_pitcher(home_stats),
            "away_edge": calcular_statcast_edge_pitcher(away_stats),
            "home_edge": calcular_statcast_edge_pitcher(home_stats),
            "advantage_home": round(calcular_statcast_edge_pitcher(home_stats) - calcular_statcast_edge_pitcher(away_stats), 3),
        },
        "clima": {
            "temp_c": safe_float(weather.get("temp_c")),
            "wind_kmh": safe_float(weather.get("wind_kmh")),
            "precip_mm": safe_float(weather.get("precip_mm")),
            "techo": bool(weather.get("techo", False)),
        },
        "ml_pick": f"{pred['favorite']} ML",
        "favorite": pred["favorite"],
        "is_home_pick": pred["favorite"] == home,
        "prob_home": round(pred["prob_home"] * 100, 1),
        "prob_away": round((1 - pred["prob_home"]) * 100, 1),
        "prob_favorite": pred["prob_favorite"],
        "confidence_pct": pred["confidence_pct"],
        "confidence_label": pred["confidence_label"],
        "ml_odds": safe_int(ml_odds),
        "has_valid_ml_odds": ml_odds is not None,
        "ml_edge_pct": ml_edge_pct,
        "ev_ml": ev_ml,
        "ev_ml_pct": round(ev_ml * 100, 2) if ev_ml is not None else 0.0,
        "grade_ml": grade_por_ev(ev_ml),
        "stake_ml": stake_por_ev(ev_ml),
        "total_projection": total_proj,
        "total_line": safe_float(total_line),
        "total_pick": total_pick,
        "total_odds": safe_int(total_odds),
        "has_valid_total_odds": total_odds is not None,
        "prob_total_model": prob_total_model,
        "ev_total": ev_total,
        "ev_total_pct": round(ev_total * 100, 2) if ev_total is not None else 0.0,
        "grade_total": grade_por_ev(ev_total),
        "stake_total": stake_por_ev(ev_total),
        "total_edge": total_edge,
        "risk_flags": risk_flags,
        "pitching_advantage": score_pitcher_real(home_stats) - score_pitcher_real(away_stats),
        "recent_form_advantage": favorite_form - underdog_form,
    }
    analisis["score_ml"] = score_pick_ml(analisis)
    analisis["score_total"] = score_pick_total(analisis)
    analisis["score_agresivo"] = round(analisis["score_ml"] * 0.65 + analisis["score_total"] * 0.35, 2)

    print(
        "[ANALISIS]",
        analisis["game"],
        "conf=",
        analisis["confidence_pct"],
        "ml_odds=",
        analisis["ml_odds"],
        "edge=",
        analisis["ml_edge_pct"],
        "ev=",
        analisis["ev_ml_pct"],
        "score_ml=",
        analisis["score_ml"],
        "score_total=",
        analisis["score_total"],
    )
    return analisis


_analisis_cache = {"data": None, "ts": 0.0}


def obtener_analisis_del_dia(force=False):
    ahora = time.time()
    if not force and _analisis_cache["data"] is not None:
        if ahora - _analisis_cache["ts"] < ANALISIS_TTL_SEGUNDOS:
            return _analisis_cache["data"]

    standings = obtener_standings()
    games = obtener_juegos_del_dia()
    analisis = []
    for game in games:
        item = analizar_juego(game, standings)
        if item:
            analisis.append(item)

    _analisis_cache["data"] = analisis
    _analisis_cache["ts"] = ahora
    return analisis

# =========================================================
# LÓGICA DE PARLEYS
# =========================================================
def _leg_ml(a, fase="modelo", score_extra=None):
    return {
        "tipo": "ML",
        "game": a["game"],
        "matchup_key": a["matchup_key"],
        "pick": a["ml_pick"],
        "confidence": a["confidence_pct"],
        "edge": a["ml_edge_pct"],
        "ev_pct": a["ev_ml_pct"],
        "score": round(score_extra if score_extra is not None else a["score_ml"], 2),
        "cuota": a["ml_odds"] if a["ml_odds"] is not None else "N/D",
        "is_home_pick": a["is_home_pick"],
        "tbd_pitcher": a["risk_flags"].get("tbd_pitcher", False),
        "fase": fase,
    }


def _leg_total(a, fase="modelo"):
    return {
        "tipo": "TOTAL",
        "game": a["game"],
        "matchup_key": a["matchup_key"],
        "pick": a["total_pick"]["pick"],
        "confidence": a["confidence_pct"],
        "edge": a["total_edge"],
        "ev_pct": a["ev_total_pct"],
        "score": round(a["score_total"], 2),
        "cuota": a["total_odds"] if a["total_odds"] is not None else "N/D",
        "is_home_pick": False,
        "tbd_pitcher": a["risk_flags"].get("tbd_pitcher", False),
        "fase": fase,
    }


def calcular_parley_del_dia(analisis_juegos, target=3, max_nd=1, debug=False):
    """
    Parley normal: intenta llegar a 3 picks.
    Si no alcanza, devuelve 2 como parley reducido o 1 como pick simple.
    Tiene relleno final para evitar que el bot quede vacío cuando sí hay juegos.
    """
    if not analisis_juegos:
        return [], "sin_juegos"

    fases = [
        {
            "nombre": "estricto",
            "min_conf": 54.0,
            "min_score": 30.0,
            "min_edge": 0.5,
            "min_ev": 0.8,
            "require_odds": True,
            "allow_tbd": False,
            "allow_extreme": False,
        },
        {
            "nombre": "fallback_A",
            "min_conf": 52.5,
            "min_score": 24.0,
            "min_edge": -0.5,
            "min_ev": -1.5,
            "require_odds": False,
            "allow_tbd": False,
            "allow_extreme": True,
        },
        {
            "nombre": "fallback_B",
            "min_conf": 51.5,
            "min_score": 18.0,
            "min_edge": -2.0,
            "min_ev": -4.0,
            "require_odds": False,
            "allow_tbd": False,
            "allow_extreme": True,
        },
        {
            "nombre": "emergencia",
            "min_conf": 50.1,
            "min_score": 0.0,
            "min_edge": -99.0,
            "min_ev": -99.0,
            "require_odds": False,
            "allow_tbd": False,
            "allow_extreme": True,
        },
    ]

    seleccionados = []
    usados_mk = set()
    nd_count = 0
    nivel_usado = "ninguno"
    reasons = {}

    def puede_agregar_leg(leg, nd_limit):
        if leg["matchup_key"] in usados_mk:
            return False
        if leg["cuota"] == "N/D" and nd_count >= nd_limit:
            return False
        return True

    def agregar_leg(leg):
        nonlocal nd_count
        seleccionados.append(leg)
        usados_mk.add(leg["matchup_key"])
        if leg["cuota"] == "N/D":
            nd_count += 1

    for fase in fases:
        candidatos = []
        for a in analisis_juegos:
            if a["risk_flags"].get("tbd_pitcher") and not fase["allow_tbd"]:
                reasons["pitcher_tbd"] = reasons.get("pitcher_tbd", 0) + 1
                continue
            if fase["require_odds"] and not a["has_valid_ml_odds"]:
                reasons["sin_odds"] = reasons.get("sin_odds", 0) + 1
                continue
            if not fase["allow_extreme"] and a["risk_flags"].get("cuota_extrema_ml"):
                reasons["cuota_extrema"] = reasons.get("cuota_extrema", 0) + 1
                continue
            if a["confidence_pct"] < fase["min_conf"]:
                reasons["conf_baja"] = reasons.get("conf_baja", 0) + 1
                continue
            if a["score_ml"] < fase["min_score"]:
                reasons["score_bajo"] = reasons.get("score_bajo", 0) + 1
                continue
            if a["ml_edge_pct"] < fase["min_edge"]:
                reasons["edge_bajo"] = reasons.get("edge_bajo", 0) + 1
                continue
            if a["ev_ml_pct"] < fase["min_ev"]:
                reasons["ev_bajo"] = reasons.get("ev_bajo", 0) + 1
                continue

            adjusted = a["score_ml"] + a["confidence_pct"] * 0.25 + a["ml_edge_pct"] * 0.7 + a["ev_ml_pct"] * 0.6
            if a["ml_odds"] is None:
                adjusted -= 4.0
            if a["risk_flags"].get("cuota_extrema_ml"):
                adjusted -= 4.0
            candidatos.append(_leg_ml(a, fase=fase["nombre"], score_extra=adjusted))

        candidatos.sort(key=lambda x: (x["cuota"] != "N/D", x["score"], x["confidence"], x["edge"]), reverse=True)

        # Si no hay odds suficientes en todo el slate, no limites a un solo N/D.
        odds_disponibles = sum(1 for a in analisis_juegos if a.get("ml_odds") is not None)
        nd_limit = max_nd if odds_disponibles >= target else target

        for leg in candidatos:
            if len(seleccionados) >= target:
                break
            if not puede_agregar_leg(leg, nd_limit):
                continue
            agregar_leg(leg)
            nivel_usado = fase["nombre"]
        if len(seleccionados) >= target:
            break

    # Relleno final: si hay juegos, evita quedarse en 1 pick por filtros demasiado duros.
    if len(seleccionados) < target:
        relleno = []
        for a in analisis_juegos:
            if a["matchup_key"] in usados_mk:
                continue
            score = a["confidence_pct"] * 1.0 + a["score_ml"] * 0.40 + a["ml_edge_pct"] * 0.60 + a["ev_ml_pct"] * 0.35
            if a["risk_flags"].get("tbd_pitcher"):
                score -= 10.0
            if a["ml_odds"] is None:
                score -= 4.0
            if a["risk_flags"].get("cuota_extrema_ml"):
                score -= 5.0
            relleno.append(_leg_ml(a, fase="relleno_final", score_extra=score))

        relleno.sort(key=lambda x: (x["cuota"] != "N/D", not x["tbd_pitcher"], x["score"], x["confidence"]), reverse=True)
        odds_disponibles = sum(1 for a in analisis_juegos if a.get("ml_odds") is not None and a["matchup_key"] not in usados_mk)
        nd_limit = max_nd if odds_disponibles >= (target - len(seleccionados)) else target

        for leg in relleno:
            if len(seleccionados) >= target:
                break
            if not puede_agregar_leg(leg, nd_limit):
                continue
            agregar_leg(leg)
            nivel_usado = "relleno_final"

    if len(seleccionados) == 2:
        nivel_usado = "parley_reducido"
    elif len(seleccionados) == 1:
        nivel_usado = "pick_simple"
    elif len(seleccionados) == 0:
        nivel_usado = "sin_picks"

    if debug:
        resumen = ", ".join(f"{k}:{v}" for k, v in sorted(reasons.items()))
        print(f"[PARLEY] nivel={nivel_usado} picks={len(seleccionados)} nd={nd_count} descartes=({resumen})")

    return seleccionados, nivel_usado


def calcular_parley_millonario(analisis_juegos, matchups_bloqueados=None, target=5, max_nd=2, debug=False):
    """
    Parley millonario: intenta 5 legs.
    Permite ML + TOTAL del mismo partido, pero evita duplicar el mismo tipo.
    Si bloquear el parley diario deja muy pocos juegos, relaja el bloqueo.
    """
    if not analisis_juegos:
        return [], "sin_juegos", False

    matchups_bloqueados = matchups_bloqueados or set()
    disponibles_base = [a for a in analisis_juegos if a["matchup_key"] not in matchups_bloqueados]
    if len(disponibles_base) < 3:
        # Si hay muy pocos disponibles, no bloquees el parley normal. Es mejor generar que quedar vacío.
        disponibles_base = list(analisis_juegos)
        matchups_bloqueados = set()

    fases = [
        {"nombre": "estricto", "ml_conf": 53.5, "ml_ev": 0.8, "ml_edge": 0.5, "tot_edge": 0.65, "tot_ev": 0.8, "allow_nd": False, "allow_tbd": False},
        {"nombre": "flex", "ml_conf": 52.0, "ml_ev": -1.0, "ml_edge": -0.5, "tot_edge": 0.45, "tot_ev": -1.0, "allow_nd": True, "allow_tbd": False},
        {"nombre": "emergencia", "ml_conf": 50.1, "ml_ev": -99.0, "ml_edge": -99.0, "tot_edge": 0.25, "tot_ev": -99.0, "allow_nd": True, "allow_tbd": True},
    ]

    pools = []
    for fase in fases:
        candidatos = []
        for a in disponibles_base:
            if a["risk_flags"].get("tbd_pitcher") and not fase["allow_tbd"]:
                continue

            if a["confidence_pct"] >= fase["ml_conf"] and a["ev_ml_pct"] >= fase["ml_ev"] and a["ml_edge_pct"] >= fase["ml_edge"]:
                if a["ml_odds"] is not None or fase["allow_nd"]:
                    score = a["score_agresivo"] + a["confidence_pct"] * 0.15 + a["ml_edge_pct"] * 0.50 + a["ev_ml_pct"] * 0.40
                    if a["ml_odds"] is None:
                        score -= 4.0
                    if a["risk_flags"].get("tbd_pitcher"):
                        score -= 8.0
                    candidatos.append(_leg_ml(a, fase=fase["nombre"], score_extra=score))

            if a.get("total_pick") and a["total_edge"] >= fase["tot_edge"] and a["ev_total_pct"] >= fase["tot_ev"]:
                if a["total_odds"] is not None or fase["allow_nd"]:
                    leg = _leg_total(a, fase=fase["nombre"])
                    if leg["cuota"] == "N/D":
                        leg["score"] -= 3.5
                    if a["risk_flags"].get("tbd_pitcher"):
                        leg["score"] -= 6.0
                    leg["score"] += 3.0
                    candidatos.append(leg)

        candidatos = filtrar_candidatos_millonario(candidatos)
        candidatos.sort(key=lambda x: (x["cuota"] != "N/D", not x["tbd_pitcher"], x["score"], x["confidence"], x["edge"]), reverse=True)
        pools.append(candidatos)

    seleccionados = []
    vistos_tipo = set()
    nd_count = 0
    nivel_usado = "sin_picks"
    uso_fallback = False
    home_ml_count = 0
    matchup_leg_count = {}

    def puede_agregar(c, nd_limit):
        clave = (c["matchup_key"], c["tipo"])
        if clave in vistos_tipo:
            return False
        if matchup_leg_count.get(c["matchup_key"], 0) >= 2:
            return False
        if c["cuota"] == "N/D" and nd_count >= nd_limit:
            return False
        if c["tipo"] == "ML" and c.get("is_home_pick") and c["confidence"] < 54 and home_ml_count >= 2:
            return False
        return True

    def agregar(c, idx):
        nonlocal nd_count, home_ml_count, nivel_usado, uso_fallback
        seleccionados.append(c)
        vistos_tipo.add((c["matchup_key"], c["tipo"]))
        matchup_leg_count[c["matchup_key"]] = matchup_leg_count.get(c["matchup_key"], 0) + 1
        if c["cuota"] == "N/D":
            nd_count += 1
        if c["tipo"] == "ML" and c.get("is_home_pick"):
            home_ml_count += 1
        nivel_usado = c.get("fase", "modelo")
        if idx > 0 or c["cuota"] == "N/D":
            uso_fallback = True

    total_con_odds = 0
    for pool in pools:
        total_con_odds += sum(1 for c in pool if c["cuota"] != "N/D")
    nd_limit = max_nd if total_con_odds >= target else target

    for idx, pool in enumerate(pools):
        for c in pool:
            if len(seleccionados) >= target:
                break
            if not puede_agregar(c, nd_limit):
                continue
            agregar(c, idx)
        if len(seleccionados) >= target:
            break

    # Relleno final si todavía no llegó a 5.
    if len(seleccionados) < target:
        extra = []
        for a in disponibles_base:
            ml_score = a["confidence_pct"] + a["score_ml"] * 0.35 + a["ml_edge_pct"] * 0.5
            if a["risk_flags"].get("tbd_pitcher"):
                ml_score -= 10
            if a["ml_odds"] is None:
                ml_score -= 4
            extra.append(_leg_ml(a, fase="relleno_final", score_extra=ml_score))

            if a.get("total_pick"):
                leg = _leg_total(a, fase="relleno_final")
                leg["score"] = a["score_total"] + a["total_edge"] * 3.0 + 2.0
                if leg["cuota"] == "N/D":
                    leg["score"] -= 3
                extra.append(leg)

        extra = filtrar_candidatos_millonario(extra)
        extra.sort(key=lambda x: (x["cuota"] != "N/D", not x["tbd_pitcher"], x["score"], x["confidence"]), reverse=True)
        for c in extra:
            if len(seleccionados) >= target:
                break
            if not puede_agregar(c, nd_limit):
                continue
            agregar(c, 2)
            uso_fallback = True

    if len(seleccionados) >= target and nivel_usado == "sin_picks":
        nivel_usado = "relleno_final"
    elif len(seleccionados) < target and len(seleccionados) > 0:
        nivel_usado = "millonario_reducido"

    if debug:
        print(
            "[MILLONARIO]",
            "nivel=", nivel_usado,
            "picks=", len(seleccionados),
            "nd=", nd_count,
            "bloqueados=", len(matchups_bloqueados),
            "pools=", [len(p) for p in pools],
        )

    return seleccionados, nivel_usado, uso_fallback


def _calcular_cuota_parlay(seleccionados, fallback_dec=1.90):
    cuota = 1.0
    alguna_nd = False
    for p in seleccionados:
        raw = p.get("cuota") or p.get("ml_odds")
        if raw in (None, "N/D"):
            alguna_nd = True
            cuota *= fallback_dec
        else:
            dec = american_to_decimal(raw)
            if dec:
                cuota *= dec
    return round(cuota, 2), alguna_nd


def _formatear_parley_guardado(parley_data, icon, titulo):
    texto = header(titulo, icon)
    texto += f"📅 {parley_data.get('fecha', 'N/D')}\n"
    texto += f"🧩 Nivel: <b>{parley_data.get('nivel', 'N/D')}</b>\n\n"
    for leg in parley_data.get("legs", []):
        cuota = leg.get("cuota")
        cuota_txt = str(cuota) if cuota not in (None, "", "N/D") else "N/D"
        texto += card_game(
            leg.get("game", "N/D"),
            [
                f"🎯 {leg.get('tipo', 'ML')}: <b>{leg.get('pick', 'N/D')}</b>",
                f"🧠 Confianza: <b>{leg.get('confidence', 'N/D')}%</b>",
                f"📈 Edge: <b>{leg.get('edge', 'N/D')}</b> | EV: <b>{leg.get('ev_pct', 'N/D')}%</b>",
                f"💵 Cuota: <b>{cuota_txt}</b>",
                f"🧩 Fase: <b>{leg.get('fase', 'N/D')}</b>",
            ],
        )
    cuota_total = parley_data.get("cuota_total")
    if cuota_total:
        try:
            ganancia = round((float(cuota_total) - 1) * 100)
            texto += f"\n💰 <b>Cuota parlay: ~{cuota_total}x</b>\n"
            texto += f"📈 $100 → ganancia neta estimada: <b>${ganancia}</b>\n"
        except Exception:
            pass
    return texto

# =========================================================
# DATASET TIKTOK
# =========================================================
def generar_dataset_tiktok():
    analisis_juegos = obtener_analisis_del_dia()
    data = {
        "fecha": hoy_str(),
        "bot_version": BOT_VERSION,
        "juegos_del_dia": [],
        "pronosticos": [],
        "apuestas": {"moneyline_ev": [], "totales_ev": [], "modelo": []},
        "parley": [],
        "parley_millonario": [],
        "statcast_edge_status": statcast_status_text(),
    }
    if not analisis_juegos:
        return data

    for a in analisis_juegos:
        data["juegos_del_dia"].append({
            "game": a["game"],
            "matchup_key": a["matchup_key"],
            "away": a["away"],
            "home": a["home"],
            "pitchers": a["pitchers"],
            "statcast": a.get("statcast", {}),
        })

    picks_modelo = filtrar_matchups_unicos(sorted(analisis_juegos, key=lambda x: (x["confidence_pct"], x["score_ml"]), reverse=True))
    for a in picks_modelo[:8]:
        data["pronosticos"].append({
            "game": a["game"],
            "matchup_key": a["matchup_key"],
            "pick": a["ml_pick"],
            "confianza": a["confidence_pct"],
            "prob_home_pct": a["prob_home"],
            "prob_away_pct": a["prob_away"],
            "total_proyectado": a["total_projection"],
            "pitchers": f"{a['pitchers']['away']} vs {a['pitchers']['home']}",
            "statcast": a.get("statcast", {}),
            "clima": a["clima"],
        })

    picks_ml = [a for a in analisis_juegos if a["grade_ml"] != "D" and not a["risk_flags"].get("tbd_pitcher")]
    picks_ml = filtrar_matchups_unicos(sorted(picks_ml, key=lambda x: (x["score_ml"], x["ev_ml_pct"]), reverse=True))
    for a in picks_ml[:5]:
        data["apuestas"]["moneyline_ev"].append({
            "game": a["game"],
            "matchup_key": a["matchup_key"],
            "pick": a["ml_pick"],
            "grade": a["grade_ml"],
            "stake": a["stake_ml"],
            "cuota": a["ml_odds"] if a["ml_odds"] is not None else "N/D",
            "model_prob": round(a["prob_favorite"] * 100, 1),
            "edge": a["ml_edge_pct"],
            "ev_pct": a["ev_ml_pct"],
            "statcast": a.get("statcast", {}),
        })

    picks_tot = [a for a in analisis_juegos if a.get("total_pick") and a["grade_total"] != "D"]
    picks_tot = filtrar_matchups_unicos(sorted(picks_tot, key=lambda x: (x["score_total"], x["ev_total_pct"]), reverse=True))
    for a in picks_tot[:5]:
        data["apuestas"]["totales_ev"].append({
            "game": a["game"],
            "matchup_key": a["matchup_key"],
            "pick": a["total_pick"]["pick"],
            "grade": a["grade_total"],
            "stake": a["stake_total"],
            "cuota": a["total_odds"] if a["total_odds"] is not None else "N/D",
            "projection": a["total_projection"],
            "line": a["total_line"],
            "edge": a["total_edge"],
            "ev_pct": a["ev_total_pct"],
        })

    for a in picks_modelo[:5]:
        data["apuestas"]["modelo"].append({
            "game": a["game"],
            "matchup_key": a["matchup_key"],
            "pick": a["ml_pick"],
            "confianza": a["confidence_pct"],
            "prob_home_pct": a["prob_home"],
            "prob_away_pct": a["prob_away"],
            "total_proyectado": a["total_projection"],
        })

    parley_existente = buscar_parley_del_dia("parley")
    if parley_existente:
        data["parley"] = parley_existente.get("legs", [])
    else:
        sel_p, nivel_p = calcular_parley_del_dia(analisis_juegos)
        data["parley"] = [{**p, "nivel": nivel_p} for p in sel_p]

    mill_existente = buscar_parley_del_dia("parley_millonario")
    if mill_existente:
        data["parley_millonario"] = mill_existente.get("legs", [])
    else:
        sel_p, _ = calcular_parley_del_dia(analisis_juegos)
        bloqueados = {p["matchup_key"] for p in sel_p}
        sel_m, nivel_m, _ = calcular_parley_millonario(analisis_juegos, bloqueados)
        data["parley_millonario"] = [{**p, "nivel": nivel_m} for p in sel_m]

    return data

# =========================================================
# CALLBACKS
# =========================================================
@bot.callback_query_handler(func=lambda call: call.data.startswith("cmd_"))
def callback_menu(call):
    try:
        try:
            bot.answer_callback_query(call.id)
        except Exception:
            pass

        handlers = {
            "cmd_hoy": hoy,
            "cmd_posiciones": posiciones,
            "cmd_apuestas": apuestas,
            "cmd_parley": parley,
            "cmd_parley_millonario": parley_millonario,
            "cmd_pitchers": pitchers,
            "cmd_pronosticos": pronosticos,
            "cmd_lesionados": lesionados,
            "cmd_roi": roi,
            "cmd_exportar_json": exportar_json,
            "cmd_stats_parlays": stats_parleys,
            "cmd_parley_ganado": parley_ganado,
            "cmd_parley_fallado": parley_fallado,
            "cmd_millonario_ganado": millonario_ganado,
            "cmd_millonario_fallado": millonario_fallado,
            "cmd_reset_parley": reset_parley,
            "cmd_reset_millonario": reset_millonario,
        }
        fn = handlers.get(call.data)
        if fn:
            fn(call.message)
    except Exception as e:
        try:
            bot.send_message(call.message.chat.id, f"❌ Error en botón: {str(e)[:120]}")
        except Exception:
            pass

# =========================================================
# COMANDOS
# =========================================================
@bot.message_handler(commands=["start"])
def start(message):
    texto = (
        "⚾ <b>MLB PRO BOT</b>\n"
        "━━━━━━━━━━━━━━━━━━\n"
        "Predicciones MLB con modelo probabilístico.\n\n"
        "• Moneyline con edge y EV\n"
        "• Totales proyectados\n"
        "• FIP + ERA real + clima\n"
        "• Statcast Edge: xwOBA, hard-hit y whiff%\n"
        "• Parley con relleno final\n"
        "• Parley millonario flexible\n\n"
        f"🧪 Versión activa: <b>{BOT_VERSION}</b>\n"
        f"🧬 Statcast Edge: <b>{statcast_status_text()}</b>\n\n"
        "Selecciona una opción:"
    )
    bot.send_message(message.chat.id, texto, parse_mode="HTML", reply_markup=menu_markup())


@bot.message_handler(commands=["hoy"])
def hoy(message):
    msg = bot.reply_to(message, "📅 Cargando juegos del día...")
    try:
        games = obtener_juegos_del_dia()
        fecha = hoy_str()
        if not games:
            bot.edit_message_text(f"📅 JUEGOS DE HOY ({fecha})\n\nNo hay juegos programados hoy.", msg.chat.id, msg.message_id)
            return

        juegos = []
        for g in games:
            teams = g.get("teams", {})
            away = teams.get("away", {}).get("team", {}).get("name", "TBD")
            home = teams.get("home", {}).get("team", {}).get("name", "TBD")
            sa = teams.get("away", {}).get("score", "-")
            sh = teams.get("home", {}).get("score", "-")
            status = g.get("status", {}).get("detailedState", "Estado desconocido")
            game_date = g.get("gameDate", "")
            try:
                dt_utc = datetime.fromisoformat(game_date.replace("Z", "+00:00"))
                dt_ve = dt_utc.astimezone(ZoneInfo("America/Caracas")) if ZoneInfo else dt_utc - timedelta(hours=4)
                hora_orden = dt_ve
                hora_txt = dt_ve.strftime("%I:%M %p")
            except Exception:
                hora_orden = None
                hora_txt = "Hora no disponible"
            juegos.append({"away": away, "home": home, "sa": sa, "sh": sh, "status": status, "hora_txt": hora_txt, "hora_orden": hora_orden})

        juegos.sort(key=lambda x: x["hora_orden"] if x["hora_orden"] else datetime.max)
        texto = header("JUEGOS DE HOY", "📅")
        texto += f"🗓️ {fecha} | Hora de Venezuela\n\n"
        for i, j in enumerate(juegos, 1):
            texto += card_game(f"{i}. {j['away']} @ {j['home']}", [f"🕒 {j['hora_txt']} VET", f"📌 {j['status']}", f"⚾️ Score: {j['sa']} - {j['sh']}"])

        bot.delete_message(msg.chat.id, msg.message_id)
        responder_largo(message.chat.id, texto, parse_mode="HTML")
    except Exception as e:
        bot.edit_message_text(f"❌ Error al cargar juegos: {str(e)[:120]}", msg.chat.id, msg.message_id)


@bot.message_handler(commands=["posiciones"])
def posiciones(message):
    msg = bot.reply_to(message, "🏆 Cargando standings...")
    try:
        season = temporada_actual()
        data = safe_get(
            f"{MLB_BASE}/standings",
            params={"leagueId": "103,104", "season": season, "standingsTypes": "regularSeason", "hydrate": "team,league,division"},
        )
        records = data.get("records", [])
        if not records:
            bot.edit_message_text("❌ No pude cargar los standings.", msg.chat.id, msg.message_id)
            return

        bloques = []
        titulo = f"🏆 <b>STANDINGS MLB {season}</b>\n"
        for record in records:
            league_name = record.get("league", {}).get("name", "League")
            division_name = record.get("division", {}).get("name", "División")
            if "Spring" in division_name or "Wild Card" in division_name:
                continue
            lineas = [
                f"{league_name} - {division_name}",
                "",
                "Team                 W   L   PCT   GB   HOME   AWAY   L10   STRK",
                "---------------------------------------------------------------",
            ]
            for team in record.get("teamRecords", []):
                nombre = team.get("team", {}).get("name", "")
                wins = team.get("wins", 0)
                losses = team.get("losses", 0)
                pct = team.get("pct", "---")
                gb = str(team.get("gamesBack", "-"))
                hw, hl = _extraer_split(team, "home")
                aw, al = _extraer_split(team, "away")
                lw, ll = _extraer_split(team, "lastTen")
                home_str = f"{hw if hw is not None else team.get('homeWins', 0)}-{hl if hl is not None else team.get('homeLosses', 0)}"
                away_str = f"{aw if aw is not None else team.get('awayWins', 0)}-{al if al is not None else team.get('awayLosses', 0)}"
                l10_str = f"{lw if lw is not None else team.get('lastTenWins', 0)}-{ll if ll is not None else team.get('lastTenLosses', 0)}"
                strk = str(team.get("streakCode", "-"))
                fila = (
                    f"{nombre[:20].ljust(20)} "
                    f"{str(wins).rjust(3)} "
                    f"{str(losses).rjust(3)} "
                    f"{str(pct).rjust(5)} "
                    f"{gb.rjust(4)} "
                    f"{home_str.rjust(6)} "
                    f"{away_str.rjust(6)} "
                    f"{l10_str.rjust(5)} "
                    f"{strk.rjust(5)}"
                )
                lineas.append(fila)
            bloques.append("<pre>" + "\n".join(lineas) + "</pre>")

        bot.delete_message(msg.chat.id, msg.message_id)
        bot.send_message(message.chat.id, titulo, parse_mode="HTML")
        for bloque in bloques:
            bot.send_message(message.chat.id, bloque, parse_mode="HTML")
    except Exception as e:
        bot.edit_message_text(f"❌ Error al cargar posiciones: {str(e)[:120]}", msg.chat.id, msg.message_id)


@bot.message_handler(commands=["pronosticos"])
def pronosticos(message):
    msg = bot.reply_to(message, "📊 Generando pronósticos del modelo...")
    try:
        analisis_juegos = obtener_analisis_del_dia()
        texto = header("PRONÓSTICOS DEL MODELO", "📊")
        texto += f"📅 {hoy_str()}\n\n"
        if not analisis_juegos:
            texto += "No hay juegos hoy."
            bot.edit_message_text(texto, msg.chat.id, msg.message_id, parse_mode="HTML")
            return

        picks = filtrar_matchups_unicos(sorted(analisis_juegos, key=lambda x: (x["confidence_pct"], x["score_ml"]), reverse=True))
        for a in picks[:8]:
            fav = a["favorite"]
            und = a["home"] if fav == a["away"] else a["away"]
            p_fav = a["confidence_pct"]
            p_und = round(100 - p_fav, 1)
            clima_txt = "Techo" if a["clima"].get("techo") else f"{a['clima'].get('temp_c')}°C | 💨 {a['clima'].get('wind_kmh')} km/h"
            texto += card_game(
                f"{a['away']} @ {a['home']}",
                [
                    f"🎯 Pick: <b>{a['ml_pick']}</b>",
                    f"🧠 <b>{fav}</b>: {p_fav}% | <b>{und}</b>: {p_und}%",
                    f"💵 Cuota: <b>{a['ml_odds'] if a['ml_odds'] is not None else 'N/D'}</b>",
                    f"📊 Total proyectado: <b>{a['total_projection']}</b>",
                    f"🎽 {a['pitchers']['away']} vs {a['pitchers']['home']}",
                    f"🧬 {a['statcast']['away']}",
                    f"🧬 {a['statcast']['home']}",
                    f"🌡️ {clima_txt}",
                ],
            )
        bot.edit_message_text(texto, msg.chat.id, msg.message_id, parse_mode="HTML")
    except Exception as e:
        bot.edit_message_text(f"❌ Error: {str(e)[:120]}", msg.chat.id, msg.message_id)


@bot.message_handler(commands=["apuestas"])
def apuestas(message):
    msg = bot.reply_to(message, "🔥 Analizando juegos con EV + stake automático...")
    try:
        analisis_juegos = obtener_analisis_del_dia()
        texto = header("APUESTAS PRO MLB", "💰")
        texto += f"📅 {hoy_str()}\n\n"
        if not analisis_juegos:
            texto += "No hay juegos hoy."
            bot.edit_message_text(texto, msg.chat.id, msg.message_id, parse_mode="HTML")
            return

        picks_ml = [a for a in analisis_juegos if a["grade_ml"] != "D" and not a["risk_flags"].get("tbd_pitcher")]
        picks_ml = filtrar_matchups_unicos(sorted(picks_ml, key=lambda x: (x["score_ml"], x["ev_ml_pct"]), reverse=True))
        picks_tot = [a for a in analisis_juegos if a.get("total_pick") and a["grade_total"] != "D"]
        picks_tot = filtrar_matchups_unicos(sorted(picks_tot, key=lambda x: (x["score_total"], x["ev_total_pct"]), reverse=True))
        picks_mod = [a for a in analisis_juegos if not a["risk_flags"].get("tbd_pitcher")]
        picks_mod = filtrar_matchups_unicos(sorted(picks_mod, key=lambda x: (x["confidence_pct"], x["score_ml"]), reverse=True))

        texto += "💰 <b>MONEYLINE CON EV+</b>\n━━━━━━━━━━━━━━━━━━\n\n"
        if picks_ml:
            for a in picks_ml[:5]:
                texto += card_game(
                    f"{a['away']} @ {a['home']}",
                    [
                        f"🎯 Pick: <b>{a['ml_pick']}</b>",
                        f"🏷️ Grade: <b>{a['grade_ml']}</b> | Stake: <b>{a['stake_ml']}</b>",
                        f"💵 Cuota: <b>{a['ml_odds'] if a['ml_odds'] is not None else 'N/D'}</b>",
                        f"🧠 Modelo: <b>{round(a['prob_favorite'] * 100, 1)}%</b> | Score: <b>{a['score_ml']}/100</b>",
                        f"📈 Edge: <b>{a['ml_edge_pct']:+.1f}%</b> | EV: <b>{a['ev_ml_pct']:+.1f}%</b>",
                        f"🎽 {a['pitchers']['away']} vs {a['pitchers']['home']}",
                        f"🧬 Statcast: <b>{a['statcast']['status']}</b> | Ventaja: <b>{a['statcast']['advantage_home']:+.3f}</b>",
                    ],
                )
        else:
            texto += "No hubo moneylines con EV positivo hoy.\n\n"

        texto += "📊 <b>TOTALES CON EV+</b>\n━━━━━━━━━━━━━━━━━━\n\n"
        if picks_tot:
            for a in picks_tot[:5]:
                texto += card_game(
                    f"{a['away']} @ {a['home']}",
                    [
                        f"🎯 Pick: <b>{a['total_pick']['pick']}</b>",
                        f"🏷️ Grade: <b>{a['grade_total']}</b> | Stake: <b>{a['stake_total']}</b>",
                        f"💵 Cuota: <b>{a['total_odds'] if a['total_odds'] is not None else 'N/D'}</b>",
                        f"📊 Proyección: {a['total_projection']} | Línea: {a['total_line']}",
                        f"📈 Edge: <b>{a['total_edge']:.2f}</b> | EV: <b>{a['ev_total_pct']:+.1f}%</b>",
                    ],
                )
        else:
            texto += "No hubo totales con EV positivo hoy.\n\n"

        texto += "🧠 <b>PICKS DEL MODELO</b>\n━━━━━━━━━━━━━━━━━━\n\n"
        if picks_mod:
            for a in picks_mod[:5]:
                texto += card_game(
                    f"{a['away']} @ {a['home']}",
                    [
                        f"🎯 Pick: <b>{a['ml_pick']}</b>",
                        f"🧠 Confianza: <b>{a['confidence_pct']}%</b> | Score: <b>{a['score_ml']}/100</b>",
                        f"💵 Cuota: <b>{a['ml_odds'] if a['ml_odds'] is not None else 'N/D'}</b>",
                        f"📊 Total proyectado: <b>{a['total_projection']}</b>",
                        f"🧬 Statcast: <b>{a['statcast']['status']}</b>",
                    ],
                )
        else:
            texto += "No se pudieron generar picks del modelo."

        bot.delete_message(msg.chat.id, msg.message_id)
        responder_largo(message.chat.id, texto, parse_mode="HTML")
    except Exception as e:
        bot.edit_message_text(f"❌ Error en /apuestas: {str(e)[:200]}", msg.chat.id, msg.message_id)


@bot.message_handler(commands=["parley", "parley_del_dia"])
def parley(message):
    msg = bot.reply_to(message, "🎯 Construyendo parley del día...")
    try:
        existente = buscar_parley_del_dia("parley")
        if existente:
            count = len(existente.get("legs", []))
            titulo = "PARLEY DEL DÍA (FIJO)" if count >= 3 else "PARLEY REDUCIDO / PICK SIMPLE (FIJO)"
            texto = _formatear_parley_guardado(existente, "🎯", titulo)
            bot.edit_message_text(texto, msg.chat.id, msg.message_id, parse_mode="HTML")
            return

        analisis_juegos = obtener_analisis_del_dia()
        seleccionados, nivel = calcular_parley_del_dia(analisis_juegos, target=3, max_nd=1, debug=True)
        count = len(seleccionados)
        if count >= 3:
            titulo = "PARLEY DEL DÍA MLB"
        elif count == 2:
            titulo = "PARLEY REDUCIDO / 2 LEGS"
        elif count == 1:
            titulo = "PICK SIMPLE DEL MODELO"
        else:
            titulo = "PARLEY DEL DÍA MLB"

        texto = header(titulo, "🎯")
        texto += f"📅 {hoy_str()}\n"
        texto += f"🧩 Nivel: <b>{nivel}</b>\n\n"

        if not seleccionados:
            texto += "No hay juegos suficientes hoy."
        else:
            if count == 1:
                texto += "⚠️ Solo salió 1 pick apto. No lo trates como parley.\n\n"
            elif count == 2:
                texto += "⚠️ Parley reducido: solo 2 legs disponibles.\n\n"

            cuota_total, tiene_nd = _calcular_cuota_parlay(seleccionados, fallback_dec=1.90)
            for p in seleccionados:
                texto += card_game(
                    p["game"],
                    [
                        f"🎯 {p['tipo']}: <b>{p['pick']}</b>",
                        f"🧠 Confianza: <b>{p['confidence']}%</b>",
                        f"📈 Edge: <b>{p['edge']:+.2f}%</b> | EV: <b>{p.get('ev_pct', 0.0):+.1f}%</b>",
                        f"💵 Cuota: <b>{p['cuota']}</b>",
                        f"🧩 Fase: <b>{p.get('fase', 'N/D')}</b>",
                    ],
                )

            nd_nota = " ⚠️ incluye picks sin cuota real" if tiene_nd else ""
            ganancia = round((cuota_total - 1) * 100)
            texto += f"\n💰 <b>Cuota estimada: ~{cuota_total}x</b>{nd_nota}\n"
            texto += f"📈 $100 → ganancia neta estimada: <b>${ganancia}</b>\n"

            registrar_parley_del_dia("parley", seleccionados, cuota_total=cuota_total, nivel=nivel)

        bot.edit_message_text(texto, msg.chat.id, msg.message_id, parse_mode="HTML")
    except Exception as e:
        import traceback
        print(traceback.format_exc())
        bot.edit_message_text(f"❌ Error en /parley: {str(e)[:180]}", msg.chat.id, msg.message_id)


@bot.message_handler(commands=["parley_millonario"])
def parley_millonario(message):
    msg = bot.reply_to(message, "💎 Construyendo parley millonario del día...")
    try:
        existente = buscar_parley_del_dia("parley_millonario")
        if existente:
            texto = _formatear_parley_guardado(existente, "💎", "PARLEY MILLONARIO (FIJO)")
            bot.edit_message_text(texto, msg.chat.id, msg.message_id, parse_mode="HTML")
            return

        analisis_juegos = obtener_analisis_del_dia()
        parley_mem, _ = calcular_parley_del_dia(analisis_juegos, target=3, max_nd=1, debug=False)
        bloqueados = {p["matchup_key"] for p in parley_mem}
        seleccionados, nivel, uso_fallback = calcular_parley_millonario(analisis_juegos, bloqueados, target=5, max_nd=2, debug=True)

        texto = header("PARLEY MILLONARIO (ALTO RIESGO)", "💎")
        texto += f"📅 {hoy_str()}\n"
        texto += f"🧩 Nivel: <b>{nivel}</b>\n\n"

        if not seleccionados:
            texto += "No hay juegos disponibles hoy para el millonario."
        else:
            if uso_fallback or nivel in ["emergencia", "relleno_final", "millonario_reducido"]:
                texto += "⚠️ Se usó fallback agresivo del modelo. Alto riesgo.\n\n"
            if len(seleccionados) < 5:
                texto += f"⚠️ Millonario reducido: solo {len(seleccionados)} legs disponibles.\n\n"

            cuota_total, tiene_nd = _calcular_cuota_parlay(seleccionados, fallback_dec=2.05)
            for p in seleccionados:
                tipo_icon = "🎯" if p["tipo"] == "ML" else "📊"
                texto += card_game(
                    p["game"],
                    [
                        f"{tipo_icon} {p['tipo']}: <b>{p['pick']}</b>",
                        f"🧠 Confianza: <b>{p['confidence']}%</b>",
                        f"📈 Edge: <b>{p['edge']:+.2f}</b> | EV: <b>{p.get('ev_pct', 0.0):+.1f}%</b>",
                        f"💵 Cuota: <b>{p['cuota']}</b>",
                        f"🧩 Fase: <b>{p.get('fase', 'N/D')}</b>",
                    ],
                )

            nd_nota = " ⚠️ incluye picks sin cuota real" if tiene_nd else ""
            ganancia = round((cuota_total - 1) * 100)
            texto += f"\n💰 <b>Cuota estimada: ~{cuota_total}x</b>{nd_nota}\n"
            texto += f"📈 $100 → ganancia neta estimada: <b>${ganancia}</b>\n"
            texto += "⚠️ <i>Alto riesgo — apuesta solo lo que puedes perder.</i>\n"

            registrar_parley_del_dia("parley_millonario", seleccionados, cuota_total=cuota_total, nivel=nivel)

        bot.edit_message_text(texto, msg.chat.id, msg.message_id, parse_mode="HTML")
    except Exception as e:
        import traceback
        print(traceback.format_exc())
        bot.edit_message_text(f"❌ Error en /parley_millonario: {str(e)[:180]}", msg.chat.id, msg.message_id)


@bot.message_handler(commands=["pitchers"])
def pitchers(message):
    msg = bot.reply_to(message, "🧢 Cargando pitchers probables...")
    try:
        analisis_juegos = obtener_analisis_del_dia()
        texto = header("PITCHERS PROBABLES", "🧢")
        texto += f"📅 {hoy_str()}\n\n"
        if not analisis_juegos:
            texto += "No hay juegos programados hoy."
            bot.edit_message_text(texto, msg.chat.id, msg.message_id, parse_mode="HTML")
            return
        for a in analisis_juegos:
            as_ = a["pitcher_stats"]["away"]
            hs_ = a["pitcher_stats"]["home"]
            texto += card_game(
                f"{a['away']} @ {a['home']}",
                [
                    f"🛣️ {abreviar_equipo(a['away'])}: <b>{a['pitchers']['away']}</b> | ERA {as_['era']} | FIP {as_['fip']} | WHIP {as_['whip']}",
                    f"🧬 {resumen_statcast_pitcher(as_)}",
                    f"🏠 {abreviar_equipo(a['home'])}: <b>{a['pitchers']['home']}</b> | ERA {hs_['era']} | FIP {hs_['fip']} | WHIP {hs_['whip']}",
                    f"🧬 {resumen_statcast_pitcher(hs_)}",
                ],
            )
        bot.delete_message(msg.chat.id, msg.message_id)
        responder_largo(message.chat.id, texto, parse_mode="HTML")
    except Exception as e:
        bot.edit_message_text(f"❌ Error en /pitchers: {str(e)[:120]}", msg.chat.id, msg.message_id)


@bot.message_handler(commands=["lesionados"])
def lesionados(message):
    msg = bot.reply_to(message, "🚨 Cargando lesionados...")
    try:
        transactions = obtener_transacciones_hoy()
        texto = header("LESIONADOS / IL RECIENTES", "🚨")
        count = 0
        for t in transactions:
            desc = str(t.get("description", ""))
            lower = desc.lower()
            if any(x in lower for x in ["injured", " il", "injury", "60-day", "15-day", "10-day", "placed on"]):
                texto += f"• {desc}\n"
                count += 1
                if count >= 15:
                    break
        if count == 0:
            texto += "No encontré movimientos importantes de IL recientemente."
        bot.delete_message(msg.chat.id, msg.message_id)
        responder_largo(message.chat.id, texto, parse_mode="HTML")
    except Exception as e:
        bot.edit_message_text(f"❌ Error al cargar lesionados: {str(e)[:120]}", msg.chat.id, msg.message_id)


@bot.message_handler(commands=["lineups"])
def lineups(message):
    texto = (
        "📋 <b>LINEUPS</b>\n"
        "━━━━━━━━━━━━━━━━━━\n\n"
        "Las alineaciones oficiales suelen salir entre 1 y 2 horas antes del primer juego.\n\n"
        "Mientras tanto usa:\n"
        "• /pitchers\n"
        "• /lesionados\n"
        "• /hoy"
    )
    bot.reply_to(message, texto, parse_mode="HTML")


@bot.message_handler(commands=["exportar_json"])
def exportar_json(message):
    msg = bot.reply_to(message, "📦 Generando archivo JSON maestro para TikTok...")
    try:
        data = generar_dataset_tiktok()
        ruta = guardar_json_tiktok(data)
        if not ruta:
            bot.edit_message_text("❌ No se pudo guardar el archivo JSON.", msg.chat.id, msg.message_id)
            return
        texto = (
            "✅ <b>JSON maestro generado correctamente</b>\n\n"
            f"📅 Fecha: <b>{data['fecha']}</b>\n"
            f"🤖 Versión: <b>{data['bot_version']}</b>\n"
            f"⚾ Juegos del día: <b>{len(data['juegos_del_dia'])}</b>\n"
            f"📊 Pronósticos: <b>{len(data['pronosticos'])}</b>\n"
            f"💰 ML EV+: <b>{len(data['apuestas']['moneyline_ev'])}</b>\n"
            f"📈 Totales EV+: <b>{len(data['apuestas']['totales_ev'])}</b>\n"
            f"🎯 Parley: <b>{len(data['parley'])}</b>\n"
            f"💎 Millonario: <b>{len(data['parley_millonario'])}</b>\n\n"
            f"📂 Guardado en:\n<code>{ruta}</code>"
        )
        bot.edit_message_text(texto, msg.chat.id, msg.message_id, parse_mode="HTML")
    except Exception as e:
        import traceback
        print(traceback.format_exc())
        bot.edit_message_text(f"❌ Error en /exportar_json: {str(e)[:180]}", msg.chat.id, msg.message_id)


@bot.message_handler(commands=["roi"])
def roi(message):
    try:
        if not os.path.exists(RESULTADOS_CSV):
            bot.reply_to(message, "No existe el archivo de resultados todavía.")
            return

        total_apuestas = 0
        total_unidades = 0.0
        total_profit = 0.0
        ganadas = 0
        perdidas = 0

        with open(RESULTADOS_CSV, "r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                resultado = str(row.get("resultado", "")).strip().lower()
                profit = row.get("profit", "")
                stake = row.get("stake", "")
                if resultado in ["win", "lose"] and profit not in ["", None]:
                    total_apuestas += 1
                    total_unidades += extraer_unidades(stake)
                    total_profit += float(profit)
                    if resultado == "win":
                        ganadas += 1
                    elif resultado == "lose":
                        perdidas += 1

        if total_apuestas == 0 or total_unidades == 0:
            bot.reply_to(message, "Todavía no hay apuestas cerradas en el CSV para calcular ROI.")
            return

        roi_pct = round((total_profit / total_unidades) * 100, 2)
        hit_rate = round((ganadas / total_apuestas) * 100, 2)
        texto = header("RESUMEN ROI", "📈")
        texto += (
            f"🎯 Apuestas cerradas: <b>{total_apuestas}</b>\n"
            f"✅ Ganadas: <b>{ganadas}</b>\n"
            f"❌ Perdidas: <b>{perdidas}</b>\n"
            f"📊 Hit Rate: <b>{hit_rate}%</b>\n"
            f"💵 Unidades arriesgadas: <b>{round(total_unidades, 2)}u</b>\n"
            f"💰 Profit neto: <b>{round(total_profit, 2)}u</b>\n"
            f"🚀 ROI: <b>{roi_pct}%</b>\n"
        )
        bot.reply_to(message, texto, parse_mode="HTML")
    except Exception as e:
        bot.reply_to(message, f"❌ Error calculando ROI: {str(e)[:120]}")


@bot.message_handler(commands=["historial"])
def historial(message):
    parleys = cargar_parleys_diarios()
    texto = header("HISTORIAL DE PARLEYS", "📚")
    if not parleys:
        texto += "No hay historial registrado todavía."
        bot.reply_to(message, texto, parse_mode="HTML")
        return

    stats = {
        "parley": {"ganado": 0, "fallado": 0, "pendiente": 0},
        "parley_millonario": {"ganado": 0, "fallado": 0, "pendiente": 0},
    }
    for p in parleys:
        tipo = p.get("tipo")
        estado = p.get("estado", "pendiente")
        if tipo in stats and estado in stats[tipo]:
            stats[tipo][estado] += 1

    def efectividad(tipo):
        gan = stats[tipo]["ganado"]
        fal = stats[tipo]["fallado"]
        total = gan + fal
        return round((gan / total) * 100, 1) if total else 0

    texto += (
        f"🎯 Parley: ✅ {stats['parley']['ganado']} | ❌ {stats['parley']['fallado']} | ⏳ {stats['parley']['pendiente']} | Efectividad: <b>{efectividad('parley')}%</b>\n"
        f"💎 Millonario: ✅ {stats['parley_millonario']['ganado']} | ❌ {stats['parley_millonario']['fallado']} | ⏳ {stats['parley_millonario']['pendiente']} | Efectividad: <b>{efectividad('parley_millonario')}%</b>\n\n"
        f"🧾 <b>Últimos registros</b>\n\n"
    )

    for item in sorted(parleys, key=lambda x: x.get("fecha", ""), reverse=True)[:12]:
        texto += f"📅 <b>{item.get('fecha', 'N/D')}</b> | {item.get('tipo', 'N/D')} | Estado: <b>{item.get('estado', 'pendiente')}</b> | Nivel: <b>{item.get('nivel', 'N/D')}</b>\n"
        for leg in item.get("legs", []):
            texto += f"• {leg.get('game', 'N/D')} → {leg.get('pick', 'N/D')}\n"
        texto += "\n"

    responder_largo(message.chat.id, texto, parse_mode="HTML")


@bot.message_handler(commands=["stats_parleys"])
def stats_parleys(message):
    parleys = cargar_parleys_diarios()
    stats = {"parley": {"ganado": 0, "fallado": 0}, "parley_millonario": {"ganado": 0, "fallado": 0}}
    for p in parleys:
        tipo = p.get("tipo")
        estado = p.get("estado")
        if tipo in stats and estado in ["ganado", "fallado"]:
            stats[tipo][estado] += 1

    total_parley = stats["parley"]["ganado"] + stats["parley"]["fallado"]
    total_millonario = stats["parley_millonario"]["ganado"] + stats["parley_millonario"]["fallado"]
    efectividad_parley = round((stats["parley"]["ganado"] / total_parley) * 100, 2) if total_parley > 0 else 0
    efectividad_millonario = round((stats["parley_millonario"]["ganado"] / total_millonario) * 100, 2) if total_millonario > 0 else 0

    texto = header("ESTADÍSTICAS DE PARLEYS", "📊")
    texto += (
        f"🎯 <b>Parley diario</b>\n"
        f"✅ Ganados: <b>{stats['parley']['ganado']}</b>\n"
        f"❌ Fallados: <b>{stats['parley']['fallado']}</b>\n"
        f"📈 Efectividad: <b>{efectividad_parley}%</b>\n\n"
        f"💎 <b>Parley millonario</b>\n"
        f"✅ Ganados: <b>{stats['parley_millonario']['ganado']}</b>\n"
        f"❌ Fallados: <b>{stats['parley_millonario']['fallado']}</b>\n"
        f"📈 Efectividad: <b>{efectividad_millonario}%</b>\n"
    )
    bot.reply_to(message, texto, parse_mode="HTML")


@bot.message_handler(commands=["reset_parley"])
def reset_parley(message):
    try:
        borrado = eliminar_parley_del_dia("parley")
        bot.reply_to(message, "♻️ Parley del día reiniciado correctamente." if borrado else "No había parley del día guardado hoy.")
    except Exception as e:
        bot.reply_to(message, f"❌ Error al resetear parley: {str(e)[:120]}")


@bot.message_handler(commands=["reset_millonario"])
def reset_millonario(message):
    try:
        borrado = eliminar_parley_del_dia("parley_millonario")
        bot.reply_to(message, "♻️ Parley millonario reiniciado correctamente." if borrado else "No había parley millonario guardado hoy.")
    except Exception as e:
        bot.reply_to(message, f"❌ Error al resetear millonario: {str(e)[:120]}")


@bot.message_handler(commands=["parley_ganado"])
def parley_ganado(message):
    ok = actualizar_estado_parley(hoy_str(), "parley", "ganado")
    bot.reply_to(message, "✅ Parley del día marcado como GANADO." if ok else "❌ No encontré parley del día para marcar.")


@bot.message_handler(commands=["parley_fallado"])
def parley_fallado(message):
    ok = actualizar_estado_parley(hoy_str(), "parley", "fallado")
    bot.reply_to(message, "❌ Parley del día marcado como FALLADO." if ok else "❌ No encontré parley del día para marcar.")


@bot.message_handler(commands=["millonario_ganado"])
def millonario_ganado(message):
    ok = actualizar_estado_parley(hoy_str(), "parley_millonario", "ganado")
    bot.reply_to(message, "✅ Parley millonario marcado como GANADO." if ok else "❌ No encontré parley millonario del día.")


@bot.message_handler(commands=["millonario_fallado"])
def millonario_fallado(message):
    ok = actualizar_estado_parley(hoy_str(), "parley_millonario", "fallado")
    bot.reply_to(message, "❌ Parley millonario marcado como FALLADO." if ok else "❌ No encontré parley millonario del día.")

# =========================================================
# INICIO
# =========================================================
if __name__ == "__main__":
    print(f"INICIANDO BOT {BOT_VERSION}")
    bot.remove_webhook()
    time.sleep(1)
    bot.infinity_polling(
        skip_pending=True,
        timeout=30,
        long_polling_timeout=30,
        allowed_updates=["message", "callback_query"],
    )
