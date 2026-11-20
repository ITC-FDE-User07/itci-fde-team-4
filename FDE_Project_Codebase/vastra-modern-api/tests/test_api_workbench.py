def test_browser_workbench_is_served(client):
    response = client.get("/ui/")
    assert response.status_code == 200
    assert "Vastra API Workbench" in response.text
    assert "Run read-only smoke checks" in response.text
