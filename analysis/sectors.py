"""Momentum des secteurs (via leurs ETF) et classement des meilleures entreprises d'un secteur."""
from analysis.lang import L, pct
from analysis.indicators import rsi, sma_series
from analysis.scoring import compute_score

W = {"r3m": 0.25, "r1m": 0.15, "r6m": 0.15, "rs": 0.25, "trend": 0.20}


def _scale(x, lo, hi):
    return max(0.0, min(100.0, (x - lo) / (hi - lo) * 100))


def _ret(closes, n):
    return closes[-1] / closes[-n - 1] - 1 if len(closes) > n else None


def state_for(score):
    if score >= 70:
        return "strong_up"
    if score >= 55:
        return "up"
    if score >= 45:
        return "neutral"
    return "down"


def state_label(code):
    return {"strong_up": L("Forte hausse", "Strong uptrend"), "up": L("Hausse", "Uptrend"), "neutral": L("Neutre", "Neutral"),
            "down": L("Baisse", "Downtrend")}.get(code, code or "")


def trend_label(code):
    return {"up": L("haussière", "uptrend"), "correction": L("correction dans une tendance haussière", "pullback within an uptrend"),
            "rebound": L("rebond", "rebound"), "down": L("baissière", "downtrend")}.get(code, code or "")


def sector_metrics(closes, bench):
    """closes, bench : cours de l'ETF et de l'indice de référence (mêmes jours, du plus ancien au plus récent)."""
    if len(closes) < 70:
        return None
    r1, r3, r6 = _ret(closes, 21), _ret(closes, 63), _ret(closes, 126)
    b3 = _ret(bench, 63) if bench and len(bench) > 63 else 0.0
    rs = (r3 - b3) if r3 is not None else 0.0
    price = closes[-1]
    s50 = sma_series(closes, 50)[-1]
    s200 = sma_series(closes, 200)[-1] if len(closes) >= 200 else None
    if s200 and price > s50 > s200:
        trend, tscore = "up", 100
    elif s200 and price > s200:
        trend, tscore = "correction", 60
    elif price > s50:
        trend, tscore = "rebound", 45
    else:
        trend, tscore = "down", 15
    r6v = r6 if r6 is not None else r3
    comp = {"r3m": _scale(r3, -0.10, 0.25), "r1m": _scale(r1, -0.05, 0.12), "r6m": _scale(r6v, -0.10, 0.40),
            "rs": _scale(rs, -0.08, 0.10), "trend": tscore}
    score = round(sum(comp[k] * w for k, w in W.items()), 1)
    return {"score": score, "state": state_for(score), "r1m": r1, "r3m": r3, "r6m": r6, "rs3m": rs,
            "trend": trend, "rsi": rsi(closes)}


def best_companies(universe, sector_r3m, top=5):
    """universe : {ticker: closes}. Classe par score technique + surperformance vs secteur, pénalise le surachat."""
    rows = []
    for t, closes in universe.items():
        res = compute_score(closes)
        if not res:
            continue
        r3 = _ret(closes, 63)
        rs = (r3 - sector_r3m) if (r3 is not None and sector_r3m is not None) else 0.0
        r = rsi(closes)
        s50 = sma_series(closes, 50)[-1]
        s200 = sma_series(closes, 200)[-1] if len(closes) >= 200 else None
        key = res["score"] + max(-8, min(8, rs * 100)) - (10 if r and r > 80 else 0)
        flag = "full" if (s200 and closes[-1] > s50 > s200) else "above50" if (s50 and closes[-1] > s50) else None
        rows.append({"ticker": t, "price": round(closes[-1], 2), "score": res["score"], "label": res["label"],
                     "r3m": r3, "rs3m": rs, "rsi": r, "rank": round(key, 1), "trend_flag": flag})
    rows.sort(key=lambda x: -x["rank"])
    return rows[:top]


def reasons_for(row, has_sector_ref=True):
    """Raisons affichées d'une ligne de best_companies, dans la langue courante (à appeler à la lecture, rien n'est stocké)."""
    out = []
    r3, rs, r = row.get("r3m"), row.get("rs3m"), row.get("rsi")
    if r3 is not None:
        rel = (L(f" ({rs * 100:+.0f} pts vs le secteur)", f" ({rs * 100:+.0f} pts vs sector)") if has_sector_ref else "")
        out.append(L(f"{pct(r3 * 100, 0, True)} sur 3 mois", f"{pct(r3 * 100, 0, True)} over 3 months") + rel)
    flag = row.get("trend_flag")
    if flag == "full":
        out.append(L("tendance haussière (cours > MM50 > MM200)", "uptrend (price > 50-day MA > 200-day MA)"))
    elif flag == "above50":
        out.append(L("au-dessus de sa MM50", "above its 50-day MA"))
    if r:
        out.append(f"RSI {r:.0f}" + (L(" (surachat)", " (overbought)") if r > 75 else L(" (survendu)", " (oversold)") if r < 35 else ""))
    return out
