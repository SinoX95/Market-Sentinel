"""Traduction anglais -> français des titres d'actualités et descriptions (service de traduction public de Google, sans clé).
En cas d'échec (service bloqué, quota), le texte d'origine est conservé : rien ne casse."""
import json
import logging
import re
import time
from urllib.parse import quote

from collectors import health
from collectors.http import fetch

log = logging.getLogger("sentinel")
_cooldown = [0.0]  # après un échec, on laisse le service tranquille 15 minutes
_FR_HINT = re.compile(r"[éèêàùçôîœ]|\b(le|la|les|des|du|une|est|sur|pour|avec|dans|et)\b", re.I)


def looks_french(text):
    return len(_FR_HINT.findall(text or "")) >= 2


def parse(payload):
    """Réponse du service : [[["traduction", "original", ...], ...], ...] -> texte traduit."""
    data = json.loads(payload)
    return "".join(seg[0] for seg in (data[0] or []) if seg and seg[0]).strip()


def to_french(text, timeout=8):
    if not text or looks_french(text) or health.is_paused("google_translate") or time.time() < _cooldown[0]:
        return None
    try:
        raw = fetch("https://translate.googleapis.com/translate_a/single?client=gtx&sl=auto&tl=fr&dt=t&q=" + quote(text[:4500]),
                    "google_translate", timeout=timeout, retries=0)
        out = parse(raw.decode("utf-8"))
        return out or None
    except Exception as e:  # noqa: BLE001
        log.info("traduction impossible : %s", e)
        _cooldown[0] = time.time() + 900
        return None


def add_french_titles(items, limit=15):
    """Ajoute « title_fr » aux actualités en anglais (au plus 'limit' nouvelles traductions)."""
    n = 0
    for it in items:
        if it.get("title_fr") is not None or n >= limit:
            continue
        if looks_french(it.get("title", "")):
            it["title_fr"] = it["title"]
            continue
        if time.time() < _cooldown[0]:
            break
        tr = to_french(it.get("title", ""))
        n += 1
        if tr:
            it["title_fr"] = tr
    return items
