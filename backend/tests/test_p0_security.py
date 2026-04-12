import importlib
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest
from fastapi import HTTPException

from .conftest import make_user
from contextedge.ai import provider
from contextedge.api.v1 import patterns, playbooks


@pytest.mark.asyncio
async def test_patterns_rbac():
    with pytest.raises(HTTPException) as exc_info:
        await patterns.discover_pattern(
            patterns.PatternDiscoverRequest(episode_ids=[uuid4()]),
            db=SimpleNamespace(),
            user=make_user(roles=[]),
        )
    assert exc_info.value.status_code == 403
    assert exc_info.value.detail == "Role 'domain_admin' required"


@pytest.mark.asyncio
async def test_playbooks_generate_rbac():
    with pytest.raises(HTTPException) as exc_info:
        await playbooks.generate_playbook(
            playbooks.GeneratePlaybookRequest(pattern_id=uuid4()),
            db=SimpleNamespace(),
            user=make_user(roles=[]),
        )
    assert exc_info.value.status_code == 403
    assert exc_info.value.detail == "Role 'knowledge_manager' required"


@pytest.mark.asyncio
async def test_llm_json_parse_error():
    with patch.object(provider, "llm_complete", AsyncMock(return_value="not-json")):
        with pytest.raises(ValueError, match="invalid JSON"):
            await provider.llm_complete_json("prompt", task="extraction")


def test_config_rejects_default_jwt_secret_in_non_development(monkeypatch):
    import contextedge.config as config_module

    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("JWT_SECRET_KEY", "change-me-in-production")

    try:
        with pytest.raises(RuntimeError, match="JWT_SECRET_KEY must be changed"):
            importlib.reload(config_module)
    finally:
        monkeypatch.setenv("APP_ENV", "development")
        monkeypatch.delenv("JWT_SECRET_KEY", raising=False)
        importlib.reload(config_module)
