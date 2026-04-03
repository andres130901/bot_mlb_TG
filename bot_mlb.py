import os
import re
import json
import math
import random
import requests
import telebot
from dotenv import load_dotenv
from datetime import datetime, date, timedelta

try:
    from zoneinfo import ZoneInfo
except ImportError:
    ZoneInfo = None


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
REQUEST_TIMEOUT = 15


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


historial_parlays = cargar_historial()


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


def dividir_mensaje(texto, max_len=4000):
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


def responder_largo(chat_id, texto):
    partes = dividir_mensaje(texto)
    for parte in partes:
        bot.send_message(chat_id, parte)


def convertir_a_hora_venezuela(game_date_str):
    try:
        dt_utc = datetime.fromisoformat(game_date_str.replace("Z", "+00:00"))
        if ZoneInfo:
            dt_ve = dt_utc.astimezone(ZoneInfo("America/Caracas"))
        else:
            dt_ve = dt_utc - timedelta(hours=4)
        return dt_ve.strftime("%I:%M %p")
    except Exception:
        return "Hora no disponible"


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
    if prob >= 0.64:
        return "Alta"
    if prob >= 0.58:
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
            run_diff = rs - ra

            last10_w = t.get("lastTenWins", 0)
            last10_l = t.get("lastTenLosses", 0)
            last10_games = max(last10_w + last10_l, 1)

            equipos[name] = {
                "wins": wins,
                "losses": losses,
                "win_pct": wins / games,
                "home_win_pct": home_w / home_games,
                "away_win_pct": away_w / away_games,
                "run_diff": run_diff,
                "runs_scored": rs,
                "runs_allowed": ra,
                "last10_w": last10_w,
                "last10_l": last10_l,
                "last10_win_pct": last10_w / last10_games,
                "streak": t.get("streakCode", ""),
                "games_back": t.get("gamesBack", "-"),
                "pct_text": t.get("pct", "---"),
                "home_record": f"{home_w}-{home_l}",
                "away_record": f"{away_w}-{away_l}",
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
    return data.get("dates", [{}])[0].get("games", [])


def obtener_transacciones_hoy():
    url = f"{MLB_BASE}/transactions"
    params = {
        "startDate": f"{temporada_actual()}-03-01",
        "endDate": hoy_str(),
        "sportId": 1
    }
    data = safe_get(url, params=params)
    return data.get("transactions", [])


# =========================================================
# MODELO SIMPLE Y CONSERVADOR
# =========================================================
def pitcher_adjustment(pitcher_name):
    """
    Ajuste conservador.
    Si no hay pitcher confirmado, se penaliza la confianza.
    """
    if not pitcher_name or pitcher_name == "TBD":
        return -0.03
    return 0.00


def calcular_probabilidad_local(away_team, home_team, standings, away_pitcher="TBD", home_pitcher="TBD"):
    away = standings.get(away_team)
    home = standings.get(home_team)

    if not away or not home:
        return 0.50

    diff_win_pct = home["win_pct"] - away["win_pct"]
    diff_split = home["home_win_pct"] - away["away_win_pct"]
    diff_last10 = home["last10_win_pct"] - away["last10_win_pct"]
    diff_run_diff = (home["run_diff"] - away["run_diff"]) / 100.0
    diff_streak = parse_streak(home["streak"]) - parse_streak(away["streak"])

    score = 0.0
    score += diff_win_pct * 2.2
    score += diff_split * 1.4
    score += diff_last10 * 1.0
    score += diff_run_diff * 1.1
    score += diff_streak
    score += 0.08  # ventaja local razonable

    score += pitcher_adjustment(home_pitcher)
    score -= pitcher_adjustment(away_pitcher)

    prob_home = logistic(score)
    prob_home = clamp(prob_home, 0.30, 0.70)

    return prob_home


def obtener_pick_juego(away_team, home_team, standings, away_pitcher="TBD", home_pitcher="TBD"):
    prob_home = calcular_probabilidad_local(
        away_team, home_team, standings, away_pitcher, home_pitcher
    )

    favorito = home_team if prob_home >= 0.5 else away_team
    prob_fav = prob_home if favorito == home_team else (1 - prob_home)
    return {
        "favorite": favorito,
        "prob_home": prob_home,
        "prob_favorite": prob_fav,
        "confidence_pct": round(prob_fav * 100),
        "confidence_label": confidence_label(prob_fav),
        "avoid": away_pitcher == "TBD" or home_pitcher == "TBD"
    }


def sugerir_total(prob_favorite, away_pitcher, home_pitcher):
    if away_pitcher == "TBD" or home_pitcher == "TBD":
        return "Sin recomendación de total (pitcher TBD)"
    if prob_favorite >= 0.62:
        return "Under 9.5"
    return "Lean Under 9.5"


def sugerir_runline(prob_favorite, favorito):
    if prob_favorite >= 0.63:
        return f"{favorito} -1.5"
    return f"{favorito} ML"


# =========================================================
# ODDS OPCIONALES
# =========================================================
def normalizar_nombre_equipo_odds(team_name):
    """
    Puedes ajustar este mapeo si tu proveedor de odds usa nombres distintos.
    """
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


def obtener_odds(away_team, home_team):
    """
    Intenta buscar odds reales si tienes ODDS_API_KEY.
    Si falla, devuelve None y el bot sigue funcionando.
    """
    if not ODDS_API_KEY:
        return None

    try:
        url = "https://api.the-odds-api.com/v4/sports/baseball_mlb/odds"
        params = {
            "apiKey": ODDS_API_KEY,
            "regions": "us",
            "markets": "h2h",
            "oddsFormat": "american"
        }
        data = safe_get(url, params=params)

        away_norm = normalizar_nombre_equipo_odds(away_team)
        home_norm = normalizar_nombre_equipo_odds(home_team)

        if not isinstance(data, list):
            return None

        for event in data:
            home_name = event.get("home_team", "")
            away_name = None
            teams = event.get("teams", [])
            if isinstance(teams, list) and len(teams) == 2:
                away_name = [t for t in teams if t != home_name]
                away_name = away_name[0] if away_name else ""

            if home_name == home_norm and away_name == away_norm:
                bookmakers = event.get("bookmakers", [])
                for book in bookmakers:
                    for market in book.get("markets", []):
                        if market.get("key") == "h2h":
                            outcomes = market.get("outcomes", [])
                        home_ml = None
                        away_ml = None
                        for o in outcomes:
                                if o.get("name") == home_name:
                                    home_ml = o.get("price")
                                elif o.get("name") == away_name:
                                    away_ml = o.get("price")
            if home_ml is not None and away_ml is not None:
                                return {
                                    "home_moneyline": home_ml,
                                    "away_moneyline": away_ml,
                                    "bookmaker": book.get("title", "Bookmaker")
                                }
        return None
    except Exception as e:
        print(f"Error obteniendo odds: {e}")
        return None


# =========================================================
# PROPS EXPERIMENTALES
# =========================================================
def generar_props_experimentales(games, standings):
    """
    Props conservadoras y marcadas como experimentales.
    No inventa jugadores fuera del calendario sin filtrar primero.
    """
    candidates = [
        {"player": "Aaron Judge", "team": "New York Yankees", "prop": "Over 1.5 Total Bases", "base_conf": 60},
        {"player": "Shohei Ohtani", "team": "Los Angeles Dodgers", "prop": "Over 1.5 Total Bases", "base_conf": 60},
        {"player": "Juan Soto", "team": "New York Mets", "prop": "Over 0.5 Runs Scored", "base_conf": 57},
        {"player": "Bryce Harper", "team": "Philadelphia Phillies", "prop": "Over 0.5 RBI", "base_conf": 56},
        {"player": "Freddie Freeman", "team": "Los Angeles Dodgers", "prop": "Over 0.5 RBI", "base_conf": 56},
        {"player": "Yordan Alvarez", "team": "Houston Astros", "prop": "Over 1.5 Total Bases", "base_conf": 57},
        {"player": "Ronald Acuña Jr.", "team": "Atlanta Braves", "prop": "Over 0.5 Runs", "base_conf": 56},
        {"player": "Francisco Lindor", "team": "New York Mets", "prop": "Over 0.5 Hits", "base_conf": 55},
    ]

    active_teams = set()
    for g in games:
        active_teams.add(g["teams"]["away"]["team"]["name"])
        active_teams.add(g["teams"]["home"]["team"]["name"])

    props = []
    for c in candidates:
        if c["team"] not in active_teams:
            continue

        t = standings.get(c["team"], {})
        adj = 0
        adj += int((t.get("win_pct", 0.5) - 0.5) * 20)
        adj += int(parse_streak(t.get("streak", "")) * 100)

        conf = clamp(c["base_conf"] + adj, 52, 64)

        props.append({
            "player": c["player"],
            "team": c["team"],
            "prop": c["prop"],
            "confidence": int(conf)
        })

    props.sort(key=lambda x: x["confidence"], reverse=True)
    return props[:5]


# =========================================================
# COMANDOS
# =========================================================
@bot.message_handler(commands=["start"])
def start(message):
    texto = (
        "⚾️ Bot MLB mejorado y estable\n\n"
        "Comandos disponibles:\n"
        "/posiciones - Tabla de standings\n"
        "/hoy - Juegos del día\n"
        "/pronosticos - Resumen analítico\n"
        "/apuestas - Picks recomendados\n"
        "/parley - 3 picks más sólidos\n"
        "/parley_millonario - Parley de alto riesgo\n"
        "/props - Props experimentales\n"
        "/lesionados - Movimientos IL / lesionados\n"
        "/pitchers - Pitchers abridores del día\n"
        "/lineups - Estado de alineaciones\n"
        "/historial - Historial guardado\n"
        "/registrar [parlay] [leg] [gano/perdio] - Registrar resultado\n\n"
        "Ejemplo:\n"
        "/registrar 1 2 gano"
    )
    bot.reply_to(message, texto)
@bot.message_handler(commands=["posiciones"])
def posiciones(message):
    msg = bot.reply_to(message, "🏆 Cargando standings...")
    try:
        season = temporada_actual()
        url = f"{MLB_BASE}/standings"
        params = {
            "leagueId": "103,104",
            "season": season,
            "standingsTypes": "regularSeason"
        }
        data = safe_get(url, params=params)

        texto = f"🏆 STANDINGS MLB {season}\n\n"

        records = data.get("records", [])
        if not records:
            bot.edit_message_text("No pude cargar los standings.", msg.chat.id, msg.message_id)
            return

        for record in records:
            league_name = record.get("league", {}).get("name", "League")
            division_name = record.get("division", {}).get("name", "División")

            if "Spring" in division_name or "Wild Card" in division_name:
                continue

            texto += f"{league_name} - {division_name}\n"
            texto += "Equipo               W   L   Pct   GB   Home    Away    L10    Strk\n"
            texto += "-------------------------------------------------------------------\n"

            for team in record.get("teamRecords", []):
                name = team.get("team", {}).get("name", "")[:19].ljust(19)
                w = str(team.get("wins", 0)).rjust(3)
                l = str(team.get("losses", 0)).rjust(3)
                pct = str(team.get("pct", "---")).ljust(5)
                gb = str(team.get("gamesBack", "-")).ljust(5)
                home = f"{team.get('homeWins', 0)}-{team.get('homeLosses', 0)}".ljust(7)
                away = f"{team.get('awayWins', 0)}-{team.get('awayLosses', 0)}".ljust(7)
                l10 = f"{team.get('lastTenWins', 0)}-{team.get('lastTenLosses', 0)}".ljust(6)
                streak = str(team.get("streakCode", "-")).ljust(5)

                texto += f"{name} {w} {l} {pct} {gb} {home} {away} {l10} {streak}\n"

            texto += "\n"

        bot.delete_message(msg.chat.id, msg.message_id)
        responder_largo(message.chat.id, texto)

    except Exception as e:
        bot.edit_message_text(f"❌ Error al cargar posiciones: {str(e)[:120]}", msg.chat.id, msg.message_id)


@bot.message_handler(commands=["hoy"])
def hoy(message):
    msg = bot.reply_to(message, "📅 Cargando juegos del día...")
    try:
        games = obtener_juegos_del_dia()
        texto = f"📅 JUEGOS DE HOY ({hoy_str()}) - Hora de Venezuela\n\n"

        if not games:
            texto += "No hay juegos programados hoy."
            bot.edit_message_text(texto, msg.chat.id, msg.message_id)
            return

        for g in games:
            away = g["teams"]["away"]["team"]["name"]
            home = g["teams"]["home"]["team"]["name"]
            sa = g["teams"]["away"].get("score", "-")
            sh = g["teams"]["home"].get("score", "-")
            status = g.get("status", {}).get("detailedState", "Estado desconocido")
            hora = convertir_a_hora_venezuela(g.get("gameDate", ""))

            texto += f"{away} @ {home}\n"
            texto += f"Score: {sa}-{sh} | {status}\n"
            texto += f"🕒 {hora} VET\n\n"

        bot.delete_message(msg.chat.id, msg.message_id)
        responder_largo(message.chat.id, texto)

    except Exception as e:
        bot.edit_message_text(f"❌ Error al cargar juegos: {str(e)[:120]}", msg.chat.id, msg.message_id)


@bot.message_handler(commands=["pronosticos"])
def pronosticos(message):
    msg = bot.reply_to(message, "📊 Generando análisis estadístico...")
    try:
        standings = obtener_standings()
        games = obtener_juegos_del_dia()

        texto = f"📊 ANÁLISIS ESTADÍSTICO MLB - {hoy_str()}\n\n"

        if not games:
            texto += "No hay juegos programados hoy."
            bot.edit_message_text(texto, msg.chat.id, msg.message_id)
            return
        for g in games[:10]:
                away = g["teams"]["away"]["team"]["name"]
                home = g["teams"]["home"]["team"]["name"]
                away_p = g["teams"]["away"].get("probablePitcher", {}).get("fullName", "TBD")
                home_p = g["teams"]["home"].get("probablePitcher", {}).get("fullName", "TBD")

        away_data = standings.get(away, {})
        home_data = standings.get(home, {})

        pred = obtener_pick_juego(away, home, standings, away_p, home_p)

        texto += f"{away} @ {home}\n"
        texto += f"Pitchers: {away_p} vs {home_p}\n"
        texto += f"Récord: {away_data.get('wins', 0)}-{away_data.get('losses', 0)} | {home_data.get('wins', 0)}-{home_data.get('losses', 0)}\n"
        texto += f"L10: {away_data.get('last10_record', '-')} | {home_data.get('last10_record', '-')}\n"
        texto += f"Run Diff: {away_data.get('run_diff', 0)} | {home_data.get('run_diff', 0)}\n"
        texto += f"Favorito del modelo: {pred['favorite']} ({pred['confidence_pct']}%)\n"
        texto += f"Confianza: {pred['confidence_label']}\n"
        if pred["avoid"]:
            texto += "⚠️ Precaución: pitcher TBD detectado.\n"
            texto += "\n"

        texto += "Nota: análisis conservador basado en standings, splits home/away, run differential, últimos 10 y racha."

        bot.delete_message(msg.chat.id, msg.message_id)
        responder_largo(message.chat.id, texto)

    except Exception as e:
        bot.edit_message_text(f"❌ Error en /pronosticos: {str(e)[:120]}", msg.chat.id, msg.message_id)


@bot.message_handler(commands=["apuestas"])
def apuestas(message):
    msg = bot.reply_to(message, "🔥 Analizando juegos del día...")
    try:
        standings = obtener_standings()
        games = obtener_juegos_del_dia()

        texto = f"🔥 APUESTAS RECOMENDADAS MLB - {hoy_str()}\n\n"

        if not games:
            texto += "No hay juegos hoy."
            bot.edit_message_text(texto, msg.chat.id, msg.message_id)
            return

        picks = []

        for g in games:
            away = g["teams"]["away"]["team"]["name"]
            home = g["teams"]["home"]["team"]["name"]
            away_p = g["teams"]["away"].get("probablePitcher", {}).get("fullName", "TBD")
            home_p = g["teams"]["home"].get("probablePitcher", {}).get("fullName", "TBD")

            pred = obtener_pick_juego(away, home, standings, away_p, home_p)
            odds = obtener_odds(away, home)

            value_txt = "Sin odds"
            odds_txt = "No disponible"
            if odds:
                if pred["favorite"] == home:
                    implied = moneyline_to_prob(odds["home_moneyline"])
                    odds_txt = f"{odds['bookmaker']} | Home ML: {odds['home_moneyline']} | Away ML: {odds['away_moneyline']}"
                else:
                    implied = moneyline_to_prob(odds["away_moneyline"])
                    odds_txt = f"{odds['bookmaker']} | Home ML: {odds['home_moneyline']} | Away ML: {odds['away_moneyline']}"

                if implied is not None:
                    edge = pred["prob_favorite"] - implied
                    value_txt = f"Edge estimado: {round(edge * 100, 1)}%"

            picks.append({
                "game": f"{away} @ {home}",
                "favorite": pred["favorite"],
                "confidence_pct": pred["confidence_pct"],
                "confidence_label": pred["confidence_label"],
                "pitchers": f"{away_p} vs {home_p}",
                "avoid": pred["avoid"],
                "total_pick": sugerir_total(pred["prob_favorite"], away_p, home_p),
                "runline_pick": sugerir_runline(pred["prob_favorite"], pred["favorite"]),
                "odds_text": odds_txt,
                "value_text": value_txt,
                "prob_favorite": pred["prob_favorite"],
            })

        picks.sort(key=lambda x: x["confidence_pct"], reverse=True)
        for p in picks[:10]:
            texto += f"{p['game']}\n"
            texto += f"Pitchers: {p['pitchers']}\n"
            texto += f"Moneyline recomendado: {p['favorite']} ML\n"
            texto += f"Run Line: {p['runline_pick']}\n"
            texto += f"Total: {p['total_pick']}\n"
            texto += f"Confianza: {p['confidence_pct']}% ({p['confidence_label']})\n"
            texto += f"Odds: {p['odds_text']}\n"
            texto += f"{p['value_text']}\n"
            if p["avoid"]:
                texto += "⚠️ Evitar stake alto por pitcher TBD.\n"
            texto += "\n"

        texto += "Consejo: evita parlays grandes si la confianza es baja o hay pitcher TBD."

        bot.delete_message(msg.chat.id, msg.message_id)
        responder_largo(message.chat.id, texto)

    except Exception as e:
        bot.edit_message_text(f"❌ Error al generar apuestas: {str(e)[:120]}", msg.chat.id, msg.message_id)


@bot.message_handler(commands=["props"])
def props(message):
    msg = bot.reply_to(message, "📊 Buscando props experimentales del día...")
    try:
        standings = obtener_standings()
        games = obtener_juegos_del_dia()

        texto = f"📊 PROPS EXPERIMENTALES - {hoy_str()}\n\n"
        texto += "⚠️ Estas props son experimentales y no sustituyen una fuente de props en tiempo real.\n\n"

        if not games:
            texto += "No hay juegos hoy."
            bot.edit_message_text(texto, msg.chat.id, msg.message_id)
            return

        mejores = generar_props_experimentales(games, standings)

        if not mejores:
            texto += "No se generaron props confiables hoy."
            bot.edit_message_text(texto, msg.chat.id, msg.message_id)
            return

        for i, prop in enumerate(mejores, 1):
            texto += f"{i}. {prop['player']} ({prop['team']})\n"
            texto += f"   {prop['prop']}\n"
            texto += f"   Confianza estimada: {prop['confidence']}%\n\n"

        texto += "Recomendación: usa stake pequeño en props experimentales."

        bot.edit_message_text(texto, msg.chat.id, msg.message_id)

    except Exception as e:
        bot.edit_message_text(f"❌ Error al cargar props: {str(e)[:120]}", msg.chat.id, msg.message_id)


@bot.message_handler(commands=["parley", "parley_del_dia"])
def parley(message):
    msg = bot.reply_to(message, "🎯 Generando parley conservador...")
    try:
        standings = obtener_standings()
        games = obtener_juegos_del_dia()

        picks = []

        for g in games:
            away = g["teams"]["away"]["team"]["name"]
            home = g["teams"]["home"]["team"]["name"]
            away_p = g["teams"]["away"].get("probablePitcher", {}).get("fullName", "TBD")
            home_p = g["teams"]["home"].get("probablePitcher", {}).get("fullName", "TBD")

            pred = obtener_pick_juego(away, home, standings, away_p, home_p)

            if pred["confidence_pct"] >= 57 and not pred["avoid"]:
                picks.append({
                    "game": f"{away} @ {home}",
                    "pick": f"{pred['favorite']} ML",
                    "confidence": pred["confidence_pct"]
                })

        picks.sort(key=lambda x: x["confidence"], reverse=True)

        texto = f"🎯 PARLEY DEL DÍA MLB - {hoy_str()}\n\n"

        if len(picks) < 3:
            texto += "Hoy no hay 3 picks suficientemente sólidos. Mejor no forzar el parley."
            bot.edit_message_text(texto, msg.chat.id, msg.message_id)
            return

        mejores = picks[:3]

        for i, p in enumerate(mejores, 1):
            texto += f"{i}. {p['game']} → {p['pick']} ({p['confidence']}%)\n"

        nuevo_parlay = {
            "fecha": hoy_str(),
            "legs": [
                {
                    "game": p["game"],
                    "pick": p["pick"],
                    "confidence": p["confidence"],
                    "acierto": None
                }
                for p in mejores
            ]
        }

        historial_parlays.append(nuevo_parlay)
        if len(historial_parlays) > 100:
            historial_parlays.pop(0)

        guardar_historial(historial_parlays)

        texto += "\n✅ Parley guardado en historial."
        texto += "\nUsa /historial para verlo."
        texto += "\nUsa /registrar 1 2 gano para cargar resultados manualmente."

        bot.edit_message_text(texto, msg.chat.id, msg.message_id)

    except Exception as e:
        bot.edit_message_text(f"❌ Error al generar parley: {str(e)[:120]}", msg.chat.id, msg.message_id)


@bot.message_handler(commands=["parley_millonario"])
def parley_millonario(message):
    msg = bot.reply_to(message, "💰 Generando parley millonario...")
    try:
        standings = obtener_standings()
        games = obtener_juegos_del_dia()

        texto = f"💰 PARLEY MILLONARIO - {hoy_str()}\n\n"
        texto += "⚠️ Alto riesgo / alta recompensa\n"
        texto += "Solo toma una selección por juego para evitar contradicciones.\n\n"

        if len(games) < 6:
            texto += "No hay suficientes juegos hoy para un parley millonario razonable."
            bot.edit_message_text(texto, msg.chat.id, msg.message_id)
            return

        legs = []

        for g in games:
            away = g["teams"]["away"]["team"]["name"]
            home = g["teams"]["home"]["team"]["name"]
            away_p = g["teams"]["away"].get("probablePitcher", {}).get("fullName", "TBD")
            home_p = g["teams"]["home"].get("probablePitcher", {}).get("fullName", "TBD")

            pred = obtener_pick_juego(away, home, standings, away_p, home_p)

            opciones = []

            if not pred["avoid"]:
                opciones.append(f"{away} @ {home} → {pred['favorite']} ML")
                opciones.append(f"{away} @ {home} → {sugerir_runline(pred['prob_favorite'], pred['favorite'])}")
                opciones.append(f"{away} @ {home} → NRFI")
                opciones.append(f"{away} @ {home} → {sugerir_total(pred['prob_favorite'], away_p, home_p)}")
            else:
                opciones.append(f"{away} @ {home} → NRFI")
                opciones.append(f"{away} @ {home} → Lean Under 9.5")

            legs.append(random.choice(opciones))

        random.shuffle(legs)
        seleccionadas = legs[:10] if len(legs) >= 10 else legs

        for i, leg in enumerate(seleccionadas, 1):
            texto += f"{i}. {leg}\n"

        texto += "\nJuega con responsabilidad."

        bot.delete_message(msg.chat.id, msg.message_id)
        responder_largo(message.chat.id, texto)

    except Exception as e:
        bot.edit_message_text(f"❌ Error en /parley_millonario: {str(e)[:120]}", msg.chat.id, msg.message_id)


@bot.message_handler(commands=["lesionados"])
def lesionados(message):
    msg = bot.reply_to(message, "🚨 Cargando lesionados / IL...")
    try:
        transactions = obtener_transacciones_hoy()
        texto = "🚨 LESIONADOS / IL RECIENTES\n\n"

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
        responder_largo(message.chat.id, texto)

    except Exception as e:
        bot.edit_message_text(f"❌ Error al cargar lesionados: {str(e)[:120]}", msg.chat.id, msg.message_id)


@bot.message_handler(commands=["pitchers"])
def pitchers(message):
    msg = bot.reply_to(message, "🧢 Cargando pitchers abridores...")
    try:
        games = obtener_juegos_del_dia()
        texto = f"🧢 PITCHERS DEL DÍA - {hoy_str()}\n\n"

        if not games:
            texto += "No hay juegos hoy."
            bot.edit_message_text(texto, msg.chat.id, msg.message_id)
            return
        for g in games:
            away = g["teams"]["away"]["team"]["name"]
            home = g["teams"]["home"]["team"]["name"]
            away_p = g["teams"]["away"].get("probablePitcher", {}).get("fullName", "TBD")
            home_p = g["teams"]["home"].get("probablePitcher", {}).get("fullName", "TBD")

            texto += f"{away} @ {home}\n"
            texto += f"   {away_p} vs {home_p}\n\n"

        bot.delete_message(msg.chat.id, msg.message_id)
        responder_largo(message.chat.id, texto)

    except Exception as e:
        bot.edit_message_text(f"❌ Error al cargar pitchers: {str(e)[:120]}", msg.chat.id, msg.message_id)


@bot.message_handler(commands=["lineups"])
def lineups(message):
    texto = (
        "📋 LINEUPS\n\n"
        "Las alineaciones oficiales suelen publicarse entre 1 y 2 horas antes del primer juego.\n"
        "Usa mientras tanto:\n"
        "/pitchers\n"
        "/lesionados\n"
        "/hoy\n\n"
        "Si luego quieres, te ayudo a agregar lineups reales con otra fuente."
    )
    bot.reply_to(message, texto)


@bot.message_handler(commands=["historial"])
def historial(message):
    if not historial_parlays:
        bot.reply_to(
            message,
            "📊 HISTORIAL DEL BOT\n\n"
            "Todavía no hay parlays guardados.\n"
            "Genera uno con /parley."
        )
        return

    texto = "📊 HISTORIAL DEL BOT - PARLAYS\n\n"
    texto += f"Total guardados: {len(historial_parlays)}\n\n"

    aciertos_total = 0
    legs_total_evaluadas = 0

    ultimos = list(reversed(historial_parlays[-10:]))

    for idx, parlay in enumerate(ultimos, 1):
        texto += f"Parlay {idx} - {parlay.get('fecha', 'Sin fecha')}\n"

        for j, leg in enumerate(parlay.get("legs", []), 1):
            acierto = leg.get("acierto")
            if acierto is True:
                resultado = "✅"
                aciertos_total += 1
                legs_total_evaluadas += 1
            elif acierto is False:
                resultado = "❌"
                legs_total_evaluadas += 1
            else:
                resultado = "⏳"

            texto += f"   {j}. {leg.get('game', '-') } → {leg.get('pick', '-') } ({leg.get('confidence', '-') }%) {resultado}\n"

        texto += "\n"

    if legs_total_evaluadas > 0:
        porcentaje = round((aciertos_total / legs_total_evaluadas) * 100, 1)
        texto += "Estadísticas reales registradas:\n"
        texto += f"• Legs evaluadas: {legs_total_evaluadas}\n"
        texto += f"• Aciertos: {aciertos_total}\n"
        texto += f"• Efectividad: {porcentaje}%\n\n"
    else:
        texto += "Todavía no hay legs evaluadas para calcular efectividad real.\n\n"

    texto += "Ejemplo para registrar resultados:\n"
    texto += "/registrar 1 2 gano\n"
    texto += "/registrar 1 3 perdio"

    responder_largo(message.chat.id, texto)


@bot.message_handler(commands=["registrar"])
def registrar(message):
    """
    /registrar [parlay] [leg] [gano/perdio]

    Importante:
    Se usa sobre el historial mostrado en /historial, donde:
    - Parlay 1 es el más reciente mostrado
    - Leg 1, 2, 3 según el orden mostrado
    """
    try:
        partes = message.text.strip().split()

        if len(partes) != 4:
            bot.reply_to(
                message,
                "Formato inválido.\n\nUsa:\n/registrar [parlay] [leg] [gano/perdio]\n\nEjemplo:\n/registrar 1 2 gano"
            )
            return

        _, parlay_str, leg_str, resultado_str = partes

        if not parlay_str.isdigit() or not leg_str.isdigit():
            bot.reply_to(message, "Parlay y leg deben ser números.")
            return

        parlay_num = int(parlay_str)
        leg_num = int(leg_str)
        resultado = resultado_str.lower().strip()

        if resultado not in ["gano", "ganó", "perdio", "perdió"]:
            bot.reply_to(message, "El resultado debe ser: gano o perdio.")
            return

        if not historial_parlays:
            bot.reply_to(message, "No hay historial para registrar.")
            return
        ultimos_indices = list(range(len(historial_parlays)))[-10:]
        ultimos_indices.reverse()

        if parlay_num < 1 or parlay_num > len(ultimos_indices):
            bot.reply_to(message, "Número de parlay fuera de rango.")
            return

        real_index = ultimos_indices[parlay_num - 1]
        parlay = historial_parlays[real_index]

        legs = parlay.get("legs", [])
        if leg_num < 1 or leg_num > len(legs):
            bot.reply_to(message, "Número de leg fuera de rango.")
            return

        legs[leg_num - 1]["acierto"] = resultado in ["gano", "ganó"]
        guardar_historial(historial_parlays)

        bot.reply_to(
            message,
            f"✅ Registrado correctamente:\n"
            f"Parlay {parlay_num}, Leg {leg_num} → {'Ganó' if legs[leg_num - 1]['acierto'] else 'Perdió'}"
        )

    except Exception as e:
        bot.reply_to(message, f"❌ Error al registrar resultado: {str(e)[:120]}")


# =========================================================
# MAIN
# =========================================================
print("🤖 Bot MLB mejorado iniciado correctamente...")
bot.infinity_polling(skip_pending=True, timeout=30, long_polling_timeout=30)