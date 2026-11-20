from __future__ import annotations

import os
import sqlite3
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DB_PATH = Path(os.getenv("VASTRA_API_DB", ROOT / "data" / "vastra_api.db"))


def connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(DB_PATH, timeout=10, check_same_thread=False)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    con.execute("PRAGMA busy_timeout = 10000")
    return con


def init_db(legacy_db: str | None = None) -> None:
    con = connect()
    con.executescript(
        """
        CREATE TABLE IF NOT EXISTS products (
            sku TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            category TEXT,
            base_price INTEGER NOT NULL,
            sale_price INTEGER,
            sale_ends_utc TEXT,
            member_discount_pct INTEGER NOT NULL DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS stock (
            sku TEXT PRIMARY KEY,
            qty INTEGER NOT NULL DEFAULT 0,
            updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS stock_events (
            event_id TEXT PRIMARY KEY,
            sku TEXT NOT NULL,
            delta INTEGER NOT NULL,
            reason TEXT NOT NULL,
            actor TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS reservations (
            reservation_id TEXT PRIMARY KEY,
            sku TEXT NOT NULL,
            qty INTEGER NOT NULL,
            status TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS orders (
            order_ref TEXT PRIMARY KEY,
            idempotency_key TEXT UNIQUE,
            created_at TEXT NOT NULL,
            channel TEXT NOT NULL,
            sku TEXT NOT NULL,
            qty INTEGER NOT NULL,
            total INTEGER NOT NULL,
            status TEXT NOT NULL,
            response_json TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS price_overrides (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            sku TEXT NOT NULL,
            store_id TEXT NOT NULL,
            price INTEGER NOT NULL,
            actor TEXT NOT NULL,
            reason TEXT NOT NULL,
            expires_at TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS audit_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            actor TEXT NOT NULL,
            action TEXT NOT NULL,
            sku TEXT,
            store_id TEXT,
            reason TEXT,
            request_id TEXT,
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS outbox (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            event_id TEXT UNIQUE NOT NULL,
            event_type TEXT NOT NULL,
            payload TEXT NOT NULL,
            published INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS checkout_payments (
            order_ref TEXT PRIMARY KEY,
            idempotency_key TEXT NOT NULL,
            charge_id TEXT,
            status TEXT NOT NULL,
            amount INTEGER NOT NULL,
            created_at TEXT NOT NULL
        );
        """
    )

    if legacy_db:
        legacy = sqlite3.connect(f"file:{legacy_db}?mode=ro", uri=True)
        legacy.row_factory = sqlite3.Row
        products = legacy.execute(
            "SELECT sku, name, category, base_price, sale_price, sale_ends_utc, member_discount_pct FROM products"
        ).fetchall()
        stocks = legacy.execute("SELECT sku, qty FROM stock_copy").fetchall()
        for p in products:
            con.execute(
                """
                INSERT OR IGNORE INTO products
                (sku, name, category, base_price, sale_price, sale_ends_utc, member_discount_pct)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                tuple(p),
            )
        now = __import__("datetime").datetime.now(__import__("datetime").timezone.utc).isoformat()
        for s in stocks:
            con.execute(
                "INSERT OR IGNORE INTO stock (sku, qty, updated_at) VALUES (?, ?, ?)",
                (s["sku"], s["qty"], now),
            )
        legacy.close()

    con.commit()
    con.close()
