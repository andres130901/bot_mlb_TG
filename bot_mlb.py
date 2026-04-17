import os
import csv
import json
import math
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
BOT_VERSION = "V6_3_TRACKED_EDGE"

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
        print(f"Error cargando historial: {e}")
        return []


def guardar_historial(historial):
    try:
        with open(HISTORIAL_FILE, "w", encoding="utf-8") as f:
            json.dump(historial, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"Error guardando historial: {e}")


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
        print(f"Error inicializando CSV: {e}")


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
        print(f"Error cargando parleys diarios: {e}")
        return []


def guardar_parleys_diarios(data):
    try:
        with open(PARLEYS_DIARIOS_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"Error guardando parleys diarios: {e}")


def buscar_parley_del_dia(tipo, fecha=None):
    if fecha is None:
        fecha = hoy_str()
    data = cargar_parleys_diarios()
    for p in data:
        if p.get("fecha") == fecha and p.get("tipo") == tipo:
            return p
    return None


def registrar_parley_del_dia(tipo, legs, fecha=None):
    if fecha is None:
        fecha = hoy_str()
    existente = buscar_parley_del_dia(tipo, fecha)
    if existente:
        return existente
    data = cargar_parleys_diarios()
    nuevo = {"fecha": fecha, "tipo": tipo, "estado": "pendiente", "legs": legs}
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
    nuevo = []
    borrado = False

    for p in data:
        if p.get("fecha") == fecha and p.get("tipo") == tipo:
            borrado = True
            continue
        nuevo.append(p)

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
        print(f"Error GET {url}: {e}")
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
            bot.send_message(
                chat_id, parte, parse_mode=parse_mode, reply_markup=reply_markup
            )
        else:
            bot.send_message(chat_id, parte, parse_mode=parse_mode)


def parse_streak(streak_code):
    if not streak_code:
        return 0.0
    streak_code = str(streak_code).upper().strip()
    try:
        if streak_code.startswith("W"):
            return min(int(streak_code[1:]) * 0.01, 0.05)
        if streak_code.startswith("L"):
            return max(int(streak_code[1:]) * -0.01, -0.05)
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


def filtrar_matchups_unicos(items):
    vistos = set()
    filtrados = []

    for item in items:
        clave = item.get("matchup_key")

        if not clave:
            game = item.get("game", "")
            if " @ " in game:
                away, home = game.split(" @ ", 1)
                clave = normalizar_matchup(away, home)
            else:
                clave = game.strip().lower()

        if clave in vistos:
            continue

        vistos.add(clave)
        filtrados.append(item)

    return filtrados


def obtener_carpeta_exportacion():
    carpeta_base = "exports_tiktok"
    carpeta_fecha = os.path.join(carpeta_base, hoy_str())
    os.makedirs(carpeta_fecha, exist_ok=True)
    return carpeta_fecha


def guardar_json_tiktok(data):
    carpeta = obtener_carpeta_exportacion()
    ruta = os.path.join(carpeta, "mlb_contenido.json")
    try:
        with open(ruta, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        return ruta
    except Exception as e:
        print(f"Error guardando JSON TikTok: {e}")
        return None


def safe_float(value, default=None):
    try:
        if value is None:
            return default
        return float(value)
    except Exception:
        return default


def safe_int(value, default=None):
    try:
        if value is None:
            return default
        return int(value)
    except Exception:
        return default


# =========================================================
# MLB DATA
# =========================================================
def obtener_standings():
    url = f"{MLB_BASE}/standings"
    params = {
        "leagueId": "103,104",
        "season": temporada_actual(),
        "standingsTypes": "regularSeason",
    }
    data = safe_get(url, params=params)
    equipos = {}

    for record in data.get("records", []):
        for t in record.get("teamRecords", []):
            name = t.get("team", {}).get("name")
            if not name:
                continue

            wins = t.get("wins", 0)
            losses = t.get("losses", 0)
            games = max(wins + losses, 1)

            home_w = t.get("homeWins", 0)
            home_l = t.get("homeLosses", 0)
            home_games = max(home_w + home_l, 1)

            away_w = t.get("awayWins", 0)
            away_l = t.get("awayLosses", 0)
            away_games = max(away_w + away_l, 1)

            rs = t.get("runsScored", 0)
            ra = t.get("runsAllowed", 0)
            last10_w = t.get("lastTenWins", 0)
            last10_l = t.get("lastTenLosses", 0)
            last10_games = max(last10_w + last10_l, 1)

            equipos[name] = {
                "wins": wins,
                "losses": losses,
                "win_pct": wins / games,
                "home_win_pct": home_w / home_games,
                "away_win_pct": away_w / away_games,
                "run_diff": rs - ra,
                "runs_scored": rs / games if games else 4.5,
                "runs_allowed": ra / games if games else 4.5,
                "last10_win_pct": last10_w / last10_games,
                "streak": t.get("streakCode", ""),
                "last10_record": f"{last10_w}-{last10_l}",
            }

    return equipos


def obtener_juegos_del_dia():
    url = f"{MLB_BASE}/schedule"
    params = {"sportId": 1, "date": hoy_str(), "hydrate": "probablePitcher,venue"}
    data = safe_get(url, params=params)
    dates = data.get("dates", [])
    if not dates or not isinstance(dates, list):
        return []

    first_date = dates[0]
    if not isinstance(first_date, dict):
        return []

    return first_date.get("games", [])


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
    base = {"era": 4.20, "whip": 1.30, "so9": 8.2, "ip": 0.0, "sample_ok": False}
    if not person_id:
        return base

    if season is None:
        season = temporada_actual()

    url = f"{MLB_BASE}/people/{person_id}/stats"
    params = {"stats": "season", "group": "pitching", "season": season, "gameType": "R"}
    data = safe_get(url, params=params)

    stats_list = data.get("stats", [])
    if not stats_list or not isinstance(stats_list, list):
        return base

    first_stats = stats_list[0]
    if not isinstance(first_stats, dict):
        return base

    splits = first_stats.get("splits", [])
    if not splits or not isinstance(splits, list):
        return base

    first_split = splits[0]
    if not isinstance(first_split, dict):
        return base

    stat = first_split.get("stat", {})

    era = float(stat.get("era", 4.20) or 4.20)
    whip = float(stat.get("whip", 1.30) or 1.30)

    innings_pitched = stat.get("inningsPitched", "0")
    try:
        ip = float(str(innings_pitched).replace(",", ""))
    except Exception:
        ip = 0.0

    strikeouts = stat.get("strikeOuts", 0)
    try:
        strikeouts = int(strikeouts)
    except Exception:
        strikeouts = 0

    so9 = (strikeouts * 9 / ip) if ip > 0 else 8.2

    return {
        "era": round(era, 2),
        "whip": round(whip, 2),
        "so9": round(so9, 2),
        "ip": round(ip, 1),
        "sample_ok": ip >= 10,
    }


@lru_cache(maxsize=128)
def obtener_venue_detalle(venue_id):
    if not venue_id:
        return {}

    url = f"{MLB_BASE}/venues"
    params = {"venueIds": str(venue_id)}
    data = safe_get(url, params=params)

    venues = data.get("venues", [])
    if not venues or not isinstance(venues, list):
        return {}

    return venues[0]


@lru_cache(maxsize=128)
def geocodificar_lugar(nombre_lugar):
    if not nombre_lugar:
        return None

    url = "https://geocoding-api.open-meteo.com/v1/search"
    params = {"name": nombre_lugar, "count": 1, "language": "en", "format": "json"}
    data = safe_get(url, params=params)
    results = data.get("results", [])
    if not results:
        return None

    r = results[0]
    return {"latitude": r.get("latitude"), "longitude": r.get("longitude")}


def extraer_coords_venue(venue):
    if not venue:
        return None

    location = venue.get("location", {}) or {}
    default_coords = location.get("defaultCoordinates", {}) or {}
    lat = default_coords.get("latitude")
    lon = default_coords.get("longitude")
    if lat is not None and lon is not None:
        return {"latitude": lat, "longitude": lon}

    venue_name = venue.get("name", "")
    city = location.get("city", "")
    state = location.get("stateAbbrev", "") or location.get("state", "")
    query = ", ".join([x for x in [venue_name, city, state] if x])

    return geocodificar_lugar(query)


def obtener_clima_partido(game):
    try:
        venue_id = game.get("venue", {}).get("id")
        venue = obtener_venue_detalle(venue_id)
        coords = extraer_coords_venue(venue)

        if not coords:
            return {"temp_c": None, "wind_kmh": None, "precip_mm": None}

        game_date = game.get("gameDate")
        if not game_date:
            return {"temp_c": None, "wind_kmh": None, "precip_mm": None}

        dt_utc = datetime.fromisoformat(game_date.replace("Z", "+00:00"))

        url = "https://api.open-meteo.com/v1/forecast"
        params = {
            "latitude": coords["latitude"],
            "longitude": coords["longitude"],
            "hourly": "temperature_2m,precipitation,wind_speed_10m",
            "timezone": "auto",
            "forecast_days": 2,
        }
        data = safe_get(url, params=params)
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
                diff = abs(
                    (
                        dt_local.replace(tzinfo=None) - dt_utc.replace(tzinfo=None)
                    ).total_seconds()
                )
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
        print(f"Error obteniendo clima: {e}")
        return {"temp_c": None, "wind_kmh": None, "precip_mm": None}


# =========================================================
# MODELO
# =========================================================
def score_pitcher_real(stats):
    era = stats.get("era", 4.20)
    whip = stats.get("whip", 1.30)
    so9 = stats.get("so9", 8.2)
    ip = stats.get("ip", 0.0)
    sample_ok = stats.get("sample_ok", False)

    score = 0.0

    if era <= 2.80:
        score += 0.32
    elif era <= 3.40:
        score += 0.22
    elif era <= 4.00:
        score += 0.10
    elif era > 4.60:
        score -= 0.14

    if whip <= 1.05:
        score += 0.20
    elif whip <= 1.18:
        score += 0.12
    elif whip <= 1.30:
        score += 0.04
    elif whip > 1.40:
        score -= 0.10

    if so9 >= 10.5:
        score += 0.12
    elif so9 >= 9.0:
        score += 0.08
    elif so9 >= 8.0:
        score += 0.03
    elif so9 < 6.5:
        score -= 0.05

    if not sample_ok or ip < 10:
        score *= 0.75

    return round(score, 3)


def ajuste_clima_total(weather):
    if not weather:
        return 0.0

    temp_c = weather.get("temp_c")
    wind_kmh = weather.get("wind_kmh")
    precip_mm = weather.get("precip_mm")

    adj = 0.0
    if temp_c is not None:
        if temp_c >= 28:
            adj += 0.35
        elif temp_c >= 24:
            adj += 0.20
        elif temp_c <= 10:
            adj -= 0.30
        elif temp_c <= 15:
            adj -= 0.15

    if wind_kmh is not None:
        if wind_kmh >= 25:
            adj += 0.20
        elif wind_kmh >= 18:
            adj += 0.10

    if precip_mm is not None and precip_mm >= 1.0:
        adj -= 0.20

    return round(adj, 2)


def ajuste_clima_ml(weather):
    if not weather:
        return 0.0

    temp_c = weather.get("temp_c")
    precip_mm = weather.get("precip_mm")

    adj = 0.0
    if precip_mm is not None and precip_mm >= 1.0:
        adj -= 0.01
    if temp_c is not None and temp_c <= 8:
        adj -= 0.01
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
):
    away = standings.get(away_team)
    home = standings.get(home_team)

    if not away or not home:
        return 0.50

    diff_win_pct = home["win_pct"] - away["win_pct"]
    diff_split = home["home_win_pct"] - away["away_win_pct"]
    diff_last10 = home["last10_win_pct"] - away["last10_win_pct"]
    diff_run_diff = (home["run_diff"] - away["run_diff"]) / 100.0
    diff_streak = parse_streak(home["streak"]) - parse_streak(away["streak"])
    diff_runs_scored = (
        home.get("runs_scored", 4.5) - away.get("runs_scored", 4.5)
    ) / 10.0
    diff_runs_allowed = (
        away.get("runs_allowed", 4.5) - home.get("runs_allowed", 4.5)
    ) / 10.0

    if away_pitcher_stats is None:
        away_pitcher_stats = {
            "era": 4.20,
            "whip": 1.30,
            "so9": 8.2,
            "ip": 0.0,
            "sample_ok": False,
        }
    if home_pitcher_stats is None:
        home_pitcher_stats = {
            "era": 4.20,
            "whip": 1.30,
            "so9": 8.2,
            "ip": 0.0,
            "sample_ok": False,
        }

    p_home = score_pitcher_real(home_pitcher_stats)
    p_away = score_pitcher_real(away_pitcher_stats)
    diff_pitcher = p_home - p_away

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
    score += ajuste_clima_ml(weather)

    if away_pitcher == "TBD":
        score += 0.04
    if home_pitcher == "TBD":
        score -= 0.04

    prob = logistic(score)

    if 0.495 <= prob <= 0.505:
        if score >= 0:
            prob = 0.518
        else:
            prob = 0.482

    return clamp(prob, 0.32, 0.68)


