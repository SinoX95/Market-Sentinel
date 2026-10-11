"""Tests des fonctions d'alerte de backend/service.py : seuils de prix, flux officiels, dirigeants.

backend.db.kv_get/kv_set sont remplacés par un petit magasin en mémoire (la vraie base est la frontière
externe), et backend.service.notify par un espion : ces fonctions ne doivent jamais toucher le réseau ou
une vraie base pour être testées.
"""
from datetime import date, datetime, timezone

import pytest

from backend import db, service

TICKER = "NVDA"
ITEM_1 = {"title": "Décision sur les taux", "url": "https://example.org/pr1", "source": "Fed", "published": "2026-01-01T00:00:00+00:00"}
ITEM_2 = {"title": "Minutes publiées", "url": "https://example.org/pr2", "source": "Fed", "published": "2026-01-02T00:00:00+00:00"}
EXEC = {"slug": "huang-nvda", "name": "Jensen Huang", "ticker": "NVDA", "query": "Jensen Huang NVIDIA"}
# Contenu d'affaires identifiable nommant l'entreprise (Module 4/5/8) : contrairement à ITEM_1/ITEM_2 (une
# simple actualité Fed, sans rapport avec un dirigeant suivi), ces titres passent le score de pertinence
# (alerts/relevance.py) et déclenchent effectivement une notification.
EXEC_ITEM_1 = {"title": "NVIDIA signe un partenariat stratégique majeur", "url": "https://example.org/exec1",
               "source": "Reuters", "published": "2026-01-01T00:00:00+00:00"}
EXEC_ITEM_2 = {"title": "NVIDIA annonce de nouveaux résultats trimestriels", "url": "https://example.org/exec2",
               "source": "Reuters", "published": "2026-01-02T00:00:00+00:00"}


@pytest.fixture
def fake_kv(monkeypatch):
    """Magasin clé/valeur en mémoire, même contrat que backend.db.kv_get/kv_set (kv_get renvoie (valeur, maj))."""
    store = {}
    monkeypatch.setattr(db, "kv_get", lambda key: (store.get(key), None))
    monkeypatch.setattr(db, "kv_set", lambda key, value: store.update({key: value}))
    return store


@pytest.fixture
def spy_notify(monkeypatch):
    """Espion sur service.notify : (sujet, texte, ticker, kind) de chaque appel, sans jamais toucher
    Telegram/email ni la vraie base (voir alerts/notify.py pour ce que ticker/kind veulent dire)."""
    calls = []
    monkeypatch.setattr(service, "notify",
                        lambda subject, text, ticker=None, kind=None, **ctx: calls.append((subject, text, ticker, kind)))
    return calls


class TestCheckPriceMoves:
    def test_alerts_when_move_exceeds_the_default_threshold(self, fake_kv, spy_notify):
        companies = [{"ticker": TICKER}]
        closes = {TICKER: [(date(2026, 1, 1), 100.0), (date(2026, 1, 2), 110.0)]}  # +10 % > seuil 7 %

        service._check_price_moves(companies, closes)

        assert len(spy_notify) == 1
        assert TICKER in spy_notify[0][0]

    def test_does_not_alert_when_move_is_within_the_default_threshold(self, fake_kv, spy_notify):
        companies = [{"ticker": TICKER}]
        closes = {TICKER: [(date(2026, 1, 1), 100.0), (date(2026, 1, 2), 103.0)]}  # +3 % < seuil 7 %

        service._check_price_moves(companies, closes)

        assert spy_notify == []

    def test_uses_the_tickers_own_threshold_instead_of_the_default_when_set(self, fake_kv, spy_notify):
        companies = [{"ticker": TICKER, "alert_pct": 2.0}]
        closes = {TICKER: [(date(2026, 1, 1), 100.0), (date(2026, 1, 2), 103.0)]}  # +3 % > seuil propre 2 %

        service._check_price_moves(companies, closes)

        assert len(spy_notify) == 1

    def test_does_not_alert_twice_for_the_same_closing_day(self, fake_kv, spy_notify):
        """
        Given une variation déjà au-delà du seuil pour la clôture du jour
        When le cycle est rejoué pour la même clôture (ex. relance manuelle)
        Then une seule alerte est envoyée au total — la déduplication est par jour de clôture, pas par appel.
        """
        companies = [{"ticker": TICKER}]
        closes = {TICKER: [(date(2026, 1, 1), 100.0), (date(2026, 1, 2), 110.0)]}

        service._check_price_moves(companies, closes)
        service._check_price_moves(companies, closes)

        assert len(spy_notify) == 1

    def test_does_not_alert_when_history_has_fewer_than_two_points(self, fake_kv, spy_notify):
        companies = [{"ticker": TICKER}]
        closes = {TICKER: [(date(2026, 1, 2), 110.0)]}

        service._check_price_moves(companies, closes)

        assert spy_notify == []


