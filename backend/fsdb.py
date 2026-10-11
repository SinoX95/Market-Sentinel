"""Stockage Firestore (Google Cloud), avec un cache en mémoire : les lectures répétées ne quittent jamais le serveur.

Même interface que les autres moteurs de db.py. Organisation :
 - kv/{clé}            : objets JSON (découpés en morceaux dans kvc/ au-delà de ~700 Ko)
 - prices/{symbole}    : historique des clôtures, un document par symbole (chaîne JSON {jour: clôture})
 - recos/{symbole}     : historique des recommandations (chaîne JSON)
Les écritures passent aussitôt dans le cache de l'instance ; les autres instances voient les changements au plus tard après CACHE_TTL.
"""
import json
import os
import random
import threading
import time
import zlib
from datetime import date, datetime, timezone

CACHE_TTL = int(os.getenv("FIRESTORE_CACHE_TTL", "180"))  # secondes
CHUNK = 700_000
_client_obj = None
_lock = threading.RLock()
_kv, _prices, _recos = {}, {}, {}   # clé -> (valeur, date de mise à jour côté base, horodatage du cache)


def _client():
    global _client_obj
    with _lock:
        if _client_obj is None:
            from google.cloud import firestore
            _client_obj = firestore.Client(project=os.getenv("GOOGLE_CLOUD_PROJECT") or os.getenv("GCLOUD_PROJECT") or None)
        return _client_obj


def _did(key):
    return str(key).replace("/", "|")[:1400]


def _fresh(entry):
    return entry is not None and time.time() - entry[2] < CACHE_TTL


def _decode(v, default):
    """Lit une valeur stockée : chaîne JSON (format actuel) ou JSON compressé zlib (ancien format d'import)."""
    if v is None or v == b"" or v == "":
        return default
    if isinstance(v, (bytes, bytearray)):
        return json.loads(zlib.decompress(bytes(v)).decode("utf-8"))
    return json.loads(v)


def _legacy_kv_raw(c, did, d):
    """Ancien format d'import : champ « data » compressé, découpé dans kv_parts/{clé}#{n} si « parts » > 0."""
    parts = int(d.get("parts") or 0)
    if parts:
        blob = b"".join(bytes((c.collection("kv_parts").document(f"{did}#{i}").get().to_dict() or {}).get("data") or b"") for i in range(parts))
        return _decode(blob, None)
    return _decode(d.get("data"), None)


def migrate_legacy():
    """Convertit une fois les données importées dans l'ancien format (collection « recommendations », valeurs compressées)."""
    c = _client()
    by = {}
    for doc in c.collection("recommendations").stream():
        r = doc.to_dict() or {}
        if not r.get("ticker") or not r.get("label"):
            continue
        at = r.get("created_at")
        at = _aware(at).isoformat() if isinstance(at, datetime) else str(at or _now().isoformat())
        by.setdefault(r["ticker"], []).append([r["label"], float(r.get("score") or 0), float(r.get("price_at") or 0), at])
    for t, h in by.items():
        ref = c.collection("recos").document(t)
        if not ref.get().exists:
            ref.set({"data": json.dumps(sorted(h, key=lambda x: x[3])), "t": _now()})
    for doc in c.collection("prices").stream():
        v = (doc.to_dict() or {}).get("data")
        if isinstance(v, (bytes, bytearray)):
            doc.reference.set({"data": json.dumps(_decode(v, {})), "t": _now()})
    for doc in c.collection("kv").stream():
        d = doc.to_dict() or {}
        if "v" not in d and not d.get("n") and "data" in d:
            try:
                val = _legacy_kv_raw(c, doc.id, d)
            except Exception:
                continue
            if val is not None:
                kv_set(doc.id, val)


def _now():
    return datetime.now(timezone.utc)


def _aware(dt):
    if dt is None:
        return _now()
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


# ---------- démarrage ----------
def init_schema(retries=1):
    """Vérifie l'accès à Firestore (les collections se créent toutes seules), puis précharge le cache."""
    try:
        _client().collection("kv").document("_ping").set({"t": _now()})
    except Exception as e:  # noqa: BLE001
        raise RuntimeError(f"Firestore injoignable ({type(e).__name__}: {str(e).strip()[:250]})") from e
    threading.Thread(target=_start, daemon=True).start()


def _start():
    try:
        migrate_legacy()
    except Exception:
        pass
    warm()


