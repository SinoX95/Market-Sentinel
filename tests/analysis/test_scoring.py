"""Tests du score technique (analysis/scoring.py)."""
import pytest

from analysis.scoring import MIN_POINTS, SCORE_WINDOW, compute_score, label_for

# Scores de référence pour chaque palier, repris de la docstring du module (BUY >= 80, HOLD 60-79, WATCH 40-59).
SCORE_BUY_THRESHOLD = 80
SCORE_HOLD_THRESHOLD = 60
SCORE_WATCH_THRESHOLD = 40


def _flat_series(price, n):
    """n cours identiques : momentum et tendance nuls, volatilité nulle, pas de retracement."""
    return [price] * n


def _rising_series(start, growth, n):
    return [start * (1 + growth) ** i for i in range(n)]


class TestLabelFor:
    def test_returns_buy_when_score_is_at_threshold(self):
        assert label_for(SCORE_BUY_THRESHOLD) == "BUY"

    def test_returns_hold_when_score_is_at_threshold(self):
        assert label_for(SCORE_HOLD_THRESHOLD) == "HOLD"

    def test_returns_watch_when_score_is_at_threshold(self):
        assert label_for(SCORE_WATCH_THRESHOLD) == "WATCH"

    def test_returns_avoid_reassess_when_score_is_below_watch_threshold(self):
        assert label_for(SCORE_WATCH_THRESHOLD - 0.1) == "AVOID/REASSESS"


class TestComputeScore:
    def test_returns_none_if_history_is_shorter_than_minimum(self):
        """
        Given un historique plus court que MIN_POINTS
        When le score est calculé
        Then None est renvoyé — un score sur trop peu de points n'aurait pas de sens statistique.
        """
        closes = _flat_series(100.0, MIN_POINTS - 1)

        result = compute_score(closes)

        assert result is None

    def test_returns_score_if_history_has_exactly_minimum_points(self):
        closes = _flat_series(100.0, MIN_POINTS)

        result = compute_score(closes)

        assert result is not None

    def test_uses_only_last_score_window_points_if_history_is_longer(self):
        """
        Given un historique bien plus long que SCORE_WINDOW, où seule la fin est plate
        When le score est calculé sur un historique tronqué à SCORE_WINDOW et sur le même historique complet
        Then les deux scores sont identiques — sinon la même action afficherait un score différent selon que
        la page regarde 1 an ou tout l'historique (bug corrigé par SCORE_WINDOW).
        """
        noisy_prefix = _rising_series(10.0, 0.05, 500)
        flat_tail = _flat_series(noisy_prefix[-1], SCORE_WINDOW)
        full_history = noisy_prefix + flat_tail

        truncated_result = compute_score(flat_tail)
        full_result = compute_score(full_history)

        assert truncated_result["score"] == pytest.approx(full_result["score"])

    def test_returns_high_score_for_strongly_rising_series(self):
        closes = _rising_series(100.0, 0.01, 260)  # ~28 % de hausse sur un an, sans creux

        result = compute_score(closes)

        assert result["label"] in ("BUY", "HOLD")

    def test_returns_low_score_for_strongly_falling_series(self):
        closes = _rising_series(100.0, -0.01, 260)

        result = compute_score(closes)

        assert result["label"] in ("WATCH", "AVOID/REASSESS")

    def test_omits_long_trend_component_penalty_if_history_is_shorter_than_200_points(self):
        """
        Given un historique de MIN_POINTS points (moins de 200 : pas de moyenne mobile longue possible)
        When le score est calculé
        Then la composante long_trend vaut 50 (neutre), ni favorable ni défavorable.
        """
        closes = _flat_series(100.0, MIN_POINTS)

        result = compute_score(closes)

        assert result["components"]["long_trend"] == pytest.approx(50.0)
