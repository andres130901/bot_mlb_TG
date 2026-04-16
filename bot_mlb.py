import os
import csv
import json
import math
import logging
import time
import threading
from datetime import datetime, date, timedelta
from functools import lru_cache

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
BOT_VERSION = "V7_4_FINAL"

# =========================================================
# CONFIG
# =========================================================
load_dotenv()
TOKEN = os.getenv("TOKEN")
ODDS_API_KEY = os.getenv("ODDS_API_KEY", "").strip()

if not TOKEN:
    raise ValueError("Falta TOKEN en .env")

bot = telebot.TeleBot(TOKEN, parse_mode="HTML")
bot.remove_webhook()  # Asegurar que no hay webhook activo

MLB_BASE = "https://statsapi.mlb.com/api/v1"
PARLEYS_DIARIOS_FILE = "parleys_diarios.json"
RESULTADOS_CSV = "resultados_apuestas.csv"
REQUEST_TIMEOUT = 20
MAX_RETRIES = 3

# =========================================================
# UTILIDADES BÁSICAS
# =========================================================
def hoy_str():
    return date.today().strftime("%Y-%m-%d")

def safe_get(url, params=None, timeout=REQUEST_TIMEOUT):
    for attempt in range(MAX_RETRIES):
        try:
            r = requests.get(url, params=params, timeout=timeout)
            r.raise_for_status()
            return r.json()
        except Exception as e:
            logger.warning(f"Intento {attempt+1} fallido: {e}")
            time.sleep(2 ** attempt)
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

def clamp(value, low, high):
    return max(low, min(high, value))

def logistic(x):
    return 1 / (1 + math.exp(-x))

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
    botones = [
        InlineKeyboardButton("📅 Hoy", callback_data="hoy"),
        InlineKeyboardButton("🏆 Posiciones", callback_data="posiciones"),
        InlineKeyboardButton("💰 Apuestas", callback_data="apuestas"),
        InlineKeyboardButton("🎯 Parley", callback_data="parley"),
        InlineKeyboardButton("💎 Parley Mill.", callback_data="parley_millonario"),
        InlineKeyboardButton("📊 Pronósticos", callback_data="pronosticos"),
        InlineKeyboardButton("🚨 Lesionados", callback_data="lesionados"),
        InlineKeyboardButton("📈 ROI", callback_data="roi"),
        InlineKeyboardButton("📦 Exportar JSON", callback_data="exportar_json"),
        InlineKeyboardButton("📊 Stats Parlays", callback_data="stats_parlays"),
        InlineKeyboardButton("🔍 Debug Odds", callback_data="debug_odds"),
        InlineKeyboardButton("✅ Parley G", callback_data="parley_ganado"),
        InlineKeyboardButton("❌ Parley F", callback_data="parley_fallado"),
        InlineKeyboardButton("💎✅ Mill G", callback_data="millonario_ganado"),
        InlineKeyboardButton("💎❌ Mill F", callback_data="millonario_fallado"),
        InlineKeyboardButton("♻️ Reset Parley", callback_data="reset_parley"),
        InlineKeyboardButton("♻️ Reset Mill", callback_data="reset_millonario")
    ]
    markup.add(*botones)
    return markup

