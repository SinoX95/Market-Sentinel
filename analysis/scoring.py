"""Score technique 0-100 calculé à partir de l'historique des cours de clôture.

Seuils : BUY >= 80, HOLD 60-79, WATCH 40-59, AVOID/REASSESS < 40.
Pour l'instant le score ne repose que sur les prix (momentum, tendance, risque).
Les news et la threat intel viendront s'ajouter comme composantes supplémentaires.
"""
from math import sqrt

from analysis.lang import L, pct

MIN_POINTS = 60
# Le score est toujours calculé sur la même fenêtre (1 an de bourse), quelle que soit la longueur de l'historique
# fourni : l'écart au plus haut et la volatilité dépendent de la période regardée. Sans ça, la même action recevait
# un score différent selon l'endroit (classement « Marché » : 1 an téléchargé ; fiche : tout l'historique en base,
# plusieurs années) et pouvait passer de BUY à HOLD en ouvrant sa fiche.
SCORE_WINDOW = 252
WEIGHTS = {"momentum": 0.30, "trend": 0.20, "long_trend": 0.15, "risk": 0.15, "drawdown": 0.20}


def label_for(score: float) -> str:
    if score >= 80:
        return "BUY"
    if score >= 60:
        return "HOLD"
    if score >= 40:
        return "WATCH"
    return "AVOID/REASSESS"


def _scale(x, lo, hi):
    """Projette x de [lo, hi] vers [0, 100] (borné)."""
    return max(0.0, min(100.0, (x - lo) / (hi - lo) * 100))


def _sma(values, n):
    return sum(values[-n:]) / n


def compute_score(closes):
    """closes : liste de cours triée du plus ancien au plus récent.
    Retourne {"score", "label", "components"} ou None si l'historique est trop court."""
    closes = list(closes)[-SCORE_WINDOW:]
    if len(closes) < MIN_POINTS:
        return None
    price = closes[-1]

    momentum_ret = price / closes[-63] - 1 if len(closes) >= 63 else price / closes[0] - 1
    trend = price / _sma(closes, 50) - 1
    long_trend = _sma(closes, 50) / _sma(closes, 200) - 1 if len(closes) >= 200 else None

    rets = [closes[i] / closes[i - 1] - 1 for i in range(1, len(closes))]
    mean = sum(rets) / len(rets)
    vol = sqrt(sum((r - mean) ** 2 for r in rets) / len(rets)) * sqrt(252)
    drawdown = price / max(closes) - 1

    comp = {
        "momentum": _scale(momentum_ret, -0.20, 0.40),
        "trend": _scale(trend, -0.10, 0.15),
        "long_trend": _scale(long_trend, -0.10, 0.20) if long_trend is not None else 50.0,
        "risk": _scale(-vol, -0.70, -0.20),
        "drawdown": _scale(drawdown, -0.35, 0.0),
    }
    score = round(sum(comp[k] * w for k, w in WEIGHTS.items()), 1)
    return {"score": score, "label": label_for(score), "components": {k: round(v, 1) for k, v in comp.items()}}


def phrases():
    """composante -> (phrase si favorable, phrase si défavorable), dans la langue courante."""
    return {
        "momentum": (L("hausse récente marquée", "strong recent gain"), L("momentum faible ou négatif", "weak or negative momentum")),
        "trend": (L("cours au-dessus de sa moyenne 50 jours", "price above its 50-day moving average"),
                  L("cours sous sa moyenne 50 jours", "price below its 50-day moving average")),
        "long_trend": (L("tendance de fond haussière", "long-term uptrend"), L("tendance de fond baissière", "long-term downtrend")),
        "risk": (L("faible volatilité", "low volatility"), L("volatilité élevée", "high volatility")),
        "drawdown": (L("proche de ses plus hauts", "close to its highs"), L("loin de ses plus hauts", "far from its highs")),
    }


def names():
    return {"momentum": L("le momentum sur 3 mois", "3-month momentum"),
            "trend": L("la position par rapport à la moyenne 50 jours", "the position versus the 50-day moving average"),
            "long_trend": L("la tendance de fond", "the long-term trend"),
            "risk": L("la volatilité", "volatility"), "drawdown": L("l'écart avec le plus haut", "the distance from the high")}


NEXT_LEVEL = {"BUY": None, "HOLD": ("BUY", 80), "WATCH": ("HOLD", 60), "AVOID/REASSESS": ("WATCH", 40)}


