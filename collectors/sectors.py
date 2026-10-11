"""Détection des secteurs en hausse (ETF sectoriels/thématiques) puis des meilleures entreprises de ces secteurs."""
import json
import logging
from pathlib import Path

import yfinance as yf

from analysis.sectors import best_companies, sector_metrics
from collectors import yahoo
from collectors.prices import download_closes

log = logging.getLogger("sentinel")
CONFIG = Path("config/sectors.json")


def load_config():
    return json.loads(CONFIG.read_text(encoding="utf-8"))


def etf_holdings(etf, limit=12):
    """Principales lignes de l'ETF (composition à jour). Retourne [(symbole, nom)] ou [] si indisponible."""
    try:
        df = yahoo.call(f"composition {etf}", lambda: yf.Ticker(etf).funds_data.top_holdings)
    except Exception as e:
        log.info("composition %s indisponible : %s", etf, e)
        return []
    if df is None or getattr(df, "empty", True):
        return []
    out = []
    for idx, row in df.head(limit).iterrows():
        sym = str(idx if isinstance(idx, str) else row.get("Symbol", "")).strip()
        if sym and sym.replace("-", "").replace(".", "").isalnum() and "." not in sym:
            out.append((sym, str(row.get("Name") or "")))
    return out


def sector_by_id(sid):
    return next((s for s in load_config()["sectors"] if s["id"] == sid), None)


def rank_sector(sector, r3m, limit=24, top=24):
    """Classe les entreprises d'un secteur (composition de l'ETF + liste de secours). Retourne (classement, erreurs)."""
    holdings = etf_holdings(sector["etf"])
    names = {sym: nm for sym, nm in holdings if nm}
    syms = list(dict.fromkeys([sym for sym, _ in holdings] + sector["universe"]))[:limit]
    prices, errors = download_closes(syms, "1y")
    best = best_companies({t: [c for _, c in prices[t]] for t in syms if t in prices}, r3m, top=top)
    for b in best:
        b["name"] = names.get(b["ticker"], "")
    return best, errors


def collect_sectors():
    cfg = load_config()
    sectors, errors = cfg["sectors"], []
    etfs = [s["etf"] for s in sectors] + [cfg["benchmark"]]
    data, errs = download_closes(etfs, "1y")
    errors += errs
    bench = [c for _, c in data.get(cfg["benchmark"], [])]
    rows, series = [], {}
    for s in sectors:
        pts = data.get(s["etf"])
        if not pts:
            continue
        closes = [c for _, c in pts]
        m = sector_metrics(closes, bench)
        if not m:
            continue
        series[s["etf"]] = pts
        rows.append({**s, **m, "spark": [round(x, 2) for x in closes[-60:]], "hot": False, "best": []})
    rows.sort(key=lambda r: -r["score"])
    hot = rows[: cfg.get("top_count", 5)]  # les meilleurs secteurs : on classe leurs entreprises
    for r in hot:
        r["hot"] = r["score"] >= 55

    # Univers d'entreprises : composition à jour de l'ETF + liste de secours, sans doublon
    names, universe = {}, {}
    for r in hot:
        holdings = etf_holdings(r["etf"])
        names.update({sym: nm for sym, nm in holdings if nm})
        syms = list(dict.fromkeys([sym for sym, _ in holdings] + r["universe"]))[: cfg.get("cycle_universe_size", 12)]
        universe[r["id"]] = syms
    wanted = sorted({t for syms in universe.values() for t in syms})
    prices, errs = download_closes(wanted, "1y") if wanted else ({}, [])
    errors += errs
    for r in hot:
        closes_by_t = {t: [c for _, c in prices[t]] for t in universe[r["id"]] if t in prices}
        best = best_companies(closes_by_t, r["r3m"], top=5)
        for b in best:
            b["name"] = names.get(b["ticker"], "")
        r["best"] = best
    ref = [(r["etf"], series[r["etf"]]) for r in rows[:6]]
    dates = sorted({d for _, pts in ref for d, _ in pts})[-126:]
    chart = {"dates": [d.isoformat() for d in dates],
             "series": {etf: [dict(pts).get(d) for d in dates] for etf, pts in ref}}
    bm = sector_metrics(bench, None) if bench else None
    return {"sectors": rows, "chart": chart, "benchmark": {"etf": cfg["benchmark"], "r3m": bm["r3m"] if bm else None},
            "n_hot": len(hot)}, errors
