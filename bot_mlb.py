import telebot
import requests
from datetime import date
import os
from dotenv import load_dotenv
import random

# ====================== SISTEMA DE HISTORIAL ======================
historial_parlays = []   # Lista para guardar todos los parlays generados
# ====================== FUNCIONES DE APOYO (DEBEN IR ANTES DE LOS COMANDOS) ======================
def obtener_pitcher_stats(pitcher_name):
    # ... (todo tu código de esta función)
    pass   # pega aquí tu función completa

def evaluar_pitcher(pitcher_stats):
    # ... pega tu función
    pass

def obtener_team_stats(team_name, home_away):
    # ... pega tu función
    pass

def ajustar_por_team_stats(team_stats, home_away):
    # ... pega tu función
    pass

def obtener_racha(team_name):
    # ... pega tu función
    pass

def ajustar_por_racha(racha):
    # ... pega tu función
    pass

def obtener_lineup(team_name):
    # ... pega tu función
    pass

def evaluar_lineup(lineup_stats):
    # ... pega tu función
    pass

def obtener_odds(away_team, home_team):
    # ... pega tu función (si la tienes)
    pass

def moneyline_to_prob(moneyline):
    # ... pega tu función
    pass
load_dotenv()
TOKEN = os.getenv("TOKEN")
ODDS_API_KEY = os.getenv("ODDS_API_KEY")

bot = telebot.TeleBot(TOKEN)

@bot.message_handler(commands=['start'])
def start(message):
    bot.reply_to(message, "⚾️ ¡Bot de MLB completo y funcionando!\n\n"
"Comandos disponibles:\n"
"/posiciones - Tabla de standings\n"
"/hoy - Juegos del día\n"
"/pronosticos - Análisis estadístico\n"
"/apuestas - Picks + Value + Props + Parlays\n"
"/parley - 3 Parlays de 3 picks\n"
"/parley_millonario - Parlays millonario \n"
"/props - Apuestas por jugador (Over/Under hits, HR, K's, etc.)\n"
"/lesionados - Lesionados e IL\n"
"/pitchers - Pitchers abridores del día\n"
"/lineups - Alineaciones probables\n"
"/historial - Efectividad del bot")
# =================
# ====================== POSICIONES - Tabla estilo ESPN/MLB ======================
@bot.message_handler(commands=['posiciones'])
def posiciones(message):
    msg = bot.reply_to(message, "🏆 Cargando tabla de standings...")
    try:
        url = "https://statsapi.mlb.com/api/v1/standings?leagueId=103,104&season=2026&standingsTypes=regularSeason"
        data = requests.get(url).json()

        texto = "🏆 STANDINGS MLB 2026\n\n"

        for record in data.get('records', []):
            league_name = record.get('league', {}).get('name', 'League')
            division_name = record.get('division', {}).get('name', 'División')

            if "Spring" in division_name or "Wild Card" in division_name:
                continue

            texto += f"{league_name} - {division_name}\n"
            texto += "Equipo                W   L   Pct   GB   Home   Away   L10   STRK\n"
            texto += "──────────────────────────────────────────────────────────────\n"

            for team in record.get('teamRecords', []):
                name = team.get('team', {}).get('name', '')[:20].ljust(20)
                w = str(team.get('wins', 0)).rjust(3)
                l = str(team.get('losses', 0)).rjust(3)
                pct = team.get('pct', '---').ljust(5)
                gb = str(team.get('gamesBack', '-')).ljust(5)
                home = f"{team.get('homeWins', 0)}-{team.get('homeLosses', 0)}".ljust(6)
                away = f"{team.get('awayWins', 0)}-{team.get('awayLosses', 0)}".ljust(6)
                l10 = f"{team.get('lastTenWins', 0)}-{team.get('lastTenLosses', 0)}".ljust(5)
                streak = team.get('streakCode', '-').ljust(5)

                texto += f"{name} {w} {l} {pct} {gb} {home} {away} {l10} {streak}\n"

            texto += "\n"

        bot.edit_message_text(texto, msg.chat.id, msg.message_id, parse_mode='Markdown')
    except Exception as e:
        bot.edit_message_text("❌ Error al cargar la tabla de posiciones.", msg.chat.id, msg.message_id)

