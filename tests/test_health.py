def test_health(client):
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_root(client):
    response = client.get("/")
    assert response.status_code == 200
    assert "Fingoh" in response.json()["message"]


def test_protected_route_without_token():
    """Unauthenticated request to a protected route should return 403."""
    from fastapi.testclient import TestClient
    from app.main import app
    # Use a fresh client with no overrides
    with TestClient(app, raise_server_exceptions=False) as c:
        response = c.get("/api/v1/events/")
    assert response.status_code in (401, 403)
