"""Analyse d'une entreprise : sections notées (technique, valorisation, croissance, rentabilité, solidité, analystes,
actualités, grands investisseurs), verdict global et « timing ». Tout repose sur des chiffres affichés :
les seuils sont génériques (à comparer aux entreprises du même secteur) et ne constituent pas un conseil."""
from analysis.lang import L, money, money_big, pct
from analysis.indicators import rsi, sma_series
from analysis.opportunities import technical_signals
from analysis.scoring import compute_score

WEIGHTS = {"technique": 20, "valorisation": 15, "croissance": 20, "rentabilite": 15, "solidite": 10,
           "analystes": 10, "actualites": 5, "investisseurs": 5}


def labels():
    return {"technique": L("Technique (cours)", "Technical (price)"), "valorisation": L("Valorisation", "Valuation"),
            "croissance": L("Croissance", "Growth"), "rentabilite": L("Rentabilité", "Profitability"),
            "solidite": L("Solidité financière", "Financial strength"), "analystes": L("Analystes", "Analysts"),
            "actualites": L("Actualités", "News"), "investisseurs": L("Grands investisseurs", "Major investors")}


def verdicts():
    return [(72, "good", L("Situation intéressante", "Attractive situation"), "good"),
            (58, "fairly_good", L("Plutôt intéressante", "Fairly attractive"), "info"),
            (45, "neutral", L("Neutre : à surveiller", "Neutral: keep watching"), "neutral"),
            (30, "poor", L("Peu attractive", "Unattractive"), "warn"),
            (0, "avoid", L("À éviter pour l'instant", "Avoid for now"), "crit")]


def _scale(x, lo, hi):
    return max(0.0, min(100.0, (x - lo) / (hi - lo) * 100))


def _tone(score):
    return "pos" if score >= 65 else "neg" if score <= 40 else "neutral"


def _pct(v, signed=True):
    return pct(v * 100, 0, signed)


def _section(key, parts, reasons):
    """parts : notes 0-100 ; reasons : [(texte, note ou None)]"""
    score = round(sum(parts) / len(parts), 1) if parts else None
    return {"key": key, "label": labels()[key], "weight": WEIGHTS[key], "score": score,
            "reasons": [{"text": r[0], "tone": _tone(r[1]) if r[1] is not None else "neutral", **({"key": r[2]} if len(r) > 2 else {})}
                        for r in reasons]}


def technique(closes, currency="USD"):
    if len(closes) < 60:
        return _section("technique", [], [])
    res = compute_score(closes)
    reasons = [(s["text"], 50 + s["impact"] * 25) for s in technical_signals(closes, currency) if s.get("key") != "score"]
    hi, lo = max(closes[-252:]), min(closes[-252:])
    reasons.append((L(f"Cours à {_pct(closes[-1] / hi - 1)} de son plus haut sur 52 semaines et à {_pct(closes[-1] / lo - 1)} de son plus bas.",
                      f"Price is {_pct(closes[-1] / hi - 1)} from its 52-week high and {_pct(closes[-1] / lo - 1)} from its low."), None))
    reasons.insert(0, (L(f"Score technique {int(res['score'] + 0.5)}/100 ({res['label']}).", f"Technical score {int(res['score'] + 0.5)}/100 ({res['label']})."), res["score"], "score"))
    return _section("technique", [res["score"]], reasons)


