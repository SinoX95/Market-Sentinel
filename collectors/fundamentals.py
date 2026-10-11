"""Fiche société complète (fondamentaux, actionnaires, opérations d'initiés) via Yahoo Finance,
avec Finnhub en secours si une clé est configurée."""
import logging

import yfinance as yf

from collectors import finnhub, yahoo

log = logging.getLogger("sentinel")

INFO_KEYS = [
    "longName", "shortName", "exchange", "fullExchangeName", "quoteType", "sector", "industry", "country", "website",
    "marketState",  # REGULAR/PRE/POST/PREPRE/POSTPOST/CLOSED : état du marché fourni par Yahoo, pour savoir si la bourse est ouverte
    "fullTimeEmployees", "longBusinessSummary", "currency", "marketCap", "enterpriseValue", "sharesOutstanding",
    "floatShares", "averageVolume", "beta", "trailingPE", "forwardPE", "trailingPegRatio", "priceToBook",
    "priceToSalesTrailing12Months", "enterpriseToEbitda", "enterpriseToRevenue", "trailingEps", "forwardEps",
    "totalRevenue", "revenueGrowth", "earningsGrowth", "earningsQuarterlyGrowth", "grossMargins", "operatingMargins",
    "profitMargins", "ebitdaMargins", "returnOnEquity", "returnOnAssets", "totalCash", "totalDebt", "debtToEquity",
    "currentRatio", "quickRatio", "freeCashflow", "operatingCashflow", "trailingAnnualDividendYield", "payoutRatio",
    "fiftyTwoWeekHigh", "fiftyTwoWeekLow", "fiftyDayAverage", "twoHundredDayAverage", "targetMeanPrice",
    "targetHighPrice", "targetLowPrice", "numberOfAnalystOpinions", "recommendationKey", "recommendationMean",
    "shortPercentOfFloat", "heldPercentInsiders", "heldPercentInstitutions",
]


def _num(v):
    try:
        return float(v) if v == v else None  # écarte NaN
    except (TypeError, ValueError):
        return None


def _pick(raw):
    out = {}
    for k in INFO_KEYS:
        v = (raw or {}).get(k)
        if v is None:
            continue
        out[k] = v if isinstance(v, str) else (_num(v) if not isinstance(v, bool) else v)
    return {k: v for k, v in out.items() if v is not None}


def collect_fundamentals(ticker):
    errors, info, institutions, insiders = [], {}, [], {"buys": 0, "sells": 0, "recent": []}
    t = yf.Ticker(ticker)
    try:
        info = _pick(yahoo.call("fiche société", lambda: t.info))
    except Exception as e:
        errors.append(f"Yahoo (fiche société) : {e}")
    if len(info) < 5 and finnhub.enabled():
        try:
            info = {**finnhub.fundamentals(ticker), **info}
        except Exception as e:
            errors.append(f"Finnhub : {e}")
    blocked = any("403" in e or "429" in e for e in errors)
    if not blocked:
        try:
            df = yahoo.call("actionnaires", lambda: t.institutional_holders)
            if df is not None and not df.empty:
                for _, r in df.head(8).iterrows():
                    institutions.append({"holder": str(r.get("Holder") or ""), "pct": _num(r.get("pctHeld")),
                                         "shares": _num(r.get("Shares")), "value": _num(r.get("Value")),
                                         "change": _num(r.get("pctChange"))})
        except Exception as e:
            errors.append(f"Yahoo (actionnaires) : {e}")
        try:
            df = yahoo.call("initiés", lambda: t.insider_transactions)
            if df is not None and not df.empty:
                for _, r in df.head(10).iterrows():
                    text = str(r.get("Text") or "")
                    low = text.lower()
                    insiders["buys"] += "purchase" in low or "buy" in low
                    insiders["sells"] += "sale" in low or "sold" in low
                    d = r.get("Start Date")
                    insiders["recent"].append({"date": str(d)[:10] if d is not None else "", "insider": str(r.get("Insider") or ""),
                                               "position": str(r.get("Position") or ""), "text": text,
                                               "shares": _num(r.get("Shares")), "value": _num(r.get("Value"))})
        except Exception as e:
            errors.append(f"Yahoo (initiés) : {e}")
    return {"info": info, "institutions": institutions, "insiders": insiders}, errors
