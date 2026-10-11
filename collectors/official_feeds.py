"""Annonces officielles des banques centrales (Réserve fédérale américaine, Banque centrale européenne),
via leurs flux RSS publics — gratuits, sans clé, pas de scraping de page HTML (seulement des flux prévus
pour la syndication).

Ce ne sont pas des calendriers de réunions à venir : ce sont les communiqués au moment où ils sont publiés.
Objectif réel et honnête : réduire le délai entre la publication et la lecture par l'utilisateur, jamais
prétendre détecter l'information avant le reste du marché.
"""
import logging

from collectors.http import fetch
from collectors.news import _parse_rss

log = logging.getLogger("sentinel")

# URLs vérifiées manuellement (pas de supposition) : les deux renvoient directement du XML RSS 2.0, malgré
# l'extension .html pour le flux de la BCE.
FEEDS = {
    "fed": ("https://www.federalreserve.gov/feeds/press_all.xml", "Réserve fédérale (Fed)"),
    "ecb": ("https://www.ecb.europa.eu/rss/press.html", "Banque centrale européenne (BCE)"),
}


def fetch_feed(key):
    """Items les plus récents d'une banque centrale : [{title, url, source, published}]."""
    url, label = FEEDS[key]
    return _parse_rss(fetch(url, f"{key}_press"), label)


def fetch_all():
    """{clé: items} pour chaque banque centrale ; une source en échec n'empêche pas de lire les autres."""
    out = {}
    for key in FEEDS:
        try:
            out[key] = fetch_feed(key)
        except Exception as e:
            log.info("flux officiel %s indisponible : %s", key, e)
            out[key] = []
    return out