def obtener_pick_juego_pro(
    away_team,
    home_team,
    standings,
    away_pitcher="TBD",
    home_pitcher="TBD",
    away_pitcher_stats=None,
    home_pitcher_stats=None,
    weather=None,
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
):
    away = standings.get(away_team, {})
    home = standings.get(home_team, {})

    total = 8.6

    away_rs = away.get("runs_scored", 4.5)
    home_rs = home.get("runs_scored", 4.5)
    away_ra = away.get("runs_allowed", 4.5)
    home_ra = home.get("runs_allowed", 4.5)

    total += ((away_rs + home_rs) - 9.0) * 0.24
    total += ((away_ra + home_ra) - 9.0) * 0.20
    total += ((away.get("run_diff", 0) + home.get("run_diff", 0)) / 162.0) * 0.22

    if away_pitcher_stats is None:
        away_pitcher_stats = {
            "era": 4.20,
            "whip": 1.30,
            "so9": 8.2,
            "ip": 0.0,
            "sample_ok": False,
        }
    if home_pitcher_stats is None:
        home_pitcher_stats = {
            "era": 4.20,
            "whip": 1.30,
            "so9": 8.2,
            "ip": 0.0,
            "sample_ok": False,
        }

    total += (away_pitcher_stats.get("era", 4.20) - 4.00) * 0.34
    total += (home_pitcher_stats.get("era", 4.20) - 4.00) * 0.34
    total += (away_pitcher_stats.get("whip", 1.30) - 1.25) * 0.82
    total += (home_pitcher_stats.get("whip", 1.30) - 1.25) * 0.82
    total -= (away_pitcher_stats.get("so9", 8.2) - 8.5) * 0.10
    total -= (home_pitcher_stats.get("so9", 8.2) - 8.5) * 0.10

    if away_pitcher == "TBD":
        total += 0.45
    if home_pitcher == "TBD":
        total += 0.45

    last10_away = away.get("last10_win_pct", 0.5)
    last10_home = home.get("last10_win_pct", 0.5)
    total += ((last10_away + last10_home) - 1.0) * 0.25

    total += ajuste_clima_total(weather)

    return round(clamp(total, 6.2, 12.8), 1)


def elegir_total_pick(total_proyectado, total_line):
    if total_line is None:
        return None

    diff = total_proyectado - total_line

    if diff >= 0.85:
        return {
            "pick": f"Over {total_line}",
            "edge": round(diff, 2),
            "strength": "Alta",
        }
    if diff >= 0.55:
        return {
            "pick": f"Over {total_line}",
            "edge": round(diff, 2),
            "strength": "Media",
        }
    if diff <= -0.85:
        return {
            "pick": f"Under {total_line}",
            "edge": round(abs(diff), 2),
            "strength": "Alta",
        }
    if diff <= -0.55:
        return {
            "pick": f"Under {total_line}",
            "edge": round(abs(diff), 2),
            "strength": "Media",
        }

    return None


def elegir_total_pick_fallback(total_proyectado):
    if total_proyectado >= 9.4:
        return {
            "pick": "Over 8.5",
            "edge": round(total_proyectado - 8.5, 2),
            "strength": "Fallback",
        }
    if total_proyectado <= 7.6:
        return {
            "pick": "Under 8.5",
            "edge": round(8.5 - total_proyectado, 2),
            "strength": "Fallback",
        }
    return None


def clasificar_apuesta(prob_model, implied_prob, avoid=False):
    if avoid:
        return None

    edge = prob_model - implied_prob

    if edge >= 0.04:
        return "A"
    if edge >= 0.025:
        return "B"
    if edge >= 0.01:
        return "C"
    return None


def stake_sugerido(grade):
    if grade == "A":
        return "1.5u"
    if grade == "B":
        return "1.0u"
    if grade == "C":
        return "0.5u"
    return "Pass"


def american_to_decimal(american_odds):
    try:
        american_odds = float(american_odds)
        if american_odds > 0:
            return 1 + (american_odds / 100)
        return 1 + (100 / abs(american_odds))
    except Exception:
        return None


def calcular_ev(prob_model, american_odds):
    """
    EV por unidad apostada.
    Fórmula:
    EV = p*(cuota_decimal-1) - (1-p)
    """
    dec = american_to_decimal(american_odds)
    if dec is None:
        return None

    try:
        p = float(prob_model)
        ev = (p * (dec - 1)) - (1 - p)
        return round(ev, 4)
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


