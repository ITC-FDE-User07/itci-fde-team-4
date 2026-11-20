import os
import tempfile
from pathlib import Path

os.environ["LATETIME_SCALE"] = "0.01"
os.environ["VASTRA_API_DB"] = str(Path(tempfile.gettempdir()) / "vastra-api-test.db")

import pytest
from fastapi.testclient import TestClient

from app.db import DB_PATH, init_db
from app.main import app


@pytest.fixture(autouse=True)
def isolated_database(tmp_path):
    os.environ["VASTRA_API_DB"] = str(tmp_path / "api.db")
    import app.db as db

    db.DB_PATH = tmp_path / "api.db"
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
    con.execute("INSERT INTO stock (sku, qty, updated_at) VALUES (?, ?, ?)", ("SKU00003", 10, "2026-11-20T00:00:00+00:00"))
    con.commit()
    con.close()


@pytest.fixture
def client():
    return TestClient(app)


def test_health(client):
    r = client.get("/v1/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


def test_test_workbench_is_served(client):
    r = client.get("/ui/")
    assert r.status_code == 200
    assert "Vastra API Workbench" in r.text
    assert "Run read-only smoke checks" in r.text


def test_catalogue_requires_api_key(client):
    assert client.get("/v1/catalogue/SKU00003").status_code == 401


def test_catalogue_returns_etag(client):
    r = client.get("/v1/catalogue/SKU00003", headers={"X-API-Key": "vastra-web"})
    assert r.status_code == 200
    assert r.headers["ETag"].startswith('"')
    assert r.headers["Cache-Control"].startswith("public")


def test_one_pricing_engine_applies_consistent_rules(client):
    r = client.get(
        "/v1/prices/SKU00003",
        params={"coupon_pct": 10, "member": True},
        headers={"X-API-Key": "vastra-web"},
    )
    assert r.status_code == 200
    assert r.json()["unit_price"] == 1296
    assert r.json()["total"] == 1296


def test_prices_require_price_role(client):
    r = client.get(
        "/v1/prices/SKU00003",
        headers={"X-API-Key": "vastra-warehouse"},
    )
    assert r.status_code == 403


def test_stock_event_updates_live_stock(client):
    r = client.post(
        "/v1/stock/events",
        json={"event_id": "evt-001", "sku": "SKU00003", "delta": 3, "reason": "warehouse.received"},
        headers={"X-API-Key": "vastra-warehouse", "X-User": "warehouse-1"},
    )
    assert r.status_code == 200
    assert r.json()["qty"] == 13

    repeat = client.post(
        "/v1/stock/events",
        json={"event_id": "evt-001", "sku": "SKU00003", "delta": 3, "reason": "warehouse.received"},
        headers={"X-API-Key": "vastra-warehouse", "X-User": "warehouse-1"},
    )
    assert repeat.json()["status"] == "duplicate"


def test_stock_event_cannot_make_stock_negative(client):
    r = client.post(
        "/v1/stock/events",
        json={"event_id": "evt-negative", "sku": "SKU00003", "delta": -11, "reason": "damage"},
        headers={"X-API-Key": "vastra-warehouse"},
    )
    assert r.status_code == 409


def test_named_store_override_is_audited(client):
    r = client.post(
        "/v1/overrides",
        json={
            "sku": "SKU00003",
            "store_id": "ST001",
            "price": 999,
            "reason": "Diwali local promotion",
            "expires_at": "2099-12-31T23:59:59+00:00",
        },
        headers={"X-API-Key": "vastra-pos", "X-User": "manager-07"},
    )
    assert r.status_code == 200
    audit = client.get("/v1/audit/overrides", headers={"X-API-Key": "vastra-pos"})
    assert audit.status_code == 200
    assert audit.json()[0]["actor"] == "manager-07"


def test_override_changes_store_price_but_not_default_price(client):
    client.post(
        "/v1/overrides",
        json={
            "sku": "SKU00003",
            "store_id": "ST001",
            "price": 999,
            "reason": "Store promotion",
            "expires_at": "2099-12-31T23:59:59+00:00",
        },
        headers={"X-API-Key": "vastra-pos", "X-User": "manager-07"},
    )
    store = client.get(
        "/v1/prices/SKU00003",
        params={"store_id": "ST001"},
        headers={"X-API-Key": "vastra-pos"},
    )
    default = client.get(
        "/v1/prices/SKU00003",
        headers={"X-API-Key": "vastra-pos"},
    )
    assert store.json()["unit_price"] == 999
    assert default.json()["unit_price"] == 1600


def test_checkout_requires_idempotency_key(client):
    r = client.post(
        "/v1/checkout",
        json={"sku": "SKU00003", "qty": 1},
        headers={"X-API-Key": "vastra-web"},
    )
    assert r.status_code == 422


def test_checkout_rejects_short_idempotency_key(client):
    r = client.post(
        "/v1/checkout",
        json={"sku": "SKU00003", "qty": 1},
        headers={"X-API-Key": "vastra-web", "Idempotency-Key": "short"},
    )
    assert r.status_code == 400


def test_successful_checkout_is_idempotent_and_reserves_stock(client, monkeypatch):
    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {"status": "success", "charge_id": "charge-001"}

    class FakeAsyncClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def post(self, *args, **kwargs):
            return FakeResponse()

    async def fake_courier(pincode):
        return {"pincode": pincode, "delivery_days": 3, "cod_available": True}

    monkeypatch.setattr("app.main.httpx.AsyncClient", FakeAsyncClient)
    monkeypatch.setattr("app.main.call_courier", fake_courier)

    headers = {
        "X-API-Key": "vastra-web",
        "Idempotency-Key": "checkout-key-001",
    }
    first = client.post("/v1/checkout", json={"sku": "SKU00003", "qty": 1}, headers=headers)
    second = client.post("/v1/checkout", json={"sku": "SKU00003", "qty": 1}, headers=headers)

    assert first.status_code == 200
    assert first.json()["status"] == "paid"
    assert second.status_code == 200
    assert second.json()["order_ref"] == first.json()["order_ref"]
    assert second.json()["stock_remaining"] == 9


def test_checkout_rejects_oversell(client):
    r = client.post(
        "/v1/checkout",
        json={"sku": "SKU00003", "qty": 11},
        headers={"X-API-Key": "vastra-web", "Idempotency-Key": "checkout-over-001"},
    )
    assert r.status_code == 409
