"""Sentiment très simple d'un titre d'article, par mots-clés (indicatif, pas une analyse fine)."""
import re

POS = ["upgrade", "upgrades", "upgraded", "raises price target", "price target raised", "raises target",
       "beats", "beat estimates", "tops estimates", "record revenue", "record high", "surge", "surges",
       "soars", "soar", "jumps", "rallies", "strong demand", "wins contract", "partnership", "buyback",
       "raises guidance", "raises outlook", "outperform", "overweight", "buy rating", "new high", "expands"]
NEG = ["downgrade", "downgrades", "downgraded", "cuts price target", "lowers price target", "misses",
       "missed estimates", "lawsuit", "sued", "probe", "investigation", "recall", "plunge", "plunges",
       "slump", "tumbles", "falls", "warning", "cuts guidance", "lowers outlook", "layoffs", "ban",
       "export restrictions", "antitrust", "breach", "outage", "sell rating", "underperform", "selloff"]

_POS = [(t, re.compile(r"\b" + re.escape(t) + r"\b")) for t in POS]
_NEG = [(t, re.compile(r"\b" + re.escape(t) + r"\b")) for t in NEG]


def headline_sentiment(text):
    """Retourne (-1 | 0 | 1, [mots-clés trouvés])."""
    t = (text or "").lower()
    pos = [w for w, rx in _POS if rx.search(t)]
    neg = [w for w, rx in _NEG if rx.search(t)]
    s = (len(pos) > len(neg)) - (len(pos) < len(neg))
    return s, pos + neg