# =========================================================
# ODDS
# =========================================================
def normalizar_nombre_equipo_odds(team_name):
    mapping = {
        "Arizona Diamondbacks": "Arizona Diamondbacks",
        "Atlanta Braves": "Atlanta Braves",
        "Baltimore Orioles": "Baltimore Orioles",
        "Boston Red Sox": "Boston Red Sox",
        "Chicago Cubs": "Chicago Cubs",
        "Chicago White Sox": "Chicago White Sox",
        "Cincinnati Reds": "Cincinnati Reds",
        "Cleveland Guardians": "Cleveland Guardians",
        "Colorado Rockies": "Colorado Rockies",
        "Detroit Tigers": "Detroit Tigers",
        "Houston Astros": "Houston Astros",
        "Kansas City Royals": "Kansas City Royals",
        "Los Angeles Angels": "Los Angeles Angels",
        "Los Angeles Dodgers": "Los Angeles Dodgers",
        "Miami Marlins": "Miami Marlins",
        "Milwaukee Brewers": "Milwaukee Brewers",
        "Minnesota Twins": "Minnesota Twins",
        "New York Mets": "New York Mets",
        "New York Yankees": "New York Yankees",
        "Athletics": "Oakland Athletics",
        "Philadelphia Phillies": "Philadelphia Phillies",
        "Pittsburgh Pirates": "Pittsburgh Pirates",
        "San Diego Padres": "San Diego Padres",
        "San Francisco Giants": "San Francisco Giants",
        "Seattle Mariners": "Seattle Mariners",
        "St. Louis Cardinals": "St Louis Cardinals",
        "Tampa Bay Rays": "Tampa Bay Rays",
        "Texas Rangers": "Texas Rangers",
        "Toronto Blue Jays": "Toronto Blue Jays",
        "Washington Nationals": "Washington Nationals",
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


def obtener_odds_completas(away_team, home_team):
    if not ODDS_API_KEY:
        return None

    try:
        url = "https://api.the-odds-api.com/v4/sports/baseball_mlb/odds"
        params = {
            "apiKey": ODDS_API_KEY,
            "regions": "us",
            "markets": "h2h,totals",
            "oddsFormat": "american",
        }

        data = safe_get(url, params=params)

        away_norm = normalizar_nombre_equipo_odds(away_team)
        home_norm = normalizar_nombre_equipo_odds(home_team)
        away_key = team_key(away_norm)
        home_key = team_key(home_norm)

        if not isinstance(data, list):
            return None
        mejor_evento = None
        mejor_score = -1

        for event in data:
            home_name = event.get("home_team", "")
            teams = event.get("teams", [])
            away_name = [t for t in teams if t != home_name]
            away_name = away_name[0] if away_name else ""
            home_key_event = team_key(home_name)
            away_key_event = team_key(away_name)
            same_exact = home_name == home_norm and away_name == away_norm
            same_fuzzy = home_key_event == home_key and away_key_event == away_key
            swapped_fuzzy = home_key_event == away_key and away_key_event == home_key

            if same_exact or same_fuzzy or swapped_fuzzy:
                if swapped_fuzzy:
                    mapped_home_name = away_name
                    mapped_away_name = home_name
                else:
                    mapped_home_name = home_name
                    mapped_away_name = away_name
                bookmakers = event.get("bookmakers", [])
                for book in bookmakers:
                    resultado = {
                        "bookmaker": book.get("title", "Bookmaker"),
                        "home_moneyline": None,
                        "away_moneyline": None,
                        "total_line": None,
                        "over_price": None,
                        "under_price": None,
                    }

                    for market in book.get("markets", []):
                        key = market.get("key")

                        if key == "h2h":
                            for o in market.get("outcomes", []):
                                if o.get("name") == mapped_home_name:
                                    resultado["home_moneyline"] = o.get("price")
                                elif o.get("name") == mapped_away_name:
                                    resultado["away_moneyline"] = o.get("price")

                        elif key == "totals":
                            for o in market.get("outcomes", []):
                                if o.get("name") == "Over":
                                    resultado["total_line"] = o.get("point")
                                    resultado["over_price"] = o.get("price")
                                elif o.get("name") == "Under":
                                    resultado["under_price"] = o.get("price")

                    return resultado

            score_direct = score_team_match(home_name, home_norm) + score_team_match(
                away_name, away_norm
            )
            score_swap = score_team_match(home_name, away_norm) + score_team_match(
                away_name, home_norm
            )
            score_event = max(score_direct, score_swap)
            if score_event > mejor_score:
                mejor_score = score_event
                mejor_evento = (event, score_swap > score_direct)

        if mejor_evento and mejor_score >= 120:
            event, usar_swap = mejor_evento
            home_name = event.get("home_team", "")
            teams = event.get("teams", [])
            away_name = [t for t in teams if t != home_name]
            away_name = away_name[0] if away_name else ""
            if usar_swap:
                mapped_home_name = away_name
                mapped_away_name = home_name
            else:
                mapped_home_name = home_name
                mapped_away_name = away_name

            for book in event.get("bookmakers", []):
                resultado = {
                    "bookmaker": book.get("title", "Bookmaker"),
                    "home_moneyline": None,
                    "away_moneyline": None,
                    "total_line": None,
                    "over_price": None,
                    "under_price": None,
                }
                for market in book.get("markets", []):
                    key = market.get("key")
                    if key == "h2h":
                        for o in market.get("outcomes", []):
                            if o.get("name") == mapped_home_name:
                                resultado["home_moneyline"] = o.get("price")
                            elif o.get("name") == mapped_away_name:
                                resultado["away_moneyline"] = o.get("price")
                    elif key == "totals":
                        for o in market.get("outcomes", []):
                            if o.get("name") == "Over":
                                resultado["total_line"] = o.get("point")
                                resultado["over_price"] = o.get("price")
                            elif o.get("name") == "Under":
                                resultado["under_price"] = o.get("price")
                return resultado

        return None

    except Exception as e:
        print(f"Error obteniendo odds completas: {e}")
        return None


def calcular_prob_total_modelo(total_diff):
    magnitud = abs(total_diff)
    if magnitud < 0.55:
        return None
    base = 0.50 + min(magnitud, 1.8) * 0.055
    return clamp(base, 0.52, 0.64)


def score_pick_ml(analisis):
    score = 0.0
    score += analisis["confidence_pct"] * 0.48
    score += analisis["ml_edge_pct"] * 1.15
    score += analisis["ev_ml_pct"] * 1.10
    score += analisis["pitching_advantage"] * 6.0
    score += analisis["recent_form_advantage"] * 30.0
    if analisis["is_home_pick"]:
        score += 0.7
    if analisis["risk_flags"].get("tbd_pitcher"):
        score -= 7.0
    if analisis["risk_flags"].get("sin_odds_ml"):
        score -= 1.4
    if analisis["risk_flags"].get("cuota_extrema_ml"):
        score -= 3.0
    return round(score, 2)


def score_pick_total(analisis):
    if not analisis.get("total_pick"):
        return 0.0
    score = 0.0
    score += analisis["confidence_pct"] * 0.32
    score += analisis["total_edge"] * 6.2
    score += analisis["ev_total_pct"] * 1.15
    if analisis["total_pick"]["strength"] == "Alta":
        score += 2.4
    if analisis["risk_flags"].get("sin_odds_total"):
        score -= 1.2
    if analisis["risk_flags"].get("clima_extremo"):
        score -= 1.0
    return round(score, 2)


def analizar_juego(game, standings):
    teams = game.get("teams", {})
    away_data = teams.get("away", {})
    home_data = teams.get("home", {})
    away = away_data.get("team", {}).get("name")
    home = home_data.get("team", {}).get("name")
    if not away or not home:
        return None

    away_pitcher_obj = away_data.get("probablePitcher", {}) or {}
    home_pitcher_obj = home_data.get("probablePitcher", {}) or {}
    away_pitcher = away_pitcher_obj.get("fullName", "TBD")
    home_pitcher = home_pitcher_obj.get("fullName", "TBD")
    away_stats = obtener_stats_pitcher_reales(away_pitcher_obj.get("id"))
    home_stats = obtener_stats_pitcher_reales(home_pitcher_obj.get("id"))
    weather = obtener_clima_partido(game) or {
        "temp_c": None,
        "wind_kmh": None,
        "precip_mm": None,
    }

    pred = obtener_pick_juego_pro(
        away,
        home,
        standings,
        away_pitcher,
        home_pitcher,
        away_stats,
        home_stats,
        weather,
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
    )
    odds = obtener_odds_completas(away, home)

    ml_odds = None
    total_line = None
    total_pick = None
    total_odds = None
    prob_total_model = None
    implied_ml = None
    implied_total = None
    ev_ml = None
    ev_total = None
    ml_edge_pct = 0.0
    total_edge = 0.0

    if odds and isinstance(odds, dict):
        if pred["favorite"] == home:
            ml_odds = odds.get("home_moneyline")
        else:
            ml_odds = odds.get("away_moneyline")

        total_line = odds.get("total_line")
        total_pick = elegir_total_pick(total_proj, total_line)
        if total_pick:
            total_edge = total_pick["edge"]
            total_odds = (
                odds.get("over_price")
                if "Over" in total_pick["pick"]
                else odds.get("under_price")
            )
            prob_total_model = calcular_prob_total_modelo(total_proj - total_line)
            if prob_total_model is not None and total_odds is not None:
                implied_total = moneyline_to_prob(total_odds)
                ev_total = calcular_ev(prob_total_model, total_odds)

    if total_pick is None:
        total_pick = elegir_total_pick_fallback(total_proj)
        if total_pick:
            total_edge = total_pick["edge"]
            total_line = 8.5 if total_line is None else total_line

    if ml_odds is not None:
        implied_ml = moneyline_to_prob(ml_odds)
        ev_ml = calcular_ev(pred["prob_favorite"], ml_odds)
        if implied_ml is not None:
            ml_edge_pct = round((pred["prob_favorite"] - implied_ml) * 100, 2)
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
        "usando_fallback_modelo": ml_odds is None or total_odds is None,
        "cuota_extrema_ml": ml_odds is not None and (ml_odds <= -220 or ml_odds >= 170),
        "clima_extremo": weather.get("wind_kmh") is not None
        and weather.get("wind_kmh") >= 30,
    }

    analisis = {
        "game": f"{away} @ {home}",
        "away": away,
        "home": home,
        "matchup_key": normalizar_matchup(away, home),
        "pitchers": {"away": away_pitcher, "home": home_pitcher},
        "pitcher_stats": {"away": away_stats, "home": home_stats},
        "clima": {
            "temp_c": safe_float(weather.get("temp_c")),
            "wind_kmh": safe_float(weather.get("wind_kmh")),
            "precip_mm": safe_float(weather.get("precip_mm")),
        },
        "ml_pick": f"{pred['favorite']} ML",
        "favorite": pred["favorite"],
        "is_home_pick": pred["favorite"] == home,
        "prob_favorite": pred["prob_favorite"],
        "confidence_pct": pred["confidence_pct"],
        "confidence_label": pred["confidence_label"],
        "ml_odds": safe_int(ml_odds),
        "has_valid_ml_odds": ml_odds is not None,
        "ml_edge_pct": ml_edge_pct,
        "ev_ml": ev_ml if ev_ml is not None else None,
        "ev_ml_pct": round(ev_ml * 100, 2) if ev_ml is not None else 0.0,
        "grade_ml": grade_por_ev(ev_ml) if ev_ml is not None else "D",
        "stake_ml": stake_por_ev(ev_ml) if ev_ml is not None else "0u",
        "total_projection": total_proj,
        "total_line": safe_float(total_line),
        "total_pick": total_pick,
        "total_odds": safe_int(total_odds),
        "has_valid_total_odds": total_odds is not None,
        "prob_total_model": prob_total_model,
        "ev_total": ev_total if ev_total is not None else None,
        "ev_total_pct": round(ev_total * 100, 2) if ev_total is not None else 0.0,
        "grade_total": grade_por_ev(ev_total) if ev_total is not None else "D",
        "stake_total": stake_por_ev(ev_total) if ev_total is not None else "0u",
        "total_edge": total_edge,
        "risk_flags": risk_flags,
        "pitching_advantage": score_pitcher_real(home_stats)
        - score_pitcher_real(away_stats),
        "recent_form_advantage": favorite_form - underdog_form,
    }

    analisis["score_ml"] = score_pick_ml(analisis)
    analisis["score_total"] = score_pick_total(analisis)
    analisis["score_agresivo"] = round(
        analisis["score_ml"] * 0.65 + analisis["score_total"] * 0.35, 2
    )
    print(
        "[ANALISIS]",
        analisis["game"],
        "ml_odds=",
        analisis["ml_odds"],
        "total_odds=",
        analisis["total_odds"],
        "conf=",
        analisis["confidence_pct"],
        "ml_edge=",
        analisis["ml_edge_pct"],
        "ev_ml=",
        analisis["ev_ml_pct"],
        "score_ml=",
        analisis["score_ml"],
        "score_total=",
        analisis["score_total"],
        "score_agresivo=",
        analisis["score_agresivo"],
    )
    return analisis


