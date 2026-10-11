"""Accès HTTP poli et diagnostiqué : identification correcte, cadence limitée, nouvelles tentatives,
pause automatique quand une source bloque (403/429), et journal d'état lisible dans l'interface."""
import json
import os
import re
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

from collectors import health

SETTINGS = Path("config/settings.json")
MIN_INTERVAL = {"ecb": 0.5, "frankfurter": 0.5, "sec": 0.15, "google_news": 1.0, "bing_news": 1.0, "finnhub": 1.1, "stooq": 0.6, "twelvedata": 7.8,
                "alphavantage": 13.0, "polygon": 12.5, "tiingo": 1.5, "fed_press": 1.0, "ecb_press": 1.0}  # secondes entre 2 requêtes
BROWSER_COMPAT_UA = "Mozilla/5.0 (compatible; MarketSentinel/1.0)"
_last, _lock = {}, threading.Lock()


class SourceError(Exception):
    def __init__(self, source, status, message):
        super().__init__(f"{source}: {message}" + (f" (HTTP {status})" if status else ""))
        self.source, self.status, self.message = source, status, message


ENV_KEYS = {"contact_email": "CONTACT_EMAIL", "finnhub_api_key": "FINNHUB_API_KEY", "twelvedata_api_key": "TWELVEDATA_API_KEY",
            "alphavantage_api_key": "ALPHAVANTAGE_API_KEY", "polygon_api_key": "POLYGON_API_KEY", "tiingo_api_key": "TIINGO_API_KEY",
            "login_user": "LOGIN_USER", "login_password": "LOGIN_PASSWORD",
            # envoi des codes de connexion et d'invitation par email (voir backend/mailer.py)
            "smtp_user": "SMTP_USER", "smtp_password": "SMTP_PASSWORD", "smtp_host": "SMTP_HOST", "smtp_port": "SMTP_PORT",
            # canal d'alertes Telegram (voir alerts/notify.py) : jeton du bot et identifiant du salon/chat à notifier
            "telegram_bot_token": "TELEGRAM_BOT_TOKEN", "telegram_chat_id": "TELEGRAM_CHAT_ID",
            # canal d'alertes Discord (voir alerts/notify.py) : URL de webhook d'un salon (Paramètres du
            # serveur -> Intégrations -> Webhooks -> Nouveau webhook -> copier l'URL)
            "discord_webhook_url": "DISCORD_WEBHOOK_URL"}


def raw_settings():
    """Contenu du fichier config/settings.json uniquement (pour pouvoir le réécrire sans y copier des secrets d'environnement)."""
    try:
        return json.loads(SETTINGS.read_text(encoding="utf-8"))
    except Exception:
        return {}


_shared_cache = [0.0, {}]
SHARED_KEYS = ("contact_email", "finnhub_api_key", "twelvedata_api_key", "alphavantage_api_key", "polygon_api_key", "tiingo_api_key",
               "telegram_bot_token", "telegram_chat_id", "discord_webhook_url")


def shared_settings(fresh=False):
    """Clés d'API et email de contact partagés par tous les utilisateurs, enregistrés en base (survivent aux redémarrages et au cloud)."""
    if fresh or time.time() - _shared_cache[0] > 20:
        try:
            from backend import db
            _shared_cache[1] = db.kv_get("settings:shared")[0] or {}
        except Exception:
            pass
        _shared_cache[0] = time.time()
    return _shared_cache[1]


def settings():
    """Réglages effectifs : fichier, puis réglages partagés en base, puis variables d'environnement (prioritaires : déploiement cloud)."""
    s = raw_settings()
    # En cloud, config/settings.json n'est plus envoyé (il contient des secrets) : ses clés d'API arrivent par Secret
    # Manager dans des variables « …_DEFAULT », au même niveau que le fichier. Une clé modifiée ensuite depuis
    # l'interface (enregistrée en base) reste donc prioritaire, exactement comme avant.
    for key in SHARED_KEYS:
        default = os.getenv(ENV_KEYS[key] + "_DEFAULT")
        if default and not s.get(key):
            s[key] = default
    s.update({k: v for k, v in shared_settings().items() if k in SHARED_KEYS})
    for key, env in ENV_KEYS.items():
        if os.getenv(env):
            s[key] = os.getenv(env)
    if os.getenv("AUTH_ENABLED") is not None:
        s["auth_enabled"] = os.getenv("AUTH_ENABLED").strip().lower() not in ("0", "false", "no", "")
    return s


def contact_email():
    e = (settings().get("contact_email") or "").strip()
    return e if re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", e) else ""


def user_agent(source):
    if source == "sec":
        # Format demandé par la SEC : un nom d'application + un contact joignable.
        return f"MarketSentinel/1.0 {contact_email()}"
    return BROWSER_COMPAT_UA


def _throttle(source):
    gap = MIN_INTERVAL.get(source, 0)
    if not gap:
        return
    with _lock:
        wait = _last.get(source, 0) + gap - time.time()
        _last[source] = time.time() + max(wait, 0)
    if wait > 0:
        time.sleep(wait)


def redact(url):
    """Masque les clés d'API dans une URL avant de la journaliser ou de l'afficher."""
    return re.sub(r"(?i)((?:api_?key|token|apikey)=)[^&]+", r"\1***", url or "")


def fetch(url, source, headers=None, timeout=25, retries=2):
    if health.is_paused(source):
        raise SourceError(source, None, "source en pause après un refus (403/429), nouvelle tentative plus tard")
    if source == "sec" and not contact_email():
        err = SourceError("sec", None, "contact_email manquant dans config/settings.json : requêtes SEC désactivées")
        health.record_error("sec", None, err.message, redact(url))
        raise err
    h = {"User-Agent": user_agent(source), "Accept-Encoding": "identity", **(headers or {})}
    last = None
    for attempt in range(retries + 1):
        _throttle(source)
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=h), timeout=timeout) as r:
                data = r.read()
            health.record_ok(source)
            return data
        except urllib.error.HTTPError as e:
            body = ""
            try:
                body = e.read(200).decode("utf-8", "replace")
            except Exception:
                pass
            last = SourceError(source, e.code, redact((body.strip().replace("\n", " ") or str(e.reason) or "erreur HTTP")[:160]))
            if e.code in (429, 500, 502, 503, 504) and attempt < retries:
                try:
                    wait = float(e.headers.get("Retry-After", ""))
                except (TypeError, ValueError):
                    wait = 2 ** attempt * 2
                time.sleep(min(wait, 30))
                continue
            break
        except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
            last = SourceError(source, None, f"réseau : {getattr(e, 'reason', e)}")
            if attempt < retries:
                time.sleep(2 ** attempt)
                continue
    health.record_error(source, last.status, last.message, redact(url))
    raise last


def fetch_json(url, source, **kw):
    return json.loads(fetch(url, source, **kw))
