"""Recoupement des sources de cours : plusieurs fournisseurs, comparaison, remplacement d'une source bloquée."""
import concurrent.futures as cf
import logging
import statistics
import threading
import time
from datetime import datetime, timezone

from collectors import health
from collectors.providers import REGISTRY, Unsupported

log = logging.getLogger("sentinel")
QUOTE_ORDER = ["finnhub", "tiingo", "twelvedata", "yahoo", "polygon", "stooq", "alphavantage"]      # temps réel d'abord
HISTORY_ORDER = ["yahoo", "stooq", "tiingo", "twelvedata", "polygon", "alphavantage"]
VERIFY_ORDER = ["stooq", "tiingo", "twelvedata", "polygon", "alphavantage", "yahoo"]                # indépendantes de Yahoo d'abord
_cache, _lock = {}, threading.Lock()


def available(names):
    return [REGISTRY[n] for n in names if n in REGISTRY and REGISTRY[n].available() and not health.is_paused(n)]


def _cached(key, ttl, fn):
    with _lock:
        hit = _cache.get(key)
        if hit and time.time() - hit[0] < ttl:
            return hit[1]
    val = fn()
    with _lock:
        _cache[key] = (time.time(), val)
    return val


def history_fallback(ticker, days=400, skip=(), min_rows=150):
    """Premier fournisseur (hors 'skip') qui renvoie un historique assez long. Retourne (source, [(date, close)], erreurs)."""
    errors, best = [], (None, [])
    for p in available(HISTORY_ORDER):
        if p.name in skip:
            continue
        try:
            rows = p.history(ticker, days)
        except Unsupported as e:
            errors.append(f"{p.label} : {e}")
            continue
        except Exception as e:
            errors.append(f"{p.label} : {e}")
            continue
        if len(rows) >= days * 0.9:  # historique complet : inutile d'interroger les autres
            return p.name, rows, errors
        if len(rows) > len(best[1]):  # sinon on garde le plus long (certaines offres gratuites limitent à 1 ou 2 ans)
            best = (p.name, rows)
        errors.append(f"{p.label} : historique partiel ({len(rows)} jours)")
    if len(best[1]) >= min_rows:
        return best[0], best[1], errors
    return None, [], errors


def quote_all(ticker, max_sources=3, live_only=False):
    """Cotations récentes venant de plusieurs fournisseurs, interrogés en parallèle (chacun a son propre délai court,
    voir providers.py) au lieu de les attendre un par un : le temps total est celui du plus lent, pas leur somme.
    Retourne (liste, erreurs). Mise en cache 60 s."""
    def run():
        providers = available(QUOTE_ORDER)
        out, errors = [], []
        if not providers:
            return out, errors
        ex = cf.ThreadPoolExecutor(max_workers=len(providers))
        futs = {ex.submit(p.quote, ticker): p for p in providers}
        try:
            for fut in cf.as_completed(futs, timeout=6):
                p = futs[fut]
                try:
                    qt = fut.result()
                except Unsupported:
                    continue
                except Exception as e:
                    errors.append(f"{p.label} : {e}")
                    continue
                if live_only and qt.get("eod"):
                    continue
                out.append(qt)
                if len(out) >= max_sources:
                    break
        except cf.TimeoutError:
            pass  # certaines sources n'ont pas répondu à temps : on garde celles qui ont déjà répondu
        finally:
            ex.shutdown(wait=False)  # les threads encore en cours se terminent tout seuls, sans bloquer l'appelant
        return out, errors
    return _cached(("quotes", ticker, max_sources, live_only), 60, run)


def consensus_price(quotes, tol=0.01):
    """Prix médian des cotations les plus fraîches et accord entre sources."""
    if not quotes:
        return None
    pool = [q for q in quotes if not q.get("eod")] or quotes
    prices = [q["price"] for q in pool]
    med = statistics.median(prices)
    agree = [q for q in pool if abs(q["price"] / med - 1) <= tol]
    return {"price": round(med, 4), "n": len(pool), "agree": len(agree), "spread_pct": round((max(prices) - min(prices)) / med * 100, 3),
            "live": any(not q.get("delayed") for q in pool), "eod": all(q.get("eod") for q in pool),
            "sources": [{"source": q["source"], "label": REGISTRY[q["source"]].label, "price": q["price"], "ts": q["ts"], "delayed": q.get("delayed", True),
                         "outlier": abs(q["price"] / med - 1) > tol} for q in pool]}


def live_quotes(tickers, ttl=30, budget_s=20):
    """Dernier cours de chaque action, première source en temps réel disponible. {ticker: {price, change_pct, source, ts}}"""
    out, t0 = {}, time.time()
    for t in tickers:
        if time.time() - t0 > budget_s:
            break

        def one(t=t):
            for p in available(QUOTE_ORDER):
                try:
                    qt = p.quote(t)
                except Exception:
                    continue
                if qt.get("eod"):
                    continue
                pc = qt.get("prev_close")
                return {"price": qt["price"], "change_pct": round((qt["price"] / pc - 1) * 100, 2) if pc else None,
                        "source": qt["source"], "ts": qt["ts"], "delayed": qt.get("delayed", True)}
            return None
        r = _cached(("live", t), ttl, one)
        if r:
            out[t] = r
    return out


def verify_close(ticker, series, base_source=None, max_sources=3, tol=0.003, only=None):
    """Recoupe la dernière clôture stockée avec d'autres fournisseurs, le même jour."""
    if not series:
        return None
    d0, c0 = series[-1]
    others, errors = [], []
    for p in available(VERIFY_ORDER):
        if p.name == base_source or len(others) >= max_sources or (only and p.name not in only):
            continue
        try:
            rows = p.history(ticker, 10)
        except Unsupported:
            continue
        except Exception as e:
            errors.append(f"{p.label} : {e}")
            continue
        by = dict(rows)
        if d0 in by:
            others.append({"source": p.name, "label": p.label, "date": d0.isoformat(), "close": by[d0], "diff_pct": round((by[d0] / c0 - 1) * 100, 3)})
        elif rows:
            others.append({"source": p.name, "label": p.label, "date": rows[-1][0].isoformat(), "close": rows[-1][1], "diff_pct": None})
    comparable = [o for o in others if o["diff_pct"] is not None]
    agree = sum(1 for o in comparable if abs(o["diff_pct"]) <= tol * 100)
    if not comparable:
        level = "single"
    elif agree < len(comparable):
        level = "warn"
    else:
        level = "high" if len(comparable) >= 2 else "good"
    return {"date": d0.isoformat(), "base": {"source": base_source or "yahoo", "close": round(c0, 4)}, "others": others, "n_compared": len(comparable),
            "n_agree": agree, "level": level, "errors": errors[:3], "checked": datetime.now(timezone.utc).isoformat()}
