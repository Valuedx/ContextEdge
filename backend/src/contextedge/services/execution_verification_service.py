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
from contextedge.models.verification import (
    VerificationAssessment,
    VerificationObservation,
)
from contextedge.services.event_log_service import append_operational_event
from contextedge.services.verification_criteria_service import (
    CriterionResult,
    Verdict,
    aggregate,
    legacy_status,
)

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


# How far back to look for evidence that a CI produces signals at all. If a
# CI has been silent for this long BEFORE the run too, its silence afterwards
# says nothing about the fix — that is the difference between "recovered" and
# "was never watched", and conflating them is what F9 fixes.
OBSERVABILITY_LOOKBACK_DAYS = 30


async def _ci_ever_observable(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    entity_ids: list[uuid.UUID],
    completed_at: datetime,
) -> bool:
    """Did any of these CIs produce an incident or alert before the run?

    Absence of a signal from a source that never produced one is not evidence
    the fix held. This is the check that turns the old sweep's most dangerous
    silent pass into an honest ``inconclusive``.
    """
    if not entity_ids:
        return False
    since = completed_at - timedelta(days=OBSERVABILITY_LOOKBACK_DAYS)
    found = (
        await db.execute(
            select(EvidenceItem.id)
            .join(
                GraphEdge,
                (GraphEdge.source_node_type == "evidence")
                & (GraphEdge.source_node_id == EvidenceItem.id),
            )
            .where(
                GraphEdge.tenant_id == tenant_id,
                GraphEdge.edge_type == "affects_ci",
                GraphEdge.target_node_type == "entity",
                GraphEdge.target_node_id.in_(tuple(entity_ids)),
                GraphEdge.valid_to.is_(None),
                EvidenceItem.tenant_id == tenant_id,
                func.coalesce(EvidenceItem.created_at_source, EvidenceItem.created_at)
                <= completed_at,
                func.coalesce(EvidenceItem.created_at_source, EvidenceItem.created_at)
                >= since,
            )
            .limit(1)
        )
    ).first()
    return found is not None


def _conversation_on_cis(tenant_id: uuid.UUID, entity_ids: list[uuid.UUID], after: datetime):
    """Classified conversational evidence about these CIs, after *after*.

    Scoped through ``affects_ci`` — the same join the absence criteria use —
    rather than through the case, because ``CaseLink`` keys on the
    correlation layer's canonical case id, not on ``ResolutionSession.id``.
    Inventing a session↔evidence join here would be inventing the wrong one.
    """
    return (
        select(func.count())
        .select_from(EvidenceItem)
        .join(
            GraphEdge,
            (GraphEdge.source_node_type == "evidence")
            & (GraphEdge.source_node_id == EvidenceItem.id),
        )
        .where(
            GraphEdge.tenant_id == tenant_id,
            GraphEdge.edge_type == "affects_ci",
            GraphEdge.target_node_type == "entity",
            GraphEdge.target_node_id.in_(tuple(entity_ids)),
            GraphEdge.valid_to.is_(None),
            EvidenceItem.tenant_id == tenant_id,
            func.coalesce(EvidenceItem.created_at_source, EvidenceItem.created_at) > after,
        )
    )


async def _user_confirmation_after(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    entity_ids: list[uuid.UUID],
    completed_at: datetime,
) -> int | None:
    """Count post-completion messages a classifier read as confirmation.

    The first POSITIVE signal in the verification plane: somebody said it
    worked, rather than nothing said it broke. Uses ``message_function``,
    which the A1 classifier already writes at ingest — no new extraction, no
    new model call on the verification path.

    Returns None when there was no classified conversation at all, so "nobody
    confirmed" and "there was nobody to confirm" stay distinguishable — the
    same distinction the absence criteria draw between quiet and unwatched.
    """
    if not entity_ids:
        return None
    total = (
        await db.execute(
            _conversation_on_cis(tenant_id, entity_ids, completed_at).where(
                EvidenceItem.message_function.is_not(None)
            )
        )
    ).scalar_one_or_none()
    if not total:
        return None
    confirmations = (
        await db.execute(
            _conversation_on_cis(tenant_id, entity_ids, completed_at).where(
                EvidenceItem.message_function == "resolution_confirmation"
            )
        )
    ).scalar_one_or_none()
    return int(confirmations or 0)


