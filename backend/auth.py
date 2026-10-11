"""Comptes, connexion et sessions.

- Administrateur : un seul compte, le seul à avoir les droits d'administration. Connexion en deux temps : email +
  mot de passe (config/settings.json, ou CONTACT_EMAIL / LOGIN_PASSWORD en cloud), puis un code à 6 chiffres envoyé
  par email (valable 10 minutes, à usage unique, 5 essais).
- Invités : aucune inscription libre. Depuis la page de connexion, un visiteur demande un code ; ce code part
  uniquement sur la boîte mail de l'administrateur, qui le transmet (ou non) à la personne. Demander un code ne donne
  donc accès à rien. Le code est aléatoire, valable 24 h, à usage unique, limité à 5 essais et ne fonctionne que sur
  le navigateur qui l'a demandé. Avec un code valide, la personne indique juste son nom : son compte est créé, sans
  email ni mot de passe, et elle reste connectée.
- Session : cookie signé « __session » (HMAC, HttpOnly), renouvelé à l'usage : un invité actif ne se reconnecte
  jamais ; la session expire après 1 an d'inactivité (30 jours pour l'administrateur).
"""
import base64
import contextvars
import hashlib
import hmac
import logging
import os
import re
import secrets
import threading
import time

from backend import db, mailer
from collectors.http import contact_email, settings

log = logging.getLogger("sentinel")

# Firebase Hosting ne transmet à Cloud Run que le cookie nommé « __session » : on l'utilise partout.
COOKIE = "__session"
PRODUCTION = bool(os.getenv("K_SERVICE") or os.getenv("PUBLIC_DEPLOY"))
ADMIN_TTL = 30 * 24 * 3600
GUEST_TTL = 365 * 24 * 3600
RENEW_AFTER = 24 * 3600          # un cookie de plus d'un jour est réémis à la visite suivante (session glissante)
MFA_TTL = 10 * 60                # code de connexion administrateur
INVITE_TTL = 24 * 3600           # code d'invitation
MAX_TRIES = 5                    # essais par code, après quoi le code est détruit
MAX_USERS = 300
NAME_RE = re.compile(r"^\w[\w .'\-]{0,39}$")  # nom d'un invité : lettres (accents compris), chiffres, espaces, . ' -
_ulock = threading.Lock()
_current = contextvars.ContextVar("current_user", default=None)


class AuthError(Exception):
    def __init__(self, status, message, code):
        super().__init__(message)
        self.status, self.message, self.code = status, message, code


def current():
    return _current.get()


def set_current(user):
    _current.set(user)


def norm(name):
    return str(name or "").strip().lower()


def config():
    s = settings()
    user, password = str(s.get("login_user", "test")), str(s.get("login_password", "test"))
    weak = (user == "test" and password == "test") or len(password) < 8
    # En déploiement public, la connexion est toujours exigée et refusée pour l'administrateur tant que ses identifiants ne sont pas réglés.
    return {"enabled": PRODUCTION or s.get("auth_enabled", True) is not False, "user": user, "password": password,
            "locked": PRODUCTION and weak}


def admin_email():
    return contact_email()


# ---------- limites de fréquence (par instance du serveur) ----------
_hits = {}
_hlock = threading.Lock()


def _limited(bucket, key, n, window):
    now = time.time()
    with _hlock:
        k = (bucket, key)
        _hits[k] = [t for t in _hits.get(k, []) if now - t < window]
        return len(_hits[k]) >= n


def _hit(bucket, key):
    with _hlock:
        _hits.setdefault((bucket, key), []).append(time.time())


# ---------- comptes ----------
def _users(fresh=False):
    return (db.kv_get_fresh("users") if fresh else db.kv_get("users")[0]) or {}


def find(name):
    """Nom canonique d'un compte existant (administrateur ou invité), sinon None."""
    n = norm(name)
    if not n:
        return None
    if n == norm(config()["user"]):
        return config()["user"]
    u = _users().get(n)
    if u is None and n.startswith("g-"):
        u = _users(fresh=True).get(n)  # compte créé il y a quelques secondes sur une autre instance du serveur
    return u["name"] if u else None


def is_admin(name=None):
    """Seul le compte administrateur principal a les droits d'administration."""
    name = name if name is not None else current()
    return bool(norm(name)) and norm(name) == norm(config()["user"])


def list_users():
    admin = config()["user"]
    out = [{"name": admin, "display": admin, "role": "admin", "owner": True}]
    out += [{"name": u["name"], "display": u.get("display") or u["name"], "role": "user", "created": u.get("created")}
            for u in sorted(_users().values(), key=lambda u: (u.get("created") or "", norm(u.get("display"))))]
    return out


