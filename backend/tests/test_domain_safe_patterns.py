"""Domain-safe pattern mining: patterns are synthesized CONTENT, so a
cluster must never mix episode text across domain boundaries."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch
from uuid import uuid4

import pytest

from contextedge.services.pattern_service import (
    DomainMismatchError,
    _assert_domain_safe_membership,
)
from contextedge.workers.pattern_tasks import _cluster, _domain_predicate


def _membership_db(rows):
    result = Mock()
    result.all.return_value = rows
    return SimpleNamespace(execute=AsyncMock(return_value=result))


# --- service guard (defense in depth) ---------------------------------------


@pytest.mark.asyncio
async def test_guard_rejects_cross_domain_membership():
    tenant_id = uuid4()
    domain_a, domain_b = uuid4(), uuid4()
    ep_a, ep_b = uuid4(), uuid4()
    db = _membership_db([(ep_a, tenant_id, domain_a), (ep_b, tenant_id, domain_b)])

    with pytest.raises(DomainMismatchError, match="belongs to domain"):
        await _assert_domain_safe_membership(db, tenant_id, domain_a, [ep_a, ep_b])


@pytest.mark.asyncio
async def test_guard_allows_global_episodes_in_domain_pattern():
    tenant_id = uuid4()
    domain_a = uuid4()
    ep_a, ep_global = uuid4(), uuid4()
    db = _membership_db([(ep_a, tenant_id, domain_a), (ep_global, tenant_id, None)])

    await _assert_domain_safe_membership(db, tenant_id, domain_a, [ep_a, ep_global])


@pytest.mark.asyncio
async def test_guard_rejects_domain_episode_in_global_pattern():
    """A NULL-domain pattern is visible to ALL domains — domain-scoped
    content in it would leak everywhere."""
    tenant_id = uuid4()
    ep = uuid4()
    db = _membership_db([(ep, tenant_id, uuid4())])

    with pytest.raises(DomainMismatchError, match="GLOBAL"):
        await _assert_domain_safe_membership(db, tenant_id, None, [ep])


@pytest.mark.asyncio
async def test_guard_hides_foreign_tenant_episodes_as_missing():
    """Cross-tenant probes get the same error as nonexistent ids — never
    confirm another tenant's episode exists."""
    tenant_id = uuid4()
    ep_foreign, ep_missing = uuid4(), uuid4()
    db = _membership_db([(ep_foreign, uuid4(), None)])

    with pytest.raises(DomainMismatchError, match="does not exist"):
        await _assert_domain_safe_membership(db, tenant_id, None, [ep_foreign])
    with pytest.raises(DomainMismatchError, match="does not exist"):
        await _assert_domain_safe_membership(
            _membership_db([]), tenant_id, None, [ep_missing]
        )


# --- miner scoping ----------------------------------------------------------


def test_domain_predicate_is_strict_per_scope():
    domain = uuid4()
    assert "domain_id =" in str(_domain_predicate(domain))
    assert "domain_id IS NULL" in str(_domain_predicate(None))


def _empty_scalars():
    result = Mock()
    result.scalars.return_value.all.return_value = []
    return result


def _empty_rows():
    result = Mock()
    result.all.return_value = []
    return result


@pytest.mark.asyncio
async def test_cluster_queries_are_domain_scoped():
    """The candidates query of a domain pass must carry the strict domain
    filter; the global pass must carry IS NULL. Tenant-wide mining tagged
    with the requested domain was the leak this replaces."""
    captured: list[str] = []

    async def execute(stmt):
        captured.append(str(stmt))
        return _empty_rows() if "pattern_evidence_links" in str(stmt) else _empty_scalars()

    tid, did = uuid4(), uuid4()
    result = await _cluster(SimpleNamespace(execute=execute), tid, did)
    assert result["patterns_created"] == 0
    candidates_sql = captured[-1]
    assert "episodes.domain_id = " in candidates_sql

    captured.clear()
    await _cluster(SimpleNamespace(execute=execute), tid, None)
    assert "episodes.domain_id IS NULL" in captured[-1]


@pytest.mark.asyncio
async def test_create_pattern_guard_fires_before_any_write():
    from contextedge.services.pattern_service import create_pattern_from_episodes

    tenant_id = uuid4()
    ep = uuid4()
    membership = Mock()
    membership.all.return_value = [(ep, tenant_id, uuid4())]  # foreign domain
    added = []
    db = SimpleNamespace(
        execute=AsyncMock(return_value=membership),
        add=added.append,
        flush=AsyncMock(),
    )

    with pytest.raises(DomainMismatchError):
        await create_pattern_from_episodes(
            db, tenant_id, None, "Mixed", [ep]
        )
    assert added == []  # no Pattern row, no links — nothing written
