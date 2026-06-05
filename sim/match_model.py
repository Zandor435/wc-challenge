"""Deliberately simple probabilistic match model.

One numeric strength rating per team. Group matches yield Win/Draw/Loss from an
Elo-style rating difference with a draw probability that shrinks as the gap
grows. Knockout matches have no draw: a single strength-adjusted win probability
(a penalty-shootout win is just a knockout win, folded into this probability).

Goals are NOT modeled with Poisson here. We only need a light goal margin to
break group ties; the final tiebreak is random. Everything is documented and
configurable via the constants below.

VENDORED UNCHANGED from the preseason engine (wc_pool/match_model.py) so the
daily win-probability reproduces the preseason baseline exactly under the same
seed and inputs.
"""

from __future__ import annotations

import numpy as np

# Elo scale: a 400-point gap ~ 10:1 expected-score ratio.
ELO_SCALE = 400.0
# Draw model: max draw rate for evenly matched teams, decaying with |rating gap|.
DRAW_BASE = 0.26
DRAW_DECAY = 350.0


def expected_score(rating_a: float, rating_b: float) -> float:
    """Elo expected score for A in [0, 1]."""
    return 1.0 / (1.0 + 10.0 ** (-(rating_a - rating_b) / ELO_SCALE))


def group_match_probs(rating_a: float, rating_b: float) -> tuple[float, float, float]:
    """Return (p_win_a, p_draw, p_win_b) for a group-stage match."""
    diff = rating_a - rating_b
    p_draw = DRAW_BASE * np.exp(-(diff / DRAW_DECAY) ** 2)
    e_a = expected_score(rating_a, rating_b)
    p_win_a = (1.0 - p_draw) * e_a
    p_win_b = (1.0 - p_draw) * (1.0 - e_a)
    return p_win_a, p_draw, p_win_b


def simulate_group_match(rating_a, rating_b, rng: np.random.Generator):
    """Return (goals_a, goals_b). Result first, then a light margin for tiebreaks."""
    p_win_a, p_draw, _ = group_match_probs(rating_a, rating_b)
    u = rng.random()
    if u < p_draw:
        g = rng.integers(0, 3)  # 0-0, 1-1, or 2-2
        return int(g), int(g)
    winner_is_a = u < p_draw + p_win_a
    margin = _sample_margin(abs(rating_a - rating_b), rng)
    loser_goals = int(rng.integers(0, 3))
    winner_goals = loser_goals + margin
    return (winner_goals, loser_goals) if winner_is_a else (loser_goals, winner_goals)


def _sample_margin(abs_diff: float, rng: np.random.Generator) -> int:
    """1-goal wins are most common; bigger gaps slightly raise the margin."""
    p3 = min(0.10 + abs_diff / 4000.0, 0.30)
    p2 = 0.30
    r = rng.random()
    if r < p3:
        return 3
    if r < p3 + p2:
        return 2
    return 1


def simulate_knockout(rating_a, rating_b, rng: np.random.Generator) -> bool:
    """Return True if A advances. No draws (shootout folded into win prob)."""
    return rng.random() < expected_score(rating_a, rating_b)
