"""Données OFFICIELLES de la SEC (EDGAR), gratuites et sans clé :
 - comptes annuels (XBRL « company facts ») pour recouper les fondamentaux de Yahoo ;
 - documents déposés récemment (8-K, 10-Q, 10-K…) : la source primaire des annonces des entreprises ;
 - transactions des dirigeants (formulaire 4) : achats et ventes sur le marché.
Couvre les sociétés américaines qui déposent des 10-K (pas les sociétés étrangères en 20-F/IFRS).
"""
import logging
import xml.etree.ElementTree as ET
from datetime import date, timedelta

from collectors.http import fetch, fetch_json

log = logging.getLogger("sentinel")
FACTS = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik:010d}.json"
SUBMISSIONS = "https://data.sec.gov/submissions/CIK{cik:010d}.json"
ARCHIVE = "https://www.sec.gov/Archives/edgar/data/{cik}/{acc}/{doc}"

REVENUE = ["Revenues", "RevenueFromContractWithCustomerExcludingAssessedTax", "SalesRevenueNet", "RevenueFromContractWithCustomerIncludingAssessedTax"]
ITEM_FR = {"1.01": "Accord important signé", "1.02": "Fin d'un accord important", "1.03": "Faillite", "2.01": "Acquisition ou cession",
           "2.02": "Résultats trimestriels", "2.03": "Nouvelle dette", "2.05": "Restructuration", "2.06": "Dépréciation d'actifs",
           "3.01": "Avis de radiation de la cote", "3.02": "Émission de titres", "4.02": "Comptes à corriger", "5.01": "Changement de contrôle",
           "5.02": "Changement de dirigeants ou d'administrateurs", "5.03": "Modification des statuts", "5.07": "Résultat du vote des actionnaires",
           "7.01": "Communication aux investisseurs", "8.01": "Autre événement important", "9.01": "Pièces jointes"}
FORM_FR = {"8-K": "Annonce d'événement (8-K)", "10-Q": "Rapport trimestriel (10-Q)", "10-K": "Rapport annuel (10-K)", "4": "Transaction de dirigeant (formulaire 4)",
           "6-K": "Annonce (6-K)", "20-F": "Rapport annuel (20-F)", "SC 13D": "Prise de participation (13D)", "SC 13G": "Participation > 5 % (13G)",
           "SC 13G/A": "Participation > 5 % (13G/A)", "SC 13D/A": "Prise de participation (13D/A)", "DEF 14A": "Assemblée générale (DEF 14A)", "S-1": "Introduction en bourse (S-1)"}


def _d(s):
    return date.fromisoformat(s[:10])


def _series(facts, concepts, unit="USD", flow=True):
    """Valeurs annuelles (exercices clos, formulaire 10-K), la plus récente d'abord : [{end, val}]."""
    for c in concepts:
        arr = facts.get("us-gaap", {}).get(c, {}).get("units", {}).get(unit, [])
        best = {}
        for e in arr:
            if e.get("form") not in ("10-K", "10-K/A") or e.get("fp") != "FY" or "end" not in e:
                continue
            if flow:
                if not e.get("start") or not 340 <= (_d(e["end"]) - _d(e["start"])).days <= 380:
                    continue
            cur = best.get(e["end"])
            if not cur or e.get("filed", "") > cur.get("filed", ""):
                best[e["end"]] = e
        if best:
            return sorted(best.values(), key=lambda x: x["end"], reverse=True)
    return []


def _v(series, i=0):
    return series[i]["val"] if len(series) > i else None


def annual_metrics(cik):
    """Indicateurs tirés du dernier exercice annuel déposé. None si non calculable."""
    facts = fetch_json(FACTS.format(cik=int(cik)), "sec", timeout=90).get("facts", {})
    rev = _series(facts, REVENUE)
    ni = _series(facts, ["NetIncomeLoss", "ProfitLoss"])
    op = _series(facts, ["OperatingIncomeLoss"])
    gp = _series(facts, ["GrossProfit"])
    eq = _series(facts, ["StockholdersEquity", "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest"], flow=False)
    ltd = _series(facts, ["LongTermDebtNoncurrent", "LongTermDebt"], flow=False)
    std = _series(facts, ["LongTermDebtCurrent", "DebtCurrent", "ShortTermBorrowings"], flow=False)
    ca, cl = _series(facts, ["AssetsCurrent"], flow=False), _series(facts, ["LiabilitiesCurrent"], flow=False)
    ocf = _series(facts, ["NetCashProvidedByUsedInOperatingActivities"])
    capex = _series(facts, ["PaymentsToAcquirePropertyPlantAndEquipment", "PaymentsToAcquireProductiveAssets"])
    eps = _series(facts, ["EarningsPerShareDiluted"], unit="USD/shares")
    if not rev:
        return None
    div = lambda a, b: (a / b) if a is not None and b else None
    debt = (_v(ltd) or 0) + (_v(std) or 0) if (ltd or std) else None
    return {
        "fy_end": rev[0]["end"], "filed": rev[0].get("filed"), "revenue": _v(rev), "revenue_prev": _v(rev, 1),
        "revenue_growth": (_v(rev) / _v(rev, 1) - 1) if _v(rev, 1) else None,
        "net_margin": div(_v(ni), _v(rev)), "operating_margin": div(_v(op), _v(rev)), "gross_margin": div(_v(gp), _v(rev)),
        "roe": div(_v(ni), _v(eq)), "debt_to_equity": (debt / _v(eq) * 100) if debt is not None and _v(eq) else None,
        "current_ratio": div(_v(ca), _v(cl)), "fcf": (_v(ocf) - _v(capex)) if _v(ocf) is not None and _v(capex) is not None else None,
        "eps": _v(eps), "net_income": _v(ni),
        "url": f"https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK={int(cik):010d}&type=10-K",
    }