def obtener_analisis_del_dia():
    standings = obtener_standings()
    games = obtener_juegos_del_dia()
    analisis = []
    for game in games:
        item = analizar_juego(game, standings)
        if item:
            analisis.append(item)
    return analisis


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
    }

    if not analisis_juegos:
        return data

    picks_ml = []
    picks_totals = []
    picks_modelo = []
    candidatos_parley = []
    candidatos_millonario = []

    for a in analisis_juegos:
        data["juegos_del_dia"].append(
            {
                "game": a["game"],
                "matchup_key": a["matchup_key"],
                "away": a["away"],
                "home": a["home"],
                "pitchers": a["pitchers"],
            }
        )
        picks_modelo.append(
            {
                "game": a["game"],
                "matchup_key": a["matchup_key"],
                "pick": a["ml_pick"],
                "confianza": a["confidence_pct"],
                "confianza_label": a["confidence_label"],
                "pitchers": f"{a['pitchers']['away']} vs {a['pitchers']['home']}",
                "era": f"{a['pitcher_stats']['away']['era']} vs {a['pitcher_stats']['home']['era']}",
                "total_proyectado": a["total_projection"],
                "clima": a["clima"],
            }
        )

        if a["grade_ml"] != "D":
            picks_ml.append(
                {
                    "game": a["game"],
                    "matchup_key": a["matchup_key"],
                    "pick": a["ml_pick"],
                    "grade": a["grade_ml"],
                    "stake": a["stake_ml"],
                    "model_prob": round(a["prob_favorite"] * 100, 1),
                    "implied_prob": (
                        None
                        if a["ml_odds"] is None
                        else round(moneyline_to_prob(a["ml_odds"]) * 100, 1)
                    ),
                    "edge": a["ml_edge_pct"],
                    "ev_pct": a["ev_ml_pct"],
                    "cuota": a["ml_odds"] if a["ml_odds"] is not None else "N/D",
                    "confianza": a["confidence_pct"],
                    "pitchers": f"{a['pitchers']['away']} vs {a['pitchers']['home']}",
                    "era": f"{a['pitcher_stats']['away']['era']} vs {a['pitcher_stats']['home']['era']}",
                    "clima": a["clima"],
                }
            )

        if a.get("total_pick") and a["grade_total"] != "D":
            picks_totals.append(
                {
                    "game": a["game"],
                    "matchup_key": a["matchup_key"],
                    "pick": a["total_pick"]["pick"],
                    "grade": a["grade_total"],
                    "stake": a["stake_total"],
                    "ev_pct": a["ev_total_pct"],
                    "edge": a["total_edge"],
                    "projection": a["total_projection"],
                    "line": a["total_line"],
                    "model_prob": round((a["prob_total_model"] or 0) * 100, 1),
                    "implied_prob": (
                        None
                        if a["total_odds"] is None
                        else round(moneyline_to_prob(a["total_odds"]) * 100, 1)
                    ),
                    "cuota": a["total_odds"] if a["total_odds"] is not None else "N/D",
                    "confianza": a["confidence_pct"],
                    "pitchers": f"{a['pitchers']['away']} vs {a['pitchers']['home']}",
                    "era": f"{a['pitcher_stats']['away']['era']} vs {a['pitcher_stats']['home']['era']}",
                    "clima": a["clima"],
                }
            )

        candidatos_parley.append(
            {
                "game": a["game"],
                "matchup_key": a["matchup_key"],
                "pick": a["ml_pick"],
                "grade": a["grade_ml"],
                "edge": a["ml_edge_pct"],
                "ev_pct": a["ev_ml_pct"],
                "confianza": a["confidence_pct"],
                "cuota": a["ml_odds"] if a["ml_odds"] is not None else "N/D",
            }
        )

        candidatos_millonario.append(
            {
                "tipo": "ML",
                "game": a["game"],
                "matchup_key": a["matchup_key"],
                "pick": a["ml_pick"],
                "edge": a["ml_edge_pct"],
                "confianza": a["confidence_pct"],
                "cuota": a["ml_odds"] if a["ml_odds"] is not None else "N/D",
            }
        )
        if a.get("total_pick"):
            candidatos_millonario.append(
                {
                    "tipo": "TOTAL",
                    "game": a["game"],
                    "matchup_key": a["matchup_key"],
                    "pick": a["total_pick"]["pick"],
                    "edge": a["total_edge"],
                    "confianza": a["confidence_pct"],
                    "cuota": a["total_odds"] if a["total_odds"] is not None else "N/D",
                }
            )

    picks_ml.sort(key=lambda x: (x["ev_pct"], x["confianza"]), reverse=True)
    picks_totals.sort(key=lambda x: (x["ev_pct"], x["edge"]), reverse=True)
    picks_modelo.sort(key=lambda x: x["confianza"], reverse=True)
    candidatos_parley.sort(key=lambda x: (x["ev_pct"], x["confianza"]), reverse=True)
    candidatos_millonario.sort(key=lambda x: (x["confianza"], x["edge"]), reverse=True)

    picks_ml = filtrar_matchups_unicos(picks_ml)
    picks_totals = filtrar_matchups_unicos(picks_totals)
    picks_modelo = filtrar_matchups_unicos(picks_modelo)
    candidatos_parley = filtrar_matchups_unicos(candidatos_parley)
    candidatos_millonario = filtrar_matchups_unicos(candidatos_millonario)

    data["pronosticos"] = picks_modelo[:8]
    data["apuestas"]["moneyline_ev"] = picks_ml[:5]
    data["apuestas"]["totales_ev"] = picks_totals[:5]
    data["apuestas"]["modelo"] = picks_modelo[:5]
    data["parley"] = candidatos_parley[:3]
    data["parley_millonario"] = candidatos_millonario[:10]

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

        if call.data == "cmd_hoy":
            hoy(call.message)
        elif call.data == "cmd_posiciones":
            posiciones(call.message)
        elif call.data == "cmd_apuestas":
            apuestas(call.message)
        elif call.data == "cmd_parley":
            parley(call.message)
        elif call.data == "cmd_parley_millonario":
            parley_millonario(call.message)
        elif call.data == "cmd_pitchers":
            pitchers(call.message)
        elif call.data == "cmd_pronosticos":
            pronosticos(call.message)
        elif call.data == "cmd_lesionados":
            lesionados(call.message)
        elif call.data == "cmd_roi":
            roi(call.message)
        elif call.data == "cmd_exportar_json":
            exportar_json(call.message)
        elif call.data == "cmd_stats_parlays":
            stats_parleys(call.message)
        elif call.data == "cmd_parley_ganado":
            parley_ganado(call.message)
        elif call.data == "cmd_parley_fallado":
            parley_fallado(call.message)
        elif call.data == "cmd_millonario_ganado":
            millonario_ganado(call.message)
        elif call.data == "cmd_millonario_fallado":
            millonario_fallado(call.message)
        elif call.data == "cmd_reset_parley":
            reset_parley(call.message)
        elif call.data == "cmd_reset_millonario":
            reset_millonario(call.message)

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
        "Predicciones MLB con estilo premium.\n\n"
        "• Moneyline con edge\n"
        "• Totales con modelo\n"
        "• ERA real + clima\n"
        "• Parlays filtrados\n"
        "• Fallback al modelo\n\n"
        f"🧪 Versión activa: <b>{BOT_VERSION}</b>\n\n"
        "Selecciona una opción:"
    )
    bot.send_message(
        message.chat.id, texto, parse_mode="HTML", reply_markup=menu_markup()
    )