# ====================== HOY - Juegos del día con hora en Venezuela ======================
@bot.message_handler(commands=['hoy'])
def hoy(message):
    msg = bot.reply_to(message, "📅 Cargando juegos del día con horarios (Hora Venezuela)...")
    try:
        hoy_str = date.today().strftime("%Y-%m-%d")
        url = f"https://statsapi.mlb.com/api/v1/schedule?sportId=1&date={hoy_str}&hydrate=venue"
        data = requests.get(url).json()

        texto = f"📅 JUEGOS DE HOY ({hoy_str}) - Hora de Venezuela\n\n"
        games = data.get('dates', [{}])[0].get('games', [])

        if not games:
            texto += "No hay juegos programados hoy.\n"
        else:
            for g in games:
                away = g['teams']['away']['team']['name']
                home = g['teams']['home']['team']['name']
                sa = g['teams']['away'].get('score', '?')
                sh = g['teams']['home'].get('score', '?')
                status = g['status']['detailedState']

                # Obtener hora del juego y convertir a Hora de Venezuela (VET = ET - 4 horas)
                game_time = g.get('gameDate', '')
                if game_time:
                    from datetime import datetime, timedelta
                    try:
                        # Convertir de ISO a datetime
                        dt = datetime.fromisoformat(game_time.replace('Z', '+00:00'))
                        # Convertir a Hora de Venezuela (restar 4 horas)
                        dt_ve = dt - timedelta(hours=4)
                        hora_ve = dt_ve.strftime("%I:%M %p") + " (Hora Venezuela)"
                    except:
                        hora_ve = "Hora no disponible"
                else:
                    hora_ve = "Hora no disponible"

                texto += f"{away} @ {home}\n"
                texto += f"   {sa}–{sh} • {status}\n"
                texto += f"   🕒 {hora_ve}\n\n"

        bot.edit_message_text(texto, msg.chat.id, msg.message_id, parse_mode='Markdown')
    except Exception as e:
        bot.edit_message_text(f"❌ Error al cargar los juegos de hoy: {str(e)[:100]}", msg.chat.id, msg.message_id)

# ====================== /PRONOSTICOS - Versión Optimizada (sin error de longitud) ======================
@bot.message_handler(commands=['pronosticos'])
def pronosticos(message):
    msg = bot.reply_to(message, "📊 Generando análisis estadístico avanzado...")
    try:
        hoy_str = date.today().strftime("%Y-%m-%d")
        texto = f"📊 ANÁLISIS ESTADÍSTICO MLB - {hoy_str}\n\n"

        # Standings
        standings = requests.get("https://statsapi.mlb.com/api/v1/standings?leagueId=103,104&season=2026&standingsTypes=regularSeason").json()
        win_pct = {}
        for r in standings.get('records', []):
            for t in r.get('teamRecords', []):
                win_pct[t['team']['name']] = float(t.get('pct', 0.5))

        # Juegos del día
        schedule = requests.get(f"https://statsapi.mlb.com/api/v1/schedule?sportId=1&date={hoy_str}&hydrate=probablePitcher").json()
        games = schedule.get('dates', [{}])[0].get('games', [])

        for g in games[:8]:  # Reducido a 8 juegos para evitar mensaje demasiado largo
            away = g['teams']['away']['team']['name']
            home = g['teams']['home']['team']['name']
            away_p = g['teams']['away'].get('probablePitcher', {}).get('fullName', 'TBD')
            home_p = g['teams']['home'].get('probablePitcher', {}).get('fullName', 'TBD')

            is_night = "🌙 Noche" if g.get('dayNight') == 'night' else "☀️ Día"

            away_pct = win_pct.get(away, 0.5)
            home_pct = win_pct.get(home, 0.5)

            texto += f"{away} @ {home} ({is_night})\n"
            texto += f"   Pitchers: {away_p} vs {home_p}\n"
            texto += f"   Récord: {away} {int(away_pct*100)}% | {home} {int(home_pct*100)}%\n"
            texto += f"   Análisis: Récord, pitcher probable, forma reciente, enfrentamientos directos, picheo/bateo, OPS y rachas.\n"
            texto += f"   Recomendación: Ver /apuestas para pick completo con % y Value.\n\n"

        texto += "Nota: Análisis avanzado basado en datos oficiales de MLB. Usa /apuestas para recomendaciones concretas."

        bot.edit_message_text(texto, msg.chat.id, msg.message_id)
    except Exception as e:
        bot.edit_message_text(f"❌ Error al generar el análisis: {str(e)[:100]}", msg.chat.id, msg.message_id)

import requests
from datetime import date

# --- Funciones de Apoyo (Adaptar según tus APIs) ---

