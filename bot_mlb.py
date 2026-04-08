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
BOT_VERSION = "V5_EDGE_MIXED"

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
            writer.writerow([
                "fecha", "juego", "tipo_apuesta", "pick", "cuota",
                "prob_modelo", "prob_implicita", "edge", "stake",
                "grade", "resultado", "profit"
            ])
    except Exception as e:
        print(f"Error inicializando CSV: {e}")
        
def registrar_apuesta_csv(fecha, juego, tipo_apuesta, pick, cuota, prob_modelo,
                          prob_implicita, edge, stake, grade):
    try:
        with open(RESULTADOS_CSV, "a", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow([
                fecha, juego, tipo_apuesta, pick, cuota,
                prob_modelo, prob_implicita, edge, stake,
                grade, "pendiente", 0
            ])
        return True
    except Exception as e:
        print(f"Error registrando apuesta en CSV: {e}")
        return False


def cerrar_apuesta_csv(fecha, juego, pick, resultado):
    """
    resultado: win / lose
    """
    try:
        filas = []
        encontrada = False
        headers = None

        with open(RESULTADOS_CSV, "r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            headers = reader.fieldnames
            for row in reader:
                if (
                    row["fecha"] == fecha and
                    row["juego"] == juego and
                    row["pick"] == pick and
                    str(row["resultado"]).strip().lower() == "pendiente"
                ):
                    stake = extraer_unidades(row["stake"])
                    cuota = row["cuota"]

                    profit = 0.0
                    dec = american_to_decimal(cuota)

                    if resultado == "win":
                        if dec is not None:
                            profit = round((dec - 1) * stake, 2)
                        else:
                            profit = round(stake, 2)
                    elif resultado == "lose":
                        profit = round(-stake, 2)

                    row["resultado"] = resultado
                    row["profit"] = profit
                    encontrada = True

                filas.append(row)

        if not encontrada or not headers:
            return False

        with open(RESULTADOS_CSV, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=headers)
            writer.writeheader()
            writer.writerows(filas)

        return True

    except Exception as e:
        print(f"Error cerrando apuesta en CSV: {e}")
        return False


def registrar_apuesta_csv(fecha, juego, tipo_apuesta, pick, cuota, prob_modelo,
                          prob_implicita, edge, stake, grade):
    try:
        with open(RESULTADOS_CSV, "a", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow([
                fecha, juego, tipo_apuesta, pick, cuota,
                prob_modelo, prob_implicita, edge, stake,
                grade, "pendiente", 0
            ])
        return True
    except Exception as e:
        print(f"Error registrando apuesta en CSV: {e}")
        return False


def cerrar_apuesta_csv(fecha, juego, pick, resultado):
    """
    resultado: win / lose
    """
    try:
        filas = []
        encontrada = False

        with open(RESULTADOS_CSV, "r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            headers = reader.fieldnames
            for row in reader:
                if (
                    row["fecha"] == fecha and
                    row["juego"] == juego and
                    row["pick"] == pick and
                    str(row["resultado"]).strip().lower() == "pendiente"
                ):
                    stake = extraer_unidades(row["stake"])
                    cuota = row["cuota"]

                    profit = 0.0
                    dec = american_to_decimal(cuota)

                    if resultado == "win":
                        if dec is not None:
                            profit = round((dec - 1) * stake, 2)
                        else:
                            profit = stake
                    elif resultado == "lose":
                        profit = round(-stake, 2)

                    row["resultado"] = resultado
                    row["profit"] = profit
                    encontrada = True

                filas.append(row)

        if not encontrada:
            return False

        with open(RESULTADOS_CSV, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=headers)
            writer.writeheader()
            writer.writerows(filas)

        return True

    except Exception as e:
        print(f"Error cerrando apuesta en CSV: {e}")
        return False

historial_parlays = cargar_historial()
inicializar_csv_resultados()

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
        InlineKeyboardButton("📝 Registrar", callback_data="cmd_registrar_apuesta"),
        InlineKeyboardButton("✅ Cerrar", callback_data="cmd_cerrar_apuesta"),
        InlineKeyboardButton("📦 Exportar JSON", callback_data="cmd_exportar_json")
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
            bot.send_message(chat_id, parte, parse_mode=parse_mode, reply_markup=reply_markup)
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
        "Colorado Rockies": "Rockies"
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
        "standingsTypes": "regularSeason"
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
    params = {
        "sportId": 1,
        "date": hoy_str(),
        "hydrate": "probablePitcher,venue"
    }
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
        "sportId": 1
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
    params = {
        "stats": "season",
        "group": "pitching",
        "season": season,
        "gameType": "R"
    }
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
        "sample_ok": ip >= 10
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
            "forecast_days": 2
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
                diff = abs((dt_local.replace(tzinfo=None) - dt_utc.replace(tzinfo=None)).total_seconds())
                if min_diff is None or diff < min_diff:
                    min_diff = diff
                    best_idx = i
            except Exception:
                continue

        return {
            "temp_c": temps[best_idx] if best_idx < len(temps) else None,
            "wind_kmh": winds[best_idx] if best_idx < len(winds) else None,
            "precip_mm": precs[best_idx] if best_idx < len(precs) else None
        }

    except Exception as e:
        print(f"Error obteniendo clima: {e}")
        return {"temp_c": None, "wind_kmh": None, "precip_mm": None}

# =========================================================
# MODELO
# =========================================================

def score_pitcher_real(stats):
    era = float(stats.get("era", 4.20) or 4.20)
    whip = float(stats.get("whip", 1.30) or 1.30)
    so9 = float(stats.get("so9", 8.2) or 8.2)
    ip = float(stats.get("ip", 0.0) or 0.0)
    sample_ok = stats.get("sample_ok", False)

    score = 0.0

    # ERA
    if era <= 2.50:
        score += 0.60
    elif era <= 3.20:
        score += 0.40
    elif era <= 3.80:
        score += 0.20
    elif era >= 5.20:
        score -= 0.42
    elif era >= 4.60:
        score -= 0.25

    # WHIP
    if whip <= 1.00:
        score += 0.42
    elif whip <= 1.12:
        score += 0.28
    elif whip <= 1.24:
        score += 0.14
    elif whip >= 1.45:
        score -= 0.28
    elif whip >= 1.35:
        score -= 0.14

    # K/9
    if so9 >= 11.0:
        score += 0.24
    elif so9 >= 9.5:
        score += 0.16
    elif so9 >= 8.5:
        score += 0.08
    elif so9 <= 6.0:
        score -= 0.14

    # Sample-size penalty
    if not sample_ok or ip < 8:
        score *= 0.65
    elif ip < 15:
        score *= 0.82

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
    away_team, home_team, standings,
    away_pitcher="TBD", home_pitcher="TBD",
    away_pitcher_stats=None, home_pitcher_stats=None, weather=None
):
    away = standings.get(away_team)
    home = standings.get(home_team)

    if not away or not home:
        return 0.50

    diff_win_pct = home["win_pct"] - away["win_pct"]
    diff_split = home["home_win_pct"] - away["away_win_pct"]
    diff_last10 = home["last10_win_pct"] - away["last10_win_pct"]
    diff_run_diff = (home["run_diff"] - away["run_diff"]) / 45.0
    diff_streak = parse_streak(home["streak"]) - parse_streak(away["streak"])
    diff_runs_scored = (home.get("runs_scored", 4.5) - away.get("runs_scored", 4.5)) / 2.4
    diff_runs_allowed = (away.get("runs_allowed", 4.5) - home.get("runs_allowed", 4.5)) / 2.4

    if away_pitcher_stats is None:
        away_pitcher_stats = {"era": 4.20, "whip": 1.30, "so9": 8.2, "ip": 0.0, "sample_ok": False}
    if home_pitcher_stats is None:
        home_pitcher_stats = {"era": 4.20, "whip": 1.30, "so9": 8.2, "ip": 0.0, "sample_ok": False}

    p_home = score_pitcher_real(home_pitcher_stats)
    p_away = score_pitcher_real(away_pitcher_stats)
    diff_pitcher = p_home - p_away

    score = 0.02
    score += diff_win_pct * 4.8
    score += diff_split * 3.6
    score += diff_last10 * 2.0
    score += diff_run_diff * 2.6
    score += diff_streak * 1.3
    score += diff_runs_scored * 1.5
    score += diff_runs_allowed * 1.5
    score += diff_pitcher * 3.8
    score += ajuste_clima_ml(weather)

    if away_pitcher == "TBD":
        score += 0.10
    if home_pitcher == "TBD":
        score -= 0.10

    # force a little separation if score is too close to zero
    if -0.08 < score < 0.08:
        score = 0.11 if score >= 0 else -0.11

    prob = logistic(score)
    return clamp(prob, 0.34, 0.66)


def obtener_pick_juego_pro(
    away_team, home_team, standings,
    away_pitcher="TBD", home_pitcher="TBD",
    away_pitcher_stats=None, home_pitcher_stats=None, weather=None
):
    prob_home = calcular_probabilidad_local_pro(
        away_team, home_team, standings,
        away_pitcher, home_pitcher,
        away_pitcher_stats, home_pitcher_stats, weather
    )

    prob_away = 1 - prob_home
    avoid = away_pitcher == "TBD" or home_pitcher == "TBD"

    if away_pitcher_stats is None:
        away_pitcher_stats = {"era": 4.20, "whip": 1.30, "so9": 8.2, "ip": 0.0, "sample_ok": False}
    if home_pitcher_stats is None:
        home_pitcher_stats = {"era": 4.20, "whip": 1.30, "so9": 8.2, "ip": 0.0, "sample_ok": False}

    away_pitch_score = score_pitcher_real(away_pitcher_stats)
    home_pitch_score = score_pitcher_real(home_pitcher_stats)

    # desempate real cuando está muy cerca del 50/50
    if abs(prob_home - 0.5) < 0.02:
        if away_pitch_score > home_pitch_score:
            favorito = away_team
            prob_fav = prob_away
        elif home_pitch_score > away_pitch_score:
            favorito = home_team
            prob_fav = prob_home
        else:
            # si siguen iguales, decidir por mejor récord general
            away_data = standings.get(away_team, {})
            home_data = standings.get(home_team, {})
            away_wp = away_data.get("win_pct", 0.5)
            home_wp = home_data.get("win_pct", 0.5)

            if away_wp > home_wp:
                favorito = away_team
                prob_fav = prob_away
            else:
                favorito = home_team
                prob_fav = prob_home
    else:
        if prob_home > prob_away:
            favorito = home_team
            prob_fav = prob_home
        else:
            favorito = away_team
            prob_fav = prob_away

    # empujar fuera del 50 exacto
    if 0.495 <= prob_fav <= 0.505:
        prob_fav = 0.518

    return {
        "favorite": favorito,
        "prob_home": prob_home,
        "prob_favorite": prob_fav,
        "confidence_pct": round(prob_fav * 100, 1),
        "confidence_label": confidence_label(prob_fav),
        "avoid": avoid
    } 

def estimar_total_juego_pro(
    away_team, home_team, standings,
    away_pitcher="TBD", home_pitcher="TBD",
    away_pitcher_stats=None, home_pitcher_stats=None, weather=None
):
    away = standings.get(away_team, {})
    home = standings.get(home_team, {})

    total = 8.6

    away_rs = away.get("runs_scored", 4.5)
    home_rs = home.get("runs_scored", 4.5)
    away_ra = away.get("runs_allowed", 4.5)
    home_ra = home.get("runs_allowed", 4.5)

    total += ((away_rs + home_rs) - 9.0) * 0.22
    total += ((away_ra + home_ra) - 9.0) * 0.18
    total += ((away.get("run_diff", 0) + home.get("run_diff", 0)) / 162.0) * 0.20

    if away_pitcher_stats is None:
        away_pitcher_stats = {"era": 4.20, "whip": 1.30, "so9": 8.2, "ip": 0.0, "sample_ok": False}
    if home_pitcher_stats is None:
        home_pitcher_stats = {"era": 4.20, "whip": 1.30, "so9": 8.2, "ip": 0.0, "sample_ok": False}

    total += (away_pitcher_stats.get("era", 4.20) - 4.00) * 0.30
    total += (home_pitcher_stats.get("era", 4.20) - 4.00) * 0.30
    total += (away_pitcher_stats.get("whip", 1.30) - 1.25) * 0.75
    total += (home_pitcher_stats.get("whip", 1.30) - 1.25) * 0.75
    total -= (away_pitcher_stats.get("so9", 8.2) - 8.5) * 0.08
    total -= (home_pitcher_stats.get("so9", 8.2) - 8.5) * 0.08

    if away_pitcher == "TBD":
        total += 0.45
    if home_pitcher == "TBD":
        total += 0.45

    last10_away = away.get("last10_win_pct", 0.5)
    last10_home = home.get("last10_win_pct", 0.5)
    total += ((last10_away + last10_home) - 1.0) * 0.30

    total += ajuste_clima_total(weather)

    return round(clamp(total, 6.5, 12.5), 1)

def elegir_total_pick(total_proyectado, total_line):
    if total_line is None:
        return None

    try:
        total_line = float(total_line)
    except Exception:
        return None

    diff = total_proyectado - total_line

    # Más permisivo para que sí entren Overs/Unders
    if diff >= 0.30:
        return {
            "pick": f"Over {total_line}",
            "edge": round(diff, 2),
            "strength": "Alta" if diff >= 0.55 else "Media"
        }

    if diff <= -0.30:
        return {
            "pick": f"Under {total_line}",
            "edge": round(abs(diff), 2),
            "strength": "Alta" if diff <= -0.55 else "Media"
        }

    # zona gris leve
    if diff >= 0.12:
        return {
            "pick": f"Over {total_line}",
            "edge": round(diff, 2),
            "strength": "Suave"
        }

    if diff <= -0.12:
        return {
            "pick": f"Under {total_line}",
            "edge": round(abs(diff), 2),
            "strength": "Suave"
        }

    return None

def elegir_total_pick_fallback(total_proyectado):
    """
    Genera un total aunque no haya línea de la casa.
    Esto sirve para que el parley millonario no quede solo en ML.
    """
    if total_proyectado >= 9.4:
        return {"pick": "Over 8.5", "edge": round(total_proyectado - 8.5, 2), "strength": "Alta"}
    elif total_proyectado >= 8.9:
        return {"pick": "Over 8.0", "edge": round(total_proyectado - 8.0, 2), "strength": "Media"}
    elif total_proyectado <= 7.4:
        return {"pick": "Under 8.5", "edge": round(8.5 - total_proyectado, 2), "strength": "Alta"}
    elif total_proyectado <= 7.9:
        return {"pick": "Under 8.0", "edge": round(8.0 - total_proyectado, 2), "strength": "Media"}
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

def obtener_odds_completas(away_team, home_team):
    if not ODDS_API_KEY:
        return None

    try:
        url = "https://api.the-odds-api.com/v4/sports/baseball_mlb/odds"
        params = {
            "apiKey": ODDS_API_KEY,
            "regions": "us",
            "markets": "h2h,totals",
            "oddsFormat": "american"
        }

        data = safe_get(url, params=params)

        away_norm = normalizar_nombre_equipo_odds(away_team)
        home_norm = normalizar_nombre_equipo_odds(home_team)

        if not isinstance(data, list):
            return None
        for event in data:
            home_name = event.get("home_team", "")
            teams = event.get("teams", [])
            away_name = [t for t in teams if t != home_name]
            away_name = away_name[0] if away_name else ""

            if home_name == home_norm and away_name == away_norm:
                bookmakers = event.get("bookmakers", [])
                for book in bookmakers:
                    resultado = {
                        "bookmaker": book.get("title", "Bookmaker"),
                        "home_moneyline": None,
                        "away_moneyline": None,
                        "total_line": None,
                        "over_price": None,
                        "under_price": None
                    }

                    for market in book.get("markets", []):
                        key = market.get("key")

                        if key == "h2h":
                            for o in market.get("outcomes", []):
                                if o.get("name") == home_name:
                                    resultado["home_moneyline"] = o.get("price")
                                elif o.get("name") == away_name:
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



def generar_dataset_tiktok():
    standings = obtener_standings()
    games = obtener_juegos_del_dia()

    data = {
        "fecha": hoy_str(),
        "bot_version": BOT_VERSION,
        "juegos_del_dia": [],
        "pronosticos": [],
        "apuestas": {
            "moneyline_ev": [],
            "totales_ev": [],
            "modelo": []
        },
        "parley": [],
        "parley_millonario": []
    }

    if not games:
        return data

    picks_ml = []
    picks_totals = []
    picks_modelo = []
    candidatos_parley = []
    candidatos_millonario = []

    for g in games:
        try:
            teams = g.get("teams", {})
            away_data = teams.get("away", {})
            home_data = teams.get("home", {})

            away = away_data.get("team", {}).get("name")
            home = home_data.get("team", {}).get("name")
            if not away or not home:
                continue

            matchup_key = normalizar_matchup(away, home)

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
                "precip_mm": None
            }

            game_date = g.get("gameDate", "")
            hora_local = None
            try:
                dt_utc = datetime.fromisoformat(game_date.replace("Z", "+00:00"))
                if ZoneInfo:
                    dt_ve = dt_utc.astimezone(ZoneInfo("America/Caracas"))
                else:
                    dt_ve = dt_utc - timedelta(hours=4)
                hora_local = dt_ve.strftime("%I:%M %p")
            except Exception:
                hora_local = None

            status = g.get("status", {}).get("detailedState", "Estado desconocido")

            data["juegos_del_dia"].append({
                "game": f"{away} @ {home}",
                "matchup_key": matchup_key,
                "away": away,
                "home": home,
                "hora_venezuela": hora_local,
                "status": status,
                "pitchers": {
                    "away": away_p,
                    "home": home_p
                }
            })

            pred = obtener_pick_juego_pro(
                away, home, standings,
                away_p, home_p,
                away_stats, home_stats, weather
            )

            total_proj = estimar_total_juego_pro(
                away, home, standings,
                away_p, home_p,
                away_stats, home_stats, weather
            )

            pronostico_item = {
                "game": f"{away} @ {home}",
                "matchup_key": matchup_key,
                "pick": f"{pred['favorite']} ML",
                "confianza": pred["confidence_pct"],
                "confianza_label": pred["confidence_label"],
                "pitchers": f"{away_p} vs {home_p}",
                "era": f"{away_stats['era']} vs {home_stats['era']}",
                "total_proyectado": total_proj,
                "clima": {
                    "temp_c": safe_float(weather.get("temp_c")),
                    "wind_kmh": safe_float(weather.get("wind_kmh")),
                    "precip_mm": safe_float(weather.get("precip_mm"))
                }
            }
            picks_modelo.append(pronostico_item)

            odds = obtener_odds_completas(away, home)

            if odds and isinstance(odds, dict):
                cuota_ml = None

                if pred["favorite"] == home and odds.get("home_moneyline") is not None:
                    cuota_ml = odds.get("home_moneyline")
                elif pred["favorite"] == away and odds.get("away_moneyline") is not None:
                    cuota_ml = odds.get("away_moneyline")

                if cuota_ml is not None:
                    implied = moneyline_to_prob(cuota_ml)
                    ev = calcular_ev(pred["prob_favorite"], cuota_ml)
                    grade = grade_por_ev(ev)
                    stake = stake_por_ev(ev)

                    if grade != "D":
                        picks_ml.append({
                            "game": f"{away} @ {home}",
                            "matchup_key": matchup_key,
                            "pick": f"{pred['favorite']} ML",
                            "grade": grade,
                            "stake": stake,
                            "model_prob": round(pred["prob_favorite"] * 100, 1),
                            "implied_prob": round(implied * 100, 1) if implied is not None else None,
                            "edge": round((pred["prob_favorite"] - implied) * 100, 1) if implied is not None else 0,
                            "ev_pct": round(ev * 100, 2) if ev is not None else 0,
                            "cuota": safe_int(cuota_ml),
                            "confianza": pred["confidence_pct"],
                            "pitchers": f"{away_p} vs {home_p}",
                            "era": f"{away_stats['era']} vs {home_stats['era']}",
                            "clima": {
                                "temp_c": safe_float(weather.get("temp_c")),
                                "wind_kmh": safe_float(weather.get("wind_kmh")),
                                "precip_mm": safe_float(weather.get("precip_mm"))
                            }
                        })

                total_line = odds.get("total_line")
                total_pick = elegir_total_pick(total_proj, total_line)

                if total_pick:
                    if "Over" in total_pick["pick"]:
                        cuota_total = odds.get("over_price")
                        prob_total_model = clamp(0.50 + (total_pick["edge"] * 0.06), 0.51, 0.62)
                    else:
                        cuota_total = odds.get("under_price")
                        prob_total_model = clamp(0.50 + (total_pick["edge"] * 0.06), 0.51, 0.62)

                    if cuota_total is not None:
                        implied_total = moneyline_to_prob(cuota_total)
                        ev_total = calcular_ev(prob_total_model, cuota_total)
                        grade_total = grade_por_ev(ev_total)
                        stake_total = stake_por_ev(ev_total)

                        if grade_total != "D":
                            picks_totals.append({
                                "game": f"{away} @ {home}",
                                "matchup_key": matchup_key,
                                "pick": total_pick["pick"],
                                "grade": grade_total,
                                "stake": stake_total,
                                "ev_pct": round(ev_total * 100, 2) if ev_total is not None else 0,
                                "edge": total_pick["edge"],
                                "projection": total_proj,
                                "line": safe_float(total_line),
                                "model_prob": round(prob_total_model * 100, 1),
                                "implied_prob": round(implied_total * 100, 1) if implied_total is not None else None,
                                "cuota": safe_int(cuota_total),
                                "confianza": pred["confidence_pct"],
                                "pitchers": f"{away_p} vs {home_p}",
                                "era": f"{away_stats['era']} vs {home_stats['era']}",
                                "clima": {
                                    "temp_c": safe_float(weather.get("temp_c")),
                                    "wind_kmh": safe_float(weather.get("wind_kmh")),
                                    "precip_mm": safe_float(weather.get("precip_mm"))
                                }
                            })

            cuota_parley = "N/D"
            ev_parley = None
            grade_parley = "Modelo"
            edge_parley = 0

            if odds and not pred["avoid"]:
                if pred["favorite"] == home and odds.get("home_moneyline") is not None:
                    cuota_parley = odds.get("home_moneyline")
                elif pred["favorite"] == away and odds.get("away_moneyline") is not None:
                    cuota_parley = odds.get("away_moneyline")

                if cuota_parley != "N/D":
                    implied = moneyline_to_prob(cuota_parley)
                    edge_parley = round((pred["prob_favorite"] - implied) * 100, 1) if implied is not None else 0
                    ev_parley = calcular_ev(pred["prob_favorite"], cuota_parley)
                    grade_parley = grade_por_ev(ev_parley)

            candidatos_parley.append({
                "game": f"{away} @ {home}",
                "matchup_key": matchup_key,
                "pick": f"{pred['favorite']} ML",
                "grade": grade_parley,
                "edge": edge_parley,
                "ev_pct": round(ev_parley * 100, 2) if ev_parley is not None else 0,
                "confianza": pred["confidence_pct"],
                "cuota": safe_int(cuota_parley) if cuota_parley != "N/D" else "N/D",
                "pitchers": f"{away_p} vs {home_p}",
                "era": f"{away_stats['era']} vs {home_stats['era']}",
                "clima": {
                    "temp_c": safe_float(weather.get("temp_c")),
                    "wind_kmh": safe_float(weather.get("wind_kmh")),
                    "precip_mm": safe_float(weather.get("precip_mm"))
                }
            })

            cuota_ml_m = "N/D"
            edge_ml_m = 0
            implied_ml_m = None

            if odds and isinstance(odds, dict):
                if pred["favorite"] == home and odds.get("home_moneyline") is not None:
                    cuota_ml_m = odds.get("home_moneyline")
                    implied_ml_m = moneyline_to_prob(cuota_ml_m)
                elif pred["favorite"] == away and odds.get("away_moneyline") is not None:
                    cuota_ml_m = odds.get("away_moneyline")
                    implied_ml_m = moneyline_to_prob(cuota_ml_m)

                if implied_ml_m is not None:
                    edge_ml_m = round((pred["prob_favorite"] - implied_ml_m) * 100, 1)

            if not pred["avoid"]:
                candidatos_millonario.append({
                    "tipo": "ML",
                    "game": f"{away} @ {home}",
                    "matchup_key": matchup_key,
                    "pick": f"{pred['favorite']} ML",
                    "edge": edge_ml_m,
                    "confianza": pred["confidence_pct"],
                    "cuota": safe_int(cuota_ml_m) if cuota_ml_m != "N/D" else "N/D",
                    "pitchers": f"{away_p} vs {home_p}",
                    "era": f"{away_stats['era']} vs {home_stats['era']}",
                    "clima": {
                        "temp_c": safe_float(weather.get("temp_c")),
                        "wind_kmh": safe_float(weather.get("wind_kmh")),
                        "precip_mm": safe_float(weather.get("precip_mm"))
                    }
                })

            total_line_m = 8.5
            over_price = "N/D"
            under_price = "N/D"

            if odds and isinstance(odds, dict):
                if odds.get("total_line") is not None:
                    total_line_m = odds.get("total_line")
                over_price = odds.get("over_price", "N/D")
                under_price = odds.get("under_price", "N/D")

            total_pick_m = elegir_total_pick(total_proj, total_line_m)

            if total_pick_m:
                cuota_total_m = over_price if "Over" in total_pick_m["pick"] else under_price
                candidatos_millonario.append({
                    "tipo": "TOTAL",
                    "game": f"{away} @ {home}",
                    "matchup_key": matchup_key,
                    "pick": total_pick_m["pick"],
                    "edge": total_pick_m["edge"],
                    "confianza": pred["confidence_pct"],
                    "cuota": safe_int(cuota_total_m) if cuota_total_m != "N/D" else "N/D",
                    "pitchers": f"{away_p} vs {home_p}",
                    "era": f"{away_stats['era']} vs {home_stats['era']}",
                    "clima": {
                        "temp_c": safe_float(weather.get("temp_c")),
                        "wind_kmh": safe_float(weather.get("wind_kmh")),
                        "precip_mm": safe_float(weather.get("precip_mm"))
                    }
                })

        except Exception as game_error:
            print(f"Error procesando juego en dataset TikTok: {game_error}")
            continue

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




def construir_candidatos_base(standings, games, incluir_totales=False, excluir_matchups=None):
    excluir_matchups = set(excluir_matchups or [])
    candidatos = []

    for g in games:
        try:
            teams = g.get("teams", {})
            away_data = teams.get("away", {})
            home_data = teams.get("home", {})

            away = away_data.get("team", {}).get("name")
            home = home_data.get("team", {}).get("name")
            if not away or not home:
                continue

            matchup_key = normalizar_matchup(away, home)
            if matchup_key in excluir_matchups:
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
                "precip_mm": None
            }

            pred = obtener_pick_juego_pro(
                away, home, standings,
                away_p, home_p,
                away_stats, home_stats, weather
            )

            total_proj = estimar_total_juego_pro(
                away, home, standings,
                away_p, home_p,
                away_stats, home_stats, weather
            )

            odds = obtener_odds_completas(away, home)
            cuota_ml = "N/D"
            edge_ml = 0.0
            ev_ml = 0.0

            if odds and not pred["avoid"]:
                if pred["favorite"] == home and odds.get("home_moneyline") is not None:
                    cuota_ml = odds.get("home_moneyline")
                elif pred["favorite"] == away and odds.get("away_moneyline") is not None:
                    cuota_ml = odds.get("away_moneyline")

                if cuota_ml != "N/D":
                    implied = moneyline_to_prob(cuota_ml)
                    if implied is not None:
                        edge_ml = round((pred["prob_favorite"] - implied) * 100, 1)
                    ev_calc = calcular_ev(pred["prob_favorite"], cuota_ml)
                    if ev_calc is not None:
                        ev_ml = round(ev_calc * 100, 2)

            candidatos.append({
                "tipo": "ML",
                "game": f"{away} @ {home}",
                "matchup_key": matchup_key,
                "pick": f"{pred['favorite']} ML",
                "confidence": pred["confidence_pct"],
                "edge": edge_ml,
                "ev_pct": ev_ml,
                "cuota": cuota_ml,
                "pitchers": f"{away_p} vs {home_p}",
                "eras": f"{away_stats['era']} vs {home_stats['era']}",
                "weather": weather,
                "avoid": pred["avoid"]
            })

            if incluir_totales and odds and isinstance(odds, dict):
                total_line = odds.get("total_line")
                total_pick = elegir_total_pick(total_proj, total_line)

                if total_pick:
                    cuota_total = odds.get("over_price") if "Over" in total_pick["pick"] else odds.get("under_price")
                    candidatos.append({
                        "tipo": "TOTAL",
                        "game": f"{away} @ {home}",
                        "matchup_key": matchup_key,
                        "pick": total_pick["pick"],
                        "confidence": max(50.0, round(pred["confidence_pct"] - 0.4, 1)),
                        "edge": float(total_pick["edge"]),
                        "ev_pct": 0.0,
                        "cuota": cuota_total if cuota_total is not None else "N/D",
                        "pitchers": f"{away_p} vs {home_p}",
                        "eras": f"{away_stats['era']} vs {home_stats['era']}",
                        "weather": weather,
                        "avoid": False
                    })

        except Exception as game_error:
            print(f"Error construyendo candidato base: {game_error}")
            continue

    candidatos = filtrar_matchups_unicos(candidatos)
    candidatos.sort(key=lambda x: (x["confidence"], x["edge"], x["ev_pct"]), reverse=True)
    return candidatos


def seleccionar_parley_balanceado(candidatos, legs=3):
    tier1 = [c for c in candidatos if c["tipo"] == "ML" and (not c["avoid"]) and c["confidence"] >= 52.0]
    tier2 = [c for c in candidatos if c["tipo"] == "ML" and c["confidence"] >= 51.0]
    tier3 = [c for c in candidatos if c["tipo"] == "ML" and c["confidence"] >= 50.0]

    seleccionados = []
    for pool in (tier1, tier2, tier3, candidatos):
        for c in pool:
            if len(seleccionados) >= legs:
                break
            if c["tipo"] != "ML":
                continue
            if any(s["matchup_key"] == c["matchup_key"] for s in seleccionados):
                continue
            if c in seleccionados:
                continue
            seleccionados.append(c)
        if len(seleccionados) >= legs:
            break
    return seleccionados


def seleccionar_parley_millonario(candidatos, legs=10, excluir_matchups=None):
    excluir_matchups = set(excluir_matchups or [])
    filtrados = [c for c in candidatos if c["matchup_key"] not in excluir_matchups]

    ml = [c for c in filtrados if c["tipo"] == "ML"]
    totals = [c for c in filtrados if c["tipo"] == "TOTAL"]

    ml.sort(key=lambda x: (x["confidence"], x["edge"], x["ev_pct"]), reverse=True)
    totals.sort(key=lambda x: (x["edge"], x["confidence"]), reverse=True)

    seleccionados = []
    usados = set()

    # target mix: 5 ML + 5 totals if available
    for pool, target in ((ml, 5), (totals, 5), (ml, legs), (totals, legs)):
        current_type_count = sum(1 for s in seleccionados if s["tipo"] == ("ML" if pool is ml else "TOTAL"))
        for c in pool:
            if len(seleccionados) >= legs:
                break
            if current_type_count >= target and target != legs:
                break
            if c["matchup_key"] in usados:
                continue
            seleccionados.append(c)
            usados.add(c["matchup_key"])
            current_type_count += 1

    return seleccionados

# =========================================================
# CALLBACKS
# =========================================================
@bot.callback_query_handler(func=lambda call: call.data.startswith("cmd_"))
def callback_menu(call):
    try:
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
        elif call.data == "cmd_registrar_apuesta":
            bot.send_message(
         call.message.chat.id,
         "Usa:\n"
         "/registrar_apuesta juego | tipo | pick | cuota | prob_modelo | prob_implicita | edge | stake | grade"
     )

        elif call.data == "cmd_cerrar_apuesta":
            bot.send_message(
         call.message.chat.id,
         "Usa:\n"
         "/cerrar_apuesta fecha | juego | pick | resultado\n"
         "resultado = win o lose"
     )
        elif call.data == "cmd_exportar_json":
            exportar_json(call.message)

        bot.answer_callback_query(call.id)
    except Exception as e:
        bot.answer_callback_query(call.id, f"Error: {str(e)[:80]}")

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
    bot.send_message(message.chat.id, texto, parse_mode="HTML", reply_markup=menu_markup())
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
                msg.message_id
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

            juegos_ordenados.append({
                "away": away,
                "home": home,
                "score_away": sa,
                "score_home": sh,
                "status": status,
                "hora_txt": hora_txt,
                "hora_orden": hora_orden
            })

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
                    f"⚾️ Score: {j['score_away']} - {j['score_home']}"
                ]
            )

        bot.delete_message(msg.chat.id, msg.message_id)
        responder_largo(message.chat.id, texto, parse_mode="HTML")

    except Exception as e:
        bot.edit_message_text(
            f"❌ Error al cargar juegos: {str(e)[:120]}",
            msg.chat.id,
            msg.message_id
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
            "standingsTypes": "regularSeason"
        }

        data = safe_get(url, params=params)
        records = data.get("records", [])

        if not records:
            bot.edit_message_text(
                "❌ No pude cargar los standings.",
                msg.chat.id,
                msg.message_id
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
                "---------------------------------------------------------------"
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
            msg.message_id
        )

