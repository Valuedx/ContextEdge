import pytest

from contextedge import seed_guard
from contextedge.seed_guard import (
    DestructiveResetBlocked,
    require_destructive_reset_allowed,
)


def test_allowed_in_development(monkeypatch):
    monkeypatch.setattr(seed_guard.settings, "app_env", "development")
    monkeypatch.delenv("CONTEXTEDGE_ALLOW_DB_RESET", raising=False)
    require_destructive_reset_allowed("test-script")


def test_blocked_outside_development(monkeypatch):
    monkeypatch.setattr(seed_guard.settings, "app_env", "production")
    monkeypatch.delenv("CONTEXTEDGE_ALLOW_DB_RESET", raising=False)
    with pytest.raises(DestructiveResetBlocked, match="CONTEXTEDGE_ALLOW_DB_RESET"):
        require_destructive_reset_allowed("test-script")


def test_env_override_allows_reset(monkeypatch):
    monkeypatch.setattr(seed_guard.settings, "app_env", "production")
    monkeypatch.setenv("CONTEXTEDGE_ALLOW_DB_RESET", "1")
    require_destructive_reset_allowed("test-script")


def test_override_must_be_exactly_1(monkeypatch):
    monkeypatch.setattr(seed_guard.settings, "app_env", "staging")
    monkeypatch.setenv("CONTEXTEDGE_ALLOW_DB_RESET", "true")
    with pytest.raises(DestructiveResetBlocked):
        require_destructive_reset_allowed("test-script")