def valorisation(info, price):
    fpe, tpe, peg, ps = info.get("forwardPE"), info.get("trailingPE"), info.get("trailingPegRatio"), info.get("priceToSalesTrailing12Months")
    parts, reasons = [], []
    pe = fpe if fpe and fpe > 0 else tpe if tpe and tpe > 0 else None
    if pe:
        s = 88 if pe <= 12 else 75 if pe <= 18 else 62 if pe <= 25 else 45 if pe <= 35 else 30 if pe <= 50 else 15
        parts.append(s)
        fwd = bool(fpe and fpe > 0)
        reasons.append((L(f"P/E {'prévisionnel' if fwd else 'sur 12 mois'} de {pe:.1f} (repère : moins de 18 = raisonnable, plus de 35 = exigeant)",
                          f"{'Forward' if fwd else 'Trailing 12-month'} P/E of {pe:.1f} (benchmark: under 18 = reasonable, over 35 = demanding)"), s))
    elif (info.get("trailingEps") is not None and info["trailingEps"] <= 0) or (tpe is not None and tpe <= 0):
        parts.append(20)
        reasons.append((L("Pas de bénéfices sur 12 mois : la valorisation repose sur la croissance future",
                          "No earnings over the last 12 months: the valuation relies on future growth"), 20))
    if peg and peg > 0:
        s = 80 if peg < 1 else 60 if peg < 2 else 30
        parts.append(s)
        reasons.append((L(f"PEG de {peg:.1f} (P/E rapporté à la croissance des bénéfices ; moins de 1 = croissance bon marché)",
                          f"PEG of {peg:.1f} (P/E relative to earnings growth; under 1 = growth is cheap)"), s))
    if ps:
        s = 70 if ps < 4 else 50 if ps < 10 else 25
        parts.append(s)
        reasons.append((L(f"Valeur boursière = {ps:.1f} fois le chiffre d'affaires annuel", f"Market value = {ps:.1f} times annual revenue"), s))
    if info.get("priceToBook"):
        reasons.append((L(f"Cours = {info['priceToBook']:.1f} fois la valeur comptable", f"Price = {info['priceToBook']:.1f} times book value"), None))
    return _section("valorisation", parts, reasons)


def croissance(info):
    parts, reasons = [], []
    rg, eg = info.get("revenueGrowth"), info.get("earningsGrowth")
    if rg is not None:
        s = _scale(rg, -0.05, 0.30)
        parts.append(s)
        reasons.append((L(f"Chiffre d'affaires {_pct(rg)} sur un an", f"Revenue {_pct(rg)} year over year"), s))
    if eg is not None:
        s = _scale(eg, -0.20, 0.40)
        parts.append(s)
        reasons.append((L(f"Bénéfices {_pct(eg)} sur un an", f"Earnings {_pct(eg)} year over year"), s))
    return _section("croissance", parts, reasons)


def rentabilite(info):
    parts, reasons = [], []
    for key, lab, hi in (("profitMargins", L("Marge nette", "Net margin"), 0.25), ("operatingMargins", L("Marge opérationnelle", "Operating margin"), 0.30),
                         ("returnOnEquity", L("Rentabilité des capitaux propres (ROE)", "Return on equity (ROE)"), 0.25)):
        v = info.get(key)
        if v is not None:
            s = _scale(v, 0, hi)
            parts.append(s)
            reasons.append((L(f"{lab} de {_pct(v, False)}", f"{lab} of {_pct(v, False)}"), s))
    return _section("rentabilite", parts, reasons)


def solidite(info):
    parts, reasons = [], []
    financial = (info.get("sector") or "") == "Financial Services"
    de = info.get("debtToEquity")
    if de is not None and not financial:
        s = 90 if de < 30 else 72 if de < 80 else 48 if de < 150 else 20
        parts.append(s)
        reasons.append((L(f"Dette / capitaux propres de {de:.0f} %", f"Debt / equity of {de:.0f}%"), s))
    cr = info.get("currentRatio")
    if cr is not None and not financial:
        s = 80 if cr >= 1.5 else 60 if cr >= 1 else 30
        parts.append(s)
        reasons.append((L(f"Ratio de liquidité courante de {cr:.1f} (moins de 1 = tension possible à court terme)",
                          f"Current ratio of {cr:.1f} (under 1 = possible short-term strain)"), s))
    fcf = info.get("freeCashflow")
    if fcf is not None:
        s = 70 if fcf > 0 else 30
        parts.append(s)
        cur = info.get("financialCurrency") or info.get("currency") or "USD"
        amt = money_big(abs(fcf), cur)
        reasons.append((L(f"Flux de trésorerie disponible {'positif' if fcf > 0 else 'négatif'} ({amt})",
                          f"{'Positive' if fcf > 0 else 'Negative'} free cash flow ({amt})"), s))
    if financial:
        reasons.append((L("Ratios d'endettement non pertinents pour une banque ou un assureur : ignorés",
                          "Leverage ratios are not relevant for a bank or an insurer: ignored"), None))
    return _section("solidite", parts, reasons)


