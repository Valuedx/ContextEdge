from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

from contextedge.main import create_app


@patch("contextedge.main.aioredis.from_url")
def test_health_ok(mock_from_url):
    fake = AsyncMock()
    fake.close = AsyncMock()
    fake.setex = AsyncMock()
    fake.get = AsyncMock(return_value=None)
    mock_from_url.return_value = fake

    app = create_app()
    with TestClient(app) as client:
        r = client.get("/health")
    assert r.status_code == 200
    assert r.json().get("status") == "healthy"