def warm():
    """Charge d'un coup les petits documents (kv, cours, recommandations) : les premières pages s'affichent sans attendre la base."""
    try:
        c, t0 = _client(), time.time()
        for doc in c.collection("kv").stream():
            d = doc.to_dict() or {}
            if doc.id.startswith("_") or d.get("n"):
                continue
            try:
                _kv[doc.id] = (json.loads(d["v"]), _aware(d.get("t")), t0)
            except Exception:
                continue
        for doc in c.collection("prices").stream():
            try:
                _prices[doc.id] = (_decode((doc.to_dict() or {}).get("data"), {}), None, t0)
            except Exception:
                continue
        for doc in c.collection("recos").stream():
            try:
                _recos[doc.id] = (_decode((doc.to_dict() or {}).get("data"), []), None, t0)
            except Exception:
                continue
    except Exception:
        pass  # le cache se remplira à la demande


# ---------- kv ----------
def kv_get(key):
    """Retourne (objet, date de mise à jour) ou (None, None)."""
    did = _did(key)
    e = _kv.get(did)
    if _fresh(e):
        return e[0], e[1]
    try:
        doc = _client().collection("kv").document(did).get()
        if not doc.exists:
            _kv[did] = (None, None, time.time())
            return None, None
        d = doc.to_dict() or {}
        raw = d.get("v")
        if d.get("n"):
            raw = "".join((_client().collection("kvc").document(f"{did}~{i}").get().to_dict() or {}).get("v", "") for i in range(d["n"]))
        if raw is None and "data" in d:  # ancien format d'import
            val = _legacy_kv_raw(_client(), did, d)
            if val is not None:
                upd = _aware(d.get("updated_at"))
                _kv[did] = (val, upd, time.time())
                return val, upd
        if raw is None:  # document existant mais sans contenu (écriture concurrente incomplète) : traité comme absent
            _kv[did] = (None, None, time.time())
            return None, None
        val, upd = json.loads(raw), _aware(d.get("t"))
        _kv[did] = (val, upd, time.time())
        return val, upd
    except Exception:
        if e is not None:  # base injoignable : on sert la dernière valeur connue
            return e[0], e[1]
        raise


def kv_set(key, obj):
    did, payload, now = _did(key), json.dumps(obj, ensure_ascii=False), _now()
    c = _client()
    if len(payload) > CHUNK:
        parts = [payload[i:i + CHUNK] for i in range(0, len(payload), CHUNK)]
        for i, p in enumerate(parts):
            c.collection("kvc").document(f"{did}~{i}").set({"v": p})
        c.collection("kv").document(did).set({"n": len(parts), "t": now})
    else:
        c.collection("kv").document(did).set({"v": payload, "t": now})
    _kv[did] = (json.loads(payload), now, time.time())


def kv_setnx(key, obj):
    """Enregistre obj seulement si la clé n'existe pas ; retourne la valeur définitivement stockée (utile aux secrets partagés entre instances)."""
    from google.api_core.exceptions import AlreadyExists
    did = _did(key)
    try:
        _client().collection("kv").document(did).create({"v": json.dumps(obj, ensure_ascii=False), "t": _now()})
        _kv[did] = (obj, _now(), time.time())
        return obj
    except AlreadyExists:
        _kv.pop(did, None)
        cur = kv_get(key)[0]
        if cur is not None:
            return cur
        kv_set(key, obj)  # le document existait mais sans contenu lisible : on le répare avec cette valeur
        return obj


def kv_get_fresh(key):
    """Lecture directe en base, sans le cache mémoire : pour les données de connexion (codes, comptes) qu'une autre
    instance a pu modifier il y a quelques secondes. Échoue plutôt que de servir une valeur ancienne."""
    _kv.pop(_did(key), None)
    return kv_get(key)[0]


def kv_delete(key):
    did = _did(key)
    _kv.pop(did, None)
    _client().collection("kv").document(did).delete()


# ---------- cours ----------
def _load_prices_doc(ticker):
    e = _prices.get(ticker)
    if _fresh(e):
        return e[0]
    try:
        doc = _client().collection("prices").document(ticker).get()
        data = _decode((doc.to_dict() or {}).get("data"), {}) if doc.exists else {}
        _prices[ticker] = (data, None, time.time())
        return data
    except Exception:
        if e is not None:
            return e[0]
        raise


def upsert_prices(ticker, rows):
    """rows : liste de (date, close)."""
    with _lock:
        data = dict(_load_prices_doc(ticker))
        for d, c in rows:
            data[d.isoformat() if hasattr(d, "isoformat") else str(d)] = float(c)
        _client().collection("prices").document(ticker).set({"data": json.dumps(data), "t": _now()})
        _prices[ticker] = (data, None, time.time())


