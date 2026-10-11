"""Fournisseurs de cours interchangeables. Chacun sait donner une cotation récente et/ou un historique de clôtures ;
le module multi.py les recoupe pour détecter les écarts et remplacer une source bloquée.

Cotation : {"price", "prev_close", "ts" (ISO), "source", "delayed" (bool)}   Historique : [(date, close)]
"""
import csv
import io
import threading
from datetime import date, datetime, timedelta, timezone
from urllib.parse import quote as q

from collectors import health
from collectors.http import SourceError, fetch, fetch_json, settings

_used, _ulock = {}, threading.Lock()


class Unsupported(Exception):
    """Ce fournisseur ne couvre pas ce symbole ou cette donnée (pas une erreur de la source)."""


def _allow(name, limit):
    """Compteur de requêtes par jour (les offres gratuites ont des quotas stricts)."""
    if not limit:
        return True
    key = (name, date.today().isoformat())
    with _ulock:
        if _used.get(key, 0) >= limit:
            return False
        _used[key] = _used.get(key, 0) + 1
    return True


def quota_used(name):
    with _ulock:
        return _used.get((name, date.today().isoformat()), 0)


def _iso(ts):
    return datetime.fromtimestamp(ts, timezone.utc).isoformat()


def _f(v):
    try:
        x = float(v)
        return x if x == x and x > 0 else None
    except (TypeError, ValueError):
        return None


class Provider:
    name = ""
    label = ""
    key = None               # nom du réglage contenant la clé (None = pas de clé nécessaire)
    signup = ""
    note = ""                # ce que ça apporte
    free_limit = ""
    daily_limit = 0
    realtime = ""

    def key_value(self):
        return (settings().get(self.key) or "").strip() if self.key else ""

    def available(self):
        return (not self.key) or bool(self.key_value())

    def _guard(self):
        if not self._allow_more():
            raise SourceError(self.name, 429, "quota journalier gratuit atteint")

    def _allow_more(self):
        return _allow(self.name, self.daily_limit)

    def quote(self, t):
        raise Unsupported("cotation non disponible")

    def history(self, t, days=400):
        raise Unsupported("historique non disponible")


def _us_only(t):
    if "." in t or "^" in t or "=" in t:
        raise Unsupported("symbole non américain")
    return t


class Yahoo(Provider):
    name, label = "yahoo", "Yahoo Finance"
    note = "Cours en direct (léger différé possible), historique, fondamentaux, analystes"
    free_limit = "sans clé, limité par IP"
    realtime = "quasi temps réel (jusqu'à 15 min de différé selon la bourse)"

    def quote(self, t):
        import yfinance as yf
        from collectors import yahoo
        fi = yahoo.call("cotation", lambda: yf.Ticker(t).fast_info, timeout=4)
        price, prev = _f(fi["last_price"]), _f(fi["previous_close"])
        if not price:
            raise Unsupported("aucun cours")
        return {"price": price, "prev_close": prev, "ts": datetime.now(timezone.utc).isoformat(), "source": self.name, "delayed": True}

    def history(self, t, days=400):
        from collectors.prices import download_closes
        data, errs = download_closes([t], "10y" if days > 1300 else "5y" if days > 520 else "2y" if days > 300 else "1y")
        if t not in data:
            raise SourceError("yahoo", None, (errs or ["aucune donnée"])[0])
        return data[t][-days:]


def parse_stooq_csv(text):
    """CSV Stooq -> [(date, close)] trié par date croissante."""
    out = []
    for row in csv.DictReader(io.StringIO(text)):
        d, c = row.get("Date"), _f(row.get("Close"))
        if d and c:
            out.append((date.fromisoformat(d), c))
    return sorted(out)


