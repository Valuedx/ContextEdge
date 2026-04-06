import pytest
from pydantic import ValidationError

from contextedge.schemas.tenant import LoginRequest, UserCreate


def test_login_request_accepts_seeded_local_email():
    body = LoginRequest(email=" Admin@ContextEdge.Local ", password="admin123")

    assert body.email == "admin@contextedge.local"


def test_user_create_accepts_local_email():
    user = UserCreate(
        email="analyst@contextedge.local",
        display_name="Sample Analyst",
        password="analyst123",
    )

    assert user.email == "analyst@contextedge.local"


@pytest.mark.parametrize(
    "email",
    [
        "",
        "admin",
        "admin@",
        "@contextedge.local",
        "admin @contextedge.local",
        "admin@@contextedge.local",
        "admin@.local",
    ],
)
def test_login_request_rejects_malformed_email(email: str):
    with pytest.raises(ValidationError):
        LoginRequest(email=email, password="admin123")
