"""Appels à Yahoo Finance (yfinance) avec le même diagnostic que le reste : cadence, pause en cas de blocage."""
import re
import threading
import time

from collectors import health
from collectors.http import SourceError

_lock, _last = threading.Lock(), [0.0]
GAP = 0.35  # secondes entre deux appels : yfinance n'est pas une API officielle, on reste discret
DATA_MARKERS = ("delisted", "no data found", "not found", "no price data", "aucune donnée", "no timezone found")


def classify(exc):
    """('rate', code) si Yahoo nous limite/bloque, ('data', None) si c'est le ticker qui pose problème."""
    text = f"{type(exc).__name__} {exc}"
    low = text.lower()
    m = re.search(r"\b(403|429|401)\b", text)
    if m or "ratelimit" in low or "too many requests" in low or "forbidden" in low or "rate limit" in low:
        return "rate", int(m.group(1)) if m else 429
    if any(k in low for k in DATA_MARKERS):
        return "data", None
    return "other", None


def _classify_and_raise(label, e):
    kind, status = classify(e)
    if kind == "rate":
        health.record_error("yahoo", status, f"{label}: {e}")
        raise SourceError("yahoo", status, f"{label}: {e}") from e
    if kind == "other":
        health.record_error("yahoo", None, f"{label}: {e}")
    raise e


def call(label, fn, timeout=None):
    """Exécute fn() ; enregistre l'état de la source ; lève SourceError si Yahoo bloque.
    timeout (secondes, optionnel) : pour un appel sur le chemin interactif (cotation, actualités), n'attend pas une
    réponse lente indéfiniment — le calcul tourne dans un thread à part et, passé ce délai, on abandonne côté appelant
    (le thread se termine tout seul en arrière-plan, sans rien bloquer)."""
    if health.is_paused("yahoo"):
        raise SourceError("yahoo", None, "Yahoo en pause après un blocage (403/429), nouvelle tentative plus tard")
    with _lock:
        wait = _last[0] + GAP - time.time()
        if wait > 0:
            time.sleep(wait)
        _last[0] = time.time()
    if timeout is None:
        try:
            result = fn()
        except Exception as e:
            _classify_and_raise(label, e)
        health.record_ok("yahoo")
        return result
    box = {}

    def run():
        try:
            box["result"] = fn()
        except Exception as e:
            box["error"] = e
    th = threading.Thread(target=run, daemon=True)
    th.start()
    th.join(timeout)
    if th.is_alive():
        health.record_error("yahoo", None, f"{label}: délai dépassé ({timeout}s)")
        raise SourceError("yahoo", None, f"{label} : délai dépassé ({timeout}s)")
    if "error" in box:
        _classify_and_raise(label, box["error"])
    health.record_ok("yahoo")
    return box["result"]
