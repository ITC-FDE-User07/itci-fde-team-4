def test_named_store_override_is_audited(client):
    response = client.post(
        "/v1/overrides",
        json={
            "sku": "SKU00003",
            "store_id": "ST001",
            "price": 999,
            "reason": "Local promotion",
            "expires_at": "2099-12-31T23:59:59+00:00",
        },
        headers={"X-API-Key": "vastra-pos", "X-User": "manager-07"},
    )
    assert response.status_code == 200

    audit = client.get("/v1/audit/overrides", headers={"X-API-Key": "vastra-pos"})
    assert audit.status_code == 200
    assert audit.json()[0]["actor"] == "manager-07"
    assert audit.json()[0]["reason"] == "Local promotion"


def test_override_is_store_scoped_and_requires_pos_role(client):
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
    store_price = client.get(
        "/v1/prices/SKU00003",
        params={"store_id": "ST001"},
        headers={"X-API-Key": "vastra-pos"},
    )
    regular_price = client.get(
        "/v1/prices/SKU00003",
        headers={"X-API-Key": "vastra-pos"},
    )
    forbidden = client.post(
        "/v1/overrides",
        json={
            "sku": "SKU00003",
            "store_id": "ST002",
            "price": 999,
            "reason": "Should fail",
            "expires_at": "2099-12-31T23:59:59+00:00",
        },
        headers={"X-API-Key": "vastra-web", "X-User": "web-user"},
    )

    assert store_price.json()["unit_price"] == 999
    assert regular_price.json()["unit_price"] == 1600
    assert forbidden.status_code == 403
