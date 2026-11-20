def test_stock_change_is_available_to_erp_integration_feed(client):
    stock = client.post(
        "/v1/stock/events",
        json={
            "event_id": "event-outbox-stock-001",
            "sku": "SKU00003",
            "delta": 2,
            "reason": "warehouse.received",
        },
        headers={"X-API-Key": "vastra-warehouse"},
    )
    assert stock.status_code == 200

    events = client.get("/v1/events", headers={"X-API-Key": "vastra-erp"})
    assert events.status_code == 200
    assert any(event["event_type"] == "stock.changed" for event in events.json())


def test_events_feed_requires_erp_role(client):
    response = client.get("/v1/events", headers={"X-API-Key": "vastra-web"})
    assert response.status_code == 403
