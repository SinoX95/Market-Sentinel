import base64
import hashlib
import hmac
import logging
import mimetypes
import re
import os
import threading
from contextlib import asynccontextmanager
from pathlib import Path

import time

from fastapi import Body, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from backend import auth, service
from analysis import lang as _lang
from collectors import fx as _fx
from backend import db
from backend.db import init_schema

# Sans ceci, le logger "sentinel" (backend/service.py, backend/auth.py, backend/mailer.py) reste au
# niveau par défaut WARNING : tous les log.info(...) — notamment les décisions de scoring/filtrage des
# actualités (Module 11 du cahier des charges) — sont silencieusement ignorés avant même d'atteindre
# stderr, alors que Cloud Run capture bien stderr. Uvicorn configure ses propres loggers ("uvicorn",
# "uvicorn.access") mais jamais le logger racine, donc ce réglage ne lui fait pas concurrence.
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

STATIC = Path(__file__).parent / "static"

# Sous Windows, les types MIME viennent du registre et peuvent être faux (ex. .css en text/plain),
# ce qui fait ignorer la feuille de style : on les impose.
mimetypes.add_type("text/css", ".css")
mimetypes.add_type("text/javascript", ".js")
mimetypes.add_type("application/manifest+json", ".webmanifest")


DB = {"ready": False, "error": ""}


def _safe(msg):
    """Message d'erreur sans le mot de passe de la base."""
    try:
        from urllib.parse import urlparse
        pw = urlparse(os.environ.get("DATABASE_URL", "")).password
        if pw:
            msg = msg.replace(pw, "***")
    except Exception:
        pass
    return msg[:400]


def _migrate_auth():
    """Une seule fois, au premier démarrage de la version « accès sur invitation » : suppression des anciens comptes."""
    try:
        auth.migrate_once()
    except Exception:  # noqa: BLE001
        import logging
        logging.getLogger("sentinel").exception("migration des comptes")


def _init_db():
    """Connexion à la base en arrière-plan : le serveur écoute tout de suite (Cloud Run exige une réponse rapide) et affiche
    l'erreur exacte si la base est injoignable, au lieu de planter sans explication."""
    delay = 2
    while True:
        try:
            init_schema(retries=1)
            _migrate_auth()
            DB.update(ready=True, error="")
            break
        except Exception as e:  # noqa: BLE001
            DB["error"] = _safe(f"{type(e).__name__}: {e}")
            logging.getLogger("sentinel").warning("base de données indisponible : %s", DB["error"])  # détail dans les journaux seulement
            time.sleep(delay)
            delay = min(delay * 2, 30)
    if not service.SERVERLESS:
        service.start_background()  # collecte + scoring automatiques, sans rien lancer à la main


@asynccontextmanager
async def lifespan(app):
    if db.USE_PG or db.USE_FS:
        threading.Thread(target=_init_db, daemon=True).start()
    else:
        init_schema()
        _migrate_auth()
        DB["ready"] = True
        if not service.SERVERLESS:
            service.start_background()
    yield


app = FastAPI(title="Market Sentinel", lifespan=lifespan)


PUBLIC = {"/login", "/login/verify", "/invite/request", "/invite/check", "/invite/redeem", "/health", "/auth/hint",
          "/favicon.ico", "/internal/cycle",
          "/sw.js", "/static/manifest.webmanifest"}  # appli installable : le navigateur les lit sans cookie de connexion
PUBLIC_PREFIXES = ("/static/icons/",)  # icônes de l'appli (écran d'accueil, page de connexion)


@app.middleware("http")
async def guard(request: Request, call_next):
    """Tout est protégé par la connexion, sauf la page de connexion elle-même."""
    path = request.url.path
    if not auth.config()["enabled"]:
        auth.set_current(auth.config()["user"])  # authentification désactivée (usage local) : tout se passe sous le compte principal
        return await call_next(request)
    user, age = auth.verify_full(request.cookies.get(auth.COOKIE))
    auth.set_current(user)
    if user or path in PUBLIC or path.startswith(PUBLIC_PREFIXES):
        resp = await call_next(request)
        # session glissante : un jeton de plus d'un jour est réémis, l'utilisateur actif reste donc connecté
        if user and age > auth.RENEW_AFTER and not path.startswith(("/static/", "/logout")):
            auth.set_cookie(resp, user, request.url.scheme == "https")
        return resp
    if path.startswith("/api/"):
        return JSONResponse({"detail": "Non connecté"}, status_code=401)
    return RedirectResponse("/login", status_code=303)


def _prefs(request: Request):
    """Langue et devise de la requête : cookies posés à l'enregistrement du profil ; français et euro par défaut."""
    lg = request.headers.get("x-ms-lang") or request.cookies.get("ms_lang")
    if lg not in _lang.LANGS:
        lg = "fr"
    cur = request.headers.get("x-ms-cur") or request.cookies.get("ms_cur")
    if cur not in _fx.SUPPORTED:
        cur = "USD" if lg == "en" else "EUR"
    return lg, cur