@bot.message_handler(commands=["apuestas"])
def apuestas(message):
    msg = bot.reply_to(message, "🔥 Analizando juegos con EV + stake automático...")
    try:
        standings = obtener_standings()
        games = obtener_juegos_del_dia()

        texto = header("APUESTAS PRO MLB", "💰")
        texto += f"📅 {hoy_str()}\n\n"

        if not games:
            texto += "No hay juegos hoy."
            bot.edit_message_text(texto, msg.chat.id, msg.message_id)
            return

        picks_ml = []
        picks_totals = []
        picks_modelo = []

        for g in games:
            try:
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
                    "precip_mm": None
                }

                pred = obtener_pick_juego_pro(
                    away, home, standings,
                    away_p, home_p,
                    away_stats, home_stats, weather
                )

                total_proj = estimar_total_juego_pro(
                    away, home, standings,
                    away_p, home_p,
                    away_stats, home_stats, weather
                )

                if not pred["avoid"]:
                    picks_modelo.append({
                        "game": f"{away} @ {home}",
                        "matchup_key": normalizar_matchup(away, home),
                        "pick": f"{pred['favorite']} ML",
                        "conf": pred["confidence_pct"],
                        "pitchers": f"{away_p} vs {home_p}",
                        "eras": f"{away_stats['era']} vs {home_stats['era']}",
                        "weather": weather,
                        "total_proj": total_proj
                    })

                odds = obtener_odds_completas(away, home)

                if odds and isinstance(odds, dict):
                    cuota_ml = None

                    if pred["favorite"] == home and odds.get("home_moneyline") is not None:
                        cuota_ml = odds.get("home_moneyline")
                    elif pred["favorite"] == away and odds.get("away_moneyline") is not None:
                        cuota_ml = odds.get("away_moneyline")

                    if cuota_ml is not None:
                        implied = moneyline_to_prob(cuota_ml)
                        ev = calcular_ev(pred["prob_favorite"], cuota_ml)
                        grade = grade_por_ev(ev)
                        stake = stake_por_ev(ev)

                        if grade != "D":
                            picks_ml.append({
                                "game": f"{away} @ {home}",
                                "matchup_key": normalizar_matchup(away, home),
                                "pick": f"{pred['favorite']} ML",
                                "grade": grade,
                                "stake": stake,
                                "model_prob": round(pred["prob_favorite"] * 100, 1),
                                "implied_prob": round(implied * 100, 1) if implied is not None else "-",
                                "edge": round((pred["prob_favorite"] - implied) * 100, 1) if implied is not None else 0,
                                "ev_pct": round(ev * 100, 2) if ev is not None else 0,
                                "cuota": cuota_ml,
                                "pitchers": f"{away_p} vs {home_p}",
                                "eras": f"{away_stats['era']} vs {home_stats['era']}",
                                "weather": weather,
                                "conf": pred["confidence_pct"]
                            })

                    total_line = odds.get("total_line")
                    total_pick = elegir_total_pick(total_proj, total_line)

                    if total_pick:
                        if "Over" in total_pick["pick"]:
                            cuota_total = odds.get("over_price")
                            prob_total_model = clamp(0.50 + (total_pick["edge"] * 0.06), 0.51, 0.62)
                        else:
                            cuota_total = odds.get("under_price")
                            prob_total_model = clamp(0.50 + (total_pick["edge"] * 0.06), 0.51, 0.62)

                        if cuota_total is not None:
                            implied_total = moneyline_to_prob(cuota_total)
                            ev_total = calcular_ev(prob_total_model, cuota_total)
                            grade_total = grade_por_ev(ev_total)
                            stake_total = stake_por_ev(ev_total)

                            if grade_total != "D":
                                picks_totals.append({
                                    "game": f"{away} @ {home}",
                                    "matchup_key": normalizar_matchup(away, home),
                                    "pick": total_pick["pick"],
                                    "grade": grade_total,
                                    "stake": stake_total,
                                    "ev_pct": round(ev_total * 100, 2) if ev_total is not None else 0,
                                    "edge_total": total_pick["edge"],
                                    "projection": total_proj,
                                    "line": total_line,
                                    "model_prob": round(prob_total_model * 100, 1),
                                    "implied_prob": round(implied_total * 100, 1) if implied_total is not None else "-",
                                    "cuota": cuota_total,
                                    "pitchers": f"{away_p} vs {home_p}",
                                    "eras": f"{away_stats['era']} vs {home_stats['era']}",
                                    "weather": weather,
                                    "conf": pred["confidence_pct"]
                                })

            except Exception as game_error:
                print(f"Error procesando juego en /apuestas: {game_error}")
                continue

        picks_ml.sort(key=lambda x: (x["ev_pct"], x["conf"]), reverse=True)
        picks_totals.sort(key=lambda x: (x["ev_pct"], x["edge_total"]), reverse=True)
        picks_modelo.sort(key=lambda x: x["conf"], reverse=True)

        picks_ml = filtrar_matchups_unicos(picks_ml)
        picks_totals = filtrar_matchups_unicos(picks_totals)
        picks_modelo = filtrar_matchups_unicos(picks_modelo)

        texto += "💰 <b>MONEYLINE CON EV+</b>\n━━━━━━━━━━━━━━━━━━\n\n"
        if picks_ml:
            for p in picks_ml[:5]:
                texto += card_game(
                    p["game"],
                    [
                        f"🎯 Pick: <b>{p['pick']}</b>",
                        f"🏷️ Grade: <b>{p['grade']}</b> | Stake: <b>{p['stake']}</b>",
                        f"💵 Cuota: <b>{p['cuota']}</b>",
                        f"🧠 Modelo: {p['model_prob']}% | Implícita: {p['implied_prob']}%",
                        f"📈 Edge: <b>+{p['edge']}%</b> | EV: <b>{p['ev_pct']}%</b>",
                        f"🎽 Pitchers: {p['pitchers']}",
                        f"📉 ERA: {p['eras']}",
                        f"🌡️ Clima: {p['weather'].get('temp_c')}°C | 💨 {p['weather'].get('wind_kmh')} km/h"
                    ]
                )
        else:
            texto += "No hubo moneylines con EV positivo suficiente.\n\n"
            texto += "📊 <b>TOTALES CON EV+</b>\n━━━━━━━━━━━━━━━━━━\n\n"
        if picks_totals:
            for p in picks_totals[:5]:
                texto += card_game(
                    p["game"],
                    [
                        f"🎯 Pick: <b>{p['pick']}</b>",
                        f"🏷️ Grade: <b>{p['grade']}</b> | Stake: <b>{p['stake']}</b>",
                        f"💵 Cuota: <b>{p['cuota']}</b>",
                        f"📊 Proyección: {p['projection']} | Línea: {p['line']}",
                        f"🧠 Modelo: {p['model_prob']}% | Implícita: {p['implied_prob']}%",
                        f"📈 Edge: <b>{p['edge_total']}</b> | EV: <b>{p['ev_pct']}%</b>",
                        f"🎽 Pitchers: {p['pitchers']}",
                        f"📉 ERA: {p['eras']}",
                        f"🌡️ Clima: {p['weather'].get('temp_c')}°C | 💨 {p['weather'].get('wind_kmh')} km/h"
                    ]
                )
        else:
            texto += "No hubo totales con EV positivo suficiente.\n\n"

        texto += "🧠 <b>MEJORES PICKS DEL MODELO</b>\n━━━━━━━━━━━━━━━━━━\n\n"
        if picks_modelo:
            for p in picks_modelo[:5]:
                texto += card_game(
                    p["game"],
                    [
                        f"🎯 Pick modelo: <b>{p['pick']}</b>",
                        f"🧠 Confianza: <b>{p['conf']}%</b>",
                        f"🎽 Pitchers: {p['pitchers']}",
                        f"📉 ERA: {p['eras']}",
                        f"📊 Total proyectado: <b>{p['total_proj']}</b>",
                        f"🌡️ Clima: {p['weather'].get('temp_c')}°C | 💨 {p['weather'].get('wind_kmh')} km/h"
                    ]
                )
        else:
            texto += "No se pudieron generar picks del modelo."

        bot.delete_message(msg.chat.id, msg.message_id)
        responder_largo(message.chat.id, texto, parse_mode="HTML")

    except Exception as e:
        import traceback
        print(traceback.format_exc())
        bot.edit_message_text(
            f"❌ Error en /apuestas: {str(e)[:200]}",
            msg.chat.id,
            msg.message_id
        )

