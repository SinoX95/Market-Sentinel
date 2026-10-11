"""Accès aux données. Deux moteurs, même interface :
 - PostgreSQL si la variable DATABASE_URL est définie (mode Docker)
 - SQLite (fichier data/sentinel.db) sinon (mode sans Docker)
"""
import functools
import json
import os
import sqlite3
import threading
import time
from contextlib import closing
from datetime import date, datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PG_SCHEMA = ROOT / "infrastructure" / "sql" / "init.sql"
SQLITE_PATH = Path(os.getenv("SQLITE_PATH", ROOT / "data" / "sentinel.db"))
# Firestore : choisi explicitement (DB_BACKEND=firestore) ou automatiquement sur Cloud Run quand aucune base Postgres n'est fournie.
USE_FS = os.getenv("DB_BACKEND", "").lower() == "firestore" or (bool(os.getenv("K_SERVICE")) and not os.getenv("DATABASE_URL") and os.getenv("DB_BACKEND", "").lower() != "sqlite")
USE_PG = bool(os.getenv("DATABASE_URL")) and not USE_FS

SQLITE_SCHEMA = """
CREATE TABLE IF NOT EXISTS prices (
    ticker TEXT NOT NULL, day TEXT NOT NULL, close REAL NOT NULL,
    PRIMARY KEY (ticker, day));
CREATE TABLE IF NOT EXISTS recommendations (
    id INTEGER PRIMARY KEY AUTOINCREMENT, ticker TEXT NOT NULL, score REAL NOT NULL,
    label TEXT NOT NULL, price_at REAL, created_at TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS idx_reco_ticker ON recommendations (ticker, created_at);
CREATE TABLE IF NOT EXISTS kv (
    key TEXT PRIMARY KEY, value TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS alerts (
    id INTEGER PRIMARY KEY AUTOINCREMENT, ticker TEXT, kind TEXT NOT NULL, subject TEXT NOT NULL,
    text TEXT NOT NULL, channels TEXT NOT NULL, importance_score REAL, importance_tier TEXT,
    category TEXT, url TEXT, image_url TEXT, created_at TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS idx_alerts_created ON alerts (created_at);
CREATE INDEX IF NOT EXISTS idx_alerts_ticker ON alerts (ticker, created_at);
"""
# Colonnes ajoutées après la création initiale de la table (Module 2/11) : CREATE TABLE IF NOT EXISTS ne
# modifie pas une table déjà existante en production, d'où cette migration explicite, idempotente (SQLite
# 3.35+ supporte ADD COLUMN IF NOT EXISTS). Sans objet pour Firestore (schéma libre, voir backend/fsdb.py)
# ni pour une base Postgres fraîchement créée (voir infrastructure/sql/init.sql, qui porte la même migration).
_SQLITE_MIGRATIONS = (
    "ALTER TABLE alerts ADD COLUMN IF NOT EXISTS category TEXT",
    "ALTER TABLE alerts ADD COLUMN IF NOT EXISTS url TEXT",
    "ALTER TABLE alerts ADD COLUMN IF NOT EXISTS image_url TEXT",
)


_tl = threading.local()


class _Conn:
    """Connexion PostgreSQL réutilisée par thread (une connexion TLS vers une base distante coûte cher à ouvrir)."""

    def __enter__(self):
        import psycopg
        conn = getattr(_tl, "conn", None)
        if conn is None or conn.closed or conn.broken:
            conn = psycopg.connect(os.environ["DATABASE_URL"], autocommit=True, connect_timeout=10)
            _tl.conn = conn
        return conn

    def __exit__(self, *exc):
        return False


def _pg():
    return _Conn()


def _drop_conn():
    conn = getattr(_tl, "conn", None)
    _tl.conn = None
    try:
        if conn is not None:
            conn.close()
    except Exception:
        pass


def _resilient(fn):
    """Si la connexion PostgreSQL a été coupée (base en veille, réseau), on la rouvre et on réessaie une fois."""
    @functools.wraps(fn)
    def wrapper(*a, **k):
        if not USE_PG:
            return fn(*a, **k)
        import psycopg
        try:
            return fn(*a, **k)
        except (psycopg.OperationalError, psycopg.InterfaceError):
            _drop_conn()
            return fn(*a, **k)
    return wrapper


def _lite():
    SQLITE_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(SQLITE_PATH, timeout=30)
    return closing(conn), conn


def init_schema(retries=30):
    if USE_PG:
        import psycopg
        last = None
        for i in range(retries):
            try:
                with _pg() as conn:
                    conn.execute(PG_SCHEMA.read_text())
                return
            except psycopg.OperationalError as e:
                last = e
                _drop_conn()
                if i < retries - 1:
                    time.sleep(2)
        raise RuntimeError(f"Base de données injoignable ({str(last).strip()[:250]})")
    ctx, conn = _lite()
    with ctx:
        conn.executescript(SQLITE_SCHEMA)
        for stmt in _SQLITE_MIGRATIONS:
            conn.execute(stmt)
        conn.commit()


