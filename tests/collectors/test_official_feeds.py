"""Tests de collectors/official_feeds.py : fetch() (frontière réseau de collectors.http) est mocké,
jamais le vrai flux RSS."""
from collectors import official_feeds

RSS_FED = b"""<?xml version="1.0"?>
<rss version="2.0"><channel>
<item><title>Minutes of the FOMC</title><link>https://example.org/fed1</link>
<pubDate>Wed, 07 Jan 2026 18:00:00 GMT</pubDate></item>
</channel></rss>
"""


def test_fetch_feed_returns_parsed_items_for_a_known_key(monkeypatch):
    monkeypatch.setattr(official_feeds, "fetch", lambda url, source: RSS_FED)

    items = official_feeds.fetch_feed("fed")

    assert items[0]["title"] == "Minutes of the FOMC"
    assert items[0]["source"] == "Réserve fédérale (Fed)"


def test_fetch_all_returns_items_for_every_configured_feed(monkeypatch):
    monkeypatch.setattr(official_feeds, "fetch", lambda url, source: RSS_FED)

    result = official_feeds.fetch_all()

    assert set(result.keys()) == {"fed", "ecb"}


def test_fetch_all_returns_an_empty_list_for_a_feed_that_fails_instead_of_raising(monkeypatch):
    """
    Given une des deux sources (fed) échoue (ex. coupure réseau)
    When fetch_all est appelé
    Then elle renvoie une liste vide pour cette clé, et l'autre source reste exploitable — une banque
    centrale indisponible ne doit jamais empêcher de lire l'autre.
    """
    def fetch(url, source):
        if source == "fed_press":
            raise TimeoutError()
        return RSS_FED

    monkeypatch.setattr(official_feeds, "fetch", fetch)

    result = official_feeds.fetch_all()

    assert result["fed"] == []
    assert result["ecb"] != []
