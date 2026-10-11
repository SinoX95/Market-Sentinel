"""Positions des grands investisseurs via les dépôts 13F de la SEC (EDGAR, gratuit, sans clé).

Un 13F liste les actions détenues à la fin de chaque trimestre, déposé jusqu'à 45 jours plus tard :
c'est une photo, avec du retard. Il donne le QUOI (achats, ventes, taille), pas le POURQUOI.
"""
import logging
import xml.etree.ElementTree as ET

from collectors.http import fetch, fetch_json
from collectors.news import search_news

log = logging.getLogger("sentinel")
SUBMISSIONS = "https://data.sec.gov/submissions/CIK{cik}.json"
ARCHIVE = "https://www.sec.gov/Archives/edgar/data/{cik_int}/{acc}/"


def _tag(el):
    return el.tag.rsplit("}", 1)[-1]


def parse_infotable(xml_bytes, in_thousands=False):
    """Retourne {(cusip, put_call): {issuer, cusip, pc, shares, value}} (lignes agrégées par titre)."""
    root = ET.fromstring(xml_bytes)
    mult = 1000 if in_thousands else 1
    rows = {}
    for el in root.iter():
        if _tag(el) != "infoTable":
            continue
        flat = {}
        for c in el.iter():
            flat.setdefault(_tag(c), (c.text or "").strip())
        if flat.get("sshPrnamtType", "SH").upper() != "SH":
            continue  # obligations : ignorées
        try:
            shares = int(float(flat.get("sshPrnamt", "0")))
            value = float(flat.get("value", "0")) * mult
        except ValueError:
            continue
        cusip, pc = flat.get("cusip", ""), flat.get("putCall", "").upper()
        r = rows.setdefault((cusip, pc), {"issuer": flat.get("nameOfIssuer", ""), "cusip": cusip,
                                          "pc": pc, "shares": 0, "value": 0.0})
        r["shares"] += shares
        r["value"] += value
    return rows


def list_13f(cik):
    j = fetch_json(SUBMISSIONS.format(cik=cik), "sec")
    rec = j["filings"]["recent"]
    out, seen = [], set()
    for i, form in enumerate(rec["form"]):
        if form == "13F-HR" and rec["reportDate"][i] not in seen:
            seen.add(rec["reportDate"][i])
            out.append({"accession": rec["accessionNumber"][i], "filed": rec["filingDate"][i],
                        "period": rec["reportDate"][i]})
    return j.get("name", ""), out


def fetch_infotable(cik, filing):
    base = ARCHIVE.format(cik_int=int(cik), acc=filing["accession"].replace("-", ""))
    idx = fetch_json(base + "index.json", "sec")
    names = [it["name"] for it in idx["directory"]["item"] if it["name"].lower().endswith(".xml")]
    for n in sorted(names):
        if "primary_doc" in n.lower():
            continue
        rows = parse_infotable(fetch(base + n, "sec", timeout=90), in_thousands=filing["filed"] < "2023-01-03")
        if rows:
            return rows
    raise ValueError("table d'information 13F introuvable")


def diff(cur, prev):
    """Changements de position entre deux trimestres, triés par montant estimé."""
    out = []
    for k, c in cur.items():
        p = prev.get(k)
        unit = c["value"] / c["shares"] if c["shares"] else 0
        if p is None:
            out.append({"kind": "new", "issuer": c["issuer"], "cusip": c["cusip"], "pc": c["pc"],
                        "shares": c["shares"], "pct": None, "flow": c["value"]})
        elif p["shares"]:
            pct = (c["shares"] - p["shares"]) / p["shares"] * 100
            if abs(pct) >= 10:
                out.append({"kind": "add" if pct > 0 else "reduce", "issuer": c["issuer"], "cusip": c["cusip"],
                            "pc": c["pc"], "shares": c["shares"], "pct": round(pct, 1),
                            "flow": (c["shares"] - p["shares"]) * unit})
    for k, p in prev.items():
        if k not in cur:
            out.append({"kind": "exit", "issuer": p["issuer"], "cusip": p["cusip"], "pc": p["pc"],
                        "shares": 0, "pct": -100.0, "flow": -p["value"]})
    out.sort(key=lambda x: abs(x["flow"]), reverse=True)
    return out[:400]


def refresh_investor(inv):
    name, filings = list_13f(inv["cik"])
    if not filings:
        raise ValueError("aucun dépôt 13F trouvé")
    cur = fetch_infotable(inv["cik"], filings[0])
    prev = fetch_infotable(inv["cik"], filings[1]) if len(filings) > 1 else {}
    total = sum(r["value"] for r in cur.values())
    holdings = sorted(cur.values(), key=lambda r: r["value"], reverse=True)
    result = {
        "sec_name": name,
        "period": filings[0]["period"], "filed": filings[0]["filed"], "accession": filings[0]["accession"],
        "prev_period": filings[1]["period"] if len(filings) > 1 else None,
        "total_value": total, "n_positions": len(cur),
        "holdings": holdings, "changes": diff(cur, prev) if prev else [],
        "news": [],
    }
    try:
        result["news"] = search_news(inv["news_query"], days=60, limit=6)
    except Exception as e:
        log.warning("news investisseur %s : %s", inv["slug"], e)
    return result
