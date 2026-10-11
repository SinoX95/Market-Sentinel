"""Tests de alerts/dedup.py : dédup par événement (titre normalisé + fenêtre de temps), distincte de la
dédup par URL déjà vue existante dans backend/service.py (plusieurs médias reprenant la même dépêche ont
des URL différentes mais doivent compter comme un seul événement)."""
from datetime import datetime, timedelta, timezone

from alerts import dedup


class _FakeKv:
    """Même contrat que backend.db.kv_get/kv_set (kv_get renvoie (valeur, maj)), en mémoire."""

    def __init__(self):
        self.store = {}

    def get(self, key):
        return self.store.get(key), None

    def set(self, key, value):
        self.store[key] = value


class TestNormalizeTitle:
    def test_ignores_word_order(self):
        assert dedup.normalize_title("NVIDIA announces new chip") == dedup.normalize_title("new chip announces NVIDIA")

    def test_ignores_case_and_punctuation(self):
        assert dedup.normalize_title("NVIDIA's new chip!") == dedup.normalize_title("nvidia new chip")

    def test_drops_stopwords(self):
        assert "the" not in dedup.normalize_title("NVIDIA announces the new chip").split()


class TestIsNewEvent:
    def test_first_sighting_of_a_title_is_new(self):
        kv = _FakeKv()

        assert dedup.is_new_event("NVDA", "NVIDIA acquires a startup", kv.get, kv.set) is True

    def test_a_reworded_repeat_of_the_same_event_is_not_new(self):
        kv = _FakeKv()
        dedup.is_new_event("NVDA", "NVIDIA acquires a leading AI startup", kv.get, kv.set)

        assert dedup.is_new_event("NVDA", "Startup acquired by NVIDIA, a leading AI company", kv.get, kv.set) is False

    def test_a_genuinely_different_event_for_the_same_ticker_is_new(self):
        kv = _FakeKv()
        dedup.is_new_event("NVDA", "NVIDIA acquires a startup", kv.get, kv.set)

        assert dedup.is_new_event("NVDA", "NVIDIA publishes quarterly results", kv.get, kv.set) is True

    def test_the_same_title_for_a_different_ticker_is_new(self):
        kv = _FakeKv()
        dedup.is_new_event("NVDA", "announces a new partnership", kv.get, kv.set)

        assert dedup.is_new_event("AMD", "announces a new partnership", kv.get, kv.set) is True

    def test_an_event_outside_the_time_window_is_new_again(self):
        kv = _FakeKv()
        stale = datetime.now(timezone.utc) - timedelta(days=dedup.WINDOW_DAYS + 1)
        kv.store["newsevent:NVDA:cluster"] = [{"norm": dedup.normalize_title("NVIDIA acquires a startup"),
                                               "at": stale.isoformat()}]

        assert dedup.is_new_event("NVDA", "NVIDIA acquires a startup", kv.get, kv.set) is True

    def test_an_empty_title_is_always_treated_as_new(self):
        kv = _FakeKv()

        assert dedup.is_new_event("NVDA", "", kv.get, kv.set) is True