class Stooq(Provider):
    name, label = "stooq", "Stooq"
    note = "Clôtures quotidiennes : seconde source indépendante, sans clé, pour vérifier les cours"
    free_limit = "sans clé"
    realtime = "fin de journée (pas de temps réel)"

    def history(self, t, days=400, timeout=25, retries=2):
        _us_only(t)
        d1 = (date.today() - timedelta(days=int(days * 1.5))).strftime("%Y%m%d")
        text = fetch(f"https://stooq.com/q/d/l/?s={q(t.lower())}.us&i=d&d1={d1}&d2={date.today():%Y%m%d}", "stooq",
                      timeout=timeout, retries=retries).decode("utf-8", "replace")
        rows = parse_stooq_csv(text)
        if not rows:
            raise Unsupported("aucune donnée Stooq pour ce symbole")
        return rows[-days:]

    def quote(self, t):
        rows = self.history(t, 10, timeout=4, retries=0)   # chemin interactif : pas le temps d'attendre les nouvelles tentatives
        (d, c), prev = rows[-1], (rows[-2][1] if len(rows) > 1 else None)
        return {"price": c, "prev_close": prev, "ts": datetime.combine(d, datetime.min.time(), timezone.utc).isoformat(), "source": self.name, "delayed": True, "eod": True}


class Finnhub(Provider):
    name, label, key = "finnhub", "Finnhub", "finnhub_api_key"
    signup = "https://finnhub.io/register"
    note = "Cotation en temps réel (actions US), actualités, indicateurs financiers"
    free_limit = "60 requêtes par minute"
    realtime = "temps réel (actions US)"

    def quote(self, t):
        _us_only(t)
        j = fetch_json(f"https://finnhub.io/api/v1/quote?symbol={q(t)}&token={q(self.key_value())}", "finnhub", timeout=4, retries=0)
        if not _f(j.get("c")):
            raise Unsupported("aucun cours")
        return {"price": _f(j["c"]), "prev_close": _f(j.get("pc")), "ts": _iso(j["t"]) if j.get("t") else datetime.now(timezone.utc).isoformat(), "source": self.name, "delayed": False}


class TwelveData(Provider):
    name, label, key = "twelvedata", "Twelve Data", "twelvedata_api_key"
    signup = "https://twelvedata.com/pricing"
    note = "Cotation et historique quotidien (actions US et mondiales)"
    free_limit = "8 requêtes par minute, 800 par jour"
    daily_limit = 780
    realtime = "quasi temps réel (offre gratuite)"

    def _get(self, path, timeout=25, retries=2):
        self._guard()
        j = fetch_json(f"https://api.twelvedata.com/{path}&apikey={q(self.key_value())}", "twelvedata", timeout=timeout, retries=retries)
        if isinstance(j, dict) and j.get("status") == "error":
            code = j.get("code") or 400
            if code in (400, 404):
                raise Unsupported(j.get("message", "symbole inconnu"))
            health.record_error("twelvedata", code, j.get("message", ""))
            raise SourceError("twelvedata", code, j.get("message", "erreur"))
        return j

    def quote(self, t):
        _us_only(t)
        j = self._get(f"quote?symbol={q(t)}", timeout=4, retries=0)
        price = _f(j.get("close"))
        if not price:
            raise Unsupported("aucun cours")
        ts = j.get("timestamp")
        return {"price": price, "prev_close": _f(j.get("previous_close")), "ts": _iso(int(ts)) if ts else datetime.now(timezone.utc).isoformat(),
                "source": self.name, "delayed": False}

    def history(self, t, days=400):
        _us_only(t)
        j = self._get(f"time_series?symbol={q(t)}&interval=1day&outputsize={min(days, 400)}")
        rows = [(date.fromisoformat(v["datetime"][:10]), _f(v["close"])) for v in j.get("values", []) if _f(v.get("close"))]
        if not rows:
            raise Unsupported("aucune donnée")
        return sorted(rows)


