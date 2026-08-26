"""Playbook steps in the agent projection: a playbook node carries its
current version's bounded steps and trigger conditions, so the agent can
act on it without a second round-trip."""

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch
from uuid import uuid4

import pytest

from contextedge.graph.agent.contracts import AgentGraphAccessScope, GraphNodeRef
from contextedge.graph.agent.hydrators import (
    PLAYBOOK_STEPS_CAP,
    playbook_version_facts,
)
from contextedge.graph.agent.repository import SQLAlchemyAgentGraphRepository


def _version(**kw):
    return SimpleNamespace(
        id=kw.get("id", uuid4()),
        playbook_id=kw.get("playbook_id", uuid4()),
        semantic_version=kw.get("semantic_version", "1.2.0"),
        steps=kw.get(
            "steps",
            [
                {"title": "Check certificate expiry on vpn-gw-east-01"},
                {"text": "Renew the certificate"},
                {"action": "Restart the authentication service"},
            ],
        ),
        trigger_conditions=kw.get(
            "trigger_conditions",
            {"symptoms": ["users cannot log in to the VPN"]},
        ),
        rollback_notes=kw.get("rollback_notes", "Reinstall the previous certificate"),
        playbook_confidence=kw.get("playbook_confidence", 0.82),
        published_at=kw.get("published_at", datetime.now(UTC)),
    )


# --- version fact rendering -------------------------------------------------


def test_version_facts_render_ordered_bounded_steps():
    facts, confidence = playbook_version_facts(_version())
    assert facts["steps"] == [
        "1. Check certificate expiry on vpn-gw-east-01",
        "2. Renew the certificate",
        "3. Restart the authentication service",
    ]
    assert facts["steps_total"] == 3
    assert facts["semantic_version"] == "1.2.0"
    assert facts["trigger_conditions"] == ["users cannot log in to the VPN"]
    assert facts["rollback_notes"] == "Reinstall the previous certificate"
    assert confidence == 0.82


def test_version_facts_cap_steps_but_report_total():
    version = _version(steps=[{"title": f"Step {i}"} for i in range(40)])
    facts, _ = playbook_version_facts(version)
    assert len(facts["steps"]) == PLAYBOOK_STEPS_CAP
    assert facts["steps_total"] == 40


def test_version_facts_bound_step_and_rollback_text():
    version = _version(
        steps=[{"title": "x" * 5_000}],
        rollback_notes="r" * 5_000,
    )
    facts, _ = playbook_version_facts(version)
    assert len(facts["steps"][0]) <= 210  # numbering prefix + 200-char label
    assert len(facts["rollback_notes"]) == 300


def test_version_facts_handle_plain_string_steps_and_empty():
    facts, _ = playbook_version_facts(_version(steps=["restart service", None]))
    assert facts["steps"] == ["1. restart service"]

    facts, confidence = playbook_version_facts(
        _version(steps=[], trigger_conditions={}, rollback_notes=None)
    )
    assert facts["steps"] == []
    assert facts["steps_total"] == 0
    assert "trigger_conditions" not in facts
    assert "rollback_notes" not in facts


# --- repository hydration ---------------------------------------------------


def _playbook_row(version_id, tenant_id):
    return SimpleNamespace(
        id=uuid4(),
        tenant_id=tenant_id,
        domain_id=None,
        workspace_id=None,
        title="SSO Certificate Remediation",
        description="Recovery for expired VPN auth certificates.",
        stable_key="pb-acme-vpn",
        lifecycle_state="approved",
        risk_tier="medium",
        automation_mode="suggest_only",
        current_version_id=version_id,
        expiry_at=None,
        last_validated_at=None,
        created_at=None,
        updated_at=None,
    )


def _scope(tenant_id):
    return AgentGraphAccessScope(
        tenant_id=tenant_id, principal_id=uuid4(), principal_type="user"
    )


def _scalars_result(values):
    result = Mock()
    result.scalars.return_value.all.return_value = values
    return result


async def _hydrate_one(playbook, versions, fallback_versions=None):
    remaining = [
        _scalars_result([playbook]),
        _scalars_result(versions if versions is not None else []),
    ]
    if fallback_versions is not None:
        remaining.append(_scalars_result(fallback_versions))

    async def execute(_stmt):
        if remaining:
            return remaining.pop(0)
        return _scalars_result([])

    db = SimpleNamespace(execute=AsyncMock(side_effect=execute))
    repo = SQLAlchemyAgentGraphRepository(db)
    with patch(
        "contextedge.graph.agent.repository.resolve_excluded_access_policy_ids",
        AsyncMock(return_value=[]),
    ):
        hydrated = await repo.hydrate_nodes(
            [GraphNodeRef(type="playbook", id=playbook.id)],
            _scope(playbook.tenant_id),
        )
    return hydrated, db


@pytest.mark.asyncio
async def test_hydrated_playbook_carries_current_version_steps():
    tenant_id = uuid4()
    version = _version()
    playbook = _playbook_row(version.id, tenant_id)
    version.playbook_id = playbook.id

    hydrated, db = await _hydrate_one(playbook, [version])

    (node,) = hydrated.values()
    assert node.facts["steps"][0].startswith("1. Check certificate expiry")
    assert node.facts["semantic_version"] == "1.2.0"
    assert node.confidence == 0.82
    assert db.execute.await_count == 2  # playbooks + one batched version load


@pytest.mark.asyncio
async def test_foreign_version_is_never_leaked():
    """A stale/corrupt current_version_id pointing at another playbook's
    version must not surface that version's steps."""
    tenant_id = uuid4()
    version = _version()  # playbook_id stays a random uuid ≠ row id
    playbook = _playbook_row(version.id, tenant_id)

    hydrated, _ = await _hydrate_one(playbook, [version])

    (node,) = hydrated.values()
    assert "steps" not in node.facts
    assert node.confidence is None


@pytest.mark.asyncio
async def test_missing_version_row_still_hydrates_playbook():
    tenant_id = uuid4()
    playbook = _playbook_row(uuid4(), tenant_id)

    hydrated, _ = await _hydrate_one(playbook, [])

    (node,) = hydrated.values()
    assert node.label == "SSO Certificate Remediation"
    assert "steps" not in node.facts


@pytest.mark.asyncio
async def test_unpublished_current_falls_back_to_newest_published():
    """N1: an approved playbook whose current_version_id is a draft must
    project the newest published version, not the unreviewed steps."""
    tenant_id = uuid4()
    playbook = _playbook_row(uuid4(), tenant_id)
    draft = _version(
        id=playbook.current_version_id,
        playbook_id=playbook.id,
        semantic_version="2.0.0",
        published_at=None,
        steps=[{"title": "UNREVIEWED DRAFT wipe the endpoint"}],
    )
    published = _version(
        playbook_id=playbook.id,
        semantic_version="1.0.0",
        steps=[{"title": "Restart VPN from published runbook"}],
    )

    hydrated, db = await _hydrate_one(playbook, [draft], fallback_versions=[published])

    (node,) = hydrated.values()
    assert node.facts["semantic_version"] == "1.0.0"
    assert "Restart VPN" in node.facts["steps"][0]
    assert "UNREVIEWED" not in "".join(node.facts["steps"])
    assert db.execute.await_count == 3


def test_version_facts_survive_corrupt_steps_shape():
    """A non-list steps JSONB degrades to no steps — never a TypeError
    that kills the whole projection."""
    facts, _ = playbook_version_facts(_version(steps={"oops": "dict"}))
    assert facts["steps"] == []
    assert facts["steps_total"] == 0