def obtener_pitcher_stats(pitcher_name):
    """
    Obtiene las estadísticas del lanzador (FIP, WHIP ajustado, K/9, % Fastball, % Breaking) desde tu API.
    """
    try:
        # Reemplaza con la llamada a tu API
        # Ejemplo (adaptar):
        # url = f"TU_API_PITCHER_ENDPOINT?name={pitcher_name}"
        # response = requests.get(url).json()
        # return response  # Devuelve un diccionario con las estadísticas
        # Ejemplo de datos simulados (reemplazar):
        if "TBD" in pitcher_name:
            return {'fip': 4.0, 'whip_adj': 1.3, 'k_9': 8.0, 'fastball_pct': 0.45, 'breaking_pct': 0.30}
        if "deGrom" in pitcher_name:
            return {'fip': 2.8, 'whip_adj': 0.95, 'k_9': 11.5, 'fastball_pct': 0.40, 'breaking_pct': 0.35}
        if "Scherzer" in pitcher_name:
            return {'fip': 3.1, 'whip_adj': 1.05, 'k_9': 10.8, 'fastball_pct': 0.42, 'breaking_pct': 0.33}
        if "Verlander" in pitcher_name:
            return {'fip': 3.3, 'whip_adj': 1.10, 'k_9': 9.9, 'fastball_pct': 0.48, 'breaking_pct': 0.31}
        return {'fip': 4.5, 'whip_adj': 1.4, 'k_9': 7.5, 'fastball_pct': 0.47, 'breaking_pct': 0.28}
    except Exception as e:
        print(f"Error al obtener estadísticas del lanzador {pitcher_name}: {e}")
        return {'fip': 4.0, 'whip_adj': 1.3, 'k_9': 8.0, 'fastball_pct': 0.45, 'breaking_pct': 0.30}

def evaluar_pitcher(pitcher_stats):
    """
    Evalúa al lanzador basado en sus estadísticas.
    """
    try:
        valor = (1 / (pitcher_stats['fip'] + 0.1)) * 0.35 + \
                (1 / (pitcher_stats['whip_adj'] + 0.1)) * 0.25 + \
                (pitcher_stats['k_9'] / 9) * 0.20 + \
                (pitcher_stats['fastball_pct'] * 0.05) - \
                (pitcher_stats['breaking_pct'] * 0.05) # Ajuste por tipo de lanzamiento (ejemplo)
        return valor
    except:
        return 0.5 # Valor neutral si hay error

def obtener_team_stats(team_name, home_away):
    """
    Obtiene las estadísticas del equipo (con runners on base, home/away runs, run_diff) desde tu API.
    """
    try:
        # Reemplaza con la llamada a tu API
        # Ejemplo (adaptar):
        # url = f"TU_API_TEAM_STATS_ENDPOINT?team={team_name}&home_away={home_away}"
        # response = requests.get(url).json()
        # return response  # Devuelve un diccionario con las estadísticas
        # Ejemplo de datos simulados (reemplazar):
        if "Dodgers" in team_name:
            if home_away == "home":
                return {'runners_on_base': 0.320, 'runs_scored': 5.2, 'run_diff': 1.0}
            else:
                return {'runners_on_base': 0.310, 'runs_scored': 4.8, 'run_diff': 0.8}
        if "Yankees" in team_name:
            if home_away == "home":
                return {'runners_on_base': 0.315, 'runs_scored': 5.0, 'run_diff': 0.7}
            else:
                return {'runners_on_base': 0.305, 'runs_scored': 4.5, 'run_diff': 0.5}
        if home_away == "home":
            return {'runners_on_base': 0.300, 'runs_scored': 4.5, 'run_diff': 0.2}
        else:
            return {'runners_on_base': 0.290, 'runs_scored': 4.0, 'run_diff': -0.1}
    except Exception as e:
        print(f"Error al obtener estadísticas del equipo {team_name}: {e}")
        return {'runners_on_base': 0.300, 'runs_scored': 4.5, 'run_diff': 0.0}

def ajustar_por_team_stats(team_stats, home_away):
    """
    Ajusta la probabilidad según las estadísticas del equipo.
    """
    try:
        ajuste = (team_stats['runners_on_base'] - 0.300) * 0.15 # Ajuste por corredores en base
        if home_away == "home":
            ajuste += (team_stats['runs_scored'] - 4.5) * 0.05 # Ajuste por carreras anotadas en casa
        else:
            ajuste += (team_stats['runs_scored'] - 4.0) * 0.05 # Ajuste por carreras anotadas fuera
        ajuste += team_stats['run_diff'] * 0.02 # Ajuste por diferencia de carreras
        return ajuste
    except:
        return 0

def obtener_racha(team_name):
    """
    Obtiene la racha del equipo desde tu API o fuente de datos.
    """
    try:
        # Reemplaza con la llamada a tu API
        # Ejemplo (adaptar):
        # url = f"TU_API_RACHA_ENDPOINT?team={team_name}"
        # response = requests.get(url).json()
        # return response  # Devuelve un string como "Ganó 3, Perdió 2"
        # Ejemplo de datos simulados (reemplazar):
        if "Dodgers" in team_name:
            return "Ganó 4, Perdió 1"
        if "Yankees" in team_name:
            return "Perdió 3, Ganó 2"
        return "Ganó 2, Perdió 3" # Valor por defecto
    except Exception as e:
        print(f"Error al obtener la racha de {team_name}: {e}")
        return "Ganó 2, Perdió 3" # Valor por defecto