@bot.message_handler(commands=["hoy"])
def hoy(message):
    msg = bot.reply_to(message, "📅 Cargando juegos del día...")
    try:
        games = obtener_juegos_del_dia()
        fecha = hoy_str()

        if not games:
            bot.edit_message_text(
                f"📅 JUEGOS DE HOY ({fecha})\n\nNo hay juegos programados hoy.",
                msg.chat.id,
                msg.message_id,
            )
            return

        juegos_ordenados = []

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
                if ZoneInfo:
                    dt_ve = dt_utc.astimezone(ZoneInfo("America/Caracas"))
                else:
                    dt_ve = dt_utc - timedelta(hours=4)

                hora_orden = dt_ve
                hora_txt = dt_ve.strftime("%I:%M %p")
            except Exception:
                hora_orden = None
                hora_txt = "Hora no disponible"

            juegos_ordenados.append(
                {
                    "away": away,
                    "home": home,
                    "score_away": sa,
                    "score_home": sh,
                    "status": status,
                    "hora_txt": hora_txt,
                    "hora_orden": hora_orden,
                }
            )

        juegos_ordenados.sort(
            key=lambda x: x["hora_orden"] if x["hora_orden"] else datetime.max
        )

        texto = header("JUEGOS DE HOY", "📅")
        texto += f"🗓️ {fecha} | Hora de Venezuela\n\n"

        for i, j in enumerate(juegos_ordenados, 1):
            texto += card_game(
                f"{i}. {j['away']} @ {j['home']}",
                [
                    f"🕒 {j['hora_txt']} VET",
                    f"📌 {j['status']}",
                    f"⚾️ Score: {j['score_away']} - {j['score_home']}",
                ],
            )

        bot.delete_message(msg.chat.id, msg.message_id)
        responder_largo(message.chat.id, texto, parse_mode="HTML")

    except Exception as e:
        bot.edit_message_text(
            f"❌ Error al cargar juegos: {str(e)[:120]}", msg.chat.id, msg.message_id
        )


@bot.message_handler(commands=["posiciones"])
def posiciones(message):
    msg = bot.reply_to(message, "🏆 Cargando standings estilo ESPN...")
    try:
        season = temporada_actual()
        url = f"{MLB_BASE}/standings"
        params = {
            "leagueId": "103,104",
            "season": season,
            "standingsTypes": "regularSeason",
        }

        data = safe_get(url, params=params)
        records = data.get("records", [])

        if not records:
            bot.edit_message_text(
                "❌ No pude cargar los standings.", msg.chat.id, msg.message_id
            )
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
                home = f"{team.get('homeWins', 0)}-{team.get('homeLosses', 0)}"
                away = f"{team.get('awayWins', 0)}-{team.get('awayLosses', 0)}"
                l10 = f"{team.get('lastTenWins', 0)}-{team.get('lastTenLosses', 0)}"
                strk = str(team.get("streakCode", "-"))

                fila = (
                    f"{nombre[:20].ljust(20)} "
                    f"{str(wins).rjust(3)} "
                    f"{str(losses).rjust(3)} "
                    f"{str(pct).rjust(5)} "
                    f"{gb.rjust(4)} "
                    f"{home.rjust(6)} "
                    f"{away.rjust(6)} "
                    f"{l10.rjust(5)} "
                    f"{strk.rjust(5)}"
                )
                lineas.append(fila)

            bloques.append("<pre>" + "\n".join(lineas) + "</pre>")

        bot.delete_message(msg.chat.id, msg.message_id)
        bot.send_message(message.chat.id, titulo, parse_mode="HTML")

        for bloque in bloques:
            bot.send_message(message.chat.id, bloque, parse_mode="HTML")

    except Exception as e:
        bot.edit_message_text(
            f"❌ Error al cargar posiciones: {str(e)[:120]}",
            msg.chat.id,
            msg.message_id,
        )


