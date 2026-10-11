"""Lecture des 13F : positions clés, mouvements du trimestre, explications et lien avec la watchlist."""
from datetime import date

from analysis.lang import L, lang, money_big, pct
from analysis.names import same_company

SEC_FILING = "https://www.sec.gov/Archives/edgar/data/{cik_int}/{acc}/"


def fmt_usd(x):
    """Montant abrégé, converti dans la devise choisie (les valeurs 13F sont en USD)."""
    return money_big(abs(x), "USD")


def quarter_label(period):
    y, m, _ = period.split("-")
    return L(f"T{(int(m) - 1) // 3 + 1} {y}", f"Q{(int(m) - 1) // 3 + 1} {y}")


def match_ticker(issuer, watchlist):
    """Ticker de la première entreprise de la liste dont le nom correspond à l'émetteur 13F."""
    up = (issuer or "").upper()
    for c in watchlist:
        if any(m.upper() in up for m in c.get("match", [])) or same_company(issuer, c.get("name", "")):
            return c["ticker"]
    return None


def _price_since(series, period):
    """Évolution du cours depuis la fin du trimestre (series = [(date, close)])."""
    if not series:
        return None
    end = date.fromisoformat(period)
    base = next((c for d, c in series if d >= end), None)
    return round((series[-1][1] / base - 1) * 100, 1) if base else None


def explain(ch, weight, inv, price_pct=None):
    """Texte 'pourquoi' : ce que le mouvement signifie, sans inventer de motif propre au fonds."""
    k, pct_, flow = ch["kind"], ch["pct"], abs(ch["flow"])
    if k == "new":
        s = L(f"Nouvelle position d'environ {fmt_usd(flow)} ({pct(weight, 1)} du portefeuille) : "
              "elle n'existait pas le trimestre précédent, ce qui traduit un vrai choix d'investissement.",
              f"New position of about {fmt_usd(flow)} ({pct(weight, 1)} of the portfolio): "
              "it did not exist in the previous quarter, which reflects a genuine investment decision.")
    elif k == "exit":
        s = L(f"Position entièrement vendue (≈ {fmt_usd(flow)} au trimestre précédent) : perte de conviction, "
              "prise de bénéfices ou rééquilibrage.",
              f"Position fully sold (≈ {fmt_usd(flow)} in the previous quarter): loss of conviction, "
              "profit-taking or rebalancing.")
    elif k == "add":
        s = L(f"Renforcement de {pct(pct_, 0, True)} du nombre d'actions (≈ {fmt_usd(flow)} achetés) : "
              "le fonds en veut davantage, signe de conviction croissante.",
              f"Position increased by {pct(pct_, 0, True)} in share count (≈ {fmt_usd(flow)} bought): "
              "the fund wants more, a sign of growing conviction.")
    else:
        s = L(f"Allègement de {pct(abs(pct_), 0)} du nombre d'actions (≈ {fmt_usd(flow)} vendus) : "
              "prise de bénéfices, réduction du risque ou changement de thèse.",
              f"Position trimmed by {pct(abs(pct_), 0)} in share count (≈ {fmt_usd(flow)} sold): "
              "profit-taking, risk reduction or a change of thesis.")
    if ch["pc"]:
        opt = L("achat", "call") if ch["pc"] == "CALL" else L("vente", "put")
        s += L(f" Attention : ligne d'options ({opt}), pas des actions.", f" Note: options line ({opt}), not shares.")
    if price_pct is not None:
        s += L(f" Depuis la fin du trimestre, le cours a évolué de {pct(price_pct, 1, True)}.",
               f" Since the end of the quarter, the price has moved {pct(price_pct, 1, True)}.")
    if inv.get("quant"):
        s += L(" Fonds quantitatif : mouvement issu de modèles statistiques, pas d'une thèse fondamentale.",
               " Quantitative fund: the move comes from statistical models, not a fundamental thesis.")
    return s


IMPACT = {"new": 2.0, "add": 1.5, "reduce": -1.0, "exit": -1.5}