DEFAULT_THRESHOLD = service.DEFAULT_ALERT_PCT


class TestMaybeAlertPriceMove:
    def test_does_not_alert_when_move_is_below_threshold(self, fake_kv, spy_notify):
        service._maybe_alert_price_move(TICKER, 3.0, DEFAULT_THRESHOLD, live=True)

        assert spy_notify == []

    def test_labels_the_alert_source_as_live_when_live_is_true(self, fake_kv, spy_notify):
        service._maybe_alert_price_move(TICKER, 8.0, DEFAULT_THRESHOLD, live=True)

        assert "direct" in spy_notify[0][0]

    def test_labels_the_alert_source_as_closing_when_live_is_false(self, fake_kv, spy_notify):
        service._maybe_alert_price_move(TICKER, 8.0, DEFAULT_THRESHOLD, live=False)

        assert "clôture" in spy_notify[0][0]

    def test_does_not_alert_again_the_same_day_for_a_smaller_move_in_the_same_direction(self, fake_kv, spy_notify):
        service._maybe_alert_price_move(TICKER, -16.0, DEFAULT_THRESHOLD, live=True)
        service._maybe_alert_price_move(TICKER, -9.0, DEFAULT_THRESHOLD, live=True)

        assert len(spy_notify) == 1

    def test_alerts_again_the_same_day_when_the_move_worsens_by_a_full_step(self, fake_kv, spy_notify):
        """
        Given une première alerte déjà envoyée aujourd'hui pour une baisse de 8 % (seuil franchi)
        When le titre continue de chuter jusqu'à 16 % (un palier complet de plus, INTRADAY_ALERT_STEP_PCT)
        Then une deuxième alerte part — un creux qui s'aggrave ne doit pas être réduit au silence par la
        déduplication quotidienne, sans quoi la toute première alerte serait la seule de la journée.
        """
        service._maybe_alert_price_move(TICKER, -8.0, DEFAULT_THRESHOLD, live=True)
        service._maybe_alert_price_move(TICKER, -16.0, DEFAULT_THRESHOLD, live=True)

        assert len(spy_notify) == 2

    def test_alerts_again_the_same_day_when_the_direction_flips(self, fake_kv, spy_notify):
        """
        Given une alerte de baisse déjà envoyée aujourd'hui pour ce titre
        When il repart ensuite à la hausse au-delà du seuil, toujours le même jour
        Then une nouvelle alerte part — un retournement est une information distincte, pas une répétition
        du même mouvement.
        """
        service._maybe_alert_price_move(TICKER, -8.0, DEFAULT_THRESHOLD, live=True)
        service._maybe_alert_price_move(TICKER, 8.0, DEFAULT_THRESHOLD, live=True)

        assert len(spy_notify) == 2


RECENT_PUBLISHED = datetime.now(timezone.utc).isoformat()


def _news_item(sentiment, title="NVIDIA annonce un nouveau partenariat majeur", title_fr=None):
    item = {"title": title, "url": "https://example.com/a", "source": "Reuters",
            "published": RECENT_PUBLISHED, "sentiment": sentiment}
    if title_fr is not None:
        item["title_fr"] = title_fr
    return item


