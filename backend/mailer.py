"""Envoi d'emails (codes de connexion administrateur et codes d'invitation), par SMTP.

Par défaut le serveur d'envoi de Gmail : il suffit d'un « mot de passe d'application » Google (variable SMTP_PASSWORD,
ou smtp_password dans config/settings.json en local). L'expéditeur est SMTP_USER, à défaut l'email de l'administrateur.
"""
import logging
import smtplib
import ssl
from email.message import EmailMessage
from email.utils import formataddr

from collectors.http import contact_email, settings

log = logging.getLogger("sentinel")


def _cfg():
    s = settings()
    user = (s.get("smtp_user") or contact_email() or "").strip()
    return {"host": (s.get("smtp_host") or "smtp.gmail.com").strip(), "port": int(s.get("smtp_port") or 465),
            "user": user, "password": str(s.get("smtp_password") or "").replace(" ", "")}  # Google affiche le mot de passe par groupes de 4


def configured():
    c = _cfg()
    return bool(c["user"] and c["password"])


def send(to, subject, body):
    """True si l'email est parti. Jamais d'exception : l'appelant décide quoi faire en cas d'échec."""
    c = _cfg()
    if not (to and configured()):
        return False
    msg = EmailMessage()
    msg["From"], msg["To"], msg["Subject"] = formataddr(("Market Sentinel", c["user"])), to, subject
    msg.set_content(body)
    try:
        if c["port"] == 465:
            with smtplib.SMTP_SSL(c["host"], c["port"], context=ssl.create_default_context(), timeout=15) as s:
                s.login(c["user"], c["password"])
                s.send_message(msg)
        else:
            with smtplib.SMTP(c["host"], c["port"], timeout=15) as s:
                s.starttls(context=ssl.create_default_context())
                s.login(c["user"], c["password"])
                s.send_message(msg)
        return True
    except Exception as e:  # noqa: BLE001
        log.error("envoi d'email impossible (%s) : %s", c["host"], type(e).__name__)
        return False