@_resilient
def upsert_prices(ticker, rows):
    """rows : liste de (date, close)."""
    if USE_PG:
        with _pg() as conn:
            for d, c in rows:
                conn.execute(
                    "INSERT INTO prices (ticker, day, close) VALUES (%s,%s,%s) "
                    "ON CONFLICT (ticker, day) DO UPDATE SET close = EXCLUDED.close", (ticker, d, c))
        return
    ctx, conn = _lite()
    with ctx:
        conn.executemany(
            "INSERT INTO prices (ticker, day, close) VALUES (?,?,?) "
            "ON CONFLICT (ticker, day) DO UPDATE SET close = excluded.close",
            [(ticker, d.isoformat(), c) for d, c in rows])
        conn.commit()


@_resilient
def load_closes(tickers):
    out = {t: [] for t in tickers}
    if USE_PG:
        with _pg() as conn:
            rows = conn.execute(
                "SELECT ticker, day, close FROM prices WHERE ticker = ANY(%s) ORDER BY ticker, day",
                (list(tickers),)).fetchall()
        for t, d, c in rows:
            out[t].append((d, float(c)))
        return out
    ctx, conn = _lite()
    with ctx:
        rows = conn.execute("SELECT ticker, day, close FROM prices ORDER BY ticker, day").fetchall()
    for t, d, c in rows:
        if t in out:
            out[t].append((date.fromisoformat(d), float(c)))
    return out


@_resilient
def last_label(ticker):
    if USE_PG:
        with _pg() as conn:
            r = conn.execute("SELECT label FROM recommendations WHERE ticker=%s "
                             "ORDER BY created_at DESC LIMIT 1", (ticker,)).fetchone()
        return r[0] if r else None
    ctx, conn = _lite()
    with ctx:
        r = conn.execute("SELECT label FROM recommendations WHERE ticker=? "
                         "ORDER BY created_at DESC, id DESC LIMIT 1", (ticker,)).fetchone()
    return r[0] if r else None


@_resilient
def add_reco(ticker, score, label, price_at):
    if USE_PG:
        with _pg() as conn:
            conn.execute("INSERT INTO recommendations (ticker, score, label, price_at) "
                         "VALUES (%s,%s,%s,%s)", (ticker, score, label, price_at))
        return
    ctx, conn = _lite()
    with ctx:
        conn.execute("INSERT INTO recommendations (ticker, score, label, price_at, created_at) "
                     "VALUES (?,?,?,?,?)",
                     (ticker, score, label, price_at, datetime.now(timezone.utc).isoformat()))
        conn.commit()


@_resilient
def latest_recos():
    """Dernière recommandation par ticker : {ticker: (label, score, price_at, created_at)}."""
    if USE_PG:
        with _pg() as conn:
            rows = conn.execute(
                "SELECT DISTINCT ON (ticker) ticker, label, score, price_at, created_at "
                "FROM recommendations ORDER BY ticker, created_at DESC").fetchall()
        return {r[0]: (r[1], float(r[2]), float(r[3]), r[4]) for r in rows}
    ctx, conn = _lite()
    with ctx:
        rows = conn.execute(
            "SELECT r.ticker, r.label, r.score, r.price_at, r.created_at FROM recommendations r "
            "WHERE r.id = (SELECT MAX(id) FROM recommendations WHERE ticker = r.ticker)").fetchall()
    return {r[0]: (r[1], float(r[2]), float(r[3]), datetime.fromisoformat(r[4])) for r in rows}


@_resilient
def reco_history(ticker):
    """Historique des recommandations : [(label, score, price_at, created_at)] du plus ancien au plus récent."""
    if USE_PG:
        with _pg() as conn:
            rows = conn.execute("SELECT label, score, price_at, created_at FROM recommendations "
                                "WHERE ticker=%s ORDER BY created_at", (ticker,)).fetchall()
        return [(r[0], float(r[1]), float(r[2]), r[3]) for r in rows]
    ctx, conn = _lite()
    with ctx:
        rows = conn.execute("SELECT label, score, price_at, created_at FROM recommendations "
                            "WHERE ticker=? ORDER BY id", (ticker,)).fetchall()
    return [(r[0], r[1], r[2], datetime.fromisoformat(r[3])) for r in rows]


@_resilient
def add_alert(ticker, kind, subject, text, channels, importance_score=None, importance_tier=None,
             category=None, url=None, image_url=None):
    """Journalise une alerte envoyée (Module G), que des canaux Telegram/email soient configurés ou non —
    l'historique reste utile pour vérifier après coup qu'une alerte aurait dû partir. importance_score/tier
    (Module E — voir alerts/importance.py) sont optionnels : une alerte sans règle de score connue reste
    journalisée quand même plutôt que d'échouer. category/url/image_url (Module 2/11) sont optionnels pour
    la même raison (un mouvement de prix n'a ni source ni image, par exemple)."""
    channels_json = json.dumps(channels or {}, ensure_ascii=False)
    if USE_PG:
        with _pg() as conn:
            conn.execute("INSERT INTO alerts (ticker, kind, subject, text, channels, importance_score, "
                         "importance_tier, category, url, image_url) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                         (ticker, kind, subject, text, channels_json, importance_score, importance_tier,
                          category, url, image_url))
        return
    ctx, conn = _lite()
    with ctx:
        conn.execute("INSERT INTO alerts (ticker, kind, subject, text, channels, importance_score, "
                     "importance_tier, category, url, image_url, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                     (ticker, kind, subject, text, channels_json, importance_score, importance_tier,
                      category, url, image_url, datetime.now(timezone.utc).isoformat()))
        conn.commit()


