"""Calendrier (résultats), avis d'analystes et objectifs de cours, via yfinance."""
import logging
from datetime import date, datetime

import yfinance as yf

from collectors import yahoo

log = logging.getLogger("sentinel")


def _iso(d):
    if isinstance(d, datetime):
        return d.date().isoformat()
    if isinstance(d, date):
        return d.isoformat()
    return str(d)[:10]


def collect_events(ticker):
    t = yf.Ticker(ticker)
    ev = {"earnings_dates": [], "analyst": [], "target": None}
    errors = []

    def step(label, fn):
        try:
            fn()
        except Exception as e:
            errors.append(f"{label} : {e}")
            if getattr(e, "source", None) == "yahoo" and getattr(e, "status", None) in (403, 429):
                raise  # Yahoo nous bloque : inutile d'insister sur les autres appels

    def calendar():
        cal = yahoo.call("calendrier", lambda: t.calendar)
        dates = cal.get("Earnings Date") if isinstance(cal, dict) else None
        ev["earnings_dates"] = sorted({_iso(d) for d in (dates or [])})

    def analysts():
        df = yahoo.call("analystes", lambda: t.upgrades_downgrades)
        if df is not None and not df.empty:
            for idx, row in df.head(12).iterrows():
                ev["analyst"].append({"date": _iso(idx), "firm": str(row.get("Firm") or ""),
                                      "action": str(row.get("Action") or ""),
                                      "from": str(row.get("FromGrade") or ""), "to": str(row.get("ToGrade") or "")})

    def targets():
        pt = yahoo.call("objectifs", lambda: t.analyst_price_targets)
        if isinstance(pt, dict) and pt.get("mean"):
            ev["target"] = {k: (float(pt[k]) if pt.get(k) is not None else None) for k in ("current", "mean", "high", "low")}

    try:
        step("calendrier", calendar)
        step("analystes", analysts)
        step("objectifs", targets)
    except Exception:
        pass
    return ev, errors
