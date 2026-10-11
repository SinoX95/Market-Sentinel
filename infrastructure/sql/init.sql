CREATE TABLE IF NOT EXISTS prices (
    ticker TEXT NOT NULL,
    day    DATE NOT NULL,
    close  NUMERIC NOT NULL,
    PRIMARY KEY (ticker, day)
);

-- Chaque recommandation est historisée pour mesurer la performance réelle ensuite.
CREATE TABLE IF NOT EXISTS recommendations (
    id          SERIAL PRIMARY KEY,
    ticker      TEXT NOT NULL,
    score       NUMERIC NOT NULL,
    label       TEXT NOT NULL,
    price_at    NUMERIC,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_reco_ticker ON recommendations (ticker, created_at);

-- Stockage clé/valeur (JSON) : news, événements, positions des investisseurs.
CREATE TABLE IF NOT EXISTS kv (
    key        TEXT PRIMARY KEY,
    value      TEXT NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Journal des alertes envoyées (Module G) : indépendant des canaux (Telegram/email), pour pouvoir
-- vérifier après coup qu'une alerte est bien partie même si le message n'a pas été vu.
CREATE TABLE IF NOT EXISTS alerts (
    id          SERIAL PRIMARY KEY,
    ticker      TEXT,
    kind        TEXT NOT NULL,
    subject     TEXT NOT NULL,
    text        TEXT NOT NULL,
    channels    TEXT NOT NULL,
    importance_score REAL,
    importance_tier   TEXT,
    category    TEXT,
    url         TEXT,
    image_url   TEXT,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
ALTER TABLE alerts ADD COLUMN IF NOT EXISTS category TEXT;
ALTER TABLE alerts ADD COLUMN IF NOT EXISTS url TEXT;
ALTER TABLE alerts ADD COLUMN IF NOT EXISTS image_url TEXT;
CREATE INDEX IF NOT EXISTS idx_alerts_created ON alerts (created_at);
CREATE INDEX IF NOT EXISTS idx_alerts_ticker ON alerts (ticker, created_at);
