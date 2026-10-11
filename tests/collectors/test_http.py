"""Tests de collectors/http.py : fusion des réglages (fichier / base / environnement) et masquage des clés dans les URLs.
Isolation : no_settings_file et settings_file (conftest) empêchent tout test de lire le vrai config/settings.json.
"""
from collectors import http

ENV_PRIORITY_VALUE = "env-wins"
FILE_VALUE = "file-value"


class TestSettings:
    def test_returns_value_from_file_if_no_environment_variable_is_set(self, settings_file):
        settings_file({"finnhub_api_key": FILE_VALUE})

        result = http.settings()

        assert result["finnhub_api_key"] == FILE_VALUE

    def test_environment_variable_overrides_file_value(self, settings_file, monkeypatch):
        """
        Given une clé présente à la fois dans le fichier et dans une variable d'environnement
        When les réglages effectifs sont calculés
        Then la variable d'environnement gagne — c'est elle qui pilote la configuration en déploiement cloud.
        """
        settings_file({"finnhub_api_key": FILE_VALUE})
        monkeypatch.setenv("FINNHUB_API_KEY", ENV_PRIORITY_VALUE)

        result = http.settings()

        assert result["finnhub_api_key"] == ENV_PRIORITY_VALUE

    def test_parses_auth_enabled_as_false_if_environment_value_is_falsy_string(self, no_settings_file, monkeypatch):
        monkeypatch.setenv("AUTH_ENABLED", "false")

        result = http.settings()

        assert result["auth_enabled"] is False

    def test_parses_auth_enabled_as_true_if_environment_value_is_present_and_not_falsy(self, no_settings_file, monkeypatch):
        monkeypatch.setenv("AUTH_ENABLED", "1")

        result = http.settings()

        assert result["auth_enabled"] is True


class TestContactEmail:
    def test_returns_email_if_it_matches_a_valid_format(self, settings_file):
        settings_file({"contact_email": "dev@example.com"})

        assert http.contact_email() == "dev@example.com"

    def test_returns_empty_string_if_value_is_not_a_valid_email(self, settings_file):
        settings_file({"contact_email": "not-an-email"})

        assert http.contact_email() == ""

    def test_returns_empty_string_if_no_contact_email_is_configured(self, no_settings_file):
        assert http.contact_email() == ""


class TestRedact:
    def test_masks_api_key_query_parameter(self):
        url = "https://api.example.com/data?api_key=SECRET123&symbol=NVDA"

        result = http.redact(url)

        assert "SECRET123" not in result
        assert "symbol=NVDA" in result

    def test_masks_token_parameter_case_insensitively(self):
        url = "https://api.example.com/data?TOKEN=abc123"

        result = http.redact(url)

        assert "abc123" not in result

    def test_returns_empty_string_if_url_is_none(self):
        assert http.redact(None) == ""