@app.middleware("http")
async def prefs(request: Request, call_next):
    lg, cur = _prefs(request)
    _lang.set_context(lg, cur, service.stored_rates() if request.url.path.startswith("/api/") else None)
    return await call_next(request)


@app.middleware("http")
async def no_cache(request, call_next):
    resp = await call_next(request)
    p = request.url.path
    if p == "/" or p.startswith("/static/"):
        resp.headers["Cache-Control"] = "no-cache"  # toujours la dernière version après une mise à jour
    elif p.startswith(("/api/", "/login", "/logout", "/auth/", "/internal/")):
        resp.headers["Cache-Control"] = "private, no-store"  # données personnelles : jamais dans un cache partagé (CDN)
    return resp


@app.middleware("http")
async def db_ready(request: Request, call_next):
    # le détail de l'erreur reste dans les journaux du serveur : les visiteurs ne voient qu'un message générique
    if not DB["ready"] and request.url.path != "/health":
        if request.url.path.startswith("/api/"):
            return JSONResponse({"detail": "Base de données en cours de connexion, réessaie dans un instant."}, status_code=503)
        return HTMLResponse("<!doctype html><meta charset=utf-8><meta name=viewport content='width=device-width,initial-scale=1'><meta http-equiv=refresh content=5>"
                            "<body style='font:16px system-ui;max-width:560px;margin:12vh auto;padding:0 20px'><h2>Market Sentinel démarre…</h2>"
                            "<p>Connexion à la base de données en cours.</p><p style='color:#888'>Cette page se rafraîchit toute seule.</p>",
                            status_code=503)
    return await call_next(request)


def _inline_script_hashes():
    """Empreintes des petits scripts écrits directement dans index.html et login.html : seuls ces scripts-là (et ceux du
    site lui-même) ont le droit de s'exécuter. Calculées au démarrage, elles suivent automatiquement les modifications."""
    out = set()
    for name in ("index.html", "login.html"):
        try:
            text = (STATIC / name).read_text(encoding="utf-8")
        except OSError:
            continue
        for m in re.finditer(r"<script>(.*?)</script>", text, re.S):
            body = m.group(1).replace("\r\n", "\n").replace("\r", "\n")  # le navigateur lit les fins de ligne Windows comme \n
            out.add("'sha256-" + base64.b64encode(hashlib.sha256(body.encode("utf-8")).digest()).decode() + "'")
    return " ".join(sorted(out))


CSP = ("default-src 'self'; script-src 'self' " + _inline_script_hashes() + "; style-src 'self' 'unsafe-inline'; "
       "img-src 'self' data: https:; connect-src 'self'; font-src 'self' data:; manifest-src 'self'; worker-src 'self'; "
       "object-src 'none'; base-uri 'self'; form-action 'self'; frame-ancestors 'none'")


@app.middleware("http")
async def security_headers(request: Request, call_next):
    """En-têtes de sécurité sur toutes les réponses : pas d'intégration dans un autre site (anti-clickjacking), pas de
    script étranger (CSP), pas de devinette du type de fichier, connexion HTTPS imposée, aucune adresse transmise aux
    sites externes (logos), aucun accès à la caméra, au micro ou à la position."""
    resp = await call_next(request)
    h = resp.headers
    h.setdefault("X-Content-Type-Options", "nosniff")
    h.setdefault("X-Frame-Options", "DENY")
    h.setdefault("Referrer-Policy", "same-origin")
    h.setdefault("Cross-Origin-Opener-Policy", "same-origin")
    h.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=(), payment=(), usb=()")
    if request.url.scheme == "https":
        h.setdefault("Strict-Transport-Security", "max-age=31536000")
    if h.get("content-type", "").startswith("text/html"):
        h.setdefault("Content-Security-Policy", CSP)
    return resp


app.mount("/static", StaticFiles(directory=STATIC), name="static")


@app.get("/login")
def login_page(request: Request):
    if not auth.config()["enabled"] or auth.verify(request.cookies.get(auth.COOKIE)):
        return RedirectResponse("/", status_code=303)
    return FileResponse(STATIC / "login.html")


@app.get("/sw.js")
def service_worker():
    """Service worker de l'appli installable : servi à la racine (et non sous /static/) pour couvrir tout le site,
    jamais mis en cache pour que chaque mise à jour soit prise en compte."""
    return FileResponse(STATIC / "sw.js", media_type="text/javascript", headers={"Cache-Control": "no-cache"})


@app.get("/auth/hint")
def auth_hint():
    return {"locked": auth.config()["locked"]}


def _ip(request: Request):
    return request.client.host if request.client else "?"