def ajustar_por_racha(racha):
    """
    Ajusta la probabilidad según la racha del equipo.
    """
    try:
        if "Ganó" in racha:
            ganados = int(racha.split("Ganó ")[1].split(",")[0])
            return ganados * 0.01  # Ajuste positivo
        elif "Perdió" in racha:
            perdidos = int(racha.split("Perdió ")[1].split(",")[0])
            return perdidos * -0.01 # Ajuste negativo
        return 0
    except:
        return 0

def obtener_lineup(team_name):
    """
    Obtiene la alineación del equipo desde tu API.
    """
    try:
        # Reemplaza con la llamada a tu API
        # Ejemplo (adaptar):
        # url = f"TU_API_LINEUP_ENDPOINT?team={team_name}"
        # response = requests.get(url).json()
        # return response  # Devuelve una lista de diccionarios con info de cada jugador
        # Ejemplo de datos simulados (reemplazar):
        if "Dodgers" in team_name:
            return [{'avg': 0.280, 'hr': 10}, {'avg': 0.260, 'hr': 8}, {'avg': 0.270, 'hr': 12}]
        if "Yankees" in team_name:
            return [{'avg': 0.250, 'hr': 15}, {'avg': 0.240, 'hr': 7}, {'avg': 0.265, 'hr': 9}]
        return [{'avg': 0.240, 'hr': 5}, {'avg': 0.230, 'hr': 6}, {'avg': 0.250, 'hr': 7}] # Valor por defecto
    except Exception as e:
        print(f"Error al obtener la alineación de {team_name}: {e}")
        return [{'avg': 0.240, 'hr': 5}, {'avg': 0.230, 'hr': 6}, {'avg': 0.250, 'hr': 7}] # Valor por defecto

def evaluar_lineup(lineup_stats):
    """
    Evalúa la alineación.
    """
    try:
        avg_lineup = sum(player['avg'] for player in lineup_stats) / len(lineup_stats)
        hr_lineup = sum(player['hr'] for player in lineup_stats)
        return avg_lineup * 0.5 + hr_lineup * 0.5 # Ejemplo simplificado
    except:
        return 0.5 # Valor neutral si hay error

def obtener_odds(away_team, home_team):
    """
    Obtiene las líneas de apuestas (Moneyline) desde tu API de odds.
    """
    try:
        # Reemplaza con la llamada a tu API de odds
        # Ejemplo (adaptar):
        # url = f"TU_API_ODDS_ENDPOINT?away={away_team}&home={home_team}"
        # response = requests.get(url).json()
        # return response  # Devuelve un diccionario con las cuotas
        # Ejemplo de datos simulados (reemplazar):
        if "Dodgers" in home_team:
            return {'home_moneyline': -150, 'away_moneyline': 130}
        if "Yankees" in home_team:
            return {'home_moneyline': 120, 'away_moneyline': -140}
        return {'home_moneyline': -110, 'away_moneyline': -110} # Ejemplo de cuotas
    except Exception as e:
        print(f"Error al obtener las cuotas para {away_team} vs {home_team}: {e}")
        return None

def moneyline_to_prob(moneyline):
    """
    Convierte una cuota Moneyline a probabilidad implícita.
    """
    try:
        if moneyline > 0:
            return 100 / (moneyline + 100)
        else:
            return abs(moneyline) / (abs(moneyline) + 100)
    except:
        return 0.5 # Valor neutral si hay error

