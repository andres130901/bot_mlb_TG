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
# VERSION
# =========================================================
BOT_VERSION = "V7_2_MILL_5PICKS"

# =========================================================
# CONFIG
# =========================================================
load_dotenv()

TOKEN        = os.getenv("TOKEN")
ODDS_API_KEY = os.getenv("ODDS_API_KEY", "").strip()

if not TOKEN:
    raise ValueError("Falta TOKEN en tu archivo .env")

bot = telebot.TeleBot(TOKEN, parse_mode=None)

MLB_BASE            = "https://statsapi.mlb.com/api/v1"
HISTORIAL_FILE      = "historial_parlays.json"
RESULTADOS_CSV      = "resultados_apuestas.csv"
PARLEYS_DIARIOS_FILE= "parleys_diarios.json"
REQUEST_TIMEOUT     = 20

# =========================================================
# CONSTANTES
# =========================================================

# Venue IDs de estadios cerrados o con techo retráctil (clima irrelevante)
VENUE_IDS_TECHO = {
    12,    # Tropicana Field (Rays)
    2392,  # Globe Life Field (Rangers)
    2394,  # Rogers Centre (Blue Jays)
    680,   # Minute Maid Park (Astros)
    3289,  # loanDepot park (Marlins)
    32,    # Chase Field (D-backs)
    2395,  # American Family Field (Brewers)
}

