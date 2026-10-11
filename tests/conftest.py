"""Fixtures partagées par plusieurs modules de test.

Les tests tournent sans réseau, sans base de données et sans config/settings.json : chaque fixture isole le
morceau testé de ses dépendances externes (fichier de réglages, variables d'environnement, horloge).
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


@pytest.fixture(autouse=True)
def _no_rate_limit(monkeypatch):
    """Désactive la limitation de débit de alerts/notify.py (Module 12) pour toute la suite : sans ça,
    chaque test qui envoie plusieurs messages Telegram/Discord de suite ajoute des secondes de sommeil
    réel pour respecter les limites de débit des vraies API, inutiles en test."""
    from alerts import notify
    monkeypatch.setattr(notify, "RATE_LIMIT_ENABLED", False)


@pytest.fixture
def no_settings_file(tmp_path, monkeypatch):
    """Fait pointer collectors.http vers un fichier de réglages vide : aucun test ne lit les vraies clés d'API."""
    from collectors import http

    empty = tmp_path / "settings.json"
    empty.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(http, "SETTINGS", empty)
    monkeypatch.setattr(http, "_shared_cache", [float("inf"), {}])  # neutralise la lecture en base
    for env in http.ENV_KEYS.values():
        monkeypatch.delenv(env, raising=False)
        monkeypatch.delenv(env + "_DEFAULT", raising=False)
    monkeypatch.delenv("AUTH_ENABLED", raising=False)
    yield empty


@pytest.fixture
def settings_file(no_settings_file):
    """Écrit un fichier de réglages de test et renvoie la fonction qui le remplit."""
    import json

    def write(values):
        no_settings_file.write_text(json.dumps(values), encoding="utf-8")
        return no_settings_file

    return write
