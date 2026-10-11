"""Score d'importance (0-100), palier et catégorie d'affichage pour chaque alerte (Module E du cahier des
charges), indépendamment de ce qui l'a déclenchée.

Règles explicites et déterministes, jamais un modèle opaque : chaque score vient avec sa raison en texte,
affichable telle quelle — c'est elle qui justifie le classement, pas un chiffre sorti de nulle part.

Pour les mentions de dirigeants et les actualités d'entreprise (kind="executive"/"company_news"), le score
n'est plus fixe : voir alerts/relevance.py, qui regarde le contenu réel du titre plutôt que le simple fait
qu'un nom ait été trouvé.
"""

CRITIQUE, IMPORTANT, A_SURVEILLER, FAIBLE = "Critique", "Important", "À surveiller", "Faible pertinence"
SEUIL_CRITIQUE, SEUIL_IMPORTANT, SEUIL_SURVEILLER = 80, 60, 40

# Catégories d'affichage (Module 11) : décident l'emoji et la couleur de l'embed Discord, indépendamment
# du palier d'importance (un palier "Critique" négatif et un palier "Critique" positif n'ont pas la même
# couleur — voir alerts/notify.py::EMBED_STYLE).
BREAKING, CATALYST_POS, CATALYST_NEG, MARKET_ALERT, UPCOMING, INNOVATION, RISK, INFO = (
    "breaking", "catalyst_positive", "catalyst_negative", "market_alert", "upcoming_event", "innovation", "risk", "info")

# Catégories de relevance.py (contenu "stratégique majeur" ou "risque") qui doivent primer sur le simple
# signe du sentiment pour le choix de la couleur/catégorie d'affichage (Module 2 : violet/orange dédiés).
from alerts.relevance import CAPEX, MERGER, PRODUCT, REGULATORY  # noqa: E402


def tier_for(score):
    if score >= SEUIL_CRITIQUE:
        return CRITIQUE
    if score >= SEUIL_IMPORTANT:
        return IMPORTANT
    if score >= SEUIL_SURVEILLER:
        return A_SURVEILLER
    return FAIBLE


def score_for(kind, **ctx):
    """(score, raison) pour un type d'alerte (voir les valeurs de « kind » passées à alerts.notify.notify).
    ctx porte les détails utiles selon le type : pct/threshold pour un mouvement de prix, label pour un
    changement de score, title/ticker/company_name/source pour une actualité. Un type inconnu reçoit un
    score neutre plutôt qu'une erreur — l'app ne doit jamais bloquer une alerte réelle pour une question de
    classement."""
    if kind == "earnings_filing":
        return 90, "Résultats trimestriels officiels (SEC, item 2.02) : impact direct et à la source."
    if kind and kind.startswith("official_feed"):
        return 85, "Annonce officielle d'une banque centrale (Fed/BCE) : effet potentiel sur l'ensemble du marché."
    if kind in ("intraday_move", "daily_move"):
        pct, threshold = abs(ctx.get("pct") or 0), ctx.get("threshold") or 1
        if pct >= 2 * threshold:
            return 80, f"Variation de {pct:.1f} % : plus du double du seuil réglé ({threshold:.1f} %)."
        return 65, f"Variation de {pct:.1f} % au-delà du seuil réglé ({threshold:.1f} %)."
    if kind == "score_change":
        label = ctx.get("label") or ""
        if label in ("BUY", "AVOID"):
            return 65, f"Le score technique atteint le niveau {label} — changement de catégorie, pas une simple fluctuation."
        return 40, f"Le score technique passe à {label or '?'}."
    if kind in ("executive", "company_news"):
        from alerts.relevance import score_news
        score, _cat, reason = score_news(ctx.get("title"), ticker=ctx.get("ticker"),
                                         company_name=ctx.get("company_name"), source=ctx.get("source"))
        return score, reason
    return 35, "Type d'alerte sans règle d'importance dédiée."


def classify(kind, ctx):
    """(catégorie, bias) — bias ∈ {-1, 0, 1}, utilisé par alerts/notify.py pour choisir l'emoji et la
    couleur de l'embed Discord (Module 2/11), indépendamment du score/palier d'importance."""
    if kind == "earnings_filing":
        return UPCOMING, 0
    if kind and kind.startswith("official_feed"):
        return BREAKING, 0
    if kind in ("intraday_move", "daily_move"):
        pct = ctx.get("pct") or 0
        return MARKET_ALERT, (1 if pct > 0 else -1)
    if kind == "score_change":
        label = ctx.get("label") or ""
        if label == "BUY":
            return CATALYST_POS, 1
        if label == "AVOID":
            return CATALYST_NEG, -1
        return INFO, 0
    if kind in ("executive", "company_news"):
        from alerts.relevance import categorize
        from analysis.sentiment import headline_sentiment
        title = ctx.get("title")
        cat, _base, _hits = categorize(title)
        bias, _words = headline_sentiment(title)
        if cat in (MERGER, PRODUCT, CAPEX) and bias >= 0:
            return INNOVATION, bias
        if cat == REGULATORY:
            return RISK, bias
        if bias > 0:
            return CATALYST_POS, bias
        if bias < 0:
            return CATALYST_NEG, bias
        return INFO, 0
    return INFO, 0