BOOKMAKERS_PRIORITARIOS = [
    "DraftKings", "FanDuel", "BetMGM", "Caesars",
    "PointsBet", "BetRivers", "Unibet", "William Hill",
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
    except Exception:
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
            writer.writerow([
                "fecha", "juego", "tipo_apuesta", "pick", "cuota",
                "prob_modelo", "prob_implicita", "edge", "stake",
                "grade", "resultado", "profit",
            ])
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
    except Exception:
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


def registrar_parley_del_dia(tipo, legs, cuota_total=None, fecha=None):
    if fecha is None:
        fecha = hoy_str()
    if buscar_parley_del_dia(tipo, fecha):
        return buscar_parley_del_dia(tipo, fecha)
    data  = cargar_parleys_diarios()
    nuevo = {
        "fecha": fecha, "tipo": tipo, "estado": "pendiente",
        "legs": legs, "cuota_total": cuota_total,
    }
    data.append(nuevo)
    guardar_parleys_diarios(data)
    return nuevo


def actualizar_estado_parley(fecha, tipo, estado):
    data      = cargar_parleys_diarios()
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
    data   = cargar_parleys_diarios()
    nuevo  = [p for p in data if not (p.get("fecha") == fecha and p.get("tipo") == tipo)]
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
        InlineKeyboardButton("📅 Hoy",           callback_data="cmd_hoy"),
        InlineKeyboardButton("🏆 Posiciones",     callback_data="cmd_posiciones"),
        InlineKeyboardButton("💰 Apuestas",       callback_data="cmd_apuestas"),
        InlineKeyboardButton("🎯 Parley",         callback_data="cmd_parley"),
        InlineKeyboardButton("💎 Parley Mill.",   callback_data="cmd_parley_millonario"),
        InlineKeyboardButton("🧢 Pitchers",       callback_data="cmd_pitchers"),
        InlineKeyboardButton("📊 Pronósticos",    callback_data="cmd_pronosticos"),
        InlineKeyboardButton("🚨 Lesionados",     callback_data="cmd_lesionados"),
        InlineKeyboardButton("📈 ROI",            callback_data="cmd_roi"),
        InlineKeyboardButton("📦 Exportar JSON",  callback_data="cmd_exportar_json"),
        InlineKeyboardButton("📊 Stats Parlays",  callback_data="cmd_stats_parlays"),
        InlineKeyboardButton("✅ Parley G",       callback_data="cmd_parley_ganado"),
        InlineKeyboardButton("❌ Parley F",       callback_data="cmd_parley_fallado"),
        InlineKeyboardButton("💎✅ Mill G",       callback_data="cmd_millonario_ganado"),
        InlineKeyboardButton("💎❌ Mill F",       callback_data="cmd_millonario_fallado"),
        InlineKeyboardButton("♻️ Reset Parley",   callback_data="cmd_reset_parley"),
        InlineKeyboardButton("♻️ Reset Mill",     callback_data="cmd_reset_millonario"),
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
        print(f"[WARN] safe_get {url}: {e}")
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


def extraer_unidades(stake_texto):
    try:
        return float(str(stake_texto).lower().replace("u", "").strip())
    except Exception:
        return 0.0


def abreviar_equipo(nombre):
    reemplazos = {
        "New York Yankees": "Yankees", "Boston Red Sox": "Red Sox",
        "Toronto Blue Jays": "Blue Jays", "Tampa Bay Rays": "Rays",
        "Baltimore Orioles": "Orioles", "Cleveland Guardians": "Guardians",
        "Chicago White Sox": "White Sox", "Kansas City Royals": "Royals",
        "Minnesota Twins": "Twins", "Detroit Tigers": "Tigers",
        "Houston Astros": "Astros", "Seattle Mariners": "Mariners",
        "Texas Rangers": "Rangers", "Los Angeles Angels": "Angels",
        "Athletics": "Athletics", "Philadelphia Phillies": "Phillies",
        "Atlanta Braves": "Braves", "New York Mets": "Mets",
        "Miami Marlins": "Marlins", "Washington Nationals": "Nationals",
        "Chicago Cubs": "Cubs", "Milwaukee Brewers": "Brewers",
        "St. Louis Cardinals": "Cardinals", "Cincinnati Reds": "Reds",
        "Pittsburgh Pirates": "Pirates", "Los Angeles Dodgers": "Dodgers",
        "San Diego Padres": "Padres", "San Francisco Giants": "Giants",
        "Arizona Diamondbacks": "D-backs", "Colorado Rockies": "Rockies",
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
    return game_str.strip().lower()


def filtrar_matchups_unicos(items):
    """Deduplicación estándar por matchup_key — una entrada por partido."""
    vistos    = set()
    filtrados = []
    for item in items:
        clave = item.get("matchup_key") or _clave_desde_game(item.get("game", ""))
        if clave not in vistos:
            vistos.add(clave)
            filtrados.append(item)
    return filtrados


def filtrar_candidatos_millonario(items):
    """
    Deduplicación para el parley millonario.
    Clave = (matchup_key, tipo) → permite ML + TOTAL del mismo partido,
    pero nunca dos ML ni dos Totales del mismo partido.
    """
    vistos    = set()
    filtrados = []
    for item in items:
        mk    = item.get("matchup_key") or _clave_desde_game(item.get("game", ""))
        tipo  = item.get("tipo", "ML")
        clave = (mk, tipo)
        if clave not in vistos:
            vistos.add(clave)
            filtrados.append(item)
    return filtrados


def obtener_carpeta_exportacion():
    carpeta = os.path.join("exports_tiktok", hoy_str())
    os.makedirs(carpeta, exist_ok=True)
    return carpeta


def guardar_json_tiktok(data):
    carpeta = obtener_carpeta_exportacion()
    ruta    = os.path.join(carpeta, "mlb_contenido.json")
    try:
        with open(ruta, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        return ruta
    except Exception as e:
        print(f"[ERROR] guardar_json_tiktok: {e}")
        return None


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

# =========================================================
# MLB DATA
# =========================================================

def _extraer_split(team_record, tipo):
    """Lee wins/losses de splitRecords por tipo (home, away, lastTen)."""
    for s in team_record.get("splitRecords", []):
        if s.get("type") == tipo:
            return safe_int(s.get("wins"), 0), safe_int(s.get("losses"), 0)
    return None, None


def obtener_standings():
    """
    Lee standings desde la API MLB.
    Usa splitRecords para home/away/lastTen (fuente más confiable).
    Calcula runs_scored y runs_allowed por partido (RS/G, RA/G).
    """
    url    = f"{MLB_BASE}/standings"
    params = {
        "leagueId":      "103,104",
        "season":        temporada_actual(),
        "standingsTypes":"regularSeason",
        "hydrate":       "team,league,division",
    }
    data    = safe_get(url, params=params)
    equipos = {}

    for record in data.get("records", []):
        for t in record.get("teamRecords", []):
            name = t.get("team", {}).get("name")
            if not name:
                continue

            wins   = safe_int(t.get("wins"),   0)
            losses = safe_int(t.get("losses"), 0)
            games  = max(wins + losses, 1)

            # ── Home / Away ── leer desde splitRecords primero ──────────
            hw, hl = _extraer_split(t, "home")
            aw, al = _extraer_split(t, "away")

            # Fallback a campos raíz si splitRecords no los tiene
            if hw is None:
                hw = safe_int(t.get("homeWins"),   wins  // 2)
                hl = safe_int(t.get("homeLosses"), losses // 2)
            if aw is None:
                aw = safe_int(t.get("awayWins"),   wins  - (hw or 0))
                al = safe_int(t.get("awayLosses"), losses - (hl or 0))

            home_games = max((hw or 0) + (hl or 0), 1)
            away_games = max((aw or 0) + (al or 0), 1)

            # ── Last 10 ──────────────────────────────────────────────────
            lw, ll = _extraer_split(t, "lastTen")
            if lw is None:
                lw = safe_int(t.get("lastTenWins"),   5)
                ll = safe_int(t.get("lastTenLosses"), 5)
            last10_games = max((lw or 0) + (ll or 0), 1)

            # ── Runs ─────────────────────────────────────────────────────
            rs_total = safe_int(t.get("runsScored"),  0)
            ra_total = safe_int(t.get("runsAllowed"), 0)

            # RS/G y RA/G: si la API no devuelve runs, usar promedio MLB (~4.5)
            # Detectamos "no disponible" si ambos son 0 con > 5 juegos jugados
            if rs_total == 0 and games > 5:
                rs_pg = 4.50
                ra_pg = 4.50
            else:
                rs_pg = rs_total / games
                ra_pg = ra_total / games

            equipos[name] = {
                "wins":           wins,
                "losses":         losses,
                "win_pct":        wins / games,
                "home_win_pct":   (hw or 0) / home_games,
                "away_win_pct":   (aw or 0) / away_games,
                "run_diff":       rs_total - ra_total,
                "runs_scored":    rs_pg,
                "runs_allowed":   ra_pg,
                "last10_win_pct": (lw or 0) / last10_games,
                "streak":         t.get("streakCode", ""),
                "last10_record":  f"{lw}-{ll}",
            }

    return equipos


def obtener_juegos_del_dia():
    url    = f"{MLB_BASE}/schedule"
    params = {"sportId": 1, "date": hoy_str(), "hydrate": "probablePitcher,venue"}
    data   = safe_get(url, params=params)
    dates  = data.get("dates", [])
    if not dates or not isinstance(dates[0], dict):
        return []
    return dates[0].get("games", [])


def obtener_transacciones_hoy():
    url    = f"{MLB_BASE}/transactions"
    params = {
        "startDate": f"{temporada_actual()}-03-01",
        "endDate":   hoy_str(),
        "sportId":   1,
    }
    data = safe_get(url, params=params)
    return data.get("transactions", [])


@lru_cache(maxsize=256)
def obtener_stats_pitcher_reales(person_id, season=None):
    base = {
        "era": 4.20, "whip": 1.30, "so9": 8.2, "bb9": 3.2,
        "hr9": 1.2,  "fip": 4.20, "ip": 0.0,   "sample_ok": False,
    }
    if not person_id:          # FIX: no cachear None como pitcher válido
        return base
    if season is None:
        season = temporada_actual()

    url    = f"{MLB_BASE}/people/{person_id}/stats"
    params = {"stats": "season", "group": "pitching", "season": season, "gameType": "R"}
    data   = safe_get(url, params=params)

    stats_list = data.get("stats", [])
    if not stats_list or not isinstance(stats_list[0], dict):
        return base

    splits = stats_list[0].get("splits", [])
    if not splits or not isinstance(splits[0], dict):
        return base

    stat = splits[0].get("stat", {})

    era  = float(stat.get("era",  4.20) or 4.20)
    whip = float(stat.get("whip", 1.30) or 1.30)

    try:
        ip = float(str(stat.get("inningsPitched", "0")).replace(",", ""))
    except Exception:
        ip = 0.0

    k  = int(stat.get("strikeOuts",   0) or 0)
    bb = int(stat.get("baseOnBalls",  0) or 0)
    hr = int(stat.get("homeRuns",     0) or 0)

    so9 = (k  * 9 / ip) if ip > 0 else 8.2
    bb9 = (bb * 9 / ip) if ip > 0 else 3.2
    hr9 = (hr * 9 / ip) if ip > 0 else 1.2

    fip_raw = ((13 * hr + 3 * bb - 2 * k) / ip + 3.20) if ip > 0 else 4.20
    fip     = round(clamp(fip_raw, 1.50, 7.50), 2)

    return {
        "era":       round(era, 2),
        "whip":      round(whip, 2),
        "so9":       round(so9, 2),
        "bb9":       round(bb9, 2),
        "hr9":       round(hr9, 2),
        "fip":       fip,
        "ip":        round(ip, 1),
        "sample_ok": ip >= 30,
    }


@lru_cache(maxsize=128)
def obtener_venue_detalle(venue_id):
    if not venue_id:
        return {}
    data   = safe_get(f"{MLB_BASE}/venues", params={"venueIds": str(venue_id)})
    venues = data.get("venues", [])
    return venues[0] if venues else {}


@lru_cache(maxsize=128)
def geocodificar_lugar(nombre_lugar):
    if not nombre_lugar:
        return None
    data    = safe_get("https://geocoding-api.open-meteo.com/v1/search",
                       params={"name": nombre_lugar, "count": 1, "language": "en", "format": "json"})
    results = data.get("results", [])
    if not results:
        return None
    r = results[0]
    return {"latitude": r.get("latitude"), "longitude": r.get("longitude")}


def extraer_coords_venue(venue):
    if not venue:
        return None
    loc           = venue.get("location", {}) or {}
    default_coords= loc.get("defaultCoordinates", {}) or {}
    lat = default_coords.get("latitude")
    lon = default_coords.get("longitude")
    if lat is not None and lon is not None:
        return {"latitude": lat, "longitude": lon}
    query = ", ".join([x for x in [venue.get("name",""), loc.get("city",""), loc.get("stateAbbrev","")] if x])
    return geocodificar_lugar(query)


def obtener_clima_partido(game):
    try:
        venue_id = game.get("venue", {}).get("id")
        # No pedir clima para estadios con techo
        if venue_id and int(venue_id) in VENUE_IDS_TECHO:
            return {"temp_c": None, "wind_kmh": None, "precip_mm": None, "techo": True}

        venue  = obtener_venue_detalle(venue_id)
        coords = extraer_coords_venue(venue)
        if not coords:
            return {"temp_c": None, "wind_kmh": None, "precip_mm": None}

        game_date = game.get("gameDate")
        if not game_date:
            return {"temp_c": None, "wind_kmh": None, "precip_mm": None}

        dt_utc = datetime.fromisoformat(game_date.replace("Z", "+00:00"))
        data   = safe_get("https://api.open-meteo.com/v1/forecast", params={
            "latitude":     coords["latitude"],
            "longitude":    coords["longitude"],
            "hourly":       "temperature_2m,precipitation,wind_speed_10m",
            "timezone":     "auto",
            "forecast_days": 2,
        })
        hourly = data.get("hourly", {})
        times  = hourly.get("time", [])
        temps  = hourly.get("temperature_2m", [])
        precs  = hourly.get("precipitation", [])
        winds  = hourly.get("wind_speed_10m", [])

        if not times:
            return {"temp_c": None, "wind_kmh": None, "precip_mm": None}

        best_idx, min_diff = 0, None
        for i, t in enumerate(times):
            try:
                diff = abs((datetime.fromisoformat(t).replace(tzinfo=None)
                            - dt_utc.replace(tzinfo=None)).total_seconds())
                if min_diff is None or diff < min_diff:
                    min_diff, best_idx = diff, i
            except Exception:
                continue

        return {
            "temp_c":    temps[best_idx] if best_idx < len(temps) else None,
            "wind_kmh":  winds[best_idx] if best_idx < len(winds) else None,
            "precip_mm": precs[best_idx] if best_idx < len(precs) else None,
        }
    except Exception as e:
        print(f"[WARN] obtener_clima_partido: {e}")
        return {"temp_c": None, "wind_kmh": None, "precip_mm": None}

# =========================================================
# MODELO
# =========================================================

def score_pitcher_real(stats):
    """
    Score de calidad del pitcher en escala abierta (~-0.5 a +0.5).
    Pondera ERA (40%), FIP (30%), WHIP (15%), K/9 (10%), BB/9 (5%).
    Ajusta por muestra (IP) de forma gradual.
    """
    era  = stats.get("era",  4.20)
    whip = stats.get("whip", 1.30)
    so9  = stats.get("so9",  8.2)
    fip  = stats.get("fip",  4.20)
    bb9  = stats.get("bb9",  3.2)
    hr9  = stats.get("hr9",  1.2)
    ip   = stats.get("ip",   0.0)

    score = 0.0

    if   era <= 2.80: score += 0.30
    elif era <= 3.30: score += 0.20
    elif era <= 3.80: score += 0.10
    elif era <= 4.20: score += 0.02
    elif era >  5.00: score -= 0.18
    else:             score -= 0.08

    if   fip <= 2.90: score += 0.22
    elif fip <= 3.40: score += 0.15
    elif fip <= 3.90: score += 0.07
    elif fip <= 4.30: score += 0.00
    elif fip >  5.00: score -= 0.15
    else:             score -= 0.07

    if   whip <= 1.00: score += 0.14
    elif whip <= 1.15: score += 0.09
    elif whip <= 1.28: score += 0.03
    elif whip >  1.45: score -= 0.12

    if   so9 >= 11.0: score += 0.10
    elif so9 >=  9.5: score += 0.07
    elif so9 >=  8.0: score += 0.03
    elif so9 <   6.5: score -= 0.06

    if   bb9 <= 2.0: score += 0.06
    elif bb9 <= 2.8: score += 0.03
    elif bb9 >= 4.5: score -= 0.08

    if   hr9 <= 0.8: score += 0.04
    elif hr9 >= 1.8: score -= 0.07

    # Peso por tamaño de muestra (gradual)
    if   ip >= 100: mult = 1.00
    elif ip >=  60: mult = 0.90
    elif ip >=  30: mult = 0.78
    elif ip >=  15: mult = 0.60
    else:           mult = 0.38

    return round(score * mult, 3)


def ajuste_clima_total(weather, venue_id=None):
    """Ajuste al total proyectado por condiciones climáticas. Ignora estadios con techo."""
    if venue_id and safe_int(venue_id, 0) in VENUE_IDS_TECHO:
        return 0.0
    if not weather or weather.get("techo"):
        return 0.0

    adj      = 0.0
    temp_c   = weather.get("temp_c")
    wind_kmh = weather.get("wind_kmh")
    precip   = weather.get("precip_mm")

    if temp_c is not None:
        if   temp_c >= 30: adj += 0.40
        elif temp_c >= 26: adj += 0.22
        elif temp_c >= 22: adj += 0.10
        elif temp_c <=  8: adj -= 0.35
        elif temp_c <= 14: adj -= 0.18

    if wind_kmh is not None:
        if   wind_kmh >= 30: adj += 0.28
        elif wind_kmh >= 22: adj += 0.15
        elif wind_kmh >= 16: adj += 0.07

    if precip is not None and precip >= 1.0:
        adj -= 0.22

    return round(adj, 2)


def ajuste_clima_ml(weather, venue_id=None):
    """Ajuste menor a probabilidad ML por clima extremo."""
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
    away_team, home_team, standings,
    away_pitcher="TBD", home_pitcher="TBD",
    away_pitcher_stats=None, home_pitcher_stats=None,
    weather=None,
):
    """
    Probabilidad de victoria del local (0-1).
    Usa: win%, split home/away, últimos 10, diferencial de carreras,
         racha, runs scored/allowed y calidad del pitcher.
    Rango real: 0.28 – 0.72 (expandido para capturar escenarios extremos).
    """
    away = standings.get(away_team)
    home = standings.get(home_team)

    # Si algún equipo no está en standings (inicio de temporada, expansion, etc.)
    if not away or not home:
        return 0.50

    _def = {"era": 4.20, "whip": 1.30, "so9": 8.2,
            "fip": 4.20, "bb9": 3.2,  "hr9": 1.2,
            "ip":  0.0,  "sample_ok": False}
    if away_pitcher_stats is None: away_pitcher_stats = _def
    if home_pitcher_stats is None: home_pitcher_stats = _def

    # Diferencias (positivo = ventaja local)
    diff_win_pct      = home["win_pct"]        - away["win_pct"]
    diff_split        = home["home_win_pct"]    - away["away_win_pct"]
    diff_last10       = home["last10_win_pct"]  - away["last10_win_pct"]
    diff_run_diff     = (home["run_diff"]        - away["run_diff"]) / 100.0
    diff_streak       = parse_streak(home["streak"]) - parse_streak(away["streak"])
    diff_runs_scored  = (home["runs_scored"]     - away["runs_scored"]) / 10.0
    diff_runs_allowed = (away["runs_allowed"]    - home["runs_allowed"]) / 10.0
    diff_pitcher      = score_pitcher_real(home_pitcher_stats) - score_pitcher_real(away_pitcher_stats)

    score = (
        diff_win_pct      * 2.8 +
        diff_split        * 1.9 +
        diff_last10       * 1.2 +
        diff_run_diff     * 1.6 +
        diff_streak       * 1.0 +
        diff_runs_scored  * 0.9 +
        diff_runs_allowed * 0.9 +
        diff_pitcher      * 1.8 +
        0.09                        # ventaja de localía base MLB
    )

    score += ajuste_clima_ml(weather)
    if away_pitcher == "TBD": score += 0.04
    if home_pitcher == "TBD": score -= 0.04

    prob = logistic(score)

    # Romper empate matemático
    if 0.492 <= prob <= 0.508:
        prob = 0.518 if score >= 0 else 0.482

    return clamp(prob, 0.28, 0.72)


def obtener_pick_juego_pro(
    away_team, home_team, standings,
    away_pitcher="TBD", home_pitcher="TBD",
    away_pitcher_stats=None, home_pitcher_stats=None,
    weather=None,
):
    prob_home = calcular_probabilidad_local_pro(
        away_team, home_team, standings,
        away_pitcher, home_pitcher,
        away_pitcher_stats, home_pitcher_stats, weather,
    )
    favorito   = home_team if prob_home >= 0.5 else away_team
    prob_fav   = prob_home if favorito == home_team else (1 - prob_home)
    avoid      = away_pitcher == "TBD" or home_pitcher == "TBD"

    return {
        "favorite":         favorito,
        "prob_home":        prob_home,
        "prob_favorite":    prob_fav,
        "confidence_pct":   round(prob_fav * 100, 1),
        "confidence_label": confidence_label(prob_fav),
        "avoid":            avoid,
    }


def estimar_total_juego_pro(
    away_team, home_team, standings,
    away_pitcher="TBD", home_pitcher="TBD",
    away_pitcher_stats=None, home_pitcher_stats=None,
    weather=None, venue_id=None,
):
    away = standings.get(away_team, {})
    home = standings.get(home_team, {})
    _def = {"era": 4.20, "whip": 1.30, "so9": 8.2, "fip": 4.20, "ip": 0.0}
    if away_pitcher_stats is None: away_pitcher_stats = _def
    if home_pitcher_stats is None: home_pitcher_stats = _def

    away_rs = away.get("runs_scored",  4.5)
    home_rs = home.get("runs_scored",  4.5)
    away_ra = away.get("runs_allowed", 4.5)
    home_ra = home.get("runs_allowed", 4.5)

    total  = 8.6
    total += ((away_rs + home_rs) - 9.0) * 0.24
    total += ((away_ra + home_ra) - 9.0) * 0.20
    total += ((away.get("run_diff", 0) + home.get("run_diff", 0)) / 162.0) * 0.22

    # ERA del pitcher (FIP más fiable si disponible)
    ap_era = away_pitcher_stats.get("fip", away_pitcher_stats.get("era", 4.20))
    hp_era = home_pitcher_stats.get("fip", home_pitcher_stats.get("era", 4.20))
    total += (ap_era - 4.00) * 0.34
    total += (hp_era - 4.00) * 0.34
    total += (away_pitcher_stats.get("whip", 1.30) - 1.25) * 0.82
    total += (home_pitcher_stats.get("whip", 1.30) - 1.25) * 0.82
    total -= (away_pitcher_stats.get("so9",  8.2) - 8.5)   * 0.10
    total -= (home_pitcher_stats.get("so9",  8.2) - 8.5)   * 0.10

    if away_pitcher == "TBD": total += 0.45
    if home_pitcher == "TBD": total += 0.45

    last10 = (away.get("last10_win_pct", 0.5) + home.get("last10_win_pct", 0.5))
    total += (last10 - 1.0) * 0.25

    total += ajuste_clima_total(weather, venue_id=venue_id)

    return round(clamp(total, 6.2, 12.8), 1)


def elegir_total_pick(total_proyectado, total_line):
    """Pick de total cuando hay línea real de mercado."""
    if total_line is None:
        return None
    diff = total_proyectado - total_line
    if   diff >=  0.85: return {"pick": f"Over {total_line}",  "edge": round(diff, 2), "strength": "Alta"}
    elif diff >=  0.55: return {"pick": f"Over {total_line}",  "edge": round(diff, 2), "strength": "Media"}
    elif diff <= -0.85: return {"pick": f"Under {total_line}", "edge": round(abs(diff), 2), "strength": "Alta"}
    elif diff <= -0.55: return {"pick": f"Under {total_line}", "edge": round(abs(diff), 2), "strength": "Media"}
    return None


def elegir_total_pick_fallback(total_proyectado, total_line_conocida=None):
    """
    Pick de total cuando NO hay precio de mercado.
    Usa la línea conocida si existe; si no, usa el promedio histórico MLB (8.8).
    NUNCA hardcodea 8.5.
    """
    referencia = total_line_conocida if total_line_conocida is not None else 8.8
    diff       = total_proyectado - referencia
    if   diff >=  0.80: return {"pick": f"Over {referencia}",  "edge": round(diff, 2),      "strength": "Fallback"}
    elif diff <= -0.80: return {"pick": f"Under {referencia}", "edge": round(abs(diff), 2), "strength": "Fallback"}
    return None


def calcular_prob_total_modelo(total_diff):
    magnitud = abs(total_diff)
    if magnitud < 0.55:
        return None
    return clamp(0.50 + min(magnitud, 1.8) * 0.055, 0.52, 0.64)


def american_to_decimal(american_odds):
    try:
        ml = float(american_odds)
        if ml > 0: return 1 + ml / 100
        return 1 + 100 / abs(ml)
    except Exception:
        return None


def calcular_ev(prob_model, american_odds):
    dec = american_to_decimal(american_odds)
    if dec is None:
        return None
    try:
        p  = float(prob_model)
        return round(p * (dec - 1) - (1 - p), 4)
    except Exception:
        return None


def grade_por_ev(ev):
    if ev is None:    return "D"
    if ev >= 0.08:    return "A+"
    if ev >= 0.05:    return "A"
    if ev >= 0.03:    return "B"
    if ev >= 0.015:   return "C"
    return "D"


def stake_por_ev(ev):
    if ev is None:    return "0u"
    if ev >= 0.08:    return "1.5u"
    if ev >= 0.05:    return "1.0u"
    if ev >= 0.03:    return "0.75u"
    if ev >= 0.015:   return "0.5u"
    return "0u"


def score_pick_ml(analisis):
    """
    Score normalizado 0–100 para ranking de picks ML.
    Todos los componentes se escalan antes de ponderar.
    """
    conf_n  = clamp(analisis["confidence_pct"] / 72.0, 0, 1)
    edge_n  = clamp(analisis["ml_edge_pct"]    / 12.0, -1, 1)
    ev_n    = clamp(analisis["ev_ml_pct"]       / 15.0, -1, 1)
    pitch_n = clamp((analisis["pitching_advantage"] + 0.5) / 1.0, 0, 1)
    form_n  = clamp((analisis["recent_form_advantage"] + 0.5) / 1.0, 0, 1)

    score = (conf_n * 35.0 + ev_n * 30.0 + edge_n * 20.0
             + pitch_n * 10.0 + form_n * 5.0)

    if analisis["risk_flags"].get("tbd_pitcher"):       score -= 20.0
    if analisis["risk_flags"].get("sin_odds_ml"):       score -=  8.0
    if analisis["risk_flags"].get("cuota_extrema_ml"):  score -= 12.0
    if analisis["is_home_pick"]:                        score +=  2.0

    return round(clamp(score, 0, 100), 2)


def score_pick_total(analisis):
    if not analisis.get("total_pick"):
        return 0.0

    edge_n = clamp(analisis["total_edge"]    / 2.5, 0, 1)
    ev_n   = clamp(analisis["ev_total_pct"]  / 12.0, -1, 1)
    conf_n = clamp(analisis["confidence_pct"]/ 72.0, 0, 1)

    score  = ev_n * 40.0 + edge_n * 35.0 + conf_n * 25.0

    if analisis["total_pick"].get("strength") == "Alta":     score +=  8.0
    if analisis["risk_flags"].get("sin_odds_total"):          score -= 10.0
    if analisis["risk_flags"].get("clima_extremo"):           score -=  5.0
    if analisis["total_pick"].get("strength") == "Fallback":  score -= 15.0

    return round(clamp(score, 0, 100), 2)


def score_pick_agresivo(analisis):
    return round(analisis["score_ml"] * 0.65 + analisis["score_total"] * 0.35, 2)

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
    return " ".join(
        str(texto).lower()
        .replace(".", "").replace("-", " ").replace("'", "")
        .replace("los angeles", "la").replace("st louis", "stlouis")
        .replace("new york", "ny").split()
    )


def score_team_match(a, b):
    ak = set(team_key(a).split())
    bk = set(team_key(b).split())
    if not ak or not bk:
        return 0
    inter = len(ak & bk)
    return int((inter / max(len(ak), len(bk))) * 100) if inter else 0


def obtener_odds_completas(away_team, home_team):
    """
    Busca odds en The Odds API.
    Selecciona el bookmaker con mejor precio (mayor valor para el apostador).
    Prioriza bookmakers de la lista BOOKMAKERS_PRIORITARIOS.
    """
    if not ODDS_API_KEY:
        return None

    try:
        data = safe_get(
            "https://api.the-odds-api.com/v4/sports/baseball_mlb/odds",
            params={"apiKey": ODDS_API_KEY, "regions": "us",
                    "markets": "h2h,totals", "oddsFormat": "american"},
        )
        if not isinstance(data, list):
            return None

        away_norm = normalizar_nombre_equipo_odds(away_team)
        home_norm = normalizar_nombre_equipo_odds(home_team)
        away_key  = team_key(away_norm)
        home_key  = team_key(home_norm)

        evento_match = None
        usar_swap    = False

        # Buscar coincidencia exacta primero
        for event in data:
            h_name  = event.get("home_team", "")
            teams   = event.get("teams", [])
            a_name  = next((t for t in teams if t != h_name), "")
            direct  = team_key(h_name) == home_key and team_key(a_name) == away_key
            swapped = team_key(h_name) == away_key and team_key(a_name) == home_key
            if direct or swapped:
                evento_match, usar_swap = event, swapped
                break

        # Fuzzy fallback
        if not evento_match:
            best_score = -1
            for event in data:
                h_name = event.get("home_team", "")
                teams  = event.get("teams", [])
                a_name = next((t for t in teams if t != h_name), "")
                sd = score_team_match(h_name, home_norm) + score_team_match(a_name, away_norm)
                ss = score_team_match(h_name, away_norm) + score_team_match(a_name, home_norm)
                s  = max(sd, ss)
                if s > best_score and s >= 120:
                    best_score   = s
                    evento_match = event
                    usar_swap    = ss > sd

        if not evento_match:
            return None

        h_name = evento_match.get("home_team", "")
        teams  = evento_match.get("teams", [])
        a_name = next((t for t in teams if t != h_name), "")
        mapped_home = a_name if usar_swap else h_name
        mapped_away = h_name if usar_swap else a_name

        # Seleccionar mejor bookmaker
        mejor_res   = None
        mejor_score = -999

        for book in evento_match.get("bookmakers", []):
            res = {
                "bookmaker":      book.get("title", ""),
                "home_moneyline": None, "away_moneyline": None,
                "total_line":     None, "over_price": None, "under_price": None,
            }
            for market in book.get("markets", []):
                key = market.get("key")
                if key == "h2h":
                    for o in market.get("outcomes", []):
                        if o.get("name") == mapped_home:
                            res["home_moneyline"] = o.get("price")
                        elif o.get("name") == mapped_away:
                            res["away_moneyline"] = o.get("price")
                elif key == "totals":
                    for o in market.get("outcomes", []):
                        if o.get("name") == "Over":
                            res["total_line"] = o.get("point")
                            res["over_price"] = o.get("price")
                        elif o.get("name") == "Under":
                            res["under_price"] = o.get("price")

            if res["home_moneyline"] is None and res["away_moneyline"] is None:
                continue

            book_name   = book.get("title", "")
            name_bonus  = 10 if book_name in BOOKMAKERS_PRIORITARIOS else 0
            ml_h = res["home_moneyline"] or 0
            ml_a = res["away_moneyline"] or 0
            # Preferir mayor cuota absoluta (más valor)
            price_score = max(abs(ml_h), abs(ml_a)) / 10.0
            bs = name_bonus + price_score

            if bs > mejor_score:
                mejor_score = bs
                mejor_res   = res

        return mejor_res

    except Exception as e:
        print(f"[WARN] obtener_odds_completas: {e}")
        return None

# =========================================================
# ANÁLISIS DE JUEGO
# =========================================================

def clasificar_apuesta(prob_model, implied_prob, avoid=False):
    if avoid:
        return None
    edge = prob_model - implied_prob
    if edge >= 0.04:   return "A"
    if edge >= 0.025:  return "B"
    if edge >= 0.01:   return "C"
    return None


def analizar_juego(game, standings):
    teams     = game.get("teams", {})
    away_data = teams.get("away", {})
    home_data = teams.get("home", {})
    away      = away_data.get("team", {}).get("name")
    home      = home_data.get("team", {}).get("name")
    if not away or not home:
        return None

    away_pitcher_obj  = away_data.get("probablePitcher", {}) or {}
    home_pitcher_obj  = home_data.get("probablePitcher", {}) or {}
    away_pitcher      = away_pitcher_obj.get("fullName", "TBD")
    home_pitcher      = home_pitcher_obj.get("fullName", "TBD")
    away_stats        = obtener_stats_pitcher_reales(away_pitcher_obj.get("id"))
    home_stats        = obtener_stats_pitcher_reales(home_pitcher_obj.get("id"))

    venue_id = game.get("venue", {}).get("id")
    weather  = obtener_clima_partido(game) or {"temp_c": None, "wind_kmh": None, "precip_mm": None}

    pred       = obtener_pick_juego_pro(away, home, standings, away_pitcher, home_pitcher, away_stats, home_stats, weather)
    total_proj = estimar_total_juego_pro(away, home, standings, away_pitcher, home_pitcher, away_stats, home_stats, weather, venue_id=venue_id)
    odds       = obtener_odds_completas(away, home)

    ml_odds         = None
    total_line      = None
    total_pick      = None
    total_odds      = None
    prob_total_model= None
    ev_ml           = None
    ev_total        = None
    ml_edge_pct     = 0.0
    total_edge      = 0.0

    if odds and isinstance(odds, dict):
        ml_odds    = odds.get("home_moneyline") if pred["favorite"] == home else odds.get("away_moneyline")
        total_line = odds.get("total_line")
        total_pick = elegir_total_pick(total_proj, total_line)

        if total_pick:
            total_edge = total_pick["edge"]
            total_odds = odds.get("over_price") if "Over" in total_pick["pick"] else odds.get("under_price")
            prob_total_model = calcular_prob_total_modelo(total_proj - (total_line or total_proj))
            if prob_total_model is not None and total_odds is not None:
                ev_total = calcular_ev(prob_total_model, total_odds)

    # Fallback de total si no hay pick con línea de mercado
    if total_pick is None:
        total_pick = elegir_total_pick_fallback(total_proj, total_line)
        if total_pick:
            total_edge = total_pick["edge"]

    # EV y edge del ML
    if ml_odds is not None:
        impl_ml     = moneyline_to_prob(ml_odds)
        ev_ml       = calcular_ev(pred["prob_favorite"], ml_odds)
        ml_edge_pct = round((pred["prob_favorite"] - (impl_ml or 0.5)) * 100, 2)
    else:
        ml_edge_pct = round((pred["prob_favorite"] - 0.5) * 100, 2)

    # Forma reciente
    away_form     = standings.get(away, {}).get("last10_win_pct", 0.5)
    home_form     = standings.get(home, {}).get("last10_win_pct", 0.5)
    favorite_form = home_form if pred["favorite"] == home else away_form
    underdog_form = away_form if pred["favorite"] == home else home_form

    risk_flags = {
        "tbd_pitcher":     pred["avoid"],
        "sin_odds_ml":     ml_odds is None,
        "sin_odds_total":  total_pick is not None and total_odds is None,
        "usando_fallback": ml_odds is None or total_odds is None,
        "cuota_extrema_ml":ml_odds is not None and (ml_odds <= -220 or ml_odds >= 170),
        "clima_extremo":   (weather.get("wind_kmh") or 0) >= 30,
    }

    analisis = {
        "game":             f"{away} @ {home}",
        "away":             away,
        "home":             home,
        "matchup_key":      normalizar_matchup(away, home),
        "pitchers":         {"away": away_pitcher, "home": home_pitcher},
        "pitcher_stats":    {"away": away_stats, "home": home_stats},
        "clima":            {
            "temp_c":   safe_float(weather.get("temp_c")),
            "wind_kmh": safe_float(weather.get("wind_kmh")),
            "precip_mm":safe_float(weather.get("precip_mm")),
        },
        "ml_pick":          f"{pred['favorite']} ML",
        "favorite":         pred["favorite"],
        "is_home_pick":     pred["favorite"] == home,
        "prob_home":        round(pred["prob_home"] * 100, 1),
        "prob_away":        round((1 - pred["prob_home"]) * 100, 1),
        "prob_favorite":    pred["prob_favorite"],
        "confidence_pct":   pred["confidence_pct"],
        "confidence_label": pred["confidence_label"],
        "ml_odds":          safe_int(ml_odds),
        "has_valid_ml_odds":ml_odds is not None,
        "ml_edge_pct":      ml_edge_pct,
        "ev_ml":            ev_ml,
        "ev_ml_pct":        round(ev_ml * 100, 2) if ev_ml is not None else 0.0,
        "grade_ml":         grade_por_ev(ev_ml),
        "stake_ml":         stake_por_ev(ev_ml),
        "total_projection": total_proj,
        "total_line":       safe_float(total_line),
        "total_pick":       total_pick,
        "total_odds":       safe_int(total_odds),
        "has_valid_total_odds": total_odds is not None,
        "prob_total_model": prob_total_model,
        "ev_total":         ev_total,
        "ev_total_pct":     round(ev_total * 100, 2) if ev_total is not None else 0.0,
        "grade_total":      grade_por_ev(ev_total),
        "stake_total":      stake_por_ev(ev_total),
        "total_edge":       total_edge,
        "risk_flags":       risk_flags,
        "pitching_advantage":      score_pitcher_real(home_stats) - score_pitcher_real(away_stats),
        "recent_form_advantage":   favorite_form - underdog_form,
    }

    analisis["score_ml"]       = score_pick_ml(analisis)
    analisis["score_total"]    = score_pick_total(analisis)
    analisis["score_agresivo"] = score_pick_agresivo(analisis)
    return analisis

# =========================================================
# CACHE DE ANÁLISIS (TTL 15 min)
# =========================================================

_analisis_cache = {"data": None, "ts": 0.0}
ANALISIS_TTL    = 900  # segundos


def obtener_analisis_del_dia(force=False):
    ahora = time.time()
    if (not force
            and _analisis_cache["data"] is not None
            and (ahora - _analisis_cache["ts"]) < ANALISIS_TTL):
        return _analisis_cache["data"]

    standings = obtener_standings()
    games     = obtener_juegos_del_dia()
    analisis  = []
    for game in games:
        item = analizar_juego(game, standings)
        if item:
            analisis.append(item)

    _analisis_cache["data"] = analisis
    _analisis_cache["ts"]   = ahora
    return analisis

# =========================================================
# LÓGICA DE PARLEY
# =========================================================

def calcular_parley_del_dia(analisis_juegos):
    """
    4 niveles para SIEMPRE generar parley si hay juegos disponibles.
    Nivel 1: EV real positivo vs mercado.
    Nivel 2: Odds disponibles + confianza del modelo.
    Nivel 3: Solo modelo, sin odds.
    Nivel 4: Emergencia — top por confianza, solo excluye TBD.
    """
    estrictos  = []
    fallback_a = []
    fallback_b = []
    emergencia = []

    for a in analisis_juegos:
        if a["risk_flags"]["tbd_pitcher"]:
            if a["confidence_pct"] >= 52.0:
                emergencia.append(a)
            continue

        conf     = a["confidence_pct"]
        ev       = a["ev_ml_pct"]
        edge     = a["ml_edge_pct"]
        score    = a["score_ml"]
        has_odds = a["has_valid_ml_odds"]
        ml       = a["ml_odds"]
        extrema  = ml is not None and (ml <= -275 or ml >= 210)
        clima    = a["risk_flags"].get("clima_extremo", False)

        if has_odds and not extrema and not clima and conf >= 54.0 and ev >= 0.8 and edge >= 1.0:
            estrictos.append(a)
        elif not extrema and not clima and conf >= 52.5 and score >= 35.0:
            fallback_a.append(a)
        elif conf >= 54.0 and not clima:
            fallback_b.append(a)
        elif conf >= 52.0:
            emergencia.append(a)

    estrictos.sort( key=lambda x: (x["ev_ml_pct"],    x["score_ml"]),      reverse=True)
    fallback_a.sort(key=lambda x: (x["score_ml"],     x["confidence_pct"]),reverse=True)
    fallback_b.sort(key=lambda x: x["confidence_pct"], reverse=True)
    emergencia.sort(key=lambda x: x["confidence_pct"], reverse=True)

    seleccionados = []
    usados_mk     = set()
    nivel_usado   = "ninguno"

    def agregar(pool, limite=3):
        for a in pool:
            if len(seleccionados) >= limite:
                break
            if a["matchup_key"] not in usados_mk:
                usados_mk.add(a["matchup_key"])
                seleccionados.append(a)

    agregar(estrictos)
    if len(seleccionados) >= 3:
        nivel_usado = "estricto"
    else:
        agregar(fallback_a)
        if len(seleccionados) >= 3:
            nivel_usado = "fallback_A"
        else:
            agregar(fallback_b)
            if len(seleccionados) >= 2:
                nivel_usado = "fallback_B"
            else:
                agregar(emergencia)
                nivel_usado = "emergencia"

    return seleccionados, nivel_usado


def calcular_parley_millonario(analisis_juegos, matchups_bloqueados):
    """
    5 legs agresivos. Excluye matchups del parley diario.

    DEDUPLICACIÓN: clave (matchup_key, tipo).
    Un partido puede aparecer con ML Y TOTAL al mismo tiempo (comportamiento correcto).
    Nunca dos ML ni dos TOTAL del mismo partido.
    Siempre intenta completar 5 picks pasando por 4 pools de menor a mayor permisividad.
    """
    disponibles     = [a for a in analisis_juegos
                       if a["matchup_key"] not in matchups_bloqueados
                       and not a["risk_flags"]["tbd_pitcher"]]
    disponibles_tbd = [a for a in analisis_juegos
                       if a["matchup_key"] not in matchups_bloqueados]

    def construir_pool(juegos, ml_conf, ml_ev, ml_edge, tot_edge, tot_ev, aceptar_nd=False):
        """
        Genera candidatos ML y TOTAL por separado.
        Para cada partido, si califica como ML Y como TOTAL,
        ambos entran al pool con sus scores respectivos.
        La selección final decide cuál se usa (1 por partido).
        """
        candidatos = []
        for a in juegos:
            conf     = a["confidence_pct"]
            has_odds = a["has_valid_ml_odds"]
            ml       = a["ml_odds"]
            extrema  = ml is not None and (ml <= -300 or ml >= 250)

            # Candidato ML
            if not extrema:
                if has_odds and conf >= ml_conf and a["ev_ml_pct"] >= ml_ev and a["ml_edge_pct"] >= ml_edge:
                    candidatos.append({
                        "tipo": "ML", "game": f"{a['away']} @ {a['home']}",
                        "matchup_key": a["matchup_key"], "pick": a["ml_pick"],
                        "confidence": conf, "edge": a["ml_edge_pct"],
                        "score": a["score_agresivo"], "cuota": ml,
                        "is_home": a["is_home_pick"],
                    })
                elif aceptar_nd and conf >= max(ml_conf - 1.5, 51.0):
                    candidatos.append({
                        "tipo": "ML", "game": f"{a['away']} @ {a['home']}",
                        "matchup_key": a["matchup_key"], "pick": a["ml_pick"],
                        "confidence": conf, "edge": a["ml_edge_pct"],
                        "score": a["score_agresivo"] - 2.0, "cuota": ml or "N/D",
                        "is_home": a["is_home_pick"],
                    })

            # Candidato TOTAL
            if a.get("total_pick") and a["total_edge"] >= tot_edge:
                if a["ev_total_pct"] >= tot_ev or (aceptar_nd and a["score_total"] >= 20.0):
                    cuota_t = a["total_odds"] if a["total_odds"] is not None else "N/D"
                    if cuota_t != "N/D" or aceptar_nd:
                        candidatos.append({
                            "tipo": "TOTAL", "game": f"{a['away']} @ {a['home']}",
                            "matchup_key": a["matchup_key"], "pick": a["total_pick"]["pick"],
                            "confidence": conf, "edge": a["total_edge"],
                            "score": a["score_agresivo"] + 1.5, "cuota": cuota_t,
                            "is_home": False,
                        })

        # Ordenar por score descendente; NO deduplicar aquí para preservar
        # ambas opciones (ML y TOTAL) del mismo partido — la selección final
        # garantiza 1 pick por partido.
        candidatos.sort(key=lambda x: x["score"], reverse=True)
        return candidatos

    pools = [
        construir_pool(disponibles,     52.5, 0.5, 0.8, 0.60, 0.8, False),
        construir_pool(disponibles,     51.5, 0.0, 0.0, 0.45, 0.0, True),
        construir_pool(disponibles_tbd, 51.0, 0.0, 0.0, 0.35, 0.0, True),
        # Pool de emergencia: solo ML ordenado por confianza
        sorted(
            [{"tipo": "ML", "game": f"{a['away']} @ {a['home']}",
              "matchup_key": a["matchup_key"], "pick": a["ml_pick"],
              "confidence": a["confidence_pct"], "edge": a["ml_edge_pct"],
              "score": a["score_agresivo"], "cuota": a["ml_odds"] or "N/D",
              "is_home": a["is_home_pick"]}
             for a in disponibles_tbd],
            key=lambda x: x["confidence"], reverse=True,
        ),
    ]

    seleccionados    = []
    # Clave (matchup_key, tipo): permite ML + TOTAL del mismo partido (comportamiento confirmado correcto)
    vistos_mk_tipo   = set()
    home_ml_count    = 0
    nivel_usado      = "ninguno"

    for idx, pool in enumerate(pools):
        for c in pool:
            if len(seleccionados) >= 5:
                break

            clave = (c["matchup_key"], c["tipo"])
            if clave in vistos_mk_tipo:
                continue

            # No saturar de locales de baja confianza
            if c["tipo"] == "ML" and c.get("is_home") and c["confidence"] < 54 and home_ml_count >= 2:
                continue

            vistos_mk_tipo.add(clave)
            seleccionados.append(c)

            if c["tipo"] == "ML" and c.get("is_home"):
                home_ml_count += 1

        if len(seleccionados) >= 5:
            nivel_usado = ["estricto", "fallback_A", "fallback_B", "emergencia"][idx]
            break

    if nivel_usado == "ninguno" and seleccionados:
        nivel_usado = "emergencia"

    return seleccionados, nivel_usado


def _calcular_cuota_parlay(seleccionados, fallback_dec=1.90):
    cuota     = 1.0
    alguna_nd = False
    for p in seleccionados:
        ml = p.get("ml_odds") or p.get("cuota")
        if ml in (None, "N/D"):
            alguna_nd = True
            cuota    *= fallback_dec
        else:
            dec = american_to_decimal(ml)
            if dec:
                cuota *= dec
    return round(cuota, 2), alguna_nd


def _formatear_parley_guardado(parley_data, icon, titulo):
    texto  = header(titulo, icon)
    texto += f"📅 {parley_data['fecha']}\n\n"
    for leg in parley_data.get("legs", []):
        cuota_str = str(leg.get("cuota")) if leg.get("cuota") not in (None, "N/D", "") else "N/D"
        texto += card_game(leg["game"], [
            f"🎯 Pick: <b>{leg['pick']}</b>",
            f"🧠 Confianza: <b>{leg.get('confidence', 'N/D')}%</b>",
            f"💵 Cuota: <b>{cuota_str}</b>",
        ])
    cuota_total = parley_data.get("cuota_total")
    if cuota_total:
        ganancia = round((float(cuota_total) - 1) * 100)
        texto += f"\n💰 <b>Cuota parlay: ~{cuota_total}x</b>\n"
        texto += f"📈 $100 → ganás <b>${ganancia}</b>\n"
    return texto

# =========================================================
# DATASET TIKTOK — replica exacta del comportamiento del bot
# =========================================================

def generar_dataset_tiktok():
    """
    Exporta exactamente los mismos picks que muestran /apuestas,
    /pronosticos, /parley y /parley_millonario.
    """
    analisis_juegos = obtener_analisis_del_dia()

    data = {
        "fecha":      hoy_str(),
        "bot_version":BOT_VERSION,
        "juegos_del_dia":     [],
        "pronosticos":        [],
        "apuestas": {
            "moneyline_ev": [],
            "totales_ev":   [],
            "modelo":       [],
        },
        "parley":             [],
        "parley_millonario":  [],
    }

    if not analisis_juegos:
        return data

    # ── Juegos del día ───────────────────────────────────────
    for a in analisis_juegos:
        data["juegos_del_dia"].append({
            "game":        a["game"],
            "matchup_key": a["matchup_key"],
            "away":        a["away"],
            "home":        a["home"],
            "pitchers":    a["pitchers"],
        })

    # ── Pronósticos (misma lógica que /pronosticos) ──────────
    pronosticos = sorted(analisis_juegos, key=lambda x: x["confidence_pct"], reverse=True)
    for a in pronosticos[:8]:
        data["pronosticos"].append({
            "game":              a["game"],
            "matchup_key":       a["matchup_key"],
            "pick":              a["ml_pick"],
            "prob_home_pct":     a["prob_home"],
            "prob_away_pct":     a["prob_away"],
            "confianza":         a["confidence_pct"],
            "confianza_label":   a["confidence_label"],
            "total_proyectado":  a["total_projection"],
            "pitchers":          f"{a['pitchers']['away']} vs {a['pitchers']['home']}",
            "era":               f"{a['pitcher_stats']['away']['era']} vs {a['pitcher_stats']['home']['era']}",
            "clima":             a["clima"],
        })

    # ── Apuestas ML con EV+ (misma lógica que /apuestas) ─────
    picks_ml = [a for a in analisis_juegos if a["grade_ml"] != "D" and not a["risk_flags"]["tbd_pitcher"]]
    picks_ml.sort(key=lambda x: (x["score_ml"], x["ev_ml_pct"]), reverse=True)
    picks_ml = filtrar_matchups_unicos(picks_ml)
    for a in picks_ml[:5]:
        data["apuestas"]["moneyline_ev"].append({
            "game":        a["game"],
            "matchup_key": a["matchup_key"],
            "pick":        a["ml_pick"],
            "grade":       a["grade_ml"],
            "stake":       a["stake_ml"],
            "cuota":       a["ml_odds"] if a["ml_odds"] is not None else "N/D",
            "model_prob":  round(a["prob_favorite"] * 100, 1),
            "edge":        a["ml_edge_pct"],
            "ev_pct":      a["ev_ml_pct"],
            "pitchers":    f"{a['pitchers']['away']} vs {a['pitchers']['home']}",
            "clima":       a["clima"],
        })

    # ── Apuestas Totales con EV+ ──────────────────────────────
    picks_tot = [a for a in analisis_juegos if a.get("total_pick") and a["grade_total"] != "D"]
    picks_tot.sort(key=lambda x: (x["score_total"], x["ev_total_pct"]), reverse=True)
    picks_tot = filtrar_matchups_unicos(picks_tot)
    for a in picks_tot[:5]:
        data["apuestas"]["totales_ev"].append({
            "game":        a["game"],
            "matchup_key": a["matchup_key"],
            "pick":        a["total_pick"]["pick"],
            "grade":       a["grade_total"],
            "stake":       a["stake_total"],
            "cuota":       a["total_odds"] if a["total_odds"] is not None else "N/D",
            "projection":  a["total_projection"],
            "line":        a["total_line"],
            "edge":        a["total_edge"],
            "ev_pct":      a["ev_total_pct"],
            "pitchers":    f"{a['pitchers']['away']} vs {a['pitchers']['home']}",
            "clima":       a["clima"],
        })

    # ── Modelo puro (top confianza sin TBD) ──────────────────
    picks_mod = [a for a in analisis_juegos if not a["risk_flags"]["tbd_pitcher"]]
    picks_mod.sort(key=lambda x: x["confidence_pct"], reverse=True)
    picks_mod = filtrar_matchups_unicos(picks_mod)
    for a in picks_mod[:5]:
        data["apuestas"]["modelo"].append({
            "game":          a["game"],
            "matchup_key":   a["matchup_key"],
            "pick":          a["ml_pick"],
            "confianza":     a["confidence_pct"],
            "prob_home_pct": a["prob_home"],
            "prob_away_pct": a["prob_away"],
            "pitchers":      f"{a['pitchers']['away']} vs {a['pitchers']['home']}",
            "total_proyectado": a["total_projection"],
            "clima":         a["clima"],
        })

    # ── Parley diario (exactamente igual que /parley) ─────────
    parley_existente = buscar_parley_del_dia("parley")
    if parley_existente:
        data["parley"] = parley_existente.get("legs", [])
    else:
        sel_p, _ = calcular_parley_del_dia(analisis_juegos)
        for p in sel_p:
            data["parley"].append({
                "game":       f"{p['away']} @ {p['home']}",
                "pick":       p["ml_pick"],
                "confidence": p["confidence_pct"],
                "cuota":      p["ml_odds"] if p.get("ml_odds") is not None else "N/D",
            })

    # ── Parley millonario (exactamente igual que /parley_millonario) ──
    mill_existente = buscar_parley_del_dia("parley_millonario")
    if mill_existente:
        data["parley_millonario"] = mill_existente.get("legs", [])
    else:
        picks_diarios, _ = calcular_parley_del_dia(analisis_juegos)
        bloqueados       = {a["matchup_key"] for a in picks_diarios}
        sel_m, _         = calcular_parley_millonario(analisis_juegos, bloqueados)
        for p in sel_m:
            data["parley_millonario"].append({
                "game":       p["game"],
                "tipo":       p["tipo"],
                "pick":       p["pick"],
                "confidence": p["confidence"],
                "cuota":      p["cuota"],
            })

    return data

# =========================================================
# HELPERS DE DISPLAY
# =========================================================

def _efectividad_parleys(stats, tipo):
    """Calcula % de efectividad para un tipo de parley."""
    g = stats[tipo]["ganado"]
    f = stats[tipo]["fallado"]
    return round(g / (g + f) * 100, 1) if (g + f) else 0


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
            "cmd_hoy":               hoy,
            "cmd_posiciones":        posiciones,
            "cmd_apuestas":          apuestas,
            "cmd_parley":            parley,
            "cmd_parley_millonario": parley_millonario,
            "cmd_pitchers":          pitchers,
            "cmd_pronosticos":       pronosticos,
            "cmd_lesionados":        lesionados,
            "cmd_roi":               roi,
            "cmd_exportar_json":     exportar_json,
            "cmd_stats_parlays":     stats_parleys,
            "cmd_parley_ganado":     parley_ganado,
            "cmd_parley_fallado":    parley_fallado,
            "cmd_millonario_ganado": millonario_ganado,
            "cmd_millonario_fallado":millonario_fallado,
            "cmd_reset_parley":      reset_parley,
            "cmd_reset_millonario":  reset_millonario,
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
        "• Parlays filtrados por nivel\n\n"
        f"🧪 Versión: <b>{BOT_VERSION}</b>\n\n"
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
            bot.edit_message_text(f"📅 No hay juegos programados hoy ({fecha}).", msg.chat.id, msg.message_id)
            return

        juegos = []
        for g in games:
            teams  = g.get("teams", {})
            away   = teams.get("away", {}).get("team", {}).get("name", "TBD")
            home   = teams.get("home", {}).get("team", {}).get("name", "TBD")
            sa     = teams.get("away", {}).get("score", "-")
            sh     = teams.get("home", {}).get("score", "-")
            status = g.get("status", {}).get("detailedState", "?")
            gdate  = g.get("gameDate", "")
            try:
                dt_utc = datetime.fromisoformat(gdate.replace("Z", "+00:00"))
                dt_ve  = dt_utc.astimezone(ZoneInfo("America/Caracas")) if ZoneInfo else dt_utc - timedelta(hours=4)
                hora_orden = dt_ve
                hora_txt   = dt_ve.strftime("%I:%M %p")
            except Exception:
                hora_orden = None
                hora_txt   = "N/D"
            juegos.append({"away": away, "home": home, "sa": sa, "sh": sh,
                           "status": status, "hora_txt": hora_txt, "hora_orden": hora_orden})

        juegos.sort(key=lambda x: x["hora_orden"] if x["hora_orden"] else datetime.max)

        texto  = header("JUEGOS DE HOY", "📅")
        texto += f"🗓️ {fecha} | Hora Venezuela\n\n"
        for i, j in enumerate(juegos, 1):
            texto += card_game(f"{i}. {j['away']} @ {j['home']}", [
                f"🕒 {j['hora_txt']} VET",
                f"📌 {j['status']}",
                f"⚾️ Score: {j['sa']} - {j['sh']}",
            ])

        bot.delete_message(msg.chat.id, msg.message_id)
        responder_largo(message.chat.id, texto, parse_mode="HTML")
    except Exception as e:
        bot.edit_message_text(f"❌ Error: {str(e)[:120]}", msg.chat.id, msg.message_id)


@bot.message_handler(commands=["posiciones"])
def posiciones(message):
    msg = bot.reply_to(message, "🏆 Cargando standings...")
    try:
        season = temporada_actual()
        data   = safe_get(f"{MLB_BASE}/standings", params={
            "leagueId": "103,104", "season": season,
            "standingsTypes": "regularSeason", "hydrate": "team,league,division",
        })
        records = data.get("records", [])
        if not records:
            bot.edit_message_text("❌ No pude cargar los standings.", msg.chat.id, msg.message_id)
            return

        bloques = []
        titulo  = f"🏆 <b>STANDINGS MLB {season}</b>\n"

        for record in records:
            league_name   = record.get("league",   {}).get("name", "")
            division_name = record.get("division", {}).get("name", "División")
            if "Spring" in division_name or "Wild Card" in division_name:
                continue

            lineas = [
                f"{league_name} — {division_name}", "",
                "Equipo               W    L   PCT    GB   HOME   AWAY   L10   STK",
                "─" * 68,
            ]
            for t in record.get("teamRecords", []):
                nombre = t.get("team", {}).get("name", "")
                wins   = t.get("wins",   0)
                losses = t.get("losses", 0)
                pct    = t.get("pct",    "---")
                gb     = str(t.get("gamesBack", "-"))

                hw, hl = _extraer_split(t, "home")
                aw, al = _extraer_split(t, "away")
                lw, ll = _extraer_split(t, "lastTen")
                hw = hw or t.get("homeWins", 0);  hl = hl or t.get("homeLosses", 0)
                aw = aw or t.get("awayWins", 0);  al = al or t.get("awayLosses", 0)
                lw = lw or t.get("lastTenWins", 0); ll = ll or t.get("lastTenLosses", 0)

                home_str  = f"{hw}-{hl}"
                away_str  = f"{aw}-{al}"
                l10_str   = f"{lw}-{ll}"
                strk      = str(t.get("streakCode", "-"))

                fila = (f"{nombre[:20].ljust(20)} "
                        f"{str(wins).rjust(4)} {str(losses).rjust(4)} "
                        f"{str(pct).rjust(5)} {gb.rjust(5)} "
                        f"{home_str.rjust(6)} {away_str.rjust(6)} "
                        f"{l10_str.rjust(5)} {strk.rjust(5)}")
                lineas.append(fila)

            bloques.append("<pre>" + "\n".join(lineas) + "</pre>")

        bot.delete_message(msg.chat.id, msg.message_id)
        bot.send_message(message.chat.id, titulo, parse_mode="HTML")
        for bloque in bloques:
            bot.send_message(message.chat.id, bloque, parse_mode="HTML")
    except Exception as e:
        bot.edit_message_text(f"❌ Error: {str(e)[:120]}", msg.chat.id, msg.message_id)


@bot.message_handler(commands=["pronosticos"])
def pronosticos(message):
    msg = bot.reply_to(message, "📊 Generando pronósticos...")
    try:
        analisis_juegos = obtener_analisis_del_dia()

        texto  = header("PRONÓSTICOS DEL MODELO", "📊")
        texto += f"📅 {hoy_str()}\n\n"

        if not analisis_juegos:
            texto += "No hay juegos hoy."
            bot.edit_message_text(texto, msg.chat.id, msg.message_id, parse_mode="HTML")
            return

        picks = sorted(analisis_juegos, key=lambda x: x["confidence_pct"], reverse=True)
        picks = filtrar_matchups_unicos(picks)

        for a in picks[:8]:
            fav   = a["favorite"]
            und   = a["home"] if fav == a["away"] else a["away"]
            p_fav = a["confidence_pct"]
            p_und = round(100 - p_fav, 1)
            texto += card_game(f"{a['away']} @ {a['home']}", [
                f"🎯 Pick: <b>{a['ml_pick']}</b>",
                f"🧠 <b>{fav}</b>: {p_fav}% | <b>{und}</b>: {p_und}%",
                f"📊 Total proyectado: <b>{a['total_projection']}</b>",
                f"🎽 {a['pitchers']['away']} vs {a['pitchers']['home']}",
                f"📉 ERA: {a['pitcher_stats']['away']['era']} vs {a['pitcher_stats']['home']['era']}",
                f"🌡️ {a['clima'].get('temp_c')}°C | 💨 {a['clima'].get('wind_kmh')} km/h",
            ])

        bot.edit_message_text(texto, msg.chat.id, msg.message_id, parse_mode="HTML")
    except Exception as e:
        bot.edit_message_text(f"❌ Error: {str(e)[:120]}", msg.chat.id, msg.message_id)


@bot.message_handler(commands=["apuestas"])
def apuestas(message):
    msg = bot.reply_to(message, "🔥 Analizando con EV + stake automático...")
    try:
        analisis_juegos = obtener_analisis_del_dia()

        texto  = header("APUESTAS PRO MLB", "💰")
        texto += f"📅 {hoy_str()}\n\n"

        if not analisis_juegos:
            texto += "No hay juegos hoy."
            bot.edit_message_text(texto, msg.chat.id, msg.message_id, parse_mode="HTML")
            return

        # ML con EV+
        picks_ml = [a for a in analisis_juegos if a["grade_ml"] != "D" and not a["risk_flags"]["tbd_pitcher"]]
        picks_ml.sort(key=lambda x: (x["score_ml"], x["ev_ml_pct"]), reverse=True)
        picks_ml = filtrar_matchups_unicos(picks_ml)

        # Totales con EV+
        picks_tot = [a for a in analisis_juegos if a.get("total_pick") and a["grade_total"] != "D"]
        picks_tot.sort(key=lambda x: (x["score_total"], x["ev_total_pct"]), reverse=True)
        picks_tot = filtrar_matchups_unicos(picks_tot)

        # Modelo puro
        picks_mod = [a for a in analisis_juegos if not a["risk_flags"]["tbd_pitcher"]]
        picks_mod.sort(key=lambda x: x["confidence_pct"], reverse=True)
        picks_mod = filtrar_matchups_unicos(picks_mod)

        texto += "💰 <b>MONEYLINE CON EV+</b>\n━━━━━━━━━━━━━━━━━━\n\n"
        if picks_ml:
            for a in picks_ml[:5]:
                fav   = a["favorite"]
                und   = a["home"] if fav == a["away"] else a["away"]
                p_fav = a["confidence_pct"]
                p_und = round(100 - p_fav, 1)
                texto += card_game(f"{a['away']} @ {a['home']}", [
                    f"🎯 Pick: <b>{a['ml_pick']}</b>",
                    f"🧠 <b>{fav}</b>: {p_fav}% | <b>{und}</b>: {p_und}%",
                    f"🏷️ Grade: <b>{a['grade_ml']}</b> | Stake: <b>{a['stake_ml']}</b>",
                    f"💵 Cuota: <b>{a['ml_odds'] if a['ml_odds'] is not None else 'N/D'}</b>",
                    f"📈 Edge: <b>{a['ml_edge_pct']:+.1f}%</b> | EV: <b>{a['ev_ml_pct']:+.1f}%</b>",
                    f"🎽 {a['pitchers']['away']} vs {a['pitchers']['home']}",
                ])
        else:
            texto += "No hubo moneylines con EV positivo hoy.\n\n"

        texto += "📊 <b>TOTALES CON EV+</b>\n━━━━━━━━━━━━━━━━━━\n\n"
        if picks_tot:
            for a in picks_tot[:5]:
                texto += card_game(f"{a['away']} @ {a['home']}", [
                    f"🎯 Pick: <b>{a['total_pick']['pick']}</b>",
                    f"🏷️ Grade: <b>{a['grade_total']}</b> | Stake: <b>{a['stake_total']}</b>",
                    f"💵 Cuota: <b>{a['total_odds'] if a['total_odds'] is not None else 'N/D'}</b>",
                    f"📊 Proyección: {a['total_projection']} | Línea: {a['total_line']}",
                    f"📈 Edge: <b>{a['total_edge']:.2f}</b> | EV: <b>{a['ev_total_pct']:+.1f}%</b>",
                    f"🎽 {a['pitchers']['away']} vs {a['pitchers']['home']}",
                ])
        else:
            texto += "No hubo totales con EV positivo hoy.\n\n"

        texto += "🧠 <b>PICKS DEL MODELO</b>\n━━━━━━━━━━━━━━━━━━\n\n"
        if picks_mod:
            for a in picks_mod[:5]:
                fav   = a["favorite"]
                und   = a["home"] if fav == a["away"] else a["away"]
                p_fav = a["confidence_pct"]
                p_und = round(100 - p_fav, 1)
                texto += card_game(f"{a['away']} @ {a['home']}", [
                    f"🎯 Pick: <b>{a['ml_pick']}</b>",
                    f"🧠 <b>{fav}</b>: {p_fav}% | <b>{und}</b>: {p_und}%",
                    f"📊 Total proyectado: <b>{a['total_projection']}</b>",
                    f"🎽 {a['pitchers']['away']} vs {a['pitchers']['home']}",
                    f"🌡️ {a['clima'].get('temp_c')}°C | 💨 {a['clima'].get('wind_kmh')} km/h",
                ])
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
            texto = _formatear_parley_guardado(existente, "🎯", "PARLEY DEL DÍA (FIJO)")
            bot.edit_message_text(texto, msg.chat.id, msg.message_id, parse_mode="HTML")
            return

        analisis_juegos   = obtener_analisis_del_dia()
        seleccionados, nivel = calcular_parley_del_dia(analisis_juegos)

        nivel_labels = {
            "estricto":   "✅ Picks con EV positivo real",
            "fallback_A": "📊 Picks del modelo con cuotas",
            "fallback_B": "🧠 Picks del modelo puro (sin cuotas)",
            "emergencia": "⚠️ Emergencia — apostar con precaución",
            "ninguno":    "⚠️ Sin clasificación",
        }

        texto  = header("PARLEY DEL DÍA MLB", "🎯")
        texto += f"📅 {hoy_str()}\n\n"

        if not seleccionados:
            texto += "No hay juegos disponibles hoy (todos con pitcher TBD o sin datos)."
        else:
            texto += f"{nivel_labels.get(nivel, '')}\n\n"
            cuota_total, tiene_nd = _calcular_cuota_parlay(seleccionados)

            for a in seleccionados:
                fav   = a["favorite"]
                und   = a["home"] if fav == a["away"] else a["away"]
                p_fav = a["confidence_pct"]
                p_und = round(100 - p_fav, 1)
                ml_d  = a["ml_odds"] if a.get("ml_odds") is not None else "N/D"
                texto += card_game(f"{a['away']} @ {a['home']}", [
                    f"🎯 Pick: <b>{a['ml_pick']}</b>",
                    f"🧠 <b>{fav}</b>: {p_fav}% | <b>{und}</b>: {p_und}%",
                    f"📈 Score: <b>{a['score_ml']:.0f}/100</b> | EV: <b>{a['ev_ml_pct']:+.1f}%</b>",
                    f"💵 Cuota: <b>{ml_d}</b>",
                ])

            nd_nota  = " ⚠️ (picks sin cuota real)" if tiene_nd else ""
            ganancia = round((cuota_total - 1) * 100)
            texto += f"\n💰 <b>Cuota parlay: ~{cuota_total}x</b>{nd_nota}\n"
            texto += f"📈 $100 → ganás <b>${ganancia}</b>\n"

            legs = [{"game": f"{a['away']} @ {a['home']}", "pick": a["ml_pick"],
                     "confidence": a["confidence_pct"], "cuota": a.get("ml_odds")}
                    for a in seleccionados]
            registrar_parley_del_dia("parley", legs, cuota_total)

        bot.edit_message_text(texto, msg.chat.id, msg.message_id, parse_mode="HTML")
    except Exception as e:
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

        analisis_juegos  = obtener_analisis_del_dia()
        picks_d, _       = calcular_parley_del_dia(analisis_juegos)
        matchups_bloq    = {a["matchup_key"] for a in picks_d}

        seleccionados, nivel = calcular_parley_millonario(analisis_juegos, matchups_bloq)

        nivel_labels = {
            "estricto":   "✅ Picks agresivos con EV+",
            "fallback_A": "📊 Picks agresivos del modelo",
            "fallback_B": "🧠 Picks del modelo (sin filtro EV)",
            "emergencia": "⚠️ Emergencia — máximo riesgo",
            "ninguno":    "⚠️ Sin clasificación",
        }

        texto  = header("PARLEY MILLONARIO (ALTO RIESGO)", "💎")
        texto += f"📅 {hoy_str()}\n\n"

        if not seleccionados:
            texto += "No hay juegos disponibles hoy para el millonario."
        else:
            texto += f"{nivel_labels.get(nivel, '')}\n\n"

            cuota_mill = 1.0
            for p in seleccionados:
                ml_raw = p.get("cuota")
                dec    = american_to_decimal(ml_raw) if ml_raw not in (None, "N/D") else 2.05
                if dec:
                    cuota_mill *= dec
            cuota_mill = round(cuota_mill, 2)
            ganancia   = round((cuota_mill - 1) * 100)

            for p in seleccionados:
                tipo_icon = "🎯" if p["tipo"] == "ML" else "📊"
                texto += card_game(p["game"], [
                    f"{tipo_icon} {p['tipo']}: <b>{p['pick']}</b>",
                    f"🧠 Confianza: <b>{p['confidence']}%</b>",
                    f"📈 Edge: <b>{p['edge']:+.2f}</b>",
                    f"💵 Cuota: <b>{p['cuota']}</b>",
                ])

            texto += f"\n💰 <b>Cuota parlay: ~{cuota_mill}x</b>\n"
            texto += f"📈 $100 → ganás <b>${ganancia}</b>\n"
            texto += f"⚠️ <i>Alto riesgo — apostá solo lo que podés perder</i>\n"

            legs = [{"game": p["game"], "pick": p["pick"],
                     "confidence": p["confidence"], "cuota": p["cuota"]}
                    for p in seleccionados]
            registrar_parley_del_dia("parley_millonario", legs, cuota_mill)

        bot.edit_message_text(texto, msg.chat.id, msg.message_id, parse_mode="HTML")
    except Exception as e:
        bot.edit_message_text(f"❌ Error en /parley_millonario: {str(e)[:180]}", msg.chat.id, msg.message_id)


@bot.message_handler(commands=["pitchers"])
def pitchers(message):
    msg = bot.reply_to(message, "🧢 Cargando pitchers probables...")
    try:
        analisis_juegos = obtener_analisis_del_dia()
        texto  = header("PITCHERS PROBABLES", "🧢")
        texto += f"📅 {hoy_str()}\n\n"

        if not analisis_juegos:
            texto += "No hay juegos programados hoy."
            bot.edit_message_text(texto, msg.chat.id, msg.message_id, parse_mode="HTML")
            return

        for a in analisis_juegos:
            as_ = a["pitcher_stats"]["away"]
            hs_ = a["pitcher_stats"]["home"]
            texto += card_game(f"{a['away']} @ {a['home']}", [
                f"🛣️ {abreviar_equipo(a['away'])}: <b>{a['pitchers']['away']}</b> | ERA {as_['era']} | FIP {as_['fip']} | WHIP {as_['whip']}",
                f"🏠 {abreviar_equipo(a['home'])}: <b>{a['pitchers']['home']}</b> | ERA {hs_['era']} | FIP {hs_['fip']} | WHIP {hs_['whip']}",
            ])

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
            desc  = str(t.get("description", ""))
            lower = desc.lower()
            if any(x in lower for x in ["injured", " il", "injury", "60-day", "15-day", "10-day", "placed on"]):
                texto += f"• {desc}\n"
                count += 1
                if count >= 15:
                    break
        if count == 0:
            texto += "No encontré movimientos de IL recientemente."
        bot.delete_message(msg.chat.id, msg.message_id)
        responder_largo(message.chat.id, texto, parse_mode="HTML")
    except Exception as e:
        bot.edit_message_text(f"❌ Error: {str(e)[:120]}", msg.chat.id, msg.message_id)


@bot.message_handler(commands=["roi"])
def roi(message):
    try:
        if not os.path.exists(RESULTADOS_CSV):
            bot.reply_to(message, "No existe el archivo de resultados todavía.")
            return

        total_apuestas = 0
        total_unidades = 0.0
        total_profit   = 0.0
        ganadas        = 0
        perdidas       = 0

        with open(RESULTADOS_CSV, "r", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                resultado = str(row.get("resultado", "")).strip().lower()
                profit    = row.get("profit", "")
                stake     = row.get("stake",  "")
                if resultado in ["win", "lose"] and profit not in ["", None]:
                    total_apuestas += 1
                    total_unidades += extraer_unidades(stake)
                    total_profit   += float(profit)
                    if resultado == "win":  ganadas  += 1
                    else:                   perdidas += 1

        if total_apuestas == 0 or total_unidades == 0:
            bot.reply_to(message, "Todavía no hay apuestas cerradas en el CSV.")
            return

        roi_pct  = round((total_profit / total_unidades) * 100, 2)
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


@bot.message_handler(commands=["exportar_json"])
def exportar_json(message):
    msg = bot.reply_to(message, "📦 Generando JSON maestro...")
    try:
        data = generar_dataset_tiktok()
        ruta = guardar_json_tiktok(data)
        if not ruta:
            bot.edit_message_text("❌ No se pudo guardar el JSON.", msg.chat.id, msg.message_id)
            return

        texto = (
            "✅ <b>JSON maestro generado</b>\n\n"
            f"📅 Fecha: <b>{data['fecha']}</b>\n"
            f"🤖 Versión: <b>{data['bot_version']}</b>\n"
            f"⚾ Juegos: <b>{len(data['juegos_del_dia'])}</b>\n"
            f"📊 Pronósticos: <b>{len(data['pronosticos'])}</b>\n"
            f"💰 ML EV+: <b>{len(data['apuestas']['moneyline_ev'])}</b>\n"
            f"📈 Totales EV+: <b>{len(data['apuestas']['totales_ev'])}</b>\n"
            f"🎯 Parley: <b>{len(data['parley'])}</b>\n"
            f"💎 Millonario: <b>{len(data['parley_millonario'])}</b>\n\n"
            f"📂 <code>{ruta}</code>"
        )
        bot.edit_message_text(texto, msg.chat.id, msg.message_id, parse_mode="HTML")
    except Exception as e:
        bot.edit_message_text(f"❌ Error en /exportar_json: {str(e)[:180]}", msg.chat.id, msg.message_id)


@bot.message_handler(commands=["historial"])
def historial(message):
    parleys = cargar_parleys_diarios()
    texto   = header("HISTORIAL DE PARLEYS", "📚")

    if not parleys:
        texto += "No hay historial registrado todavía."
        bot.reply_to(message, texto, parse_mode="HTML")
        return

    stats = {
        "parley":             {"ganado": 0, "fallado": 0, "pendiente": 0},
        "parley_millonario":  {"ganado": 0, "fallado": 0, "pendiente": 0},
    }
    for p in parleys:
        tipo   = p.get("tipo")
        estado = p.get("estado", "pendiente")
        if tipo in stats and estado in stats[tipo]:
            stats[tipo][estado] += 1

    texto += (
        f"🎯 Parley: ✅{stats['parley']['ganado']} ❌{stats['parley']['fallado']} ⏳{stats['parley']['pendiente']} | <b>{_efectividad_parleys(stats, 'parley')}%</b>\n"
        f"💎 Millonario: ✅{stats['parley_millonario']['ganado']} ❌{stats['parley_millonario']['fallado']} ⏳{stats['parley_millonario']['pendiente']} | <b>{_efectividad_parleys(stats, 'parley_millonario')}%</b>\n\n"
        f"🧾 <b>Últimos registros</b>\n\n"
    )

    for item in sorted(parleys, key=lambda x: x.get("fecha", ""), reverse=True)[:12]:
        texto += f"📅 <b>{item.get('fecha','N/D')}</b> | {item.get('tipo','?')} | <b>{item.get('estado','?')}</b>\n"
        for leg in item.get("legs", []):
            texto += f"• {leg.get('game','?')} → {leg.get('pick','?')}\n"
        texto += "\n"

    responder_largo(message.chat.id, texto, parse_mode="HTML")


@bot.message_handler(commands=["stats_parlays"])
def stats_parleys(message):
    parleys = cargar_parleys_diarios()
    stats   = {
        "parley":            {"ganado": 0, "fallado": 0},
        "parley_millonario": {"ganado": 0, "fallado": 0},
    }
    for p in parleys:
        tipo   = p.get("tipo")
        estado = p.get("estado")
        if tipo in stats and estado in ["ganado", "fallado"]:
            stats[tipo][estado] += 1

    texto = header("ESTADÍSTICAS DE PARLEYS", "📊")
    texto += (
        f"🎯 <b>Parley diario</b>\n"
        f"✅ Ganados: <b>{stats['parley']['ganado']}</b>\n"
        f"❌ Fallados: <b>{stats['parley']['fallado']}</b>\n"
        f"📈 Efectividad: <b>{_efectividad_parleys(stats, 'parley')}%</b>\n\n"
        f"💎 <b>Parley millonario</b>\n"
        f"✅ Ganados: <b>{stats['parley_millonario']['ganado']}</b>\n"
        f"❌ Fallados: <b>{stats['parley_millonario']['fallado']}</b>\n"
        f"📈 Efectividad: <b>{_efectividad_parleys(stats, 'parley_millonario')}%</b>\n"
    )
    bot.reply_to(message, texto, parse_mode="HTML")


@bot.message_handler(commands=["reset_parley"])
def reset_parley(message):
    borrado = eliminar_parley_del_dia("parley")
    bot.reply_to(message, "♻️ Parley del día reiniciado." if borrado else "No había parley guardado hoy.")


@bot.message_handler(commands=["reset_millonario"])
def reset_millonario(message):
    borrado = eliminar_parley_del_dia("parley_millonario")
    bot.reply_to(message, "♻️ Parley millonario reiniciado." if borrado else "No había parley millonario guardado hoy.")


@bot.message_handler(commands=["parley_ganado"])
def parley_ganado(message):
    ok = actualizar_estado_parley(hoy_str(), "parley", "ganado")
    bot.reply_to(message, "✅ Parley del día marcado como GANADO." if ok else "❌ No encontré parley del día.")


@bot.message_handler(commands=["parley_fallado"])
def parley_fallado(message):
    ok = actualizar_estado_parley(hoy_str(), "parley", "fallado")
    bot.reply_to(message, "❌ Parley del día marcado como FALLADO." if ok else "❌ No encontré parley del día.")


@bot.message_handler(commands=["millonario_ganado"])
def millonario_ganado(message):
    ok = actualizar_estado_parley(hoy_str(), "parley_millonario", "ganado")
    bot.reply_to(message, "✅ Parley millonario marcado como GANADO." if ok else "❌ No encontré parley millonario.")


@bot.message_handler(commands=["millonario_fallado"])
def millonario_fallado(message):
    ok = actualizar_estado_parley(hoy_str(), "parley_millonario", "fallado")
    bot.reply_to(message, "❌ Parley millonario marcado como FALLADO." if ok else "❌ No encontré parley millonario.")


@bot.message_handler(commands=["lineups"])
def lineups(message):
    texto = (
        "📋 <b>LINEUPS</b>\n━━━━━━━━━━━━━━━━━━\n\n"
        "Las alineaciones salen 1–2 horas antes del primer juego.\n\n"
        "Mientras tanto usá:\n• /pitchers\n• /lesionados\n• /hoy"
    )
    bot.reply_to(message, texto, parse_mode="HTML")


# =========================================================
# INICIO
# =========================================================

print(f"[BOT] Iniciando {BOT_VERSION}")
bot.remove_webhook()
time.sleep(1)
bot.infinity_polling(
    skip_pending=True,
    timeout=30,
    long_polling_timeout=30,
    allowed_updates=["message", "callback_query"],
)