def _recent(cik):
    return fetch_json(SUBMISSIONS.format(cik=int(cik)), "sec")["filings"]["recent"]


def recent_filings(cik, days=60, limit=10):
    """Documents officiels récents : {form, label, date, items, url}. Les formulaires 4 sont traités à part."""
    rec = _recent(cik)
    cutoff = (date.today() - timedelta(days=days)).isoformat()
    out = []
    for i, form in enumerate(rec["form"]):
        if rec["filingDate"][i] < cutoff or form == "4" or form not in FORM_FR:
            continue
        items = [it.strip() for it in (rec.get("items", [""] * len(rec["form"]))[i] or "").split(",") if it.strip()]
        what = [ITEM_FR[it] for it in items if it in ITEM_FR and it != "9.01"]
        out.append({"form": form, "label": FORM_FR[form], "date": rec["filingDate"][i], "items": items, "what": what,
                    "url": ARCHIVE.format(cik=int(cik), acc=rec["accessionNumber"][i].replace("-", ""), doc=rec["primaryDocument"][i])})
        if len(out) >= limit:
            break
    return out


def parse_form4(xml_bytes):
    """Transactions sur le marché d'un formulaire 4 : [{date, code (P achat, S vente), shares, price}] + identité."""
    root = ET.fromstring(xml_bytes)
    tx = lambda el, path: (el.findtext(path) or "").strip()
    owner = root.find("reportingOwner")
    name = tx(owner, "reportingOwnerId/rptOwnerName") if owner is not None else ""
    rel = owner.find("reportingOwnerRelationship") if owner is not None else None
    role = ""
    if rel is not None:
        role = tx(rel, "officerTitle") or ("Administrateur" if tx(rel, "isDirector") in ("1", "true") else "Actionnaire important" if tx(rel, "isTenPercentOwner") in ("1", "true") else "")
    out = []
    for t in root.iter("nonDerivativeTransaction"):
        code = tx(t, "transactionCoding/transactionCode")
        if code not in ("P", "S"):
            continue
        try:
            shares = float(tx(t, "transactionAmounts/transactionShares/value"))
            price = float(tx(t, "transactionAmounts/transactionPricePerShare/value") or 0)
        except ValueError:
            continue
        out.append({"date": tx(t, "transactionDate/value"), "code": code, "shares": shares, "price": price})
    return name, role, out


def insider_trades(cik, days=120, max_forms=10):
    """Achats et ventes de dirigeants sur le marché, d'après les formulaires 4 (déclarés sous 2 jours ouvrés)."""
    rec = _recent(cik)
    cutoff = (date.today() - timedelta(days=days)).isoformat()
    trades, seen = [], 0
    for i, form in enumerate(rec["form"]):
        if form != "4" or rec["filingDate"][i] < cutoff:
            continue
        seen += 1
        if seen > max_forms:
            break
        doc = rec["primaryDocument"][i].split("/")[-1]  # version XML brute (sans la feuille de style xsl)
        url = ARCHIVE.format(cik=int(cik), acc=rec["accessionNumber"][i].replace("-", ""), doc=doc)
        try:
            name, role, txs = parse_form4(fetch(url, "sec"))
        except Exception as e:
            log.info("formulaire 4 illisible (%s) : %s", url, e)
            continue
        for t in txs:
            trades.append({**t, "insider": name, "role": role, "filed": rec["filingDate"][i], "value": t["shares"] * t["price"], "url": url})
    buys = [t for t in trades if t["code"] == "P"]
    sells = [t for t in trades if t["code"] == "S"]
    return {"trades": trades[:12], "buys": len(buys), "sells": len(sells), "buy_value": sum(t["value"] for t in buys),
            "sell_value": sum(t["value"] for t in sells), "days": days, "forms_read": min(seen, max_forms)}


def cik_for(ticker):
    """CIK (identifiant SEC) d'un symbole, via l'index public des tickers (nécessite l'email de contact)."""
    from collectors.search import sec_index
    t = ticker.upper()
    for tk, _, cik in sec_index():
        if tk.upper() == t:
            return int(cik)
    return None