@bot.message_handler(commands=["apuestas"])
def apuestas(message):
    msg = bot.reply_to(message, "🔥 Analizando juegos con EV + stake automático...")
    try:
        analisis_juegos = obtener_analisis_del_dia()

        texto = header("APUESTAS PRO MLB", "💰")
        texto += f"📅 {hoy_str()}\n\n"

        if not analisis_juegos:
            texto += "No hay juegos hoy."
            bot.edit_message_text(texto, msg.chat.id, msg.message_id)
            return

        picks_ml = [
            a
            for a in analisis_juegos
            if a["grade_ml"] != "D" and not a["risk_flags"]["tbd_pitcher"]
        ]
        picks_totals = [
            a
            for a in analisis_juegos
            if a.get("total_pick") and a["grade_total"] != "D"
        ]
        picks_modelo = [
            a for a in analisis_juegos if not a["risk_flags"]["tbd_pitcher"]
        ]

        picks_ml.sort(key=lambda x: (x["score_ml"], x["ev_ml_pct"]), reverse=True)
        picks_totals.sort(
            key=lambda x: (x["score_total"], x["ev_total_pct"]), reverse=True
        )
        picks_modelo.sort(key=lambda x: x["confidence_pct"], reverse=True)

        texto += "💰 <b>MONEYLINE CON EV+</b>\n━━━━━━━━━━━━━━━━━━\n\n"
        if picks_ml:
            for p in picks_ml[:5]:
                texto += card_game(
                    f"{p['away']} @ {p['home']}",
                    [
                        f"🎯 Pick: <b>{p['ml_pick']}</b>",
                        f"🏷️ Grade: <b>{p['grade_ml']}</b> | Stake: <b>{p['stake_ml']}</b>",
                        f"💵 Cuota: <b>{p['ml_odds'] if p['ml_odds'] is not None else 'N/D'}</b>",
                        f"🧠 Modelo: {round(p['prob_favorite']*100, 1)}% | Confianza: {p['confidence_pct']}%",
                        f"📈 Edge: <b>{p['ml_edge_pct']}%</b> | EV: <b>{p['ev_ml_pct']}%</b>",
                        f"🎽 Pitchers: {p['pitchers']['away']} vs {p['pitchers']['home']}",
                        f"📉 ERA: {p['pitcher_stats']['away']['era']} vs {p['pitcher_stats']['home']['era']}",
                        f"🌡️ Clima: {p['clima'].get('temp_c')}°C | 💨 {p['clima'].get('wind_kmh')} km/h",
                    ],
                )
        else:
            texto += "No hubo moneylines con EV positivo suficiente.\n\n"
        texto += "📊 <b>TOTALES CON EV+</b>\n━━━━━━━━━━━━━━━━━━\n\n"
        if picks_totals:
            for p in picks_totals[:5]:
                texto += card_game(
                    f"{p['away']} @ {p['home']}",
                    [
                        f"🎯 Pick: <b>{p['total_pick']['pick']}</b>",
                        f"🏷️ Grade: <b>{p['grade_total']}</b> | Stake: <b>{p['stake_total']}</b>",
                        f"💵 Cuota: <b>{p['total_odds'] if p['total_odds'] is not None else 'N/D'}</b>",
                        f"📊 Proyección: {p['total_projection']} | Línea: {p['total_line']}",
                        f"🧠 Modelo total: {round((p['prob_total_model'] or 0)*100, 1)}%",
                        f"📈 Edge: <b>{p['total_edge']}</b> | EV: <b>{p['ev_total_pct']}%</b>",
                        f"🎽 Pitchers: {p['pitchers']['away']} vs {p['pitchers']['home']}",
                    ],
                )
        else:
            texto += "No hubo totales con EV positivo suficiente.\n\n"

        texto += "🧠 <b>MEJORES PICKS DEL MODELO</b>\n━━━━━━━━━━━━━━━━━━\n\n"
        if picks_modelo:
            for p in picks_modelo[:5]:
                texto += card_game(
                    f"{p['away']} @ {p['home']}",
                    [
                        f"🎯 Pick modelo: <b>{p['ml_pick']}</b>",
                        f"🧠 Confianza: <b>{p['confidence_pct']}%</b>",
                        f"🎽 Pitchers: {p['pitchers']['away']} vs {p['pitchers']['home']}",
                        f"📉 ERA: {p['pitcher_stats']['away']['era']} vs {p['pitcher_stats']['home']['era']}",
                        f"📊 Total proyectado: <b>{p['total_projection']}</b>",
                        f"🌡️ Clima: {p['clima'].get('temp_c')}°C | 💨 {p['clima'].get('wind_kmh')} km/h",
                    ],
                )
        else:
            texto += "No se pudieron generar picks del modelo."

        bot.delete_message(msg.chat.id, msg.message_id)
        responder_largo(message.chat.id, texto, parse_mode="HTML")

    except Exception as e:
        bot.edit_message_text(
            f"❌ Error en /apuestas: {str(e)[:200]}", msg.chat.id, msg.message_id
        )


# PARLEY FUNCTION FIX (PRO_FALLBACK_V3)


@bot.message_handler(commands=["parley", "parley_del_dia"])
def parley(message):
    msg = bot.reply_to(message, "🎯 Construyendo parley del día...")
    try:
        parley_existente = buscar_parley_del_dia("parley")
        if parley_existente:
            texto = header("PARLEY DEL DÍA (FIJO)", "🎯")
            texto += f"📅 {parley_existente['fecha']}\n\n"
            for leg in parley_existente.get("legs", []):
                texto += card_game(
                    leg["game"],
                    [
                        f"🎯 Pick: <b>{leg['pick']}</b>",
                        f"🧠 Confianza: <b>{leg.get('confidence', 'N/D')}%</b>",
                    ],
                )
            bot.edit_message_text(texto, msg.chat.id, msg.message_id, parse_mode="HTML")
            return

        analisis_juegos = obtener_analisis_del_dia()

        candidatos_estrictos = []
        candidatos_fallback = []
        for a in analisis_juegos:
            motivo = None
            if a["risk_flags"]["tbd_pitcher"]:
                motivo = "pitcher_tbd"
            elif a["has_valid_ml_odds"]:
                if (
                    a["confidence_pct"] >= 55.5
                    and a["ml_edge_pct"] >= 1.6
                    and a["ev_ml_pct"] >= 1.1
                    and not (a["ml_odds"] <= -275 or a["ml_odds"] >= 190)
                ):
                    candidatos_estrictos.append(a)
                else:
                    motivo = "no_cumple_estricto_odds"
            else:
                if (
                    a["confidence_pct"] >= 53.0
                    and a["score_ml"] >= 20.5
                    and not a["risk_flags"]["clima_extremo"]
                ):
                    candidatos_fallback.append(a)
                else:
                    motivo = "no_cumple_fallback_modelo"

            if motivo:
                print("[PARLEY_DESCARTE]", a["game"], motivo)

        candidatos_estrictos = filtrar_matchups_unicos(candidatos_estrictos)
        candidatos_fallback = filtrar_matchups_unicos(candidatos_fallback)
        candidatos_estrictos.sort(
            key=lambda x: (x["score_ml"], x["ev_ml_pct"]), reverse=True
        )
        candidatos_fallback.sort(
            key=lambda x: (x["score_ml"], x["confidence_pct"]), reverse=True
        )

        seleccionados = candidatos_estrictos[:3]
        uso_fallback = False
        if len(seleccionados) < 2:
            for c in candidatos_fallback:
                if len(seleccionados) >= 3:
                    break
                if any(s["matchup_key"] == c["matchup_key"] for s in seleccionados):
                    continue
                seleccionados.append(c)
                uso_fallback = True

        texto = header("PARLEY DEL DÍA MLB", "🎯")
        texto += f"📅 {hoy_str()}\n\n"

        if not seleccionados:
            texto += "No hay picks conservadores de calidad hoy, incluso tras fallback moderado."
        else:
            if uso_fallback:
                texto += "⚠️ Se usó fallback moderado del modelo por falta de cuotas suficientes.\n\n"
            for p in seleccionados:
                texto += card_game(
                    f"{p['away']} @ {p['home']}",
                    [
                        f"🎯 Pick: <b>{p['ml_pick']}</b>",
                        f"🧠 Confianza: <b>{p['confidence_pct']}%</b>",
                        f"📈 Edge: <b>{p['ml_edge_pct']}%</b> | EV: <b>{p['ev_ml_pct']}%</b>",
                        f"💵 Cuota: <b>{p['ml_odds'] if p['ml_odds'] is not None else 'N/D'}</b>",
                    ],
                )

            legs = [
                {
                    "game": f"{p['away']} @ {p['home']}",
                    "pick": p["ml_pick"],
                    "confidence": p["confidence_pct"],
                }
                for p in seleccionados
            ]
            registrar_parley_del_dia("parley", legs)

        bot.edit_message_text(texto, msg.chat.id, msg.message_id, parse_mode="HTML")

    except Exception as e:
        bot.edit_message_text(
            f"❌ Error en /parley: {str(e)[:120]}", msg.chat.id, msg.message_id
        )


@bot.message_handler(commands=["reset_millonario"])
def reset_millonario(message):
    try:
        borrado = eliminar_parley_del_dia("parley_millonario")

        if borrado:
            bot.reply_to(
                message, "♻️ Parley millonario del día reiniciado correctamente."
            )
        else:
            bot.reply_to(message, "No había parley millonario guardado hoy.")
    except Exception as e:
        bot.reply_to(message, f"❌ Error al resetear millonario: {str(e)[:120]}")