# PARLEY FUNCTION FIX (PRO_FALLBACK_V3)


@bot.message_handler(commands=["parley", "parley_del_dia"])
def parley(message):
    msg = bot.reply_to(message, "🎯 Construyendo parley V5 balanceado...")
    try:
        standings = obtener_standings()
        games = obtener_juegos_del_dia()
        candidatos = construir_candidatos_base(standings, games, incluir_totales=False)
        seleccionados = seleccionar_parley_balanceado(candidatos, legs=3)

        texto = header("PARLEY BALANCEADO MLB", "🎯")
        texto += f"📅 {hoy_str()}\n\n"

        if not seleccionados:
            texto += "No hay juegos suficientes hoy."
        else:
            for p in seleccionados:
                texto += card_game(
                    p["game"],
                    [
                        f"🎯 Pick: <b>{p['pick']}</b>",
                        f"🧠 Confianza: <b>{p['confidence']}%</b>",
                        f"📈 Edge: <b>{p['edge']}%</b> | EV: <b>{p['ev_pct']}%</b>",
                        f"💵 Cuota: <b>{p['cuota']}</b>",
                        f"🎽 Pitchers: {p['pitchers']}",
                        f"📉 ERA: {p['eras']}",
                        f"🌡️ Temp: {p['weather'].get('temp_c')}°C | 💨 Viento: {p['weather'].get('wind_kmh')} km/h"
                    ]
                )

        bot.edit_message_text(texto, msg.chat.id, msg.message_id, parse_mode="HTML")

    except Exception as e:
        import traceback
        print(traceback.format_exc())
        bot.edit_message_text(
            f"❌ Error en /parley: {str(e)[:120]}",
            msg.chat.id,
            msg.message_id
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
            "standingsTypes": "regularSeason"
        }

        data = safe_get(url, params=params)
        records = data.get("records", [])

        if not records:
            bot.edit_message_text(
                "❌ No pude cargar los standings.",
                msg.chat.id,
                msg.message_id
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
                "---------------------------------------------------------------"
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
            msg.message_id
        )

