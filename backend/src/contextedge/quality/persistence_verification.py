"""Shared persistence checks for the playbook-quality layer.

Used by ``scripts/verify_quality_persistence.py`` and by the integration
test in ``tests/test_playbook_quality_persistence_integration.py``. The unit
suite covers pure logic; this module covers SQL that only a real PostgreSQL
can exercise.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field

from sqlalchemy.ext.asyncio import AsyncSession

from contextedge.models.playbook import Playbook, PlaybookVersion
from contextedge.quality import ValidationContext, assess, build_content
from contextedge.quality.hashing import content_hash
from contextedge.services import playbook_quality_service as svc


@dataclass
class PersistenceVerificationResult:
    passed: list[str] = field(default_factory=list)
    failed: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.failed


def _check(
    result: PersistenceVerificationResult,
    label: str,
    condition: bool,
    detail: str = "",
) -> None:
    if condition:
        result.passed.append(label)
    else:
        result.failed.append(f"{label}{(' — ' + detail) if detail else ''}")


def _steps() -> list[dict]:
    return [
        {
            "step_id": "s1",
            "order": 1,
            "type": "remediation",
            "text": "Restart the AutomationEdge Agent service on the affected host.",
        },
        {
            "step_id": "s2",
            "order": 2,
            "type": "verification",
            "text": "Confirm the agent shows Running in the console.",
        },
    ]


async def verify_quality_persistence(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    *,
    owner_user_id: uuid.UUID | None = None,
) -> PersistenceVerificationResult:
    """Run every persistence check inside the caller's transaction.

    The caller must ``rollback()`` when using a live database — nothing here is
    kept on purpose.
    """
    result = PersistenceVerificationResult()
    owner = owner_user_id or uuid.uuid4()

    playbook = Playbook(
        tenant_id=tenant_id,
        stable_key=f"pb-verify-{uuid.uuid4().hex[:8]}",
        title="Quality persistence verification",
        description="Created and rolled back by persistence verification",
        owner_user_id=owner,
        lifecycle_state="candidate",
    )
    db.add(playbook)
    await db.flush()

    version = PlaybookVersion(
        tenant_id=tenant_id,
        playbook_id=playbook.id,
        semantic_version="0.1.0",
        steps=_steps(),
    )
    db.add(version)
    await db.flush()
    playbook.current_version_id = version.id
    await db.flush()

    first = await svc.ensure_content_revision(db, playbook, version, origin="generation")
    again = await svc.ensure_content_revision(db, playbook, version, origin="generation")
    _check(
        result,
        "identical content is one revision",
        first.id == again.id,
        "the unique constraint and the fast path agree",
    )
    _check(result, "revision numbering starts at 1", first.revision_number == 1)

    playbook.title = "Quality persistence verification (retitled)"
    shell_edit = await svc.ensure_content_revision(db, playbook, version, origin="shell_edit")
    _check(
        result,
        "a title-only edit mints a new revision",
        shell_edit.id != first.id and shell_edit.revision_number == 2,
    )

    content = build_content(playbook, version)
    outcome = assess(
        ValidationContext(
            content=content,
            content_hash=content_hash(content),
            playbook_id=str(playbook.id),
            tenant_id=str(tenant_id),
        )
    )
    _check(
        result,
        "no unbuilt validator produces a pass",
        outcome.overall_state != "pass",
        f"state={outcome.overall_state}",
    )

    older = await svc.record_assessment(db, playbook, shell_edit, outcome)
    newer = await svc.record_assessment(db, playbook, shell_edit, outcome)
    await db.flush()
    await db.refresh(older)
    _check(
        result,
        "recording supersedes rather than overwrites",
        older.superseded_at is not None and older.superseded_by_id == newer.id,
    )
    _check(result, "the new assessment is the open one", newer.superseded_at is None)

    findings = await svc.findings_for(db, tenant_id, newer.id)
    rank = {name: index for index, name in enumerate(("critical", "major", "minor", "info"))}
    severities = [f.severity for f in findings]
    _check(
        result,
        "findings come back worst-first",
        severities == sorted(severities, key=lambda s: rank.get(s, 99)),
        str(severities[:6]),
    )
    counts = await svc.finding_counts_for(db, tenant_id, [older.id, newer.id])
    _check(
        result,
        "the batched histogram totals match",
        sum(counts[newer.id].values()) == len(findings),
        str(counts[newer.id]),
    )
    current = await svc.assessments_for_playbooks(db, tenant_id, [playbook.id])
    _check(
        result,
        "the batch lookup returns the open assessment",
        current.get(playbook.id) is not None and current[playbook.id].id == newer.id,
    )

    summary = svc.summarize(
        newer, live_content_hash=shell_edit.content_hash, finding_counts=counts[newer.id]
    )
    _check(result, "summary matches live content", summary["matches_current_content"] is True)
    _check(
        result,
        "summary exposes the three independent decisions",
        set(summary["groups"]) == {"subject", "steps", "coherence"},
        str(summary["groups"]),
    )
    _check(
        result,
        "structure is reported outside the three groups",
        "structure" in summary and "structure" not in summary["groups"],
        f"structure={summary['structure']!r}",
    )
    _check(
        result,
        "coverage reports mostly-undecided in this bundle",
        summary["coverage"]["undecided"] > summary["coverage"]["decided"],
        str(summary["coverage"]),
    )
    stale_view = svc.summarize(newer, live_content_hash="0" * 64)
    _check(
        result,
        "a moved content hash is reported as not current",
        stale_view["matches_current_content"] is False,
    )
    readiness = await svc.publication_readiness(db, playbook, version)
    _check(
        result,
        "publication readiness refuses, with a reason",
        readiness["ready"] is False and bool(readiness["blocked_reason"]),
        str(readiness["blocked_reason"]),
    )

    changed = await svc.invalidate_assessments(
        db, tenant_id, playbook.id, reason=svc.STALE_SOURCE_CHANGED
    )
    await db.refresh(newer)
    _check(result, "invalidation marks exactly the open assessment", changed == 1)
    _check(
        result,
        "invalidation means stale, not fail",
        newer.overall_state == "stale"
        and newer.stale_reason == svc.STALE_SOURCE_CHANGED
        and newer.stale_at is not None,
    )

    report = await svc.quality_report(db, playbook, version)
    _check(
        result,
        "the report carries every key the endpoint reads",
        set(report)
        == {"playbook_id", "content_hash", "assessment", "summary", "findings", "readiness"},
    )

    return result
