"""Déduplication d'un même événement repris par plusieurs médias (Module 7) : la dédup existante
(backend/service.py) est un simple ensemble d'URL déjà vues par source — donc 5 médias relayant la même
dépêche avec 5 URL différentes comptent comme 5 « nouveautés ». Ce module ajoute une dédup par événement
réel : même entreprise + titre normalisé proche, dans une fenêtre de temps glissante.

Stocké via backend.db.kv_get/kv_set (même mécanisme que les sets d'URL existants — pas de nouvelle table).
"""
import re
from datetime import datetime, timedelta, timezone

WINDOW_DAYS = 3  # fenêtre par défaut : assez large pour absorber les reprises tardives d'un même sujet,
                  # assez courte pour qu'un vrai développement ultérieur reste une alerte à part (voir le
                  # cahier des charges : « si une annonce initiale est suivie d'un développement réellement
                  # nouveau, une nouvelle notification peut être justifiée »)

_STOPWORDS = {"the", "a", "an", "of", "to", "in", "for", "on", "with", "and", "le", "la", "les", "des",
              "du", "une", "un", "de", "et", "pour", "sur", "dans", "avec"}


def normalize_title(title):
    """Titre réduit à ses mots significatifs, triés — insensible à l'ordre des mots, à la ponctuation, à
    la casse et aux mots vides : deux titres qui ne diffèrent que par leur formulation convergent vers la
    même clé, sans pour autant fusionner deux sujets réellement différents."""
    words = re.findall(r"[a-z0-9]+", (title or "").lower())
    significant = sorted(w for w in words if w not in _STOPWORDS and len(w) > 2)
    return " ".join(significant)


SIMILARITY_THRESHOLD = 0.5  # proportion de mots significatifs communs (indice de Jaccard) à partir de
                             # laquelle deux titres sont considérés comme le même événement reformulé


def _similarity(norm_a, norm_b):
    words_a, words_b = set(norm_a.split()), set(norm_b.split())
    if not words_a or not words_b:
        return 0.0
    return len(words_a & words_b) / len(words_a | words_b)


def is_new_event(ticker, title, kv_get, kv_set, window_days=WINDOW_DAYS):
    """True si ce titre n'a pas déjà été vu, ou reformulé de façon proche (indice de Jaccard sur les mots
    significatifs — « NVIDIA acquires a startup » et « Startup acquired by NVIDIA » sont le même
    événement), pour ce ticker dans la fenêtre — et l'enregistre dans ce cas. kv_get/kv_set sont injectés
    (plutôt qu'importés ici) pour rester testable sans base réelle, comme le reste du module alerts."""
    key = f"newsevent:{ticker}:cluster"
    norm = normalize_title(title)
    if not norm:
        return True  # rien à comparer (titre vide) : ne bloque jamais une vraie alerte par prudence
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(days=window_days)
    seen = (kv_get(key)[0] or [])
    fresh = [e for e in seen if _parse(e.get("at")) and _parse(e.get("at")) > cutoff]
    for e in fresh:
        if _similarity(e.get("norm", ""), norm) >= SIMILARITY_THRESHOLD:
            kv_set(key, fresh)  # purge les entrées expirées même quand ce titre est un doublon
            return False
    fresh.append({"norm": norm, "at": now.isoformat()})
    kv_set(key, fresh[-50:])  # borné : un ticker très actif ne doit pas faire grossir la clé indéfiniment
    return True


def _parse(iso):
    try:
        return datetime.fromisoformat(iso)
    except (TypeError, ValueError):
        return None
