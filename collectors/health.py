"""État de santé des sources de données : dernier succès, dernière erreur (code HTTP, message) et pauses.

Sert à deux choses : ne pas marteler une source qui nous bloque (403/429), et dire clairement
à l'utilisateur d'où vient un problème et comment le régler.
"""
import threading
import time
from datetime import datetime, timezone

SOURCES = {
    "sec": "SEC EDGAR (positions des grands investisseurs, recherche d'entreprises)",
    "yahoo": "Yahoo Finance (cours, fondamentaux, analystes, recherche)",
    "ecb": "Banque centrale européenne (taux de change)",
    "frankfurter": "Frankfurter (taux de change, secours)",
    "google_news": "Google Actualités",
    "bing_news": "Bing Actualités (secours)",
    "finnhub": "Finnhub (cours en direct, actualités, clé optionnelle)",
    "stooq": "Stooq (cours de clôture, vérification indépendante, sans clé)",
    "twelvedata": "Twelve Data (cours, clé optionnelle)",
    "alphavantage": "Alpha Vantage (cours de clôture, clé optionnelle)",
    "polygon": "Polygon.io (cours de clôture, clé optionnelle)",
    "tiingo": "Tiingo (cours, clé optionnelle)",
    "google_translate": "Google Traduction (titres d'actualités en français)",
}
PAUSE_S = {403: 900, 429: 900}
_lock = threading.Lock()
_h = {}


def hint(source, status, message=""):
    m = (message or "").lower()
    if source == "sec":
        if "contact_email" in m:
            return "La SEC exige d'identifier les programmes qui l'interrogent. Renseigne ton email dans config/settings.json (champ contact_email) ou relance start.bat."
        if status == 403:
            return "La SEC refuse la requête : vérifie que contact_email (config/settings.json) est une vraie adresse email, et évite les VPN."
        if status == 429:
            return "Trop de requêtes vers la SEC (limite 10 par seconde) : le programme ralentit et réessaie."
    if source == "yahoo":
        if status in (403, 429) or "rate" in m or "too many" in m:
            return "Yahoo limite ou bloque les requêtes (trop nombreuses, VPN, IP d'entreprise). Pause automatique puis nouvelle tentative ; les données déjà collectées restent affichées. Mets yfinance à jour et évite les VPN."
    if source in ("google_news", "bing_news") and status in (403, 429):
        return "Le service d'actualités bloque temporairement les requêtes : l'autre source prend le relais."
    if source in ("finnhub", "twelvedata", "alphavantage", "polygon", "tiingo") and status in (401, 403):
        return "Clé API invalide, expirée ou non autorisée pour cette donnée : vérifie-la dans l'onglet Sources."
    if source in ("twelvedata", "alphavantage", "polygon", "tiingo", "finnhub") and status == 429:
        return "Quota gratuit atteint : le programme ralentit et utilise les autres sources."
    return ""


def _entry(source):
    return _h.setdefault(source, {"ok": 0, "errors": 0, "last_ok": None, "last_error": None, "paused_until": 0})


def record_ok(source):
    with _lock:
        e = _entry(source)
        e["ok"] += 1
        e["last_ok"] = datetime.now(timezone.utc).isoformat()


def record_error(source, status=None, message="", url=None):
    with _lock:
        e = _entry(source)
        e["errors"] += 1
        e["last_error"] = {"at": datetime.now(timezone.utc).isoformat(), "status": status,
                           "message": (message or "")[:240], "url": (url or "")[:160],
                           "hint": hint(source, status, message)}
        if status in PAUSE_S:
            e["paused_until"] = time.time() + PAUSE_S[status]


def is_paused(source):
    with _lock:
        return _entry(source)["paused_until"] > time.time()


def resume(source=None):
    with _lock:
        for k in ([source] if source else list(_h)):
            _entry(k)["paused_until"] = 0


def snapshot():
    out = []
    with _lock:
        for key, label in SOURCES.items():
            e = _entry(key)
            le = e["last_error"]
            failing = bool(le) and (not e["last_ok"] or le["at"] > e["last_ok"])
            out.append({"source": key, "label": label, "ok": e["ok"], "errors": e["errors"],
                        "last_ok": e["last_ok"], "last_error": le, "failing": failing,
                        "paused": e["paused_until"] > time.time(),
                        "paused_for_s": max(0, int(e["paused_until"] - time.time()))})
    return out


def problems():
    """Sources actuellement en échec (dernière erreur plus récente que le dernier succès)."""
    return [{"source": s["source"], "label": s["label"], "status": s["last_error"]["status"],
             "message": s["last_error"]["message"], "hint": s["last_error"]["hint"], "paused": s["paused"]}
            for s in snapshot() if s["failing"]]
