from app.db import connect


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
