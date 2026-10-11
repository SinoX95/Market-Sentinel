"""Actualités par entreprise : Yahoo Finance + Google Actualités, avec Bing Actualités en secours."""
import concurrent.futures as cf
import logging
import re
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import quote

import yfinance as yf

from analysis.sentiment import headline_sentiment
from collectors import finnhub, yahoo
from collectors.http import fetch

log = logging.getLogger("sentinel")


def _parse_rss(xml_bytes, default_source):
    root = ET.fromstring(xml_bytes)
    out = []
    for it in root.iter("item"):
        title = (it.findtext("title") or "").strip()
        source = ""
        for c in it:
            if c.tag.rsplit("}", 1)[-1] == "Source" and (c.text or "").strip():
                source = c.text.strip()
        if source and title.endswith(" - " + source):
            title = title[: -len(source) - 3]
        try:
            pub = parsedate_to_datetime(it.findtext("pubDate")).astimezone(timezone.utc).isoformat()
        except Exception:
            pub = None
        link = (it.findtext("link") or "").strip()
        if title and link and pub:
            out.append({"title": title, "url": link, "source": source or default_source, "published": pub})
    return out


def google_news(query, days=14, limit=10, french=False):
    q = quote(f"{query} when:{days}d")
    ed = "hl=fr&gl=FR&ceid=FR:fr" if french else "hl=en-US&gl=US&ceid=US:en"
    items = _parse_rss(fetch(f"https://news.google.com/rss/search?q={q}&{ed}", "google_news"), "Google Actualités")[:limit]
    if french:
        for it in items:
            it["title_fr"] = it["title"]
    return items


def bing_news(query, limit=10):
    # setmkt=fr-FR : sans ce paramètre, Bing choisit le marché selon la géolocalisation de l'IP sortante du
    # serveur (ex. nl-BE en Europe), pas celle de l'utilisateur — ce qui donnait des résultats en néerlandais.
    return _parse_rss(fetch(f"https://www.bing.com/news/search?q={quote(query)}&format=rss&setmkt=fr-FR", "bing_news"),
                      "Bing Actualités")[:limit]


def search_news(query, days=14, limit=10):
    """Google d'abord ; si la source est bloquée ou vide, Bing prend le relais."""
    errors = []
    for fn in (lambda: google_news(query, days, limit), lambda: bing_news(query, limit)):
        try:
            items = fn()
            if items:
                return items
        except Exception as e:
            errors.append(str(e))
    if errors:
        raise RuntimeError(" | ".join(errors))
    return []


def direct_url(url, timeout=5):
    """URL directe de l'article plutôt qu'un lien d'agrégateur (Module 6) — appelée uniquement pour les
    actualités qui passent le seuil de pertinence et sont réellement envoyées, jamais pour chaque résultat
    de recherche (pas de coût réseau supplémentaire pour les items filtrés).

    Bing (apiclick.aspx) porte l'URL finale dans son propre paramètre « url= » : aucun appel réseau
    nécessaire, un simple décodage suffit. Google Actualités (RSS) ne porte pas l'URL finale dans la
    requête ; il faut suivre la redirection (un seul HEAD, échec silencieux -> on garde le lien d'origine
    plutôt que de bloquer l'envoi pour ça)."""
    if not url:
        return url
    if "bing.com" in url:
        m = re.search(r"[?&]url=([^&]+)", url)
        if m:
            from urllib.parse import unquote
            return unquote(m.group(1))
        return url
    if "news.google.com" in url:
        import urllib.error
        import urllib.request
        try:
            req = urllib.request.Request(url, method="HEAD", headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.url
        except (urllib.error.URLError, OSError):
            return url
    return url


def yahoo_news(ticker, limit=10):
    out = []
    for n in (yahoo.call("actualités", lambda: yf.Ticker(ticker).news, timeout=6) or [])[:limit]:
        c = n.get("content") if isinstance(n.get("content"), dict) else None
        if c:  # nouveau format
            title = c.get("title")
            url = ((c.get("canonicalUrl") or {}).get("url")) or ((c.get("clickThroughUrl") or {}).get("url"))
            source = (c.get("provider") or {}).get("displayName") or "Yahoo Finance"
            pub = c.get("pubDate")
            if pub:
                pub = datetime.fromisoformat(pub.replace("Z", "+00:00")).astimezone(timezone.utc).isoformat()
        else:  # ancien format
            title, url, source = n.get("title"), n.get("link"), n.get("publisher") or "Yahoo Finance"
            ts = n.get("providerPublishTime")
            pub = datetime.fromtimestamp(ts, timezone.utc).isoformat() if ts else None
        if title and url and pub:
            out.append({"title": title, "url": url, "source": source, "published": pub})
    return out


def collect_news(ticker, name, limit=15):
    """Interroge toutes les sources en parallèle (au lieu de les attendre l'une après l'autre) et n'attend pas plus de
    15 s au total : la fiche s'affiche avec ce qui a eu le temps de répondre plutôt que d'attendre une source lente."""
    providers = [("Yahoo", lambda: yahoo_news(ticker)), ("presse", lambda: search_news(f'"{name}" stock')),
                 ("presse française", lambda: google_news(f'"{name}" action bourse', limit=6, french=True))]
    if finnhub.enabled():
        providers.append(("Finnhub", lambda: finnhub.news(ticker)))
    items, errors = [], []
    ex = cf.ThreadPoolExecutor(max_workers=len(providers))
    futs = {ex.submit(fn): label for label, fn in providers}
    try:
        for fut in cf.as_completed(futs, timeout=15):
            label = futs[fut]
            try:
                items += fut.result()
            except Exception as e:
                log.warning("news %s/%s : %s", ticker, label, e)
                errors.append(f"{label} : {e}")
    except cf.TimeoutError:
        pass  # une source n'a pas répondu à temps : on garde celles qui ont déjà répondu
    finally:
        ex.shutdown(wait=False)
    seen, out = set(), []
    for it in sorted(items, key=lambda x: x["published"], reverse=True):
        key = re.sub(r"[^a-z0-9]", "", it["title"].lower())[:60]
        if key in seen:
            continue
        seen.add(key)
        it["sentiment"], it["terms"] = headline_sentiment(it["title"])
        out.append(it)
    from collectors.translate import add_french_titles
    return add_french_titles(out[:limit]), errors
