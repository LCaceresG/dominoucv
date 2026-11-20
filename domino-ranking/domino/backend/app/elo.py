"""Cálculo ELO para dominó (parejas 2 vs 2, también sirve 1 vs 1).

Reglas:
- Rating inicial: 1000.
- Rating de equipo = promedio de sus jugadores.
- Cada jugador recibe su propio delta según su K (más alto mientras es nuevo).
- Multiplicador por diferencia de puntos: ganar 100-20 vale más que 100-90.
- Empate: cuenta como 0.5 para cada lado y sin multiplicador.

Este módulo es puro (sin base de datos), así es fácil de probar y de ajustar.
"""
from dataclasses import dataclass

INITIAL_RATING = 1000.0
PROVISIONAL_GAMES = 20  # hasta este número de partidas el rating es "provisional"


@dataclass
class Participant:
    rating: float
    games: int


def expected_score(rating_a: float, rating_b: float) -> float:
    """Probabilidad esperada de que A le gane a B."""
    return 1.0 / (1.0 + 10 ** ((rating_b - rating_a) / 400.0))


def k_factor(games_played: int) -> float:
    """Jugadores nuevos se mueven más rápido; los veteranos son más estables."""
    if games_played < PROVISIONAL_GAMES:
        return 40.0
    if games_played < 60:
        return 28.0
    return 20.0


def margin_multiplier(points_winner: int, points_loser: int, target: int = 100) -> float:
    """Entre 1.0 (ganó muy apretado) y 1.5 (paliza)."""
    if target <= 0:
        return 1.0
    diff = max(points_winner - points_loser, 0)
    return 1.0 + 0.5 * min(diff / target, 1.0)


def team_rating(team: list[Participant]) -> float:
    return sum(p.rating for p in team) / len(team)


def compute_deltas(
    team_a: list[Participant],
    team_b: list[Participant],
    points_a: int,
    points_b: int,
    target: int = 100,
    use_margin: bool = True,
) -> tuple[list[float], list[float]]:
    """Devuelve los cambios de rating (delta) de cada jugador de A y de B.

    use_margin=False: el marcador no influye (solo ganar, empatar o perder), como en FIDE.
    """
    draw = points_a == points_b
    a_won = points_a > points_b
    exp_a = expected_score(team_rating(team_a), team_rating(team_b))
    exp_b = 1.0 - exp_a
    score_a = 0.5 if draw else (1.0 if a_won else 0.0)
    score_b = 1.0 - score_a

    if draw or not use_margin:
        mult = 1.0
    else:
        winner_pts, loser_pts = (points_a, points_b) if a_won else (points_b, points_a)
        mult = margin_multiplier(winner_pts, loser_pts, target)

    deltas_a = [round(k_factor(p.games) * mult * (score_a - exp_a), 2) for p in team_a]
    deltas_b = [round(k_factor(p.games) * mult * (score_b - exp_b), 2) for p in team_b]
    return deltas_a, deltas_b
