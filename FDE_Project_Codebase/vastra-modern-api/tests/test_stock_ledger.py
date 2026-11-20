def test_stock_event_updates_ledger_and_deduplicates_retries(client):
    payload = {
        "event_id": "event-stock-001",
        "sku": "SKU00003",
        "delta": 3,
        "reason": "warehouse.received",
    }
    headers = {"X-API-Key": "vastra-warehouse", "X-User": "warehouse-1"}

    first = client.post("/v1/stock/events", json=payload, headers=headers)
    retry = client.post("/v1/stock/events", json=payload, headers=headers)

    assert first.status_code == 200
    assert first.json()["qty"] == 13
    assert retry.status_code == 200
    assert retry.json()["status"] == "duplicate"


def test_stock_event_rejects_negative_inventory(client):
    response = client.post(
        "/v1/stock/events",
        json={"event_id": "event-stock-negative", "sku": "SKU00003", "delta": -11, "reason": "damage"},
        headers={"X-API-Key": "vastra-warehouse"},
    )
    assert response.status_code == 409


def test_stock_event_requires_stock_write_role(client):
    response = client.post(
        "/v1/stock/events",
        json={"event_id": "event-stock-forbidden", "sku": "SKU00003", "delta": 1, "reason": "test"},
        headers={"X-API-Key": "vastra-web"},
    )
    assert response.status_code == 403
