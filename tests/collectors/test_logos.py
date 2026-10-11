"""Tests de collectors/logos.py : mapping statique ticker -> logo, jamais d'image cassée pour un ticker
absent de la table."""
from collectors import logos


class TestLogoUrl:
    def test_returns_a_url_for_a_known_ticker(self):
        assert logos.logo_url("NVDA") == "https://logo.clearbit.com/nvidia.com?size=128"

    def test_is_case_insensitive(self):
        assert logos.logo_url("nvda") == logos.logo_url("NVDA")

    def test_returns_none_for_an_unknown_ticker(self):
        assert logos.logo_url("ZZZZ") is None

    def test_returns_none_for_an_empty_ticker(self):
        assert logos.logo_url("") is None

    def test_returns_none_for_none(self):
        assert logos.logo_url(None) is None