def delete_user(name):
    with _ulock:
        users = _users(fresh=True)
        if norm(name) not in users:
            raise ValueError("compte introuvable (le compte administrateur ne peut pas être supprimé)")
        users.pop(norm(name))
        db.kv_set("users", users)


def clean_name(name):
    name = re.sub(r"\s+", " ", str(name or "")).strip()
    if not NAME_RE.match(name):
        raise AuthError(400, "Nom invalide : 1 à 40 caractères (lettres, chiffres, espaces, . ' -).", "bad_name")
    return name


def _create_guest(display):
    with _ulock:
        users = _users(fresh=True)
        if len(users) >= MAX_USERS:
            raise AuthError(403, "Le site a atteint son nombre maximal de comptes.", "full")
        uid = "g-" + secrets.token_hex(6)  # identifiant interne : deux invités peuvent porter le même nom
        users[uid] = {"name": uid, "display": display, "role": "user", "created": time.strftime("%Y-%m-%d")}
        db.kv_set("users", users)
    return uid


def migrate_once():
    """Passage à l'accès sur invitation : supprime une seule fois tous les anciens comptes (l'administrateur, qui n'est
    pas enregistré dans cette liste, est conservé). Le jeton n'est écrit qu'une fois, même avec plusieurs instances."""
    tok = secrets.token_hex(8)
    if db.kv_setnx("auth:migration:invites-v1", tok) != tok:
        return 0
    old = _users(fresh=True)
    db.kv_set("users", {})
    log.warning("accès sur invitation : %d ancien(s) compte(s) supprimé(s)", len(old))
    return len(old)


# ---------- codes à 6 chiffres ----------
def _digest(kind, rid, code):
    return hmac.new(_secret(), f"{kind}|{rid}|{code}".encode(), hashlib.sha256).hexdigest()


def _new_code(kind, ttl):
    """Crée une demande et son code ; seule l'empreinte du code est enregistrée, jamais le code lui-même."""
    rid, code = secrets.token_urlsafe(24), f"{secrets.randbelow(10 ** 6):06d}"
    db.kv_set(f"auth:{kind}:{rid}", {"h": _digest(kind, rid, code), "exp": time.time() + ttl, "tries": 0})
    return rid, code


def _check_code(kind, rid, code, ip, consume):
    if _limited("code", ip, 15, 900):
        raise AuthError(429, "Trop d'essais : réessaie dans quelques minutes.", "rate")
    rid, code = str(rid or "")[:64], re.sub(r"\D", "", str(code or ""))
    key = f"auth:{kind}:{rid}"
    rec = db.kv_get_fresh(key) if re.fullmatch(r"[\w\-]{20,64}", rid) else None
    if not rec or rec.get("exp", 0) < time.time():
        raise AuthError(400, "Code expiré ou déjà utilisé : demande un nouveau code.", "expired")
    if len(code) != 6 or not hmac.compare_digest(rec["h"], _digest(kind, rid, code)):
        _hit("code", ip)
        rec["tries"] = rec.get("tries", 0) + 1
        if rec["tries"] >= MAX_TRIES:
            db.kv_delete(key)
            raise AuthError(400, "Trop d'erreurs : ce code n'est plus valable, demande un nouveau code.", "expired")
        db.kv_set(key, rec)
        left = MAX_TRIES - rec["tries"]
        raise AuthError(400, f"Code incorrect ({left} essai{'s' if left > 1 else ''} restant{'s' if left > 1 else ''}).", "bad_code")
    if consume:
        tok = secrets.token_hex(8)
        if db.kv_setnx(f"auth:used:{kind}:{rid}", tok) != tok:  # atomique : si deux requêtes arrivent ensemble, une seule passe
            raise AuthError(400, "Code expiré ou déjà utilisé : demande un nouveau code.", "expired")
        db.kv_delete(key)


def _deliver(subject, body, code, what):
    if mailer.send(admin_email(), subject, body):
        return True
    # pas d'envoi possible (SMTP non configuré) : le code n'est écrit que dans les journaux du serveur, lisibles par le
    # seul propriétaire du projet Google Cloud ; il n'est jamais renvoyé au navigateur
    log.warning("%s (email non envoyé, SMTP non configuré) : %s", what, code)
    return False


# ---------- administrateur : email + mot de passe, puis code par email ----------
def admin_password_ok(password):
    c = config()
    return not c["locked"] and hmac.compare_digest(str(password or "").encode(), c["password"].encode())


def _check_admin(ident, password):
    email, ident = admin_email(), norm(ident)
    who_ok = (ident == norm(email)) if email else (ident == norm(config()["user"]))  # sans email réglé : identifiant (usage local)
    return admin_password_ok(password) and who_ok