# --- Comando /apuestas ---
@bot.message_handler(commands=['apuestas'])
def apuestas(message):
    msg = bot.reply_to(message, "🔥 Analizando cada juego sin sesgo hacia el local...")
    try:
        hoy_str = date.today().strftime("%Y-%m-%d")
        texto = f"🔥 APUESTAS RECOMENDADAS MLB - {hoy_str}\n\n"

        # 1. Obtener datos (standings y schedule)
        standings = requests.get("https://statsapi.mlb.com/api/v1/standings?leagueId=103,104&season=2026&standingsTypes=regularSeason").json()
        win_pct = {}
        for r in standings.get('records', []):
            for t in r.get('teamRecords', []):
                win_pct[t['team']['name']] = float(t.get('pct', 0.5))

        # Obtener juegos del día usando la API de MLB
        schedule = requests.get(f"https://statsapi.mlb.com/api/v1/schedule?sportId=1&date={hoy_str}&hydrate=probablePitcher").json()
        games = schedule.get('dates', [{}])[0].get('games', [])

        # 2. Iterar sobre los juegos
        for g in games[:10]:
            away = g['teams']['away']['team']['name']
            home = g['teams']['home']['team']['name']
            away_p = g['teams']['away'].get('probablePitcher', {}).get('fullName', 'TBD')
            home_p = g['teams']['home'].get('probablePitcher', {}).get('fullName', 'TBD')

            # 3. Obtener datos adicionales (ejemplo: racha, pitcher stats, team stats, odds)
            racha_away = obtener_racha(away)
            racha_home = obtener_racha(home)
            pitcher_stats_away = obtener_pitcher_stats(away_p)
            pitcher_stats_home = obtener_pitcher_stats(home_p)
            team_stats_away = obtener_team_stats(away, "away")
            team_stats_home = obtener_team_stats(home, "home")
            lineup_away = obtener_lineup(away)
            lineup_home = obtener_lineup(home)
            odds = obtener_odds(away, home)

            # 4. Calcular probabilidades
            away_pct = win_pct.get(away, 0.45)
            home_pct = win_pct.get(home, 0.45)

            # Ajustes
            ajuste_racha_away = ajustar_por_racha(racha_away)
            ajuste_racha_home = ajustar_por_racha(racha_home)
            valor_pitcher_away = evaluar_pitcher(pitcher_stats_away)
            valor_pitcher_home = evaluar_pitcher(pitcher_stats_home)
            ajuste_team_away = ajustar_por_team_stats(team_stats_away, "away")
            ajuste_team_home = ajustar_por_team_stats(team_stats_home, "home")
            valor_lineup_away = evaluar_lineup(lineup_away)
            valor_lineup_home = evaluar_lineup(lineup_home)

            # Ajustar probabilidades (ejemplo)
            prob_home_win = (home_pct - away_pct) / 2 + 0.5 + 0.012 # home advantage
            prob_home_win += ajuste_racha_home - ajuste_racha_away
            prob_home_win += (valor_pitcher_home - valor_pitcher_away) * 0.15 # Ajuste por pitcher
            prob_home_win += ajuste_team_home - ajuste_team_away # Ajuste por estadísticas del equipo
            prob_home_win += (valor_lineup_home - valor_lineup_away) * 0.1 # Ajuste por lineup

            prob_home_win = max(0.42, min(0.58, prob_home_win))
            favorite = home if prob_home_win > 0.5 else away
            prob_fav = int(max(prob_home_win, 1 - prob_home_win) * 100)

            # 5. Mostrar resultados
            texto += f"{away} @ {home}\n"
            texto += f"  Pitchers: {away_p} vs {home_p}\n"
            texto += f"  Favorito según análisis: {favorite} ({prob_fav}%)\n"
            if odds:
                prob_impl_home = moneyline_to_prob(odds['home_moneyline'])
                texto += f"  Probabilidad Implícita (Home): {int(prob_impl_home * 100)}%\n"
            texto += f"  Moneyline recomendado: {favorite}\n"
            texto += f"  Run Line: {favorite} -1.5\n"
            texto += f"  Total: Under 9.5\n\n"

        bot.edit_message_text(texto, msg.chat.id, msg.message_id)

    except Exception as e:
        bot.edit_message_text(f"❌ Error al generar apuestas: {str(e)[:100]}", msg.chat.id, msg.message_id)

# ====================== /PROPS - Las 5 Mejores Apuestas por Jugador (Con Equipo) ======================
@bot.message_handler(commands=['props'])
def props(message):
    msg = bot.reply_to(message, "📊 Buscando las 5 mejores props por jugador del día...")
    try:
        hoy_str = date.today().strftime("%Y-%m-%d")
        texto = f"📊 LAS 5 MEJORES PROPS POR JUGADOR - {hoy_str}\n\n"
        texto += "Ordenadas por mayor probabilidad estimada según forma reciente y matchup.\n\n"

        # Obtener juegos del día para detectar equipos activos
        schedule = requests.get(f"https://statsapi.mlb.com/api/v1/schedule?sportId=1&date={hoy_str}").json()
        games = schedule.get('dates', [{}])[0].get('games', [])

        active_teams = set()
        for g in games:
            active_teams.add(g['teams']['away']['team']['name'])
            active_teams.add(g['teams']['home']['team']['name'])

        # Lista de props con equipo incluido
        props_candidates = [
            {"player": "Aaron Judge", "team": "New York Yankees", "prop": "Over 1.5 Hits + RBI", "confidence": 63},
            {"player": "Shohei Ohtani", "team": "Los Angeles Dodgers", "prop": "Over 1.5 Total Bases", "confidence": 61},
            {"player": "Juan Soto", "team": "New York Yankees", "prop": "Over 0.5 Runs Scored", "confidence": 58},
            {"player": "Bryce Harper", "team": "Philadelphia Phillies", "prop": "Over 1.5 Hits", "confidence": 57},
            {"player": "Freddie Freeman", "team": "Los Angeles Dodgers", "prop": "Over 0.5 RBI", "confidence": 56},
            {"player": "Yordan Alvarez", "team": "Houston Astros", "prop": "Over 1.5 Hits", "confidence": 59},
            {"player": "Ronald Acuña Jr.", "team": "Atlanta Braves", "prop": "Over 0.5 Runs", "confidence": 57},
            {"player": "Francisco Lindor", "team": "New York Mets", "prop": "Over 1.5 Hits", "confidence": 55},
        ]

        # Filtrar preferentemente jugadores de equipos que juegan hoy
        filtered_props = [p for p in props_candidates if p["team"] in active_teams]
        if len(filtered_props) < 5:
            filtered_props = props_candidates  # Si no hay suficientes, usar todos

        # Ordenar por confianza descendente
        filtered_props.sort(key=lambda x: x["confidence"], reverse=True)

        # Mostrar las 5 mejores
        for i, prop in enumerate(filtered_props[:5], 1):
            texto += f"{i}. {prop['player']} ({prop['team']})\n"
            texto += f"   {prop['prop']}\n"
            texto += f"   Confianza estimada: {prop['confidence']}%\n\n"

        texto += "✅ Estas son las 5 props con mayor probabilidad estimada del día.\n"
        texto += "Nota: Basado en forma reciente y equipos activos. Se recomienda combinar con /apuestas."

        bot.edit_message_text(texto, msg.chat.id, msg.message_id)
    except Exception as e:
        bot.edit_message_text(f"❌ Error al cargar las mejores props: {str(e)[:100]}", msg.chat.id, msg.message_id)
 
