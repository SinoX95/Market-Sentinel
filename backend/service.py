"""Boucle automatique (collecte, scoring, secteurs, investisseurs) et données servies au dashboard."""
import json
import logging
import os
import re
import threading
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutTimeout
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from analysis.fundamentals import assess
from analysis.investors import build_view, signals_for
from analysis.crosscheck import compare as crosscheck_compare, quality as data_quality
from analysis.opportunities import analyze, official_signals, upcoming
from analysis.scoring import compute_score, explain
from alerts.notify import notify
from backend import auth, db
from collectors import health
from collectors.catalysts import collect_events
from collectors.fundamentals import collect_fundamentals
from collectors.http import SETTINGS, contact_email, raw_settings, settings
from collectors.investors import refresh_investor
from collectors.news import collect_news
from collectors import multi, sec_facts
from collectors.providers import REGISTRY, quota_used
from collectors.prices import collect_prices
from collectors.search import names_by_ticker, search
from collectors.sectors import collect_sectors, load_config as load_sector_config, rank_sector, sector_by_id
from collectors.prices import download_closes, download_intraday_many, download_ohlc
from analysis.compare import stock_metrics
from analysis import lang
from analysis import fr_terms
from analysis.lang import L
from analysis.sectors import reasons_for, state_label, trend_label
from collectors import fx

log = logging.getLogger("sentinel")
WATCHLIST = Path("config/watchlist.json")
INVESTORS = Path("config/investors.json")
INVESTOR_REFRESH_H = 24
SECTOR_REFRESH_H = 4
NEWS_TTL, EVENTS_TTL, FUND_TTL, PRICE_TTL = 3600, 12 * 3600, 12 * 3600, 3600
COMPANY_TTL = 3600  # fiche complète (fondamentaux, actualités, SEC…) rechargée en arrière-plan toutes les heures
VERIFY_TTL, SECFACTS_TTL, SECFILINGS_TTL, SECINSIDERS_TTL = 6 * 3600, 24 * 3600, 3 * 3600, 6 * 3600
INTERVAL_S = int(os.getenv("COLLECT_INTERVAL_MIN", "60")) * 60
TICKER_RE = re.compile(r"^[A-Z0-9][A-Z0-9.\-]{0,11}$")

# Mode « serverless » (Cloud Run) : pas de boucle en arrière-plan (le CPU est coupé entre deux requêtes) ;
# la collecte est déclenchée par un planificateur externe via /internal/cycle.
SERVERLESS = bool(os.getenv("SERVERLESS") or os.getenv("K_SERVICE"))
state = {"last_run": None, "running": False, "phase": None, "investor_errors": {}}
_lock = threading.Lock()


# ---------- listes de suivi ----------
def _wl_key(user):
    return f"watchlist:u:{auth.norm(user)}"


def _starter_list():
    """Liste de départ du compte principal : la liste de base du projet + son ancienne liste commune éventuelle."""
    base = json.loads(WATCHLIST.read_text(encoding="utf-8"))["companies"]
    legacy = db.kv_get("watchlist:user")[0] or []
    have = {c["ticker"] for c in base}
    return [dict(c) for c in base] + [c for c in legacy if c["ticker"] not in have]


def _user_watchlist(user=None):
    """Entreprises suivies par un utilisateur. Le compte principal démarre avec la liste de base du projet ;
    tous les autres comptes démarrent avec une liste vide."""
    user = user or auth.current()
    data = db.kv_get(_wl_key(user))[0]
    if data is None:
        data = _starter_list() if auth.norm(user) == auth.norm(auth.config()["user"]) else []
    return data


def _all_users_watchlists():
    out, seen = [], set()
    names = [auth.config()["user"]] + [u["name"] for u in auth.list_users()[1:]]
    for n in names:
        for c in _user_watchlist(n):
            if c["ticker"] not in seen:
                seen.add(c["ticker"])
                out.append(c)
    return out


def load_watchlist(all_users=False):
    """Entreprises suivies par l'utilisateur courant ; en tâche de fond (ou all_users), celles de tous les utilisateurs réunies."""
    rows = _all_users_watchlists() if (all_users or auth.current() is None) else _user_watchlist()
    return [{**c, "base": False} for c in rows]


def load_investors():
    return json.loads(INVESTORS.read_text(encoding="utf-8"))["investors"]


def add_to_watchlist(ticker, name=None):
    ticker = (ticker or "").upper().strip()
    if not TICKER_RE.match(ticker):
        raise ValueError("symbole invalide")
    if any(c["ticker"] == ticker for c in load_watchlist()):
        return {"ok": True, "already": True}
    items = _user_watchlist() + [{"ticker": ticker, "name": name or ticker}]
    db.kv_set(_wl_key(auth.current()), items)
    if SERVERLESS:
        _prepare_followed(ticker, name)  # pas de tâche de fond fiable : on prépare avant de répondre
    else:
        threading.Thread(target=_prepare_followed, args=(ticker, name), daemon=True).start()
    return {"ok": True}


def remove_from_watchlist(ticker):
    ticker = ticker.upper()
    items = [c for c in _user_watchlist() if c["ticker"] != ticker]
    db.kv_set(_wl_key(auth.current()), items)
    return {"ok": True}


def _prepare_followed(ticker, name):
    try:
        ensure_company(ticker, hint_name=name)
        _record_recommendations(_load_closes([ticker]))
    except Exception:
        log.exception("préparation de %s", ticker)


# ---------- collecte ----------
def _load_closes(tickers):
    return db.load_closes(tickers)


def _record_recommendations(closes):
    """Enregistre une recommandation à chaque changement de niveau, avec le prix du moment, et alerte sur les
    canaux configurés (Telegram/email — voir alerts/notify.py). Pas d'alerte si aucun canal n'est réglé, ni
    lors du tout premier calcul pour un titre (pas un vrai changement, juste l'absence d'historique)."""
    for t, series in closes.items():
        res = compute_score([c for _, c in series])
        if not res:
            continue
        prev_label = db.last_label(t)
        if prev_label != res["label"]:
            price = series[-1][1]
            db.add_reco(t, res["score"], res["label"], price)
            if prev_label is not None:
                try:
                    notify(f"Market Sentinel : {t} {prev_label} → {res['label']}",
                           f"Score {res['score']}/100, prix {price}.", ticker=t, kind="score_change",
                           label=res["label"])
                except Exception:
                    log.exception("notification de changement de niveau pour %s échouée", t)


DEFAULT_ALERT_PCT = 7.0  # variation déclenchant une alerte, si le titre n'a pas son propre seuil "alert_pct"
                          # dans config/watchlist.json (Module H)
INTRADAY_ALERT_STEP_PCT = 5.0  # palier (points de %) autorisant une nouvelle alerte le même jour : sinon un
                                 # creux qui se détériore encore après une première alerte ne serait jamais resignalé


