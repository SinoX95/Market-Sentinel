"""Indicateurs comparables entre plusieurs actions, calculés à partir des seuls cours."""
from math import sqrt

from analysis.lang import L
from analysis.indicators import rsi, sma_series
from analysis.scoring import compute_score


def _ret(c, n):
    return c[-1] / c[-n - 1] - 1 if len(c) > n else None


def stock_metrics(closes):
    if len(closes) < 30:
        return None
    rets = [closes[i] / closes[i - 1] - 1 for i in range(1, len(closes))]
    mean = sum(rets) / len(rets)
    vol = sqrt(sum((r - mean) ** 2 for r in rets) / len(rets)) * sqrt(252)
    res = compute_score(closes)
    s50 = sma_series(closes, 50)[-1]
    s200 = sma_series(closes, 200)[-1] if len(closes) >= 200 else None
    trend = "up" if s200 and closes[-1] > s50 > s200 else "above50" if s50 and closes[-1] > s50 else "below50"
    return {"r1m": _ret(closes, 21), "r3m": _ret(closes, 63), "r6m": _ret(closes, 126), "vol": vol,
            "dd": closes[-1] / max(closes) - 1, "rsi": rsi(closes), "trend": trend, "price": closes[-1],
            "score": res["score"] if res else None, "label": res["label"] if res else None}


def trend_label(code):
    """Libellé de la tendance (codes : up, above50, below50)."""
    return {"up": L("haussière", "uptrend"), "above50": L("au-dessus de la MM50", "above the 50-day MA"),
            "below50": L("sous la MM50", "below the 50-day MA")}.get(code, code or "")