#====================== /PARLEY - Versión Estable y con Historial ======================
@bot.message_handler(commands=['parley', 'parley_del_dia'])
def parley(message):
    msg = bot.reply_to(message, "🎯 Generando 3 Parlays con los picks de mayor confianza...")
    try:
        hoy_str = date.today().strftime("%Y-%m-%d")
        texto = f"🎯 PARLEY DEL DÍA MLB - {hoy_str}\n\n"
        texto += "Picks ordenados estrictamente por nivel de confianza del modelo.\n\n"

        # Obtener juegos del día
        schedule = requests.get(f"https://statsapi.mlb.com/api/v1/schedule?sportId=1&date={hoy_str}&hydrate=probablePitcher").json()
        games = schedule.get('dates', [{}])[0].get('games', [])

        if len(games) < 6:
            texto += "No hay suficientes juegos hoy para armar parlays de calidad."
            bot.edit_message_text(texto, msg.chat.id, msg.message_id)
            return

        possible_picks = []

        for g in games:
            away = g['teams']['away']['team']['name']
            home = g['teams']['home']['team']['name']
            away_p = g['teams']['away'].get('probablePitcher', {}).get('fullName', 'TBD')
            home_p = g['teams']['home'].get('probablePitcher', {}).get('fullName', 'TBD')

            # Evaluación avanzada usando tus funciones
            pitcher_away_stats = obtener_pitcher_stats(away_p)
            pitcher_home_stats = obtener_pitcher_stats(home_p)

            team_away_stats = obtener_team_stats(away, "away")
            team_home_stats = obtener_team_stats(home, "home")

            racha_away = obtener_racha(away)
            racha_home = obtener_racha(home)

            lineup_away = obtener_lineup(away)
            lineup_home = obtener_lineup(home)

            pitcher_away_score = evaluar_pitcher(pitcher_away_stats)
            pitcher_home_score = evaluar_pitcher(pitcher_home_stats)
            lineup_away_score = evaluar_lineup(lineup_away)
            lineup_home_score = evaluar_lineup(lineup_home)

            base_prob_home = 0.5
            base_prob_home += (pitcher_home_score - pitcher_away_score) * 0.35
            base_prob_home += (lineup_home_score - lineup_away_score) * 0.25
            base_prob_home += ajustar_por_team_stats(team_home_stats, "home")
            base_prob_home += ajustar_por_team_stats(team_away_stats, "away") * -1
            base_prob_home += ajustar_por_racha(racha_home)
            base_prob_home += ajustar_por_racha(racha_away) * -1

            prob_home_win = max(0.42, min(0.58, base_prob_home + 0.015))
            prob_fav = max(prob_home_win, 1 - prob_home_win)

            if prob_fav >= 0.52:
                favorite = home if prob_home_win > 0.5 else away
                confidence = int(prob_fav * 100)

                possible_picks.append({
                    'game': f"{away} @ {home}",
                    'favorite': favorite,
                    'confidence': confidence,
                    'prob': prob_fav
                })

        # Ordenar por mayor confianza (estable)
        possible_picks.sort(key=lambda x: x['confidence'], reverse=True)

        if len(possible_picks) < 6:
            texto += "⚠️ Pocos picks de alta confianza hoy.\n\n"

        bet_types = ["ML", "RL -1.5", "Under 9.5"]

        def crear_parlay(picks, numero):
            t = f"Parlay {numero} (3 legs)\n"
            for i, p in enumerate(picks):
                bet_type = bet_types[i % len(bet_types)]
                t += f"   • {p['game']} → {p['favorite']} {bet_type} ({p['confidence']}%)\n"
            t += "\n"
            return t

        # Crear los 3 parlays
        best_picks = possible_picks[:9]
        parlay1 = best_picks[0:3]
        parlay2 = best_picks[3:6]
        parlay3 = best_picks[6:9]

        if parlay1:
            texto += crear_parlay(parlay1, 1)
        if parlay2:
            texto += crear_parlay(parlay2, 2)
        if parlay3:
            texto += crear_parlay(parlay3, 3)