def _auth_error(e):
    return JSONResponse({"detail": e.message, "code": e.code}, status_code=e.status)


def _signed_in(request: Request, user, extra=None):
    resp = JSONResponse({"ok": True, **(extra or {})})
    auth.set_cookie(resp, user, request.url.scheme == "https")
    return resp


@app.post("/login")
def login(request: Request, payload: dict = Body(...)):
    """Administrateur, étape 1 : email + mot de passe. S'ils sont bons, un code est envoyé par email (rien de plus)."""
    try:
        return auth.admin_login_start(payload.get("email"), payload.get("password"), _ip(request))
    except auth.AuthError as e:
        return _auth_error(e)


@app.post("/login/verify")
def login_verify(request: Request, payload: dict = Body(...)):
    """Administrateur, étape 2 : le code reçu par email ouvre la session."""
    try:
        return _signed_in(request, auth.admin_login_finish(payload.get("pid"), payload.get("code"), _ip(request)))
    except auth.AuthError as e:
        return _auth_error(e)


@app.post("/invite/request")
def invite_request(request: Request):
    """Invité : demande un code. Le code part chez l'administrateur ; la réponse ne contient que l'identifiant de la demande."""
    try:
        return auth.invite_request(_ip(request))
    except auth.AuthError as e:
        return _auth_error(e)


@app.post("/invite/check")
def invite_check(request: Request, payload: dict = Body(...)):
    """Invité : vérifie le code transmis par l'administrateur (sans le consommer) avant de demander le nom."""
    try:
        auth.invite_check(payload.get("rid"), payload.get("code"), _ip(request))
        return {"ok": True}
    except auth.AuthError as e:
        return _auth_error(e)


@app.post("/invite/redeem")
def invite_redeem(request: Request, payload: dict = Body(...)):
    """Invité : code + nom. Le code est consommé, le compte créé et la session ouverte (persistante)."""
    try:
        uid, name = auth.invite_redeem(payload.get("rid"), payload.get("code"), payload.get("name"), _ip(request))
    except auth.AuthError as e:
        return _auth_error(e)
    service.save_profile(uid, {"display_name": name})
    return _signed_in(request, uid)


@app.post("/logout")
def logout():
    resp = JSONResponse({"ok": True})
    resp.delete_cookie(auth.COOKIE, path="/")
    return resp


@app.get("/api/me")
def me():
    u = auth.current()
    return {"user": u, "admin": auth.is_admin(u), "auth": auth.config()["enabled"], "profile": service.get_profile(u)}


def _prefs_response(payload, profile):
    resp = JSONResponse(payload)
    for k, v in (("ms_lang", profile["language"]), ("ms_cur", profile["currency"])):
        resp.set_cookie(k, v, max_age=365 * 24 * 3600, samesite="lax", httponly=False)
    return resp


@app.get("/api/profile")
def profile_get():
    return service.build_me(auth.current())


@app.post("/api/profile")
def profile_save(payload: dict = Body(...)):
    """Nom affiché, langue, devise (et email de contact) ; langue et devise sont aussi posées en cookies."""
    try:
        if payload.get("contact_email") is not None:
            _need_admin()
        p = service.save_profile(auth.current(), payload)
        if payload.get("contact_email") is not None:
            service.save_settings(payload["contact_email"], None)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return _prefs_response({"ok": True, "profile": p}, p)


def _need_admin():
    if not auth.is_admin():
        raise HTTPException(403, "Réservé à l'administrateur")


@app.get("/api/users")
def users_list():
    _need_admin()
    return {"users": auth.list_users()}


@app.delete("/api/users/{name}")
def users_delete(name: str):
    _need_admin()
    try:
        auth.delete_user(name)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"users": auth.list_users()}


@app.post("/api/password")
def password_change(payload: dict = Body(...)):
    _need_admin()  # les invités n'ont pas de mot de passe
    try:
        return service.change_password(str(payload.get("current", "")), str(payload.get("new", "")))
    except ValueError as e:
        raise HTTPException(400, str(e))


@app.get("/api/fx")
def fx_rates():
    return service.build_fx()


@app.get("/")
def index():
    resp = FileResponse(STATIC / "index.html")
    saved = db.kv_get(f"profile:{auth.current()}")[0]
    if saved:  # langue et devise du profil, valables sur tous les appareils
        for k, v in (("ms_lang", saved.get("language", "fr")), ("ms_cur", saved.get("currency", "EUR"))):
            resp.set_cookie(k, v, max_age=365 * 24 * 3600, samesite="lax")
    return resp


@app.get("/api/dashboard")
def dashboard():
    return service.build_dashboard()


@app.get("/api/opportunities")
def opportunities():
    return service.build_opportunities()


@app.get("/api/investors")
def investors():
    return service.build_investors()


