import os
import csv
import json
import math
import logging
import time
import threading
from datetime import datetime, date, timedelta
from functools import lru_cache
from typing import Dict, List, Optional, Tuple, Any

import requests
import telebot
from dotenv import load_dotenv
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton

try:
    from zoneinfo import ZoneInfo
except ImportError:
    ZoneInfo = None

# =========================================================
# LOGGING
# =========================================================
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[logging.FileHandler("bot.log"), logging.StreamHandler()]
)
logger = logging.getLogger("MLB_BOT")

# =========================================================
# VERSION
# =========================================================
BOT_VERSION = "V7_3_FULL_FIX"

# =========================================================
# CONFIG
# =========================================================
load_dotenv()
TOKEN = os.getenv("TOKEN")
ODDS_API_KEY = os.getenv("ODDS_API_KEY", "").strip()
if not TOKEN:
    raise ValueError("Falta TOKEN en .env")

bot = telebot.TeleBot(TOKEN, parse_mode=None)

MLB_BASE = "https://statsapi.mlb.com/api/v1"
HISTORIAL_FILE = "historial_parlays.json"
RESULTADOS_CSV = "resultados_apuestas.csv"
PARLEYS_DIARIOS_FILE = "parleys_diarios.json"
CALIBRACION_FILE = "calibracion_pesos.json"
REQUEST_TIMEOUT = 20
MAX_RETRIES = 3

