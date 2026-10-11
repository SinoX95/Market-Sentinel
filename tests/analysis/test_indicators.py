"""Tests des indicateurs techniques (analysis/indicators.py)."""
import pytest

from analysis.indicators import rsi, sma_series

RSI_PERIOD = 14
RSI_OVERSOLD_UPPER_BOUND = 100.0


class TestSmaSeries:
    def test_returns_none_for_points_before_window_is_full(self):
        values = [1.0, 2.0, 3.0]

        result = sma_series(values, n=5)

        assert result == [None, None, None]

    def test_returns_average_of_window_once_enough_points_are_available(self):
        values = [1.0, 2.0, 3.0, 4.0, 5.0]

        result = sma_series(values, n=3)

        # Les 2 premiers points n'ont pas encore 3 valeurs ; le 3e est la moyenne de (1,2,3), etc.
        assert result == [None, None, pytest.approx(2.0), pytest.approx(3.0), pytest.approx(4.0)]

    def test_returns_same_length_as_input(self):
        values = list(range(20))

        result = sma_series(values, n=7)

        assert len(result) == len(values)


class TestRsi:
    def test_returns_none_if_history_is_not_longer_than_period(self):
        closes = [100.0] * RSI_PERIOD

        result = rsi(closes, n=RSI_PERIOD)

        assert result is None

    def test_returns_100_if_price_never_decreases(self):
        """
        Given une série de cours strictement croissante
        When le RSI est calculé
        Then il vaut 100 — aucune perte moyenne, la division par la perte serait sinon un division par zéro.
        """
        closes = [100.0 + i for i in range(RSI_PERIOD + 5)]

        result = rsi(closes, n=RSI_PERIOD)

        assert result == pytest.approx(RSI_OVERSOLD_UPPER_BOUND)

    def test_returns_value_between_0_and_100_for_mixed_series(self):
        closes = [100, 102, 101, 103, 102, 104, 103, 105, 104, 106, 105, 107, 106, 108, 107, 109]

        result = rsi(closes, n=RSI_PERIOD)

        assert 0.0 <= result <= 100.0
