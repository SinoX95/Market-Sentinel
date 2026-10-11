"""Tests de backend/service.py::find_missed_price_moves (Module K) : un audit après coup, jamais une
recommandation — db.load_closes/recent_alerts et service.load_watchlist sont les frontières mockées."""
from datetime import date, datetime, timedelta, timezone

import pytest

from backend import db, service

TICKER = "NVDA"
TODAY = date(2026, 10, 10)


@pytest.fixture
def watchlist(monkeypatch):
    companies = [{"ticker": TICKER}]
    monkeypatch.setattr(service, "load_watchlist", lambda all_users=False: companies)
    return companies


@pytest.fixture
def fake_today(monkeypatch):
    class _FixedDate(date):
        @classmethod
        def today(cls):
            return TODAY

    monkeypatch.setattr(service, "date", _FixedDate)
    return TODAY


def _closes(pairs):
    """pairs: [(jours_avant_aujourd'hui, prix), ...] du plus ancien au plus récent."""
    return {TICKER: [(TODAY - timedelta(days=d), p) for d, p in pairs]}


def _alert(kind, days_ago):
    return {"kind": kind, "created_at": datetime(TODAY.year, TODAY.month, TODAY.day, tzinfo=timezone.utc) - timedelta(days=days_ago)}


class TestFindMissedPriceMoves:
    def test_flags_a_move_beyond_threshold_with_no_matching_alert(self, watchlist, fake_today, monkeypatch):
        monkeypatch.setattr(db, "load_closes", lambda tickers: _closes([(1, 100.0), (0, 110.0)]))  # +10 % > seuil 7 %
        monkeypatch.setattr(db, "recent_alerts", lambda limit, ticker: [])

        result = service.find_missed_price_moves(days=30)

        assert len(result) == 1
        assert result[0]["ticker"] == TICKER
        assert result[0]["pct"] == pytest.approx(10.0, abs=0.1)

    def test_does_not_flag_when_a_matching_alert_already_exists_for_that_date(self, watchlist, fake_today, monkeypatch):
        monkeypatch.setattr(db, "load_closes", lambda tickers: _closes([(1, 100.0), (0, 110.0)]))
        monkeypatch.setattr(db, "recent_alerts", lambda limit, ticker: [_alert("daily_move", 0)])

        assert service.find_missed_price_moves(days=30) == []

    def test_does_not_flag_moves_below_the_threshold(self, watchlist, fake_today, monkeypatch):
        monkeypatch.setattr(db, "load_closes", lambda tickers: _closes([(1, 100.0), (0, 103.0)]))  # +3 % < seuil 7 %
        monkeypatch.setattr(db, "recent_alerts", lambda limit, ticker: [])

        assert service.find_missed_price_moves(days=30) == []

    def test_uses_the_tickers_own_alert_pct_instead_of_the_default(self, fake_today, monkeypatch):
        monkeypatch.setattr(service, "load_watchlist", lambda all_users=False: [{"ticker": TICKER, "alert_pct": 2.0}])
        monkeypatch.setattr(db, "load_closes", lambda tickers: _closes([(1, 100.0), (0, 103.0)]))  # +3 % > seuil propre 2 %
        monkeypatch.setattr(db, "recent_alerts", lambda limit, ticker: [])

        result = service.find_missed_price_moves(days=30)

        assert len(result) == 1
        assert result[0]["threshold"] == 2.0

    def test_ignores_moves_older_than_the_requested_window(self, watchlist, fake_today, monkeypatch):
        monkeypatch.setattr(db, "load_closes", lambda tickers: _closes([(100, 100.0), (99, 110.0)]))  # +10 %, il y a 99 j
        monkeypatch.setattr(db, "recent_alerts", lambda limit, ticker: [])

        assert service.find_missed_price_moves(days=30) == []

    def test_sorts_results_most_recent_first(self, watchlist, fake_today, monkeypatch):
        monkeypatch.setattr(db, "load_closes",
                            lambda tickers: _closes([(5, 100.0), (4, 110.0), (1, 110.0), (0, 121.0)]))
        monkeypatch.setattr(db, "recent_alerts", lambda limit, ticker: [])

        result = service.find_missed_price_moves(days=30)

        assert [r["date"] for r in result] == [TODAY.isoformat(), (TODAY - timedelta(days=4)).isoformat()]

    def test_continues_with_other_tickers_when_alert_history_lookup_fails_for_one(self, fake_today, monkeypatch):
        monkeypatch.setattr(service, "load_watchlist", lambda all_users=False: [{"ticker": TICKER}, {"ticker": "AMD"}])

        def closes(tickers):
            return {TICKER: [(TODAY - timedelta(days=1), 100.0), (TODAY, 110.0)],
                    "AMD": [(TODAY - timedelta(days=1), 50.0), (TODAY, 55.0)]}

        def recent_alerts(limit, ticker):
            if ticker == TICKER:
                raise RuntimeError("base indisponible")
            return []

        monkeypatch.setattr(db, "load_closes", closes)
        monkeypatch.setattr(db, "recent_alerts", recent_alerts)

        result = service.find_missed_price_moves(days=30)

        assert [r["ticker"] for r in result] == ["AMD"]

    def test_each_result_exposes_its_own_reason(self, watchlist, fake_today, monkeypatch):
        monkeypatch.setattr(db, "load_closes", lambda tickers: _closes([(1, 100.0), (0, 110.0)]))
        monkeypatch.setattr(db, "recent_alerts", lambda limit, ticker: [])

        result = service.find_missed_price_moves(days=30)

        assert result[0]["reason"]