class TestMaybeAlertPriceMoveNewsEnrichment:
    def test_includes_matching_recent_news_in_the_alert_text(self, fake_kv, spy_notify):
        fake_kv[f"news:{TICKER}"] = [_news_item(sentiment=1)]  # positif, va dans le sens de la hausse

        service._maybe_alert_price_move(TICKER, 8.0, DEFAULT_THRESHOLD, live=True)

        assert "NVIDIA annonce un nouveau partenariat majeur" in spy_notify[0][1]
        assert "Reuters" in spy_notify[0][1]

    def test_prefers_the_french_title_over_the_english_one_when_available(self, fake_kv, spy_notify):
        fake_kv[f"news:{TICKER}"] = [_news_item(sentiment=1, title="NVIDIA announces major partnership",
                                                 title_fr="NVIDIA annonce un partenariat majeur")]

        service._maybe_alert_price_move(TICKER, 8.0, DEFAULT_THRESHOLD, live=True)

        assert "NVIDIA annonce un partenariat majeur" in spy_notify[0][1]
        assert "NVIDIA announces major partnership" not in spy_notify[0][1]

    def test_includes_the_article_url_alongside_the_source(self, fake_kv, spy_notify):
        fake_kv[f"news:{TICKER}"] = [_news_item(sentiment=1)]

        service._maybe_alert_price_move(TICKER, 8.0, DEFAULT_THRESHOLD, live=True)

        assert "https://example.com/a" in spy_notify[0][1]

    def test_does_not_include_news_whose_sentiment_contradicts_the_direction(self, fake_kv, spy_notify):
        fake_kv[f"news:{TICKER}"] = [_news_item(sentiment=-1)]  # négatif, mais le titre monte

        service._maybe_alert_price_move(TICKER, 8.0, DEFAULT_THRESHOLD, live=True)

        assert "Actualité" not in spy_notify[0][1]

    def test_does_not_include_news_section_when_nothing_is_cached(self, fake_kv, spy_notify):
        service._maybe_alert_price_move(TICKER, 8.0, DEFAULT_THRESHOLD, live=True)

        assert "Actualité" not in spy_notify[0][1]

    def test_still_alerts_when_the_news_lookup_itself_fails(self, fake_kv, spy_notify, monkeypatch):
        """
        Given la lecture du cache d'actualités échoue (ex. donnée corrompue)
        When un mouvement de prix franchit le seuil
        Then l'alerte de prix part quand même, sans l'enrichissement — un problème d'actualités ne doit
        jamais faire taire une alerte de prix par ailleurs valide.
        """
        def raising_kv_get(key):
            if key.startswith("news:"):
                raise RuntimeError("cache corrompu")
            return fake_kv.get(key), None

        monkeypatch.setattr(db, "kv_get", raising_kv_get)

        service._maybe_alert_price_move(TICKER, 8.0, DEFAULT_THRESHOLD, live=True)

        assert len(spy_notify) == 1


class TestCheckIntradayMoves:
    def test_alerts_when_the_live_price_exceeds_the_threshold_vs_previous_close(self, fake_kv, spy_notify, monkeypatch):
        companies = [{"ticker": TICKER}]
        monkeypatch.setattr(service, "download_intraday_many", lambda tickers: {TICKER: [("2026-10-10T14:00", 100.0, 101.0, 95.0, 88.0)]})
        monkeypatch.setattr(service, "_previous_close", lambda t: 100.0)  # -12 % > seuil 7 %

        service._check_intraday_moves(companies)

        assert len(spy_notify) == 1
        assert "direct" in spy_notify[0][0]

    def test_does_nothing_when_the_live_price_download_fails(self, fake_kv, spy_notify, monkeypatch):
        companies = [{"ticker": TICKER}]

        def fail(tickers):
            raise RuntimeError("Yahoo indisponible")

        monkeypatch.setattr(service, "download_intraday_many", fail)

        service._check_intraday_moves(companies)

        assert spy_notify == []

    def test_skips_a_ticker_with_no_previous_close(self, fake_kv, spy_notify, monkeypatch):
        companies = [{"ticker": TICKER}]
        monkeypatch.setattr(service, "download_intraday_many", lambda tickers: {TICKER: [("t", 100.0, 101.0, 95.0, 88.0)]})
        monkeypatch.setattr(service, "_previous_close", lambda t: None)

        service._check_intraday_moves(companies)

        assert spy_notify == []

    def test_skips_a_ticker_with_no_intraday_rows(self, fake_kv, spy_notify, monkeypatch):
        companies = [{"ticker": TICKER}]
        monkeypatch.setattr(service, "download_intraday_many", lambda tickers: {TICKER: []})

        service._check_intraday_moves(companies)

        assert spy_notify == []