@bot.message_handler(commands=["parley_millonario"])
def parley_millonario(message):
    msg = bot.reply_to(message, "💎 Construyendo parley millonario del día...")
    try:
        parley_existente = buscar_parley_del_dia("parley_millonario")
        if parley_existente:
            texto = header("PARLEY MILLONARIO (FIJO)", "💎")
            texto += f"📅 {parley_existente['fecha']}\n\n"
            for leg in parley_existente.get("legs", []):
                texto += card_game(
                    leg["game"],
                    [
                        f"🔥 Pick: <b>{leg['pick']}</b>",
                        f"🧠 Confianza: <b>{leg.get('confidence', 'N/D')}%</b>",
                    ],
                )
            bot.edit_message_text(texto, msg.chat.id, msg.message_id, parse_mode="HTML")
            return

        # =========================================
        # BLOQUEAR TODOS LOS JUEGOS DEL PARLEY DIARIO
        # =========================================
        parley_diario = buscar_parley_del_dia("parley")
        matchups_bloqueados = set()

        if parley_diario:
            for leg in parley_diario.get("legs", []):
                game = leg.get("game", "")
                if " @ " in game:
                    away, home = game.split(" @ ", 1)
                    matchups_bloqueados.add(normalizar_matchup(away, home))

        analisis_juegos = obtener_analisis_del_dia()

        def construir_candidatos(
            ml_conf, ml_ev, ml_edge, total_edge, total_ev, aceptar_nd=False
        ):
            candidatos = []
            for a in analisis_juegos:
                motivo = None
                if a["matchup_key"] in matchups_bloqueados:
                    motivo = "bloqueado_por_parley"
                    continue
                if a["risk_flags"]["tbd_pitcher"]:
                    motivo = "pitcher_tbd"
                    continue

                if (
                    a["ml_odds"] is not None
                    and a["confidence_pct"] >= ml_conf
                    and a["ev_ml_pct"] >= ml_ev
                    and a["ml_edge_pct"] >= ml_edge
                ):
                    candidatos.append(
                        {
                            "tipo": "ML",
                            "game": f"{a['away']} @ {a['home']}",
                            "matchup_key": a["matchup_key"],
                            "pick": a["ml_pick"],
                            "confidence": a["confidence_pct"],
                            "edge": a["ml_edge_pct"],
                            "score": a["score_agresivo"],
                            "cuota": a["ml_odds"],
                            "is_home_pick": a["is_home_pick"],
                        }
                    )
                elif (
                    a["ml_odds"] is None
                    and a["confidence_pct"] >= max(ml_conf - 1.0, 52.0)
                    and a["score_agresivo"] >= 17.8
                    and aceptar_nd
                ):
                    candidatos.append(
                        {
                            "tipo": "ML",
                            "game": f"{a['away']} @ {a['home']}",
                            "matchup_key": a["matchup_key"],
                            "pick": a["ml_pick"],
                            "confidence": a["confidence_pct"],
                            "edge": a["ml_edge_pct"],
                            "score": a["score_agresivo"] - 0.8,
                            "cuota": "N/D",
                            "is_home_pick": a["is_home_pick"],
                        }
                    )
                else:
                    motivo = "ml_no_califica"

                if (
                    a.get("total_pick")
                    and a["total_edge"] >= total_edge
                    and (
                        a["ev_total_pct"] >= total_ev
                        or (
                            a["total_odds"] is None
                            and a["score_total"] >= 15.5
                            and aceptar_nd
                        )
                    )
                ):
                    cuota_total = (
                        a["total_odds"] if a["total_odds"] is not None else "N/D"
                    )
                    if cuota_total != "N/D" or aceptar_nd:
                        candidatos.append(
                            {
                                "tipo": "TOTAL",
                                "game": f"{a['away']} @ {a['home']}",
                                "matchup_key": a["matchup_key"],
                                "pick": a["total_pick"]["pick"],
                                "confidence": a["confidence_pct"],
                                "edge": a["total_edge"],
                                "score": a["score_agresivo"] + 1.3,
                                "cuota": cuota_total,
                                "is_home_pick": a["is_home_pick"],
                            }
                        )
                elif motivo is None:
                    motivo = "total_no_califica"
                if motivo and not a["matchup_key"] in matchups_bloqueados:
                    print("[MILL_DESCARTE]", a["game"], motivo)
            candidatos = filtrar_matchups_unicos(candidatos)
            candidatos.sort(key=lambda x: x["score"], reverse=True)
            return candidatos

        candidatos_estrictos = construir_candidatos(
            ml_conf=54.0,
            ml_ev=2.2,
            ml_edge=1.8,
            total_edge=0.75,
            total_ev=1.5,
            aceptar_nd=False,
        )
        candidatos_flex = construir_candidatos(
            ml_conf=52.5,
            ml_ev=1.0,
            ml_edge=0.9,
            total_edge=0.55,
            total_ev=0.8,
            aceptar_nd=True,
        )
        candidatos_emergencia = construir_candidatos(
            ml_conf=51.5,
            ml_ev=0.5,
            ml_edge=0.4,
            total_edge=0.45,
            total_ev=0.3,
            aceptar_nd=True,
        )

        seleccionados = []
        uso_fallback_modelo = False
        home_ml_count = 0
        for idx, pool in enumerate(
            [candidatos_estrictos, candidatos_flex, candidatos_emergencia]
        ):
            for c in pool:
                if len(seleccionados) >= 5:
                    break
                if any(s["matchup_key"] == c["matchup_key"] for s in seleccionados):
                    continue
                if (
                    c["tipo"] == "ML"
                    and c["is_home_pick"]
                    and c["confidence"] < 55
                    and home_ml_count >= 1
                ):
                    continue
                if c["cuota"] == "N/D" and len(seleccionados) >= 3:
                    continue
                if c["tipo"] == "ML" and c["is_home_pick"]:
                    home_ml_count += 1
                seleccionados.append(c)
                if idx > 0 or c["cuota"] == "N/D":
                    uso_fallback_modelo = True
            if len(seleccionados) >= 5:
                break

        texto = header("PARLEY MILLONARIO (ALTO RIESGO CALCULADO)", "💎")
        texto += f"📅 {hoy_str()}\n\n"

        if not seleccionados:
            texto += (
                "No hay picks agresivos de calidad hoy, incluso tras fallback flexible."
            )
        else:
            if uso_fallback_modelo:
                texto += "⚠️ Se usó fallback agresivo del modelo por falta de cuotas suficientes.\n\n"
            for p in seleccionados:
                texto += card_game(
                    p["game"],
                    [
                        f"🔥 {p['tipo']}: <b>{p['pick']}</b>",
                        f"🧠 Confianza: <b>{p['confidence']}%</b>",
                        f"📈 Edge: <b>{p['edge']}</b>",
                        f"💵 Cuota: <b>{p['cuota']}</b>",
                    ],
                )

            legs = [
                {"game": p["game"], "pick": p["pick"], "confidence": p["confidence"]}
                for p in seleccionados
            ]
            registrar_parley_del_dia("parley_millonario", legs)

        bot.edit_message_text(texto, msg.chat.id, msg.message_id, parse_mode="HTML")

    except Exception as e:
        bot.edit_message_text(
            f"❌ Error en /parley_millonario: {str(e)[:120]}",
            msg.chat.id,
            msg.message_id,
        )


@bot.message_handler(commands=["pronosticos"])
def pronosticos(message):
    msg = bot.reply_to(message, "📊 Generando pronósticos del modelo...")
    try:
        standings = obtener_standings()
        games = obtener_juegos_del_dia()

        texto = header("PRONÓSTICOS DEL MODELO", "📊")
        texto += f"📅 {hoy_str()}\n\n"

        if not games:
            texto += "No hay juegos hoy."
            bot.edit_message_text(texto, msg.chat.id, msg.message_id)
            return

        picks = []

        for g in games:
            teams = g.get("teams", {})
            away_data = teams.get("away", {})
            home_data = teams.get("home", {})

            away = away_data.get("team", {}).get("name")
            home = home_data.get("team", {}).get("name")
            if not away or not home:
                continue

            away_pitcher_obj = away_data.get("probablePitcher", {}) or {}
            home_pitcher_obj = home_data.get("probablePitcher", {}) or {}
            away_p = away_pitcher_obj.get("fullName", "TBD")
            home_p = home_pitcher_obj.get("fullName", "TBD")
            away_pid = away_pitcher_obj.get("id")
            home_pid = home_pitcher_obj.get("id")

            away_stats = obtener_stats_pitcher_reales(away_pid)
            home_stats = obtener_stats_pitcher_reales(home_pid)
            weather = obtener_clima_partido(g) or {
                "temp_c": None,
                "wind_kmh": None,
                "precip_mm": None,
            }

            pred = obtener_pick_juego_pro(
                away, home, standings, away_p, home_p, away_stats, home_stats, weather
            )

            picks.append(
                {
                    "game": f"{away} @ {home}",
                    "pick": f"{pred['favorite']} ML",
                    "conf": pred["confidence_pct"],
                }
            )

        picks.sort(key=lambda x: x["conf"], reverse=True)

        for p in picks[:8]:
            texto += card_game(
                p["game"],
                [f"🎯 Pick: <b>{p['pick']}</b>", f"🧠 Confianza: <b>{p['conf']}%</b>"],
            )

        bot.edit_message_text(texto, msg.chat.id, msg.message_id, parse_mode="HTML")
    except Exception as e:
        bot.edit_message_text(f"❌ Error: {str(e)[:120]}", msg.chat.id, msg.message_id)


