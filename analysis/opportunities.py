"""Moteur d'opportunités : chaque signal est concret (chiffre, date, source) et pondéré."""
from datetime import date, datetime, timedelta, timezone

from analysis.lang import L, date_s, money, money_big
from analysis.indicators import rsi, sma_series
from analysis.scoring import compute_score
from analysis.fr_terms import analyst_row

def fr(iso):
    """2026-10-03 -> 03/10/2026 (fr) ou Oct 3, 2026 (en)"""
    return date_s(iso)


LEVELS = [(5.0, "strong"), (2.5, "medium"), (0.0, "weak")]


def level_for(total):
    """Code stable : strong / medium / weak / caution."""
    return next((lab for thr, lab in LEVELS if total >= thr), "caution")


def level_label(code):
    return {"strong": L("Forte", "Strong"), "medium": L("Moyenne", "Medium"), "weak": L("Faible", "Weak"),
            "caution": L("Prudence", "Caution")}.get(code, code or "")


def technical_signals(closes, currency="USD"):
    out = []
    if len(closes) < 60:
        return out
    price = closes[-1]
    s50, s200 = sma_series(closes, 50)[-1], (sma_series(closes, 200)[-1] if len(closes) >= 200 else None)
    uptrend = s50 is not None and price > s50 * 0.97 and (s200 is None or s50 > s200)
    high3m = max(closes[-63:])
    dd = price / high3m - 1
    if uptrend and -0.15 <= dd <= -0.04:
        out.append({"kind": "technique", "impact": 2.0,
                    "text": L(f"Repli de {abs(dd) * 100:.1f} % depuis le plus haut à 3 mois, alors que la tendance de fond reste haussière"
                              + (" (moyenne 50 j au-dessus de la moyenne 200 j)." if s200 else "."),
                              f"Pullback of {abs(dd) * 100:.1f}% from the 3-month high, while the long-term trend remains up"
                              + (" (50-day average above the 200-day average)." if s200 else "."))})
    if s50 and uptrend and abs(price / s50 - 1) < 0.02:
        out.append({"kind": "technique", "impact": 1.0,
                    "text": L(f"Cours au contact de sa moyenne mobile 50 jours ({money(s50, currency)}), un support fréquent en tendance haussière.",
                              f"Price is touching its 50-day moving average ({money(s50, currency)}), a frequent support in an uptrend.")})
    r = rsi(closes)
    if r is not None and r < 35:
        out.append({"kind": "technique", "impact": 1.5 if uptrend else 0.5,
                    "text": L(f"RSI 14 jours à {r:.0f} : zone de survente, un rebond est plus probable.",
                              f"14-day RSI at {r:.0f}: oversold zone, a rebound is more likely.")})
    elif r is not None and r > 75:
        out.append({"kind": "technique", "impact": -1.5,
                    "text": L(f"RSI 14 jours à {r:.0f} : zone de surachat, mieux vaut attendre un repli.",
                              f"14-day RSI at {r:.0f}: overbought zone, better to wait for a pullback.")})
    res = compute_score(closes)
    if res:
        out.append({"kind": "technique", "key": "score", "impact": round((res["score"] - 50) / 25, 2),
                    "text": L(f"Score technique {int(res['score'] + 0.5)}/100 ({res['label']}).", f"Technical score {int(res['score'] + 0.5)}/100 ({res['label']}).")})
    return out


def analyst_signals(events, today, currency="USD"):
    out = []
    for a in (events or {}).get("analyst", []):
        a = analyst_row(a)  # recommandations traduites en français (interface française)
        try:
            d = date.fromisoformat(a["date"])
        except Exception:
            continue
        if (today - d).days > 45:
            continue
        act = a["code"]
        if act == "up":
            out.append({"kind": "analystes", "impact": 1.5, "date": a["date"], "source": a["firm"],
                        "text": L(f"{a['firm']} relève sa recommandation : {a['from'] or '?'} → {a['to']} ({fr(a['date'])}).",
                                  f"{a['firm']} upgrades its rating: {a['from'] or '?'} → {a['to']} ({fr(a['date'])}).")})
        elif act == "down":
            out.append({"kind": "analystes", "impact": -1.5, "date": a["date"], "source": a["firm"],
                        "text": L(f"{a['firm']} abaisse sa recommandation : {a['from'] or '?'} → {a['to']} ({fr(a['date'])}).",
                                  f"{a['firm']} downgrades its rating: {a['from'] or '?'} → {a['to']} ({fr(a['date'])}).")})
        elif act == "init":
            out.append({"kind": "analystes", "impact": 0.5, "date": a["date"], "source": a["firm"],
                        "text": L(f"{a['firm']} commence à suivre l'action avec « {a['to']} » ({fr(a['date'])}).",
                                  f"{a['firm']} initiates coverage with \"{a['to']}\" ({fr(a['date'])}).")})
    t = (events or {}).get("target")
    if t and t.get("mean") and t.get("current"):
        up = (t["mean"] / t["current"] - 1) * 100
        if up >= 15:
            out.append({"kind": "analystes", "impact": 1.0,
                        "text": L(f"Objectif de cours moyen des analystes : {money(t['mean'], currency)} (+{up:.0f} % par rapport au cours).",
                                  f"Average analyst price target: {money(t['mean'], currency)} (+{up:.0f}% vs. the current price).")})
        elif up <= 0:
            out.append({"kind": "analystes", "impact": -1.0,
                        "text": L(f"Objectif de cours moyen des analystes : {money(t['mean'], currency)} ({up:.0f} % : déjà atteint).",
                                  f"Average analyst price target: {money(t['mean'], currency)} ({up:.0f}%: already reached).")})
    return out