@bot.message_handler(commands=["parley_millonario"])
def parley_millonario(message):
    msg = bot.reply_to(message, "💎 Construyendo parley millonario SOLO TOTALS...")
    try:
        standings = obtener_standings()
        games = obtener_juegos_del_dia()

        candidatos_totals = []

        for g in games:
            try:
                teams = g.get("teams", {})
                away_data = teams.get("away", {})
                home_data = teams.get("home", {})

                away = away_data.get("team", {}).get("name")
                home = home_data.get("team", {}).get("name")
                if not away or not home:
                    continue

                matchup_key = normalizar_matchup(away, home)

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
                    "precip_mm": None
                }

                pred = obtener_pick_juego_pro(
                    away, home, standings,
                    away_p, home_p,
                    away_stats, home_stats, weather
                )

                total_proj = estimar_total_juego_pro(
                    away, home, standings,
                    away_p, home_p,
                    away_stats, home_stats, weather
                )

                odds = obtener_odds_completas(away, home)

                total_pick = None
                cuota_total = "N/D"

                if odds and isinstance(odds, dict) and odds.get("total_line") is not None:
                    total_line = odds.get("total_line")
                    total_pick = elegir_total_pick(total_proj, total_line)

                    if total_pick:
                        cuota_total = odds.get("over_price") if "Over" in total_pick["pick"] else odds.get("under_price")
                else:
                    total_pick = elegir_total_pick_fallback(total_proj)

                if total_pick:
                    candidatos_totals.append({
                        "tipo": "TOTAL",
                        "game": f"{away} @ {home}",
                        "matchup_key": matchup_key,
                        "pick": total_pick["pick"],
                        "confidence": pred["confidence_pct"],
                        "edge": round(total_pick["edge"], 2),
                        "ev_pct": 0.0,
                        "cuota": cuota_total,
                        "pitchers": f"{away_p} vs {home_p}",
                        "eras": f"{away_stats['era']} vs {home_stats['era']}",
                        "weather": weather,
                        "total_proj": total_proj
                    })

            except Exception as game_error:
                print(f"Error procesando juego en /parley_millonario: {game_error}")
                continue

        candidatos_totals = filtrar_matchups_unicos(candidatos_totals)

        # ordenar por edge real de totals
        candidatos_totals.sort(key=lambda x: (x["edge"], x["total_proj"]), reverse=True)

        seleccionados = candidatos_totals[:10]

        texto = header("PARLEY MILLONARIO SOLO TOTALS", "💎")
        texto += f"📅 {hoy_str()}\n\n"

        if not seleccionados:
            texto += "No hubo totals suficientes hoy."
        else:
            texto += f"📌 Totals seleccionados: <b>{len(seleccionados)}</b>\n\n"

            for p in seleccionados:
                texto += card_game(
                    p["game"],
                    [
                        f"🔥 Pick: <b>{p['pick']}</b>",
                        f"📌 Tipo: <b>{p['tipo']}</b>",
                        f"📈 Edge: <b>{p['edge']}%</b>",
                        f"💵 Cuota: <b>{p['cuota']}</b>",
                        f"📊 Total proyectado: <b>{p['total_proj']}</b>",
                        f"🎽 Pitchers: {p['pitchers']}",
                        f"📉 ERA: {p['eras']}",
                        f"🌡️ Temp: {p['weather'].get('temp_c')}°C | 💨 Viento: {p['weather'].get('wind_kmh')} km/h"
                    ]
                )

        bot.edit_message_text(texto, msg.chat.id, msg.message_id, parse_mode="HTML")

    except Exception as e:
        import traceback
        print(traceback.format_exc())
        bot.edit_message_text(
            f"❌ Error en /parley_millonario: {str(e)[:120]}",
            msg.chat.id,
            msg.message_id
        )
