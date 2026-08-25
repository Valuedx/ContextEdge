import pytest
from pydantic import ValidationError

from contextedge.schemas.tenant import LoginRequest, UserCreate


def test_login_request_accepts_username():
    body = LoginRequest(username=" SuperAdmin-ContextEdge ", password="secret-password")

    assert body.username == "superadmin-contextedge"


def test_user_create_accepts_username():
    user = UserCreate(
        username="analyst-ae",
        display_name="Sample Analyst",
        password="analyst-password",
    )

    assert user.username == "analyst-ae"
    assert user.role == "analyst"


@pytest.mark.parametrize(
    "username",
    [
        "",
        "admin@company",
        "admin user",
        "@admin",
        "bad username!",
    ],
)
def test_login_request_rejects_email_or_malformed_username(username: str):
    with pytest.raises(ValidationError):
        LoginRequest(username=username, password="secret-password")