def build_view(inv, data, watchlist, closes):
    """Vue affichable d'un investisseur. data = résultat de refresh_investor (ou None)."""
    view = {"slug": inv["slug"], "name": inv["name"], "style": (inv.get("style_en") or inv["style"]) if lang() == "en" else inv["style"], "quant": bool(inv.get("quant")),
            "available": data is not None}
    if not data:
        return view
    total = data["total_value"] or 1
    cik_int = int(inv["cik"])
    url = SEC_FILING.format(cik_int=cik_int, acc=data["accession"].replace("-", ""))

    def with_ticker(x):
        return {**x, "ticker": match_ticker(x["issuer"], watchlist)}

    changes = []
    for ch in data["changes"]:
        t = match_ticker(ch["issuer"], watchlist)
        weight = 0.0
        held = next((h for h in data["holdings"] if h["cusip"] == ch["cusip"] and h["pc"] == ch["pc"]), None)
        if held:
            weight = held["value"] / total * 100
        price_pct = _price_since(closes.get(t, []), data["period"]) if t else None
        changes.append({**ch, "ticker": t, "weight": round(weight, 2),
                        "why": explain(ch, weight, inv, price_pct)})

    hits = []
    for ch in changes:
        if ch["ticker"]:
            hits.append({"ticker": ch["ticker"], "kind": ch["kind"], "pct": ch["pct"], "flow": ch["flow"],
                         "weight": ch["weight"], "why": ch["why"]})
    changed = {h["ticker"] for h in hits}
    for h in data["holdings"]:
        t = match_ticker(h["issuer"], watchlist)
        if t and t not in changed and not h["pc"] and h["value"] / total >= 0.005:
            hits.append({"ticker": t, "kind": "hold", "pct": 0.0, "flow": 0.0,
                         "weight": round(h["value"] / total * 100, 2),
                         "why": L(f"Position conservée ({fmt_usd(h['value'])}, {pct(h['value'] / total * 100, 1)} du portefeuille).",
                                  f"Position held ({fmt_usd(h['value'])}, {pct(h['value'] / total * 100, 1)} of the portfolio).")})

    view.update({
        "period": data["period"], "period_label": quarter_label(data["period"]), "filed": data["filed"],
        "url": url, "total_value": data["total_value"], "n_positions": data["n_positions"],
        "holdings": [with_ticker({**h, "weight": round(h["value"] / total * 100, 2)}) for h in data["holdings"][:10]],
        "changes": changes[:10], "hits": hits, "news": data.get("news", []),
    })
    return view


def signals_for(ticker, views):
    """Signaux 'investisseurs' pour un ticker, utilisés par le moteur d'opportunités."""
    out = []
    for v in views:
        if not v.get("available"):
            continue
        for h in v["hits"]:
            if h["ticker"] != ticker:
                continue
            k = h["kind"]
            base = IMPACT.get(k, 0.5)
            impact = base * (0.5 if v["quant"] else 1)
            pct_ = h["pct"] or 0
            label = {"new": lambda: L("a initié une position", "opened a position"),
                     "add": lambda: L(f"a renforcé sa position ({pct(pct_, 0, True)} d'actions)", f"increased its position ({pct(pct_, 0, True)} in shares)"),
                     "reduce": lambda: L(f"a allégé sa position ({pct(pct_, 0, True)} d'actions)", f"trimmed its position ({pct(pct_, 0, True)} in shares)"),
                     "exit": lambda: L("a vendu toute sa position", "sold its entire position"),
                     "hold": lambda: L(f"détient {pct(h['weight'], 1)} de son portefeuille", f"holds {pct(h['weight'], 1)} of its portfolio")}[k]()
            out.append({"kind": "investisseurs", "impact": impact,
                        "text": L(f"{v['name'].split(' (')[0]} {label} au {v['period_label']} (13F).",
                                 f"{v['name'].split(' (')[0]} {label} in {v['period_label']} (13F filing)."),
                        "url": v["url"], "source": "SEC EDGAR", "date": v["filed"]})
    return out