def admin_login_start(ident, password, ip):
    if _limited("pw", ip, 8, 300) or _limited("pw", "*", 50, 3600):
        raise AuthError(429, "Trop d'essais : réessaie dans quelques minutes.", "rate")
    if config()["locked"]:
        raise AuthError(403, "Connexion désactivée : définis LOGIN_PASSWORD (8 caractères minimum) sur le serveur.", "locked")
    if not _check_admin(ident, password):
        _hit("pw", ip)
        _hit("pw", "*")
        time.sleep(0.6)  # ralentit les essais en série
        raise AuthError(401, "Email ou mot de passe incorrect.", "bad_credentials")
    if _limited("mfa", ip, 5, 900) or _limited("mfa", "*", 20, 3600):
        raise AuthError(429, "Trop de codes envoyés : réessaie dans quelques minutes.", "rate")
    _hit("mfa", ip)
    _hit("mfa", "*")
    pid, code = _new_code("mfa", MFA_TTL)
    sent = _deliver("Market Sentinel : ton code de connexion",
                    f"Ton code de connexion administrateur : {code}\n\n"
                    "Il est valable 10 minutes et ne sert qu'une fois.\n\n"
                    "Si tu n'es pas à l'origine de cette connexion, quelqu'un connaît ton mot de passe : change-le.",
                    code, "code de connexion administrateur")
    return {"pid": pid, "sent": sent}


def admin_login_finish(pid, code, ip):
    _check_code("mfa", pid, code, ip, consume=True)
    return config()["user"]


# ---------- invités : code demandé ici, envoyé à l'administrateur, transmis par lui ----------
def invite_request(ip):
    if _limited("inv", ip, 3, 3600) or _limited("inv", "*", 20, 24 * 3600):
        raise AuthError(429, "Trop de demandes de code : réessaie plus tard.", "rate")
    _hit("inv", ip)
    _hit("inv", "*")
    rid, code = _new_code("inv", INVITE_TTL)
    sent = _deliver("Market Sentinel : demande d'accès",
                    f"Quelqu'un demande à accéder à Market Sentinel.\n\nCode d'invitation : {code}\n\n"
                    "Pour lui donner accès, transmets-lui ce code. Il est valable 24 heures, ne sert qu'une fois et ne "
                    "fonctionne que sur l'appareil qui l'a demandé.\n\n"
                    "Si tu ne sais pas qui a fait cette demande, ignore simplement cet email : sans ce code, personne ne peut entrer.\n\n"
                    f"(Demande faite le {time.strftime('%d/%m/%Y à %H:%M')} UTC depuis l'adresse {ip}.)",
                    code, "code d'invitation")
    return {"rid": rid, "sent": sent}


def invite_check(rid, code, ip):
    _check_code("inv", rid, code, ip, consume=False)


def invite_redeem(rid, code, name, ip):
    name = clean_name(name)
    _check_code("inv", rid, code, ip, consume=True)
    return _create_guest(name), name


# ---------- sessions ----------
_key = []


def _secret():
    if not _key:
        # « v2 » : nouvelle clé au passage à la double authentification, ce qui déconnecte toutes les anciennes sessions
        data = db.kv_setnx("auth:secret:v2", secrets.token_hex(32))  # un seul secret, partagé par toutes les instances
        _key.append(data.encode())
    return _key[0]


def ttl(user):
    return ADMIN_TTL if is_admin(user) else GUEST_TTL


def make_token(user):
    payload = base64.urlsafe_b64encode(f"{user}|{int(time.time())}".encode()).decode()
    return payload + "." + hmac.new(_secret(), payload.encode(), hashlib.sha256).hexdigest()


def verify_full(token):
    """(nom canonique, âge du jeton en secondes) si la session est valable et le compte existe encore, sinon (None, 0)."""
    if not token or "." not in token or len(token) > 400:
        return None, 0
    payload, sig = token.rsplit(".", 1)
    if not hmac.compare_digest(sig, hmac.new(_secret(), payload.encode(), hashlib.sha256).hexdigest()):
        return None, 0
    try:
        user, ts = base64.urlsafe_b64decode(payload.encode()).decode().rsplit("|", 1)
        age = time.time() - int(ts)
        who = find(user)
        return (who, age) if who and 0 <= age < ttl(who) else (None, 0)
    except Exception:
        return None, 0


def verify(token):
    return verify_full(token)[0]


def set_cookie(resp, user, secure):
    resp.set_cookie(COOKIE, make_token(user), httponly=True, samesite="lax", max_age=ttl(user), secure=secure, path="/")