async def _evaluate_criteria(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    *,
    cis: list[Entity],
    completed_at: datetime,
) -> list[CriterionResult]:
    """Evaluate every criterion for this run, independently."""
    results: list[CriterionResult] = []
    ci_names = ", ".join(ci.name for ci in cis) or "—"

    if not cis:
        # Both absence criteria are unevaluable without a CI to watch. Two
        # rows rather than one, because "we could not check incidents" and
        # "we could not check alerts" are separately true.
        for criterion_type, label in (
            ("incident_absence", "no new incidents"),
            ("alert_absence", "no new alert activity"),
        ):
            results.append(
                CriterionResult(
                    criterion_type=criterion_type,
                    criterion_name=f"{label} on the case's CIs",
                    status="not_observable",
                    detail="the case names no resolvable CI",
                    window_start=completed_at,
                )
            )
    else:
        entity_ids = [ci.id for ci in cis]
        signals = await _post_action_signals(db, tenant_id, entity_ids, completed_at)
        alert_evidence_ids = signals.pop("alert_evidence_ids")
        if signals["new_incidents"] == 0 and signals["new_alert_batches"] > 0:
            # Alerts are the deciding signal — confirm by the alerts' own
            # event times so re-delivered old alerts (state changes after a
            # successful fix) cannot produce a false failure.
            signals["new_alert_batches"] = await _confirm_alert_batches(
                db, tenant_id, alert_evidence_ids, completed_at
            )
        observable = await _ci_ever_observable(db, tenant_id, entity_ids, completed_at)

        for criterion_type, label, count in (
            ("incident_absence", "no new incidents", signals["new_incidents"]),
            ("alert_absence", "no new alert activity", signals["new_alert_batches"]),
        ):
            if count > 0:
                status, detail = "fail", f"{count} observed after completion"
            elif observable:
                status, detail = "pass", "none observed, and this CI does report"
            else:
                # The silent pass the old sweep called `verified`.
                status = "not_observable"
                detail = (
                    "none observed, but no incident or alert has been seen on this "
                    f"CI in the last {OBSERVABILITY_LOOKBACK_DAYS} days either — "
                    "silence here is not evidence"
                )
            results.append(
                CriterionResult(
                    criterion_type=criterion_type,
                    criterion_name=f"{label} on {ci_names}",
                    status=status,
                    criterion_params={
                        "cis": [str(i) for i in entity_ids],
                        "observability_lookback_days": OBSERVABILITY_LOOKBACK_DAYS,
                    },
                    observed_value={"count": count, "ci_observable": observable},
                    detail=detail,
                    window_start=completed_at,
                )
            )

    confirmations = await _user_confirmation_after(
        db, tenant_id, [ci.id for ci in cis], completed_at
    )
    if confirmations is None:
        status, detail = "not_observable", "no conversational evidence on this case"
    elif confirmations > 0:
        status, detail = "pass", f"{confirmations} confirmation message(s) after completion"
    else:
        status, detail = "inconclusive", "conversation continued but nobody confirmed"
    results.append(
        CriterionResult(
            criterion_type="user_confirmation",
            criterion_name="someone confirmed the issue is resolved",
            status=status,
            observed_value={"confirmations": confirmations},
            detail=detail,
            window_start=completed_at,
        )
    )
    return results


async def _persist_assessment(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    *,
    run: ExecutionRun,
    outcome: Verdict,
    results: list[CriterionResult],
    now: datetime,
) -> VerificationAssessment:
    assessment = VerificationAssessment(
        tenant_id=tenant_id,
        execution_run_id=run.id,
        overall_result=outcome.overall_result,
        summary=outcome.summary,
        rollback_recommended=outcome.rollback_recommended,
        retry_recommended=outcome.retry_recommended,
        escalation_required=outcome.escalation_required,
        verified_by="execution_verification_sweep",
        verified_at=now,
    )
    db.add(assessment)
    await db.flush()
    for result in results:
        db.add(
            VerificationObservation(
                tenant_id=tenant_id,
                assessment_id=assessment.id,
                criterion_type=result.criterion_type,
                criterion_name=result.criterion_name[:200],
                criterion_params=result.criterion_params,
                status=result.status,
                observed_value=result.observed_value,
                detail=result.detail,
                window_start=result.window_start,
                window_end=now,
            )
        )
    await db.flush()
    return assessment


