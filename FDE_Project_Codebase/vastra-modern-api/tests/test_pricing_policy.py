import pytest


@pytest.mark.parametrize("api_key", ["vastra-web", "vastra-app", "vastra-pos"])
def test_documented_price_is_consistent_across_channels(client, api_key):
    response = client.get(
        "/v1/prices/SKU00003",
        params={"coupon_pct": 10, "member": True},
        headers={"X-API-Key": api_key},
    )

    assert response.status_code == 200
    assert response.json()["unit_price"] == 1296
    assert response.json()["breakdown"] == [
        {"step": "sale", "amount": 1600},
        {"step": "coupon", "pct": 10, "amount": 1440},
        {"step": "member", "pct": 10, "amount": 1296},
    ]


def test_price_endpoint_returns_quantity_total_and_requires_price_role(client):
    response = client.get(
        "/v1/prices/SKU00003",
        params={"coupon_pct": 10, "member": True, "qty": 2},
        headers={"X-API-Key": "vastra-web"},
    )
    assert response.status_code == 200
    assert response.json()["qty"] == 2
    assert response.json()["total"] == 2592

    forbidden = client.get(
        "/v1/prices/SKU00003",
        headers={"X-API-Key": "vastra-warehouse"},
    )
    assert forbidden.status_code == 403
