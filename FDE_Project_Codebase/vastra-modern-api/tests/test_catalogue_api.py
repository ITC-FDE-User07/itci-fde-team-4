def test_catalogue_requires_api_key(client):
    response = client.get("/v1/catalogue/SKU00003")
    assert response.status_code == 401


def test_catalogue_returns_product_and_cache_headers(client):
    response = client.get(
        "/v1/catalogue/SKU00003",
        headers={"X-API-Key": "vastra-web"},
    )
    assert response.status_code == 200
    assert response.json()["sku"] == "SKU00003"
    assert response.headers["ETag"].startswith('"')
    assert response.headers["Cache-Control"].startswith("public")
