"""Déclarations publiques des dirigeants des entreprises suivies (Module B), via une recherche d'actualités
ciblée (collectors.news.search_news) — pas de scraping de réseaux sociaux, pas d'API payante Twitter/X
(coûteuse et non indispensable : voir alerts/README.md).

Fiabilité (Module I) : ce sont des résultats d'actualités qui MENTIONNENT la personne, pas une citation
vérifiée directement depuis un compte officiel — l'app doit toujours les afficher comme tels, jamais comme
une déclaration confirmée mot pour mot.

config/executives.json est optionnel : si absent ou vide, cette fonctionnalité ne fait simplement rien
(aucune erreur), exactement comme pour config/investors.json.
"""
import json
import logging
from pathlib import Path

from collectors.news import search_news

log = logging.getLogger("sentinel")
CONFIG = Path("config/executives.json")


def load_config():
    """Liste des dirigeants suivis, ou [] si le fichier est absent/invalide."""
    try:
        return json.loads(CONFIG.read_text(encoding="utf-8")).get("executives", [])
    except (FileNotFoundError, json.JSONDecodeError):
        return []


def fetch_mentions(executive, days=2, limit=5):
    """Articles récents mentionnant ce dirigeant, les plus récents en tête."""
    return search_news(executive["query"], days=days, limit=limit)