@app.get("/api/alerts")
def alerts(ticker: str = None, limit: int = 100):
    """Historique des alertes envoyées (Module G), la plus récente en premier — importance (score/palier,
    Module E) incluse. `limit` est borné pour éviter qu'un appel mal formé ne charge tout l'historique."""
    return db.recent_alerts(limit=min(limit, 500), ticker=ticker)


@app.get("/api/missed-opportunities")
def missed_opportunities(days: int = service.MISSED_MOVES_DAYS):
    """Audit (Module K) : mouvements de clôture au-delà du seuil réglé pour lesquels aucune alerte n'a été
    trouvée dans l'historique sur les `days` derniers jours — voir service.find_missed_price_moves pour la
    méthode et ses limites. Un résultat vide est une bonne nouvelle, pas une absence de données."""
    return service.find_missed_price_moves(days=min(days, 365))


@app.get("/api/company/{ticker}")
def company(ticker: str, refresh: bool = False):
    d = service.build_company(ticker, force=refresh)
    if d is None:
        raise HTTPException(400, "Symbole invalide")
    return d


@app.get("/api/company/{ticker}/candles")
def company_candles(ticker: str, range: str = "month", fresh: bool = False):
    d = service.build_candles(ticker, range, fresh=fresh)
    if d is None:
        raise HTTPException(400, "Symbole ou période invalide")
    return d


@app.post("/api/company/{ticker}/refresh")
def company_refresh(ticker: str, part: str):
    """Bouton « Actualiser » : un morceau de la fiche à la fois, lancés en parallèle par la page."""
    d = service.refresh_company_part(ticker, part)
    if d is None:
        raise HTTPException(400, "Symbole ou partie invalide")
    return d


@app.get("/api/search")
def search(q: str = ""):
    return service.do_search(q)


@app.get("/api/sectors")
def sectors():
    return service.build_sectors()


@app.post("/api/watchlist/{ticker}")
def follow(ticker: str, name: str = ""):
    try:
        return service.add_to_watchlist(ticker, name or None)
    except ValueError as e:
        raise HTTPException(400, str(e))


@app.delete("/api/watchlist/{ticker}")
def unfollow(ticker: str):
    return service.remove_from_watchlist(ticker)


@app.get("/api/sources")
def sources():
    return service.build_sources()


@app.post("/api/settings")
def settings_save(payload: dict = Body(...)):
    _need_admin()  # clés d'API et email SEC : partagés par tous, modifiables par l'administrateur seulement
    try:
        return service.save_settings(payload.get("contact_email"), payload.get("api_keys"))
    except ValueError as e:
        raise HTTPException(400, str(e))


@app.get("/api/sector/{sid}")
def sector(sid: str, refresh: bool = False):
    d = service.build_sector(sid, force=refresh)
    if d is None:
        raise HTTPException(404, "Secteur inconnu")
    return d


@app.get("/api/compare-best/{ticker}")
def compare_best(ticker: str, with_: str = ""):
    return service.build_compare_best(ticker, [x.strip().upper() for x in with_.split(",") if x.strip()] or None)


@app.get("/api/live")
def live():
    return service.live_prices()


@app.get("/api/compare")
def compare(tickers: str = ""):
    return service.build_compare([t.strip().upper() for t in tickers.split(",") if t.strip()])


@app.post("/internal/cycle")
def internal_cycle(request: Request):
    """Collecte complète, déclenchée par Cloud Scheduler (en-tête X-Cron-Secret = variable CRON_SECRET)."""
    expected = os.getenv("CRON_SECRET", "")
    if not expected:
        raise HTTPException(503, "CRON_SECRET non défini sur le serveur")
    if not hmac.compare_digest(request.headers.get("x-cron-secret", "").encode(), expected.encode()):
        raise HTTPException(403, "Secret invalide")
    n = service.run_budget(45)  # travail progressif : les unités les plus en retard d'abord
    return {"ok": True, "units": n, "last_run": service.status()["last_run"], "problems": service.status()["problems"]}


_last_refresh = [0.0]


@app.post("/api/refresh")
def refresh():
    # une relance de collecte au plus par minute : sinon n'importe quel compte pourrait la déclencher en boucle (coût, blocage des sources)
    if time.time() - _last_refresh[0] < 60:
        return {"started": False, "recent": True}
    _last_refresh[0] = time.time()
    from collectors import health
    health.resume()  # l'utilisateur relance : on lève les pauses automatiques
    if service.SERVERLESS:
        service.run_cycle(force=False, light=True)  # synchrone et léger : le CPU est coupé après la réponse
        return {"started": True}
    threading.Thread(target=service.run_cycle, kwargs={"force": True}, daemon=True).start()
    return {"started": True}


@app.get("/health")
def health():
    return {"status": "ok", "db": DB["ready"]}  # sans détail d'erreur : cette adresse est publique
