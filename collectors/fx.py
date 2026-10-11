"""Taux de change : Banque centrale européenne (taux de référence, gratuit, sans clé), Frankfurter en secours ;
historique quotidien via Frankfurter (données de la BCE) pour convertir correctement les courbes."""
import xml.etree.ElementTree as ET
from datetime import date, datetime, timedelta, timezone

from backend import db
from collectors.http import fetch, fetch_json

ECB = "https://www.ecb.europa.eu/stats/eurofxref/eurofxref-daily.xml"
SUPPORTED = ["EUR", "USD", "GBP", "CHF", "CAD", "JPY", "AUD"]
SUFFIX_CCY = {"PA": "EUR", "DE": "EUR", "F": "EUR", "AS": "EUR", "BR": "EUR", "MI": "EUR", "MC": "EUR", "L": "GBP", "TO": "CAD", "V": "CAD",
              "SW": "CHF", "T": "JPY", "AX": "AUD", "HK": "HKD", "ST": "SEK", "CO": "DKK", "OL": "NOK"}


def asset_currency(ticker):
    """Devise de cotation : celle de Yahoo si connue, sinon déduite du suffixe de la place de cotation, sinon USD."""
    info = (db.kv_get(f"fund:{ticker}")[0] or {}).get("info", {})
    if info.get("currency"):
        return info["currency"]
    return SUFFIX_CCY.get(ticker.rsplit(".", 1)[-1], "USD") if "." in ticker else "USD"


MAX_ECB_BYTES = 2 * 1024 * 1024  # le fichier de la BCE pèse ~3 ko : au-delà, la réponse n'est pas celle attendue


def _parse_ecb(xml_bytes):
    # Le flux vient du réseau : on refuse ce qui est anormalement gros ou contient une déclaration d'entités
    # (expansion récursive, « billion laughs »), avant de le confier à l'analyseur XML.
    if len(xml_bytes) > MAX_ECB_BYTES:
        raise ValueError(f"réponse BCE anormalement grosse ({len(xml_bytes)} octets)")
    if b"<!DOCTYPE" in xml_bytes[:4096] or b"<!ENTITY" in xml_bytes:
        raise ValueError("réponse BCE refusée : déclaration d'entités XML")
    root = ET.fromstring(xml_bytes)
    rates, day = {"EUR": 1.0}, None
    for c in root.iter():
        if c.tag.endswith("Cube") and c.get("currency") and c.get("rate"):
            rates[c.get("currency")] = float(c.get("rate"))
        elif c.tag.endswith("Cube") and c.get("time"):
            day = c.get("time")
    return {"base": "EUR", "date": day, "rates": rates, "source": "BCE"}


def latest_rates(max_age_h=3):
    """Taux du jour (1 EUR = x devise), gardés en base 3 h. None si aucune source ne répond."""
    data, updated = db.kv_get("fx:latest")
    if data and updated and (datetime.now(timezone.utc) - updated).total_seconds() < max_age_h * 3600:
        return data
    if data:  # anciens taux servis tout de suite, mise à jour en arrière-plan
        if "fx:latest" not in _fetching:
            import threading
            _fetching.add("fx:latest")

            def run():
                try:
                    _refresh_latest()
                finally:
                    _fetching.discard("fx:latest")
            threading.Thread(target=run, daemon=True).start()
        return data
    return _refresh_latest()


def _refresh_latest():
    data = None
    for _label, fn in (("BCE", lambda: _parse_ecb(fetch(ECB, "ecb"))),
                      ("Frankfurter", lambda: (lambda j: {"base": "EUR", "date": j["date"], "rates": {"EUR": 1.0, **j["rates"]}, "source": "Frankfurter"})(
                          fetch_json("https://api.frankfurter.dev/v1/latest?base=EUR", "frankfurter")))):
        try:
            data = fn()
            if len(data["rates"]) > 5:
                db.kv_set("fx:latest", data)
                return data
        except Exception:
            continue
    return None


def history(frm, to, days=400):
    """Taux quotidiens frm -> to : {date_iso: taux}. Vide si indisponible."""
    if frm == to:
        return {}
    frm = "GBP" if frm == "GBp" else frm
    key = f"fx:hist:{frm}:{to}"
    data, updated = db.kv_get(key)
    if data and updated and (datetime.now(timezone.utc) - updated).total_seconds() < 6 * 3600:
        return data
    try:
        start = date.today() - timedelta(days=int(days * 1.5))
        j = fetch_json(f"https://api.frankfurter.dev/v1/{start}..{date.today()}?base={frm}&symbols={to}", "frankfurter", timeout=40)
        data = {d: v[to] for d, v in j.get("rates", {}).items() if to in v}
        if data:
            db.kv_set(key, data)
    except Exception:
        pass
    return data or {}


def series_rate(hist, days, fallback):
    """Taux applicable à chaque date (jour ouvré précédent si absent) ; 'fallback' avant le premier taux connu."""
    out, last = [], None
    keys = sorted(hist)
    i = 0
    for d in days:
        while i < len(keys) and keys[i] <= d:
            last = hist[keys[i]]
            i += 1
        out.append(last if last is not None else fallback)
    return out


_fetching = set()


def history_cached(frm, to, days=2600):
    """Historique déjà enregistré (même ancien), sans jamais attendre le réseau ; le rafraîchit en arrière-plan si besoin."""
    import threading
    frm = "GBP" if frm == "GBp" else frm
    if frm == to:
        return {}
    key = f"fx:hist:{frm}:{to}"
    data, updated = db.kv_get(key)
    fresh = data and updated and (datetime.now(timezone.utc) - updated).total_seconds() < 6 * 3600
    if not fresh and key not in _fetching:
        _fetching.add(key)

        def run():
            try:
                history(frm, to, days)
            finally:
                _fetching.discard(key)
        threading.Thread(target=run, daemon=True).start()
    return data or {}