# =========================================================
# PERSISTENCIA
# =========================================================
def cargar_json(file: str, default=None):
    if not os.path.exists(file):
        return default if default is not None else []
    try:
        with open(file, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        logger.error(f"Error cargando {file}: {e}")
        return default if default is not None else []

def guardar_json(file: str, data):
    try:
        with open(file, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.error(f"Error guardando {file}: {e}")

def inicializar_csv_resultados():
    if not os.path.exists(RESULTADOS_CSV):
        with open(RESULTADOS_CSV, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow([
                "fecha", "juego", "tipo_apuesta", "pick", "cuota",
                "prob_modelo", "prob_implicita", "edge", "stake",
                "grade", "resultado", "profit", "analisis"
            ])

def registrar_apuesta_en_csv(fecha, juego, tipo, pick, cuota, prob_modelo, prob_impl, edge, stake, grade, resultado, profit, analisis=""):
    with open(RESULTADOS_CSV, "a", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow([fecha, juego, tipo, pick, cuota, prob_modelo, prob_impl, edge, stake, grade, resultado, profit, analisis])

# =========================================================
# PARLEYS
# =========================================================
def cargar_parleys_diarios():
    return cargar_json(PARLEYS_DIARIOS_FILE, [])

def guardar_parleys_diarios(data):
    guardar_json(PARLEYS_DIARIOS_FILE, data)

def buscar_parley_del_dia(tipo, fecha=None):
    if fecha is None:
        fecha = hoy_str()
    for p in cargar_parleys_diarios():
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
    for p in data:
        if p.get("fecha") == fecha and p.get("tipo") == tipo:
            p["estado"] = estado
            guardar_parleys_diarios(data)
            return True
    return False

def eliminar_parley_del_dia(tipo, fecha=None):
    if fecha is None:
        fecha = hoy_str()
    data = cargar_parleys_diarios()
    new_data = [p for p in data if not (p.get("fecha") == fecha and p.get("tipo") == tipo)]
    if len(new_data) != len(data):
        guardar_parleys_diarios(new_data)
        return True
    return False

# =========================================================
# UTILIDADES
# =========================================================
def hoy_str():
    return date.today().strftime("%Y-%m-%d")

def clamp(value, low, high):
    return max(low, min(high, value))

def logistic(x):
    return 1 / (1 + math.exp(-x))

def safe_get(url, params=None, timeout=REQUEST_TIMEOUT):
    for attempt in range(MAX_RETRIES):
        try:
            r = requests.get(url, params=params, timeout=timeout)
            r.raise_for_status()
            return r.json()
        except Exception as e:
            logger.warning(f"Intento {attempt+1} fallido para {url}: {e}")
            time.sleep(2 ** attempt)
    logger.error(f"Fallo definitivo para {url}")
    return {}

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

def american_to_decimal(american_odds):
    try:
        american_odds = float(american_odds)
        if american_odds > 0:
            return 1 + (american_odds / 100)
        else:
            return 1 + (100 / abs(american_odds))
    except Exception:
        return None

def moneyline_to_prob(moneyline):
    try:
        ml = int(moneyline)
        if ml > 0:
            return 100 / (ml + 100)
        else:
            return abs(ml) / (abs(ml) + 100)
    except Exception:
        return None

def extraer_unidades(stake_texto):
    try:
        return float(str(stake_texto).lower().replace("u", "").strip())
    except Exception:
        return 0.0

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

def normalizar_matchup(away, home):
    return f"{away.lower()} @ {home.lower()}"

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

# =========================================================
# MLB DATA
# =========================================================
def temporada_actual():
    return date.today().year

def obtener_standings():
    url = f"{MLB_BASE}/standings"
    params = {"leagueId": "103,104", "season": temporada_actual(), "standingsTypes": "regularSeason"}
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
            equipos[name] = {
                "wins": wins,
                "losses": losses,
                "win_pct": wins / games,
                "home_win_pct": t.get("homeWins", 0) / max(t.get("homeWins", 0) + t.get("homeLosses", 0), 1),
                "away_win_pct": t.get("awayWins", 0) / max(t.get("awayWins", 0) + t.get("awayLosses", 0), 1),
                "run_diff": t.get("runsScored", 0) - t.get("runsAllowed", 0),
                "runs_scored": t.get("runsScored", 0) / games,
                "runs_allowed": t.get("runsAllowed", 0) / games,
                "last10_win_pct": t.get("lastTenWins", 0) / max(t.get("lastTenWins", 0) + t.get("lastTenLosses", 0), 1),
                "streak": t.get("streakCode", ""),
            }
    return equipos

def obtener_juegos_del_dia():
    url = f"{MLB_BASE}/schedule"
    params = {"sportId": 1, "date": hoy_str(), "hydrate": "probablePitcher,venue"}
    data = safe_get(url, params=params)
    dates = data.get("dates", [])
    if not dates:
        return []
    return dates[0].get("games", [])

def obtener_resultados_juegos_fecha(fecha=None):
    if fecha is None:
        fecha = hoy_str()
    url = f"{MLB_BASE}/schedule"
    params = {"sportId": 1, "date": fecha, "hydrate": "linescore"}
    data = safe_get(url, params=params)
    dates = data.get("dates", [])
    if not dates:
        return []
    games = dates[0].get("games", [])
    resultados = []
    for g in games:
        teams = g.get("teams", {})
        away = teams.get("away", {}).get("team", {}).get("name")
        home = teams.get("home", {}).get("team", {}).get("name")
        status = g.get("status", {}).get("detailedState")
        if status == "Final":
            away_score = teams.get("away", {}).get("score", 0)
            home_score = teams.get("home", {}).get("score", 0)
            winner = home if home_score > away_score else away if away_score > home_score else "Tie"
            resultados.append({
                "game": f"{away} @ {home}",
                "winner": winner,
                "away_score": away_score,
                "home_score": home_score
            })
    return resultados

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
    if not stats_list:
        return base
    splits = stats_list[0].get("splits", [])
    if not splits:
        return base
    stat = splits[0].get("stat", {})
    era = safe_float(stat.get("era"), 4.20)
    whip = safe_float(stat.get("whip"), 1.30)
    ip = safe_float(stat.get("inningsPitched"), 0.0)
    strikeouts = safe_int(stat.get("strikeOuts"), 0)
    so9 = (strikeouts * 9 / ip) if ip > 0 else 8.2
    return {
        "era": round(era, 2),
        "whip": round(whip, 2),
        "so9": round(so9, 2),
        "ip": round(ip, 1),
        "sample_ok": ip >= 10
    }

def obtener_clima_partido(game):
    try:
        venue_id = game.get("venue", {}).get("id")
        if not venue_id:
            return {"temp_c": None, "wind_kmh": None, "precip_mm": None}
        url_venue = f"{MLB_BASE}/venues"
        data_venue = safe_get(url_venue, params={"venueIds": str(venue_id)})
        venues = data_venue.get("venues", [])
        if not venues:
            return {"temp_c": None, "wind_kmh": None, "precip_mm": None}
        venue = venues[0]
        location = venue.get("location", {})
        coords = location.get("defaultCoordinates", {})
        lat = coords.get("latitude")
        lon = coords.get("longitude")
        if lat is None or lon is None:
            return {"temp_c": None, "wind_kmh": None, "precip_mm": None}
        game_date = game.get("gameDate")
        if not game_date:
            return {"temp_c": None, "wind_kmh": None, "precip_mm": None}
        dt_utc = datetime.fromisoformat(game_date.replace("Z", "+00:00"))
        url_weather = "https://api.open-meteo.com/v1/forecast"
        params = {
            "latitude": lat,
            "longitude": lon,
            "hourly": "temperature_2m,precipitation,wind_speed_10m",
            "timezone": "auto",
            "forecast_days": 2
        }
        weather_data = safe_get(url_weather, params=params)
        hourly = weather_data.get("hourly", {})
        times = hourly.get("time", [])
        if not times:
            return {"temp_c": None, "wind_kmh": None, "precip_mm": None}
        best_idx = 0
        min_diff = float('inf')
        for i, t in enumerate(times):
            try:
                dt_local = datetime.fromisoformat(t)
                diff = abs((dt_local - dt_utc).total_seconds())
                if diff < min_diff:
                    min_diff = diff
                    best_idx = i
            except:
                continue
        temp = hourly.get("temperature_2m", [None])[best_idx] if best_idx < len(hourly.get("temperature_2m", [])) else None
        wind = hourly.get("wind_speed_10m", [None])[best_idx] if best_idx < len(hourly.get("wind_speed_10m", [])) else None
        precip = hourly.get("precipitation", [None])[best_idx] if best_idx < len(hourly.get("precipitation", [])) else None
        return {"temp_c": safe_float(temp), "wind_kmh": safe_float(wind), "precip_mm": safe_float(precip)}
    except Exception as e:
        logger.error(f"Error clima: {e}")
        return {"temp_c": None, "wind_kmh": None, "precip_mm": None}

# =========================================================
# MODELO MEJORADO
# =========================================================
class MLBModel:
    def __init__(self):
        self.pesos = self.cargar_pesos()

    def cargar_pesos(self):
        default = {
            "diff_win_pct": 2.8, "diff_split": 1.9, "diff_last10": 1.2,
            "diff_run_diff": 1.6, "diff_streak": 1.0, "diff_runs_scored": 0.9,
            "diff_runs_allowed": 0.9, "diff_pitcher": 1.8, "intercept": 0.09,
            "clima_ml": 0.01, "penalizacion_tbd": 0.04
        }
        data = cargar_json(CALIBRACION_FILE, default)
        for k, v in default.items():
            if k not in data:
                data[k] = v
        return data

    def score_pitcher(self, stats):
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

    def ajuste_clima_ml(self, weather):
        if not weather:
            return 0.0
        adj = 0.0
        if weather.get("precip_mm") is not None and weather["precip_mm"] >= 1.0:
            adj -= self.pesos["clima_ml"]
        if weather.get("temp_c") is not None and weather["temp_c"] <= 8:
            adj -= self.pesos["clima_ml"]
        return adj

    def calcular_prob_local(self, away_team, home_team, standings,
                           away_pitcher="TBD", home_pitcher="TBD",
                           away_stats=None, home_stats=None, weather=None):
        away = standings.get(away_team)
        home = standings.get(home_team)
        if not away or not home:
            return 0.50
        diff_win_pct = home["win_pct"] - away["win_pct"]
        diff_split = home["home_win_pct"] - away["away_win_pct"]
        diff_last10 = home["last10_win_pct"] - away["last10_win_pct"]
        diff_run_diff = (home["run_diff"] - away["run_diff"]) / 100.0
        diff_streak = parse_streak(home["streak"]) - parse_streak(away["streak"])
        diff_runs_scored = (home.get("runs_scored", 4.5) - away.get("runs_scored", 4.5)) / 10.0
        diff_runs_allowed = (away.get("runs_allowed", 4.5) - home.get("runs_allowed", 4.5)) / 10.0
        if away_stats is None:
            away_stats = {"era": 4.20, "whip": 1.30, "so9": 8.2, "ip": 0.0, "sample_ok": False}
        if home_stats is None:
            home_stats = {"era": 4.20, "whip": 1.30, "so9": 8.2, "ip": 0.0, "sample_ok": False}
        p_home = self.score_pitcher(home_stats)
        p_away = self.score_pitcher(away_stats)
        diff_pitcher = p_home - p_away
        score = 0.0
        score += diff_win_pct * self.pesos["diff_win_pct"]
        score += diff_split * self.pesos["diff_split"]
        score += diff_last10 * self.pesos["diff_last10"]
        score += diff_run_diff * self.pesos["diff_run_diff"]
        score += diff_streak * self.pesos["diff_streak"]
        score += diff_runs_scored * self.pesos["diff_runs_scored"]
        score += diff_runs_allowed * self.pesos["diff_runs_allowed"]
        score += diff_pitcher * self.pesos["diff_pitcher"]
        score += self.pesos["intercept"]
        score += self.ajuste_clima_ml(weather)
        if away_pitcher == "TBD":
            score += self.pesos["penalizacion_tbd"]
        if home_pitcher == "TBD":
            score -= self.pesos["penalizacion_tbd"]
        prob = logistic(score)
        if 0.495 <= prob <= 0.505:
            prob = 0.518 if score >= 0 else 0.482
        return clamp(prob, 0.32, 0.68)

    def obtener_pick(self, away_team, home_team, standings,
                    away_pitcher="TBD", home_pitcher="TBD",
                    away_stats=None, home_stats=None, weather=None):
        prob_home = self.calcular_prob_local(away_team, home_team, standings,
                                             away_pitcher, home_pitcher,
                                             away_stats, home_stats, weather)
        favorito = home_team if prob_home >= 0.5 else away_team
        prob_fav = prob_home if favorito == home_team else (1 - prob_home)
        avoid = away_pitcher == "TBD" or home_pitcher == "TBD"
        return {
            "favorite": favorito,
            "prob_home": prob_home,
            "prob_favorite": prob_fav,
            "confidence_pct": round(prob_fav * 100, 1),
            "confidence_label": confidence_label(prob_fav),
            "avoid": avoid
        }

# =========================================================
# TOTALES Y ODDS
# =========================================================
def estimar_total_juego(away_team, home_team, standings, away_pitcher="TBD", home_pitcher="TBD",
                       away_stats=None, home_stats=None, weather=None):
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
    if away_stats is None:
        away_stats = {"era": 4.20, "whip": 1.30, "so9": 8.2, "ip": 0.0, "sample_ok": False}
    if home_stats is None:
        home_stats = {"era": 4.20, "whip": 1.30, "so9": 8.2, "ip": 0.0, "sample_ok": False}
    total += (away_stats.get("era", 4.20) - 4.00) * 0.30
    total += (home_stats.get("era", 4.20) - 4.00) * 0.30
    total += (away_stats.get("whip", 1.30) - 1.25) * 0.75
    total += (home_stats.get("whip", 1.30) - 1.25) * 0.75
    total -= (away_stats.get("so9", 8.2) - 8.5) * 0.08
    total -= (home_stats.get("so9", 8.2) - 8.5) * 0.08
    if away_pitcher == "TBD":
        total += 0.45
    if home_pitcher == "TBD":
        total += 0.45
    last10_away = away.get("last10_win_pct", 0.5)
    last10_home = home.get("last10_win_pct", 0.5)
    total += ((last10_away + last10_home) - 1.0) * 0.30
    if weather:
        temp = weather.get("temp_c")
        wind = weather.get("wind_kmh")
        precip = weather.get("precip_mm")
        if temp is not None:
            if temp >= 28:
                total += 0.35
            elif temp >= 24:
                total += 0.20
            elif temp <= 10:
                total -= 0.30
            elif temp <= 15:
                total -= 0.15
        if wind is not None and wind >= 25:
            total += 0.20
        elif wind is not None and wind >= 18:
            total += 0.10
        if precip is not None and precip >= 1.0:
            total -= 0.20
    return round(clamp(total, 6.5, 12.5), 1)

def elegir_total_pick(total_proyectado, total_line):
    if total_line is None:
        return None
    diff = total_proyectado - total_line
    if diff >= 0.45:
        return {"pick": f"Over {total_line}", "edge": round(diff, 2), "strength": "Alta"}
    if diff >= 0.15:
        return {"pick": f"Over {total_line}", "edge": round(diff, 2), "strength": "Media"}
    if diff <= -0.45:
        return {"pick": f"Under {total_line}", "edge": round(abs(diff), 2), "strength": "Alta"}
    if diff <= -0.15:
        return {"pick": f"Under {total_line}", "edge": round(abs(diff), 2), "strength": "Media"}
    return None

def elegir_total_pick_fallback(total_proyectado):
    if total_proyectado >= 9.2:
        return {"pick": "Over 8.5", "edge": round(total_proyectado - 8.5, 2), "strength": "Forzado"}
    elif total_proyectado <= 7.8:
        return {"pick": "Under 8.5", "edge": round(8.5 - total_proyectado, 2), "strength": "Forzado"}
    return None

def normalizar_nombre_equipo_odds(team_name):
    # Mapeo robusto para coincidir nombres entre MLB y Odds API
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
        "Oakland Athletics": "Oakland Athletics",
        "Philadelphia Phillies": "Philadelphia Phillies",
        "Pittsburgh Pirates": "Pittsburgh Pirates",
        "San Diego Padres": "San Diego Padres",
        "San Francisco Giants": "San Francisco Giants",
        "Seattle Mariners": "Seattle Mariners",
        "St. Louis Cardinals": "St Louis Cardinals",  # Sin punto
        "St Louis Cardinals": "St Louis Cardinals",
        "Tampa Bay Rays": "Tampa Bay Rays",
        "Texas Rangers": "Texas Rangers",
        "Toronto Blue Jays": "Toronto Blue Jays",
        "Washington Nationals": "Washington Nationals",
    }
    return mapping.get(team_name, team_name)

def obtener_mejor_cuota(away_team, home_team):
    if not ODDS_API_KEY:
        return None
    try:
        url = "https://api.the-odds-api.com/v4/sports/baseball_mlb/odds"
        params = {"apiKey": ODDS_API_KEY, "regions": "us", "markets": "h2h,totals", "oddsFormat": "american"}
        data = safe_get(url, params=params)
        if not isinstance(data, list):
            return None
        away_norm = normalizar_nombre_equipo_odds(away_team)
        home_norm = normalizar_nombre_equipo_odds(home_team)
        mejor = {"home_ml": None, "away_ml": None, "total_line": None, "over_price": None, "under_price": None}
        for event in data:
            home_name = event.get("home_team", "")
            teams = event.get("teams", [])
            away_name = teams[0] if len(teams) > 1 and teams[0] != home_name else teams[1] if len(teams) > 1 else ""
            # Comparación normalizada
            if normalizar_nombre_equipo_odds(home_name) == home_norm and normalizar_nombre_equipo_odds(away_name) == away_norm:
                for book in event.get("bookmakers", []):
                    for market in book.get("markets", []):
                        if market.get("key") == "h2h":
                            for outcome in market.get("outcomes", []):
                                if normalizar_nombre_equipo_odds(outcome.get("name", "")) == home_norm:
                                    price = outcome.get("price")
                                    if mejor["home_ml"] is None or price > mejor["home_ml"]:
                                        mejor["home_ml"] = price
                                elif normalizar_nombre_equipo_odds(outcome.get("name", "")) == away_norm:
                                    price = outcome.get("price")
                                    if mejor["away_ml"] is None or price > mejor["away_ml"]:
                                        mejor["away_ml"] = price
                        elif market.get("key") == "totals":
                            mejor["total_line"] = market.get("outcomes", [])[0].get("point") if market.get("outcomes") else None
                            for outcome in market.get("outcomes", []):
                                if outcome.get("name") == "Over":
                                    price = outcome.get("price")
                                    if mejor["over_price"] is None or price > mejor["over_price"]:
                                        mejor["over_price"] = price
                                elif outcome.get("name") == "Under":
                                    price = outcome.get("price")
                                    if mejor["under_price"] is None or price > mejor["under_price"]:
                                        mejor["under_price"] = price
                break
        return mejor if any([mejor["home_ml"], mejor["away_ml"]]) else None
    except Exception as e:
        logger.error(f"Error odds: {e}")
        return None

# =========================================================
# KELLY FRACTION
# =========================================================
def kelly_fraction(prob, american_odds, kelly_fraction=0.25):
    dec = american_to_decimal(american_odds)
    if dec is None or prob <= 0 or prob >= 1:
        return 0.0
    b = dec - 1
    p = prob
    q = 1 - p
    f = (p * b - q) / b
    if f < 0:
        return 0.0
    return f * kelly_fraction

def stake_sugerido_kelly(prob, cuota, bankroll=100, unidad_base=1.0):
    frac = kelly_fraction(prob, cuota)
    stake_units = frac * bankroll / unidad_base
    stake_units = round(stake_units * 4) / 4
    return f"{stake_units:.2f}u" if stake_units > 0 else "Pass"

# =========================================================
# FUNCIONES DE ANÁLISIS AVANZADO
# =========================================================
def generar_analisis_pick(game, pred, standings, away_stats, home_stats, weather, odds_info):
    """Genera un análisis detallado del pick"""
    away = standings.get(game["away"], {})
    home = standings.get(game["home"], {})
    
    analisis = []
    
    # 1. Análisis de forma reciente
    away_last10 = away.get("last10_win_pct", 0.5) * 100
    home_last10 = home.get("last10_win_pct", 0.5) * 100
    
    if home_last10 > away_last10 + 15:
        analisis.append(f"📈 {game['home']} viene mejor (últimos 10: {home_last10:.0f}% vs {away_last10:.0f}%)")
    elif away_last10 > home_last10 + 15:
        analisis.append(f"📈 {game['away']} viene mejor (últimos 10: {away_last10:.0f}% vs {home_last10:.0f}%)")
    
    # 2. Análisis de local/visitante
    home_home = home.get("home_win_pct", 0.5) * 100
    away_away = away.get("away_win_pct", 0.5) * 100
    
    if pred["favorite"] == game["home"] and home_home > 55:
        analisis.append(f"🏠 {game['home']} es fuerte en casa ({home_home:.0f}%)")
    elif pred["favorite"] == game["away"] and away_away > 55:
        analisis.append(f"🛣️ {game['away']} rinde bien fuera ({away_away:.0f}%)")
    
    # 3. Análisis de pitcheo
    if away_stats.get("era", 4.20) < 3.50:
        analisis.append(f"🎯 Pitcher visitante: ERA {away_stats['era']} (excelente)")
    if home_stats.get("era", 4.20) < 3.50:
        analisis.append(f"🎯 Pitcher local: ERA {home_stats['era']} (excelente)")
    
    # 4. Análisis de clima
    if weather and weather.get("temp_c"):
        temp = weather["temp_c"]
        if temp > 28:
            analisis.append(f"🌡️ Calor extremo ({temp}°C) - favorece bateo")
        elif temp < 10:
            analisis.append(f"❄️ Frío ({temp}°C) - favorece pitcheo")
    
    if weather and weather.get("wind_kmh", 0) > 25:
        analisis.append(f"💨 Viento fuerte ({weather['wind_kmh']} km/h) - puede afectar vuelos")
    
    # 5. Valor de la cuota
    if odds_info and odds_info.get("cuota"):
        cuota = odds_info["cuota"]
        if cuota > 150:
            analisis.append(f"💎 Alto valor: cuota {cuota} para equipo con {pred['confidence_pct']}% de probabilidad")
        elif cuota > 120:
            analisis.append(f"📈 Buen valor: cuota {cuota}")
    
    # 6. Edge del modelo
    if odds_info and odds_info.get("edge"):
        edge = odds_info["edge"]
        if edge > 8:
            analisis.append(f"🔥 Edge enorme: {edge}%")
        elif edge > 5:
            analisis.append(f"✅ Edge sólido: {edge}%")
    
    if not analisis:
        analisis.append("✅ Pick respaldado por el modelo")
    
    return analisis[:2]  # Máximo 2 razones para no saturar

# =========================================================
# ACTUALIZACIÓN AUTOMÁTICA DE RESULTADOS
# =========================================================
def actualizar_resultados_parleys():
    ayer = (date.today() - timedelta(days=1)).strftime("%Y-%m-%d")
    resultados = obtener_resultados_juegos_fecha(ayer)
    if not resultados:
        return
    parleys = cargar_parleys_diarios()
    modificado = False
    for p in parleys:
        if p.get("fecha") == ayer and p.get("estado") == "pendiente":
            legs = p.get("legs", [])
            todos_acertados = True
            for leg in legs:
                game = leg.get("game")
                pick = leg.get("pick")
                for res in resultados:
                    if res["game"] == game:
                        if pick == f"{res['winner']} ML":
                            leg["resultado"] = "win"
                        elif "Over" in pick or "Under" in pick:
                            leg["resultado"] = "unknown"
                        else:
                            leg["resultado"] = "lose"
                        if leg["resultado"] != "win":
                            todos_acertados = False
                        break
                else:
                    todos_acertados = False
            if todos_acertados:
                p["estado"] = "ganado"
            else:
                p["estado"] = "fallado"
            modificado = True
            for leg in legs:
                if leg.get("resultado") in ["win", "lose"]:
                    registrar_apuesta_en_csv(
                        fecha=ayer,
                        juego=leg["game"],
                        tipo="parley",
                        pick=leg["pick"],
                        cuota="N/A",
                        prob_modelo=leg.get("confidence", 0),
                        prob_impl="N/A",
                        edge=0,
                        stake="1u",
                        grade="",
                        resultado=leg["resultado"],
                        profit=0,
                        analisis=leg.get("analisis", "")
                    )
    if modificado:
        guardar_parleys_diarios(parleys)
        logger.info(f"Resultados actualizados para {ayer}")

# =========================================================
# GENERACIÓN DE DATASET PARA TIKTOK
# =========================================================
def generar_dataset_tiktok():
    model = MLBModel()
    standings = obtener_standings()
    games = obtener_juegos_del_dia()
    data = {
        "fecha": hoy_str(),
        "bot_version": BOT_VERSION,
        "juegos_del_dia": [],
        "pronosticos": [],
        "apuestas": {"moneyline_ev": [], "totales_ev": [], "modelo": []},
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
            away = teams.get("away", {}).get("team", {}).get("name")
            home = teams.get("home", {}).get("team", {}).get("name")
            if not away or not home:
                continue
            matchup_key = normalizar_matchup(away, home)
            away_p = teams.get("away", {}).get("probablePitcher", {}).get("fullName", "TBD")
            home_p = teams.get("home", {}).get("probablePitcher", {}).get("fullName", "TBD")
            away_pid = teams.get("away", {}).get("probablePitcher", {}).get("id")
            home_pid = teams.get("home", {}).get("probablePitcher", {}).get("id")
            away_stats = obtener_stats_pitcher_reales(away_pid)
            home_stats = obtener_stats_pitcher_reales(home_pid)
            weather = obtener_clima_partido(g)
            pred = model.obtener_pick(away, home, standings, away_p, home_p, away_stats, home_stats, weather)
            total_proj = estimar_total_juego(away, home, standings, away_p, home_p, away_stats, home_stats, weather)
            odds = obtener_mejor_cuota(away, home) if ODDS_API_KEY else None
            picks_modelo.append({"game": f"{away} @ {home}", "matchup_key": matchup_key, "pick": f"{pred['favorite']} ML", "confianza": pred["confidence_pct"], "total_proyectado": total_proj})
            if odds and not pred["avoid"]:
                cuota_ml = odds["home_ml"] if pred["favorite"] == home else odds["away_ml"]
                if cuota_ml:
                    ev = (pred["prob_favorite"] * (american_to_decimal(cuota_ml)-1)) - (1-pred["prob_favorite"])
                    if ev > 0.01:
                        stake = stake_sugerido_kelly(pred["prob_favorite"], cuota_ml)
                        picks_ml.append({"game": f"{away} @ {home}", "matchup_key": matchup_key, "pick": f"{pred['favorite']} ML", "stake": stake, "ev_pct": round(ev*100,2), "cuota": cuota_ml, "confianza": pred["confidence_pct"]})
            if odds and odds.get("total_line"):
                total_pick = elegir_total_pick(total_proj, odds["total_line"])
                if total_pick:
                    cuota_total = odds["over_price"] if "Over" in total_pick["pick"] else odds["under_price"]
                    if cuota_total:
                        prob_total_model = clamp(0.50 + (total_pick["edge"]*0.06), 0.51, 0.62)
                        ev_total = (prob_total_model * (american_to_decimal(cuota_total)-1)) - (1-prob_total_model)
                        if ev_total > 0.01:
                            stake_total = stake_sugerido_kelly(prob_total_model, cuota_total)
                            picks_totals.append({"game": f"{away} @ {home}", "matchup_key": matchup_key, "pick": total_pick["pick"], "stake": stake_total, "ev_pct": round(ev_total*100,2), "cuota": cuota_total, "projection": total_proj, "line": odds["total_line"]})
            if not pred["avoid"] and odds:
                cuota_ml = odds["home_ml"] if pred["favorite"] == home else odds["away_ml"]
                if cuota_ml:
                    candidatos_parley.append({"game": f"{away} @ {home}", "matchup_key": matchup_key, "pick": f"{pred['favorite']} ML", "confidence": pred["confidence_pct"], "cuota": cuota_ml})
            if odds and odds.get("total_line"):
                total_pick = elegir_total_pick(total_proj, odds["total_line"])
                if total_pick:
                    cuota_total = odds["over_price"] if "Over" in total_pick["pick"] else odds["under_price"]
                    if cuota_total:
                        candidatos_millonario.append({"tipo": "TOTAL", "game": f"{away} @ {home}", "matchup_key": matchup_key, "pick": total_pick["pick"], "confidence": pred["confidence_pct"], "cuota": cuota_total, "edge": total_pick["edge"]})
            if not pred["avoid"] and odds:
                cuota_ml = odds["home_ml"] if pred["favorite"] == home else odds["away_ml"]
                if cuota_ml:
                    candidatos_millonario.append({"tipo": "ML", "game": f"{away} @ {home}", "matchup_key": matchup_key, "pick": f"{pred['favorite']} ML", "confidence": pred["confidence_pct"], "cuota": cuota_ml})
        except Exception as e:
            logger.error(f"Error en dataset: {e}")
            continue
    picks_ml.sort(key=lambda x: x["ev_pct"], reverse=True)
    picks_totals.sort(key=lambda x: x["ev_pct"], reverse=True)
    picks_modelo.sort(key=lambda x: x["confianza"], reverse=True)
    data["apuestas"]["moneyline_ev"] = picks_ml[:5]
    data["apuestas"]["totales_ev"] = picks_totals[:5]
    data["pronosticos"] = picks_modelo[:8]
    data["parley"] = candidatos_parley[:3]
    data["parley_millonario"] = candidatos_millonario[:10]
    return data

def guardar_json_tiktok(data):
    carpeta = f"exports_tiktok/{hoy_str()}"
    os.makedirs(carpeta, exist_ok=True)
    ruta = os.path.join(carpeta, "mlb_contenido.json")
    guardar_json(ruta, data)
    return ruta

# =========================================================
# COMANDOS DEL BOT
# =========================================================
def menu_markup():
    markup = InlineKeyboardMarkup(row_width=2)
    buttons = [
        InlineKeyboardButton("📅 Hoy", callback_data="cmd_hoy"),
        InlineKeyboardButton("🏆 Posiciones", callback_data="cmd_posiciones"),
        InlineKeyboardButton("💰 Apuestas", callback_data="cmd_apuestas"),
        InlineKeyboardButton("🎯 Parley", callback_data="cmd_parley"),
        InlineKeyboardButton("💎 Parley Mill.", callback_data="cmd_parley_millonario"),
        InlineKeyboardButton("📊 Pronósticos", callback_data="cmd_pronosticos"),
        InlineKeyboardButton("🚨 Lesionados", callback_data="cmd_lesionados"),
        InlineKeyboardButton("📈 ROI", callback_data="cmd_roi"),
        InlineKeyboardButton("📦 Exportar JSON", callback_data="cmd_exportar_json"),
        InlineKeyboardButton("📊 Stats Parlays", callback_data="cmd_stats_parlays"),
        InlineKeyboardButton("🔍 Debug Odds", callback_data="cmd_debug_odds"),
        InlineKeyboardButton("✅ Parley G", callback_data="cmd_parley_ganado"),
        InlineKeyboardButton("❌ Parley F", callback_data="cmd_parley_fallado"),
        InlineKeyboardButton("💎✅ Mill G", callback_data="cmd_millonario_ganado"),
        InlineKeyboardButton("💎❌ Mill F", callback_data="cmd_millonario_fallado"),
        InlineKeyboardButton("♻️ Reset Parley", callback_data="cmd_reset_parley"),
        InlineKeyboardButton("♻️ Reset Mill", callback_data="cmd_reset_millonario")
    ]
    markup.add(*buttons)
    return markup

@bot.message_handler(commands=["start"])
def start(message):
    texto = (
        "⚾ <b>MLB PRO BOT V7.3</b>\n"
        "━━━━━━━━━━━━━━━━━━\n"
        "Predicciones profesionales con:\n"
        "• Modelo calibrado\n"
        "• Kelly fraction para stakes\n"
        "• Análisis detallado por pick\n"
        "• Actualización automática\n\n"
        f"🧪 Versión: <b>{BOT_VERSION}</b>\n"
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
                "away": away, "home": home, "score_away": sa, "score_home": sh,
                "status": status, "hora_txt": hora_txt, "hora_orden": hora_orden
            })
        juegos_ordenados.sort(key=lambda x: x["hora_orden"] if x["hora_orden"] else datetime.max)
        texto = header("JUEGOS DE HOY", "📅") + f"🗓️ {fecha} | Hora de Venezuela\n\n"
        for i, j in enumerate(juegos_ordenados, 1):
            texto += card_game(f"{i}. {j['away']} @ {j['home']}", [f"🕒 {j['hora_txt']} VET", f"📌 {j['status']}", f"⚾️ Score: {j['score_away']} - {j['score_home']}"])
        bot.delete_message(msg.chat.id, msg.message_id)
        responder_largo(message.chat.id, texto, parse_mode="HTML")
    except Exception as e:
        bot.edit_message_text(f"❌ Error al cargar juegos: {str(e)[:120]}", msg.chat.id, msg.message_id)

@bot.message_handler(commands=["posiciones"])
def posiciones(message):
    msg = bot.reply_to(message, "🏆 Cargando standings...")
    try:
        season = temporada_actual()
        url = f"{MLB_BASE}/standings"
        params = {"leagueId": "103,104", "season": season, "standingsTypes": "regularSeason"}
        data = safe_get(url, params=params)
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
            lineas = [f"{league_name} - {division_name}", "", "Team                 W   L   PCT   GB   HOME   AWAY   L10   STRK", "---------------------------------------------------------------"]
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
                fila = f"{nombre[:20].ljust(20)} {str(wins).rjust(3)} {str(losses).rjust(3)} {str(pct).rjust(5)} {gb.rjust(4)} {home.rjust(6)} {away.rjust(6)} {l10.rjust(5)} {strk.rjust(5)}"
                lineas.append(fila)
            bloques.append("<pre>" + "\n".join(lineas) + "</pre>")
        bot.delete_message(msg.chat.id, msg.message_id)
        bot.send_message(message.chat.id, titulo, parse_mode="HTML")
        for bloque in bloques:
            bot.send_message(message.chat.id, bloque, parse_mode="HTML")
    except Exception as e:
        bot.edit_message_text(f"❌ Error: {str(e)[:120]}", msg.chat.id, msg.message_id)

@bot.message_handler(commands=["apuestas"])
def apuestas(message):
    msg = bot.reply_to(message, "💰 Analizando apuestas con Kelly...")
    try:
        model = MLBModel()
        standings = obtener_standings()
        games = obtener_juegos_del_dia()
        texto = header("APUESTAS PRO MLB", "💰") + f"📅 {hoy_str()}\n\n"
        if not games:
            texto += "❌ No hay juegos programados hoy."
            bot.edit_message_text(texto, msg.chat.id, msg.message_id)
            return
        if not ODDS_API_KEY:
            texto += "⚠️ <b>API de Odds no configurada</b>. No se pueden calcular EV.\n\n"
        picks_ml = []
        picks_totals = []
        for g in games:
            try:
                teams = g.get("teams", {})
                away = teams.get("away", {}).get("team", {}).get("name")
                home = teams.get("home", {}).get("team", {}).get("name")
                if not away or not home:
                    continue
                away_p = teams.get("away", {}).get("probablePitcher", {}).get("fullName", "TBD")
                home_p = teams.get("home", {}).get("probablePitcher", {}).get("fullName", "TBD")
                away_pid = teams.get("away", {}).get("probablePitcher", {}).get("id")
                home_pid = teams.get("home", {}).get("probablePitcher", {}).get("id")
                away_stats = obtener_stats_pitcher_reales(away_pid)
                home_stats = obtener_stats_pitcher_reales(home_pid)
                weather = obtener_clima_partido(g)
                pred = model.obtener_pick(away, home, standings, away_p, home_p, away_stats, home_stats, weather)
                total_proj = estimar_total_juego(away, home, standings, away_p, home_p, away_stats, home_stats, weather)
                odds = obtener_mejor_cuota(away, home) if ODDS_API_KEY else None
                if odds and not pred["avoid"]:
                    cuota_ml = odds["home_ml"] if pred["favorite"] == home else odds["away_ml"]
                    if cuota_ml:
                        ev = (pred["prob_favorite"] * (american_to_decimal(cuota_ml)-1)) - (1-pred["prob_favorite"])
                        if ev > 0.01:
                            stake = stake_sugerido_kelly(pred["prob_favorite"], cuota_ml)
                            picks_ml.append({"game": f"{away} @ {home}", "pick": f"{pred['favorite']} ML", "cuota": cuota_ml, "ev": round(ev*100,2), "stake": stake, "prob_modelo": round(pred["prob_favorite"]*100,1)})
                if odds and odds.get("total_line"):
                    total_pick = elegir_total_pick(total_proj, odds["total_line"])
                    if total_pick:
                        cuota_total = odds["over_price"] if "Over" in total_pick["pick"] else odds["under_price"]
                        if cuota_total:
                            prob_total_model = clamp(0.50 + (total_pick["edge"]*0.06), 0.51, 0.62)
                            ev_total = (prob_total_model * (american_to_decimal(cuota_total)-1)) - (1-prob_total_model)
                            if ev_total > 0.01:
                                stake_total = stake_sugerido_kelly(prob_total_model, cuota_total)
                                picks_totals.append({"game": f"{away} @ {home}", "pick": total_pick["pick"], "cuota": cuota_total, "ev": round(ev_total*100,2), "stake": stake_total, "proj": total_proj, "line": odds["total_line"]})
            except Exception as e:
                logger.error(f"Error en /apuestas: {e}")
                continue
        picks_ml.sort(key=lambda x: x["ev"], reverse=True)
        picks_totals.sort(key=lambda x: x["ev"], reverse=True)
        if picks_ml:
            texto += "💰 <b>MONEYLINE CON EV+ (Kelly)</b>\n"
            for p in picks_ml[:5]:
                texto += card_game(p["game"], [f"🎯 {p['pick']} @ {p['cuota']}", f"📈 EV: {p['ev']}% | Stake: {p['stake']}", f"🧠 Prob modelo: {p['prob_modelo']}%"])
        else:
            texto += "💰 <b>MONEYLINE</b>\n⚠️ No se encontraron apuestas con EV positivo.\n\n"
        if picks_totals:
            texto += "📊 <b>TOTALES CON EV+</b>\n"
            for p in picks_totals[:5]:
                texto += card_game(p["game"], [f"🎯 {p['pick']} @ {p['cuota']}", f"📈 EV: {p['ev']}% | Stake: {p['stake']}", f"📊 Proyección: {p['proj']} vs Línea {p['line']}"])
        else:
            texto += "📊 <b>TOTALES</b>\n⚠️ No se encontraron totals con EV positivo.\n"
        bot.edit_message_text(texto, msg.chat.id, msg.message_id, parse_mode="HTML")
    except Exception as e:
        logger.exception("Error en /apuestas")
        bot.edit_message_text(f"❌ Error: {str(e)[:120]}", msg.chat.id, msg.message_id)

@bot.message_handler(commands=["parley"])
def parley(message):
    msg = bot.reply_to(message, "🎯 Generando parley del día (usando modelo predictivo)...")
    try:
        # Verificar temporada
        hoy_date = date.today()
        es_temporada = (hoy_date.month >= 3 and hoy_date.month <= 10)
        if not es_temporada:
            bot.edit_message_text("⚠️ <b>FUERA DE TEMPORADA MLB</b>\n\nActualmente no hay juegos de MLB programados.\nLa temporada regular va de marzo a octubre.", msg.chat.id, msg.message_id, parse_mode="HTML")
            return
        
        existente = buscar_parley_del_dia("parley")
        if existente:
            texto = header("PARLEY DEL DÍA (FIJO)", "🎯") + f"📅 {existente['fecha']}\n\n"
            for leg in existente.get("legs", []):
                analisis = leg.get("analisis", "Sin análisis")
                if isinstance(analisis, list):
                    analisis = " | ".join(analisis)
                texto += card_game(leg["game"], [f"🎯 Pick: <b>{leg['pick']}</b>", f"🧠 Confianza: {leg.get('confidence', 'N/D')}%", f"📊 {analisis}"])
            bot.edit_message_text(texto, msg.chat.id, msg.message_id, parse_mode="HTML")
            return

        model = MLBModel()
        standings = obtener_standings()
        games = obtener_juegos_del_dia()
        
        if not games:
            bot.edit_message_text("❌ No hay juegos programados para hoy.\n\nVerifica con /hoy para confirmar.", msg.chat.id, msg.message_id, parse_mode="HTML")
            return

        candidatos = []
        for g in games:
            try:
                teams = g.get("teams", {})
                away = teams.get("away", {}).get("team", {}).get("name")
                home = teams.get("home", {}).get("team", {}).get("name")
                if not away or not home:
                    continue
                
                away_pitcher_obj = teams.get("away", {}).get("probablePitcher", {}) or {}
                home_pitcher_obj = teams.get("home", {}).get("probablePitcher", {}) or {}
                away_p = away_pitcher_obj.get("fullName", "TBD")
                home_p = home_pitcher_obj.get("fullName", "TBD")
                away_pid = away_pitcher_obj.get("id")
                home_pid = home_pitcher_obj.get("id")
                
                away_stats = obtener_stats_pitcher_reales(away_pid)
                home_stats = obtener_stats_pitcher_reales(home_pid)
                weather = obtener_clima_partido(g)
                
                pred = model.obtener_pick(away, home, standings, away_p, home_p, away_stats, home_stats, weather)
                confianza = pred["confidence_pct"]
                tiene_pitcher_tbd = pred["avoid"]
                
                fav_team = pred["favorite"]
                fav_stats = standings.get(fav_team, {})
                last10_pct = fav_stats.get("last10_win_pct", 0.5) * 100
                
                # Intentar obtener odds (opcional)
                odds = None
                cuota = "N/D"
                edge = 0
                if ODDS_API_KEY:
                    try:
                        odds = obtener_mejor_cuota(away, home)
                        if odds:
                            cuota = odds.get("home_ml") if pred["favorite"] == home else odds.get("away_ml")
                            if cuota and isinstance(cuota, (int, float)):
                                prob_impl = moneyline_to_prob(cuota)
                                if prob_impl:
                                    edge = round((pred["prob_favorite"] - prob_impl) * 100, 1)
                    except Exception as e:
                        logger.warning(f"Error obteniendo odds para {away}@{home}: {e}")
                
                game_info = {"away": away, "home": home}
                analisis_lista = generar_analisis_pick(game_info, pred, standings, away_stats, home_stats, weather, {"cuota": cuota, "edge": edge} if cuota != "N/D" else None)
                if tiene_pitcher_tbd:
                    analisis_lista.append("⚠️ Pitcher no confirmado")
                
                # Calcular score basado en confianza y forma reciente
                score = confianza
                if last10_pct > 55:
                    score += 10
                elif last10_pct > 50:
                    score += 5
                if edge > 0:
                    score += edge * 1.5
                if tiene_pitcher_tbd:
                    score *= 0.85
                
                candidatos.append({
                    "game": f"{away} @ {home}",
                    "pick": f"{pred['favorite']} ML",
                    "confidence": confianza,
                    "cuota": cuota if cuota != "N/D" else "N/D",
                    "edge": edge,
                    "score": score,
                    "analisis": " | ".join(analisis_lista[:2]),
                    "tiene_tbd": tiene_pitcher_tbd,
                    "last10_pct": last10_pct
                })
            except Exception as e:
                logger.error(f"Error procesando juego en parley: {e}")
                continue
        
        if len(candidatos) < 2:
            texto = header("DIAGNÓSTICO - POCOS PICKS", "🔍") + f"📅 {hoy_str()}\n\nJuegos encontrados: {len(games)}\nCandidatos generados: {len(candidatos)}\n\n"
            if candidatos:
                texto += "Candidatos encontrados (pero insuficientes):\n"
                for c in candidatos:
                    texto += f"• {c['game']}: {c['pick']} (confianza {c['confidence']}%)\n"
            else:
                texto += "❌ <b>No se generó ningún candidato</b>\n\nPosibles causas:\n1. Los pitchers no están confirmados (TBD)\n2. La API de MLB puede no tener datos completos\n3. Los equipos pueden estar en descanso\n\n"
            texto += "\n💡 <b>Sugerencias:</b>\n• Usa /pronosticos para ver picks del modelo\n• Usa /debug_odds para verificar la API de Odds\n• Los pitchers suelen confirmarse horas antes del juego"
            bot.edit_message_text(texto, msg.chat.id, msg.message_id, parse_mode="HTML")
            return
        
        candidatos.sort(key=lambda x: x["score"], reverse=True)
        seleccionados = candidatos[:3]
        
        texto = header("PARLEY DEL DÍA MLB", "🎯") + f"📅 {hoy_str()}\n\n"
        if any(p.get('tiene_tbd') for p in seleccionados):
            texto += "⚠️ <i>Nota: Algunos picks tienen pitchers no confirmados (TBD). Las probabilidades pueden cambiar.</i>\n\n"
        if not ODDS_API_KEY:
            texto += "ℹ️ <i>API de Odds no configurada. Los picks se basan solo en el modelo predictivo.</i>\n\n"
        
        texto += "━━━━━━━━━━━━━━━━━━\n🎯 <b>PICKS SELECCIONADOS</b>\n━━━━━━━━━━━━━━━━━━\n\n"
        for i, p in enumerate(seleccionados, 1):
            texto += f"⚾ <b>{p['game']}</b>\n🎯 Pick {i}: <b>{p['pick']}</b>\n🧠 Confianza modelo: <b>{p['confidence']}%</b>\n"
            if p['cuota'] != "N/D":
                texto += f"💵 Cuota: <b>{p['cuota']}</b>\n"
                if p['edge'] > 0:
                    texto += f"📈 Edge: <b>+{p['edge']}%</b>\n"
            else:
                texto += f"💵 Cuota: <i>No disponible en este momento</i>\n"
            if p.get('last10_pct', 0) > 0:
                texto += f"📊 Forma últimos 10 juegos: <b>{p['last10_pct']:.0f}%</b>\n"
            texto += f"📊 Análisis: {p['analisis']}\n"
            if p.get('tiene_tbd'):
                texto += f"⚠️ <i>Pitcher no confirmado (TBD)</i>\n"
            texto += divider() + "\n"
        
        cuotas_validas = [p["cuota"] for p in seleccionados if p["cuota"] != "N/D" and isinstance(p["cuota"], (int, float))]
        if cuotas_validas:
            import math
            try:
                cuota_combinada = math.prod([(c/100 + 1) if c > 0 else (100/abs(c) + 1) for c in cuotas_validas])
                cuota_combinada = round(cuota_combinada * 100) / 100
                texto += f"\n💰 <b>CUOTA COMBINADA ESTIMADA: {cuota_combinada}</b>\n📊 Probabilidad implícita: {round(1/cuota_combinada*100, 1)}%\n"
            except:
                pass
        
        texto += "\n💡 <b>Recomendación:</b> Revisa los pitchers antes de apostar. Los marcadores pueden cambiar."
        legs = [{"game": p["game"], "pick": p["pick"], "confidence": p["confidence"], "analisis": p["analisis"]} for p in seleccionados]
        registrar_parley_del_dia("parley", legs)
        bot.edit_message_text(texto, msg.chat.id, msg.message_id, parse_mode="HTML")
    except Exception as e:
        logger.exception("Error en /parley")
        bot.edit_message_text(f"❌ Error crítico: {str(e)[:200]}", msg.chat.id, msg.message_id)

@bot.message_handler(commands=["parley_millonario"])
def parley_millonario(message):
    msg = bot.reply_to(message, "💎 Construyendo parley millonario...")
    try:
        hoy_date = date.today()
        es_temporada = (hoy_date.month >= 3 and hoy_date.month <= 10)
        if not es_temporada:
            bot.edit_message_text("⚠️ <b>FUERA DE TEMPORADA MLB</b>\n\nActualmente no hay juegos de MLB programados.\nLa temporada regular va de marzo a octubre.", msg.chat.id, msg.message_id, parse_mode="HTML")
            return
        
        existente = buscar_parley_del_dia("parley_millonario")
        if existente:
            texto = header("PARLEY MILLONARIO (FIJO)", "💎") + f"📅 {existente['fecha']}\n\n"
            for leg in existente.get("legs", []):
                analisis = leg.get("analisis", "Sin análisis")
                if isinstance(analisis, list):
                    analisis = " | ".join(analisis)
                texto += card_game(leg["game"], [f"🔥 Pick: <b>{leg['pick']}</b>", f"🧠 Confianza: {leg.get('confidence', 'N/D')}%", f"📊 {analisis}"])
            bot.edit_message_text(texto, msg.chat.id, msg.message_id, parse_mode="HTML")
            return
        
        parley_normal = buscar_parley_del_dia("parley")
        juegos_bloqueados = set()
        if parley_normal:
            for leg in parley_normal.get("legs", []):
                juegos_bloqueados.add(leg["game"])
        
        model = MLBModel()
        standings = obtener_standings()
        games = obtener_juegos_del_dia()
        
        if not games:
            bot.edit_message_text("❌ No hay juegos programados para hoy.", msg.chat.id, msg.message_id)
            return
        
        candidatos_ml = []
        candidatos_totals = []
        
        for g in games:
            try:
                teams = g.get("teams", {})
                away = teams.get("away", {}).get("team", {}).get("name")
                home = teams.get("home", {}).get("team", {}).get("name")
                if not away or not home:
                    continue
                
                game_str = f"{away} @ {home}"
                if game_str in juegos_bloqueados:
                    continue
                
                away_pitcher_obj = teams.get("away", {}).get("probablePitcher", {}) or {}
                home_pitcher_obj = teams.get("home", {}).get("probablePitcher", {}) or {}
                away_p = away_pitcher_obj.get("fullName", "TBD")
                home_p = home_pitcher_obj.get("fullName", "TBD")
                away_pid = away_pitcher_obj.get("id")
                home_pid = home_pitcher_obj.get("id")
                
                away_stats = obtener_stats_pitcher_reales(away_pid)
                home_stats = obtener_stats_pitcher_reales(home_pid)
                weather = obtener_clima_partido(g)
                
                pred = model.obtener_pick(away, home, standings, away_p, home_p, away_stats, home_stats, weather)
                total_proj = estimar_total_juego(away, home, standings, away_p, home_p, away_stats, home_stats, weather)
                confianza = pred["confidence_pct"]
                es_riesgoso = pred["avoid"]
                
                odds = None
                cuota_ml = "N/D"
                edge_ml = 0
                if ODDS_API_KEY:
                    try:
                        odds = obtener_mejor_cuota(away, home)
                        if odds:
                            cuota_ml = odds.get("home_ml") if pred["favorite"] == home else odds.get("away_ml")
                            if cuota_ml and isinstance(cuota_ml, (int, float)):
                                prob_impl = moneyline_to_prob(cuota_ml)
                                if prob_impl:
                                    edge_ml = round((pred["prob_favorite"] - prob_impl) * 100, 1)
                    except Exception as e:
                        logger.warning(f"Error odds para {away}@{home}: {e}")
                
                game_info = {"away": away, "home": home}
                analisis_lista = generar_analisis_pick(game_info, pred, standings, away_stats, home_stats, weather, {"cuota": cuota_ml, "edge": edge_ml} if cuota_ml != "N/D" else None)
                if es_riesgoso:
                    analisis_lista.append("⚠️ Pitcher no confirmado")
                
                score_ml = confianza * 0.4 + max(edge_ml, 0) * 2
                if not es_riesgoso and confianza >= 50:
                    score_ml += 10
                if confianza >= 45:
                    candidatos_ml.append({
                        "tipo": "ML",
                        "game": game_str,
                        "pick": f"{pred['favorite']} ML",
                        "confidence": confianza,
                        "cuota": cuota_ml,
                        "edge": edge_ml,
                        "score": score_ml,
                        "analisis": " | ".join(analisis_lista[:2]),
                        "riesgoso": es_riesgoso
                    })
                
                # Totales
                total_pick = None
                if odds and odds.get("total_line"):
                    total_pick = elegir_total_pick(total_proj, odds["total_line"])
                if not total_pick:
                    total_pick = elegir_total_pick_fallback(total_proj)
                
                if total_pick:
                    cuota_total = "N/D"
                    if odds:
                        cuota_total = odds.get("over_price") if "Over" in total_pick["pick"] else odds.get("under_price")
                        if cuota_total is None:
                            cuota_total = "N/D"
                    analisis_total = f"📊 Proyección {total_proj} | Edge {total_pick['edge']}"
                    score_total = total_pick["edge"] * 5 + confianza * 0.2
                    candidatos_totals.append({
                        "tipo": "TOTAL",
                        "game": game_str,
                        "pick": total_pick["pick"],
                        "confidence": confianza,
                        "cuota": cuota_total,
                        "edge": total_pick["edge"],
                        "score": score_total,
                        "analisis": analisis_total,
                        "proj": total_proj,
                        "line": odds.get("total_line", 8.5) if odds else 8.5
                    })
            except Exception as e:
                logger.error(f"Error en parley millonario: {e}")
                continue
        
        if not candidatos_ml and not candidatos_totals:
            bot.edit_message_text("❌ No se encontraron picks para el parley millonario.\n\nPosibles causas:\n• No hay juegos hoy\n• Los pitchers no están confirmados\n• La API de Odds no tiene datos\n\nUsa /pronosticos para ver picks del modelo.", msg.chat.id, msg.message_id, parse_mode="HTML")
            return
        
        candidatos_ml.sort(key=lambda x: x["score"], reverse=True)
        candidatos_totals.sort(key=lambda x: x["score"], reverse=True)
        
        seleccionados = []
        for c in candidatos_totals[:3]:
            if len(seleccionados) >= 5:
                break
            if c not in seleccionados:
                seleccionados.append(c)
        for c in candidatos_ml[:3]:
            if len(seleccionados) >= 5:
                break
            if c not in seleccionados:
                seleccionados.append(c)
        if len(seleccionados) < 5:
            resto = [c for c in candidatos_ml + candidatos_totals if c not in seleccionados]
            resto.sort(key=lambda x: x["score"], reverse=True)
            for c in resto:
                if len(seleccionados) >= 5:
                    break
                seleccionados.append(c)
        
        texto = header("PARLEY MILLONARIO", "💎") + f"📅 {hoy_str()}\n\n"
        if any(p.get('riesgoso', False) for p in seleccionados):
            texto += "⚠️ <i>Algunos picks tienen pitchers no confirmados (TBD)</i>\n\n"
        if not ODDS_API_KEY:
            texto += "ℹ️ <i>API de Odds no configurada. Los picks se basan solo en el modelo.</i>\n\n"
        
        texto += "━━━━━━━━━━━━━━━━━━\n🎯 <b>5 PICKS SELECCIONADOS</b>\n━━━━━━━━━━━━━━━━━━\n\n"
        for i, p in enumerate(seleccionados[:5], 1):
            texto += f"⚾ <b>{p['game']}</b>\n🔥 Pick {i}: <b>{p['tipo']} - {p['pick']}</b>\n🧠 Confianza: <b>{p['confidence']}%</b>\n"
            if p['cuota'] != "N/D":
                texto += f"💵 Cuota: <b>{p['cuota']}</b>\n"
            if p.get('edge', 0) > 0:
                texto += f"📈 Edge: <b>+{p['edge']}</b>\n"
            if p.get('proj'):
                texto += f"📊 Proyección: {p['proj']} vs línea {p.get('line', 'N/D')}\n"
            texto += f"📊 {p['analisis']}\n"
            if p.get('riesgoso'):
                texto += f"⚠️ <i>Pitcher no confirmado</i>\n"
            texto += divider() + "\n"
        
        cuotas_validas = [p["cuota"] for p in seleccionados[:5] if p["cuota"] != "N/D" and isinstance(p["cuota"], (int, float))]
        if cuotas_validas:
            import math
            try:
                cuota_combinada = math.prod([(c/100 + 1) if c > 0 else (100/abs(c) + 1) for c in cuotas_validas])
                cuota_combinada = round(cuota_combinada * 100) / 100
                texto += f"\n💰 <b>CUOTA COMBINADA: {cuota_combinada}</b>\n"
            except:
                pass
        
        legs = [{"game": p["game"], "pick": p["pick"], "confidence": p["confidence"], "analisis": p["analisis"]} for p in seleccionados[:5]]
        registrar_parley_del_dia("parley_millonario", legs)
        bot.edit_message_text(texto, msg.chat.id, msg.message_id, parse_mode="HTML")
    except Exception as e:
        logger.exception("Error en /parley_millonario")
        bot.edit_message_text(f"❌ Error: {str(e)[:200]}", msg.chat.id, msg.message_id)

@bot.message_handler(commands=["pronosticos"])
def pronosticos(message):
    msg = bot.reply_to(message, "📊 Generando pronósticos...")
    try:
        model = MLBModel()
        standings = obtener_standings()
        games = obtener_juegos_del_dia()
        texto = header("PRONÓSTICOS DEL MODELO", "📊") + f"📅 {hoy_str()}\n\n"
        if not games:
            texto += "No hay juegos hoy."
            bot.edit_message_text(texto, msg.chat.id, msg.message_id)
            return
        picks = []
        for g in games:
            teams = g.get("teams", {})
            away = teams.get("away", {}).get("team", {}).get("name")
            home = teams.get("home", {}).get("team", {}).get("name")
            if not away or not home:
                continue
            away_p = teams.get("away", {}).get("probablePitcher", {}).get("fullName", "TBD")
            home_p = teams.get("home", {}).get("probablePitcher", {}).get("fullName", "TBD")
            away_pid = teams.get("away", {}).get("probablePitcher", {}).get("id")
            home_pid = teams.get("home", {}).get("probablePitcher", {}).get("id")
            away_stats = obtener_stats_pitcher_reales(away_pid)
            home_stats = obtener_stats_pitcher_reales(home_pid)
            weather = obtener_clima_partido(g)
            pred = model.obtener_pick(away, home, standings, away_p, home_p, away_stats, home_stats, weather)
            picks.append({"game": f"{away} @ {home}", "pick": f"{pred['favorite']} ML", "conf": pred["confidence_pct"]})
        picks.sort(key=lambda x: x["conf"], reverse=True)
        for p in picks[:8]:
            texto += card_game(p["game"], [f"🎯 Pick: <b>{p['pick']}</b>", f"🧠 Confianza: <b>{p['conf']}%</b>"])
        bot.edit_message_text(texto, msg.chat.id, msg.message_id, parse_mode="HTML")
    except Exception as e:
        bot.edit_message_text(f"❌ Error: {str(e)[:120]}", msg.chat.id, msg.message_id)

@bot.message_handler(commands=["lesionados"])
def lesionados(message):
    msg = bot.reply_to(message, "🚨 Cargando lesionados...")
    try:
        url = f"{MLB_BASE}/transactions"
        params = {"startDate": f"{temporada_actual()}-03-01", "endDate": hoy_str(), "sportId": 1}
        data = safe_get(url, params=params)
        transactions = data.get("transactions", [])
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
        bot.edit_message_text(f"❌ Error: {str(e)[:120]}", msg.chat.id, msg.message_id)

@bot.message_handler(commands=["debug_odds"])
def debug_odds(message):
    """Comando de depuración para ver qué equipos devuelve la API de Odds"""
    msg = bot.reply_to(message, "🔍 Depurando API de Odds...")
    try:
        if not ODDS_API_KEY:
            bot.edit_message_text("❌ API de Odds no configurada.", msg.chat.id, msg.message_id)
            return
        
        url = "https://api.the-odds-api.com/v4/sports/baseball_mlb/odds"
        params = {"apiKey": ODDS_API_KEY, "regions": "us", "markets": "h2h", "oddsFormat": "american"}
        data = safe_get(url, params=params)
        
        if not isinstance(data, list) or not data:
            bot.edit_message_text("❌ No se obtuvieron datos de la API de Odds.", msg.chat.id, msg.message_id)
            return
        
        texto = header("DEBUG API ODDS", "🔍")
        texto += f"Total de eventos: {len(data)}\n\n"
        texto += "📋 <b>Equipos disponibles en Odds API (primeros 10):</b>\n"
        
        equipos_odds = set()
        for event in data[:20]:
            home = event.get("home_team", "")
            teams = event.get("teams", [])
            for t in teams:
                equipos_odds.add(t)
            texto += f"• {home} vs {teams[0] if teams else '?'}\n"
        
        texto += f"\n📊 <b>Total equipos únicos: {len(equipos_odds)}</b>\n\n"
        
        games = obtener_juegos_del_dia()
        texto += "🏆 <b>Equipos MLB de hoy (primeros 10):</b>\n"
        for g in games[:10]:
            teams = g.get("teams", {})
            away = teams.get("away", {}).get("team", {}).get("name", "?")
            home = teams.get("home", {}).get("team", {}).get("name", "?")
            away_norm = normalizar_nombre_equipo_odds(away)
            home_norm = normalizar_nombre_equipo_odds(home)
            texto += f"• {away} @ {home}\n"
            if away_norm in equipos_odds:
                texto += f"  ✅ {away} ENCONTRADO en Odds\n"
            else:
                texto += f"  ❌ {away} NO encontrado (normalizado: {away_norm})\n"
            if home_norm in equipos_odds:
                texto += f"  ✅ {home} ENCONTRADO en Odds\n"
            else:
                texto += f"  ❌ {home} NO encontrado (normalizado: {home_norm})\n"
        
        bot.edit_message_text(texto, msg.chat.id, msg.message_id, parse_mode="HTML")
    except Exception as e:
        bot.edit_message_text(f"❌ Error: {str(e)[:200]}", msg.chat.id, msg.message_id)

@bot.message_handler(commands=["reset_parley"])
def reset_parley(message):
    ok = eliminar_parley_del_dia("parley")
    bot.reply_to(message, "♻️ Parley del día reiniciado." if ok else "No había parley activo.")

@bot.message_handler(commands=["reset_millonario"])
def reset_millonario(message):
    ok = eliminar_parley_del_dia("parley_millonario")
    bot.reply_to(message, "♻️ Parley millonario reiniciado." if ok else "No había parley millonario.")

@bot.message_handler(commands=["parley_ganado"])
def parley_ganado(message):
    ok = actualizar_estado_parley(hoy_str(), "parley", "ganado")
    bot.reply_to(message, "✅ Parley marcado como GANADO." if ok else "No se encontró parley del día.")

@bot.message_handler(commands=["parley_fallado"])
def parley_fallado(message):
    ok = actualizar_estado_parley(hoy_str(), "parley", "fallado")
    bot.reply_to(message, "❌ Parley marcado como FALLADO." if ok else "No se encontró parley del día.")

@bot.message_handler(commands=["millonario_ganado"])
def millonario_ganado(message):
    ok = actualizar_estado_parley(hoy_str(), "parley_millonario", "ganado")
    bot.reply_to(message, "✅ Parley millonario marcado como GANADO." if ok else "No se encontró.")

@bot.message_handler(commands=["millonario_fallado"])
def millonario_fallado(message):
    ok = actualizar_estado_parley(hoy_str(), "parley_millonario", "fallado")
    bot.reply_to(message, "❌ Parley millonario marcado como FALLADO." if ok else "No se encontró.")

@bot.message_handler(commands=["stats_parlays"])
def stats_parlays(message):
    parleys = cargar_parleys_diarios()
    stats = {"parley": {"ganado":0, "fallado":0}, "parley_millonario": {"ganado":0, "fallado":0}}
    for p in parleys:
        tipo = p.get("tipo")
        estado = p.get("estado")
        if tipo in stats and estado in ["ganado","fallado"]:
            stats[tipo][estado] += 1
    total_p = stats["parley"]["ganado"]+stats["parley"]["fallado"]
    total_m = stats["parley_millonario"]["ganado"]+stats["parley_millonario"]["fallado"]
    eff_p = round(stats["parley"]["ganado"]/total_p*100,2) if total_p>0 else 0
    eff_m = round(stats["parley_millonario"]["ganado"]/total_m*100,2) if total_m>0 else 0
    texto = header("ESTADÍSTICAS PARLEYS", "📊")
    texto += f"🎯 Parley diario: {stats['parley']['ganado']}G / {stats['parley']['fallado']}F | Efectividad: {eff_p}%\n"
    texto += f"💎 Parley millonario: {stats['parley_millonario']['ganado']}G / {stats['parley_millonario']['fallado']}F | Efectividad: {eff_m}%"
    bot.reply_to(message, texto, parse_mode="HTML")

@bot.message_handler(commands=["roi"])
def roi(message):
    if not os.path.exists(RESULTADOS_CSV):
        bot.reply_to(message, "No hay datos de resultados aún.")
        return
    total_apuestas = 0
    total_unidades = 0.0
    total_profit = 0.0
    ganadas = 0
    with open(RESULTADOS_CSV, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            resultado = row.get("resultado", "").lower()
            profit = safe_float(row.get("profit"))
            stake = extraer_unidades(row.get("stake", "0"))
            if resultado in ["win", "lose"] and profit is not None:
                total_apuestas += 1
                total_unidades += stake
                total_profit += profit
                if resultado == "win":
                    ganadas += 1
    if total_apuestas == 0:
        bot.reply_to(message, "No hay apuestas cerradas.")
        return
    roi_pct = (total_profit / total_unidades) * 100 if total_unidades > 0 else 0
    hit_rate = (ganadas / total_apuestas) * 100
    texto = header("ROI ACUMULADO", "📈")
    texto += f"Apuestas: {total_apuestas}\nGanadas: {ganadas} ({hit_rate:.1f}%)\nUnidades apostadas: {total_unidades:.2f}u\nProfit neto: {total_profit:.2f}u\nROI: {roi_pct:.2f}%"
    bot.reply_to(message, texto, parse_mode="HTML")

@bot.message_handler(commands=["exportar_json"])
def exportar_json(message):
    msg = bot.reply_to(message, "📦 Generando JSON maestro...")
    try:
        data = generar_dataset_tiktok()
        ruta = guardar_json_tiktok(data)
        bot.edit_message_text(f"✅ JSON guardado en: {ruta}", msg.chat.id, msg.message_id)
    except Exception as e:
        bot.edit_message_text(f"❌ Error: {e}", msg.chat.id, msg.message_id)

# =========================================================
# CALLBACKS
# =========================================================
@bot.callback_query_handler(func=lambda call: call.data.startswith("cmd_"))
def callback_menu(call):
    cmd = call.data.replace("cmd_", "")
    try:
        if cmd == "hoy":
            hoy(call.message)
        elif cmd == "posiciones":
            posiciones(call.message)
        elif cmd == "apuestas":
            apuestas(call.message)
        elif cmd == "parley":
            parley(call.message)
        elif cmd == "parley_millonario":
            parley_millonario(call.message)
        elif cmd == "pronosticos":
            pronosticos(call.message)
        elif cmd == "lesionados":
            lesionados(call.message)
        elif cmd == "roi":
            roi(call.message)
        elif cmd == "exportar_json":
            exportar_json(call.message)
        elif cmd == "stats_parlays":
            stats_parlays(call.message)
        elif cmd == "debug_odds":
            debug_odds(call.message)
        elif cmd == "parley_ganado":
            parley_ganado(call.message)
        elif cmd == "parley_fallado":
            parley_fallado(call.message)
        elif cmd == "millonario_ganado":
            millonario_ganado(call.message)
        elif cmd == "millonario_fallado":
            millonario_fallado(call.message)
        elif cmd == "reset_parley":
            reset_parley(call.message)
        elif cmd == "reset_millonario":
            reset_millonario(call.message)
        bot.answer_callback_query(call.id)
    except Exception as e:
        logger.error(f"Error en callback {cmd}: {e}")
        bot.answer_callback_query(call.id, "Error al procesar", show_alert=True)

# =========================================================
# MAIN
# =========================================================
if __name__ == "__main__":
    logger.info(f"Iniciando bot {BOT_VERSION}")
    inicializar_csv_resultados()
    
    def auto_update():
        while True:
            time.sleep(3600)
            actualizar_resultados_parleys()
    
    threading.Thread(target=auto_update, daemon=True).start()
    bot.remove_webhook()
    time.sleep(1)
    bot.infinity_polling(skip_pending=True, timeout=30)