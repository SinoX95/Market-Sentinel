"""Envoi des alertes vers les canaux configurés : Telegram (bot personnel) et Discord (webhook de salon),
sous forme d'embeds Discord individuels — une notification par événement, jamais plusieurs entreprises
regroupées dans un même message (voir alerts/README.md, section "Format des notifications").

Discord est l'unique canal destiné aux notifications/actualités de Market Sentinel (email retiré — voir
backend/mailer.py, qui reste utilisé uniquement pour les emails d'authentification/invitation, indépendants
de ce module).

Aucune fonction ici ne lève d'exception : un canal qui échoue ne doit jamais faire échouer le cycle de
collecte (voir backend/service.py). Pour configurer Telegram : créer un bot via @BotFather (jeton fourni
immédiatement), démarrer une conversation avec lui, puis renseigner telegram_bot_token et telegram_chat_id
dans config/settings.json (ou les variables d'environnement TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID en cloud) —
même mécanisme que les clés d'API existantes. Pour configurer Discord : dans le salon visé, Paramètres du
serveur -> Intégrations -> Webhooks -> Nouveau webhook -> copier l'URL dans discord_webhook_url (ou la
variable d'environnement DISCORD_WEBHOOK_URL en cloud). Un webhook suffit ici (poster des messages) : pas
besoin d'un bot Discord complet avec une connexion permanente, ce qui aurait demandé un processus à part,
contraire à l'architecture sans serveur dédié (Cloud Scheduler + Cloud Run) du reste du projet.
"""
import json
import logging
import re
import time
import urllib.error
import urllib.request

from alerts.importance import SEUIL_SURVEILLER, classify, score_for, tier_for
from collectors.http import settings

log = logging.getLogger("sentinel")
TELEGRAM_API = "https://api.telegram.org/bot{token}/sendMessage"
DISCORD_EMBED_COLOR = 0x5865F2  # bleu Discord par défaut, neutre/informatif
DISCORD_TITLE_MAX = 256  # limites imposées par l'API Discord pour un embed
DISCORD_DESCRIPTION_MAX = 4000
DISCORD_FIELD_VALUE_MAX = 1024
_URL_RE = re.compile(r"https?://\S+")
# Cloudflare (qui protège l'infrastructure Discord) renvoie une erreur 1010 ("signature de client
# suspecte") pour les requêtes avec le User-Agent par défaut d'urllib ("Python-urllib/3.x"), identifié
# comme un script. Un User-Agent explicite et descriptif suffit à passer ce filtre.
_USER_AGENT = "MarketSentinel/1.0 (+https://github.com/)"

# Les actualités (mention de dirigeant ou actualité d'entreprise) peuvent recevoir un score de pertinence
# trop faible (< SEUIL_SURVEILLER, palier FAIBLE) : dans ce cas, notify() n'envoie rien ET ne journalise
# rien — « ne pas envoyer dans le canal principal » (cahier des charges) pour du contenu anecdotique
# reconnu comme tel. Les autres types d'alerte (mouvement de prix, résultats...) n'ont pas ce filtre : leur
# score varie déjà pour de bonnes raisons structurelles, jamais pour du bruit pur.
_NEWS_KINDS = ("executive", "company_news")

# Limite de débit de chaque API (Module 12) : espacement minimal entre deux envois sur un même canal,
# avec une marge sous la limite réelle (Discord : ~5 requêtes/2s par webhook ; Telegram : ~1 message/s par
# conversation). Désactivé dans les tests (voir tests/conftest.py) pour ne pas ralentir la suite.
RATE_LIMIT_ENABLED = True
_MIN_INTERVAL = {"discord": 0.4, "telegram": 1.1}
_last_send = {"discord": 0.0, "telegram": 0.0}


def _throttle(channel):
    if not RATE_LIMIT_ENABLED:
        return
    wait = _MIN_INTERVAL[channel] - (time.monotonic() - _last_send[channel])
    if wait > 0:
        time.sleep(wait)
    _last_send[channel] = time.monotonic()


def _linkify_source_count(n):
    return "🔗 Source" if n == 1 else f"🔗 Source {n}"


def _html_escape(text):
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _telegram_format(text):
    """Convertit le texte brut composé par notify() en HTML Telegram : la première ligne (le sujet) est
    mise en gras, et chaque URL — souvent très longue, par exemple un lien de redirection Google News —
    est masquée derrière un lien court plutôt qu'affichée en clair."""
    head, sep, rest = text.partition("\n")
    out = f"<b>{_html_escape(head)}</b>"
    if not sep:
        return out
    n = [0]

    def repl(m):
        n[0] += 1
        # m.group(0) est déjà échappé (la substitution tourne sur le texte échappé, pas l'original) :
        # ne pas rééchapper, sous peine de "&" -> "&amp;amp;".
        return f'<a href="{m.group(0)}">{_linkify_source_count(n[0])}</a>'

    out += "\n" + _URL_RE.sub(repl, _html_escape(rest))
    return out