@bot.message_handler(commands=["pitchers"])
def pitchers(message):
    msg = bot.reply_to(message, "🧢 Cargando pitchers...")
    try:
        games = obtener_juegos_del_dia()
        texto = header("PITCHERS DEL DÍA", "🧢")
        texto += f"📅 {hoy_str()}\n\n"

        if not games:
            texto += "No hay juegos hoy."
            bot.edit_message_text(texto, msg.chat.id, msg.message_id)
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

            away_pid = away_pitcher_obj.get("id")
            home_pid = home_pitcher_obj.get("id")

            away_stats = obtener_stats_pitcher_reales(away_pid)
            home_stats = obtener_stats_pitcher_reales(home_pid)

            texto += card_game(
                f"{away} @ {home}",
                [
                    f"🎽 {away_p} vs {home_p}",
                    f"📉 ERA: {away_stats['era']} vs {home_stats['era']}",
                    f"🧪 WHIP: {away_stats['whip']} vs {home_stats['whip']}"
                ]
            )

        bot.delete_message(msg.chat.id, msg.message_id)
        responder_largo(message.chat.id, texto, parse_mode="HTML")

    except Exception as e:
        bot.edit_message_text(f"❌ Error al cargar pitchers: {str(e)[:120]}", msg.chat.id, msg.message_id)

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
            try:
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
                    "precip_mm": None
                }

                pred = obtener_pick_juego_pro(
                    away, home, standings,
                    away_p, home_p,
                    away_stats, home_stats, weather
                )

                # evitar mostrar picks absurdamente planos
                if pred["confidence_pct"] < 50.8:
                    continue

                picks.append({
                    "game": f"{away} @ {home}",
                    "matchup_key": normalizar_matchup(away, home),
                    "pick": f"{pred['favorite']} ML",
                    "conf": pred["confidence_pct"],
                    "pitchers": f"{away_p} vs {home_p}",
                    "eras": f"{away_stats['era']} vs {home_stats['era']}"
                })

            except Exception as game_error:
                print(f"Error procesando juego en /pronosticos: {game_error}")
                continue

        picks = filtrar_matchups_unicos(picks)
        picks.sort(key=lambda x: x["conf"], reverse=True)

        if not picks:
            texto += "Hoy no hubo ventajas claras del modelo."
        else:
            for p in picks[:8]:
                texto += card_game(
                    p["game"],
                    [
                        f"🎯 Pick: <b>{p['pick']}</b>",
                        f"🧠 Confianza: <b>{p['conf']}%</b>",
                        f"🎽 Pitchers: {p['pitchers']}",
                        f"📉 ERA: {p['eras']}"
                    ]
                )

        bot.edit_message_text(texto, msg.chat.id, msg.message_id, parse_mode="HTML")

    except Exception as e:
        import traceback
        print(traceback.format_exc())
        bot.edit_message_text(
            f"❌ Error en /pronosticos: {str(e)[:120]}",
            msg.chat.id,
            msg.message_id
        )

