"""Recoupement des fondamentaux de Yahoo avec les comptes annuels officiels de la SEC, et indice de fiabilité des données."""
from analysis.lang import L


def checks():  # libellé, clé Yahoo, clé SEC, type d'écart, tolérance, format
    return [
        (L("Croissance du chiffre d'affaires", "Revenue growth"), "revenueGrowth", "revenue_growth", "abs", 0.10, "pct"),
        (L("Marge nette", "Net margin"), "profitMargins", "net_margin", "abs", 0.05, "pct"),
        (L("Marge opérationnelle", "Operating margin"), "operatingMargins", "operating_margin", "abs", 0.05, "pct"),
        (L("Rentabilité des capitaux propres", "Return on equity"), "returnOnEquity", "roe", "abs", 0.15, "pct"),
        (L("Dette / capitaux propres (%)", "Debt / equity (%)"), "debtToEquity", "debt_to_equity", "abs", 35.0, "num"),
        (L("Liquidité courante", "Current ratio"), "currentRatio", "current_ratio", "abs", 0.35, "num"),
    ]


def compare(info, sec):
    """Retourne None sans données SEC ; sinon les lignes de comparaison et le nombre de concordances."""
    if not sec:
        return None
    rows, checked, ok = [], 0, 0
    for label, yk, sk, kind, tol, fmt in checks():
        y, s = (info or {}).get(yk), sec.get(sk)
        if y is None or s is None:
            rows.append({"metric": label, "yahoo": y, "sec": s, "status": "nd", "fmt": fmt})
            continue
        diff = abs(y - s)
        good = diff <= tol
        checked += 1
        ok += good
        rows.append({"metric": label, "yahoo": y, "sec": s, "status": "ok" if good else "ecart", "diff": diff, "fmt": fmt})
    return {"rows": rows, "checked": checked, "ok": ok, "fy_end": sec.get("fy_end"), "url": sec.get("url"),
            "note": L(f"Yahoo : 12 derniers mois ; SEC : exercice annuel clos le {sec.get('fy_end')}. Des écarts modérés sont normaux (périodes différentes).",
                      f"Yahoo: trailing 12 months; SEC: fiscal year ended {sec.get('fy_end')}. Moderate gaps are normal (different periods).")}


def quality(consensus, verify, cross, official):
    """Indice de fiabilité : combien de sources indépendantes confirment le cours, les fondamentaux, et les documents officiels.
    `level` est un code stable (high/good/limited/check), `level_label` le texte dans la langue courante."""
    pts, reasons, flags = 0, [], []
    if verify:
        lvl = verify["level"]
        if lvl == "high":
            pts += 3
            reasons.append(L(f"Dernière clôture confirmée par {verify['n_agree']} sources indépendantes",
                             f"Latest close confirmed by {verify['n_agree']} independent sources"))
        elif lvl == "good":
            pts += 2
            reasons.append(L("Dernière clôture confirmée par une seconde source", "Latest close confirmed by a second source"))
        elif lvl == "warn":
            flags.append(L("Une source diverge sur la dernière clôture : cours à vérifier", "One source disagrees on the latest close: price to be verified"))
        else:
            pts += 1
            reasons.append(L("Clôture issue d'une seule source (aucune seconde source disponible)", "Close from a single source (no second source available)"))
    if consensus:
        if consensus["n"] >= 2 and consensus["agree"] == consensus["n"]:
            pts += 1
            reasons.append(L(f"Cours en direct concordant sur {consensus['n']} sources (écart {consensus['spread_pct']:.2f} %)",
                             f"Live price consistent across {consensus['n']} sources (spread {consensus['spread_pct']:.2f}%)"))
        elif consensus["n"] >= 2:
            flags.append(L(f"Cours en direct : écart de {consensus['spread_pct']:.2f} % entre sources",
                           f"Live price: {consensus['spread_pct']:.2f}% spread between sources"))
        if consensus["live"]:
            pts += 1
            reasons.append(L("Au moins une source en temps réel", "At least one real-time source"))
    if cross:
        ratio = cross["ok"] / cross["checked"] if cross["checked"] else None
        ok, ch = cross["ok"], cross["checked"]
        if ratio is not None and ch >= 3 and ratio >= 0.7:
            pts += 2
            reasons.append(L(f"Fondamentaux Yahoo cohérents avec les comptes officiels SEC ({ok}/{ch})",
                             f"Yahoo fundamentals consistent with the official SEC filings ({ok}/{ch})"))
        elif ratio is not None and ratio >= 0.5:
            pts += 1
            reasons.append(L(f"Fondamentaux partiellement cohérents avec les comptes SEC ({ok}/{ch}) : périodes de calcul différentes",
                             f"Fundamentals partly consistent with SEC filings ({ok}/{ch}): different calculation periods"))
        elif ratio is not None:
            flags.append(L(f"Fondamentaux : la majorité des indicateurs Yahoo s'écarte des comptes SEC ({ok}/{ch} cohérents)",
                           f"Fundamentals: most Yahoo indicators deviate from SEC filings ({ok}/{ch} consistent)"))
    if official:
        pts += 1
        reasons.append(L("Documents officiels SEC disponibles", "Official SEC documents available"))
    level = "check" if flags and pts < 6 else "high" if pts >= 6 and not flags else "good" if pts >= 4 else "limited"
    tone = {"high": "good", "good": "info", "limited": "warn", "check": "crit"}[level]
    label = {"high": L("Élevée", "High"), "good": L("Bonne", "Good"), "limited": L("Limitée", "Limited"), "check": L("À vérifier", "To verify")}[level]
    return {"level": level, "level_label": label, "tone": tone, "points": pts, "reasons": reasons, "flags": flags}