class AlphaVantage(Provider):
    name, label, key = "alphavantage", "Alpha Vantage", "alphavantage_api_key"
    signup = "https://www.alphavantage.co/support/#api-key"
    note = "Dernière clôture officielle (vérification), 100 derniers jours"
    free_limit = "25 requêtes par jour"
    daily_limit = 22
    realtime = "fin de journée"

    def _get(self, params, timeout=25, retries=2):
        self._guard()
        j = fetch_json(f"https://www.alphavantage.co/query?{params}&apikey={q(self.key_value())}", "alphavantage", timeout=timeout, retries=retries)
        msg = j.get("Note") or j.get("Information") or j.get("Error Message")
        if msg:
            if "Error Message" in j:
                raise Unsupported("symbole inconnu")
            health.record_error("alphavantage", 429, msg)
            raise SourceError("alphavantage", 429, msg[:120])
        return j

    def quote(self, t):
        _us_only(t)
        g = self._get(f"function=GLOBAL_QUOTE&symbol={q(t)}", timeout=4, retries=0).get("Global Quote") or {}
        price = _f(g.get("05. price"))
        if not price:
            raise Unsupported("aucun cours")
        d = g.get("07. latest trading day")
        return {"price": price, "prev_close": _f(g.get("08. previous close")),
                "ts": datetime.combine(date.fromisoformat(d), datetime.min.time(), timezone.utc).isoformat() if d else datetime.now(timezone.utc).isoformat(),
                "source": self.name, "delayed": True, "eod": True}

    def history(self, t, days=100):
        _us_only(t)
        ts = self._get(f"function=TIME_SERIES_DAILY&symbol={q(t)}&outputsize=compact").get("Time Series (Daily)") or {}
        rows = sorted((date.fromisoformat(d), _f(v["4. close"])) for d, v in ts.items() if _f(v.get("4. close")))
        if not rows:
            raise Unsupported("aucune donnée")
        return rows


class Polygon(Provider):
    name, label, key = "polygon", "Polygon.io", "polygon_api_key"
    signup = "https://polygon.io/dashboard/signup"
    note = "Clôtures quotidiennes officielles des bourses américaines"
    free_limit = "5 requêtes par minute, historique 2 ans"
    realtime = "fin de journée (offre gratuite)"

    def history(self, t, days=400, timeout=25, retries=2):
        _us_only(t)
        d2, d1 = date.today(), date.today() - timedelta(days=int(days * 1.5))
        j = fetch_json(f"https://api.polygon.io/v2/aggs/ticker/{q(t)}/range/1/day/{d1}/{d2}?adjusted=true&sort=asc&limit=5000&apiKey={q(self.key_value())}", "polygon",
                        timeout=timeout, retries=retries)
        rows = [(datetime.fromtimestamp(r["t"] / 1000, timezone.utc).date(), _f(r["c"])) for r in j.get("results", []) if _f(r.get("c"))]
        if not rows:
            raise Unsupported("aucune donnée")
        return rows[-days:]

    def quote(self, t):
        rows = self.history(t, 10, timeout=4, retries=0)
        (d, c), prev = rows[-1], (rows[-2][1] if len(rows) > 1 else None)
        return {"price": c, "prev_close": prev, "ts": datetime.combine(d, datetime.min.time(), timezone.utc).isoformat(), "source": self.name, "delayed": True, "eod": True}


class Tiingo(Provider):
    name, label, key = "tiingo", "Tiingo", "tiingo_api_key"
    signup = "https://www.tiingo.com/account/api/token"
    note = "Cotation IEX en temps réel et historique quotidien"
    free_limit = "50 requêtes par heure, 1000 par jour"
    daily_limit = 900
    realtime = "temps réel (flux IEX)"

    def quote(self, t):
        _us_only(t)
        self._guard()
        j = fetch_json(f"https://api.tiingo.com/iex/{q(t.lower())}?token={q(self.key_value())}", "tiingo", timeout=4, retries=0)
        r = j[0] if isinstance(j, list) and j else None
        price = _f((r or {}).get("last") or (r or {}).get("tngoLast"))
        if not price:
            raise Unsupported("aucun cours")
        return {"price": price, "prev_close": _f(r.get("prevClose")), "ts": r.get("timestamp") or datetime.now(timezone.utc).isoformat(), "source": self.name, "delayed": False}

    def history(self, t, days=400):
        _us_only(t)
        self._guard()
        start = date.today() - timedelta(days=int(days * 1.5))
        j = fetch_json(f"https://api.tiingo.com/tiingo/daily/{q(t.lower())}/prices?startDate={start}&token={q(self.key_value())}", "tiingo")
        rows = [(date.fromisoformat(r["date"][:10]), _f(r.get("adjClose") or r.get("close"))) for r in j if _f(r.get("adjClose") or r.get("close"))]
        if not rows:
            raise Unsupported("aucune donnée")
        return rows[-days:]


REGISTRY = {p.name: p for p in (Yahoo(), Stooq(), Finnhub(), TwelveData(), Tiingo(), Polygon(), AlphaVantage())}