def calendar_signals(events, today):
    out = []
    for d in (events or {}).get("earnings_dates", []):
        try:
            dd = date.fromisoformat(d)
        except Exception:
            continue
        n = (dd - today).days
        if 0 <= n <= 45:
            out.append({"kind": "calendrier", "impact": 0.5, "date": d,
                        "text": L(f"Résultats trimestriels prévus le {fr(d)} (dans {n} jour{'s' if n > 1 else ''}) : "
                                  "un catalyseur, à la hausse comme à la baisse.",
                                  f"Quarterly earnings expected on {fr(d)} (in {n} day{'s' if n > 1 else ''}): "
                                  "a catalyst, up or down.")})
            break
    return out


def news_signals(news, now):
    out, pos, neg = [], 0, 0
    for n in news or []:
        try:
            age = now - datetime.fromisoformat(n["published"])
        except Exception:
            continue
        if age > timedelta(days=7) or not n.get("sentiment"):
            continue
        if n["sentiment"] > 0 and pos < 2:
            pos += 1
        elif n["sentiment"] < 0 and neg < 2:
            neg += 1
        else:
            continue
        out.append({"kind": "news", "impact": 1.0 * n["sentiment"], "url": n["url"], "source": n["source"],
                    "date": n["published"][:10], "text": n["title"]})
    return out


def official_signals(insiders, filings, today):
    """Signaux tirés des dépôts officiels de la SEC : transactions de dirigeants et résultats publiés."""
    out = []
    if insiders and insiders.get("buys") and insiders.get("buy_value", 0) >= 100_000:
        t = next((x for x in insiders["trades"] if x["code"] == "P"), None)
        v = insiders["buy_value"]
        val = money_big(v)
        out.append({"kind": "investisseurs", "impact": 1.5 if insiders["buys"] >= 2 else 1.0, "source": L("SEC (formulaire 4)", "SEC (Form 4)"), "date": t["filed"] if t else None,
                    "url": t["url"] if t else None,
                    "text": L(f"Dirigeants : {insiders['buys']} achat(s) sur le marché (≈ {val}) et {insiders['sells']} vente(s) sur {insiders['days']} jours.",
                              f"Insiders: {insiders['buys']} open-market purchase(s) (≈ {val}) and {insiders['sells']} sale(s) over {insiders['days']} days.")})
    elif insiders and insiders.get("sells", 0) >= 3 and not insiders.get("buys") and insiders.get("sell_value", 0) >= 5_000_000:
        out.append({"kind": "investisseurs", "impact": -0.5, "source": L("SEC (formulaire 4)", "SEC (Form 4)"),
                    "text": L(f"Dirigeants : {insiders['sells']} ventes sur le marché (≈ {money_big(insiders['sell_value'])}) sur {insiders['days']} jours, sans achat (les ventes sont fréquentes et pas toujours négatives).",
                              f"Insiders: {insiders['sells']} open-market sales (≈ {money_big(insiders['sell_value'])}) over {insiders['days']} days, with no purchases (sales are common and not always negative).")})
    for f in filings or []:
        try:
            age = (today - date.fromisoformat(f["date"])).days
        except Exception:
            continue
        if age <= 10 and "2.02" in f.get("items", []):
            out.append({"kind": "news", "impact": 0.2, "source": L("SEC (8-K, document officiel)", "SEC (8-K, official document)"), "date": f["date"], "url": f["url"],
                        "text": L(f"Résultats publiés le {fr(f['date'])} : le communiqué officiel est disponible (point 2.02 du 8-K).",
                                  f"Results published on {fr(f['date'])}: the official press release is available (Item 2.02 of the 8-K).")})
            break
    return out


def analyze(ticker, closes, news, events, inv_signals, now=None, currency="USD"):
    now = now or datetime.now(timezone.utc)
    today = now.date()
    signals = (technical_signals(closes, currency) + analyst_signals(events, today, currency) + calendar_signals(events, today)
               + news_signals(news, now) + inv_signals)
    signals.sort(key=lambda s: -abs(s["impact"]))
    # Les investisseurs sont nombreux : leur poids cumulé est plafonné pour ne pas écraser le reste.
    inv = sum(s["impact"] for s in signals if s["kind"] == "investisseurs")
    other = sum(s["impact"] for s in signals if s["kind"] != "investisseurs")
    total = round(other + max(-4.0, min(4.0, inv)), 1)
    lvl = level_for(total) if signals else None
    return {"signals": signals, "total": total, "level": lvl, "level_label": level_label(lvl) if lvl else None}


def upcoming(companies, events_by_ticker, today=None):
    today = today or date.today()
    out = []
    for c in companies:
        for d in (events_by_ticker.get(c["ticker"]) or {}).get("earnings_dates", []):
            try:
                n = (date.fromisoformat(d) - today).days
            except Exception:
                continue
            if 0 <= n <= 60:
                out.append({"ticker": c["ticker"], "name": c["name"], "date": d, "days": n, "what": L("Résultats trimestriels", "Quarterly earnings")})
                break
    return sorted(out, key=lambda x: x["days"])
