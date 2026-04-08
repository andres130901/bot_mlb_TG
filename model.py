# core/model.py
import math
from functools import lru_cache
from typing import Dict, Any, Optional

import requests

MLB_BASE = "https://statsapi.mlb.com/api/v1"


class MLBPredictor:
    """Clase central que contiene toda la lógica de predicción y scoring de MLB"""

    def init(self):
        self.MLB_BASE = MLB_BASE

    # ====================== UTILIDADES ======================
    def logistic(self, x: float) -> float:
        return 1 / (1 + math.exp(-x))

    def clamp(self, value: float, low: float, high: float) -> float:
        return max(low, min(high, value))

    def parse_streak(self, streak_code: str) -> float:
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

    def confidence_label(self, prob: float) -> str:
        if prob >= 0.62:
            return "Alta"
        if prob >= 0.56:
            return "Media"
        return "Baja"

    def moneyline_to_prob(self, moneyline: Any) -> Optional[float]:
        try:
            ml = int(moneyline)
            if ml > 0:
                return 100 / (ml + 100)
            return abs(ml) / (abs(ml) + 100)
        except Exception:
            return None

    def american_to_decimal(self, american_odds: Any) -> Optional[float]:
        try:
            american_odds = float(american_odds)
            if american_odds > 0:
                return 1 + (american_odds / 100)
            return 1 + (100 / abs(american_odds))
        except Exception:
            return None

    def calcular_ev(self, prob_model: float, american_odds: Any) -> Optional[float]:
        """Calcula Expected Value por unidad apostada"""
        dec = self.american_to_decimal(american_odds)
        if dec is None:
            return None
        try:
            p = float(prob_model)
            ev = (p * (dec - 1)) - (1 - p)
            return round(ev, 4)
        except Exception:
            return None

    def grade_por_ev(self, ev: Optional[float]) -> str:
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

    def stake_por_ev(self, ev: Optional[float]) -> str:
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

    # ====================== PITCHER STATS ======================
    @lru_cache(maxsize=256)
    def obtener_stats_pitcher_reales(self, person_id: Optional[int], season=None):
        """Obtiene estadísticas reales del pitcher desde MLB API"""
        base = {"era": 4.20, "whip": 1.30, "so9": 8.2, "ip": 0.0, "sample_ok": False}
        if not person_id:
            return base

        if season is None:
            season = 2026  # Cambia según temporada actual si es necesario

        url = f"{self.MLB_BASE}/people/{person_id}/stats"
        params = {
            "stats": "season",
            "group": "pitching",
            "season": season,
            "gameType": "R"
        }

        try:
            r = requests.get(url, params=params, timeout=15)
            r.raise_for_status()
            data = r.json()

            stats_list = data.get("stats", [])
            if not stats_list:
                return base

            first_split = stats_list[0].get("splits", [{}])[0]
            stat = first_split.get("stat", {})
            era = float(stat.get("era") or 4.20)
            whip = float(stat.get("whip") or 1.30)
            innings_pitched = float(str(stat.get("inningsPitched", "0")).replace(",", ""))
            strikeouts = int(stat.get("strikeOuts", 0))
            so9 = (strikeouts * 9 / innings_pitched) if innings_pitched > 0 else 8.2

            return {
                "era": round(era, 2),
                "whip": round(whip, 2),
                "so9": round(so9, 2),
                "ip": round(innings_pitched, 1),
                "sample_ok": innings_pitched >= 10
            }
        except Exception:
            return base

    def score_pitcher_real(self, stats: Dict) -> float:
        """Score del pitcher basado en ERA, WHIP, K/9 y sample size"""
        era = float(stats.get("era", 4.20))
        whip = float(stats.get("whip", 1.30))
        so9 = float(stats.get("so9", 8.2))
        ip = float(stats.get("ip", 0.0))
        sample_ok = stats.get("sample_ok", False)

        score = 0.0

        # ERA
        if era <= 2.50: score += 0.60
        elif era <= 3.20: score += 0.40
        elif era <= 3.80: score += 0.20
        elif era >= 5.20: score -= 0.42
        elif era >= 4.60: score -= 0.25

        # WHIP
        if whip <= 1.00: score += 0.42
        elif whip <= 1.12: score += 0.28
        elif whip <= 1.24: score += 0.14
        elif whip >= 1.45: score -= 0.28
        elif whip >= 1.35: score -= 0.14

        # K/9
        if so9 >= 11.0: score += 0.24
        elif so9 >= 9.5: score += 0.16
        elif so9 >= 8.5: score += 0.08
        elif so9 <= 6.0: score -= 0.14

        # Penalty por sample pequeño
        if not sample_ok or ip < 8:
            score *= 0.65
        elif ip < 15:
            score *= 0.82

        return round(score, 3)

    # ====================== PREDICCIONES PRINCIPALES ======================
    def calcular_probabilidad_local_pro(self, away_team: str, home_team: str, standings: Dict,
                                       away_pitcher="TBD", home_pitcher="TBD",
                                       away_pitcher_stats=None, home_pitcher_stats=None,
                                       weather=None) -> float:
        """Calcula probabilidad de victoria del equipo local"""
        away = standings.get(away_team, {})
        home = standings.get(home_team, {})

        if not away or not home:
            return 0.50

        # Diferencias clave
        diff_win_pct = home.get("win_pct", 0.5) - away.get("win_pct", 0.5)
        diff_split = home.get("home_win_pct", 0.5) - away.get("away_win_pct", 0.5)
        diff_last10 = home.get("last10_win_pct", 0.5) - away.get("last10_win_pct", 0.5)
        diff_run_diff = (home.get("run_diff", 0) - away.get("run_diff", 0)) / 45.0
        diff_streak = self.parse_streak(home.get("streak", "")) - self.parse_streak(away.get("streak", ""))
        diff_runs_scored = (home.get("runs_scored", 4.5) - away.get("runs_scored", 4.5)) / 2.4
        diff_runs_allowed = (away.get("runs_allowed", 4.5) - home.get("runs_allowed", 4.5)) / 2.4

        away_stats = away_pitcher_stats or {"era": 4.20, "whip": 1.30, "so9": 8.2, "ip": 0.0, "sample_ok": False}
        home_stats = home_pitcher_stats or {"era": 4.20, "whip": 1.30, "so9": 8.2, "ip": 0.0, "sample_ok": False}

        diff_pitcher = self.score_pitcher_real(home_stats) - self.score_pitcher_real(away_stats)

        score = 0.12
        score += diff_win_pct * 4.8
        score += diff_split * 3.6
        score += diff_last10 * 2.0
        score += diff_run_diff * 2.6
        score += diff_streak * 1.3
        score += diff_runs_scored * 1.5
        score += diff_runs_allowed * 1.5
        score += diff_pitcher * 3.8

        # Ajuste por clima (Moneyline)
        if weather:
            if weather.get("precip_mm", 0) >= 1.0:
                score -= 0.01
            if weather.get("temp_c") is not None and weather.get("temp_c") <= 8:
                score -= 0.01

        if away_pitcher == "TBD":
            score += 0.10
        if home_pitcher == "TBD":
            score -= 0.10

        # Force a little separation if score is too close to zero
        if -0.08 < score < 0.08:
            if score >= 0:
                score = 0.11
            else:
                score = -0.11

        prob = self.logistic(score)
        return self.clamp(prob, 0.34, 0.66)

    def obtener_pick_juego_pro(self, away_team, home_team, standings,
                               away_pitcher="TBD", home_pitcher="TBD",
                               away_pitcher_stats=None, home_pitcher_stats=None,
                               weather=None):
        """Devuelve el pick recomendado para Moneyline"""
        prob_home = self.calcular_probabilidad_local_pro(
            away_team, home_team, standings,
            away_pitcher, home_pitcher,
            away_pitcher_stats, home_pitcher_stats, weather
        )

        favorito = home_team if prob_home >= 0.5 else away_team
        prob_fav = prob_home if favorito == home_team else (1 - prob_home)
        avoid = away_pitcher == "TBD" or home_pitcher == "TBD"

        return {
            "favorite": favorito,
            "prob_home": prob_home,
            "prob_favorite": prob_fav,
            "confidence_pct": round(prob_fav * 100, 1),
            "confidence_label": self.confidence_label(prob_fav),
            "avoid": avoid
        }

    def estimar_total_juego_pro(self, away_team, home_team, standings,
                                away_pitcher="TBD", home_pitcher="TBD",
                                away_pitcher_stats=None, home_pitcher_stats=None,
                                weather=None) -> float:
        """Estima el total de carreras (Over/Under)"""
        away = standings.get(away_team, {})
        home = standings.get(home_team, {})

        total = 8.6
        total += ((away.get("runs_scored", 4.5) + home.get("runs_scored", 4.5)) - 9.0) * 0.22
        total += ((away.get("runs_allowed", 4.5) + home.get("runs_allowed", 4.5)) - 9.0) * 0.18
        total += ((away.get("run_diff", 0) + home.get("run_diff", 0)) / 162.0) * 0.20

        away_stats = away_pitcher_stats or {"era": 4.20, "whip": 1.30, "so9": 8.2}
        home_stats = home_pitcher_stats or {"era": 4.20, "whip": 1.30, "so9": 8.2}

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

        total += ajuste_clima_total(weather) if 'ajuste_clima_total' in globals() else 0.0

        return round(self.clamp(total, 6.5, 12.5), 1)

    def elegir_total_pick(self, total_proyectado: float, total_line: Optional[float]):
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