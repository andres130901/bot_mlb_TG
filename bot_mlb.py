import os
import re
import csv
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
                "profit"
            ])
    except Exception as e:
        print(f"Error inicializando CSV: {e}")


def guardar_pick_csv(fecha, juego, tipo_apuesta, pick, cuota, prob_modelo, prob_implicita, edge, stake, grade):
    try:
        with open(RESULTADOS_CSV, "a", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow([
                fecha,
                juego,
                tipo_apuesta,
                pick,
                cuota,
                prob_modelo,
                prob_implicita,
                edge,
                stake,
                grade,
                "",
                ""
            ])
    except Exception as e:
        print(f"Error guardando pick en CSV: {e}")


historial_parlays = cargar_historial()
inicializar_csv_resultados()


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


def responder_largo(chat_id, texto, parse_mode=None):
    partes = dividir_mensaje(texto)
    for parte in partes:
        bot.send_message(chat_id, parte, parse_mode=parse_mode)


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


def american_to_decimal(american_odds):
    try:
        american_odds = int(american_odds)
        if american_odds > 0:
            return 1 + (american_odds / 100)
        return 1 + (100 / abs(american_odds))
    except Exception:
        return None


def calcular_profit(stake_unidades, american_odds, resultado_bool):
    if resultado_bool is None:
        return None
    dec = american_to_decimal(american_odds)
    if dec is None:
        return None
    if resultado_bool:
        return round(stake_unidades * (dec - 1), 2)
    return round(-stake_unidades, 2)


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
                "runs_scored": rs / games if games else 4.5,
                "runs_allowed": ra / games if games else 4.5,
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
# MODELO PRO
# =========================================================
def pitcher_score_from_name(pitcher_name):
    if not pitcher_name or pitcher_name == "TBD":
        return -0.35

    nombre = pitcher_name.lower()

    elite_keywords = [
        "cole", "wheeler", "burnes", "skubal", "glasnow",
        "castillo", "gallen", "yamamoto", "strider"
    ]
    good_keywords = [
        "cease", "peralta", "lopez", "kirby", "valdez",
        "buehler", "gausman", "senga", "gray"
    ]
    weak_keywords = [
        "bullpen", "opener"
    ]

    for k in elite_keywords:
        if k in nombre:
            return 0.28
    for k in good_keywords:
        if k in nombre:
            return 0.14
    for k in weak_keywords:
        if k in nombre:
            return -0.18

    return 0.00


def calcular_probabilidad_local_pro(away_team, home_team, standings, away_pitcher="TBD", home_pitcher="TBD"):
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

    p_home = pitcher_score_from_name(home_pitcher)
    p_away = pitcher_score_from_name(away_pitcher)
    diff_pitcher = p_home - p_away

    score = 0.0
    score += diff_win_pct * 2.1
    score += diff_split * 1.5
    score += diff_last10 * 0.9
    score += diff_run_diff * 1.25
    score += diff_streak * 0.9
    score += diff_runs_scored * 0.7
    score += diff_runs_allowed * 0.7
    score += diff_pitcher * 1.35
    score += 0.07

    prob_home = logistic(score)
    prob_home = clamp(prob_home, 0.28, 0.72)

    return prob_home


def obtener_pick_juego_pro(away_team, home_team, standings, away_pitcher="TBD", home_pitcher="TBD"):
    prob_home = calcular_probabilidad_local_pro(
        away_team, home_team, standings, away_pitcher, home_pitcher
    )

    favorito = home_team if prob_home >= 0.5 else away_team
    prob_fav = prob_home if favorito == home_team else (1 - prob_home)
    avoid = away_pitcher == "TBD" or home_pitcher == "TBD"

    return {
        "favorite": favorito,
        "prob_home": prob_home,
        "prob_favorite": prob_fav,
        "confidence_pct": round(prob_fav * 100),
        "confidence_label": confidence_label(prob_fav),
        "avoid": avoid
    }


def estimar_total_juego_pro(away_team, home_team, standings, away_pitcher="TBD", home_pitcher="TBD"):
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

    p_away = pitcher_score_from_name(away_pitcher)
    p_home = pitcher_score_from_name(home_pitcher)

    total -= p_away * 1.10
    total -= p_home * 1.10

    if away_pitcher == "TBD":
        total += 0.45
    if home_pitcher == "TBD":
        total += 0.45

    last10_away = away.get("last10_win_pct", 0.5)
    last10_home = home.get("last10_win_pct", 0.5)
    total += ((last10_away + last10_home) - 1.0) * 0.30

    return round(clamp(total, 6.5, 12.5), 1)


def elegir_total_pick(total_proyectado, total_line):
    if total_line is None:
        return None

    diff = total_proyectado - total_line

    if diff >= 0.7:
        return {"pick": f"Over {total_line}", "edge": round(diff, 2), "strength": "Alta"}
    if diff >= 0.35:
        return {"pick": f"Over {total_line}", "edge": round(diff, 2), "strength": "Media"}
    if diff <= -0.7:
        return {"pick": f"Under {total_line}", "edge": round(abs(diff), 2), "strength": "Alta"}
    if diff <= -0.35:
        return {"pick": f"Under {total_line}", "edge": round(abs(diff), 2), "strength": "Media"}

    return None


def clasificar_apuesta(prob_model, implied_prob, avoid=False):
    if avoid:
        return None

    edge = prob_model - implied_prob

    if edge >= 0.06:
        return "A"
    if edge >= 0.04:
        return "B"
    if edge >= 0.025:
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
            "markets": "h2h,spreads,totals",
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
                        "spread_home": None,
                        "spread_away": None,
                        "spread_price_home": None,
                        "spread_price_away": None,
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

                        elif key == "spreads":
                            for o in market.get("outcomes", []):
                                if o.get("name") == home_name:
                                    resultado["spread_home"] = o.get("point")
                                    resultado["spread_price_home"] = o.get("price")
                                elif o.get("name") == away_name:
                                    resultado["spread_away"] = o.get("point")
                                    resultado["spread_price_away"] = o.get("price")

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


# =========================================================
# PROPS EXPERIMENTALES
# =========================================================
def generar_props_experimentales(games, standings):
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
        "⚾️ Bot MLB Pro Activo\n\n"
        "Comandos disponibles:\n"
        "/posiciones - Tabla de standings estilo ESPN\n"
        "/hoy - Juegos del día ordenados por horario\n"
        "/pronosticos - Resumen analítico\n"
        "/apuestas - Moneylines y Totales con edge\n"
        "/parley - Parley serio con edge real\n"
        "/parley_millonario - Parley de alto riesgo\n"
        "/props - Props experimentales\n"
        "/lesionados - Movimientos IL / lesionados\n"
        "/pitchers - Pitchers abridores del día\n"
        "/lineups - Estado de alineaciones\n"
        "/historial - Historial guardado\n"
        "/registrar [parlay] [leg] [gano/perdio] - Registrar resultado\n"
        "/roi - Resumen de ROI del CSV\n\n"
        "Ejemplo:\n"
        "/registrar 1 2 gano"
    )
    bot.reply_to(message, texto)


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
            bot.edit_message_text("❌ No pude cargar los standings.", msg.chat.id, msg.message_id)
            return

        bloques = []
        titulo = f"🏆 <b>STANDINGS MLB {season}</b>\n"

        for record in records:
            league_name = record.get("league", {}).get("name", "League")
            division_name = record.get("division", {}).get("name", "División")

            if "Spring" in division_name or "Wild Card" in division_name:
                continue

            lineas = []
            lineas.append(f"{league_name} - {division_name}")
            lineas.append("")
            lineas.append("Team                 W   L   PCT   GB   HOME   AWAY   L10   STRK")
            lineas.append("---------------------------------------------------------------")

            for team in record.get("teamRecords", []):
                nombre = abreviar_equipo(team.get("team", {}).get("name", ""))
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

            bloque = "<pre>" + "\n".join(lineas) + "</pre>"
            bloques.append(bloque)

        bot.delete_message(msg.chat.id, msg.message_id)
        bot.send_message(message.chat.id, titulo, parse_mode="HTML")

        for bloque in bloques:
            bot.send_message(message.chat.id, bloque, parse_mode="HTML")

    except Exception as e:
        bot.edit_message_text(f"❌ Error al cargar posiciones: {str(e)[:120]}", msg.chat.id, msg.message_id)


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
            away = g["teams"]["away"]["team"]["name"]
            home = g["teams"]["home"]["team"]["name"]
            sa = g["teams"]["away"].get("score", "-")
            sh = g["teams"]["home"].get("score", "-")
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

        juegos_ordenados.sort(key=lambda x: x["hora_orden"] if x["hora_orden"] else datetime.max)

        texto = f"📅 <b>JUEGOS DE HOY</b>\n"
        texto += f"🗓️ {fecha} | Hora de Venezuela\n\n"

        for i, j in enumerate(juegos_ordenados, 1):
            texto += f"<b>{i}. {j['away']} @ {j['home']}</b>\n"
            texto += f"🕒 {j['hora_txt']} VET\n"
            texto += f"📌 {j['status']}\n"
            texto += f"⚾ Score: {j['score_away']} - {j['score_home']}\n\n"

        bot.delete_message(msg.chat.id, msg.message_id)
        responder_largo(message.chat.id, texto, parse_mode="HTML")

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

            pred = obtener_pick_juego_pro(away, home, standings, away_p, home_p)
            total_proj = estimar_total_juego_pro(away, home, standings, away_p, home_p)

            texto += f"{away} @ {home}\n"
            texto += f"Pitchers: {away_p} vs {home_p}\n"
            texto += f"Récord: {away_data.get('wins', 0)}-{away_data.get('losses', 0)} | {home_data.get('wins', 0)}-{home_data.get('losses', 0)}\n"
            texto += f"L10: {away_data.get('last10_record', '-')} | {home_data.get('last10_record', '-')}\n"
            texto += f"Run Diff: {away_data.get('run_diff', 0)} | {home_data.get('run_diff', 0)}\n"
            texto += f"Favorito del modelo: {pred['favorite']} ({pred['confidence_pct']}%)\n"
            texto += f"Total proyectado: {total_proj}\n"
            texto += f"Confianza: {pred['confidence_label']}\n"
            if pred["avoid"]:
                texto += "⚠️ Precaución: pitcher TBD detectado.\n"
            texto += "\n"

        texto += "Nota: análisis basado en standings, splits home/away, run differential, últimos 10, racha y heurística de pitcher."

        bot.delete_message(msg.chat.id, msg.message_id)
        responder_largo(message.chat.id, texto)

    except Exception as e:
        bot.edit_message_text(f"❌ Error en /pronosticos: {str(e)[:120]}", msg.chat.id, msg.message_id)