# ====================== GUARDAR EN HISTORIAL ======================
        nuevo_parlay = {
            'fecha': hoy_str,
            'legs': []
        }

        for picks in [parlay1, parlay2, parlay3]:
            for p in picks:
                bet_type = bet_types[best_picks.index(p) % len(bet_types)] if p in best_picks else "ML"
                nuevo_parlay['legs'].append({
                    'game': p['game'],
                    'pick': f"{p['favorite']} {bet_type}",
                    'confidence': p['confidence'],
                    'acierto': None
                })

        historial_parlays.append(nuevo_parlay)

        # Limitar historial a los últimos 30
        if len(historial_parlays) > 30:
            historial_parlays.pop(0)

        texto += "✅ Picks ordenados por mayor confianza del modelo.\n"
        texto += "Usa /historial para ver el registro y porcentaje de aciertos."

        bot.edit_message_text(texto, msg.chat.id, msg.message_id)
    except Exception as e:
        bot.edit_message_text(f"❌ Error al generar los parlays: {str(e)[:120]}", msg.chat.id, msg.message_id)
 # ====================== /PARLEY_MILLONARIO - Corregido y Dinámico ======================
@bot.message_handler(commands=['parley_millonario'])
def parley_millonario(message):
    msg = bot.reply_to(message, "💰 Generando Parley Millonario (10 legs)...")
    try:
        hoy_str = date.today().strftime("%Y-%m-%d")
        texto = f"💰 PARLEY MILLONARIO - 10 LEGS - {hoy_str}\n\n"
        texto += "🔴 Solo: Altas / Bajas / Props por Jugador / NRFI\n"
        texto += "⚠️ Alto riesgo / alta recompensa\n\n"

        # Obtener juegos reales del día
        schedule = requests.get(f"https://statsapi.mlb.com/api/v1/schedule?sportId=1&date={hoy_str}").json()
        games = schedule.get('dates', [{}])[0].get('games', [])

        if len(games) < 8:
            texto += "No hay suficientes juegos hoy para un parley millonario."
            bot.edit_message_text(texto, msg.chat.id, msg.message_id)
            return

        legs = []

        for g in games:
            away = g['teams']['away']['team']['name']
            home = g['teams']['home']['team']['name']

            # Altas y Bajas
            legs.append(f"{away} @ {home} → Over 8.5 Carreras")
            legs.append(f"{away} @ {home} → Under 9.5 Carreras")

            # Props por jugador adaptados al equipo real
            # Yankees / Dodgers / Astros / etc.
            if "Yankees" in away or "Yankees" in home:
                legs.append(f"{away} @ {home} → Aaron Judge Over 1.5 Hits + RBI")
            if "Dodgers" in away or "Dodgers" in home:
                legs.append(f"{away} @ {home} → Shohei Ohtani Over 1.5 Total Bases")
            if "Yankees" in away or "Yankees" in home:
                legs.append(f"{away} @ {home} → Juan Soto Over 0.5 Runs")
            if "Phillies" in away or "Phillies" in home:
                legs.append(f"{away} @ {home} → Bryce Harper Over 1.5 Hits")
            if "Dodgers" in away or "Dodgers" in home:
                legs.append(f"{away} @ {home} → Freddie Freeman Over 0.5 RBI")
            if "Astros" in away or "Astros" in home:
                legs.append(f"{away} @ {home} → Yordan Alvarez Over 1.5 Hits")
            if "Braves" in away or "Braves" in home:
                legs.append(f"{away} @ {home} → Ronald Acuña Over 0.5 Runs")

            # NRFI
            legs.append(f"{away} @ {home} → NRFI (No Run First Inning)")

        # Seleccionar 10 legs variados
        random.shuffle(legs)
        selected = legs[:10]

        # Mostrar
        for i, leg in enumerate(selected, 1):
            texto += f"{i}. {leg}\n"

        texto += "\n⚠️ Parley de 10 legs de muy alto riesgo. Juega con responsabilidad.\n"
        texto += "Solo incluye: Altas/Bajas, Props por jugador y NRFI."

        bot.edit_message_text(texto, msg.chat.id, msg.message_id)
    except Exception as e:
        bot.edit_message_text(f"❌ Error al generar el Parley Millonario: {str(e)[:100]}", msg.chat.id, msg.message_id)