def _maybe_alert_price_move(ticker, pct, threshold, live):
    """Dédoublonne et notifie un mouvement de prix, que la source soit la clôture (une fois la séance
    terminée) ou le cours en direct (toutes les ~10 min pendant la séance — voir _check_intraday_moves).
    Les deux partagent la même clé de déduplication par jour : pas de double alerte si le cours en direct a
    déjà signalé le même mouvement avant la clôture officielle.

    Une nouvelle alerte le même jour est autorisée si le mouvement s'aggrave d'au moins un palier
    (INTRADAY_ALERT_STEP_PCT) ou change de sens — une seule alerte fixe par jour raterait un creux qui se
    creuse encore après coup, exactement le genre de cas que ce module doit repérer.

    Ajoute, quand elle existe, l'actualité récente (cache déjà alimenté par _collect_catalysts, pas d'appel
    réseau ici) qui va dans le même sens que le mouvement (_movement_note, réutilisé du tableau de bord) —
    une piste pour comprendre le pourquoi tout de suite, jamais présentée comme une certitude causale."""
    if abs(pct) < threshold:
        return
    today = date.today().isoformat()
    sign = "+" if pct > 0 else "-"
    bucket = int(abs(pct) // INTRADAY_ALERT_STEP_PCT)
    key = f"pricealert:{ticker}"
    last = db.kv_get(key)[0] or {}
    if last.get("date") == today and last.get("sign") == sign and last.get("bucket", 0) >= bucket:
        return  # déjà alerté aujourd'hui pour ce sens et ce palier (ou pire)
    db.kv_set(key, {"date": today, "sign": sign, "pct": round(pct, 1), "bucket": bucket})
    sens = "hausse" if pct > 0 else "baisse"
    source = "en direct" if live else "clôture"
    text = f"Variation de {pct:+.1f} % par rapport à la clôture précédente (seuil {threshold} %)."
    try:
        news = db.kv_get(f"news:{ticker}")[0] or []
        note = _movement_note(pct, news, threshold=0)  # seuil déjà filtré par l'appelant : on cherche toujours
        if note and note["matched"]:
            lignes = [f"- {n.get('title_fr') or n['title']} ({n.get('source') or '?'}) — {n.get('url') or ''}"
                      for n in note["items"]]
            text += ("\n\nActualité récente pouvant l'expliquer (pas une certitude causale — voir les sources "
                     "ci-dessous) :\n" + "\n".join(lignes))
    except Exception:
        log.exception("recherche d'actualité pour expliquer le mouvement de %s échouée", ticker)
    try:
        notify(f"Market Sentinel : {ticker} {sens} de {abs(pct):.1f} % ({source})", text,
               ticker=ticker, kind="intraday_move" if live else "daily_move", pct=pct, threshold=threshold)
    except Exception:
        log.exception("alerte de variation pour %s échouée", ticker)


def _check_price_moves(companies, closes):
    """Variation de clôture à clôture — filet de sécurité si le cours en direct est indisponible
    (_check_intraday_moves le détecte normalement plus tôt dans la journée)."""
    for c in companies:
        t = c["ticker"]
        series = closes.get(t)
        if not series or len(series) < 2:
            continue
        price, prev_price = series[-1][1], series[-2][1]
        if not prev_price:
            continue
        pct = (price - prev_price) / prev_price * 100
        _maybe_alert_price_move(t, pct, c.get("alert_pct", DEFAULT_ALERT_PCT), live=False)


def _previous_close(ticker):
    """Dernière clôture connue strictement avant aujourd'hui — jamais la clôture du jour, encore
    provisoire tant que la séance n'est pas terminée."""
    series = db.load_closes([ticker])[ticker]
    today = date.today()
    for d, c in reversed(series):
        if d < today:
            return c
    return None


def _check_intraday_moves(companies):
    """Variation du cours en direct (points de 5 min, pré/post-bourse compris — collectors.prices.
    download_intraday_many, déjà utilisé ailleurs pour l'affichage mais jamais branché sur les alertes)
    par rapport à la dernière clôture connue. Capte un creux ou un pic qui pourrait se résorber avant la
    clôture officielle, ce que _check_price_moves (clôture à clôture) ne peut pas voir avant le lendemain."""
    tickers = [c["ticker"] for c in companies]
    try:
        intraday = download_intraday_many(tickers)
    except Exception as e:
        log.info("cours en direct pour les alertes : %s", e)
        return
    by_ticker = {c["ticker"]: c for c in companies}
    for t, rows in intraday.items():
        if not rows:
            continue
        last_price = rows[-1][4]  # (horodatage, open, high, low, close)
        prev_close = _previous_close(t)
        if not prev_close:
            continue
        pct = (last_price - prev_close) / prev_close * 100
        c = by_ticker.get(t, {})
        _maybe_alert_price_move(t, pct, c.get("alert_pct", DEFAULT_ALERT_PCT), live=True)


def _check_earnings_filing(ticker, filings):
    """Alerte dès qu'un nouveau dépôt officiel de résultats trimestriels apparaît (8-K, item SEC 2.02) —
    directement depuis les documents déjà collectés par sec_facts.recent_filings (voir ensure_company
    ci-dessus), sans appel réseau supplémentaire. C'est la publication elle-même qui déclenche l'alerte,
    pas une comparaison au consensus des analystes (donnée payante que la SEC ne fournit pas) : voir
    alerts/README.md pour cette limite."""
    seen_key = f"earningsalert:{ticker}:seen"
    seen = set((db.kv_get(seen_key)[0] or {}).get("urls", []))
    results = [f for f in filings if "2.02" in f.get("items", [])]
    new_results = [f for f in results if f["url"] not in seen]
    all_urls = list(seen | {f["url"] for f in results})[-50:]
    db.kv_set(seen_key, {"urls": all_urls})
    if not seen or not new_results:
        return
    for f in new_results[:2]:
        try:
            notify(f"Market Sentinel : résultats publiés — {ticker}", f"{f['label']}, déposé le {f['date']}.\n{f['url']}",
                   ticker=ticker, kind="earnings_filing")
        except Exception:
            log.exception("alerte de résultats pour %s échouée", ticker)


def _check_official_feeds():
    """Alerte sur les nouveaux communiqués des banques centrales depuis le dernier cycle (Module B/C) —
    voir collectors/official_feeds.py. Rien n'est envoyé au tout premier cycle : on enregistre juste
    l'historique de départ, faute de quoi tout l'historique récent serait notifié d'un coup."""
    from collectors.official_feeds import fetch_all
    for key, items in fetch_all().items():
        seen_key = f"officialfeed:{key}:seen"
        seen = set((db.kv_get(seen_key)[0] or {}).get("urls", []))
        new_items = [it for it in items if it["url"] not in seen]
        all_urls = list(seen | {it["url"] for it in items})[-200:]
        db.kv_set(seen_key, {"urls": all_urls})
        if not seen or not new_items:
            continue
        for it in new_items[:5]:  # borne même en cas de rafale (ex. publication groupée)
            try:
                notify(f"Market Sentinel : annonce {it['source']}", f"{it['title']}\n{it['url']}", kind=f"official_feed:{key}")
            except Exception:
                log.exception("notification de communiqué officiel échouée")


def _check_executives(companies=None):
    """Alerte sur les nouvelles actualités mentionnant un dirigeant suivi (Module B) — voir
    collectors/executives.py. Ne fait rien si config/executives.json est absent ou vide.

    Une simple mention du nom du dirigeant ne suffit plus (Module 4/5/8) : le titre est passé au score de
    pertinence (alerts/relevance.py), qui pénalise fortement un contenu anecdotique (biographie, repas,
    récompense personnelle...) ou qui ne nomme pas l'entreprise elle-même — un résultat sous le seuil
    "À surveiller" n'est ni envoyé ni journalisé (voir alerts/notify.py::notify). Les résultats qui passent
    le seuil sont en plus dédupliqués par événement, pas seulement par URL (Module 7 — alerts/dedup.py :
    plusieurs médias relayant la même dépêche ne comptent qu'une fois), et leur URL est résolue vers
    l'article direct quand c'est possible (Module 6 — collectors/news.py::direct_url), uniquement pour ce
    sous-ensemble déjà filtré (pas de coût réseau supplémentaire pour les items écartés).

    Le titre est traduit en français si besoin (`collectors/translate.py`, même mécanisme que pour les
    actualités de mouvement de prix — traduction best-effort, le texte d'origine est gardé en cas d'échec)."""
    from alerts import dedup
    from alerts.importance import SEUIL_SURVEILLER, score_for
    from collectors.executives import fetch_mentions, load_config
    from collectors.logos import logo_url
    from collectors.news import direct_url
    from collectors.translate import add_french_titles
    names = {c["ticker"]: c["name"] for c in (companies or [])}
    for exe in load_config():
        seen_key = f"execfeed:{exe['slug']}:seen"
        try:
            items = add_french_titles(fetch_mentions(exe))
        except Exception as e:
            log.info("actualités dirigeant %s : %s", exe["slug"], e)
            continue
        seen = set((db.kv_get(seen_key)[0] or {}).get("urls", []))
        new_items = [it for it in items if it["url"] not in seen]
        all_urls = list(seen | {it["url"] for it in items})[-100:]
        db.kv_set(seen_key, {"urls": all_urls})
        log.info("dirigeant %s : %d actualité·s récupérée·s, %d nouvelle·s depuis le dernier passage",
                  exe["slug"], len(items), len(new_items))
        if not seen:
            log.info("dirigeant %s : première exécution, référence enregistrée sans alerte", exe["slug"])
            continue
        if not new_items:
            continue
        ticker = exe.get("ticker")
        company_name = names.get(ticker) or exe.get("name")
        for it in new_items[:3]:
            title = it.get("title")
            try:
                score, reason = score_for("executive", title=title, ticker=ticker, company_name=company_name,
                                           source=it.get("source"))
                if score < SEUIL_SURVEILLER:
                    log.info("dirigeant %s rejeté (score %d/100, seuil %d) : %s — %s",
                              ticker, score, SEUIL_SURVEILLER, title, reason)
                    continue  # contenu anecdotique/hors sujet (Module 4/5) : ni résolution d'URL ni envoi
                if not dedup.is_new_event(ticker, title, db.kv_get, db.kv_set):
                    log.info("dirigeant %s écarté (doublon d'un événement déjà alerté) : %s", ticker, title)
                    continue  # déjà alerté pour cet événement (reformulé par un autre média)
                log.info("dirigeant %s retenu (score %d/100) : %s — %s", ticker, score, title, reason)
                titre_fr = it.get("title_fr") or title
                notify(f"Market Sentinel : {company_name} ({ticker})", titre_fr,
                       ticker=ticker, kind="executive", title=title, company_name=company_name,
                       source=it.get("source"), url=direct_url(it.get("url")), image_url=logo_url(ticker),
                       published=it.get("published"))
            except Exception:
                log.exception("notification dirigeant échouée")


def _check_company_news(companies):
    """Alerte sur les actualités d'entreprise réellement collectées (`news:{ticker}`, déjà alimenté à
    chaque cycle par _collect_catalysts — aucun appel réseau supplémentaire ici) — Module 1/5 : jusqu'ici,
    ce cache ne servait qu'au tableau de bord et à l'enrichissement d'une alerte de mouvement de prix déjà
    déclenchée ; il ne déclenchait jamais lui-même d'alerte, alors qu'il contient les vraies actualités
    d'entreprise (résultats, contrats, produits...) — la source la plus pertinente pour ce que le cahier
    des charges demande, bien plus que la seule recherche par nom de dirigeant (voir _check_executives).

    Même filtre de pertinence, même dédup par événement et même résolution d'URL directe que
    _check_executives (voir son docstring) ; kind="company_news" plutôt que "executive" pour l'historique
    et le scoring (alerts/importance.py)."""
    from alerts import dedup
    from alerts.importance import SEUIL_SURVEILLER, score_for
    from collectors.logos import logo_url
    from collectors.news import direct_url
    for c in companies:
        t, name = c["ticker"], c["name"]
        seen_key = f"companynews:{t}:seen"
        items = db.kv_get(f"news:{t}")[0] or []
        if not items:
            log.info("actualités %s : cache news:%s vide (pas encore collecté par _collect_catalysts)", t, t)
            continue
        seen = set((db.kv_get(seen_key)[0] or {}).get("urls", []))
        new_items = [it for it in items if it.get("url") not in seen]
        all_urls = list(seen | {it["url"] for it in items})[-100:]
        db.kv_set(seen_key, {"urls": all_urls})
        log.info("actualités %s : %d actualité·s en cache, %d nouvelle·s depuis le dernier passage",
                  t, len(items), len(new_items))
        if not seen:
            log.info("actualités %s : première exécution, référence enregistrée sans alerte", t)
            continue
        if not new_items:
            continue
        for it in new_items[:5]:
            title = it.get("title")
            try:
                score, reason = score_for("company_news", title=title, ticker=t, company_name=name,
                                           source=it.get("source"))
                if score < SEUIL_SURVEILLER:
                    log.info("actualité %s rejetée (score %d/100, seuil %d) : %s — %s",
                              t, score, SEUIL_SURVEILLER, title, reason)
                    continue  # pas un contenu d'affaires identifiable (Module 4/5) : ni résolution d'URL ni envoi
                if not dedup.is_new_event(t, title, db.kv_get, db.kv_set):
                    log.info("actualité %s écartée (doublon d'un événement déjà alerté) : %s", t, title)
                    continue
                log.info("actualité %s retenue (score %d/100) : %s — %s", t, score, title, reason)
                titre_fr = it.get("title_fr") or title
                notify(f"Market Sentinel : {name} ({t})", titre_fr, ticker=t, kind="company_news",
                       title=title, company_name=name, source=it.get("source"),
                       url=direct_url(it.get("url")), image_url=logo_url(t), published=it.get("published"))
            except Exception:
                log.exception("notification actualité d'entreprise pour %s échouée", t)


MISSED_MOVES_DAYS = 90  # fenêtre d'audit par défaut (Module K) : assez pour repérer un vrai trou, pas
                          # assez pour que l'historique des alertes (purgé au-delà de 1000 lignes) manque


def find_missed_price_moves(days=MISSED_MOVES_DAYS):
    """Audit, pas une recommandation : mouvements de clôture à clôture qui auraient dû déclencher une
    alerte de prix (seuil propre à chaque titre, Module H) sur les `days` derniers jours, mais pour
    lesquels aucune alerte de ce type n'apparaît dans l'historique (Module G) à cette date. Objectif :
    détecter après coup un trou (cycle en panne, données manquantes, alerting pas encore activé à
    l'époque...) plutôt que de supposer silencieusement que l'absence d'alerte veut dire absence de
    mouvement. Ne couvre que les clôtures : un mouvement intrajournalier qui s'est déjà résorbé avant la
    clôture ne laisse pas de prix de clôture anormal à auditer ici, mais aurait été vu en direct sur le
    moment (voir _check_intraday_moves) — ce n'est donc pas un trou.

    Chaque mouvement manqué expose sa raison (le calcul lui-même), pour que la conclusion reste
    vérifiable plutôt qu'une simple liste de tickers."""
    companies = load_watchlist(all_users=True)
    cutoff = date.today() - timedelta(days=days)
    closes = db.load_closes([c["ticker"] for c in companies])
    out = []
    for c in companies:
        t = c["ticker"]
        series = closes.get(t) or []
        if len(series) < 2:
            continue
        threshold = c.get("alert_pct", DEFAULT_ALERT_PCT)
        try:
            alerted_days = {a["created_at"].date() for a in db.recent_alerts(limit=500, ticker=t)
                            if a["kind"] in ("daily_move", "intraday_move")}
        except Exception:
            log.exception("lecture de l'historique des alertes pour %s échouée — audit ignoré pour ce titre", t)
            continue
        for (d_prev, p_prev), (d, p) in zip(series, series[1:]):
            if d < cutoff or not p_prev:
                continue
            pct = (p - p_prev) / p_prev * 100
            if abs(pct) < threshold or d in alerted_days:
                continue
            out.append({"ticker": t, "date": d.isoformat(), "pct": round(pct, 1), "threshold": threshold,
                        "reason": f"variation de {pct:+.1f} % (seuil {threshold} %) sans alerte correspondante dans l'historique"})
    out.sort(key=lambda r: r["date"], reverse=True)
    return out


def _age(key):
    """Âge en secondes d'une entrée kv (None si absente)."""
    data, updated = db.kv_get(key)
    return None if data is None or not updated else (datetime.now(timezone.utc) - updated).total_seconds()


def _collect_catalysts(companies, force):
    for c in companies:
        t = c["ticker"]
        try:
            a = _age(f"news:{t}")
            if force or a is None or a > NEWS_TTL:
                news, _ = collect_news(t, c["name"])
                if news:
                    db.kv_set(f"news:{t}", news)
            a = _age(f"events:{t}")
            if force or a is None or a > EVENTS_TTL:
                ev, _ = collect_events(t)
                if ev["earnings_dates"] or ev["analyst"] or ev["target"]:
                    db.kv_set(f"events:{t}", ev)
        except Exception as e:
            log.warning("catalyseurs %s : %s", t, e)


def _refresh_sectors():
    a = _age("sectors:latest")
    if a is not None and a < SECTOR_REFRESH_H * 3600:
        return
    data, errors = collect_sectors()
    if data["sectors"]:
        data["updated"] = datetime.now(timezone.utc).isoformat()
        data["errors"] = errors[:5]
        db.kv_set("sectors:latest", data)


def _refresh_investors():
    if not contact_email():
        state["investor_errors"]["_all"] = "no_email"
        return
    state["investor_errors"].pop("_all", None)
    for inv in load_investors():
        a = _age(f"inv:{inv['slug']}")
        if a is not None and a < INVESTOR_REFRESH_H * 3600:
            continue
        if health.is_paused("sec"):
            break
        try:
            db.kv_set(f"inv:{inv['slug']}", refresh_investor(inv))
            state["investor_errors"].pop(inv["slug"], None)
        except Exception as e:
            log.warning("13F %s : %s", inv["slug"], e)
            state["investor_errors"][inv["slug"]] = str(e)


def run_cycle(force=False, light=False):
    if not _lock.acquire(blocking=False):
        return
    state["running"] = True
    try:
        companies = load_watchlist(all_users=True)
        tickers = [c["ticker"] for c in companies]
        state["phase"] = "cours"
        collect_prices(tickers)
        closes = _load_closes(tickers)
        _record_recommendations(closes)
        _check_price_moves(companies, closes)
        state["last_run"] = datetime.now(timezone.utc).isoformat()  # les cours sont déjà consultables
        db.kv_set("state:last_run", state["last_run"])
        steps = [("taux de change", lambda: fx.latest_rates()),
                 ("vérification des cours", lambda: _verify_watchlist(companies)),
                 ("cours en direct (alertes)", lambda: _check_intraday_moves(companies)),
                 ("actualités et analystes", lambda: _collect_catalysts(companies, force)),
                 ("actualités d'entreprise (alertes)", lambda: _check_company_news(companies)),
                 ("communiqués officiels", _check_official_feeds),
                 ("dirigeants", lambda: _check_executives(companies))]
        if not light:
            steps += [("secteurs", _refresh_sectors), ("investisseurs (13F)", _refresh_investors)]
        for phase, fn in steps:
            state["phase"] = phase
            try:
                fn()
            except Exception:
                log.exception("étape %s échouée", phase)
    except Exception:
        log.exception("cycle échoué")
    finally:
        state["running"] = False
        state["phase"] = None
        _lock.release()


# ---------- mise à jour automatique et progressive ----------
# Chaque tick traite UNE seule unité de travail (un lot de cours, l'actualité d'une entreprise, un fonds 13F…), la plus en
# retard par rapport à sa durée de validité. Si une unité est lente, on attend proportionnellement plus avant la suivante :
# la charge reste basse même avec une longue liste de suivi.
PRICE_TTL_S = int(os.getenv("PRICE_REFRESH_MIN", "60")) * 60
RETRY_S = 120  # une unité déjà tentée n'est pas retentée avant 2 min (évite de marteler une source qui ne renvoie rien)
FX_TTL_S = 3 * 3600
TICK_S = int(os.getenv("REFRESH_TICK_S", "20"))
IDLE_S = 60
PRICE_BATCH = 12  # au-delà, les cours sont rafraîchis par lots (les plus anciens d'abord)
INTRADAY_TTL_S = 10 * 60        # cours en direct pour les alertes de seuil : aussi souvent que le planificateur le permet
OFFICIAL_FEEDS_TTL_S = 15 * 60  # Fed/BCE : peu de publications, mais on veut les voir vite
EXEC_TTL_S = 2 * 3600           # dirigeants : recherche d'actualités, pas la peine de la relancer plus souvent
COMPANY_NEWS_TTL_S = 30 * 60    # actualités d'entreprise : lit un cache déjà alimenté (_collect_catalysts),
                                 # pas d'appel réseau propre — peut tourner plus souvent que les dirigeants
_sched = {"last_unit": None, "last_at": None, "due": 0, "next_at": None}
_tried = {}


def _price_age(t):
    at = (db.kv_get(f"pricesrc:{t}")[0] or {}).get("at")
    return None if not at else (datetime.now(timezone.utc) - datetime.fromisoformat(at)).total_seconds()


def _units():
    """Travaux en retard : liste de (retard relatif, libellé, source, fonction). Retard relatif = âge / durée de validité."""
    units = []
    companies = load_watchlist(all_users=True)
    tickers = [c["ticker"] for c in companies]
    ratio = lambda age, ttl: 99.0 if age is None else age / ttl
    # cours : lot des plus anciens
    ages = sorted(((ratio(_price_age(t), PRICE_TTL_S), t) for t in tickers), reverse=True)
    stale = [t for r, t in ages if r >= 1]
    if stale:  # Yahoo en pause (403/429) : les autres fournisseurs prennent le relais dans collect_prices
        batch = stale[:PRICE_BATCH]
        def do_prices(batch=batch):
            collect_prices(batch)
            closes = _load_closes(tickers)
            _record_recommendations(closes)
            _check_price_moves(companies, closes)
            state["last_run"] = datetime.now(timezone.utc).isoformat()
            db.kv_set("state:last_run", state["last_run"])
        units.append((ages[0][0] + 0.5, "cours", None, do_prices, "prices"))
    fxa = _age("fx:latest")
    if fxa is None or fxa > FX_TTL_S:
        units.append((ratio(fxa, FX_TTL_S), "taux de change", "ecb", lambda: fx.latest_rates(max_age_h=0), "fx"))
    for c in companies:
        t = c["ticker"]
        a = _age(f"fetched:{t}")
        if a is None or a >= COMPANY_TTL:  # fiche complète : la plupart des clics deviennent instantanés
            units.append((ratio(a, COMPANY_TTL), "fiches des entreprises", None, lambda c=c: ensure_company(c["ticker"], hint_name=c["name"]), f"company:{t}"))
    latest = db.kv_get("sectors:latest")[0] or {}
    for sec in latest.get("sectors", [])[: load_sector_config().get("top_count", 5)]:  # classements détaillés des meilleurs secteurs, un par un
        a = _age(f"sector:{sec['id']}")
        if a is None or a >= SECTOR_REFRESH_H * 3600:
            units.append((ratio(a, SECTOR_REFRESH_H * 3600), "secteurs", "yahoo", lambda sid=sec["id"]: _compute_sector(sid), f"sector:{sec['id']}"))
    a = _age("sectors:latest")
    if a is None or a >= SECTOR_REFRESH_H * 3600:
        units.append((ratio(a, SECTOR_REFRESH_H * 3600), "secteurs", "yahoo", _refresh_sectors, "sectors"))
    if contact_email() and not health.is_paused("sec"):
        for inv in load_investors():
            a = _age(f"inv:{inv['slug']}")
            if a is None or a >= INVESTOR_REFRESH_H * 3600:
                units.append((ratio(a, INVESTOR_REFRESH_H * 3600), "investisseurs (13F)", "sec", lambda inv=inv: _refresh_one_investor(inv), f"inv:{inv['slug']}"))
    elif not contact_email():
        state["investor_errors"]["_all"] = "no_email"
    a = _age("intraday:checked")
    if a is None or a >= INTRADAY_TTL_S:
        def do_intraday():
            _check_intraday_moves(companies)
            db.kv_set("intraday:checked", True)
        units.append((ratio(a, INTRADAY_TTL_S), "cours en direct (alertes)", "yahoo", do_intraday, "intraday"))
    a = _age("officialfeeds:checked")
    if a is None or a >= OFFICIAL_FEEDS_TTL_S:
        def do_official():
            _check_official_feeds()
            db.kv_set("officialfeeds:checked", True)
        units.append((ratio(a, OFFICIAL_FEEDS_TTL_S), "communiqués officiels", None, do_official, "officialfeeds"))
    a = _age("companynews:checked")
    if a is None or a >= COMPANY_NEWS_TTL_S:
        def do_company_news(companies=companies):
            _check_company_news(companies)
            db.kv_set("companynews:checked", True)
        units.append((ratio(a, COMPANY_NEWS_TTL_S), "actualités d'entreprise (alertes)", None, do_company_news, "companynews"))
    from collectors.executives import load_config as load_executives
    if load_executives():  # inutile de planifier une unité qui ne ferait jamais rien si le fichier est vide/absent
        a = _age("executives:checked")
        if a is None or a >= EXEC_TTL_S:
            def do_exec(companies=companies):
                _check_executives(companies)
                db.kv_set("executives:checked", True)
            units.append((ratio(a, EXEC_TTL_S), "dirigeants", None, do_exec, "executives"))
    now = time.time()
    units = [u for u in units if not (u[2] and health.is_paused(u[2])) and now - _tried.get(u[4], 0) > RETRY_S * (0.02 if u[0] >= 50 else 1)]
    _sched["due"] = len(units)
    bonus = {"prices": 1.0, "fx": 0.9, "sectors": 0.8, "company": 0.2, "inv": -0.1, "sector": 0.0}  # à retard égal : cours, taux, secteurs d'abord
    return sorted(units, key=lambda u: -(u[0] + bonus.get(u[4].split(":")[0], 0)))


def _refresh_one_investor(inv):
    state["investor_errors"].pop("_all", None)
    try:
        db.kv_set(f"inv:{inv['slug']}", refresh_investor(inv))
        state["investor_errors"].pop(inv["slug"], None)
    except Exception as e:
        log.warning("13F %s : %s", inv["slug"], e)
        state["investor_errors"][inv["slug"]] = str(e)
        db.kv_set(f"inv:{inv['slug']}", db.kv_get(f"inv:{inv['slug']}")[0])  # évite de réessayer en boucle serrée


def refresh_next():
    """Traite l'unité la plus en retard. Retourne la durée en secondes, ou None s'il n'y a rien à faire (ou un cycle en cours)."""
    if state["running"] or not _lock.acquire(blocking=False):
        return None
    t0 = time.time()
    try:
        units = _units()
        if not units:
            return None
        top, phase, _, fn, uid = units[0]
        _tried[uid] = time.time()
        _sched["backlog"] = top >= 50  # données jamais collectées : on rattrape vite au démarrage
        state.update(running=True, phase=phase)
        try:
            fn()
        except Exception:
            log.exception("mise à jour « %s » échouée", phase)
        _sched.update(last_unit=phase, last_at=datetime.now(timezone.utc).isoformat())
        state["last_run"] = _sched["last_at"]  # activité de mise à jour visible dans l'interface
        db.kv_set("state:last_run", state["last_run"])
        return time.time() - t0
    finally:
        state["running"] = False
        state["phase"] = None
        _lock.release()


def run_budget(seconds=45):
    """Pour Cloud Run : enchaîne les unités en retard pendant au plus « seconds » secondes (déclenché par le
    planificateur). Chaque alerte déclenchée pendant ce passage envoie sa propre notification individuelle
    (Module 1 — voir alerts/notify.py::notify), jamais regroupée avec une autre."""
    end = time.time() + seconds
    n = 0
    while time.time() < end and refresh_next() is not None:
        n += 1
    return n


def _loop():
    time.sleep(2)
    while True:
        try:
            d = refresh_next()
        except Exception:
            log.exception("planificateur")
            d = None
        # rien à faire : on vérifie de nouveau dans une minute ; sinon pause proportionnelle au temps de travail (charge ≤ ~33 %)
        wait = IDLE_S if d is None else max(2 if _sched.get("backlog") else TICK_S, 2 * d)
        _sched["next_at"] = (datetime.now(timezone.utc) + timedelta(seconds=wait)).isoformat()
        time.sleep(wait)


def start_background():
    threading.Thread(target=_loop, daemon=True).start()


def status():
    last = state["last_run"] or db.kv_get("state:last_run")[0]  # partagé entre instances / redémarrages
    return {**state, "last_run": last, "serverless": SERVERLESS, "pending": _sched["due"], "next_at": _sched["next_at"], "last_unit": _sched["last_unit"], "price_ttl_min": PRICE_TTL_S // 60, "auto": not SERVERLESS, "problems": health.problems(), "sec_configured": bool(contact_email())}


# ---------- données à la demande (recherche d'une entreprise) ----------
REFRESH_PARTS = ("prices", "fund", "news", "events", "sec")
REFRESH_MIN_GAP = 60  # secondes : un même morceau n'est pas re-téléchargé plus d'une fois par minute (clics répétés)


def ensure_company(ticker, force=False, hint_name=None, essential=False, only=None):
    """Collecte (ou rafraîchit si périmé) tout ce qu'il faut sur un ticker. Retourne la liste des problèmes rencontrés.
    only : ne traiter que ces morceaux (voir REFRESH_PARTS) — le bouton « Actualiser » les lance en parallèle, chacun
    dans sa propre requête, au lieu d'attendre toutes les sources les unes après les autres."""
    t = ticker.upper()
    meta = db.kv_get(f"fetched:{t}")[0] or {}
    now = time.time()
    want = lambda part: only is None or part in only
    stale = lambda k, ttl: (force and now - meta.get(k, 0) > (REFRESH_MIN_GAP if only else 0)) or now - meta.get(k, 0) > ttl
    errors, before = [], dict(meta)
    have = db.load_closes([t])[t]
    if want("prices") and (stale("prices", PRICE_TTL) or len(have) < 60 or not meta.get("p10")):
        r = collect_prices([t], "10y")  # 10 ans d'historique pour les graphiques et la progression annuelle
        errors += r["errors"]
        if r["written"]:
            meta["prices"] = now
            meta["p10"] = now
    fund, _ = db.kv_get(f"fund:{t}")
    if want("fund") and (stale("fund", FUND_TTL) or not fund):
        data, errs = collect_fundamentals(t)
        errors += errs
        if data["info"]:
            db.kv_set(f"fund:{t}", data)
            fund, meta["fund"] = data, now
    name = ((fund or {}).get("info") or {}).get("longName") or hint_name or t
    if essential:  # affichage rapide : cours + fiche société ; le reste est complété en arrière-plan
        db.kv_set(f"fetched:{t}", meta)
        return errors
    if want("news") and stale("news", NEWS_TTL):
        news, errs = collect_news(t, name)
        errors += errs
        if news:
            db.kv_set(f"news:{t}", news)
            meta["news"] = now
    if want("events") and stale("events", EVENTS_TTL):
        ev, errs = collect_events(t)
        errors += errs
        if ev["earnings_dates"] or ev["analyst"] or ev["target"]:
            db.kv_set(f"events:{t}", ev)
            meta["events"] = now
    # recoupement du cours avec d'autres fournisseurs
    series = db.load_closes([t])[t]
    if only is None and series and stale("verify", VERIFY_TTL):
        base = (db.kv_get(f"pricesrc:{t}")[0] or {}).get("source", "yahoo")
        try:
            v = multi.verify_close(t, series, base_source=base)
            if v:
                db.kv_set(f"verify:{t}", v)
                meta["verify"] = now
        except Exception as e:
            errors.append(f"vérification des cours : {e}")
    # données officielles de la SEC (comptes annuels, documents déposés, transactions des dirigeants)
    if want("sec") and contact_email() and any(stale(k, ttl) for k, ttl in (("secfacts", SECFACTS_TTL), ("secfilings", SECFILINGS_TTL), ("secinsiders", SECINSIDERS_TTL))):
        try:
            cik = sec_facts.cik_for(t)
        except Exception as e:
            cik = None
            errors.append(f"SEC (identifiant) : {e}")
        if cik:
            for key, ttl, ckey, fn in (("secfacts", SECFACTS_TTL, "secfacts", lambda: sec_facts.annual_metrics(cik)),
                                       ("secfilings", SECFILINGS_TTL, "secfilings", lambda: sec_facts.recent_filings(cik)),
                                       ("secinsiders", SECINSIDERS_TTL, "secinsiders", lambda: sec_facts.insider_trades(cik))):
                if stale(key, ttl):
                    try:
                        val = fn()
                        db.kv_set(f"{ckey}:{t}", val if val is not None else {"none": True})
                        meta[key] = now
                        if key == "secfilings" and val:
                            _check_earnings_filing(t, val)
                    except Exception as e:
                        errors.append(f"SEC ({key}) : {e}")
    if only is not None:  # requêtes parallèles : on ne réécrit que ses propres dates, pas celles des autres morceaux
        changed = {k: v for k, v in meta.items() if before.get(k) != v}
        if changed:
            db.kv_set(f"fetched:{t}", {**(db.kv_get_fresh(f"fetched:{t}") or {}), **changed})
        return errors
    db.kv_set(f"fetched:{t}", meta)
    return errors


def refresh_company_part(ticker, part):
    """Bouton « Actualiser » d'une fiche : rafraîchit un seul morceau (cours, fiche société, actualités, analystes, SEC)."""
    t = ticker.upper()
    if not TICKER_RE.match(t) or part not in REFRESH_PARTS:
        return None
    wl = next((c for c in load_watchlist() if c["ticker"] == t), None)
    return {"ok": True, "part": part, "errors": ensure_company(t, force=True, hint_name=wl["name"] if wl else None, only={part})}


def _verify_watchlist(companies):
    """Vérification gratuite (Stooq, sans clé) de la dernière clôture de chaque action suivie."""
    for c in companies:
        t = c["ticker"]
        a = _age(f"verify:{t}")
        if a is not None and a < VERIFY_TTL:
            continue
        series = db.load_closes([t])[t]
        base = (db.kv_get(f"pricesrc:{t}")[0] or {}).get("source", "yahoo")
        try:
            v = multi.verify_close(t, series, base_source=base, max_sources=1, only=("stooq",))
            if v:
                db.kv_set(f"verify:{t}", v)
        except Exception as e:
            log.info("vérification %s : %s", t, e)


def do_search(q):
    results, errors = search(q)
    followed = {c["ticker"] for c in load_watchlist()}
    return {"results": [{**r, "followed": r["symbol"] in followed} for r in results], "errors": errors,
            "problems": health.problems()}


# ---------- vues ----------
def _pct(a, b):
    return round((a / b - 1) * 100, 2) if b else None


def _movement_note(change_1d, news, threshold=1.5):
    """Actualités récentes qui pourraient expliquer le mouvement du jour (au mieux : pas une certitude causale).
    None si le mouvement est trop faible pour qu'on cherche une explication, ou si rien de récent n'est disponible."""
    if change_1d is None or abs(change_1d) < threshold or not news:
        return None
    cutoff = datetime.now(timezone.utc) - timedelta(days=2)
    recent = []
    for n in news:
        try:
            pub = datetime.fromisoformat(n["published"])
        except Exception:
            continue
        if pub >= cutoff:
            recent.append(n)
    if not recent:
        return None
    want = 1 if change_1d >= 0 else -1
    recent.sort(key=lambda n: n["published"], reverse=True)  # les plus récentes d'abord, à égalité de pertinence
    recent.sort(key=lambda n: 0 if (n.get("sentiment") or 0) * want > 0 else 1)  # puis celles allant dans le sens du mouvement
    matched = any((n.get("sentiment") or 0) * want > 0 for n in recent[:2])
    return {"direction": "up" if change_1d >= 0 else "down", "matched": matched, "items": recent[:2]}


def build_dashboard():
    companies = load_watchlist()
    closes = _load_closes([c["ticker"] for c in companies])
    reco_by = db.latest_recos()
    rows = []
    for c in companies:
        t = c["ticker"]
        series = [p for _, p in closes.get(t, [])]
        row = {"ticker": t, "name": c["name"], "base": c["base"], "currency": fx.asset_currency(t), "price": None, "score": None, "label": None, "reco": None, "domain": _domain(t)}
        if series:
            price = series[-1]
            row.update(price=round(price, 2), change_1d=_pct(price, series[-2]) if len(series) > 1 else None,
                       change_1m=_pct(price, series[-22]) if len(series) > 22 else None,
                       spark=[round(x, 2) for x in series[-60:]])
            res = compute_score(series)
            if res:
                row.update(score=res["score"], label=res["label"], components=res["components"])
            r = reco_by.get(t)
            if r:
                row["reco"] = {"label": r[0], "score": r[1], "price_at": r[2], "since": r[3].isoformat(),
                               "perf": _pct(price, r[2])}
        rows.append(row)
    scored = [r for r in rows if r["label"]]
    counts = {k: sum(1 for r in scored if r["label"] == k) for k in ("BUY", "HOLD", "WATCH", "AVOID/REASSESS")}
    buys = [r["reco"]["perf"] for r in rows if r["reco"] and r["reco"]["label"] == "BUY" and r["reco"]["perf"] is not None]
    return {"rows": sorted(rows, key=lambda r: (r["score"] is None, -(r["score"] or 0))), "counts": counts,
            "avg_buy_perf": round(sum(buys) / len(buys), 2) if buys else None, "status": status(),
            "interval_min": INTERVAL_S // 60}


def _investor_views(companies, closes):
    views = []
    for inv in load_investors():
        data, _ = db.kv_get(f"inv:{inv['slug']}")
        v = build_view(inv, data, companies, closes)
        err = state["investor_errors"].get(inv["slug"]) or state["investor_errors"].get("_all")
        v["error"] = L("contact_email manquant : renseigne ton email (onglet Profil) pour activer les données SEC",
                       "contact_email missing: enter your email (Profile tab) to enable SEC data") if err == "no_email" else err
        views.append(v)
    return views


def _kv_by_ticker(prefix, tickers):
    return {t: db.kv_get(f"{prefix}:{t}")[0] for t in tickers}


def _domain_from_site(site):
    """Extrait un domaine nu ('apple.com') d'une URL de site web, pour aller chercher un logo."""
    if not site:
        return None
    host = site.split("//", 1)[-1].split("/", 1)[0].lower()
    return host[4:] if host.startswith("www.") else host


def _domain(ticker):
    """Domaine du site officiel d'une entreprise, à partir des données déjà mises en cache (aucun appel réseau ici)."""
    fund, _ = db.kv_get(f"fund:{ticker}")
    return _domain_from_site((fund or {}).get("info", {}).get("website"))


_tr_running = set()


def _news_fr(news, ticker=None):
    """Titres en français quand l'interface est en français (traduction faite à la collecte, ou en arrière-plan pour les anciennes)."""
    if not news or lang.lang() != "fr":
        return news
    if ticker and any(n.get("title_fr") is None for n in news) and ticker not in _tr_running:
        _tr_running.add(ticker)

        def run(t=ticker):
            try:
                from collectors.translate import add_french_titles
                cur = db.kv_get(f"news:{t}")[0] or []
                if cur:
                    db.kv_set(f"news:{t}", add_french_titles(cur))
            except Exception:
                pass
            finally:
                _tr_running.discard(t)
        threading.Thread(target=run, daemon=True).start()
    return [{**n, "title": n.get("title_fr") or n["title"], "title_orig": n["title"]} for n in news]


def build_opportunities():
    companies = load_watchlist()
    tickers = [c["ticker"] for c in companies]
    closes = _load_closes(tickers)
    news, events = _kv_by_ticker("news", tickers), _kv_by_ticker("events", tickers)
    views = _investor_views(companies, closes)
    items = []
    for c in companies:
        t = c["ticker"]
        series = [p for _, p in closes.get(t, [])]
        if not series:
            continue
        cur = fx.asset_currency(t)
        a = analyze(t, series, _news_fr(news.get(t), t), events.get(t), signals_for(t, views), currency=cur)
        items.append({"ticker": t, "name": c["name"], "price": round(series[-1], 2), "currency": cur, "domain": _domain(t), **a})
    items.sort(key=lambda x: -x["total"])
    return {"items": items, "upcoming": upcoming(companies, events), "status": status()}


def _consensus(inv_data):
    """Achats / ventes / positions partagés par plusieurs des grands fonds suivis."""
    buys, sells, held = {}, {}, {}
    for inv, data in inv_data:
        short = inv["name"].split(" (")[0]
        for ch in data.get("changes", []):
            if ch["pc"]:
                continue
            bucket = buys if ch["kind"] in ("new", "add") else sells
            e = bucket.setdefault(ch["cusip"], {"issuer": ch["issuer"], "funds": [], "flow": 0.0})
            e["funds"].append(short)
            e["flow"] += ch["flow"]
        for h in data.get("holdings", [])[:40]:
            if h["pc"]:
                continue
            e = held.setdefault(h["cusip"], {"issuer": h["issuer"], "funds": [], "value": 0.0})
            e["funds"].append(short)
            e["value"] += h["value"]
    top = lambda d, key: [{**v, "n": len(v["funds"])} for v in sorted(d.values(), key=lambda v: (-len(v["funds"]), -abs(v[key])))[:8] if len(v["funds"]) >= 2]
    return {"buys": top(buys, "flow"), "sells": top(sells, "flow"), "held": top(held, "value")}


def build_investors():
    companies = load_watchlist()
    closes = _load_closes([c["ticker"] for c in companies])
    views = _investor_views(companies, closes)
    matrix = {}
    for v in views:
        for h in v.get("hits", []):
            matrix.setdefault(h["ticker"], []).append({"investor": v["name"].split(" (")[0], "kind": h["kind"], "pct": h["pct"],
                                                        "weight": h["weight"], "period_label": v["period_label"]})
    names = {c["ticker"]: c["name"] for c in companies}
    raw = [(inv, d) for inv in load_investors() for d in [db.kv_get(f"inv:{inv['slug']}")[0]] if d]
    return {"investors": views, "matrix": [{"ticker": t, "name": names[t], "holders": h} for t, h in matrix.items()],
            "consensus": _consensus(raw), "status": status()}


def _align_labels(rows):
    """Même niveau (BUY, HOLD…) dans « Marché » et sur la fiche : quand l'historique d'une action est déjà en base
    (c'est celui qu'utilise sa fiche) et qu'il est récent, le score est recalculé à partir de lui plutôt que du
    cliché pris au moment du classement du secteur, qui peut dater de quelques heures."""
    tickers = [r["ticker"] for r in rows if r.get("ticker")]
    if not tickers:
        return rows
    try:
        stored = _load_closes(tickers)
    except Exception:
        return rows
    recent = datetime.now(timezone.utc).date() - timedelta(days=5)
    for r in rows:
        pts = stored.get(r.get("ticker")) or []
        if len(pts) >= 60 and pts[-1][0] >= recent:
            res = compute_score([c for _, c in pts])
            if res:
                r["score"], r["label"] = res["score"], res["label"]
    return rows


def build_sectors():
    data, updated = db.kv_get("sectors:latest")
    followed = {c["ticker"] for c in load_watchlist()}
    if not data:
        return {"available": False, "status": status()}
    names = names_by_ticker()
    _align_labels([b for s in data["sectors"] for b in s.get("best", [])])
    for s in data["sectors"]:
        for b in s.get("best", []):
            b["name"] = b.get("name") or names.get(b["ticker"], "")
            b["followed"] = b["ticker"] in followed
            b["reasons"] = reasons_for(b, has_sector_ref=s.get("r3m") is not None)
            b["domain"] = _domain(b["ticker"])
        s["state_label"], s["trend_label"] = state_label(s.get("state")), trend_label(s.get("trend"))
        if lang.lang() == "en":
            s["name"], s["kind"] = s.get("name_en", s["name"]), s.get("kind_en", s.get("kind"))
    return {"available": True, "updated": data.get("updated"), "errors": data.get("errors", []), **{k: data[k] for k in ("sectors", "chart", "benchmark", "n_hot")},
            "status": status()}


def _sector_view(rec):
    """Ajoute les textes localisés (non stockés) à une fiche de secteur enregistrée."""
    followed = {c["ticker"] for c in load_watchlist()}
    names = names_by_ticker()
    rec["companies"] = [dict(b) for b in rec.get("companies", [])]  # copie : le classement en cache n'est pas modifié
    _align_labels(rec["companies"])
    for b in rec.get("companies", []):
        b["name"] = b.get("name") or names.get(b["ticker"], "")
        b["followed"] = b["ticker"] in followed
        b["reasons"] = reasons_for(b, has_sector_ref=rec.get("r3m") is not None)
        b["domain"] = _domain(b["ticker"])
    rec["state_label"], rec["trend_label"] = state_label(rec.get("state")), trend_label(rec.get("trend"))
    sec = sector_by_id(rec["id"]) or {}
    if lang.lang() == "en":
        rec["name"], rec["kind"] = sec.get("name_en", rec["name"]), sec.get("kind_en", rec.get("kind"))
    return rec


def _compute_sector(sid):
    """Classe les entreprises d'un secteur (composition de l'ETF + liste de secours) et l'enregistre."""
    sec = sector_by_id(sid)
    latest = (db.kv_get("sectors:latest")[0] or {}).get("sectors", [])
    meta = next((x for x in latest if x["id"] == sid), {})
    cfg = load_sector_config()
    companies, errors = rank_sector(sec, meta.get("r3m"), limit=cfg.get("universe_size", 24), top=24)
    rec = {"id": sid, "name": sec["name"], "etf": sec["etf"], "kind": sec.get("kind", ""), "state": meta.get("state"), "trend": meta.get("trend"),
           "r3m": meta.get("r3m"), "companies": companies, "errors": errors[:5]}
    if companies:
        db.kv_set(f"sector:{sid}", rec)
    return rec


_sector_bg = set()


def _sector_bg_refresh(sid):
    if sid in _sector_bg:
        return
    _sector_bg.add(sid)

    def run():
        try:
            _compute_sector(sid)
        except Exception:
            log.exception("classement du secteur %s", sid)
        finally:
            _sector_bg.discard(sid)
    threading.Thread(target=run, daemon=True).start()


def build_sector(sid, force=False):
    """Classement détaillé d'un secteur. Réponse immédiate : dernier classement connu (rafraîchi en arrière-plan s'il est ancien),
    sinon le top déjà calculé par la collecte (marqué « partial »), le classement complet arrivant peu après."""
    sec = sector_by_id(sid)
    if not sec:
        return None
    rec, updated = db.kv_get(f"sector:{sid}")
    if force:
        rec = _compute_sector(sid)
    elif rec:
        if not updated or (datetime.now(timezone.utc) - updated).total_seconds() > SECTOR_REFRESH_H * 3600:
            _sector_bg_refresh(sid)
    else:
        latest = (db.kv_get("sectors:latest")[0] or {}).get("sectors", [])
        meta = next((x for x in latest if x["id"] == sid), None)
        if meta and meta.get("best"):
            _sector_bg_refresh(sid)
            rec = {"id": sid, "name": sec["name"], "etf": sec["etf"], "kind": sec.get("kind", ""), "state": meta.get("state"), "trend": meta.get("trend"),
                   "r3m": meta.get("r3m"), "companies": [dict(b) for b in meta["best"]], "errors": [], "partial": True}
        else:
            rec = _compute_sector(sid)
    rec = dict(rec)
    rec["partial"] = bool(rec.get("partial")) and sid in _sector_bg
    return _sector_view(rec)


def _best_now(limit=8):
    """Meilleures actions du moment : tête de classement des secteurs en hausse."""
    data = db.kv_get("sectors:latest")[0] or {}
    names = names_by_ticker()
    out, seen = [], set()
    for sec in data.get("sectors", []):
        if not sec.get("hot"):
            continue
        for b in sec.get("best", []):
            if b["ticker"] not in seen:
                seen.add(b["ticker"])
                out.append({"ticker": b["ticker"], "name": b.get("name") or names.get(b["ticker"], ""), "sector": sec.get("name_en", sec["name"]) if lang.lang() == "en" else sec["name"],
                            "label": b["label"], "rank": b["rank"]})
    out.sort(key=lambda x: -x["rank"])
    return _align_labels(out[:limit])


def build_compare_best(ticker, chosen=None):
    """Compare une action aux meilleures actions du moment (base 100), avec les chiffres côte à côte."""
    t = ticker.upper()
    if not TICKER_RE.match(t):
        return {"found": False}
    ensure_company(t)
    own = _load_closes([t])[t]
    if len(own) < 30:
        return {"found": False, "ticker": t}
    cands = [c for c in _best_now(10) if c["ticker"] != t]
    chosen = [c for c in (chosen or [x["ticker"] for x in cands[:4]]) if c != t][:5]
    tickers = [t] + chosen
    have = _load_closes([x for x in tickers])
    missing = [x for x in chosen if len(have.get(x, [])) < 30]
    if missing:
        got, _ = download_closes(missing, "1y")
        have.update({k: v for k, v in got.items() if v})
    fund = db.kv_get(f"fund:{t}")[0] or {}
    wl = next((c for c in load_watchlist() if c["ticker"] == t), None)
    name = (fund.get("info") or {}).get("longName") or (wl or {}).get("name") or t
    names = {c["ticker"]: c["name"] for c in cands}
    rows, dates_set = [], set()
    for x in tickers:
        pts = have.get(x, [])
        m = stock_metrics([p for _, p in pts]) if pts else None
        if not m:
            continue
        dates_set.update(d for d, _ in pts[-252:])
        rows.append({"ticker": x, "name": name if x == t else names.get(x) or (names_by_ticker().get(x, "")), "base": x == t, **m,
                     "trend_label": __import__("analysis.compare", fromlist=["trend_label"]).trend_label(m["trend"])})
    dates = sorted(dates_set)
    series = {}
    for r in rows:
        by = dict(have[r["ticker"]])
        series[r["ticker"]] = [round(by[d], 2) if d in by else None for d in dates]
    base = next((r for r in rows if r["base"]), None)
    others = [r for r in rows if not r["base"]]
    summary = ""
    if base and others:
        avg3 = [r["r3m"] for r in others if r["r3m"] is not None]
        if base["r3m"] is not None and avg3:
            a = sum(avg3) / len(avg3)
            summary = L(f"{t} fait {base['r3m'] * 100:+.0f} % sur 3 mois contre {a * 100:+.0f} % en moyenne pour les meilleures actions du moment ({'devant' if base['r3m'] >= a else 'derrière'} : écart de {abs(base['r3m'] - a) * 100:.0f} pts). ",
                        f"{t} is {base['r3m'] * 100:+.0f}% over 3 months versus {a * 100:+.0f}% on average for today's best stocks ({'ahead' if base['r3m'] >= a else 'behind'} by {abs(base['r3m'] - a) * 100:.0f} pts). ")
        better = [r["ticker"] for r in others if r["score"] is not None and base["score"] is not None and r["score"] > base["score"]]
        summary += (L(f"{len(better)} des {len(others)} actions comparées ont un score technique plus élevé.", f"{len(better)} of the {len(others)} stocks compared have a higher technical score.") if better
                    else L("Aucune des actions comparées n'a un meilleur score technique.", "None of the compared stocks has a higher technical score."))
    return {"found": True, "ticker": t, "name": name, "summary": summary, "candidates": cands, "chosen": chosen, "dates": [d.isoformat() for d in dates],
            "series": series, "rows": rows}


_bg = set()
_quote_pool = ThreadPoolExecutor(max_workers=16)  # large marge : chaque cotation a maintenant son propre délai court (providers.py),
                                                   # mais on évite quand même qu'une source lente ne mette la file en attente


def _bg_refresh(t, hint=None):
    """Complète/rafraîchit une entreprise sans faire attendre l'utilisateur."""
    if t in _bg:
        return True
    _bg.add(t)

    def run():
        try:
            ensure_company(t, hint_name=hint)
        except Exception:
            log.exception("mise à jour en arrière-plan de %s", t)
        finally:
            _bg.discard(t)
    threading.Thread(target=run, daemon=True).start()
    return True


def build_company(ticker, force=False):
    t = ticker.upper()
    if not TICKER_RE.match(t):
        return None
    wl = next((c for c in load_watchlist() if c["ticker"] == t), None)
    hint = wl["name"] if wl else None
    have, fund_have = _load_closes([t])[t], db.kv_get(f"fund:{t}")[0]
    if force:
        errors, more = ensure_company(t, force=True, hint_name=hint), False
    elif len(have) >= 60 and fund_have:  # déjà connue : réponse immédiate, mise à jour éventuelle en arrière-plan
        errors, more = [], _bg_refresh(t, hint)
    else:  # nouvelle entreprise : l'essentiel d'abord, le reste ensuite
        errors, more = ensure_company(t, hint_name=hint, essential=True), _bg_refresh(t, hint)
    series = _load_closes([t])[t]
    if not series and not (db.kv_get(f"fund:{t}")[0]):
        return {"found": False, "ticker": t, "errors": errors, "problems": health.problems()}
    values = [p for _, p in series]
    fund = db.kv_get(f"fund:{t}")[0] or {"info": {}, "institutions": [], "insiders": {"buys": 0, "sells": 0, "recent": []}}
    info = fund["info"]
    if lang.lang() == "fr" and info.get("longBusinessSummary"):
        if fund.get("summary_fr"):
            info = {**info, "longBusinessSummary": fund["summary_fr"], "summary_translated": True}
        elif f"sum:{t}" not in _tr_running:
            _tr_running.add(f"sum:{t}")

            def run_sum(t=t):
                try:
                    from collectors.translate import to_french
                    f = db.kv_get(f"fund:{t}")[0]
                    if f and not f.get("summary_fr"):
                        tr = to_french(((f.get("info") or {}).get("longBusinessSummary") or "")[:4000], timeout=12)
                        if tr:
                            db.kv_set(f"fund:{t}", {**f, "summary_fr": tr})
                finally:
                    _tr_running.discard(f"sum:{t}")
            threading.Thread(target=run_sum, daemon=True).start()
    name = info.get("longName") or info.get("shortName") or (wl["name"] if wl else t)
    news, events = db.kv_get(f"news:{t}")[0] or [], db.kv_get(f"events:{t}")[0]
    news = _news_fr(news, t)
    company = {"ticker": t, "name": name, "match": (wl or {}).get("match", [])}
    views = _investor_views([company], {t: series})
    inv_sig = signals_for(t, views)
    holders = [{"investor": v["name"], "kind": h["kind"], "pct": h["pct"], "weight": h["weight"], "why": h["why"],
                "url": v["url"], "period_label": v["period_label"]}
               for v in views for h in v.get("hits", []) if h["ticker"] == t]
    res = compute_score(values)
    rnd = lambda arr: [None if x is None else round(x, 2) for x in arr]
    # courbe convertie avec les taux historiques de la BCE (le cours de chaque jour dans la devise choisie)
    target, ccy = lang.currency(), fx.asset_currency(t)
    factor = None
    if target != ccy and series:
        hist = fx.history_cached("GBP" if ccy == "GBp" else ccy, target, 2600)
        rates = stored_rates() or {}
        cur_rate = ((rates.get(target) / rates.get("GBP" if ccy == "GBp" else ccy)) if rates.get(target) and rates.get("GBP" if ccy == "GBp" else ccy) else None)
        if hist or cur_rate:
            factor = fx.series_rate(hist, [d.isoformat() for d, _ in series], cur_rate or 1.0) if hist else [cur_rate] * len(series)
            if ccy == "GBp":
                factor = [f / 100 for f in factor]
    conv = (lambda arr: [None if x is None else x * f for x, f in zip(arr, factor)]) if factor else (lambda arr: arr)
    # sources multiples : cours en direct recoupé, comptes SEC, documents et transactions officiels
    try:  # cours en direct : on n'attend pas plus de 2,5 s
        quotes, qerrs = _quote_pool.submit(multi.quote_all, t, 3).result(timeout=2.5)
    except (FutTimeout, Exception):
        quotes, qerrs = [], []
    consensus = multi.consensus_price(quotes)
    verify = db.kv_get(f"verify:{t}")[0]
    sec_metrics = db.kv_get(f"secfacts:{t}")[0]
    sec_metrics = None if not sec_metrics or sec_metrics.get("none") else sec_metrics
    filings = db.kv_get(f"secfilings:{t}")[0] or []
    insiders_sec = db.kv_get(f"secinsiders:{t}")[0]
    insiders_sec = None if not insiders_sec or insiders_sec.get("none") else insiders_sec
    cross = crosscheck_compare(info, sec_metrics)
    official = bool(filings or insiders_sec or sec_metrics)
    dq = data_quality(consensus, verify, cross, official)
    price_src = (db.kv_get(f"pricesrc:{t}")[0] or {}).get("source")
    off_signals = official_signals(insiders_sec, filings, datetime.now(timezone.utc).date())
    cur = info.get("currency") or fx.asset_currency(t)
    opp = analyze(t, values, news, events, inv_sig + off_signals, currency=cur) if values else None
    change_1d = _pct(values[-1], values[-2]) if len(values) > 1 else None
    return {
        "found": True, "ticker": t, "name": name, "loading_more": bool(more), "exchange": info.get("fullExchangeName") or info.get("exchange"),
        "sector": fr_terms.sector(info.get("sector")), "industry": fr_terms.industry(info.get("industry")), "currency": info.get("currency") or fx.asset_currency(t),
        "price": consensus["price"] if consensus else (round(values[-1], 2) if values else None), "close": round(values[-1], 2) if values else None,
        "price_live": bool(consensus and consensus["live"]), "info": info, "domain": _domain_from_site(info.get("website")),
        "in_watchlist": wl is not None, "removable": bool(wl and not wl["base"]),
        "score": res["score"] if res else None, "label": res["label"] if res else None,
        "components": res["components"] if res else None, "rating": explain(values) if values else None,
        "change_1d": change_1d, "movement": _movement_note(change_1d, news),
        "chart": {"dates": [d.isoformat() for d, _ in series], "close": rnd(conv(values)),
                  "currency": target if factor else ccy},
        "recos": [{"label": l, "score": sc, "price_at": p, "since": at.isoformat()} for l, sc, p, at in db.reco_history(t)],
        "assessment": assess(info, values, events, news, inv_sig) if values else None,
        "opportunity": opp,
        "news": news, "events": fr_terms.events(events), "institutions": fund["institutions"], "insiders": fr_terms.insiders(fund["insiders"]),
        "holders": holders, "errors": errors + qerrs[:2], "problems": health.problems(),
        "data_quality": {**dq, "consensus": consensus, "verify": verify, "crosscheck": cross, "price_source": price_src},
        "official": {"filings": filings, "insiders": ({**insiders_sec, "trades": [{**x, "role": fr_terms.position(x.get("role"))} for x in insiders_sec.get("trades") or []]}
                                                    if insiders_sec else insiders_sec), "facts": sec_metrics},
    }


# Toutes les périodes partagent le même « cours actuel » : le dernier échange connu (avant- et après-Bourse compris),
# tiré des points de 5 minutes des 5 derniers jours. « 1J » et « 1S » sont construits à partir de ces mêmes points ;
# pour « 1M » à « MAX » (cours quotidiens ou hebdomadaires), le dernier point est remplacé ou complété par ce cours
# actuel. Avant, chaque période avait sa propre source, sa propre mise en cache et sa propre conversion de devise :
# la fin de la courbe changeait d'une période à l'autre et ne correspondait pas au cours affiché en tête de fiche.
_CANDLE_RANGES = {"month": ("1mo", "1d"), "3m": ("3mo", "1d"), "6m": ("6mo", "1d"), "year": ("1y", "1d"), "max": ("max", "1wk")}
_CANDLE_TTL = {"month": 1800, "3m": 3600, "6m": 3600, "year": 3600, "max": 3600}  # historique (le dernier point, lui, est toujours à jour)
INTRA_TTL = 120  # secondes : points de 5 minutes gardés en mémoire, partagés entre la liste, la fiche et toutes les périodes
_intra = {}      # ticker -> (horodatage, lignes)


def _intraday_rows(t, ttl=INTRA_TTL):
    """Points de 5 minutes des 5 derniers jours (hors séance compris) : ([(horodatage, o, h, l, c)], erreur)."""
    e = _intra.get(t)
    if e and time.time() - e[0] < ttl:
        return e[1], None
    rows, err = download_ohlc(t, "5d", "5m", prepost=True)
    if rows:
        _intra[t] = (time.time(), rows)
        return rows, None
    return (e[1], None) if e else (None, err)  # source momentanément indisponible : derniers points connus


def _spot(rows):
    """Cours actuel tiré des points de 5 minutes : dernier échange, clôture officielle de référence, état du marché."""
    s = _last_session({"dates": [r[0] for r in rows], "close": [r[4] for r in rows]})
    i = next((k for k in range(len(s["close"]) - 1, -1, -1) if s["close"][k] is not None), None)
    if i is None:
        return None
    return {"price": s["close"][i], "at": s["dates"][i], "prev_close": s.get("prev_close"), "state": s.get("state"), "session": s.get("session")}


def _bucket(rows, minutes=30):
    """Un point par tranche de 30 minutes (le dernier cours de la tranche) : vue « 1S » lisible, même fin que « 1J »."""
    out = {}
    for r in rows:
        mm = int(r[0][14:16])
        out[r[0][:14] + f"{mm - mm % minutes:02d}"] = r
    return list(out.values())


def _align_last(rows, spot, weekly=False):
    """Cours quotidiens (ou hebdomadaires) : le dernier point devient le cours actuel (ajouté s'il s'agit d'un nouveau jour)."""
    d, p = spot["at"][:10], spot["price"]
    last = rows[-1]
    ld = last[0][:10]
    same = (date.fromisoformat(d).isocalendar()[:2] == date.fromisoformat(ld).isocalendar()[:2]) if weekly else d == ld
    if same:
        return rows[:-1] + [(last[0], last[1], max(last[2] or p, p), min(last[3] or p, p), p)]
    if d > ld:
        return rows + [(d + last[0][10:], p, p, p, p)]
    return rows


def _fx_factors(t, stamps, live_from=None):
    """Taux de conversion vers la devise choisie, point par point : taux historique de la BCE pour le passé, taux
    actuel (le même que celui des montants affichés ailleurs sur le site) pour la séance en cours et le dernier point."""
    target, ccy = lang.currency(), fx.asset_currency(t)
    base = "GBP" if ccy == "GBp" else ccy
    if target == ccy or (target == base and ccy != "GBp"):
        return None, ccy
    rates = stored_rates() or {}
    cur = rates[target] / rates[base] if rates.get(target) and rates.get(base) else None
    hist = fx.history_cached(base, target, 2600) if live_from != "all" else {}
    if not hist and not cur:
        return None, ccy
    f = fx.series_rate(hist, stamps, cur or 1.0) if hist else [cur] * len(stamps)
    if cur and live_from and live_from != "all":
        f = [cur if st[:10] >= live_from else x for st, x in zip(stamps, f)]
    if cur and f:
        f[-1] = cur
    if ccy == "GBp":
        f = [x / 100 for x in f]
    if len(f) != len(stamps):
        # Invariant : un facteur de conversion par point de la courbe. Sans lui, zip() tronquait la courbe en
        # silence (graphique amputé des points les plus récents) au lieu de signaler le problème.
        log.warning("%s : %d taux pour %d points, complété par le dernier taux connu", t, len(f), len(stamps))
        f = (f + [f[-1] if f else (cur or 1.0)] * len(stamps))[:len(stamps)]
    return f, target


def build_candles(ticker, range_id="month", fresh=False):
    """Courbe de la fiche société pour une période (1J, 1S, 1M, 3M, 6M, 1A, MAX), dans la devise choisie, se
    terminant toujours sur le même cours actuel. Les clôtures quotidiennes en base restent la seule donnée utilisée
    pour le score et les analyses."""
    t = ticker.upper()
    if not TICKER_RE.match(t) or (range_id not in _CANDLE_RANGES and range_id not in ("day", "week")):
        return None
    intra, ierr = _intraday_rows(t, ttl=15 if fresh else INTRA_TTL)  # fresh : bouton « Actualiser » (au plus un téléchargement toutes les 15 s)
    spot = _spot(intra) if intra else None
    if range_id in ("day", "week"):
        if not intra:
            return {"found": False, "ticker": t, "error": ierr}
        if range_id == "day":
            res = _last_session({"dates": [r[0] for r in intra], "open": [r[1] for r in intra], "high": [r[2] for r in intra],
                                 "low": [r[3] for r in intra], "close": [r[4] for r in intra], "close_native": [r[4] for r in intra]})
            f, cur = _fx_factors(t, res["dates"], live_from="all")  # un seul taux sur la journée : même variation qu'en devise d'origine
            k = f[-1] if f else 1.0
            out = {**res, "found": True, "ticker": t, "range": "day", "intraday": True, "currency": cur,
                   **{n: [None if v is None else v * k for v in res[n]] for n in ("open", "high", "low", "close")}}
            out["prev_close"] = res["prev_close"] * k if res.get("prev_close") is not None else None
            return out
        rows = _bucket(intra)
    else:
        key = f"ohlc:{t}:{range_id}"
        cached, updated = db.kv_get(key)
        if cached and updated and (datetime.now(timezone.utc) - updated).total_seconds() < _CANDLE_TTL[range_id]:
            rows = [tuple(r) for r in cached["rows"]]
        else:
            period, interval = _CANDLE_RANGES[range_id]
            rows, err = download_ohlc(t, period, interval)
            if rows:
                db.kv_set(key, {"rows": rows})
            elif cached:
                rows = [tuple(r) for r in cached["rows"]]
            else:
                return {"found": False, "ticker": t, "error": err}
        if spot:
            rows = _align_last(rows, spot, weekly=(range_id == "max"))
    stamps = [r[0] for r in rows]
    f, cur = _fx_factors(t, stamps, live_from=spot["session"] if spot else None)
    # strict=True : _fx_factors garantit un facteur par point ; en cas d'écart on veut une erreur visible,
    # pas une courbe silencieusement tronquée.
    conv = (lambda arr: [None if x is None else x * k for x, k in zip(arr, f, strict=True)]) if f else (lambda arr: arr)
    res = {"found": True, "ticker": t, "range": range_id, "intraday": range_id == "week", "currency": cur, "dates": stamps,
           "open": conv([r[1] for r in rows]), "high": conv([r[2] for r in rows]), "low": conv([r[3] for r in rows]), "close": conv([r[4] for r in rows])}
    if range_id == "week":
        ph = [_phase(st) for st in stamps]
        if any(x != "reg" for x in ph):
            res["phase"] = ph
    if spot:
        res.update(state=spot["state"], session=None)
    return res


_US_OFFSETS = ("-04:00", "-05:00")  # heure de New York (été / hiver)


def _phase(stamp):
    """Moment de la journée d'un point : « pre » (avant-Bourse), « reg » (séance officielle) ou « post » (après-Bourse).
    Seules les actions américaines s'échangent hors séance chez Yahoo ; ailleurs tout est « reg »."""
    if len(stamp) < 16 or stamp[-6:] not in _US_OFFSETS:
        return "reg"
    hm = stamp[11:16]
    return "pre" if hm < "09:30" else "post" if hm >= "16:00" else "reg"


def _market_state(last_stamp, session):
    """État du marché calculé à partir de l'heure et des derniers cours reçus (avant, il venait de la fiche société,
    rechargée toutes les 12 h : il pouvait afficher « fermé » en pleine séance, ou l'inverse)."""
    if not last_stamp:
        return "CLOSED"
    try:
        last = datetime.fromisoformat(last_stamp)
    except ValueError:
        return None
    now = datetime.now(timezone.utc)
    if last_stamp[-6:] in _US_OFFSETS:
        try:
            from zoneinfo import ZoneInfo
            ny = now.astimezone(ZoneInfo("America/New_York"))
        except Exception:  # noqa: BLE001 - base des fuseaux absente : décalage du dernier cours reçu
            ny = now.astimezone(last.tzinfo)
        if ny.date().isoformat() != session:  # rien d'échangé aujourd'hui (nuit, week-end, jour férié)
            return "CLOSED"
        hm = ny.strftime("%H:%M")
        return "PRE" if "04:00" <= hm < "09:30" else "REGULAR" if hm < "16:00" else "POST" if hm < "20:00" else "CLOSED"
    return "REGULAR" if (now - last).total_seconds() < 30 * 60 else "CLOSED"


def _last_session(res):
    """Vue « 1J » : la dernière séance de bourse seulement (celle du jour, avant-Bourse comprise, ou la précédente si
    rien n'a encore été échangé aujourd'hui), avec la clôture officielle de la séance d'avant comme point de
    comparaison — c'est par rapport à elle qu'on mesure la variation du jour, comme dans les applis de bourse."""
    days = [d[:10] for d in res["dates"]]  # date locale de la place de cotation (horodatage ISO avec son décalage)
    order = list(dict.fromkeys(days))
    if not order:
        return res
    pick = len(order) - 1
    if days.count(order[pick]) < 4 and pick > 0:  # moins de 20 minutes d'échanges : on montre la séance précédente, complète
        pick -= 1
    sel = [i for i, d in enumerate(days) if d == order[pick]]
    prev = [i for i, d in enumerate(days) if pick > 0 and d == order[pick - 1]]
    out = {**res, **{k: [res[k][i] for i in sel] for k in ("dates", "open", "high", "low", "close") if k in res}}

    def prev_close(arr):  # clôture officielle (16 h à New York), pas le dernier échange d'après-Bourse
        reg = [i for i in prev if arr[i] is not None and _phase(res["dates"][i]) == "reg"]
        idx = reg or [i for i in prev if arr[i] is not None]
        return arr[idx[-1]] if idx else None
    out["prev_close"] = prev_close(res["close"])
    native = out.pop("close_native", None)
    if native:
        out["prev_close_native"] = prev_close(native)
        out["last_native"] = next((native[i] for i in reversed(sel) if native[i] is not None), None)
    phases = [_phase(res["dates"][i]) for i in sel]
    if any(p != "reg" for p in phases):
        out["phase"] = phases
    out["session"] = order[pick]
    out["state"] = _market_state(res["dates"][sel[-1]], order[pick])
    return out


_ext_lock = threading.Lock()


def extended_quotes(tickers):
    """Dernier cours des actions américaines, avant- et après-Bourse compris, avec la variation depuis la dernière
    clôture officielle : {ticker: {price, change_pct, state, session, at, source}}. Mêmes points de 5 minutes que la
    fiche société (cache commun) : la liste et la fiche affichent exactement le même cours."""
    us = sorted({t for t in tickers if TICKER_RE.match(t) and not any(c in t for c in ".^=")})
    if not us:
        return {}
    with _ext_lock:
        stale = [t for t in us if not (_intra.get(t) and time.time() - _intra[t][0] < INTRA_TTL)]
        if stale:
            now = time.time()
            for t, rows in download_intraday_many(stale).items():
                _intra[t] = (now, rows)
    out = {}
    for t in us:
        e = _intra.get(t)
        sp = _spot(e[1]) if e else None
        if not sp:
            continue
        pc = sp["prev_close"]
        out[t] = {"price": round(sp["price"], 4), "change_pct": round((sp["price"] / pc - 1) * 100, 2) if pc else None, "state": sp["state"],
                  "session": sp["session"], "at": sp["at"], "source": "yahoo", "delayed": True}
    return out


def build_compare(tickers):
    """Cours alignés sur les mêmes dates, pour comparer plusieurs actions sur un même graphique."""
    valid = {c["ticker"] for c in load_watchlist()}
    tickers = [t for t in tickers if t in valid][:6]
    closes = _load_closes(tickers)
    if not tickers:
        return {"dates": [], "series": {}}
    dates = sorted({d for t in tickers for d, _ in closes[t]})
    series = {}
    for t in tickers:
        by_day = dict(closes[t])
        series[t] = [round(by_day[d], 2) if d in by_day else None for d in dates]
    return {"dates": [d.isoformat() for d in dates], "series": series}


# ---------- sources & réglages ----------
KEY_NAMES = {"finnhub_api_key": "FINNHUB_API_KEY", "twelvedata_api_key": "TWELVEDATA_API_KEY", "alphavantage_api_key": "ALPHAVANTAGE_API_KEY",
             "polygon_api_key": "POLYGON_API_KEY", "tiingo_api_key": "TIINGO_API_KEY"}


def live_prices():
    """Derniers cours de la watchlist pour l'actualisation en direct : avant- et après-Bourse compris pour les actions
    américaines (sinon, le matin, la liste montrait encore la séance de la veille alors que l'action s'échangeait déjà),
    et pour le reste la première source en temps réel disponible."""
    tickers = [c["ticker"] for c in load_watchlist()]
    try:
        ext = extended_quotes(tickers)
    except Exception as e:  # noqa: BLE001 - Yahoo bloqué : on garde les autres sources
        log.warning("cours hors séance indisponibles : %s", e)
        ext = {}
    rest = [t for t in tickers if t not in ext]
    return {"quotes": {**(multi.live_quotes(rest) if rest else {}), **ext}, "at": datetime.now(timezone.utc).isoformat()}


def build_providers():
    rows = []
    for p in REGISTRY.values():
        rows.append({"name": p.name, "label": p.label, "needs_key": bool(p.key), "key_name": p.key, "configured": p.available(), "signup": p.signup,
                     "note": p.note, "free_limit": p.free_limit, "realtime": p.realtime, "used_today": quota_used(p.name), "daily_limit": p.daily_limit,
                     "from_env": bool(p.key and os.getenv(KEY_NAMES.get(p.key, ""))), "paused": health.is_paused(p.name)})
    return rows


def build_sources():
    return {"sources": health.snapshot(), "providers": build_providers(), "sec_configured": bool(contact_email()),
            "finnhub_configured": bool((settings().get("finnhub_api_key") or "").strip()),
            "contact_email": contact_email() if auth.is_admin() else "", "status": status()}


def save_settings(contact=None, api_keys=None):
    """Enregistre l'email de contact (SEC) et/ou des clés d'API, partagés par tous les utilisateurs : en base (persistant, y compris dans
    le cloud) et, si possible, dans config/settings.json. Relance ensuite la collecte."""
    from collectors.http import shared_settings
    shared = dict(shared_settings(fresh=True))
    if contact is not None:
        if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", contact.strip()):
            raise ValueError("adresse email invalide")
        if os.getenv("CONTACT_EMAIL"):
            raise ValueError("l'email est défini par la variable d'environnement CONTACT_EMAIL : modifie-la dans Cloud Run")
        shared["contact_email"] = contact.strip()
    for key, value in (api_keys or {}).items():
        if key not in KEY_NAMES:
            raise ValueError(f"réglage inconnu : {key}")
        if os.getenv(KEY_NAMES[key]):
            raise ValueError(f"{key} est défini par la variable d'environnement {KEY_NAMES[key]} : modifie-la dans Cloud Run")
        value = (value or "").strip()
        if value and not re.fullmatch(r"[A-Za-z0-9_\-]{8,100}", value):
            raise ValueError("clé invalide (lettres, chiffres, - et _ uniquement, 8 caractères minimum)")
        if value:
            shared[key] = value
        else:
            shared.pop(key, None)
            cfg = raw_settings()  # une clé retirée doit aussi disparaître du fichier
            if key in cfg:
                cfg.pop(key)
                try:
                    SETTINGS.write_text(json.dumps(cfg, indent=2, ensure_ascii=False), encoding="utf-8")
                except OSError:
                    pass
    db.kv_set("settings:shared", shared)
    shared_settings(fresh=True)
    health.resume()
    if not SERVERLESS:
        threading.Thread(target=run_cycle, daemon=True).start()
    return {"ok": True}


# ---------- profil, langue, devises ----------
DEFAULT_PROFILE = {"display_name": "", "language": "fr", "currency": "EUR"}


def get_profile(user):
    saved = db.kv_get(f"profile:{user}")[0]
    return {**DEFAULT_PROFILE, **(saved or {}), "saved": bool(saved)}


def save_profile(user, payload):
    p = get_profile(user)
    p.pop("saved", None)
    if "display_name" in payload:
        name = str(payload["display_name"] or "").strip()
        if len(name) > 40:
            raise ValueError(L("nom trop long (40 caractères maximum)", "name too long (40 characters max)"))
        p["display_name"] = name
    if payload.get("language") in lang.LANGS:
        p["language"] = payload["language"]
    if payload.get("currency") in fx.SUPPORTED:
        p["currency"] = payload["currency"]
    db.kv_set(f"profile:{user}", p)
    return p


def stored_rates():
    return (db.kv_get("fx:latest")[0] or {}).get("rates")


def build_fx():
    data = fx.latest_rates() or {}
    return {"base": "EUR", "date": data.get("date"), "rates": data.get("rates") or {}, "source": data.get("source"), "supported": fx.SUPPORTED}


def build_me(user):
    dash = build_dashboard()
    rows = dash["rows"]
    st = status()
    return {"profile": get_profile(user), "user": user, "contact_email": contact_email() if auth.is_admin(user) else "",
            "email_from_env": bool(os.getenv("CONTACT_EMAIL")),
            "follow": {"total": len(rows), "custom": sum(1 for r in rows if not r["base"]), "counts": dash["counts"], "avg_buy_perf": dash["avg_buy_perf"],
                       "rows": [{"ticker": r["ticker"], "name": r["name"], "label": r["label"], "score": r["score"], "removable": not r["base"]} for r in rows]},
            "last_run": st["last_run"], "providers": build_providers(), "sec_configured": bool(contact_email()),
            "password_env": bool(os.getenv("LOGIN_PASSWORD")) and auth.norm(user) == auth.norm(auth.config()["user"]), "user_env": bool(os.getenv("LOGIN_USER")), "serverless": SERVERLESS,
            "admin": auth.is_admin(user), "users": auth.list_users() if auth.is_admin(user) else None}


def change_password(current, new):
    """Mot de passe de l'administrateur (les invités n'en ont pas)."""
    if not auth.is_admin():
        raise ValueError(L("réservé à l'administrateur", "administrator only"))
    if not auth.admin_password_ok(current):
        raise ValueError(L("mot de passe actuel incorrect", "current password is incorrect"))
    if len(new) < 8:
        raise ValueError(L("nouveau mot de passe trop court (8 caractères minimum)", "new password too short (8 characters minimum)"))
    if os.getenv("LOGIN_PASSWORD"):
        raise ValueError(L("le mot de passe est défini par la variable d'environnement LOGIN_PASSWORD sur le serveur", "the password is set by the LOGIN_PASSWORD environment variable on the server"))
    cfg = raw_settings()
    cfg["login_password"] = new
    SETTINGS.write_text(json.dumps(cfg, indent=2, ensure_ascii=False), encoding="utf-8")
    return {"ok": True}
