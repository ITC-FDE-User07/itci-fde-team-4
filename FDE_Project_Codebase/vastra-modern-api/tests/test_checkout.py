from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import threading

from fastapi.testclient import TestClient

import app.db as db
from app.db import connect
from app.main import app


def test_checkout_requires_a_valid_idempotency_key(client):
    missing = client.post(
        "/v1/checkout",
        json={"sku": "SKU00003", "qty": 1},
        headers={"X-API-Key": "vastra-web"},
    )
    short = client.post(
        "/v1/checkout",
        json={"sku": "SKU00003", "qty": 1},
        headers={"X-API-Key": "vastra-web", "Idempotency-Key": "short"},
    )
    assert missing.status_code == 422
    assert short.status_code == 400


def test_successful_checkout_reuses_completed_order_and_reserves_once(client, monkeypatch):
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

    headers = {"X-API-Key": "vastra-web", "Idempotency-Key": "checkout-key-001"}
    payload = {"sku": "SKU00003", "qty": 1}
    first = client.post("/v1/checkout", json=payload, headers=headers)
    retry = client.post("/v1/checkout", json=payload, headers=headers)

    assert first.status_code == 200
    assert first.json()["status"] == "paid"
    assert retry.status_code == 200
    assert retry.json()["order_ref"] == first.json()["order_ref"]
    assert retry.json()["stock_remaining"] == 9


def test_concurrent_checkout_with_same_key_is_rejected_while_in_progress(monkeypatch):
    courier_started = threading.Event()
    allow_courier_to_finish = threading.Event()
    payment_calls = []

    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {"status": "success", "charge_id": "charge-concurrent"}

    class FakeAsyncClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def post(self, *args, **kwargs):
            payment_calls.append(kwargs)
            return FakeResponse()

    async def blocked_courier(pincode):
        courier_started.set()
        assert allow_courier_to_finish.wait(timeout=5)
        return {"pincode": pincode, "delivery_days": 3, "cod_available": True}

    monkeypatch.setattr("app.main.httpx.AsyncClient", FakeAsyncClient)
    monkeypatch.setattr("app.main.call_courier", blocked_courier)
    headers = {"X-API-Key": "vastra-web", "Idempotency-Key": "checkout-concurrent-001"}
    payload = {"sku": "SKU00003", "qty": 1}

    def submit_first_request():
        with TestClient(app) as first_client:
            return first_client.post("/v1/checkout", json=payload, headers=headers)

    with ThreadPoolExecutor(max_workers=1) as executor:
        first_request = executor.submit(submit_first_request)
        assert courier_started.wait(timeout=5)
        with TestClient(app) as concurrent_client:
            duplicate = concurrent_client.post("/v1/checkout", json=payload, headers=headers)
        allow_courier_to_finish.set()
        first = first_request.result(timeout=5)

    assert first.status_code == 200
    assert duplicate.status_code == 409
    assert "in progress" in duplicate.json()["detail"]
    assert len(payment_calls) == 1


def test_completed_checkout_retry_survives_database_reinitialization(monkeypatch):
    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {"status": "success", "charge_id": "charge-restart"}

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
    headers = {"X-API-Key": "vastra-web", "Idempotency-Key": "checkout-restart-001"}
    payload = {"sku": "SKU00003", "qty": 1}

    with TestClient(app) as first_client:
        first = first_client.post("/v1/checkout", json=payload, headers=headers)

    db.init_db()
    with TestClient(app) as restarted_client:
        retry = restarted_client.post("/v1/checkout", json=payload, headers=headers)

    assert first.status_code == 200
    assert retry.status_code == 200
    assert retry.json() == first.json()


def test_persisted_in_progress_checkout_is_not_replayed_after_restart():
    request_hash = hashlib.sha256(
        json.dumps(
            {
                "sku": "SKU00003",
                "qty": 1,
                "coupon_pct": 0,
                "member": False,
                "pincode": "560001",
                "channel": "web",
                "store_id": None,
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()
    con = connect()
    con.execute(
        "INSERT INTO checkout_attempts "
        "(idempotency_key, request_hash, status, created_at) VALUES (?, ?, 'in_progress', ?)",
        ("checkout-stale-001", request_hash, "2026-11-26T00:00:00+00:00"),
    )
    con.commit()
    con.close()

    db.init_db()
    with TestClient(app) as restarted_client:
        mismatched = restarted_client.post(
            "/v1/checkout",
            json={"sku": "SKU00003", "qty": 2},
            headers={"X-API-Key": "vastra-web", "Idempotency-Key": "checkout-stale-001"},
        )
        response = restarted_client.post(
            "/v1/checkout",
            json={"sku": "SKU00003", "qty": 1},
            headers={"X-API-Key": "vastra-web", "Idempotency-Key": "checkout-stale-001"},
        )

    assert mismatched.status_code == 409
    assert "different request" in mismatched.json()["detail"]
    assert response.status_code == 409
    assert "reconcile before retrying" in response.json()["detail"]


def test_checkout_rejects_overselling(client):
    response = client.post(
        "/v1/checkout",
        json={"sku": "SKU00003", "qty": 11},
        headers={"X-API-Key": "vastra-web", "Idempotency-Key": "checkout-over-001"},
    )
    assert response.status_code == 409


def test_successful_checkout_writes_order_event_to_outbox(client, monkeypatch):
    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {"status": "success", "charge_id": "charge-002"}

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
    checkout = client.post(
        "/v1/checkout",
        json={"sku": "SKU00003", "qty": 1},
        headers={"X-API-Key": "vastra-web", "Idempotency-Key": "checkout-event-001"},
    )
    events = client.get("/v1/events", headers={"X-API-Key": "vastra-erp"})

    assert checkout.status_code == 200
    assert any(event["event_type"] == "order.created" for event in events.json())


def test_failed_payment_releases_reserved_stock(client, monkeypatch):
    class FailedPaymentResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {"status": "failed", "reason": "bank_declined"}

    class FakeAsyncClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def post(self, *args, **kwargs):
            return FailedPaymentResponse()

    async def fake_courier(pincode):
        return {"pincode": pincode, "delivery_days": 3, "cod_available": True}

    monkeypatch.setattr("app.main.httpx.AsyncClient", FakeAsyncClient)
    monkeypatch.setattr("app.main.call_courier", fake_courier)

    response = client.post(
        "/v1/checkout",
        json={"sku": "SKU00003", "qty": 2},
        headers={"X-API-Key": "vastra-web", "Idempotency-Key": "checkout-failed-001"},
    )

    con = connect()
    stock = con.execute("SELECT qty FROM stock WHERE sku='SKU00003'").fetchone()["qty"]
    con.close()

    assert response.status_code == 200
    assert response.json()["status"] == "payment_failed"
    assert stock == 10


def test_courier_error_uses_fallback_and_checkout_can_succeed(client, monkeypatch):
    import httpx

    class SuccessfulPaymentResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {"status": "success", "charge_id": "charge-fallback"}

    class FailingCourierAsyncClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def get(self, *args, **kwargs):
            raise httpx.ConnectError("courier is unavailable")

        async def post(self, *args, **kwargs):
            return SuccessfulPaymentResponse()

    monkeypatch.setattr("app.main.httpx.AsyncClient", FailingCourierAsyncClient)
    response = client.post(
        "/v1/checkout",
        json={"sku": "SKU00003", "qty": 1},
        headers={"X-API-Key": "vastra-web", "Idempotency-Key": "checkout-fallback-001"},
    )

    assert response.status_code == 200
    assert response.json()["status"] == "paid"
    assert response.json()["delivery_days"] == 5
    assert response.json()["courier_fallback"] is True