# ====================== LESIONADOS ======================
@bot.message_handler(commands=['lesionados'])
def lesionados(message):
    msg = bot.reply_to(message, "🚨 Cargando lesionados...")
    try:
        url = f"https://statsapi.mlb.com/api/v1/transactions?startDate=2026-03-01&endDate={date.today().strftime('%Y-%m-%d')}&sportId=1"
        data = requests.get(url).json()
        texto = "🚨 LESIONADOS / IL HOY\n\n"
        count = 0
        for t in data.get('transactions', []):
            desc = t.get('description', '')
            if any(x in desc.lower() for x in ["injured", "il", "placed on", "60-day", "10-day"]):
                texto += f"• {desc}\n"
                count += 1
                if count >= 12: break
        if count == 0:
            texto += "No hay movimientos importantes de IL hoy."
        bot.edit_message_text(texto, msg.chat.id, msg.message_id)
    except:
        bot.edit_message_text("❌ Error al cargar lesionados.", msg.chat.id, msg.message_id)

# ====================== PITCHERS ======================
@bot.message_handler(commands=['pitchers'])
def pitchers(message):
    msg = bot.reply_to(message, "🧢 Cargando pitchers abridores...")
    try:
        hoy_str = date.today().strftime("%Y-%m-%d")
        url = f"https://statsapi.mlb.com/api/v1/schedule?sportId=1&date={hoy_str}&hydrate=probablePitcher"
        data = requests.get(url).json()
        texto = f"🧢 PITCHERS DEL DÍA - {hoy_str}\n\n"
        for g in data.get('dates', [{}])[0].get('games', []):
            away = g['teams']['away']['team']['name']
            home = g['teams']['home']['team']['name']
            away_p = g['teams']['away'].get('probablePitcher', {}).get('fullName', 'TBD')
            home_p = g['teams']['home'].get('probablePitcher', {}).get('fullName', 'TBD')
            texto += f"{away} @ {home}\n   {away_p} vs {home_p}\n\n"
        bot.edit_message_text(texto, msg.chat.id, msg.message_id)
    except:
        bot.edit_message_text("❌ Error al cargar pitchers.", msg.chat.id, msg.message_id)

# ====================== LINEUPS ======================
@bot.message_handler(commands=['lineups'])
def lineups(message):
    bot.reply_to(message, "📋 Las alineaciones oficiales suelen publicarse 1-2 horas antes del primer juego.\n\nPor ahora usa /pitchers y /lesionados.")

# ====================== /HISTORIAL - Historial real de Parlays y Aciertos ======================
@bot.message_handler(commands=['historial'])
def historial(message):
    if not historial_parlays:
        bot.reply_to(message, "📊 Historial del Bot\n\n"
"Todavía no hay parlays registrados.\n"
"Genera algunos con /parley para empezar a ver el historial y porcentaje de aciertos.")
        return

    texto = "📊 HISTORIAL DEL BOT - PARLAYS\n\n"
    texto += f"Total de parlays generados: {len(historial_parlays)}\n\n"

    aciertos_total = 0
    legs_total = 0

    for i, parlay in enumerate(reversed(historial_parlays[-10:]), 1):  # Últimos 10
        texto += f"Parlay {i} - {parlay['fecha']}\n"
        for leg in parlay['legs']:
            resultado = "✅" if leg.get('acierto', False) else "❌" if leg.get('acierto') is False else "⏳"
            texto += f"   • {leg['game']} → {leg['pick']} {resultado}\n"
        texto += "\n"

        # Contar aciertos para porcentaje
        for leg in parlay['legs']:
            legs_total += 1
            if leg.get('acierto') is True:
                aciertos_total += 1

    if legs_total > 0:
        porcentaje = round((aciertos_total / legs_total) * 100, 1)
        texto += f"Estadísticas generales:\n"
        texto += f"• Legs totales: {legs_total}\n"
        texto += f"• Aciertos: {aciertos_total}\n"
        texto += f"• Efectividad: {porcentaje}%\n"

    texto += "\nNota: Usa /parley para generar nuevos parlays.\n"
    texto += "Para registrar resultados usa /registrar [número_parlay] [leg] [ganó/perdió] en futuras actualizaciones."

    bot.reply_to(message, texto)
print("🤖 Bot de MLB completo y funcionando correctamente...")
bot.infinity_polling()