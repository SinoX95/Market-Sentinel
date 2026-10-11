"""Tests des fonctions pures d'authentification (backend/auth.py) : normalisation des noms et jetons de session.
Ne touchent ni à la base ni au réseau — mock_secret_key isole le HMAC des jetons de toute base de données.
"""
import pytest

from backend import auth

HTTP_BAD_REQUEST = 400


@pytest.fixture
def mock_secret_key(monkeypatch):
    """Remplace la clé HMAC des jetons de session par une valeur fixe, sans toucher à la base (db.kv_setnx)."""
    monkeypatch.setattr(auth, "_key", [b"test-secret-key-not-for-production"])
    yield


class TestNorm:
    def test_returns_lowercase_stripped_name(self):
        assert auth.norm("  Sino  ") == "sino"

    def test_returns_empty_string_if_name_is_none(self):
        assert auth.norm(None) == ""


class TestCleanName:
    def test_returns_name_unchanged_if_it_matches_allowed_characters(self):
        assert auth.clean_name("Jean-Paul O'Brien") == "Jean-Paul O'Brien"

    def test_collapses_repeated_whitespace_into_single_space(self):
        assert auth.clean_name("Sino   Dev") == "Sino Dev"

    def test_raises_auth_error_if_name_contains_disallowed_characters(self):
        """
        Given un nom contenant un caractère hors de la liste autorisée (lettres/chiffres/espaces/./'/-)
        When clean_name est appelé
        Then une AuthError 400 "bad_name" est levée — empêche l'injection de balises ou de scripts via le nom affiché.
        """
        with pytest.raises(auth.AuthError) as exc_info:
            auth.clean_name("<script>alert(1)</script>")

        assert exc_info.value.status == HTTP_BAD_REQUEST
        assert exc_info.value.code == "bad_name"

    def test_raises_auth_error_if_name_is_longer_than_40_characters(self):
        too_long = "a" * 41

        with pytest.raises(auth.AuthError, match="Nom invalide"):
            auth.clean_name(too_long)

    def test_raises_auth_error_if_name_is_empty(self):
        with pytest.raises(auth.AuthError, match="Nom invalide"):
            auth.clean_name("")


class TestMakeTokenAndVerifyFull:
    def test_returns_canonical_admin_name_when_freshly_issued_token_is_verified(self, mock_secret_key, no_settings_file):
        """
        Given un jeton créé à l'instant pour le compte administrateur par défaut ("test", sans fichier de réglages)
        When il est vérifié immédiatement
        Then le nom canonique est retrouvé et l'âge du jeton est proche de zéro.
        """
        token = auth.make_token("test")

        user, age = auth.verify_full(token)

        assert user == "test"
        assert age == pytest.approx(0, abs=2)

    def test_returns_none_if_token_signature_has_been_tampered_with(self, mock_secret_key, no_settings_file):
        token = auth.make_token("test")
        payload, _, signature = token.rpartition(".")
        forged = payload + "." + ("0" * len(signature))

        user, age = auth.verify_full(forged)

        assert user is None
        assert age == 0

    def test_returns_none_if_token_is_empty(self, mock_secret_key, no_settings_file):
        user, age = auth.verify_full("")

        assert user is None

    def test_returns_none_if_token_has_no_dot_separator(self, mock_secret_key, no_settings_file):
        user, age = auth.verify_full("not-a-valid-token")

        assert user is None

    def test_returns_none_if_token_exceeds_max_length(self, mock_secret_key, no_settings_file):
        """
        Given un jeton dépassant la longueur maximale acceptée (400 caractères)
        When il est vérifié
        Then il est rejeté sans être décodé — limite la surface d'attaque par déni de service sur le décodage.
        """
        oversized_token = "a" * 401

        user, age = auth.verify_full(oversized_token)

        assert user is None


class TestIsAdmin:
    def test_returns_false_if_name_is_none_and_no_current_user_is_set(self, monkeypatch):
        monkeypatch.setattr(auth, "current", lambda: None)

        assert auth.is_admin() is False