def analystes(info, price, events):
    parts, reasons = [], []
    mean = info.get("targetMeanPrice") or ((events or {}).get("target") or {}).get("mean")
    if mean and price:
        up = mean / price - 1
        s = _scale(up, -0.10, 0.30)
        parts.append(s)
        n = info.get("numberOfAnalystOpinions")
        cur = info.get("currency") or "USD"
        reasons.append((L(f"Objectif de cours moyen {money(mean, cur)} ({_pct(up)} vs le cours)" + (f", {int(n)} analystes" if n else ""),
                          f"Average price target {money(mean, cur)} ({_pct(up)} vs. the current price)" + (f", {int(n)} analysts" if n else "")), s))
    rm = info.get("recommendationMean")
    if rm:
        s = _scale(5 - rm, 0, 3)
        parts.append(s)
        reasons.append((L(f"Recommandation moyenne {rm:.1f}/5 (1 = achat fort, 5 = vente forte)",
                          f"Average recommendation {rm:.1f}/5 (1 = strong buy, 5 = strong sell)"), s))
    return _section("analystes", parts, reasons)


def actualites(news):
    pos = sum(1 for n in news or [] if n.get("sentiment", 0) > 0)
    neg = sum(1 for n in news or [] if n.get("sentiment", 0) < 0)
    if not (pos or neg):
        return _section("actualites", [], [])
    s = 50 + 40 * (pos - neg) / (pos + neg)
    return _section("actualites", [s], [(L(f"{pos} titre(s) plutôt positif(s) et {neg} plutôt négatif(s) parmi les articles récents (détection par mots-clés)",
                                                  f"{pos} mostly positive and {neg} mostly negative headline(s) among recent articles (keyword detection)"), s)])


def investisseurs(signals):
    if not signals:
        return _section("investisseurs", [], [])
    net = sum(s["impact"] for s in signals if s["impact"])
    s = max(15.0, min(90.0, 50 + 12 * net))
    return _section("investisseurs", [s], [(x["text"], 50 + x["impact"] * 20) for x in signals[:6]])


def timing(closes):
    """`code` stable (unknown / unfavorable / favorable / neutral), `label` dans la langue courante."""
    T = {"unknown": L("inconnu", "unknown"), "unfavorable": L("défavorable", "unfavorable"), "favorable": L("favorable", "favorable"),
         "neutral": L("neutre", "neutral")}
    if len(closes) < 60:
        return {"code": "unknown", "label": T["unknown"], "tone": "neutral", "text": L("Historique de cours insuffisant.", "Not enough price history.")}
    price, r = closes[-1], rsi(closes)
    s50 = sma_series(closes, 50)[-1]
    s200 = sma_series(closes, 200)[-1] if len(closes) >= 200 else None
    dd = price / max(closes[-63:]) - 1
    if (r and r > 72) or price > s50 * 1.15:
        return {"code": "unfavorable", "label": T["unfavorable"], "tone": "warn",
                "text": L(f"Cours très étendu (RSI {r:.0f}, {_pct(price / s50 - 1)} au-dessus de sa MM50) : un repli à court terme est plus probable.",
                          f"Price very extended (RSI {r:.0f}, {_pct(price / s50 - 1)} above its 50-day MA): a short-term pullback is more likely.")}
    if s200 and price < s200 and s50 < s200:
        return {"code": "unfavorable", "label": T["unfavorable"], "tone": "warn",
                "text": L("Tendance baissière : cours et MM50 sous la MM200. Attendre un retournement confirmé.",
                          "Downtrend: price and 50-day MA below the 200-day MA. Wait for a confirmed reversal.")}
    uptrend = price > s50 * 0.97 and (s200 is None or s50 > s200)
    if uptrend and (-0.15 <= dd <= -0.04 or (r and r < 40)):
        return {"code": "favorable", "label": T["favorable"], "tone": "good",
                "text": L(f"Repli ({_pct(dd)} depuis le plus haut à 3 mois" + (f", RSI {r:.0f}" if r else "") + ") dans une tendance de fond haussière.",
                          f"Pullback ({_pct(dd)} from the 3-month high" + (f", RSI {r:.0f}" if r else "") + ") within a long-term uptrend.")}
    return {"code": "neutral", "label": T["neutral"], "tone": "neutral",
            "text": L("Ni repli marqué ni excès : pas de signal de timing net.", "Neither a marked pullback nor an excess: no clear timing signal.")}