async def _record_trust_outcomes(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    *,
    run: ExecutionRun,
    outcome: Verdict,
    cis: list[Entity],
) -> int:
    """Fold this verdict into the trust record for each scope it touched (F10).

    One outcome per (action type × CI class × environment × criticality) the
    run acted on — not one per run. A playbook that restarts a service and
    also patches a database has two track records, and averaging them is how
    the easy action vouches for the hard one.

    The action types come from the run's own steps, so a run whose steps
    declared nothing records against ``unspecified`` rather than guessing:
    an unscoped record is honest, an invented scope is not.
    """
    from contextedge.models.execution import ExecutionStepRun
    from contextedge.services.trust_service import record_outcome, scope_key

    step_rows = (
        (
            await db.execute(
                select(ExecutionStepRun.action_type).where(
                    ExecutionStepRun.execution_run_id == run.id,
                    ExecutionStepRun.tenant_id == tenant_id,
                )
            )
        )
        .scalars()
        .all()
    )
    action_types = {a for a in step_rows if a} or {None}

    # Class and criticality come from the CI the action touched. Both are
    # optional on the entity, and both degrade to "unspecified" rather than
    # to a default that would merge unrelated records.
    targets: list[tuple[str | None, str | None, str | None]] = [
        (
            (ci.attributes or {}).get("ci_class") if isinstance(ci.attributes, dict) else None,
            ci.environment,
            (ci.attributes or {}).get("criticality") if isinstance(ci.attributes, dict) else None,
        )
        for ci in cis
    ] or [(None, None, None)]

    recorded = 0
    for action_type in action_types:
        for resource_class, environment, criticality in targets:
            await record_outcome(
                db,
                tenant_id,
                scope=scope_key(
                    agent_ref=str(run.initiated_by) if run.initiated_by else None,
                    action_type=action_type,
                    resource_class=resource_class,
                    environment=environment,
                    business_criticality=criticality,
                ),
                assessment_result=outcome.overall_result,
                rolled_back=outcome.rollback_recommended,
            )
            recorded += 1
    return recorded


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

    # F9: evaluate each criterion separately, then aggregate. The verdict now
    # says WHAT was checked and what each check found, and — the case this
    # exists for — a CI that never produced a signal yields `inconclusive`
    # rather than the `verified` the old absence-only rule returned.
    results = await _evaluate_criteria(
        db, tenant_id, cis=cis, completed_at=completed_at
    )
    outcome = aggregate(results)
    verdict = legacy_status(outcome.overall_result)
    details: dict = {
        "assessment": outcome.overall_result,
        "summary": outcome.summary,
        "criteria": [
            {
                "type": r.criterion_type,
                "name": r.criterion_name,
                "status": r.status,
                "observed": r.observed_value,
            }
            for r in results
        ],
        "checked_cis": [ci.name for ci in cis],
        "recheck_after_sec": recheck_after,
    }

    run.verification_status = verdict
    run.verified_at = now
    run.verification_details = details
    await db.flush()

    assessment = await _persist_assessment(
        db, tenant_id, run=run, outcome=outcome, results=results, now=now
    )
    details["assessment_id"] = str(assessment.id)
    run.verification_details = dict(details)
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

    # F10: fold the outcome into the trust record for every scope this run
    # touched. Fail-soft for the same reason as the cohort write-back — a
    # trust counter must never break the verification that feeds it.
    try:
        await _record_trust_outcomes(db, tenant_id, run=run, outcome=outcome, cis=cis)
    except Exception as exc:
        logger.warning(
            "trust.recording_failed",
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