_BIAS_LABEL = {1: "Positif", -1: "Négatif", 0: "Neutre"}
# Emoji + couleur d'embed par catégorie d'affichage (Module 2/11) ; le mouvement de marché garde une
# couleur dynamique (hausse/baisse) calculée séparément dans _style_for.
_CATEGORY_STYLE = {
    "breaking": ("🚨", 0x5865F2),
    "catalyst_positive": ("📈", 0x57F287),
    "catalyst_negative": ("📉", 0xED4245),
    "upcoming_event": ("🗓️", 0xE67E22),
    "innovation": ("🟣", 0x9B59B6),
    "risk": ("⚠️", 0xE67E22),
    "info": ("🗞️", DISCORD_EMBED_COLOR),
}


def _style_for(category, bias):
    if category == "market_alert":
        return "📊", (0x57F287 if bias >= 0 else 0xED4245)
    return _CATEGORY_STYLE.get(category, ("🗞️", DISCORD_EMBED_COLOR))


def _discord_linkify(text):
    """Masque une URL brute derrière un lien Markdown court (Module 6) : un embed garde son lien principal
    dans le champ dédié embed["url"] (titre cliquable), mais le corps peut encore contenir ses propres
    liens — ex. plusieurs actualités citées dans l'enrichissement d'une alerte de mouvement de prix."""
    n = [0]

    def repl(m):
        n[0] += 1
        return f"[{_linkify_source_count(n[0])}]({m.group(0)})"

    return _URL_RE.sub(repl, text)


def _build_embed(subject, body, *, score, tier, reason, category, bias, ticker=None, company_name=None,
                  source=None, url=None, image_url=None, published=None):
    """Un embed Discord par événement (Module 2) : titre avec pictogramme, entreprise/ticker, résumé,
    pertinence chiffrée, biais potentiel, raison du score, source — jamais plusieurs événements combinés
    dans un même embed (voir le docstring du module)."""
    emoji, color = _style_for(category, bias)
    header = " · ".join(x for x in (company_name, ticker) if x)
    fields = []
    if header:
        fields.append({"name": "Entreprise", "value": header[:DISCORD_FIELD_VALUE_MAX], "inline": True})
    fields.append({"name": "Pertinence", "value": f"{score}/100 — {tier}", "inline": True})
    fields.append({"name": "Biais potentiel", "value": _BIAS_LABEL[1 if bias > 0 else -1 if bias < 0 else 0],
                  "inline": True})
    if reason:
        fields.append({"name": "Pourquoi ce score ?", "value": reason[:DISCORD_FIELD_VALUE_MAX], "inline": False})
    if source:
        fields.append({"name": "Source", "value": source[:DISCORD_FIELD_VALUE_MAX], "inline": True})
    description = _discord_linkify((body or "").strip())
    if len(description) > DISCORD_DESCRIPTION_MAX:
        description = description[:DISCORD_DESCRIPTION_MAX] + "\n…(tronqué)"
    embed = {"title": f"{emoji} {subject}"[:DISCORD_TITLE_MAX], "description": description, "color": color,
             "fields": fields,
             "footer": {"text": "Analyse indicative fondée sur l'information disponible — "
                                 "l'impact sur le cours reste incertain."}}
    if url:
        embed["url"] = url  # rend le titre de l'embed cliquable, sans lien brut à afficher séparément
    if image_url:
        embed["thumbnail"] = {"url": image_url}
    if published:
        embed["timestamp"] = published  # Discord affiche une date/heure localisée automatiquement
    return embed


def _importance(kind, ticker, ctx):
    """(score, palier, raison, catégorie, biais) pour un déclencheur donné (Module E — voir
    alerts/importance.py). ctx porte les détails spécifiques au type d'alerte (pct/threshold, label,
    title/source/company_name pour une actualité...) passés par l'appelant de notify()."""
    full_ctx = dict(ctx or {})
    full_ctx.setdefault("ticker", ticker)
    score, reason = score_for(kind, **full_ctx)
    category, bias = classify(kind, full_ctx)
    return score, tier_for(score), reason, category, bias


def _cfg():
    s = settings()
    return {"token": str(s.get("telegram_bot_token") or "").strip(), "chat_id": str(s.get("telegram_chat_id") or "").strip()}


def telegram_configured():
    c = _cfg()
    return bool(c["token"] and c["chat_id"])