@bot.message_handler(commands=["apuestas"])
def apuestas(message):
    msg = bot.reply_to(message, "🔥 Analizando juegos con enfoque pro...")
    try:
        standings = obtener_standings()
        games = obtener_juegos_del_dia()

        texto = f"🔥 APUESTAS PRO MLB - {hoy_str()}\n\n"

        if not games:
            texto += "No hay juegos hoy."
            bot.edit_message_text(texto, msg.chat.id, msg.message_id)
            return

        picks_ml = []
        picks_totals = []

        for g in games:
            away = g["teams"]["away"]["team"]["name"]
            home = g["teams"]["home"]["team"]["name"]
            away_p = g["teams"]["away"].get("probablePitcher", {}).get("fullName", "TBD")
            home_p = g["teams"]["home"].get("probablePitcher", {}).get("fullName", "TBD")

            pred = obtener_pick_juego_pro(away, home, standings, away_p, home_p)
            odds = obtener_odds_completas(away, home)

            if odds:
                cuota_ml = None
                implied = None

                if pred["favorite"] == home and odds["home_moneyline"] is not None:
                    cuota_ml = odds["home_moneyline"]
                    implied = moneyline_to_prob(cuota_ml)
                elif pred["favorite"] == away and odds["away_moneyline"] is not None:
                    cuota_ml = odds["away_moneyline"]
                    implied = moneyline_to_prob(cuota_ml)

                if implied is not None:
                    grade = clasificar_apuesta(pred["prob_favorite"], implied, pred["avoid"])
                    if grade:
                        stake = stake_sugerido(grade)
                        picks_ml.append({
                            "game": f"{away} @ {home}",
                            "pick": f"{pred['favorite']} ML",
                            "grade": grade,
                            "model_prob": round(pred["prob_favorite"] * 100, 1),
                            "implied_prob": round(implied * 100, 1),
                            "edge": round((pred["prob_favorite"] - implied) * 100, 1),
                            "pitchers": f"{away_p} vs {home_p}",
                            "stake": stake,
                            "cuota": cuota_ml
                        })
                        guardar_pick_csv(
                            hoy_str(),
                            f"{away} @ {home}",
                            "ML",
                            f"{pred['favorite']} ML",
                            cuota_ml,
                            round(pred["prob_favorite"] * 100, 1),
                            round(implied * 100, 1),
                            round((pred["prob_favorite"] - implied) * 100, 1),
                            stake,
                            grade
                        )

                total_proj = estimar_total_juego_pro(away, home, standings, away_p, home_p)
                total_pick = elegir_total_pick(total_proj, odds.get("total_line"))

                if total_pick:
                    stake_total = "1.0u" if total_pick["strength"] == "Alta" else "0.5u"
                    cuota_total = odds["over_price"] if "Over" in total_pick["pick"] else odds["under_price"]
                    picks_totals.append({
                        "game": f"{away} @ {home}",
                        "pick": total_pick["pick"],
                        "edge_total": total_pick["edge"],
                        "strength": total_pick["strength"],
                        "projection": total_proj,
                        "line": odds.get("total_line"),
                        "pitchers": f"{away_p} vs {home_p}",
                        "stake": stake_total,
                        "cuota": cuota_total
                    })
                    guardar_pick_csv(
                        hoy_str(),
                        f"{away} @ {home}",
                        "TOTAL",
                        total_pick["pick"],
                        cuota_total,
                        total_proj,
                        odds.get("total_line"),
                        total_pick["edge"],
                        stake_total,
                        total_pick["strength"]
                    )

        picks_ml.sort(key=lambda x: (x["grade"], x["edge"]), reverse=True)
        picks_totals.sort(key=lambda x: x["edge_total"], reverse=True)

        texto += "💰 MONEYLINE CON EDGE\n\n"
        if picks_ml:
            for p in picks_ml[:6]:
                texto += f"{p['game']}\n"
                texto += f"{p['pick']} | Grado {p['grade']} | Stake {p['stake']}\n"
                texto += f"Modelo: {p['model_prob']}% | Implícita: {p['implied_prob']}%\n"
                texto += f"Edge: +{p['edge']}% | Cuota: {p['cuota']}\n"
                texto += f"Pitchers: {p['pitchers']}\n\n"
        else:
            texto += "No hay moneylines con edge claro hoy.\n\n"

        texto += "📊 TOTALES CON EDGE\n\n"
        if picks_totals:
            for p in picks_totals[:6]:
                texto += f"{p['game']}\n"
                texto += f"{p['pick']} | Fuerza {p['strength']} | Stake {p['stake']}\n"
                texto += f"Proj: {p['projection']} | Línea: {p['line']} | Cuota: {p['cuota']}\n"
                texto += f"Ventaja modelo: {p['edge_total']}\n"
                texto += f"Pitchers: {p['pitchers']}\n\n"
        else:
            texto += "No hay totales con edge claro hoy.\n\n"

        texto += "Regla: si no hay edge, no hay apuesta."

        bot.delete_message(msg.chat.id, msg.message_id)
        responder_largo(message.chat.id, texto)

    except Exception as e:
        bot.edit_message_text(f"❌ Error en /apuestas: {str(e)[:120]}", msg.chat.id, msg.message_id)


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
    msg = bot.reply_to(message, "🎯 Construyendo parley serio...")
    try:
        standings = obtener_standings()
        games = obtener_juegos_del_dia()

        candidatos = []

        for g in games:
            away = g["teams"]["away"]["team"]["name"]
            home = g["teams"]["home"]["team"]["name"]
            away_p = g["teams"]["away"].get("probablePitcher", {}).get("fullName", "TBD")
            home_p = g["teams"]["home"].get("probablePitcher", {}).get("fullName", "TBD")

            pred = obtener_pick_juego_pro(away, home, standings, away_p, home_p)
            odds = obtener_odds_completas(away, home)

            if not odds or pred["avoid"]:
                continue

            cuota_ml = None
            implied = None

            if pred["favorite"] == home and odds["home_moneyline"] is not None:
                cuota_ml = odds["home_moneyline"]
                implied = moneyline_to_prob(cuota_ml)
            elif pred["favorite"] == away and odds["away_moneyline"] is not None:
                cuota_ml = odds["away_moneyline"]
                implied = moneyline_to_prob(cuota_ml)

            if implied is None:
                continue

            grade = clasificar_apuesta(pred["prob_favorite"], implied, pred["avoid"])
            if grade in ["A", "B"]:
                stake = stake_sugerido(grade)
                candidatos.append({
                    "game": f"{away} @ {home}",
                    "pick": f"{pred['favorite']} ML",
                    "grade": grade,
                    "edge": round((pred["prob_favorite"] - implied) * 100, 1),
                    "confidence": pred["confidence_pct"],
                    "stake": stake,
                    "cuota": cuota_ml
                })

        candidatos.sort(key=lambda x: (x["grade"], x["edge"], x["confidence"]), reverse=True)

        texto = f"🎯 PARLEY SERIO MLB - {hoy_str()}\n\n"

        if len(candidatos) < 3:
            texto += "Hoy no hay 3 legs serias. Mejor pasar."
            bot.edit_message_text(texto, msg.chat.id, msg.message_id)
            return

        mejores = candidatos[:3]

        for i, p in enumerate(mejores, 1):
            texto += f"{i}. {p['game']} → {p['pick']}\n"
            texto += f"   Grade {p['grade']} | Edge +{p['edge']}% | Conf {p['confidence']}% | Cuota {p['cuota']}\n"

        nuevo_parlay = {
            "fecha": hoy_str(),
            "legs": [
                {
                    "game": p["game"],
                    "pick": p["pick"],
                    "confidence": p["confidence"],
                    "grade": p["grade"],
                    "edge": p["edge"],
                    "stake": p["stake"],
                    "cuota": p["cuota"],
                    "acierto": None
                }
                for p in mejores
            ]
        }

        historial_parlays.append(nuevo_parlay)
        if len(historial_parlays) > 100:
            historial_parlays.pop(0)
        guardar_historial(historial_parlays)

        texto += "\n✅ Solo picks con edge real."
        texto += "\n✅ Parley guardado en historial."

        bot.edit_message_text(texto, msg.chat.id, msg.message_id)

    except Exception as e:
        bot.edit_message_text(f"❌ Error en /parley: {str(e)[:120]}", msg.chat.id, msg.message_id)


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

            pred = obtener_pick_juego_pro(away, home, standings, away_p, home_p)
            odds = obtener_odds_completas(away, home)
            total_proj = estimar_total_juego_pro(away, home, standings, away_p, home_p)

            opciones = [f"{away} @ {home} → {pred['favorite']} ML"]

            if odds and odds.get("total_line") is not None:
                tp = elegir_total_pick(total_proj, odds["total_line"])
                if tp:
                    opciones.append(f"{away} @ {home} → {tp['pick']}")

            opciones.append(f"{away} @ {home} → NRFI")
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

            texto += (
                f"   {j}. {leg.get('game', '-')} → {leg.get('pick', '-')} "
                f"({leg.get('confidence', '-')}%) {resultado}\n"
            )

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

        texto = "📈 RESUMEN ROI\n\n"
        texto += f"Apuestas cerradas: {total_apuestas}\n"
        texto += f"Ganadas: {ganadas}\n"
        texto += f"Perdidas: {perdidas}\n"
        texto += f"Hit Rate: {hit_rate}%\n"
        texto += f"Unidades arriesgadas: {round(total_unidades, 2)}u\n"
        texto += f"Profit neto: {round(total_profit, 2)}u\n"
        texto += f"ROI: {roi_pct}%\n"

        bot.reply_to(message, texto)

    except Exception as e:
        bot.reply_to(message, f"❌ Error calculando ROI: {str(e)[:120]}")


# =========================================================
# MAIN
# =========================================================
print("🔥 BOT MLB PRO CARGADO CORRECTAMENTE 🔥")
bot.infinity_polling(skip_pending=True, timeout=30, long_polling_timeout=30)