@bot.message_handler(commands=["exportar_json"])
def exportar_json(message):
    msg = bot.reply_to(message, "📦 Generando archivo JSON maestro para TikTok...")
    try:
        data = generar_dataset_tiktok()
        ruta = guardar_json_tiktok(data)

        if not ruta:
            bot.edit_message_text(
                "❌ No se pudo guardar el archivo JSON.",
                msg.chat.id,
                msg.message_id
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
            f"❌ Error en /exportar_json: {str(e)[:180]}",
            msg.chat.id,
            msg.message_id
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

            if any(x in lower for x in ["injured", "il", "injury", "60-day", "15-day", "10-day", "placed on"]):
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

@bot.message_handler(commands=["registrar_apuesta"])
def registrar_apuesta(message):
    """
    Uso:
    /registrar_apuesta juego | tipo | pick | cuota | prob_modelo | prob_implicita | edge | stake | grade

    Ejemplo:
    /registrar_apuesta Yankees @ Red Sox | ML | Yankees ML | -125 | 56.2 | 52.4 | 3.8 | 1u | A
    """
    try:
        texto = message.text.replace("/registrar_apuesta", "", 1).strip()
        partes = [p.strip() for p in texto.split("|")]

        if len(partes) != 9:
            bot.reply_to(
                message,
                "Formato incorrecto.\n\n"
                "Usa:\n"
                "/registrar_apuesta juego | tipo | pick | cuota | prob_modelo | prob_implicita | edge | stake | grade"
            )
            return

        juego, tipo_apuesta, pick, cuota, prob_modelo, prob_implicita, edge, stake, grade = partes

        ok = registrar_apuesta_csv(
            fecha=hoy_str(),
            juego=juego,
            tipo_apuesta=tipo_apuesta,
            pick=pick,
            cuota=cuota,
            prob_modelo=prob_modelo,
            prob_implicita=prob_implicita,
            edge=edge,
            stake=stake,
            grade=grade
        )

        if ok:
            bot.reply_to(
                message,
                "✅ Apuesta registrada correctamente.\n\n"
                f"📅 Fecha: {hoy_str()}\n"
                f"🎯 Juego: {juego}\n"
                f"📌 Pick: {pick}\n"
                f"💵 Cuota: {cuota}\n"
                f"🏷️ Stake: {stake}"
            )
        else:
            bot.reply_to(message, "❌ No se pudo registrar la apuesta.")

    except Exception as e:
        bot.reply_to(message, f"❌ Error registrando apuesta: {str(e)[:150]}")
        
@bot.message_handler(commands=["cerrar_apuesta"])
def cerrar_apuesta(message):
    """
    Uso:
    /cerrar_apuesta fecha | juego | pick | resultado
    resultado = win o lose
    """
    try:
        texto = message.text.replace("/cerrar_apuesta", "", 1).strip()
        partes = [p.strip() for p in texto.split("|")]

        if len(partes) != 4:
            bot.reply_to(
                message,
                "Formato incorrecto.\n\n"
                "Usa:\n"
                "/cerrar_apuesta fecha | juego | pick | resultado\n\n"
                "resultado debe ser: win o lose"
            )
            return

        fecha, juego, pick, resultado = partes
        resultado = resultado.lower().strip()

        if resultado not in ["win", "lose"]:
            bot.reply_to(message, "❌ El resultado debe ser 'win' o 'lose'.")
            return

        ok = cerrar_apuesta_csv(fecha, juego, pick, resultado)

        if ok:
            bot.reply_to(
                message,
                "✅ Apuesta cerrada correctamente.\n\n"
                f"📅 {fecha}\n"
                f"🎯 {juego}\n"
                f"📌 {pick}\n"
                f"🏁 Resultado: {resultado.upper()}"
            )
        else:
            bot.reply_to(
                message,
                "❌ No encontré esa apuesta pendiente.\n"
                "Revisa fecha, juego y pick."
            )

    except Exception as e:
        bot.reply_to(message, f"❌ Error cerrando apuesta: {str(e)[:150]}")

@bot.message_handler(commands=["roi"])
def roi(message):
    msg = bot.reply_to(message, "📈 Calculando ROI...")
    try:
        if not os.path.exists(RESULTADOS_CSV):
            bot.edit_message_text(
                "Todavía no existe el CSV de resultados.",
                msg.chat.id,
                msg.message_id
            )
            return

        total_apostado = 0.0
        total_profit = 0.0
        ganadas = 0
        perdidas = 0
        pendientes = 0
        total_cerradas = 0

        with open(RESULTADOS_CSV, "r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                resultado = str(row.get("resultado", "")).strip().lower()

                if resultado == "pendiente":
                    pendientes += 1
                    continue

                if resultado not in ["win", "lose"]:
                    continue

                stake = extraer_unidades(row.get("stake", "0u"))
                profit = float(row.get("profit", 0) or 0)

                total_apostado += stake
                total_profit += profit
                total_cerradas += 1

                if resultado == "win":
                    ganadas += 1
                elif resultado == "lose":
                    perdidas += 1

        if total_cerradas == 0:
            bot.edit_message_text(
                "Todavía no hay apuestas cerradas en el CSV para calcular ROI.",
                msg.chat.id,
                msg.message_id
            )
            return

        roi_pct = (total_profit / total_apostado * 100) if total_apostado > 0 else 0
        hit_rate = (ganadas / total_cerradas * 100) if total_cerradas > 0 else 0

        texto = header("ROI Y EFECTIVIDAD", "📈")
        texto += (
            f"✅ Ganadas: <b>{ganadas}</b>\n"
            f"❌ Perdidas: <b>{perdidas}</b>\n"
            f"⏳ Pendientes: <b>{pendientes}</b>\n"
            f"📊 Cerradas: <b>{total_cerradas}</b>\n\n"
            f"🎯 Hit Rate: <b>{hit_rate:.2f}%</b>\n"
            f"💰 Profit: <b>{total_profit:.2f}u</b>\n"
            f"🏦 Apostado: <b>{total_apostado:.2f}u</b>\n"
            f"📈 ROI: <b>{roi_pct:.2f}%</b>\n"
        )

        bot.edit_message_text(texto, msg.chat.id, msg.message_id, parse_mode="HTML")

    except Exception as e:
        bot.edit_message_text(
            f"❌ Error calculando ROI: {str(e)[:180]}",
            msg.chat.id,
            msg.message_id
        )


print(f"INICIANDO BOT {BOT_VERSION}")
bot.infinity_polling(skip_pending=True, timeout=30, long_polling_timeout=30)