def send_telegram(text, timeout=10):
    """True si le message Telegram est parti. Jamais d'exception : voir le module.

    `text` est le texte brut composé par notify() (sujet puis corps) ; _telegram_format le met en forme
    (sujet en gras, URL masquées derrière un lien court) avant envoi en HTML — un message Telegram en
    texte brut affichait les URL de certaines sources (ex. redirection Google News) en clair, illisibles
    sur plusieurs lignes."""
    c = _cfg()
    if not (c["token"] and c["chat_id"]):
        return False
    url = TELEGRAM_API.format(token=c["token"])
    payload = json.dumps({"chat_id": c["chat_id"], "text": _telegram_format(text), "parse_mode": "HTML",
                          "disable_web_page_preview": True}).encode("utf-8")
    req = urllib.request.Request(
        url, data=payload,
        headers={"Content-Type": "application/json", "User-Agent": _USER_AGENT},
        method="POST",
    )
    _throttle("telegram")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status == 200
    except urllib.error.HTTPError as e:
        # Le corps de la réponse d'erreur Telegram ne contient pas le jeton, mais on ne logge jamais l'URL
        # (elle le contient) : seul le code et le message d'erreur Telegram sont utiles ici.
        log.error("Telegram a refusé le message (HTTP %s) : %s", e.code, e.read(300))
        return False
    except Exception as e:  # noqa: BLE001
        log.error("envoi Telegram impossible : %s", type(e).__name__)
        return False


def _discord_cfg():
    return str(settings().get("discord_webhook_url") or "").strip()


def discord_configured():
    return bool(_discord_cfg())


def send_discord(embed, timeout=10):
    """True si l'embed Discord est parti. Jamais d'exception : voir le module. `embed` est un dict déjà
    construit par _build_embed — un seul événement par appel (Module 1 : jamais plusieurs entreprises
    regroupées dans un même message)."""
    url = _discord_cfg()
    if not url:
        return False
    payload = json.dumps({"embeds": [embed]}).encode("utf-8")
    req = urllib.request.Request(
        url, data=payload,
        headers={"Content-Type": "application/json", "User-Agent": _USER_AGENT},
        method="POST",
    )
    _throttle("discord")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status in (200, 204)  # Discord répond 204 sans contenu sur un envoi réussi
    except urllib.error.HTTPError as e:
        # L'URL du webhook contient le secret : jamais dans les journaux, seul le code et le message le sont.
        log.error("Discord a refusé le message (HTTP %s) : %s", e.code, e.read(300))
        return False
    except Exception as e:  # noqa: BLE001
        log.error("envoi Discord impossible : %s", type(e).__name__)
        return False


def channels_configured():
    """Canaux actuellement opérationnels, pour affichage (jamais les secrets eux-mêmes). Discord est
    l'unique canal de notification/actualités ; l'email n'en fait plus partie (voir le docstring du module)."""
    return {"telegram": telegram_configured(), "discord": discord_configured()}


def notify(subject, text, ticker=None, kind=None, company_name=None, source=None, url=None, image_url=None,
          published=None, **importance_ctx):
    """Envoie une notification individuelle sur tous les canaux configurés — jamais regroupée avec une
    autre alerte, même survenue dans le même cycle de collecte (Module 1 : chaque événement a sa propre
    notification) — et journalise l'alerte dans l'historique (Module G — voir backend/db.py::add_alert/
    recent_alerts), que des canaux soient configurés ou non : l'historique reste utile pour vérifier après
    coup qu'une alerte aurait dû partir, même sans Telegram/Discord réglés.

    ticker/kind/company_name/source/url/image_url/published enrichissent l'embed Discord et l'historique ;
    ils ne sont obligatoires pour aucun type d'alerte (un mouvement de prix n'a ni source ni image, par
    exemple). **importance_ctx (ex. pct=, threshold=, label=, title=...) alimente le score de pertinence/
    importance 0-100 et son palier (Module E — voir alerts/importance.py) : la raison du score est ajoutée
    au message envoyé.

    Pour une actualité (kind="executive"/"company_news") dont le score tombe sous le seuil "À surveiller"
    (palier FAIBLE — contenu reconnu comme anecdotique ou hors sujet), rien n'est envoyé NI journalisé :
    c'est le filtre anti-bruit du cahier des charges, distinct du cas "aucun canal configuré" (qui reste
    journalisé). Retourne {} dans les deux cas — l'appelant n'a pas besoin de les distinguer.

    Retourne quels canaux ont effectivement envoyé le message."""
    score, tier, reason, category, bias = _importance(kind, ticker, importance_ctx)
    if kind in _NEWS_KINDS and score < SEUIL_SURVEILLER:
        return {}
    from backend import db
    embed = _build_embed(subject, text, score=score, tier=tier, reason=reason, category=category, bias=bias,
                         ticker=ticker, company_name=company_name, source=source, url=url, image_url=image_url,
                         published=published)
    composed = f"[{tier}] {subject}\n\n{text}\n\nImportance : {tier} ({score}/100) — {reason}"
    sent = {}
    if telegram_configured():
        sent["telegram"] = send_telegram(composed)
    if discord_configured():
        sent["discord"] = send_discord(embed)
    try:
        db.add_alert(ticker, kind or "other", subject, composed, sent, importance_score=score, importance_tier=tier,
                     category=category, url=url, image_url=image_url)
    except Exception:
        log.exception("enregistrement de l'historique des alertes échoué")
    return sent