class TestPreviousClose:
    def test_returns_the_last_close_strictly_before_today(self, monkeypatch):
        monkeypatch.setattr(db, "load_closes", lambda tickers: {TICKER: [(date(2020, 1, 1), 50.0), (date.today(), 999.0)]})

        assert service._previous_close(TICKER) == 50.0

    def test_returns_none_when_there_is_no_close_strictly_before_today(self, monkeypatch):
        monkeypatch.setattr(db, "load_closes", lambda tickers: {TICKER: [(date.today(), 999.0)]})

        assert service._previous_close(TICKER) is None


RESULTS_FILING_1 = {"form": "8-K", "label": "Annonce d'événement (8-K)", "date": "2026-10-01", "items": ["2.02"],
                     "url": "https://example.org/8k1", "what": ["Résultats trimestriels"]}
RESULTS_FILING_2 = {"form": "8-K", "label": "Annonce d'événement (8-K)", "date": "2026-10-10", "items": ["2.02"],
                     "url": "https://example.org/8k2", "what": ["Résultats trimestriels"]}
OTHER_FILING = {"form": "8-K", "label": "Annonce d'événement (8-K)", "date": "2026-10-10", "items": ["5.02"],
                "url": "https://example.org/8k3", "what": ["Changement de dirigeants"]}


class TestCheckEarningsFiling:
    def test_records_a_baseline_without_alerting_on_the_first_run(self, fake_kv, spy_notify):
        service._check_earnings_filing(TICKER, [RESULTS_FILING_1])

        assert spy_notify == []

    def test_alerts_on_a_new_results_filing_once_a_baseline_already_exists(self, fake_kv, spy_notify):
        fake_kv[f"earningsalert:{TICKER}:seen"] = {"urls": [RESULTS_FILING_1["url"]]}

        service._check_earnings_filing(TICKER, [RESULTS_FILING_1, RESULTS_FILING_2])

        assert len(spy_notify) == 1
        assert TICKER in spy_notify[0][0]

    def test_ignores_filings_without_the_quarterly_results_item(self, fake_kv, spy_notify):
        """
        Given un dépôt 8-K dont l'objet n'est pas les résultats trimestriels (ex. item 5.02, changement de
        dirigeants) alors qu'une référence existe déjà pour ce titre
        When _check_earnings_filing est appelé
        Then aucune alerte n'est envoyée — seul l'item SEC 2.02 (résultats trimestriels) doit déclencher.
        """
        fake_kv[f"earningsalert:{TICKER}:seen"] = {"urls": [RESULTS_FILING_1["url"]]}

        service._check_earnings_filing(TICKER, [RESULTS_FILING_1, OTHER_FILING])

        assert spy_notify == []

    def test_does_not_alert_when_no_new_results_filing_since_the_last_check(self, fake_kv, spy_notify):
        fake_kv[f"earningsalert:{TICKER}:seen"] = {"urls": [RESULTS_FILING_1["url"]]}

        service._check_earnings_filing(TICKER, [RESULTS_FILING_1])

        assert spy_notify == []


