import datetime as dt

import pytest
from fastapi.testclient import TestClient

import app.db as db
from app.main import app


@pytest.fixture(autouse=True)
def isolated_database(tmp_path, monkeypatch):
    """Give each test a clean API database and deterministic product/stock fixture."""
    database_path = tmp_path / "api.db"
    monkeypatch.setattr(db, "DB_PATH", database_path)
    monkeypatch.setattr("app.main.LEGACY_DB", None)
    db.init_db()

    con = db.connect()
    con.execute(
        """
        INSERT INTO products
        (sku, name, category, base_price, sale_price, sale_ends_utc, member_discount_pct)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        ("SKU00003", "Test Kurta", "kurtas", 2000, 1600, "2099-12-31T23:59:59+00:00", 10),
    )
    con.execute(
        "INSERT INTO stock (sku, qty, updated_at) VALUES (?, ?, ?)",
        ("SKU00003", 10, dt.datetime.now(dt.timezone.utc).isoformat()),
    )
    con.commit()
    con.close()


@pytest.fixture
def client():
    """Provide an HTTP client for endpoint-level tests."""
    with TestClient(app) as test_client:
        yield test_client
