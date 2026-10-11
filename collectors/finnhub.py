"""Source de secours OPTIONNELLE (clé gratuite sur finnhub.io, à mettre dans config/settings.json : finnhub_api_key).
Utilisée seulement quand Yahoo est bloqué. Limite gratuite : 60 requêtes par minute."""
from datetime import date, datetime, timedelta, timezone
from urllib.parse import quote

from collectors.http import fetch_json, settings

BASE = "https://finnhub.io/api/v1"


def enabled():
    return bool((settings().get("finnhub_api_key") or "").strip())


def _get(path):
    sep = "&" if "?" in path else "?"
    return fetch_json(f"{BASE}{path}{sep}token={quote(settings()['finnhub_api_key'].strip())}", "finnhub")


def news(ticker, days=10):
    today = date.today()
    rows = _get(f"/company-news?symbol={quote(ticker)}&from={today - timedelta(days=days)}&to={today}")
    return [{"title": r["headline"], "url": r["url"], "source": r.get("source") or "Finnhub",
             "published": datetime.fromtimestamp(r["datetime"], timezone.utc).isoformat()}
            for r in rows[:12] if r.get("headline") and r.get("url") and r.get("datetime")]


def fundamentals(ticker):
    """Traduit les indicateurs Finnhub vers les mêmes clés que Yahoo (les pourcentages Finnhub sont en %)."""
    m = (_get(f"/stock/metric?symbol={quote(ticker)}&metric=all") or {}).get("metric") or {}
    p = _get(f"/stock/profile2?symbol={quote(ticker)}") or {}
    pct = lambda k: (m[k] / 100 if m.get(k) is not None else None)
    info = {
        "longName": p.get("name"), "exchange": p.get("exchange"), "industry": p.get("finnhubIndustry"),
        "country": p.get("country"), "website": p.get("weburl"), "currency": p.get("currency"),
        "marketCap": (p["marketCapitalization"] * 1e6) if p.get("marketCapitalization") else None,
        "trailingPE": m.get("peTTM") or m.get("peBasicExclExtraTTM"), "priceToBook": m.get("pbAnnual"),
        "priceToSalesTrailing12Months": m.get("psTTM"), "beta": m.get("beta"),
        "profitMargins": pct("netProfitMarginTTM"), "operatingMargins": pct("operatingMarginTTM"),
        "grossMargins": pct("grossMarginTTM"), "returnOnEquity": pct("roeTTM"), "returnOnAssets": pct("roaTTM"),
        "revenueGrowth": pct("revenueGrowthTTMYoy"), "earningsGrowth": pct("epsGrowthTTMYoy"),
        "debtToEquity": m.get("totalDebt/totalEquityAnnual"), "currentRatio": m.get("currentRatioAnnual"),
        "fiftyTwoWeekHigh": m.get("52WeekHigh"), "fiftyTwoWeekLow": m.get("52WeekLow"),
        "trailingEps": m.get("epsTTM"),
    }
    return {k: v for k, v in info.items() if v is not None}


def search(query):
    rows = (_get(f"/search?q={quote(query)}") or {}).get("result") or []
    return [{"symbol": r["symbol"], "name": r.get("description") or r["symbol"], "exchange": "", "type": r.get("type") or ""}
            for r in rows[:8] if r.get("symbol") and "." not in r["symbol"]]
