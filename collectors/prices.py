"""Cours de clôture via Yahoo Finance : téléchargement GROUPÉ (peu de requêtes = moins de risque de blocage)."""
import logging
from datetime import datetime, timezone

import pandas as pd
import yfinance as yf

from backend import db
from collectors import health, yahoo

log = logging.getLogger("sentinel")


def _extract(df, t):
    if df is None or df.empty:
        return None
    cols = df.columns
    try:
        if isinstance(cols, pd.MultiIndex):
            if t in cols.get_level_values(0):
                s = df[t]["Close"]
            elif t in cols.get_level_values(1):
                s = df["Close"][t]
            else:
                return None
        else:
            s = df["Close"]
    except KeyError:
        return None
    s = s.dropna()
    return s if len(s) else None


def _rate_limited_message(tickers):
    """yf.download ne lève pas toujours d'exception : les erreurs sont rangées dans yf.shared._ERRORS."""
    errs = getattr(getattr(yf, "shared", None), "_ERRORS", {}) or {}
    for t in tickers:
        msg = str(errs.get(t, ""))
        if yahoo.classify(Exception(msg))[0] == "rate":
            return msg
    return None


def download_closes(tickers, period="1y", chunk=25):
    """Retourne ({ticker: [(date, close)]}, [erreurs])."""
    out, errors = {}, []
    tickers = list(dict.fromkeys(tickers))
    for i in range(0, len(tickers), chunk):
        part = tickers[i:i + chunk]
        try:
            # part passé en argument par défaut : la lambda garde le lot de CETTE itération, même si elle est
            # appelée plus tard (sinon toutes les lambdas partagent le dernier lot de la boucle).
            df = yahoo.call("cours", lambda p=part: yf.download(p, period=period, interval="1d", auto_adjust=True,
                                                                group_by="ticker", progress=False, threads=False))
        except Exception as e:
            errors.append(f"{', '.join(part[:3])}… : {e}")
            continue
        limited = _rate_limited_message(part)
        if limited:
            health.record_error("yahoo", 429, "cours : " + limited)
            errors.append("Yahoo limite les requêtes de cours (429)")
            continue
        for t in part:
            s = _extract(df, t)
            if s is None:
                errors.append(f"{t} : aucune donnée (symbole inconnu ou retiré de la cote ?)")
                continue
            out[t] = [(ts.date(), float(v)) for ts, v in s.items()]
    return out, errors


def _extract_ohlc(df, t):
    """Ouverture/haut/bas/clôture d'une seule entreprise depuis un DataFrame yfinance (vue "bougies" : présentation
    seulement, non stocké durablement — contrairement aux clôtures de collect_prices). L'horodatage complet (pas
    seulement la date) est conservé pour que les périodes fines (heure, jour, semaine) restent lisibles."""
    if df is None or df.empty:
        return None
    cols = df.columns
    try:
        if isinstance(cols, pd.MultiIndex):
            sub = df[t] if t in cols.get_level_values(0) else df.xs(t, axis=1, level=1) if t in cols.get_level_values(1) else None
            if sub is None:
                return None
        else:
            sub = df
        o, hi, lo, c = sub["Open"], sub["High"], sub["Low"], sub["Close"]
    except KeyError:
        return None
    rows = []
    for ts in c.index:
        cv = c.get(ts)
        if pd.isna(cv):
            continue
        ov, hv, lv = o.get(ts), hi.get(ts), lo.get(ts)
        rows.append((ts.isoformat(), None if pd.isna(ov) else float(ov), None if pd.isna(hv) else float(hv),
                      None if pd.isna(lv) else float(lv), float(cv)))
    return rows if rows else None


def download_ohlc(ticker, period="6mo", interval="1d", prepost=False):
    """Chandelier (ouverture/haut/bas/clôture) pour une seule entreprise, calculé à la demande pour la vue
    "graphique" de la fiche société, pas conservé en base comme les clôtures. 'interval' va de la minute
    (périodes courtes : heure, jour, semaine) à l'hebdomadaire (10 ans), selon ce que Yahoo autorise pour chaque
    finesse. prepost=True ajoute les échanges avant et après la séance officielle (actions américaines : de 10 h à
    2 h du matin, heure de Paris), comme les applis de bourse. Retourne ([(horodatage, open, high, low, close)], erreur ou None)."""
    try:
        df = yahoo.call("cours (bougies)", lambda: yf.download(ticker, period=period, interval=interval, auto_adjust=True, prepost=prepost,
                                                                progress=False, threads=False), timeout=10)
    except Exception as e:
        return None, str(e)
    rows = _extract_ohlc(df, ticker)
    if not rows:
        return None, "aucune donnée"
    return rows, None


def download_intraday_many(tickers, timeout=25):
    """Points de 5 minutes des 5 derniers jours, échanges hors séance compris, pour plusieurs actions en une fois
    (cours « en direct » de la liste). Retourne {ticker: [(horodatage, open, high, low, close)]}."""
    tickers = list(dict.fromkeys(tickers))
    if not tickers:
        return {}
    df = yahoo.call("cours en direct", lambda: yf.download(tickers, period="5d", interval="5m", prepost=True, auto_adjust=True,
                                                         group_by="ticker", progress=False, threads=True), timeout=timeout)
    out = {}
    for t in tickers:
        rows = _extract_ohlc(df, t)
        if rows:
            out[t] = rows
    return out


def collect_prices(tickers, period="1y"):
    """Yahoo en groupe d'abord ; les symboles manquants (source bloquée ou inconnue) sont repris par les autres fournisseurs."""
    from collectors import multi
    data, errors = download_closes(tickers, period)
    used = {t: "yahoo" for t in data}
    for t in [x for x in dict.fromkeys(tickers) if x not in data]:
        src, rows, errs = multi.history_fallback(t, {"10y": 2600, "5y": 1300, "2y": 520}.get(period, 260), skip=("yahoo",))
        if rows:
            data[t], used[t] = rows, src
            errors = [e for e in errors if not e.startswith(t + " ")]
    written = 0
    for t, rows in data.items():
        db.upsert_prices(t, rows)
        db.kv_set(f"pricesrc:{t}", {"source": used[t], "at": datetime.now(timezone.utc).isoformat()})
        written += len(rows)
    return {"written": written, "errors": errors, "sources": used}