def load_closes(tickers):
    out = {t: [] for t in tickers}
    missing = [t for t in tickers if not _fresh(_prices.get(t))]
    if len(missing) > 1:  # une seule requête pour tous les symboles absents du cache
        try:
            c = _client()
            for doc in c.get_all([c.collection("prices").document(t) for t in missing]):
                data = _decode((doc.to_dict() or {}).get("data"), {}) if doc.exists else {}
                _prices[doc.id] = (data, None, time.time())
        except Exception:
            pass
    for t in tickers:
        data = _load_prices_doc(t)
        out[t] = [(date.fromisoformat(d), float(v)) for d, v in sorted(data.items())]
    return out


# ---------- recommandations ----------
def _load_recos(ticker):
    e = _recos.get(ticker)
    if _fresh(e):
        return e[0]
    try:
        doc = _client().collection("recos").document(ticker).get()
        data = _decode((doc.to_dict() or {}).get("data"), []) if doc.exists else []
        _recos[ticker] = (data, None, time.time())
        return data
    except Exception:
        if e is not None:
            return e[0]
        raise


def last_label(ticker):
    h = _load_recos(ticker)
    return h[-1][0] if h else None


def add_reco(ticker, score, label, price_at):
    with _lock:
        h = list(_load_recos(ticker)) + [[label, float(score), float(price_at), _now().isoformat()]]
        _client().collection("recos").document(ticker).set({"data": json.dumps(h), "t": _now()})
        _recos[ticker] = (h, None, time.time())


def latest_recos():
    """Dernière recommandation par ticker : {ticker: (label, score, price_at, created_at)}."""
    if not _recos or not all(_fresh(v) for v in _recos.values()):
        try:
            t0 = time.time()
            for doc in _client().collection("recos").stream():
                try:
                    _recos[doc.id] = (_decode((doc.to_dict() or {}).get("data"), []), None, t0)
                except Exception:
                    continue
        except Exception:
            pass
    return {t: (h[-1][0], float(h[-1][1]), float(h[-1][2]), datetime.fromisoformat(h[-1][3])) for t, (h, _, _) in _recos.items() if h}


def reco_history(ticker):
    """[(label, score, price_at, created_at)] du plus ancien au plus récent."""
    return [(r[0], float(r[1]), float(r[2]), datetime.fromisoformat(r[3])) for r in _load_recos(ticker)]


# ---------- journal des alertes (Module G) ----------
ALERTS_MAX = 1000  # borne le volume conservé (coût Firestore) ; purge occasionnelle, pas à chaque écriture


def add_alert(ticker, kind, subject, text, channels, importance_score=None, importance_tier=None,
             category=None, url=None, image_url=None):
    """Journalise une alerte envoyée, que des canaux Telegram/email soient configurés ou non — l'historique
    reste utile pour vérifier après coup qu'une alerte aurait dû partir. importance_score/tier (Module E) et
    category/url/image_url (Module 2/11) sont optionnels — Firestore n'a pas de schéma fixe, donc aucune
    migration n'est nécessaire pour ces nouveaux champs, à la différence de backend/db.py (SQL)."""
    _client().collection("alerts").add({
        "ticker": ticker, "kind": kind, "subject": subject, "text": text,
        "channels": json.dumps(channels or {}, ensure_ascii=False),
        "importance_score": importance_score, "importance_tier": importance_tier,
        "category": category, "url": url, "image_url": image_url, "t": _now(),
    })
    if random.random() < 0.02:  # purge rare : évite une lecture/suppression à chaque écriture
        _trim_alerts()


def _trim_alerts():
    try:
        from google.cloud import firestore
        docs = list(_client().collection("alerts").order_by("t", direction=firestore.Query.DESCENDING)
                    .offset(ALERTS_MAX).stream())
        for d in docs:
            d.reference.delete()
    except Exception:
        pass  # purge non critique : on retentera à la prochaine écriture


def recent_alerts(limit=100, ticker=None):
    """Alertes les plus récentes, du plus récent au plus ancien. Le filtre par ticker se fait en Python, pas
    via une requête Firestore composite (ticker + tri) : ça éviterait d'avoir à faire créer un index pour un
    usage personnel à faible volume."""
    from google.cloud import firestore
    try:
        docs = (_client().collection("alerts").order_by("t", direction=firestore.Query.DESCENDING)
                .limit(limit * 3 if ticker else limit).stream())
    except Exception:
        return []
    out = []
    for doc in docs:
        d = doc.to_dict() or {}
        if ticker and d.get("ticker") != ticker:
            continue
        out.append({"ticker": d.get("ticker"), "kind": d.get("kind"), "subject": d.get("subject"),
                    "text": d.get("text"), "channels": json.loads(d.get("channels") or "{}"),
                    "importance_score": d.get("importance_score"), "importance_tier": d.get("importance_tier"),
                    "category": d.get("category"), "url": d.get("url"), "image_url": d.get("image_url"),
                    "created_at": _aware(d.get("t"))})
        if len(out) >= limit:
            break
    return out
