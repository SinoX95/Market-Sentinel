"""Pertinence boursière d'une actualité (mention de dirigeant ou actualité d'entreprise), par mots-clés —
remplace le score fixe de 30/100 qu'une simple mention de nom recevait auparavant (voir alerts/importance.py
pour les autres types d'alerte, dont le score variait déjà correctement).

Règles explicites et déterministes, jamais un modèle opaque : chaque score vient avec sa raison en texte
(mots-clés trouvés, catégorie détectée, pénalités appliquées) — voir alerts/README.md pour les seuils.

Une information mentionnant un dirigeant ne mérite pas automatiquement une notification : elle ne compte
que si elle porte un contenu d'affaires identifiable (résultats, contrat, rachat, produit...) et concerne
clairement l'entreprise suivie — pas une simple biographie, récompense personnelle ou anecdote.
"""
import re

EARNINGS, MERGER, CONTRACT, REGULATORY, CAPEX, PRODUCT, MARKET_MOVE, EXEC_STATEMENT, MACRO, OTHER = (
    "earnings", "merger", "contract", "regulatory", "capex", "product", "market_move", "exec_statement",
    "macro", "other")

# (catégorie, score de base, mots-clés) — anglais ET français (les flux Google/Bing mélangent les deux),
# du plus déterminant pour le cours (fusion-acquisition) au plus anecdotique (simple déclaration).
CATEGORIES = [
    (MERGER, 75, ["acquisition", "acquires", "acquired", "merger", "buyout", "takeover", "divests",
                  "divestiture", "rachat", "fusion", "cession", "opa"]),
    (EARNINGS, 70, ["earnings", "quarterly results", "revenue", "guidance", "beats estimates",
                    "misses estimates", "résultats trimestriels", "chiffre d'affaires", "bénéfice",
                    "prévisions", "bénéfices"]),
    (CONTRACT, 65, ["contract", "partnership", "deal with", "signs agreement", "strategic partnership",
                    "wins contract", "partenariat", "contrat", "accord stratégique"]),
    (REGULATORY, 60, ["lawsuit", "sued", "antitrust", "investigation", "probe", "recall", "ban",
                       "export restriction", "regulation", "fine", "sanction", "enquête", "poursuite",
                       "amende", "interdiction"]),
    (MACRO, 55, ["federal reserve", "interest rate", "tariff", "trade war", "recession", "inflation",
                 "taux d'intérêt", "tarifs douaniers", "récession", "inflation"]),
    (CAPEX, 55, ["invests", "investment", "new factory", "new plant", "expands capacity", "fab",
                 "data center", "investissement", "nouvelle usine", "capacité de production"]),
    (PRODUCT, 50, ["launches", "unveils", "announces new", "new chip", "new product", "innovation",
                   "lance", "dévoile", "nouveau produit"]),
    (MARKET_MOVE, 45, ["surge", "plunge", "soars", "tumbles", "record high", "record low",
                       "unusual volume", "bondit", "chute", "plonge"]),
    (EXEC_STATEMENT, 35, ["says", "ceo says", "warns", "forecasts", "comments on", "déclare", "met en garde"]),
]

# Contenu anecdotique reconnu explicitement (plutôt que de recevoir le score par défaut OTHER/20) : repas
# d'entreprise, biographie, récompense personnelle, fortune — exactement le type d'actualité que le bot
# traitait auparavant comme n'importe quelle autre mention de dirigeant.
NOISE_HINTS = ["cafeteria", "lunch", "employee perk", "history of", "biography", "net worth", "richest",
               "sugardaddy", "philanthropy", "award", "prize", "repas", "récompense", "fortune personnelle",
               "biographie"]

# Bonus/malus de fiabilité de la source (critère "fiabilité de la source" du cahier des charges) : une
# publication réglementaire ou un média financier reconnu pèse plus qu'un agrégateur générique.
SOURCE_RELIABILITY = {
    "sec": 20, "reuters": 12, "bloomberg": 12, "wall street journal": 12, "financial times": 12,
    "cnbc": 8, "yahoo finance": 5, "google actualités": 0, "google news": 0, "bing actualités": -5,
    "bing news": -5,
}


def categorize(title):
    """(catégorie, score_de_base, mots_trouvés) — OTHER/20 (signal faible) si rien ne matche."""
    t = (title or "").lower()
    for cat, base, words in CATEGORIES:
        hits = [w for w in words if w in t]
        if hits:
            return cat, base, hits
    return OTHER, 20, []


def _reliability_bonus(source):
    s = (source or "").lower()
    for name, bonus in SOURCE_RELIABILITY.items():
        if name in s:
            return bonus
    return 0


def _mentions_company(title, ticker, company_name):
    t = (title or "").lower()
    if ticker and re.search(r"\b" + re.escape(ticker.lower()) + r"\b", t):
        return True
    if company_name:
        # Premier mot significatif du nom (« NVIDIA » dans « NVIDIA Corporation ») : évite d'exiger la
        # raison sociale complète, rarement reprise telle quelle dans un titre de presse.
        first = company_name.split()[0].lower()
        if len(first) >= 3 and first in t:
            return True
    return False


def score_news(title, ticker=None, company_name=None, source=None):
    """(score 0-100, catégorie, raison) pour une actualité mentionnant un dirigeant ou une entreprise
    suivie. Composantes, chacune tracée dans la raison :
    - impact économique/stratégique typique de la catégorie détectée (mots-clés) ;
    - fiabilité de la source ;
    - présence effective de l'entreprise/ticker dans le titre (sinon, pénalité — une mention du nom d'un
      dirigeant sans que l'entreprise elle-même soit nommée est le cas précisément visé) ;
    - contenu anecdotique reconnu explicitement (biographie, récompense, repas...), fortement pénalisé.
    Seuils de palier : voir alerts/importance.py (tier_for)."""
    cat, base, hits = categorize(title)
    score = base
    reason_bits = [f"catégorie « {cat} »" + (f" ({', '.join(hits[:3])})" if hits else " (aucun mot-clé d'affaires)")]

    bonus = _reliability_bonus(source)
    if bonus:
        score += bonus
        reason_bits.append(f"source {'fiable' if bonus > 0 else 'peu fiable'} ({source})")

    if _mentions_company(title, ticker, company_name):
        score += 10
    else:
        score -= 20
        reason_bits.append("l'entreprise suivie n'est pas explicitement nommée dans le titre")

    t = (title or "").lower()
    if any(w in t for w in NOISE_HINTS):
        score -= 30
        cat = OTHER
        reason_bits.append("contenu anecdotique détecté (biographie, récompense personnelle, repas...)")

    score = max(0, min(100, score))
    return score, cat, " ; ".join(reason_bits)
