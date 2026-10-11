"""Langue et devise de la requête en cours, et mise en forme des montants.

La langue et la devise sont posées par un middleware (cookies) dans des variables de contexte ; les textes générés par
le serveur s'écrivent avec L("français", "english"), et les montants passent par money() / money_big().
"""
import contextvars
import re

_lang = contextvars.ContextVar("lang", default="fr")
_cur = contextvars.ContextVar("currency", default="EUR")
_rates = contextvars.ContextVar("rates", default=None)   # {"EUR": 1.0, "USD": 1.17, ...} (1 EUR = x devise)

LANGS = ("fr", "en")
SYMBOLS = {"EUR": "€", "USD": "$", "GBP": "£", "CHF": "CHF", "CAD": "C$", "JPY": "¥", "AUD": "A$"}


def set_context(lang=None, currency=None, rates=None):
    if lang is not None:
        _lang.set(lang if lang in LANGS else "fr")
    if currency is not None:
        _cur.set(currency)
    if rates is not None:
        _rates.set(rates)


def lang():
    return _lang.get()


def currency():
    return _cur.get()


_DEC = re.compile(r"(?<=\d)\.(?=\d)")


def L(fr, en):
    """Texte dans la langue de l'interface ; en français, virgule décimale (160,4 et non 160.4)."""
    return en if _lang.get() == "en" else _DEC.sub(",", fr)


def convert(v, frm="USD", to=None):
    """Convertit v de la devise frm vers la devise choisie (ou 'to'). Sans taux disponible : v inchangé et devise d'origine."""
    to = to or _cur.get()
    if frm == "GBp":          # pence britanniques
        v, frm = v / 100, "GBP"
    r = _rates.get()
    if not r or frm == to or frm not in r or to not in r:
        return v, (frm if not r or frm not in r or to not in r else to)
    return v / r[frm] * r[to], to


def _num(v, d=2):
    s = f"{v:,.{d}f}"
    return s.replace(",", " ").replace(".", ",") if _lang.get() == "fr" else s


def _wrap(s, cur):
    sym = SYMBOLS.get(cur, cur)
    return f"{s} {sym}" if _lang.get() == "fr" else (f"{sym}{s}" if len(sym) == 1 or sym.endswith("$") else f"{s} {sym}")


def money(v, frm="USD", d=2):
    """Prix ou montant, converti et mis en forme dans la devise choisie."""
    if v is None:
        return "–"
    x, cur = convert(v, frm)
    return _wrap(_num(x, d), cur)


def money_big(v, frm="USD"):
    """Grand montant abrégé (Md / M / k en français, B / M / k en anglais)."""
    if v is None:
        return "–"
    x, cur = convert(abs(v), frm)
    sign = "-" if v < 0 else ""
    if x >= 1e12:
        n, u = x / 1e12, L(" T", "T")
    elif x >= 1e9:
        n, u = x / 1e9, L(" Md", "B")
    elif x >= 1e6:
        n, u = x / 1e6, L(" M", "M")
    else:
        n, u = x / 1e3, L(" k", "k")
    return sign + _wrap(_num(n, 1 if x >= 1e9 else 0) + u, cur)


def pct(v, d=0, sign=False):
    """Pourcentage (v déjà en %, ex. 12.3) : '+12 %' en français, '+12%' en anglais."""
    return (f"{v:+.{d}f}" if sign else f"{v:.{d}f}") + L(" %", "%")


def num(v, d=0, sign=False):
    """Nombre décimal (point décimal dans les deux langues, comme le reste des textes générés)."""
    return f"{v:+.{d}f}" if sign else f"{v:.{d}f}"


_MONTHS_EN = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


def date_s(iso):
    """'2026-10-03' -> '03/10/2026' (fr) ou 'Oct 3, 2026' (en)."""
    if not iso:
        return ""
    y, m, d = iso[:4], iso[5:7], iso[8:10]
    if _lang.get() == "en":
        return f"{_MONTHS_EN[int(m) - 1]} {int(d)}, {y}"
    return f"{d}/{m}/{y}"
