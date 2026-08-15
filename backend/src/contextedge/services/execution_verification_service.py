"""Post-action verification: did the fix actually hold?

``PlaybookVersion.verification_policy`` has promised "re-check telemetry
30 min post-action" since it was introduced — this module keeps that
promise. After an execution run completes with a success/partial
outcome, the sweep re-checks operational reality on the CIs the run's
session concerns:

- **New incidents** on those CIs after completion (Phase 1 ``affects_ci``
  edges, incident-kind threads) — someone reported the problem again.
- **New alert activity** after completion (Phase 3 alert-rollup evidence
  rows, timestamped by their batch) — telemetry says the trouble is
  still happening.

Either signal → ``verification_status = failed`` with counts in the
details; neither → ``verified``. A run whose session names no resolvable
CI (or has no session) is ``unverifiable`` — recorded honestly, never
pretended verified.

Deterministic throughout: no LLM call, every verdict carries its
rationale. ``auto_close_on_success`` does NOT close sessions — it emits
an ``execution.auto_close_recommended`` operational event; closing a
human's session automatically is a bigger decision than a telemetry
recheck should make alone.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import structlog
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from contextedge.models.entity import Entity
from contextedge.models.evidence import EvidenceItem, Thread
from contextedge.models.execution import ExecutionRun
from contextedge.models.pattern import GraphEdge
from contextedge.models.playbook import PlaybookVersion
from contextedge.models.session import ResolutionSession
from contextedge.services.event_log_service import append_operational_event

logger = structlog.get_logger()

DEFAULT_RECHECK_AFTER_SEC = 1_800
# The sweep only considers runs at least this old — a per-playbook policy
# can lengthen the recheck delay but not shorten it below this floor.
MIN_RECHECK_FLOOR_SEC = 300
MAX_SESSION_ENTITY_TERMS = 10
VERIFIABLE_OUTCOMES = ("success", "partial")


def _recheck_after_sec(version: PlaybookVersion | None) -> int:
    policy = (version.verification_policy or {}) if version is not None else {}
    try:
        value = int(policy.get("recheck_after_sec") or DEFAULT_RECHECK_AFTER_SEC)
    except (TypeError, ValueError):
        value = DEFAULT_RECHECK_AFTER_SEC
    return max(value, MIN_RECHECK_FLOOR_SEC)


def _auto_close_on_success(version: PlaybookVersion | None) -> bool:
    policy = (version.verification_policy or {}) if version is not None else {}
    return bool(policy.get("auto_close_on_success"))


async def _resolve_session_cis(
    db: AsyncSession, tenant_id: uuid.UUID, session: ResolutionSession
) -> list[Entity]:
    """The session's ``entities`` list (operational nouns recorded on the
    case) resolved against the entities table by exact case-insensitive
    name — the same matching contract seed-resolution Layer C uses."""
    terms = [
        str(term).strip().lower()
        for term in (session.entities or [])[:MAX_SESSION_ENTITY_TERMS]
        if isinstance(term, str) and str(term).strip()
    ]
    if not terms:
        return []
    rows = (
        await db.execute(
            select(Entity).where(
                Entity.tenant_id == tenant_id,
                Entity.is_active.is_(True),
                func.lower(Entity.name).in_(terms),
            )
        )
    ).scalars().all()
    return list(rows)


async def _post_action_signals(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    entity_ids: list[uuid.UUID],
    completed_at: datetime,
) -> dict:
    """Incident threads and alert batches on the CIs strictly AFTER the
    run completed. Timestamps come from ``created_at_source`` (the
    record's own clock) with ingestion time as fallback."""
    rows = (
        await db.execute(
            select(EvidenceItem.id, Thread.external_thread_id)
            .join(
                GraphEdge,
                (GraphEdge.source_node_type == "evidence")
                & (GraphEdge.source_node_id == EvidenceItem.id),
            )
            .join(Thread, EvidenceItem.thread_id == Thread.id)
            .where(
                GraphEdge.tenant_id == tenant_id,
                GraphEdge.edge_type == "affects_ci",
                GraphEdge.target_node_type == "entity",
                GraphEdge.target_node_id.in_(tuple(entity_ids)),
                GraphEdge.valid_to.is_(None),
                EvidenceItem.tenant_id == tenant_id,
                func.coalesce(EvidenceItem.created_at_source, EvidenceItem.created_at)
                > completed_at,
            )
            .distinct()
            .limit(500)
        )
    ).all()

    incident_threads: set[str] = set()
    alert_evidence_ids: list[uuid.UUID] = []
    for evidence_id, thread_key in rows:
        kind = (thread_key or "").split(":", 1)[0]
        if kind == "incident":
            incident_threads.add(thread_key)
        elif kind == "em_alert_rollup":
            alert_evidence_ids.append(evidence_id)
    return {
        "new_incidents": len(incident_threads),
        "new_alert_batches": len(alert_evidence_ids),
        "alert_evidence_ids": alert_evidence_ids,
    }


# Payload loads are bounded when confirming alert batches — the deciding
# case is rare (alerts but no incidents) and one confirmed batch settles it.
ALERT_CONFIRM_CAP = 10


async def _confirm_alert_batches(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    alert_evidence_ids: list[uuid.UUID],
    completed_at: datetime,
) -> int:
    """Count alert batches whose alerts actually FIRED after completion.

    Rollup evidence is timestamped by ingestion/batch time, but alert
    state changes re-deliver OLD alerts — a closing storm right after a
    successful fix would otherwise read as post-action trouble and fail
    the verification. The rollup payload carries ``last_event_time``
    (the alerts' own clock); only batches whose events post-date the
    completion count. A payload that cannot be read or parsed counts —
    malformed data fails toward attention, not toward a false pass.
    """
    from contextedge.models.evidence import RawEvidenceObject
    from contextedge.services.artifact_extraction_service import load_raw_payload

    confirmed = 0
    for evidence_id in alert_evidence_ids[:ALERT_CONFIRM_CAP]:
        try:
            evidence = await db.get(EvidenceItem, evidence_id)
            if evidence is None or evidence.raw_object_ref is None:
                confirmed += 1
                continue
            raw = await db.get(RawEvidenceObject, evidence.raw_object_ref)
            if raw is None:
                confirmed += 1
                continue
            payload = await load_raw_payload(raw)
            last_event = datetime.strptime(
                str(payload.get("last_event_time")), "%Y-%m-%d %H:%M:%S"
            ).replace(tzinfo=UTC)
            if last_event > completed_at:
                confirmed += 1
        except Exception:
            confirmed += 1
    return confirmed


async def verify_execution_run(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    run: ExecutionRun,
    *,
    now: datetime | None = None,
) -> dict:
    """Verify one completed run. Persists the verdict onto the run and
    emits an operational event; returns a status dict.

    ``not_due`` is the only non-persisting outcome — the run stays in the
    sweep queue until its policy's recheck delay has elapsed.
    """
    now = now or datetime.now(UTC)

    if run.status != "completed" or run.outcome not in VERIFIABLE_OUTCOMES:
        return {"status": "skipped", "reason": "not_a_verifiable_outcome"}
    if run.completed_at is None:
        return {"status": "skipped", "reason": "no_completion_time"}

    version = await db.get(PlaybookVersion, run.playbook_version_id)
    recheck_after = _recheck_after_sec(version)
    completed_at = run.completed_at
    if completed_at.tzinfo is None:
        completed_at = completed_at.replace(tzinfo=UTC)
    if now < completed_at + timedelta(seconds=recheck_after):
        due_at = completed_at + timedelta(seconds=recheck_after)
        return {"status": "not_due", "due_at": due_at.isoformat()}

    session = None
    if run.session_id is not None:
        session = await db.get(ResolutionSession, run.session_id)
        if session is not None and session.tenant_id != tenant_id:
            session = None

    cis = (
        await _resolve_session_cis(db, tenant_id, session)
        if session is not None
        else []
    )

    if not cis:
        verdict = "unverifiable"
        details: dict = {
            "reason": "no_resolvable_cis",
            "session_id": str(run.session_id) if run.session_id else None,
        }
    else:
        signals = await _post_action_signals(
            db, tenant_id, [ci.id for ci in cis], completed_at
        )
        alert_evidence_ids = signals.pop("alert_evidence_ids")
        if signals["new_incidents"] == 0 and signals["new_alert_batches"] > 0:
            # Alerts are the deciding signal — confirm by the alerts' own
            # event times so re-delivered old alerts (state changes after
            # a successful fix) can't produce a false failure.
            signals["new_alert_batches"] = await _confirm_alert_batches(
                db, tenant_id, alert_evidence_ids, completed_at
            )
            signals["alert_batches_confirmed_by_event_time"] = True
        failed = signals["new_incidents"] > 0 or signals["new_alert_batches"] > 0
        verdict = "failed" if failed else "verified"
        details = {
            **signals,
            "checked_cis": [ci.name for ci in cis],
            "recheck_after_sec": recheck_after,
        }

    run.verification_status = verdict
    run.verified_at = now
    run.verification_details = details
    await db.flush()

    # B5: a verified/failed verdict is a fix OUTCOME for every fix
    # pattern recommending this playbook, counted per cohort of each
    # resolved CI. Fail-soft — cohort accounting never breaks the
    # verification sweep.
    if verdict in ("verified", "failed") and version is not None and cis:
        try:
            from contextedge.models.error_signature import FixPattern as _FixPattern
            from contextedge.services.fix_cohort_service import record_fix_outcome

            fix_ids = (
                (
                    await db.execute(
                        select(_FixPattern.id).where(
                            _FixPattern.tenant_id == tenant_id,
                            _FixPattern.recommended_playbook_id == version.playbook_id,
                        ).limit(10)
                    )
                )
                .scalars()
                .all()
            )
            for fix_id in fix_ids:
                for ci in cis[:5]:
                    await record_fix_outcome(
                        db, tenant_id, fix_id, ci, verdict == "verified"
                    )
        except Exception as exc:
            logger.warning(
                "fix_cohort.recording_failed",
                tenant_id=str(tenant_id),
                execution_run_id=str(run.id),
                error=str(exc),
            )

    # F4: this verdict is exactly what changes the empirical support of the
    # knowledge this playbook version was built on, so it is refreshed here
    # rather than on a sweep that would recompute a whole corpus to catch a
    # handful of changed rows. Fail-soft for the same reason as the cohort
    # write-back above — a ranking input must never break the verification.
    if verdict in ("verified", "failed") and version is not None:
        version_id = None
        try:
            from contextedge.services.knowledge_validation_service import (
                refresh_support_for_playbook_version,
            )

            # Inside the try, not outside: a partially-loaded version is one
            # of the failures this block exists to absorb, and reading its id
            # is part of the operation rather than a precondition of it.
            version_id = version.id
            await refresh_support_for_playbook_version(db, tenant_id, version_id)
        except Exception as exc:
            logger.warning(
                "knowledge_support.refresh_failed",
                tenant_id=str(tenant_id),
                playbook_version_id=str(version_id) if version_id else None,
                error=str(exc),
            )

    await append_operational_event(
        db,
        tenant_id=tenant_id,
        entity_type="execution_run",
        entity_id=run.id,
        event_type="execution.verification_completed",
        payload={"verdict": verdict, **details},
    )

    if verdict == "verified" and _auto_close_on_success(version) and session is not None:
        # Recommend, never act: closing a human's session automatically is
        # a bigger decision than a telemetry recheck should make alone.
        await append_operational_event(
            db,
            tenant_id=tenant_id,
            entity_type="session",
            entity_id=session.id,
            event_type="execution.auto_close_recommended",
            payload={
                "execution_run_id": str(run.id),
                "verdict": verdict,
            },
        )

    logger.info(
        "execution.verified",
        tenant_id=str(tenant_id),
        execution_run_id=str(run.id),
        verdict=verdict,
    )
    return {"status": verdict, "details": details}
