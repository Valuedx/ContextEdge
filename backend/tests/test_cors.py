from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

from contextedge.main import create_app


@patch("contextedge.main.aioredis.from_url")
def test_login_preflight_returns_cors_headers(mock_from_url):
    fake = AsyncMock()
    fake.close = AsyncMock()
    fake.setex = AsyncMock()
    fake.get = AsyncMock(return_value=None)
    mock_from_url.return_value = fake

    app = create_app()
    with TestClient(app) as client:
        response = client.options(
            "/api/v1/auth/login",
            headers={
                "Origin": "http://localhost:3000",
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "content-type,authorization",
            },
        )

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "http://localhost:3000"
