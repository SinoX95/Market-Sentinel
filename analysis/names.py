"""Rapprochement d'un nom d'émetteur 13F (ex. « NVIDIA CORP ») avec un nom d'entreprise (« NVIDIA Corporation »)."""
import re

SUFFIX = {"INC", "CORP", "CORPORATION", "CO", "COMPANY", "LTD", "LIMITED", "PLC", "LP", "LLC", "NV", "SA", "AG", "SE",
          "HOLDINGS", "HOLDING", "HLDGS", "HLDG", "GROUP", "GRP", "THE", "CLASS", "CL", "COM", "ORD", "ADR", "SPONSORED",
          "ORDINARY", "SHS", "SHARES", "NEW", "DEL", "COMMON", "STOCK", "A", "B", "C"}
STOP = {"OF", "AND", "THE", "&", "DE", "DU", "LA", "LE"}
ABBREV = {"MFG": "MANUFACTURING", "INTL": "INTERNATIONAL", "TECH": "TECHNOLOGIES", "TECHNOLOGY": "TECHNOLOGIES",
          "SYS": "SYSTEMS", "NETWORKS": "NETWORKS", "SEMICONDUCTORS": "SEMICONDUCTOR"}


def tokens(name):
    words = re.sub(r"[^A-Z0-9& ]", " ", (name or "").upper()).split()
    out = [ABBREV.get(w, w) for w in words if w not in SUFFIX]
    return out


def same_company(a, b):
    """Vrai si les deux noms désignent (très probablement) la même entreprise."""
    ta, tb = tokens(a), tokens(b)
    if not ta or not tb:
        return False
    if ta == tb:
        return True
    sa = [w for w in ta if w not in STOP]
    sb = [w for w in tb if w not in STOP]
    if len(sa) >= 2 and len(sb) >= 2 and sa[:2] == sb[:2]:
        return True
    return len(sa) == 1 and len(sb) == 1 and sa == sb