def meaning(label):
    return {"BUY": L("signal d'achat", "buy signal"), "HOLD": L("conserver / attendre", "hold / wait"),
            "WATCH": L("à surveiller", "watch"), "AVOID/REASSESS": L("à éviter pour l'instant", "avoid for now")}[label]


def explain(closes):
    """Pourquoi ce niveau (BUY, HOLD…) ? Phrase de synthèse, points chiffrés et écart au niveau suivant."""
    closes = list(closes)[-SCORE_WINDOW:]  # même fenêtre que compute_score : les chiffres expliqués sont ceux du score
    res = compute_score(closes)
    if not res:
        return None
    price = closes[-1]
    r3 = price / closes[-63] - 1 if len(closes) >= 63 else price / closes[0] - 1
    s50 = _sma(closes, 50)
    rets = [closes[i] / closes[i - 1] - 1 for i in range(1, len(closes))]
    mean = sum(rets) / len(rets)
    vol = sqrt(sum((r - mean) ** 2 for r in rets) / len(rets)) * sqrt(252)
    dd = price / max(closes) - 1
    above = L("au-dessus", "above") if price >= s50 else L("en dessous", "below")
    texts = {
        "momentum": L(f"{pct(r3 * 100, 0, True)} sur 3 mois", f"{pct(r3 * 100, 0, True)} over 3 months"),
        "trend": L(f"Cours {pct(abs(price / s50 - 1) * 100)} {above} de sa moyenne 50 jours",
                   f"Price {pct(abs(price / s50 - 1) * 100)} {above} its 50-day moving average"),
        "risk": L(f"Volatilité annualisée de {pct(vol * 100)}", f"Annualized volatility of {pct(vol * 100)}"),
        "drawdown": (L("Au niveau de son plus haut de la période", "At its high for the period") if dd > -0.005
                     else L(f"{pct(abs(dd) * 100)} sous son plus haut de la période", f"{pct(abs(dd) * 100)} below its high for the period")),
    }
    if len(closes) >= 200:
        lt = _sma(closes, 50) / _sma(closes, 200) - 1
        texts["long_trend"] = L(
            f"Moyenne 50 jours {'au-dessus' if lt >= 0 else 'en dessous'} de la moyenne 200 jours ({pct(lt * 100, 0, True)})",
            f"50-day average {'above' if lt >= 0 else 'below'} the 200-day average ({pct(lt * 100, 0, True)})")
    comp = {k: v for k, v in res["components"].items() if k in texts}
    order = [k for k, _ in sorted(comp.items(), key=lambda kv: -abs(kv[1] - 50))][:3]
    weakest = min(comp, key=comp.get)
    if weakest not in order:
        order.append(weakest)  # le principal frein est toujours montré
    points = [{"tone": "pos" if comp[k] >= 65 else "neg" if comp[k] <= 40 else "neutral", "text": texts[k], "score": comp[k]} for k in order]
    ph = phrases()
    strong = [ph[k][0] for k, v in sorted(comp.items(), key=lambda kv: -kv[1]) if v >= 65][:2]
    weak = [ph[k][1] for k, v in sorted(comp.items(), key=lambda kv: kv[1]) if v <= 40][:2]
    sep = L(" et ", " and ")
    parts = []
    if strong:
        parts.append(sep.join(strong))
    if weak:
        parts.append((L("mais ", "but ") if strong else "") + sep.join(weak))
    label, score = res["label"], res["score"]
    colon = L(" : ", ": ")
    headline = (f"{meaning(label).capitalize()} ({int(score + 0.5)}/100)"
                + (colon + ", ".join(parts) if parts else colon + L("signaux techniques mitigés", "mixed technical signals")) + ".")
    nxt = NEXT_LEVEL[label]
    if nxt:
        missing = max(1, round(nxt[1] - score))
        why = (L(f"frein principal : {weak[0]}", f"main drag: {weak[0]}") if weak
               else L(f"point le plus faible : {names()[min(comp, key=comp.get)]}", f"weakest point: {names()[min(comp, key=comp.get)]}"))
        gap = L(f"Il manque {missing} point{'s' if missing > 1 else ''} pour atteindre {nxt[0]} ({why}).",
                f"{missing} more point{'s' if missing > 1 else ''} needed to reach {nxt[0]} ({why}).")
    else:
        gap = L("Au-dessus du seuil BUY (80) ; surveiller un éventuel excès (surachat) avant d'acheter.",
                "Above the BUY threshold (80); watch for a possible excess (overbought) before buying.")
    return {"label": label, "score": score, "headline": headline, "points": points, "gap": gap}