class TestCheckOfficialFeeds:
    def test_records_a_baseline_without_alerting_on_the_first_run(self, fake_kv, spy_notify, monkeypatch):
        monkeypatch.setattr("collectors.official_feeds.fetch_all", lambda: {"fed": [ITEM_1]})

        service._check_official_feeds()

        assert spy_notify == []

    def test_alerts_on_a_new_item_once_a_baseline_already_exists(self, fake_kv, spy_notify, monkeypatch):
        fake_kv["officialfeed:fed:seen"] = {"urls": [ITEM_1["url"]]}
        monkeypatch.setattr("collectors.official_feeds.fetch_all", lambda: {"fed": [ITEM_1, ITEM_2]})

        service._check_official_feeds()

        assert len(spy_notify) == 1
        assert ITEM_2["title"] in spy_notify[0][1]

    def test_does_not_alert_when_nothing_is_new_since_the_last_cycle(self, fake_kv, spy_notify, monkeypatch):
        fake_kv["officialfeed:fed:seen"] = {"urls": [ITEM_1["url"]]}
        monkeypatch.setattr("collectors.official_feeds.fetch_all", lambda: {"fed": [ITEM_1]})

        service._check_official_feeds()

        assert spy_notify == []

    def test_processes_every_feed_key_even_when_one_has_nothing_new(self, fake_kv, spy_notify, monkeypatch):
        fake_kv["officialfeed:ecb:seen"] = {"urls": [ITEM_1["url"]]}
        monkeypatch.setattr("collectors.official_feeds.fetch_all", lambda: {"fed": [], "ecb": [ITEM_1, ITEM_2]})

        service._check_official_feeds()

        assert len(spy_notify) == 1


class TestCheckExecutives:
    def test_does_nothing_when_no_executive_is_configured(self, fake_kv, spy_notify, monkeypatch):
        monkeypatch.setattr("collectors.executives.load_config", lambda: [])

        service._check_executives()

        assert spy_notify == []

    def test_records_a_baseline_without_alerting_on_the_first_run(self, fake_kv, spy_notify, monkeypatch):
        monkeypatch.setattr("collectors.executives.load_config", lambda: [EXEC])
        monkeypatch.setattr("collectors.executives.fetch_mentions", lambda exe, **k: [ITEM_1])

        service._check_executives()

        assert spy_notify == []

    def test_alerts_on_a_new_mention_once_a_baseline_already_exists(self, fake_kv, spy_notify, monkeypatch):
        fake_kv["execfeed:huang-nvda:seen"] = {"urls": [EXEC_ITEM_1["url"]]}
        monkeypatch.setattr("collectors.executives.load_config", lambda: [EXEC])
        monkeypatch.setattr("collectors.executives.fetch_mentions", lambda exe, **k: [EXEC_ITEM_1, EXEC_ITEM_2])

        service._check_executives([{"ticker": "NVDA", "name": "NVIDIA"}])

        assert len(spy_notify) == 1
        assert "NVIDIA" in spy_notify[0][0]

    def test_does_not_alert_on_a_mention_with_no_identifiable_business_content(self, fake_kv, spy_notify, monkeypatch):
        """
        Given une actualité qui ne fait que mentionner le nom du dirigeant, sans aucun contenu d'affaires
        ni mention de l'entreprise (ex. ITEM_1, une actualité Fed sans rapport)
        When _check_executives est appelé
        Then rien n'est envoyé — le score de pertinence (alerts/relevance.py) filtre ce bruit, qui recevait
        auparavant un score fixe de 30/100 identique à une vraie actualité d'affaires.
        """
        fake_kv["execfeed:huang-nvda:seen"] = {"urls": ["https://example.org/baseline"]}
        monkeypatch.setattr("collectors.executives.load_config", lambda: [EXEC])
        monkeypatch.setattr("collectors.executives.fetch_mentions", lambda exe, **k: [ITEM_1])

        service._check_executives([{"ticker": "NVDA", "name": "NVIDIA"}])

        assert spy_notify == []

    def test_one_executives_source_failing_does_not_block_the_others(self, fake_kv, spy_notify, monkeypatch):
        exe2 = {**EXEC, "slug": "su-amd", "name": "Lisa Su", "ticker": "AMD"}
        fake_kv["execfeed:su-amd:seen"] = {"urls": [EXEC_ITEM_1["url"]]}
        monkeypatch.setattr("collectors.executives.load_config", lambda: [EXEC, exe2])

        def fetch_mentions(exe, **k):
            if exe["slug"] == "huang-nvda":
                raise RuntimeError("source bloquée")
            return [EXEC_ITEM_1, EXEC_ITEM_2]

        monkeypatch.setattr("collectors.executives.fetch_mentions", fetch_mentions)

        service._check_executives([{"ticker": "NVDA", "name": "NVIDIA"}, {"ticker": "AMD", "name": "AMD"}])

        assert len(spy_notify) == 1
