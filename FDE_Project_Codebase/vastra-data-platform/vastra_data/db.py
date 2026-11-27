"""One database, three layers (plus two governance areas).

The lab uses SQLite files attached under schema names, so every query reads exactly like the PostgreSQL
design: raw.events, clean.sales_lines, ready.stock_position, gov.access_log, ops.failed_records.
sql/postgres/001_schemas.sql has the same tables as PostgreSQL DDL for teams running the Docker option.

  raw.db        records exactly as received (never edited)
  clean.db      UTC times, no duplicates, fixed codes, tokens instead of personal details
  ready.db      the tables the model uses, each with a contract, an owner and a freshness target
  ops.db        load runs, watermarks, failed_records, contract results
  gov.db        consent, data_catalogue, access_log
  restricted/person_tokens.db   real name, phone, email: the ONLY place personal details live
"""
import os
import sqlite3
from contextlib import contextmanager

from vastra_data import config

LAYERS = ("raw", "clean", "ready", "ops", "gov")

SCHEMA = {
    "raw": """
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
CREATE INDEX IF NOT EXISTS raw.ix_events_type ON events (event_type, source);
""",
    "ops": """
CREATE TABLE IF NOT EXISTS ops.load_runs (
    run_id INTEGER PRIMARY KEY AUTOINCREMENT, source TEXT NOT NULL, started_at TEXT NOT NULL, finished_at TEXT,
    read INTEGER DEFAULT 0, loaded INTEGER DEFAULT 0, duplicates INTEGER DEFAULT 0, failed INTEGER DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'running', actor TEXT
);
CREATE TABLE IF NOT EXISTS ops.watermarks (
    source TEXT PRIMARY KEY, position TEXT NOT NULL, updated_at TEXT NOT NULL
);
-- Bad data waits here with its reason instead of disappearing (E10: the stock sync failed nine times, silently).
CREATE TABLE IF NOT EXISTS ops.failed_records (
    failed_id   INTEGER PRIMARY KEY AUTOINCREMENT,
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
    result_id INTEGER PRIMARY KEY AUTOINCREMENT, run_at TEXT NOT NULL, table_name TEXT NOT NULL,
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
""",
    "clean": """
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
""",
    "ready": """
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
""",
    "gov": """
CREATE TABLE IF NOT EXISTS gov.consent (
    consent_id INTEGER PRIMARY KEY AUTOINCREMENT, customer_token TEXT NOT NULL,
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
    access_id INTEGER PRIMARY KEY AUTOINCREMENT, at_utc TEXT NOT NULL, actor TEXT NOT NULL,
    table_name TEXT NOT NULL, purpose TEXT NOT NULL, action TEXT NOT NULL, allowed INTEGER NOT NULL, detail TEXT
);
""",
}

RESTRICTED_SCHEMA = """
CREATE TABLE IF NOT EXISTS restricted.person_tokens (
    customer_token TEXT PRIMARY KEY, customer_ref TEXT UNIQUE NOT NULL,
    name TEXT, phone TEXT, email TEXT, created_at TEXT NOT NULL
);
"""


def _path(name):
    return os.path.join(config.settings.data_dir, f"{name}.db")


def restricted_path():
    return os.path.join(config.settings.data_dir, "restricted", "person_tokens.db")


def connect(restricted: bool = False) -> sqlite3.Connection:
    """Open every layer under its schema name. `restricted=True` also attaches the personal-data store."""
    os.makedirs(config.settings.data_dir, exist_ok=True)
    con = sqlite3.connect(":memory:", timeout=10, isolation_level=None, check_same_thread=False)
    con.row_factory = sqlite3.Row
    for layer in LAYERS:
        con.execute(f"ATTACH DATABASE ? AS {layer}", (_path(layer),))
    if restricted:
        os.makedirs(os.path.dirname(restricted_path()), exist_ok=True)
        con.execute("ATTACH DATABASE ? AS restricted", (restricted_path(),))
    return con


@contextmanager
def tx(restricted: bool = False):
    con = connect(restricted)
    try:
        con.execute("BEGIN IMMEDIATE")
        yield con
        con.execute("COMMIT")
    except Exception:
        if con.in_transaction:
            con.execute("ROLLBACK")
        raise
    finally:
        con.close()


def init():
    con = connect(restricted=True)
    try:
        for layer in LAYERS:
            con.executescript(SCHEMA[layer])
        con.executescript(RESTRICTED_SCHEMA)
    finally:
        con.close()
    try:   # owner-only file permissions on the personal-data store (no effect on Windows, harmless)
        os.chmod(restricted_path(), 0o600)
    except OSError:
        pass
