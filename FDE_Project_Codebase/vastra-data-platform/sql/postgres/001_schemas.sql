-- PostgreSQL version of the lab schema (Docker option). Same tables and columns as vastra_data/db.py.
-- psql -h localhost -U vastra_data_owner -d vastra -f sql/postgres/001_schemas.sql

CREATE SCHEMA IF NOT EXISTS raw; CREATE SCHEMA IF NOT EXISTS clean; CREATE SCHEMA IF NOT EXISTS ready;
CREATE SCHEMA IF NOT EXISTS ops; CREATE SCHEMA IF NOT EXISTS gov; CREATE SCHEMA IF NOT EXISTS restricted;

-- ---------------- raw
CREATE TABLE IF NOT EXISTS raw.events (
    event_id    TEXT PRIMARY KEY,          -- ON CONFLICT (event_id) DO NOTHING: loading twice never counts twice
    event_type  TEXT NOT NULL,
    occurred_at TEXT NOT NULL,             -- exactly as received; clean converts to UTC
    source      TEXT NOT NULL,
    actor       TEXT NOT NULL,
    payload     TEXT NOT NULL,             -- JSON, already checked against the contract, never personal details
    loaded_at   TEXT NOT NULL,
    load_run_id INTEGER
);
CREATE INDEX IF NOT EXISTS ix_events_type ON raw.events (event_type, source);