@bot.message_handler(commands=["exportar_json"])
def exportar_json(message):
    msg = bot.reply_to(message, "📦 Generando archivo JSON maestro para TikTok...")
    try:
        data = generar_dataset_tiktok()
        ruta = guardar_json_tiktok(data)

        if not ruta:
            bot.edit_message_text(
                "❌ No se pudo guardar el archivo JSON.", msg.chat.id, msg.message_id
            )
            return

        total_juegos = len(data.get("juegos_del_dia", []))
        total_pronosticos = len(data.get("pronosticos", []))
        total_ml = len(data.get("apuestas", {}).get("moneyline_ev", []))
        total_totales = len(data.get("apuestas", {}).get("totales_ev", []))
        total_parley = len(data.get("parley", []))
        total_millonario = len(data.get("parley_millonario", []))

        texto = (
            "✅ <b>JSON maestro generado correctamente</b>\n\n"
            f"📅 Fecha: <b>{data['fecha']}</b>\n"
            f"🤖 Versión: <b>{data['bot_version']}</b>\n"
            f"⚾ Juegos del día: <b>{total_juegos}</b>\n"
            f"📊 Pronósticos: <b>{total_pronosticos}</b>\n"
            f"💰 ML EV+: <b>{total_ml}</b>\n"
            f"📈 Totales EV+: <b>{total_totales}</b>\n"
            f"🎯 Parley: <b>{total_parley}</b>\n"
            f"💎 Parley millonario: <b>{total_millonario}</b>\n\n"
            f"📂 Guardado en:\n<code>{ruta}</code>"
        )

        bot.edit_message_text(texto, msg.chat.id, msg.message_id, parse_mode="HTML")

    except Exception as e:
        import traceback

        print(traceback.format_exc())
        bot.edit_message_text(
            f"❌ Error en /exportar_json: {str(e)[:180]}", msg.chat.id, msg.message_id
        )


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

            if any(
                x in lower
                for x in [
                    "injured",
                    "il",
                    "injury",
                    "60-day",
                    "15-day",
                    "10-day",
                    "placed on",
                ]
            ):
                texto += f"• {desc}\n"
                count += 1
                if count >= 15:
                    break

        if count == 0:
            texto += "No encontré movimientos importantes de IL recientemente."

        bot.delete_message(msg.chat.id, msg.message_id)
        responder_largo(message.chat.id, texto, parse_mode="HTML")
    except Exception as e:
        bot.edit_message_text(
            f"❌ Error al cargar lesionados: {str(e)[:120]}",
            msg.chat.id,
            msg.message_id,
        )


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


@bot.message_handler(commands=["pitchers"])
def pitchers(message):
    msg = bot.reply_to(message, "🧢 Cargando pitchers probables...")
    try:
        games = obtener_juegos_del_dia()
        texto = header("PITCHERS PROBABLES", "🧢")
        texto += f"📅 {hoy_str()}\n\n"

        if not games:
            texto += "No hay juegos programados hoy."
            bot.edit_message_text(texto, msg.chat.id, msg.message_id, parse_mode="HTML")
            return

        for g in games:
            teams = g.get("teams", {})
            away_data = teams.get("away", {})
            home_data = teams.get("home", {})

            away = away_data.get("team", {}).get("name", "TBD")
            home = home_data.get("team", {}).get("name", "TBD")
            away_pitcher_obj = away_data.get("probablePitcher", {}) or {}
            home_pitcher_obj = home_data.get("probablePitcher", {}) or {}

            away_p = away_pitcher_obj.get("fullName", "TBD")
            home_p = home_pitcher_obj.get("fullName", "TBD")
            away_stats = obtener_stats_pitcher_reales(away_pitcher_obj.get("id"))
            home_stats = obtener_stats_pitcher_reales(home_pitcher_obj.get("id"))

            texto += card_game(
                f"{away} @ {home}",
                [
                    f"🛣️ {abreviar_equipo(away)}: <b>{away_p}</b> | ERA {away_stats['era']} | WHIP {away_stats['whip']}",
                    f"🏠 {abreviar_equipo(home)}: <b>{home_p}</b> | ERA {home_stats['era']} | WHIP {home_stats['whip']}",
                ],
            )

        bot.delete_message(msg.chat.id, msg.message_id)
        responder_largo(message.chat.id, texto, parse_mode="HTML")
    except Exception as e:
        bot.edit_message_text(
            f"❌ Error en /pitchers: {str(e)[:120]}", msg.chat.id, msg.message_id
        )


@bot.message_handler(commands=["reset_parley"])
def reset_parley(message):
    try:
        borrado = eliminar_parley_del_dia("parley")
        if borrado:
            bot.reply_to(message, "♻️ Parley del día reiniciado correctamente.")
        else:
            bot.reply_to(message, "No había parley del día guardado hoy.")
    except Exception as e:
        bot.reply_to(message, f"❌ Error al resetear parley: {str(e)[:120]}")


@bot.message_handler(commands=["historial"])
def historial(message):
    parleys = cargar_parleys_diarios()
    texto = header("HISTORIAL DE PARLEYS", "📚")

    if not parleys:
        texto += "No hay historial registrado todavía."
        bot.reply_to(message, texto, parse_mode="HTML")
        return

    recientes = sorted(parleys, key=lambda x: x.get("fecha", ""), reverse=True)[:12]
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

    for item in recientes:
        fecha = item.get("fecha", "N/D")
        tipo = item.get("tipo", "N/D")
        estado = item.get("estado", "pendiente")
        legs = item.get("legs", [])
        texto += f"📅 <b>{fecha}</b> | {tipo} | Estado: <b>{estado}</b>\n"
        for leg in legs:
            texto += f"• {leg.get('game', 'N/D')} → {leg.get('pick', 'N/D')}\n"
        texto += "\n"

    responder_largo(message.chat.id, texto, parse_mode="HTML")


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
            bot.reply_to(
                message, "Todavía no hay apuestas cerradas en el CSV para calcular ROI."
            )
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


@bot.message_handler(commands=["parley_ganado"])
def parley_ganado(message):
    ok = actualizar_estado_parley(hoy_str(), "parley", "ganado")
    bot.reply_to(
        message,
        (
            "✅ Parley del día marcado como GANADO."
            if ok
            else "❌ No encontré parley del día para marcar."
        ),
    )


@bot.message_handler(commands=["parley_fallado"])
def parley_fallado(message):
    ok = actualizar_estado_parley(hoy_str(), "parley", "fallado")
    bot.reply_to(
        message,
        (
            "❌ Parley del día marcado como FALLADO."
            if ok
            else "❌ No encontré parley del día para marcar."
        ),
    )


@bot.message_handler(commands=["millonario_ganado"])
def millonario_ganado(message):
    ok = actualizar_estado_parley(hoy_str(), "parley_millonario", "ganado")
    bot.reply_to(
        message,
        (
            "✅ Parley millonario marcado como GANADO."
            if ok
            else "❌ No encontré parley millonario del día."
        ),
    )


@bot.message_handler(commands=["millonario_fallado"])
def millonario_fallado(message):
    ok = actualizar_estado_parley(hoy_str(), "parley_millonario", "fallado")
    bot.reply_to(
        message,
        (
            "❌ Parley millonario marcado como FALLADO."
            if ok
            else "❌ No encontré parley millonario del día."
        ),
    )


@bot.message_handler(commands=["stats_parleys"])
def stats_parleys(message):
    parleys = cargar_parleys_diarios()
    stats = {
        "parley": {"ganado": 0, "fallado": 0},
        "parley_millonario": {"ganado": 0, "fallado": 0},
    }

    for p in parleys:
        tipo = p.get("tipo")
        estado = p.get("estado")
        if tipo in stats and estado in ["ganado", "fallado"]:
            stats[tipo][estado] += 1

    total_parley = stats["parley"]["ganado"] + stats["parley"]["fallado"]
    total_millonario = (
        stats["parley_millonario"]["ganado"] + stats["parley_millonario"]["fallado"]
    )
    efectividad_parley = (
        round((stats["parley"]["ganado"] / total_parley) * 100, 2)
        if total_parley > 0
        else 0
    )
    efectividad_millonario = (
        round((stats["parley_millonario"]["ganado"] / total_millonario) * 100, 2)
        if total_millonario > 0
        else 0
    )

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


import time

print(f"INICIANDO BOT {BOT_VERSION}")
bot.remove_webhook()
time.sleep(1)
bot.infinity_polling(
    skip_pending=True,
    timeout=30,
    long_polling_timeout=30,
    allowed_updates=["message", "callback_query"],
)

