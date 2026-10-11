"""Recherche d'entreprise : index local SEC (rapide, sans Yahoo) + recherche Yahoo, Finnhub en secours."""
import logging
import re
import threading
import time
from datetime import datetime, timezone

import yfinance as yf

from backend import db
from collectors import finnhub, yahoo
from collectors.http import contact_email, fetch_json

log = logging.getLogger("sentinel")
SEC_INDEX_URL = "https://www.sec.gov/files/company_tickers.json"
_mem = {"index": None, "at": 0}
_cache, _lock = {}, threading.Lock()


def sec_index(max_age_h=24 * 7):
    """[(ticker, nom, cik)] : téléchargé une fois par semaine, gardé en base."""
    if _mem["index"] is not None and time.time() - _mem["at"] < 3600:
        return _mem["index"]
    data, updated = db.kv_get("sec:tickers")
    stale = not data or not updated or (datetime.now(timezone.utc) - updated).total_seconds() > max_age_h * 3600
    if stale and contact_email():
        try:
            raw = fetch_json(SEC_INDEX_URL, "sec")
            data = [[v["ticker"], v["title"], v["cik_str"]] for v in raw.values()]
            db.kv_set("sec:tickers", data)
        except Exception as e:
            log.warning("index SEC : %s", e)
    _mem["index"], _mem["at"] = data or [], time.time()
    return _mem["index"]


def names_by_ticker():
    return {t: n for t, n, _ in sec_index()}


def _local(query, limit=8):
    q = query.strip().lower()
    if not q:
        return []
    scored = []
    for ticker, name, _ in sec_index():
        tl, nl = ticker.lower(), name.lower()
        if tl == q:
            s = 0
        elif tl.startswith(q):
            s = 1
        elif nl.startswith(q):
            s = 2
        elif q in nl:
            s = 3
        else:
            continue
        scored.append((s, len(ticker), ticker, name))
    scored.sort()
    return [{"symbol": t, "name": n.title() if n.isupper() else n, "exchange": "US", "type": "EQUITY", "src": "SEC"}
            for _, _, t, n in scored[:limit]]


def _yahoo(query, limit=8):
    res = yahoo.call("recherche", lambda: yf.Search(query, max_results=limit, news_count=0))
    out = []
    for q in getattr(res, "quotes", []) or []:
        if q.get("quoteType") not in ("EQUITY", "ETF") or not q.get("symbol"):
            continue
        out.append({"symbol": q["symbol"], "name": q.get("longname") or q.get("shortname") or q["symbol"],
                    "exchange": q.get("exchDisp") or q.get("exchange") or "", "type": q.get("quoteType"), "src": "Yahoo"})
    return out


def search(query):
    """Retourne ({résultats}, [erreurs])."""
    query = (query or "").strip()[:60]
    if len(query) < 1 or not re.search(r"[A-Za-z0-9]", query):
        return [], []
    with _lock:
        hit = _cache.get(query.lower())
        if hit and time.time() - hit[0] < 600:
            return hit[1], []
    results, errors = _local(query), []
    if len(results) < 5:
        providers = [("Yahoo", _yahoo)] + ([("Finnhub", finnhub.search)] if finnhub.enabled() else [])
        for label, fn in providers:
            try:
                extra = fn(query)
                if extra:
                    results += extra
                    break
            except Exception as e:
                errors.append(f"{label} : {e}")
    seen, merged = set(), []
    for r in results:
        if r["symbol"] not in seen:
            seen.add(r["symbol"])
            merged.append(r)
    merged = merged[:10]
    if merged:
        with _lock:
            _cache[query.lower()] = (time.time(), merged)
    return merged, errors