# =========================================================
# PERSISTENCIA
# =========================================================
def cargar_parleys_diarios():
    if not os.path.exists(PARLEYS_DIARIOS_FILE):
        return []
    try:
        with open(PARLEYS_DIARIOS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            return data if isinstance(data, list) else []
    except Exception as e:
        logger.error(f"Error cargando parleys: {e}")
        return []

def guardar_parleys_diarios(data):
    try:
        with open(PARLEYS_DIARIOS_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.error(f"Error guardando parleys: {e}")

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

def inicializar_csv_resultados():
    if not os.path.exists(RESULTADOS_CSV):
        with open(RESULTADOS_CSV, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(["fecha", "juego", "tipo_apuesta", "pick", "cuota", "prob_modelo", "prob_implicita", "edge", "stake", "resultado", "profit"])

def registrar_apuesta_csv(fecha, juego, tipo, pick, cuota, prob_modelo, prob_impl, edge, stake, resultado, profit):
    with open(RESULTADOS_CSV, "a", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow([fecha, juego, tipo, pick, cuota, prob_modelo, prob_impl, edge, stake, resultado, profit])

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

@lru_cache(maxsize=256)
def obtener_stats_pitcher(person_id):
    base = {"era": 4.20, "whip": 1.30, "so9": 8.2, "ip": 0.0, "sample_ok": False}
    if not person_id:
        return base
    url = f"{MLB_BASE}/people/{person_id}/stats"
    params = {"stats": "season", "group": "pitching", "season": temporada_actual(), "gameType": "R"}
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

def obtener_clima(game):
    try:
        venue_id = game.get("venue", {}).get("id")
        if not venue_id:
            return {"temp_c": None, "wind_kmh": None}
        url_venue = f"{MLB_BASE}/venues"
        data_venue = safe_get(url_venue, params={"venueIds": str(venue_id)})
        venues = data_venue.get("venues", [])
        if not venues:
            return {"temp_c": None, "wind_kmh": None}
        venue = venues[0]
        location = venue.get("location", {})
        coords = location.get("defaultCoordinates", {})
        lat = coords.get("latitude")
        lon = coords.get("longitude")
        if lat is None or lon is None:
            return {"temp_c": None, "wind_kmh": None}
        game_date = game.get("gameDate")
        if not game_date:
            return {"temp_c": None, "wind_kmh": None}
        dt_utc = datetime.fromisoformat(game_date.replace("Z", "+00:00"))
        url_weather = "https://api.open-meteo.com/v1/forecast"
        params = {"latitude": lat, "longitude": lon, "hourly": "temperature_2m,wind_speed_10m", "timezone": "auto", "forecast_days": 2}
        weather_data = safe_get(url_weather, params=params)
        hourly = weather_data.get("hourly", {})
        times = hourly.get("time", [])
        if not times:
            return {"temp_c": None, "wind_kmh": None}
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
        return {"temp_c": safe_float(temp), "wind_kmh": safe_float(wind)}
    except Exception as e:
        logger.error(f"Error clima: {e}")
        return {"temp_c": None, "wind_kmh": None}

# =========================================================
# MODELO DE PREDICCIÓN
# =========================================================
class MLBModel:
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

    def obtener_pick(self, away_team, home_team, standings, away_pitcher="TBD", home_pitcher="TBD", away_stats=None, home_stats=None, weather=None):
        away = standings.get(away_team)
        home = standings.get(home_team)
        if not away or not home:
            return {"favorite": home_team, "prob_favorite": 0.50, "confidence_pct": 50.0, "avoid": True}
        
        diff_win_pct = home["win_pct"] - away["win_pct"]
        diff_split = home["home_win_pct"] - away["away_win_pct"]
        diff_last10 = home["last10_win_pct"] - away["last10_win_pct"]
        diff_run_diff = (home["run_diff"] - away["run_diff"]) / 100.0
        diff_streak = parse_streak(home["streak"]) - parse_streak(away["streak"])
        
        if away_stats is None:
            away_stats = {"era": 4.20, "whip": 1.30, "so9": 8.2, "ip": 0.0, "sample_ok": False}
        if home_stats is None:
            home_stats = {"era": 4.20, "whip": 1.30, "so9": 8.2, "ip": 0.0, "sample_ok": False}
        
        p_home = self.score_pitcher(home_stats)
        p_away = self.score_pitcher(away_stats)
        diff_pitcher = p_home - p_away
        
        score = 0.0
        score += diff_win_pct * 2.8
        score += diff_split * 1.9
        score += diff_last10 * 1.2
        score += diff_run_diff * 1.6
        score += diff_streak * 1.0
        score += diff_pitcher * 1.8
        score += 0.09
        
        prob_home = logistic(score)
        prob_home = clamp(prob_home, 0.32, 0.68)
        
        favorito = home_team if prob_home >= 0.5 else away_team
        prob_fav = prob_home if favorito == home_team else (1 - prob_home)
        avoid = away_pitcher == "TBD" or home_pitcher == "TBD"
        
        return {
            "favorite": favorito,
            "prob_favorite": prob_fav,
            "confidence_pct": round(prob_fav * 100, 1),
            "confidence_label": confidence_label(prob_fav),
            "avoid": avoid
        }

# =========================================================
# ODDS API
# =========================================================
def normalizar_equipo(nombre):
    mapping = {
        "St. Louis Cardinals": "St Louis Cardinals",
        "St Louis Cardinals": "St Louis Cardinals",
        "Athletics": "Oakland Athletics",
        "Oakland Athletics": "Oakland Athletics",
        "San Francisco Giants": "San Francisco Giants",
        "Los Angeles Dodgers": "Los Angeles Dodgers",
        "New York Yankees": "New York Yankees",
        "Boston Red Sox": "Boston Red Sox",
    }
    return mapping.get(nombre, nombre)

def obtener_odds(away_team, home_team):
    if not ODDS_API_KEY:
        return None
    try:
        url = "https://api.the-odds-api.com/v4/sports/baseball_mlb/odds"
        params = {"apiKey": ODDS_API_KEY, "regions": "us", "markets": "h2h", "oddsFormat": "american"}
        data = safe_get(url, params=params)
        if not isinstance(data, list):
            return None
        
        away_norm = normalizar_equipo(away_team)
        home_norm = normalizar_equipo(home_team)
        
        for event in data:
            home_name = event.get("home_team", "")
            teams = event.get("teams", [])
            away_name = teams[0] if len(teams) > 1 and teams[0] != home_name else teams[1] if len(teams) > 1 else ""
            
            if normalizar_equipo(home_name) == home_norm and normalizar_equipo(away_name) == away_norm:
                for book in event.get("bookmakers", []):
                    for market in book.get("markets", []):
                        if market.get("key") == "h2h":
                            for outcome in market.get("outcomes", []):
                                if normalizar_equipo(outcome.get("name", "")) == home_norm:
                                    return {"home_ml": outcome.get("price"), "away_ml": None}
                                elif normalizar_equipo(outcome.get("name", "")) == away_norm:
                                    return {"home_ml": None, "away_ml": outcome.get("price")}
                break
        return None
    except Exception as e:
        logger.error(f"Error odds: {e}")
        return None

def moneyline_to_prob(moneyline):
    try:
        ml = int(moneyline)
        if ml > 0:
            return 100 / (ml + 100)
        return abs(ml) / (abs(ml) + 100)
    except Exception:
        return None

def american_to_decimal(odds):
    try:
        odds = float(odds)
        if odds > 0:
            return 1 + (odds / 100)
        return 1 + (100 / abs(odds))
    except Exception:
        return None

def calcular_ev(prob, odds):
    dec = american_to_decimal(odds)
    if dec is None:
        return None
    return (prob * (dec - 1)) - (1 - prob)

# =========================================================
# COMANDOS DEL BOT
# =========================================================
@bot.message_handler(commands=["start"])
def start(message):
    bot.send_message(
        message.chat.id,
        f"⚾ <b>MLB PRO BOT {BOT_VERSION}</b>\n━━━━━━━━━━━━━━━━━━\n\n"
        "Predicciones profesionales MLB\n\n"
        "Selecciona una opción:",
        reply_markup=menu_markup()
    )

@bot.message_handler(commands=["hoy"])
def hoy(message):
    games = obtener_juegos_del_dia()
    if not games:
        bot.reply_to(message, "📅 No hay juegos programados para hoy.")
        return
    
    texto = header("JUEGOS DE HOY", "📅") + f"📅 {hoy_str()}\n\n"
    for g in games[:10]:
        teams = g.get("teams", {})
        away = teams.get("away", {}).get("team", {}).get("name", "?")
        home = teams.get("home", {}).get("team", {}).get("name", "?")
        texto += f"• {away} @ {home}\n"
    bot.reply_to(message, texto)

@bot.message_handler(commands=["posiciones"])
def posiciones(message):
    standings = obtener_standings()
    if not standings:
        bot.reply_to(message, "No se pudieron cargar las posiciones.")
        return
    
    texto = header("POSICIONES MLB", "🏆")
    for i, (team, stats) in enumerate(list(standings.items())[:15], 1):
        texto += f"{i}. {team}: {stats['wins']}-{stats['losses']} ({stats['win_pct']*100:.1f}%)\n"
    bot.reply_to(message, texto)

@bot.message_handler(commands=["apuestas"])
def apuestas(message):
    if not ODDS_API_KEY:
        bot.reply_to(message, "⚠️ API de Odds no configurada. Agrega ODDS_API_KEY en .env")
        return
    
    games = obtener_juegos_del_dia()
    if not games:
        bot.reply_to(message, "No hay juegos hoy.")
        return
    
    model = MLBModel()
    standings = obtener_standings()
    texto = header("APUESTAS CON EV+", "💰") + f"📅 {hoy_str()}\n\n"
    encontrados = False
    
    for g in games[:10]:
        teams = g.get("teams", {})
        away = teams.get("away", {}).get("team", {}).get("name")
        home = teams.get("home", {}).get("team", {}).get("name")
        if not away or not home:
            continue
        
        away_p = teams.get("away", {}).get("probablePitcher", {}).get("fullName", "TBD")
        home_p = teams.get("home", {}).get("probablePitcher", {}).get("fullName", "TBD")
        away_stats = obtener_stats_pitcher(teams.get("away", {}).get("probablePitcher", {}).get("id"))
        home_stats = obtener_stats_pitcher(teams.get("home", {}).get("probablePitcher", {}).get("id"))
        weather = obtener_clima(g)
        
        pred = model.obtener_pick(away, home, standings, away_p, home_p, away_stats, home_stats, weather)
        odds = obtener_odds(away, home)
        
        if odds:
            cuota = odds.get("home_ml") if pred["favorite"] == home else odds.get("away_ml")
            if cuota:
                prob_impl = moneyline_to_prob(cuota)
                if prob_impl:
                    ev = calcular_ev(pred["prob_favorite"], cuota)
                    if ev and ev > 0.02:
                        texto += card_game(f"{away} @ {home}", [
                            f"🎯 {pred['favorite']} ML",
                            f"💵 Cuota: {cuota}",
                            f"📈 EV: {round(ev*100, 1)}%",
                            f"🧠 Confianza: {pred['confidence_pct']}%"
                        ])
                        encontrados = True
    
    if not encontrados:
        texto += "No se encontraron apuestas con EV positivo (>2%).\n"
        texto += "Los pitchers pueden no estar confirmados aún."
    
    bot.reply_to(message, texto)

@bot.message_handler(commands=["parley"])
def parley(message):
    existente = buscar_parley_del_dia("parley")
    if existente:
        texto = header("PARLEY DEL DÍA", "🎯") + f"📅 {existente['fecha']}\n\n"
        for leg in existente.get("legs", []):
            texto += f"• {leg['game']}: {leg['pick']} (confianza {leg.get('confidence', 'N/D')}%)\n"
        bot.reply_to(message, texto)
        return
    
    games = obtener_juegos_del_dia()
    if not games:
        bot.reply_to(message, "No hay juegos hoy para generar parley.")
        return
    
    model = MLBModel()
    standings = obtener_standings()
    candidatos = []
    
    for g in games[:10]:
        teams = g.get("teams", {})
        away = teams.get("away", {}).get("team", {}).get("name")
        home = teams.get("home", {}).get("team", {}).get("name")
        if not away or not home:
            continue
        
        away_p = teams.get("away", {}).get("probablePitcher", {}).get("fullName", "TBD")
        home_p = teams.get("home", {}).get("probablePitcher", {}).get("fullName", "TBD")
        away_stats = obtener_stats_pitcher(teams.get("away", {}).get("probablePitcher", {}).get("id"))
        home_stats = obtener_stats_pitcher(teams.get("home", {}).get("probablePitcher", {}).get("id"))
        weather = obtener_clima(g)
        
        pred = model.obtener_pick(away, home, standings, away_p, home_p, away_stats, home_stats, weather)
        
        if pred["confidence_pct"] >= 55 and not pred["avoid"]:
            candidatos.append({
                "game": f"{away} @ {home}",
                "pick": f"{pred['favorite']} ML",
                "confidence": pred["confidence_pct"]
            })
    
    if len(candidatos) < 2:
        bot.reply_to(message, "❌ No hay suficientes picks de calidad para el parley.\n\nCriterios: confianza > 55%, sin pitchers TBD")
        return
    
    seleccionados = candidatos[:3]
    texto = header("PARLEY DEL DÍA MLB", "🎯") + f"📅 {hoy_str()}\n\n"
    for i, p in enumerate(seleccionados, 1):
        texto += f"{i}. {p['game']}\n   🎯 {p['pick']} (confianza {p['confidence']}%)\n\n"
    
    registrar_parley_del_dia("parley", seleccionados)
    bot.reply_to(message, texto)

@bot.message_handler(commands=["parley_millonario"])
def parley_millonario(message):
    existente = buscar_parley_del_dia("parley_millonario")
    if existente:
        texto = header("PARLEY MILLONARIO", "💎") + f"📅 {existente['fecha']}\n\n"
        for leg in existente.get("legs", []):
            texto += f"• {leg['game']}: {leg['pick']} (confianza {leg.get('confidence', 'N/D')}%)\n"
        bot.reply_to(message, texto)
        return
    
    # Excluir juegos del parley normal
    parley_normal = buscar_parley_del_dia("parley")
    juegos_bloqueados = set()
    if parley_normal:
        for leg in parley_normal.get("legs", []):
            juegos_bloqueados.add(leg["game"])
    
    games = obtener_juegos_del_dia()
    if not games:
        bot.reply_to(message, "No hay juegos hoy.")
        return
    
    model = MLBModel()
    standings = obtener_standings()
    candidatos = []
    
    for g in games[:15]:
        teams = g.get("teams", {})
        away = teams.get("away", {}).get("team", {}).get("name")
        home = teams.get("home", {}).get("team", {}).get("name")
        if not away or not home:
            continue
        if f"{away} @ {home}" in juegos_bloqueados:
            continue
        
        away_p = teams.get("away", {}).get("probablePitcher", {}).get("fullName", "TBD")
        home_p = teams.get("home", {}).get("probablePitcher", {}).get("fullName", "TBD")
        away_stats = obtener_stats_pitcher(teams.get("away", {}).get("probablePitcher", {}).get("id"))
        home_stats = obtener_stats_pitcher(teams.get("home", {}).get("probablePitcher", {}).get("id"))
        weather = obtener_clima(g)
        
        pred = model.obtener_pick(away, home, standings, away_p, home_p, away_stats, home_stats, weather)
        
        if pred["confidence_pct"] >= 50:
            candidatos.append({
                "game": f"{away} @ {home}",
                "pick": f"{pred['favorite']} ML",
                "confidence": pred["confidence_pct"]
            })
    
    if len(candidatos) < 3:
        bot.reply_to(message, "❌ No hay suficientes picks para el parley millonario.\n\nCriterio: confianza > 50%")
        return
    
    seleccionados = candidatos[:5]
    texto = header("PARLEY MILLONARIO 5 PICKS", "💎") + f"📅 {hoy_str()}\n\n"
    for i, p in enumerate(seleccionados, 1):
        texto += f"{i}. {p['game']}\n   🔥 {p['pick']} (confianza {p['confidence']}%)\n\n"
    
    registrar_parley_del_dia("parley_millonario", seleccionados)
    bot.reply_to(message, texto)

@bot.message_handler(commands=["pronosticos"])
def pronosticos(message):
    games = obtener_juegos_del_dia()
    if not games:
        bot.reply_to(message, "No hay juegos hoy.")
        return
    
    model = MLBModel()
    standings = obtener_standings()
    picks = []
    
    for g in games[:10]:
        teams = g.get("teams", {})
        away = teams.get("away", {}).get("team", {}).get("name")
        home = teams.get("home", {}).get("team", {}).get("name")
        if not away or not home:
            continue
        
        away_p = teams.get("away", {}).get("probablePitcher", {}).get("fullName", "TBD")
        home_p = teams.get("home", {}).get("probablePitcher", {}).get("fullName", "TBD")
        away_stats = obtener_stats_pitcher(teams.get("away", {}).get("probablePitcher", {}).get("id"))
        home_stats = obtener_stats_pitcher(teams.get("home", {}).get("probablePitcher", {}).get("id"))
        weather = obtener_clima(g)
        
        pred = model.obtener_pick(away, home, standings, away_p, home_p, away_stats, home_stats, weather)
        picks.append({"game": f"{away} @ {home}", "pick": pred["favorite"], "conf": pred["confidence_pct"]})
    
    picks.sort(key=lambda x: x["conf"], reverse=True)
    texto = header("PRONÓSTICOS DEL MODELO", "📊") + f"📅 {hoy_str()}\n\n"
    for p in picks[:8]:
        texto += f"• {p['game']}\n  🎯 {p['pick']} (confianza {p['conf']}%)\n\n"
    
    bot.reply_to(message, texto)

@bot.message_handler(commands=["lesionados"])
def lesionados(message):
    url = f"{MLB_BASE}/transactions"
    params = {"startDate": f"{temporada_actual()}-03-01", "endDate": hoy_str(), "sportId": 1}
    data = safe_get(url, params=params)
    transactions = data.get("transactions", [])
    
    texto = header("LESIONADOS / IL", "🚨")
    count = 0
    for t in transactions:
        desc = t.get("description", "")
        if "injured" in desc.lower() or "il" in desc.lower():
            texto += f"• {desc}\n"
            count += 1
            if count >= 15:
                break
    
    if count == 0:
        texto += "No hay movimientos recientes de lesionados."
    
    bot.reply_to(message, texto)

@bot.message_handler(commands=["roi"])
def roi(message):
    if not os.path.exists(RESULTADOS_CSV):
        bot.reply_to(message, "No hay datos de resultados aún.")
        return
    
    total = 0
    ganadas = 0
    with open(RESULTADOS_CSV, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            total += 1
            if row.get("resultado") == "win":
                ganadas += 1
    
    if total == 0:
        bot.reply_to(message, "No hay apuestas registradas.")
        return
    
    hit_rate = (ganadas / total) * 100
    texto = header("ROI ACUMULADO", "📈")
    texto += f"Total apuestas: {total}\n"
    texto += f"Ganadas: {ganadas} ({hit_rate:.1f}%)\n"
    texto += "Más detalles al registrar resultados."
    bot.reply_to(message, texto)

@bot.message_handler(commands=["exportar_json"])
def exportar_json(message):
    carpeta = "exports_tiktok"
    os.makedirs(carpeta, exist_ok=True)
    ruta = os.path.join(carpeta, f"mlb_{hoy_str()}.json")
    
    data = {
        "fecha": hoy_str(),
        "version": BOT_VERSION,
        "mensaje": "Datos exportados desde MLB Pro Bot"
    }
    
    with open(ruta, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    
    bot.reply_to(message, f"✅ JSON exportado: {ruta}")

@bot.message_handler(commands=["stats_parlays"])
def stats_parlays(message):
    parleys = cargar_parleys_diarios()
    stats = {"parley": {"ganado": 0, "fallado": 0}, "parley_millonario": {"ganado": 0, "fallado": 0}}
    
    for p in parleys:
        tipo = p.get("tipo")
        estado = p.get("estado")
        if tipo in stats and estado in ["ganado", "fallado"]:
            stats[tipo][estado] += 1
    
    total_p = stats["parley"]["ganado"] + stats["parley"]["fallado"]
    total_m = stats["parley_millonario"]["ganado"] + stats["parley_millonario"]["fallado"]
    eff_p = round(stats["parley"]["ganado"] / total_p * 100, 1) if total_p > 0 else 0
    eff_m = round(stats["parley_millonario"]["ganado"] / total_m * 100, 1) if total_m > 0 else 0
    
    texto = header("ESTADÍSTICAS PARLEYS", "📊")
    texto += f"🎯 Parley diario: {stats['parley']['ganado']}G / {stats['parley']['fallado']}F (efectividad {eff_p}%)\n"
    texto += f"💎 Parley millonario: {stats['parley_millonario']['ganado']}G / {stats['parley_millonario']['fallado']}F (efectividad {eff_m}%)"
    bot.reply_to(message, texto)

@bot.message_handler(commands=["debug_odds"])
def debug_odds(message):
    if not ODDS_API_KEY:
        bot.reply_to(message, "❌ API de Odds NO configurada.\n\nAgrega ODDS_API_KEY en el archivo .env")
        return
    
    texto = header("DEBUG API ODDS", "🔍")
    texto += f"API Key: {ODDS_API_KEY[:4]}...\n\n"
    
    try:
        url = "https://api.the-odds-api.com/v4/sports/baseball_mlb/odds"
        params = {"apiKey": ODDS_API_KEY, "regions": "us", "markets": "h2h", "oddsFormat": "american"}
        r = requests.get(url, params=params, timeout=10)
        
        if r.status_code == 200:
            data = r.json()
            texto += f"✅ Conexión exitosa\n"
            texto += f"Eventos encontrados: {len(data) if isinstance(data, list) else 0}\n\n"
            
            if isinstance(data, list) and data:
                texto += "Primeros eventos:\n"
                for event in data[:5]:
                    home = event.get("home_team", "?")
                    teams = event.get("teams", [])
                    away = teams[0] if teams else "?"
                    texto += f"• {away} @ {home}\n"
            else:
                texto += "⚠️ No hay eventos disponibles hoy.\n"
        else:
            texto += f"❌ Error HTTP: {r.status_code}\n"
            texto += f"Respuesta: {r.text[:200]}\n"
    except Exception as e:
        texto += f"❌ Error: {str(e)}\n"
    
    bot.reply_to(message, texto)

@bot.message_handler(commands=["parley_ganado"])
def parley_ganado(message):
    ok = actualizar_estado_parley(hoy_str(), "parley", "ganado")
    bot.reply_to(message, "✅ Parley del día marcado como GANADO." if ok else "No hay parley activo hoy.")

@bot.message_handler(commands=["parley_fallado"])
def parley_fallado(message):
    ok = actualizar_estado_parley(hoy_str(), "parley", "fallado")
    bot.reply_to(message, "❌ Parley del día marcado como FALLADO." if ok else "No hay parley activo hoy.")

@bot.message_handler(commands=["millonario_ganado"])
def millonario_ganado(message):
    ok = actualizar_estado_parley(hoy_str(), "parley_millonario", "ganado")
    bot.reply_to(message, "✅ Parley millonario marcado como GANADO." if ok else "No hay parley millonario activo.")

@bot.message_handler(commands=["millonario_fallado"])
def millonario_fallado(message):
    ok = actualizar_estado_parley(hoy_str(), "parley_millonario", "fallado")
    bot.reply_to(message, "❌ Parley millonario marcado como FALLADO." if ok else "No hay parley millonario activo.")

@bot.message_handler(commands=["reset_parley"])
def reset_parley(message):
    ok = eliminar_parley_del_dia("parley")
    bot.reply_to(message, "♻️ Parley del día reiniciado." if ok else "No había parley activo.")

@bot.message_handler(commands=["reset_millonario"])
def reset_millonario(message):
    ok = eliminar_parley_del_dia("parley_millonario")
    bot.reply_to(message, "♻️ Parley millonario reiniciado." if ok else "No había parley millonario activo.")

# =========================================================
# CALLBACKS (MANEJADOR DE BOTONES)
# =========================================================
@bot.callback_query_handler(func=lambda call: True)
def callback_handler(call):
    try:
        logger.info(f"Callback recibido: {call.data}")
        bot.answer_callback_query(call.id)
        
        # Mapeo de callbacks a comandos
        comandos = {
            "hoy": "/hoy",
            "posiciones": "/posiciones",
            "apuestas": "/apuestas",
            "parley": "/parley",
            "parley_millonario": "/parley_millonario",
            "pronosticos": "/pronosticos",
            "lesionados": "/lesionados",
            "roi": "/roi",
            "exportar_json": "/exportar_json",
            "stats_parlays": "/stats_parlays",
            "debug_odds": "/debug_odds",
            "parley_ganado": "/parley_ganado",
            "parley_fallado": "/parley_fallado",
            "millonario_ganado": "/millonario_ganado",
            "millonario_fallado": "/millonario_fallado",
            "reset_parley": "/reset_parley",
            "reset_millonario": "/reset_millonario"
        }
        
        if call.data in comandos:
            # Crear un mensaje simulado para reutilizar los handlers
            class MockMessage:
                def __init__(self, chat_id):
                    self.chat = type('obj', (object,), {'id': chat_id})
                    self.from_user = type('obj', (object,), {'id': chat_id})
            
            mock_msg = MockMessage(call.message.chat.id)
            
            # Llamar al handler correspondiente
            if call.data == "hoy":
                hoy(mock_msg)
            elif call.data == "posiciones":
                posiciones(mock_msg)
            elif call.data == "apuestas":
                apuestas(mock_msg)
            elif call.data == "parley":
                parley(mock_msg)
            elif call.data == "parley_millonario":
                parley_millonario(mock_msg)
            elif call.data == "pronosticos":
                pronosticos(mock_msg)
            elif call.data == "lesionados":
                lesionados(mock_msg)
            elif call.data == "roi":
                roi(mock_msg)
            elif call.data == "exportar_json":
                exportar_json(mock_msg)
            elif call.data == "stats_parlays":
                stats_parlays(mock_msg)
            elif call.data == "debug_odds":
                debug_odds(mock_msg)
            elif call.data == "parley_ganado":
                parley_ganado(mock_msg)
            elif call.data == "parley_fallado":
                parley_fallado(mock_msg)
            elif call.data == "millonario_ganado":
                millonario_ganado(mock_msg)
            elif call.data == "millonario_fallado":
                millonario_fallado(mock_msg)
            elif call.data == "reset_parley":
                reset_parley(mock_msg)
            elif call.data == "reset_millonario":
                reset_millonario(mock_msg)
        else:
            bot.send_message(call.message.chat.id, f"Comando no reconocido: {call.data}")
            
    except Exception as e:
        logger.error(f"Error en callback: {e}")
        try:
            bot.send_message(call.message.chat.id, f"❌ Error: {str(e)[:100]}")
        except:
            pass

# =========================================================
# INICIO DEL BOT
# =========================================================
if __name__ == "__main__":
    logger.info(f"Iniciando MLB Pro Bot {BOT_VERSION}")
    inicializar_csv_resultados()
    
    print(f"\n{'='*50}")
    print(f"🤖 MLB PRO BOT {BOT_VERSION}")
    print(f"{'='*50}")
    print(f"📱 Bot iniciado correctamente")
    print(f"🆔 Token: {TOKEN[:8]}...")
    print(f"🎲 API Odds: {'✅ Configurada' if ODDS_API_KEY else '❌ No configurada'}")
    print(f"{'='*50}")
    print("Presiona Ctrl+C para detener el bot\n")
    
    bot.infinity_polling(timeout=30, long_polling_timeout=30)