@_resilient
def recent_alerts(limit=100, ticker=None):
    """Alertes les plus récentes, du plus récent au plus ancien :
    [{ticker, kind, subject, text, channels, importance_score, importance_tier, category, url, image_url,
    created_at}]."""
    cols = "ticker, kind, subject, text, channels, importance_score, importance_tier, category, url, image_url, created_at"
    if USE_PG:
        with _pg() as conn:
            if ticker:
                rows = conn.execute(f"SELECT {cols} FROM alerts WHERE ticker=%s ORDER BY created_at DESC LIMIT %s",
                                    (ticker, limit)).fetchall()
            else:
                rows = conn.execute(f"SELECT {cols} FROM alerts ORDER BY created_at DESC LIMIT %s", (limit,)).fetchall()
        return [{"ticker": r[0], "kind": r[1], "subject": r[2], "text": r[3], "channels": json.loads(r[4]),
                 "importance_score": r[5], "importance_tier": r[6], "category": r[7], "url": r[8],
                 "image_url": r[9], "created_at": r[10]} for r in rows]
    ctx, conn = _lite()
    with ctx:
        if ticker:
            rows = conn.execute(f"SELECT {cols} FROM alerts WHERE ticker=? ORDER BY id DESC LIMIT ?",
                                (ticker, limit)).fetchall()
        else:
            rows = conn.execute(f"SELECT {cols} FROM alerts ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
    return [{"ticker": r[0], "kind": r[1], "subject": r[2], "text": r[3], "channels": json.loads(r[4]),
             "importance_score": r[5], "importance_tier": r[6], "category": r[7], "url": r[8], "image_url": r[9],
             "created_at": datetime.fromisoformat(r[10])} for r in rows]


@_resilient
def kv_set(key, obj):
    payload = json.dumps(obj, ensure_ascii=False)
    if USE_PG:
        with _pg() as conn:
            conn.execute("INSERT INTO kv (key, value, updated_at) VALUES (%s,%s,now()) "
                         "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = now()",
                         (key, payload))
        return
    ctx, conn = _lite()
    with ctx:
        conn.execute("INSERT INTO kv (key, value, updated_at) VALUES (?,?,?) "
                     "ON CONFLICT (key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at",
                     (key, payload, datetime.now(timezone.utc).isoformat()))
        conn.commit()


@_resilient
def kv_get(key):
    """Retourne (objet, date de mise à jour) ou (None, None)."""
    if USE_PG:
        with _pg() as conn:
            r = conn.execute("SELECT value, updated_at FROM kv WHERE key=%s", (key,)).fetchone()
        return (json.loads(r[0]), r[1]) if r else (None, None)
    ctx, conn = _lite()
    with ctx:
        r = conn.execute("SELECT value, updated_at FROM kv WHERE key=?", (key,)).fetchone()
    return (json.loads(r[0]), datetime.fromisoformat(r[1])) if r else (None, None)


@_resilient
def kv_setnx(key, obj):
    """Enregistre obj si la clé est absente ; retourne la valeur stockée (partagée entre instances). Atomique : si
    deux requêtes essaient en même temps, une seule écrit (sert aussi à garantir qu'un code ne sert qu'une fois)."""
    payload = json.dumps(obj, ensure_ascii=False)
    if USE_PG:
        with _pg() as conn:
            conn.execute("INSERT INTO kv (key, value, updated_at) VALUES (%s,%s,now()) ON CONFLICT (key) DO NOTHING", (key, payload))
    else:
        ctx, conn = _lite()
        with ctx:
            conn.execute("INSERT INTO kv (key, value, updated_at) VALUES (?,?,?) ON CONFLICT (key) DO NOTHING",
                         (key, payload, datetime.now(timezone.utc).isoformat()))
            conn.commit()
    return kv_get(key)[0]


def kv_get_fresh(key):
    """Lecture directe en base (pas de cache sur ces moteurs) : même interface que la version Firestore."""
    return kv_get(key)[0]


@_resilient
def kv_delete(key):
    if USE_PG:
        with _pg() as conn:
            conn.execute("DELETE FROM kv WHERE key=%s", (key,))
        return
    ctx, conn = _lite()
    with ctx:
        conn.execute("DELETE FROM kv WHERE key=?", (key,))
        conn.commit()


if USE_FS:  # le moteur Firestore (avec cache mémoire) remplace les fonctions ci-dessus
    from backend.fsdb import (add_alert, add_reco, init_schema, kv_delete, kv_get, kv_get_fresh, kv_set,  # noqa: E402,F401
                              kv_setnx, last_label, latest_recos, load_closes, reco_history, recent_alerts, upsert_prices)
