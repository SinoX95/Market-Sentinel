"""Tests de collectors/news.py::direct_url (Module 6) : récupère l'URL directe de l'article plutôt que le
lien d'agrégateur, sans jamais lever d'exception si la résolution échoue."""
import urllib.error

from collectors import news


class TestDirectUrl:
    def test_decodes_the_url_parameter_of_a_bing_apiclick_link_without_any_network_call(self, monkeypatch):
        def boom(*a, **k):
            raise AssertionError("ne doit jamais toucher le réseau pour un lien Bing")

        monkeypatch.setattr("urllib.request.urlopen", boom)
        bing_url = ("http://www.bing.com/news/apiclick.aspx?ref=FexRss&url=https%3A%2F%2Fexample.org"
                    "%2Farticle&c=123")

        assert news.direct_url(bing_url) == "https://example.org/article"

    def test_returns_the_bing_url_unchanged_when_it_has_no_url_parameter(self):
        bing_url = "http://www.bing.com/news/search?q=nvidia"

        assert news.direct_url(bing_url) == bing_url

    def test_follows_the_redirect_of_a_google_news_link(self, monkeypatch):
        class _FakeResponse:
            url = "https://example.org/the-real-article"

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

        monkeypatch.setattr("urllib.request.urlopen", lambda req, timeout=5: _FakeResponse())

        assert news.direct_url("https://news.google.com/rss/articles/abc123") == "https://example.org/the-real-article"

    def test_keeps_the_original_link_when_the_redirect_cannot_be_resolved(self, monkeypatch):
        def boom(req, timeout=5):
            raise urllib.error.URLError("unreachable")

        monkeypatch.setattr("urllib.request.urlopen", boom)
        google_url = "https://news.google.com/rss/articles/abc123"

        assert news.direct_url(google_url) == google_url

    def test_returns_other_links_unchanged(self):
        assert news.direct_url("https://example.org/direct-article") == "https://example.org/direct-article"

    def test_returns_falsy_input_unchanged(self):
        assert news.direct_url(None) is None
        assert news.direct_url("") == ""