def assess(info, closes, events, news, investor_signals):
    price = closes[-1] if closes else info.get("currentPrice")
    cur = info.get("currency") or "USD"
    sections = [technique(closes, cur), valorisation(info, price), croissance(info), rentabilite(info), solidite(info),
                analystes(info, price, events), actualites(news), investisseurs(investor_signals)]
    have = [s for s in sections if s["score"] is not None]
    wsum = sum(s["weight"] for s in have)
    total = round(sum(s["score"] * s["weight"] for s in have) / wsum, 1) if wsum else None
    code, label, tone = next(((c, lab, t) for thr, c, lab, t in verdicts() if total is not None and total >= thr),
                             ("insufficient", L("Données insuffisantes", "Insufficient data"), "neutral"))
    if total is None:
        code, label, tone = "insufficient", L("Données insuffisantes", "Insufficient data"), "neutral"
    fundamentals_ok = sum(1 for s in have if s["key"] in ("valorisation", "croissance", "rentabilite", "solidite")) >= 2
    if total is not None and not fundamentals_ok:
        code, label, tone = "partial", L("Analyse partielle", "Partial analysis"), "neutral"  # sans fondamentaux, le cours seul ne permet pas de conclure

    def avg(keys):
        v = [s for s in sections if s["key"] in keys and s["score"] is not None]
        w = sum(s["weight"] for s in v)
        return round(sum(s["score"] * s["weight"] for s in v) / w, 1) if w else None

    quality, valuation = avg({"croissance", "rentabilite", "solidite"}), avg({"valorisation"})
    tm = timing(closes)
    strengths = [{"section": s["label"], "text": r["text"]} for s in have if (s["score"] or 0) >= 60 for r in s["reasons"] if r["tone"] == "pos" and r.get("key") != "score"][:4]
    weaknesses = [{"section": s["label"], "text": r["text"]} for s in have for r in s["reasons"] if r["tone"] == "neg"][:4]
    bits = []
    if quality is not None:
        bits.append(L("entreprise " + ("solide" if quality >= 65 else "moyenne" if quality >= 45 else "fragile") + f" (qualité {quality:.0f}/100)",
                      ("solid" if quality >= 65 else "average" if quality >= 45 else "fragile") + f" company (quality {quality:.0f}/100)"))
    if valuation is not None:
        bits.append(L("valorisation " + ("attractive" if valuation >= 65 else "correcte" if valuation >= 45 else "élevée"),
                      ("attractive" if valuation >= 65 else "fair" if valuation >= 45 else "high") + " valuation"))
    bits.append(L(f"moment d'entrée {tm['label']}", f"entry timing: {tm['label']}"))
    summary = f"{label}{L(' : ', ': ')}" + ", ".join(bits) + "."
    if total is not None and not fundamentals_ok:
        summary = L("Analyse partielle : les données financières (valorisation, croissance, rentabilité, solidité) sont indisponibles "
                    "(source bloquée, ETF ou entreprise non couverte). Seuls le cours, les actualités et les investisseurs sont évalués ; "
                    "ne pas en conclure qu'investir est intéressant.",
                    "Partial analysis: financial data (valuation, growth, profitability, strength) is unavailable "
                    "(blocked source, ETF or uncovered company). Only the price, news and investors are assessed; "
                    "do not conclude that investing is attractive.")
    return {"verdict": {"code": code, "label": label, "tone": tone, "score": total, "summary": summary},
            "quality": quality, "valuation": valuation, "timing": tm, "sections": sections,
            "partial": total is not None and not fundamentals_ok, "strengths": strengths, "weaknesses": weaknesses,
            "missing": [s["label"] for s in sections if s["score"] is None]}