-- ---------------- clean
CREATE TABLE IF NOT EXISTS clean.stores (
    location_id TEXT PRIMARY KEY, name TEXT, city TEXT, region TEXT, tier TEXT, opened_on TEXT, updated_at_utc TEXT
);
CREATE TABLE IF NOT EXISTS clean.products (
    sku_id TEXT NOT NULL, channel TEXT NOT NULL, name TEXT, category TEXT, colour TEXT, top500 INTEGER,
    updated_at_utc TEXT, PRIMARY KEY (sku_id, channel)
);
CREATE TABLE IF NOT EXISTS clean.sales_lines (
    event_id TEXT PRIMARY KEY, business_date TEXT NOT NULL, sku_id TEXT NOT NULL, location_id TEXT NOT NULL,
    units INTEGER NOT NULL, net_amount INTEGER NOT NULL, occurred_at_utc TEXT NOT NULL, source TEXT NOT NULL,
    UNIQUE (business_date, sku_id, location_id)
);
CREATE TABLE IF NOT EXISTS clean.orders (
    order_id TEXT PRIMARY KEY, occurred_at_utc TEXT NOT NULL, business_date TEXT NOT NULL, sku_id TEXT NOT NULL,
    qty INTEGER NOT NULL, total INTEGER NOT NULL, channel TEXT, status TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS clean.stock_movements (
    event_id TEXT PRIMARY KEY, occurred_at_utc TEXT NOT NULL, business_date TEXT NOT NULL, sku_id TEXT NOT NULL,
    location_id TEXT NOT NULL, kind TEXT NOT NULL, qty_signed INTEGER NOT NULL, ref TEXT, source TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS clean.stock_counts (
    event_id TEXT PRIMARY KEY, counted_at_utc TEXT NOT NULL, business_date TEXT NOT NULL, sku_id TEXT NOT NULL,
    location_id TEXT NOT NULL, available INTEGER NOT NULL, on_order INTEGER NOT NULL, source TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS clean.purchase_orders (
    po_id TEXT PRIMARY KEY, sku_id TEXT NOT NULL, location_id TEXT NOT NULL, qty INTEGER NOT NULL,
    raised_at_utc TEXT NOT NULL, raised_date TEXT NOT NULL, expected_date TEXT, supplier_id TEXT,
    lead_time_days INTEGER, lead_time_filled INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS clean.goods_receipts (
    event_id TEXT PRIMARY KEY, po_id TEXT, sku_id TEXT NOT NULL, location_id TEXT NOT NULL, qty INTEGER NOT NULL,
    received_at_utc TEXT NOT NULL, business_date TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS clean.prices (
    event_id TEXT PRIMARY KEY, sku_id TEXT NOT NULL, location_id TEXT, price INTEGER NOT NULL, kind TEXT NOT NULL,
    valid_from TEXT, valid_to TEXT, occurred_at_utc TEXT NOT NULL, trust TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS clean.customers (      -- token only: no name, phone or email in this layer
    customer_token TEXT PRIMARY KEY, home_store TEXT, registered_at_utc TEXT
);

-- ---------------- ready
CREATE TABLE IF NOT EXISTS ready.store_master (
    location_id TEXT PRIMARY KEY, tier TEXT NOT NULL, region TEXT NOT NULL, city TEXT, opened_on TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS ready.product_master (
    sku_id TEXT PRIMARY KEY, name TEXT NOT NULL, category TEXT NOT NULL, colour TEXT, top500 INTEGER NOT NULL,
    channels_agree INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS ready.sales_daily (
    business_date TEXT NOT NULL, sku_id TEXT NOT NULL, location_id TEXT NOT NULL, store_tier TEXT NOT NULL,
    units_sold INTEGER NOT NULL, net_amount INTEGER NOT NULL, has_pos_data INTEGER NOT NULL,
    closing_available INTEGER NOT NULL, is_sold_out INTEGER NOT NULL, adjusted_demand REAL NOT NULL,
    PRIMARY KEY (business_date, sku_id, location_id)
);
CREATE TABLE IF NOT EXISTS ready.stock_position (
    snapshot_at TEXT NOT NULL, sku_id TEXT NOT NULL, location_id TEXT NOT NULL,
    available INTEGER NOT NULL, on_order INTEGER NOT NULL,
    PRIMARY KEY (snapshot_at, sku_id, location_id)
);
CREATE TABLE IF NOT EXISTS ready.po_receipts (
    po_id TEXT PRIMARY KEY, sku_id TEXT NOT NULL, location_id TEXT NOT NULL, supplier_id TEXT, qty INTEGER NOT NULL,
    raised_date TEXT NOT NULL, expected_date TEXT, received_date TEXT, lead_time_days INTEGER,
    lead_time_filled INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS ready.promo_calendar (
    sku_id TEXT NOT NULL, location_id TEXT NOT NULL, start_date TEXT NOT NULL, end_date TEXT NOT NULL,
    price INTEGER NOT NULL, trust TEXT NOT NULL, PRIMARY KEY (sku_id, location_id, start_date)
);

-- ---------------- ops
CREATE TABLE IF NOT EXISTS ops.load_runs (
    run_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY, source TEXT NOT NULL, started_at TEXT NOT NULL, finished_at TEXT,
    read INTEGER DEFAULT 0, loaded INTEGER DEFAULT 0, duplicates INTEGER DEFAULT 0, failed INTEGER DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'running', actor TEXT
);
CREATE TABLE IF NOT EXISTS ops.watermarks (
    source TEXT PRIMARY KEY, position TEXT NOT NULL, updated_at TEXT NOT NULL
);
-- Bad data waits here with its reason instead of disappearing (E10: the stock sync failed nine times, silently).
CREATE TABLE IF NOT EXISTS ops.failed_records (
    failed_id   BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    stage       TEXT NOT NULL,             -- load | clean
    load_run_id INTEGER, source TEXT, event_id TEXT, event_type TEXT,
    row         TEXT NOT NULL,             -- the record as received, personal details masked
    reason      TEXT NOT NULL,
    kind        TEXT NOT NULL,             -- contract (bad data, not retried) | database (failed after 3 tries)
    failed_at   TEXT NOT NULL,
    status      TEXT NOT NULL DEFAULT 'open',   -- open | fixed | loaded | ignored
    fixed_row   TEXT, resolved_at TEXT, resolved_by TEXT
);
CREATE TABLE IF NOT EXISTS ops.contract_results (
    result_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY, run_at TEXT NOT NULL, table_name TEXT NOT NULL,
    check_name TEXT NOT NULL, passed INTEGER NOT NULL, detail TEXT
);
-- Ledger vs warehouse count on each count day (the "Correct?" fitness question).
CREATE TABLE IF NOT EXISTS ops.ledger_vs_count (
    business_date TEXT NOT NULL, sku_id TEXT NOT NULL, location_id TEXT NOT NULL, ledger INTEGER, counted INTEGER,
    PRIMARY KEY (business_date, sku_id, location_id)
);
CREATE TABLE IF NOT EXISTS ops.clean_runs (
    run_at TEXT NOT NULL, table_name TEXT NOT NULL, rows_in INTEGER, rows_out INTEGER,
    duplicates_removed INTEGER, times_fixed INTEGER, codes_fixed INTEGER, rejected INTEGER
);

-- ---------------- gov
CREATE TABLE IF NOT EXISTS gov.consent (
    consent_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY, customer_token TEXT NOT NULL,
    purpose TEXT NOT NULL CHECK (purpose IN ('orders','loyalty','marketing','personalisation')),
    status TEXT NOT NULL CHECK (status IN ('given','withdrawn')),
    notice_version TEXT NOT NULL, at_utc TEXT NOT NULL, source TEXT NOT NULL, event_id TEXT UNIQUE
);
CREATE TABLE IF NOT EXISTS gov.data_catalogue (
    table_name TEXT PRIMARY KEY, owner TEXT NOT NULL, source TEXT NOT NULL,
    classification TEXT NOT NULL CHECK (classification IN ('public','internal','confidential','personal')),
    allowed_purposes TEXT NOT NULL, contract_file TEXT, description TEXT
);
CREATE TABLE IF NOT EXISTS gov.access_log (
    access_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY, at_utc TEXT NOT NULL, actor TEXT NOT NULL,
    table_name TEXT NOT NULL, purpose TEXT NOT NULL, action TEXT NOT NULL, allowed INTEGER NOT NULL, detail TEXT
);

-- ---------------- restricted (personal details: only the DPO role can read it)
CREATE TABLE IF NOT EXISTS restricted.person_tokens (
    customer_token TEXT PRIMARY KEY, customer_ref TEXT UNIQUE NOT NULL,
    name TEXT, phone TEXT, email TEXT, created_at TEXT NOT NULL
);

-- Least privilege: named roles, never the shared vastra_admin login (finding R15a).
CREATE ROLE p2_reader NOLOGIN; GRANT USAGE ON SCHEMA ready TO p2_reader; GRANT SELECT ON ALL TABLES IN SCHEMA ready TO p2_reader;
CREATE ROLE dpo NOLOGIN; GRANT USAGE ON SCHEMA restricted, gov TO dpo; GRANT SELECT, DELETE ON restricted.person_tokens TO dpo;
REVOKE ALL ON SCHEMA restricted FROM PUBLIC;
