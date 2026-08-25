# Logic & Systems Audit

## 0. Scope Note

Reviewed backend business logic in services, workers, graph/ranking, models, and relevant API callers at clean HEAD. Frontend presentation and live connector/database behavior were out of scope. No external tool executor is present in the invocation path; Issue #1 confirms approval bypass in the acceptance/audit path, while any resulting external side effect is an assumption to verify.

Validation: 44 focused tests passed. Execution-specific tests could not collect because the project virtual environment lacks the declared `rfc8785` dependency; direct reproductions were used for Issues #1, #4, and #8.

## 1. Logic Gap Matrix

| # | Severity | Confidence | Location | Failure Type | Impact Summary |
|---|---|---|---|---|---|
| 1 | P0 | Confirmed | `execution_service.py:537-551, 1149-1201` | Invariant Violation | Approval-required steps can record tool invocations while approval is still pending. |
| 2 | P0 | Confirmed | `pattern_service.py:254-313`; `pattern_tasks.py:352-360` | Reconciliation / Deduplication | Automated maintenance deletes distinct evidence sharing only a title and type. |
| 3 | P1 | Confirmed | `execution_service.py:1625-1680` | State Machine / Idempotency | A terminal execution can be completed again with a conflicting outcome. |
| 4 | P1 | Confirmed | `idempotency_service.py:62-79`; `execution_service.py:451-468` | Deduplication Collision | All ad-hoc runs without a session share the same idempotency scope. |
| 5 | P1 | Confirmed | `sync_worker_service.py:478-523` | Temporal / Lifecycle | Records fetched before pause or cancellation are persisted but never queued for normalization. |
| 6 | P1 | Confirmed | `hybrid_ranker.py:65-77, 124-137, 147-153` | Graph Temporal Logic | Closed historical edges continue to affect current recommendation scores. |
| 7 | P1 | Confirmed | `hybrid_ranker.py:237-243, 329-385`; `execution_service.py:650-667` | Branching / Business Rules | Expired playbooks can be recommended even though execution rejects them. |
| 8 | P1 | Confirmed | `identity_reconciliation_service.py:174-194, 220-277` | Score Validation / Degrade-Not-Crash | Malformed LLM confidence aborts reconciliation; NaN confidence passes the threshold. |

## 2. Detailed Findings

### Issue #1: Pending approval is treated like “approval not required”

Location: `backend/src/contextedge/services/execution_service.py:537-551, 1149-1201`

Original code (verbatim quote):

```python
    approvals = (
        (
            await db.execute(
                select(ApprovalRequest).where(
                    ApprovalRequest.tenant_id == tenant_id,
                    ApprovalRequest.step_run_id == step.id,
                    ApprovalRequest.status == "approved",
                )
            )
        )
        .scalars()
        .all()
    )
    if not approvals:
        return None
```

```python
    input_hash = await assert_approved_artifact_unchanged(db, tenant_id, step)

    run = await db.get(ExecutionRun, step.execution_run_id)
    shadow = run is not None and is_shadow_mode(run.automation_mode)

    now = datetime.now(UTC)
    invocation = ToolInvocation(
        step_run_id=step_run_id,
        tenant_id=tenant_id,
        tool_name=tool_name,
        tool_version=tool_version,
        safety_class=safety_class,
        inputs=inputs or {},
        # Shadow outputs are tagged explicitly so analytics can separate
        # real outcomes from dry-run traces when computing success rates.
        outputs={**(outputs or {}), "shadow": True} if shadow else (outputs or {}),
        status="shadow_executed" if shadow else status,
        error_message=error_message,
        duration_ms=duration_ms,
        started_at=now,
        completed_at=now,
    )
    db.add(invocation)
    await db.flush()
```

Flawed Logic Explanation: The verifier never examines `step.requires_approval`. A pending approval is absent from the `"approved"` query, so it produces exactly the same result as a step that never required approval: `None`. The caller interprets that as success and inserts the invocation.

Concrete Failure Trace:

- Given Input:

```python
step = {
    "requires_approval": True,
    "status": "awaiting_approval",
    "safety_class": "destructive",
}
approval = {"status": "pending"}
request = {"tool_name": "restart_database", "safety_class": "destructive"}
```

- Step 1: The approval query returns `[]` because the only request is `"pending"`.
- Step 2: `assert_approved_artifact_unchanged` returns `None`.
- Step 3: The invocation safety class does not exceed the step safety class.
- Step 4: A `ToolInvocation` is inserted.
- Resulting Fault: The approval gate has no blocking effect on the invocation acceptance/audit path. Whether the external tool has already run is outside the shown implementation.

Corrected Logic (minimum safe, fail-closed diff):

```diff
     if not approvals:
+        if step.requires_approval:
+            raise ExecutionPolicyError(
+                "Step requires an approved, current approval before tool invocation"
+            )
         return None
```

A modified-approval workflow should only be admitted after the modified artifact has been rebound and verified; it must not reuse this absence-as-success path.

---

### Issue #2: Title-based deduplication deletes distinct evidence

Location: `backend/src/contextedge/services/pattern_service.py:254-313`; `backend/src/contextedge/workers/pattern_tasks.py:352-360`

Original code (verbatim quote):

```python
async def deduplicate_evidence_items(db: AsyncSession, tenant_id: uuid.UUID) -> int:
    """Merge duplicate evidence items sharing identical title and evidence_type."""
    from sqlalchemy import delete, func, text

    from contextedge.models.evidence import EvidenceItem, RawEvidenceObject

    group_stmt = text("""
        SELECT title, evidence_type, COUNT(*)
        FROM evidence_items
        WHERE tenant_id = :tenant_id AND title IS NOT NULL AND evidence_type != 'thread_message'
        GROUP BY title, evidence_type
        HAVING COUNT(*) > 1
    """)
    groups = (await db.execute(group_stmt, {"tenant_id": tenant_id})).all()

    merged_evidence_count = 0

    for title, ev_type, _cnt in groups:
        ev_stmt = (
            select(EvidenceItem)
            .where(
                EvidenceItem.tenant_id == tenant_id,
                EvidenceItem.title == title,
                EvidenceItem.evidence_type == ev_type,
            )
            .order_by(EvidenceItem.ingested_at.asc())
        )
        items = (await db.execute(ev_stmt)).scalars().all()
        if len(items) <= 1:
            continue

        canonical = items[0]
        duplicates = items[1:]
```

```python
            await db.execute(delete(EvidenceItem).where(EvidenceItem.id == dup.id))
```

The function is called automatically:

```python
        from contextedge.services.pattern_service import deduplicate_patterns_and_playbooks
        try:
            dedup_stats = await deduplicate_patterns_and_playbooks(db, tid)
        except Exception as dedup_exc:  # noqa: BLE001
            logger.warning("pattern_dedup_sweep_failed", error=str(dedup_exc))
            dedup_stats = {}
```

Flawed Logic Explanation: `(title, evidence_type)` is not an identity key. Separate tickets, alerts, or articles routinely share generic titles. The function reassigns relationships to the oldest row and then deletes every other row, without comparing content, source identity, case identity, or content hash. This runs as automated housekeeping.

Concrete Failure Trace:

- Given Input:

```python
e1 = {
    "id": "11111111-1111-1111-1111-111111111111",
    "title": "Database unavailable",
    "evidence_type": "ticket",
    "content_hash": "hash-incident-A",
    "canonical_case_id": "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
}
e2 = {
    "id": "22222222-2222-2222-2222-222222222222",
    "title": "Database unavailable",
    "evidence_type": "ticket",
    "content_hash": "hash-incident-B",
    "canonical_case_id": "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb",
}
```

- Step 1: SQL groups both rows because title and type match.
- Step 2: `e1` becomes canonical because it was ingested first.
- Step 3: Links from `e2` are moved or removed.
- Step 4: `e2` is deleted.
- Resulting Fault: Distinct incident evidence is destroyed and unrelated case history is fused.

Corrected Logic (drop-in diff):

```diff
-    group_stmt = text("""
-        SELECT title, evidence_type, COUNT(*)
+    group_stmt = text("""
+        SELECT content_hash, COUNT(*)
         FROM evidence_items
-        WHERE tenant_id = :tenant_id AND title IS NOT NULL AND evidence_type != 'thread_message'
-        GROUP BY title, evidence_type
+        WHERE tenant_id = :tenant_id AND content_hash IS NOT NULL
+        GROUP BY content_hash
         HAVING COUNT(*) > 1
     """)
...
-    for title, ev_type, _cnt in groups:
+    for content_hash, _cnt in groups:
         ev_stmt = (
             select(EvidenceItem)
             .where(
                 EvidenceItem.tenant_id == tenant_id,
-                EvidenceItem.title == title,
-                EvidenceItem.evidence_type == ev_type,
+                EvidenceItem.content_hash == content_hash,
             )
```

---

### Issue #3: Terminal execution outcomes can be overwritten

Location: `backend/src/contextedge/services/execution_service.py:1625-1680`

Original code (verbatim quote):

```python
async def complete_execution(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    execution_run_id: uuid.UUID,
    outcome: str,
    outcome_summary: str | None = None,
) -> ExecutionRun | None:
    if outcome not in OUTCOMES:
        raise ExecutionPolicyError(
            f"Unknown outcome {outcome!r}; expected one of {OUTCOMES}"
        )
    run = await db.get(ExecutionRun, execution_run_id)
    if run is None or run.tenant_id != tenant_id:
        return None

    if outcome != "aborted":
        # Completion must reflect reality: refuse success/partial/failure
        # while steps are still pending, running, or awaiting approval.
        open_steps = (
            await db.execute(
                select(func.count())
                .select_from(ExecutionStepRun)
                .where(
                    ExecutionStepRun.execution_run_id == run.id,
                    ExecutionStepRun.tenant_id == tenant_id,
                    ExecutionStepRun.status.in_(
                        ("pending", "running", "awaiting_approval")
                    ),
                )
            )
        ).scalar_one()
        if open_steps:
            raise ExecutionPolicyError(
                f"{open_steps} step(s) are still open; complete, skip, or "
                "abort them before completing the run"
            )

    now = datetime.now(UTC)
    run.status = "completed" if outcome != "aborted" else "aborted"
    run.completed_at = now
    run.outcome = outcome
    run.outcome_summary = outcome_summary
    await db.flush()
```

Flawed Logic Explanation: No terminal-state guard exists. A completed, failed, or aborted run is treated as mutable. Repeated or out-of-order calls can replace the original outcome, timestamp, summary, and subsequent audit/decision outcome records.

Concrete Failure Trace:

- Given Input:

```python
run = {
    "status": "aborted",
    "outcome": "aborted",
    "outcome_summary": "Approval denied",
}
steps = [{"status": "failed"}]
request = {"outcome": "success", "outcome_summary": "Completed"}
```

- Step 1: The run exists and belongs to the tenant.
- Step 2: The open-step count is zero because `"failed"` is not open.
- Step 3: `run.status` becomes `"completed"`.
- Step 4: `run.outcome` becomes `"success"`.
- Resulting Fault: A denied/aborted execution is rewritten as successful and produces conflicting audit history.

Corrected Logic (drop-in diff):

```diff
     run = await db.get(ExecutionRun, execution_run_id)
     if run is None or run.tenant_id != tenant_id:
         return None

+    target_status = "aborted" if outcome == "aborted" else "completed"
+    if run.status in {"completed", "failed", "aborted"}:
+        if run.status == target_status and run.outcome == outcome:
+            return run
+        raise ExecutionPolicyError(
+            f"Execution is already terminal with status={run.status!r}, "
+            f"outcome={run.outcome!r}"
+        )
+
     if outcome != "aborted":
```

---

### Issue #4: Ad-hoc executions collide across unrelated runs

Location: `backend/src/contextedge/services/idempotency_service.py:62-79`; `backend/src/contextedge/services/execution_service.py:451-468`

Original code (verbatim quote):

```python
def derive_idempotency_key(
    *,
    tenant_id: uuid.UUID,
    scope_id: uuid.UUID | None,
    artifact_hash: str,
) -> str:
    """A stable key for "this action, in this case".

    ``scope_id`` is the case (resolution session). A run with no case is scoped
    to itself, so it cannot collide with anything — an ad-hoc execution outside
    a case has no prior occurrence to be a duplicate of.

    Hashed rather than concatenated because the index is global and the parts
    include tenant ids: a readable key would leak tenant identity into a column
    other tenants' rows share an index with.
    """
    material = f"{tenant_id}:{scope_id or 'no-case'}:{artifact_hash}"
    return "idem_" + hashlib.sha256(material.encode("utf-8")).hexdigest()
```

```python
        key = derive_idempotency_key(
            tenant_id=tenant_id,
            scope_id=run.session_id,
            artifact_hash=hash_step_artifact(
                playbook_id=playbook.id,
                playbook_version_id=version.id,
                semantic_version=version.semantic_version,
                step_index=step_run.step_index,
                step=step_run.inputs,
            ),
        )
        prior = await find_duplicate(db, tenant_id, key)
        if prior is not None:
            # The key stays NULL on the duplicate: the partial unique index is
            # global, and writing it would raise IntegrityError instead of
            # letting the run record what it noticed.
            step_run.duplicate_check_status = DUPLICATE_CHECK_DUPLICATE
            step_run.status = "skipped"
```

Flawed Logic Explanation: The documentation says a run without a case is scoped to its run, but the implementation substitutes the constant `"no-case"`. Every ad-hoc run in a tenant with the same artifact hash therefore receives the same key.

Concrete Failure Trace:

- Given Input:

```python
run_a = {"id": "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa", "session_id": None}
run_b = {"id": "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb", "session_id": None}
tenant_id = "cccccccc-cccc-cccc-cccc-cccccccccccc"
artifact_hash = "sha256:x"
```

- Step 1: Both calls pass `scope_id=None`.
- Step 2: Both key materials become `tenant:no-case:sha256:x`.
- Step 3: `find_duplicate` finds the step from `run_a`.
- Step 4: The step in unrelated `run_b` becomes `"skipped"`.
- Resulting Fault: A legitimate action in an unrelated ad-hoc run is silently suppressed.

Direct reproduction confirmed identical keys.

Corrected Logic (drop-in diff):

```diff
         key = derive_idempotency_key(
             tenant_id=tenant_id,
-            scope_id=run.session_id,
+            scope_id=run.session_id or run.id,
             artifact_hash=hash_step_artifact(
```

---

### Issue #5: Paused or cancelled fetches strand newly persisted evidence

Location: `backend/src/contextedge/services/sync_worker_service.py:478-523`

Original code (verbatim quote):

```python
    try:
        result = await connector.backfill(so.external_id, so.object_type, window, ck)
        events = list(result.events or [])
        raw_created, raw_deduped, new_raw_ids = await persist_ingestion_events(
            db,
            tenant_id=tenant_id,
            source_id=source.id,
            source_object_id=so.id,
            events=events,
        )
        run.items_processed = len(events) if events else result.items_processed
        # A stop is not a failure and not a completion: the records fetched
        # before it are persisted above, and the status says which of the two
        # things the operator asked for happened.
        stopped_by = await _read_control(run.id)
        run.status = finalize_status(stopped_by)
```

```python
        if result.new_checkpoint:
            db.add(
                SyncCheckpoint(
                    source_object_id=so.id,
                    checkpoint_data=result.new_checkpoint.data,
                )
            )
```

```python
    await db.flush()
    if run.status == "completed":
        await _commit_and_queue_normalization(
            db,
            run=run,
            tenant_id=tenant_id,
            source_object_id=so.id,
            new_raw_ids=new_raw_ids,
        )
```

Flawed Logic Explanation: Paused and cancelled runs persist their fetched raw objects and advance the checkpoint, but normalization is only queued for `"completed"` runs. The raw IDs are not registered as pending handoff IDs on this path.

Concrete Failure Trace:

- Given Input:

```python
result = {
    "events": [{"external_id": "INC100"}, {"external_id": "INC101"}],
    "new_checkpoint": {"offset": 200},
}
control = "pause"
```

- Step 1: Two `RawEvidenceObject` rows are inserted.
- Step 2: `run.status` becomes `"paused"`.
- Step 3: Checkpoint offset `200` is stored.
- Step 4: The `"completed"` condition is false, so neither raw ID is queued.
- Step 5: Resume starts from offset `200`; the source does not return those records again.
- Resulting Fault: Fetched evidence remains permanently outside normalization, search, correlation, and episode processing.

Corrected Logic (drop-in diff):

```diff
     await db.flush()
-    if run.status == "completed":
+    if run.status in {"completed", "paused", "cancelled"}:
         await _commit_and_queue_normalization(
```

---

### Issue #6: Closed graph edges affect current ranking

Location: `backend/src/contextedge/search/hybrid_ranker.py:65-77, 124-137, 147-153`; `backend/src/contextedge/models/pattern.py:187-198`

Original code (verbatim quote):

```python
    graph_q = select(func.count()).where(
        GraphEdge.tenant_id == tenant_id,
        or_(
            (GraphEdge.source_node_type == "playbook") & (GraphEdge.source_node_id == playbook_id),
            (GraphEdge.target_node_type == "playbook") & (GraphEdge.target_node_id == playbook_id),
        ),
    )
```

```python
    q = select(func.count(func.distinct(GraphEdge.target_node_id))).where(
        GraphEdge.tenant_id == tenant_id,
        GraphEdge.source_node_type == "playbook",
        GraphEdge.source_node_id == playbook_id,
        GraphEdge.target_node_type == "identity",
        GraphEdge.edge_type == "references_identity",
        GraphEdge.target_node_id.in_(tuple(identity_ids)),
    )
```

```python
    contradiction_q = select(func.count()).where(
        GraphEdge.tenant_id == tenant_id,
        GraphEdge.source_node_type == "playbook",
        GraphEdge.source_node_id == playbook_id,
        GraphEdge.edge_type == "contradicts",
    )
```

The graph explicitly distinguishes active edges:

```python
        Index(
            "uq_graph_edges_active_logical",
            "tenant_id",
            "domain_id",
            "source_node_type",
            "source_node_id",
            "target_node_type",
            "target_node_id",
            "edge_type",
            unique=True,
            postgresql_where=text("valid_to IS NULL"),
            postgresql_nulls_not_distinct=True,
        ),
```

Flawed Logic Explanation: All three ranking queries omit `GraphEdge.valid_to.is_(None)`. Consequently, corrected, superseded, or otherwise closed relationships continue contributing to the current graph boost, identity match, and contradiction penalty.

Concrete Failure Trace:

- Given Input:

```python
edge = {
    "edge_type": "contradicts",
    "source_node_type": "playbook",
    "source_node_id": "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
    "valid_to": "2026-08-16T12:00:00Z",
}
base_score = 0.36
```

- Step 1: The closed edge satisfies the contradiction query.
- Step 2: `contradiction_count` is at least `1`.
- Step 3: The negative score adds `0.3`; its weighted penalty is `0.015`.
- Step 4: The score falls from `0.36` to `0.345`, below the default `0.35` gate.
- Resulting Fault: A rescinded contradiction still suppresses a current recommendation. Closed positive edges can cause the inverse false promotion.

Corrected Logic (drop-in diff):

```diff
     graph_q = select(func.count()).where(
         GraphEdge.tenant_id == tenant_id,
+        GraphEdge.valid_to.is_(None),
         or_(
...
     q = select(func.count(func.distinct(GraphEdge.target_node_id))).where(
         GraphEdge.tenant_id == tenant_id,
+        GraphEdge.valid_to.is_(None),
...
     contradiction_q = select(func.count()).where(
         GraphEdge.tenant_id == tenant_id,
+        GraphEdge.valid_to.is_(None),
```

---

### Issue #7: Expired playbooks remain recommendable

Location: `backend/src/contextedge/search/hybrid_ranker.py:237-243, 329-385`; `backend/src/contextedge/services/execution_service.py:650-667`

Original code (verbatim quote):

```python
    approved_result = await db.execute(
        select(Playbook).where(
            Playbook.tenant_id == tenant_id,
            Playbook.lifecycle_state == "approved",
        )
    )
    approved_playbooks = list(approved_result.scalars().all())
```

```python
        freshness = _compute_freshness(pb, now)
        recency_score = freshness

        total = (
            weights.keyword * keyword_score
            + weights.semantic * semantic_score
            + weights.graph_distance * graph_score
            + weights.evidence_quality * quality_score
            + weights.identity * identity_score
            + weights.recency * recency_score
            + weights.freshness * freshness
            - weights.negative_penalty * neg_score
        )
```

```python
def _compute_freshness(playbook: Playbook, now: datetime) -> float:
    """Compute freshness score based on last validation and expiry."""
    if playbook.expiry_at and playbook.expiry_at < now:
        return 0.0
```

Execution applies a different rule:

```python
    if playbook.expiry_at is not None and playbook.expiry_at < datetime.now(UTC):
        raise ExecutionPolicyError(
            f"Playbook expired at {playbook.expiry_at.isoformat()} — re-validate before executing"
        )
```

Flawed Logic Explanation: Ranking demotes an expired playbook only by setting two freshness-related components to zero. Other components can still exceed the recommendation threshold. Execution then rejects the recommended playbook.

Concrete Failure Trace:

- Given Input:

```python
playbook = {
    "lifecycle_state": "approved",
    "expiry_at": "2026-08-16T00:00:00Z",
}
scores = {
    "keyword": 1.0,
    "semantic": 1.0,
    "graph": 1.0,
    "quality": 1.0,
    "identity": 0.0,
    "negative_penalty": 0.0,
}
```

- Step 1: The playbook passes the `"approved"` query.
- Step 2: Freshness and recency become `0.0`.
- Step 3: Remaining weighted signals total `0.80`.
- Step 4: `0.80 >= 0.35`, so it is returned.
- Step 5: `start_execution` rejects it as expired.
- Resulting Fault: The system recommends an action it deterministically refuses to execute.

Corrected Logic (drop-in diff):

```diff
-    approved_result = await db.execute(
+    now = datetime.now(UTC)
+    approved_result = await db.execute(
         select(Playbook).where(
             Playbook.tenant_id == tenant_id,
             Playbook.lifecycle_state == "approved",
+            or_(
+                Playbook.expiry_at.is_(None),
+                Playbook.expiry_at >= now,
+            ),
         )
     )
...
-    now = datetime.now(UTC)
```

---

### Issue #8: LLM confidence is converted without validation

Location: `backend/src/contextedge/services/identity_reconciliation_service.py:174-194, 220-277`

Original code (verbatim quote):

```python
    try:
        result = await llm_complete_json(
            prompt.format_user(entity_type=entity_type, records=listing),
            task="extraction",
            system_prompt=prompt.system,
            tenant_id=tenant_id,
            db=db,
            prompt_name=prompt.name,
            prompt_version=prompt.version,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "identity_reconciliation.call_failed",
            entity_type=entity_type,
            error_type=type(exc).__name__,
        )
        return []
    if not isinstance(result, dict):
        return []
    groups = result.get("groups")
    return groups if isinstance(groups, list) else []
```

```python
    proposals: list[ProposedMerge] = []
    for group in groups:
        if not isinstance(group, dict):
            continue
        confidence = float(group.get("confidence") or 0.0)
        if confidence < MIN_CONFIDENCE:
            continue
```

Flawed Logic Explanation: Only the outer result and `groups` container are validated. `float("high")` raises outside the provider exception handler, aborting the entire reconciliation run. `float("nan")` succeeds, and `nan < MIN_CONFIDENCE` is false, so NaN passes the confidence gate.

Concrete Failure Trace:

- Given Input:

```python
result = {
    "groups": [{
        "confidence": "high",
        "keep_id": 0,
        "merge_ids": [1],
    }]
}
```

- Step 1: `_ask` accepts the dictionary and list.
- Step 2: `_parse_groups` executes `float("high")`.
- Resulting Fault: `ValueError: could not convert string to float: 'high'`; the reconciliation pass aborts instead of discarding one malformed group.

A direct `"nan"` fixture produced one accepted proposal with `confidence_is_nan=True`.

Corrected Logic (drop-in diff):

```diff
 import uuid
+import math
...
-        confidence = float(group.get("confidence") or 0.0)
-        if confidence < MIN_CONFIDENCE:
+        try:
+            confidence = float(group.get("confidence") or 0.0)
+        except (TypeError, ValueError):
+            continue
+        if (
+            not math.isfinite(confidence)
+            or not 0.0 <= confidence <= 1.0
+            or confidence < MIN_CONFIDENCE
+        ):
             continue
```

## 3. Edge Case Test Matrix

| Fixture | Concrete Input | Expected Corrected Result |
|---|---|---|
| Pending approval | `step={"requires_approval":true,"status":"awaiting_approval"}; approvals=[{"status":"pending"}]` | Invocation rejected; no `ToolInvocation` or attempt inserted. |
| No approval required | `step={"requires_approval":false,"status":"pending"}; approvals=[]` | Invocation may proceed subject to the remaining gates. |
| Same title, distinct evidence | `e1={"title":"Database unavailable","type":"ticket","hash":"A"}; e2={"title":"Database unavailable","type":"ticket","hash":"B"}` | Zero evidence merges. |
| True duplicate evidence | `e1={"content_hash":"A"}; e2={"content_hash":"A"}` | One deterministic merge, with all dependent relationships reconciled. |
| Duplicate completion delivery | `run={"status":"completed","outcome":"success"}; request={"outcome":"success"}` | Idempotent no-op; no new event or timestamp mutation. |
| Conflicting completion | `run={"status":"aborted","outcome":"aborted"}; request={"outcome":"success"}` | Conflict rejected; original terminal state preserved. |
| Two no-case runs | `run_a={"id":"A","session_id":null}; run_b={"id":"B","session_id":null}; artifact_hash="H"` | Keys differ because run IDs scope the actions. |
| Controlled stop with fetched data | `control="pause"; new_raw_ids=["R1","R2"]; checkpoint={"offset":200}` | `R1` and `R2` are queued or durably recorded for retry before returning. |
| Closed temporal edge | `edge={"valid_to":"2026-08-16T12:00:00Z","edge_type":"contradicts"}` | Excluded from current graph, identity, and negative scores. |
| Expiry boundaries | `expiry_at ∈ {null, now-1µs, now, now+1µs}` with frozen `now` | Included for `null`, `now`, and future; excluded for past. |
| Confidence boundaries | `confidence ∈ [-0.1,0,0.949999,0.95,1.0,1.1]` | Only `0.95` and `1.0` pass. |
| Malformed confidence | `confidence ∈ [null,"","high","nan","inf",{}]` | Group discarded; reconciliation continues. |

## 4. Self-Check

Removed five candidate findings during the final trace review: approval-expiry zombie state, repeated session-close events, concurrent attempt numbering, unknown risk-tier fallback, and historical contradiction-edge lookup. Their impact or reachable trigger was not sufficiently established from the quoted code alone.

Final report contains eight findings, each tied to an exact quoted line range. No production code was changed by this audit.

---

## 5. Independent Validation

### 5.0 Validation Scope Note

Validated against commit `233b643ba8be014e64e13fc70b150fe88470f5bd`. No production files were changed.

All 21 quoted code blocks match source text and indentation at the cited ranges. Raw bytes differ only because this Markdown report uses LF while the repository Python files use CRLF.

The cited focused suite passed: `44 passed`. `backend/pyproject.toml` declares `rfc8785>=0.1.4`, but the project virtual environment cannot import it; `tests/test_f7_approval_binding.py` therefore fails collection with `ModuleNotFoundError: No module named 'rfc8785'`. Isolated in-memory re-runs exercised the actual function bodies with controlled database/connector responses.

### 5.1 Validation Matrix

| # | Original Verdict | Validator Verdict | Confidence | Notes |
|---|---|---|---|---|
| 1 | P0 Confirmed | PARTIALLY CONFIRMED | High | Bypass reproduces; P0 is unsupported without an executor in scope. |
| 2 | P0 Confirmed | PARTIALLY CONFIRMED | High | Destructive merge reproduces; proposed fix broadens merging across types/messages. |
| 3 | P1 Confirmed | PARTIALLY CONFIRMED | High | Serial overwrite reproduces; proposed fix is not concurrency-safe. |
| 4 | P1 Confirmed | CONFIRMED | High | No-session keys collide; proposed caller fix closes this path. |
| 5 | P1 Confirmed | PARTIALLY CONFIRMED | High | Paused backfill skips handoff; proposed fix can overwrite a requested stop as `failed`. |
| 6 | P1 Confirmed | CONFIRMED | High | Closed edges are counted; the proposed predicates exclude them. |
| 7 | P1 Confirmed | CONFIRMED | High | Expired playbooks rank and execution rejects them; the filter closes the trace. |
| 8 | P1 Confirmed | PARTIALLY CONFIRMED | High | Malformed/NaN values reproduce; the proposed fix accepts booleans. |

### 5.2 Per-Issue Validation Detail

#### Issue #1 Validation: Pending approval is treated like "approval not required"

Quote check: LF/CRLF byte drift only; otherwise matched at `execution_service.py:537-551` and `1149-1201`.

Trace re-run: with `requires_approval=True`, zero approved rows, and a pending approval, `record_tool_invocation` returned a `ToolInvocation`. The API endpoint has no run/step-status gate before calling it.

Fix review: the proposed guard closes the reproduced production path. It would break the existing no-approval test double because that fixture omits `requires_approval`; set that fixture to `False`. P0 is not earned from source alone because no external tool executor is present.

Cross-file impact: `record_tool_invocation` is called by `api/v1/execution.py`; the assertion helper is otherwise directly exercised by F7 tests.

Verdict: **PARTIALLY CONFIRMED** — high confidence.

#### Issue #2 Validation: Title-based deduplication deletes distinct evidence

Quote check: LF/CRLF byte drift only; otherwise matched at `pattern_service.py:254-313` and `pattern_tasks.py:352-360`.

Trace re-run: two distinct objects with the same title/type caused `deduplicate_evidence_items` to return `1` and issue `DELETE FROM evidence_items`. The worker invokes this automatically and commits successful work.

Fix review: incomplete. Grouping only by `content_hash` removes the existing `thread_message` exclusion and can merge rows of different evidence types that share a hash.

```diff
- SELECT content_hash, COUNT(*)
+ SELECT evidence_type, content_hash, COUNT(*)
  FROM evidence_items
- WHERE tenant_id = :tenant_id AND content_hash IS NOT NULL
- GROUP BY content_hash
+ WHERE tenant_id = :tenant_id
+   AND content_hash IS NOT NULL
+   AND evidence_type != 'thread_message'
+ GROUP BY evidence_type, content_hash

- for content_hash, _cnt in groups:
+ for ev_type, content_hash, _cnt in groups:
      ...
+     EvidenceItem.evidence_type == ev_type,
      EvidenceItem.content_hash == content_hash,
```

Cross-file impact: called by `deduplicate_patterns_and_playbooks`, exposed by `api/v1/patterns.py`, and run from `pattern_tasks.py`.

Verdict: **PARTIALLY CONFIRMED** — high confidence.

#### Issue #3 Validation: Terminal execution outcomes can be overwritten

Quote check: LF/CRLF byte drift only; otherwise matched at `execution_service.py:1625-1680`.

Trace re-run: an `aborted` run with no open steps became `completed`, `success`, and `Completed` after calling `complete_execution`.

Fix review: the proposed guard handles sequential retries but not concurrent callers: both can read a nonterminal row via `db.get` before either flushes. Lock the row before evaluating terminal state.

```diff
- run = await db.get(ExecutionRun, execution_run_id)
- if run is None or run.tenant_id != tenant_id:
+ run = (
+     await db.execute(
+         select(ExecutionRun)
+         .where(
+             ExecutionRun.id == execution_run_id,
+             ExecutionRun.tenant_id == tenant_id,
+         )
+         .with_for_update()
+     )
+ ).scalar_one_or_none()
+ if run is None:
      return None

+ target_status = "aborted" if outcome == "aborted" else "completed"
+ if run.status in {"completed", "failed", "aborted"}:
+     if run.status == target_status and run.outcome == outcome:
+         return run
+     raise ExecutionPolicyError(...)
```

Cross-file impact: called by the completion API and `abort_execution`. Approval denial also writes terminal run state, but an independently reachable completed-run overwrite through that path was not established.

Verdict: **PARTIALLY CONFIRMED** — high confidence.

#### Issue #4 Validation: Ad-hoc executions collide across unrelated runs

Quote check: LF/CRLF byte drift only; otherwise matched at `idempotency_service.py:62-79` and `execution_service.py:451-468`.

Trace re-run: two calls with the same tenant, hash, and `scope_id=None` produced identical keys.

Fix review: `scope_id=run.session_id or run.id` compiles, preserves session-scoped dedupe, and separates ad-hoc runs.

Cross-file impact: the production key derivation call is only in `_assign_idempotency_keys`; other matches are tests.

Verdict: **CONFIRMED** — high confidence.

#### Issue #5 Validation: Paused or cancelled fetches strand newly persisted evidence

Quote check: LF/CRLF byte drift only; otherwise matched at `sync_worker_service.py:478-523`.

Trace re-run: a paused backfill with two persisted raw IDs returned `paused` and made zero calls to `_commit_and_queue_normalization`.

Fix review: adding paused/cancelled closes the successful-handoff trace, but queue failure changes the run to `failed` and rethrows. That overwrites the requested stop and can cause task retry.

```diff
 async def _commit_and_queue_normalization(
     ...
     new_raw_ids: list[uuid.UUID],
+    preserve_status_on_enqueue_failure: str | None = None,
 ) -> None:
     ...
-    run.status = "failed"
+    run.status = preserve_status_on_enqueue_failure or "failed"
     ...
-    raise
+    if preserve_status_on_enqueue_failure is None:
+        raise
+    return
```

Apply the status/return adjustment to both enqueue exception handlers, then pass the current status only for `paused` and `cancelled` calls. Keep the incremental caller on the default `None` behavior.

Cross-file impact: the helper is called by backfill and incremental jobs; only backfill observes control signals.

Verdict: **PARTIALLY CONFIRMED** — high confidence.

#### Issue #6 Validation: Closed graph edges affect current ranking

Quote check: LF/CRLF byte drift only; otherwise matched at `hybrid_ranker.py:65-77`, `124-137`, and `147-153`.

Trace re-run: `_negative_penalty_for_playbook` returned `0.3` for one contradiction, and its generated query had no `valid_to` predicate. The graph temporal helper and active-edge indexes define current edges as `valid_to IS NULL`.

Fix review: adding `GraphEdge.valid_to.is_(None)` to all three cited GraphEdge queries compiles and closes the trace.

Cross-file impact: the private scoring helpers are called by `rank_playbooks`, which is used by runtime API/service and evaluation code.

Verdict: **CONFIRMED** — high confidence.

#### Issue #7 Validation: Expired playbooks remain recommendable

Quote check: LF/CRLF byte drift only; otherwise matched at `hybrid_ranker.py:237-243`, `329-385`, and `execution_service.py:650-667`.

Trace re-run: an approved playbook expired yesterday ranked at `0.8`; `start_execution` rejects that same playbook.

Fix review: the proposed database filter compiles, uses the already imported `or_`, preserves non-expiring and boundary candidates, and blocks the reproduced already-expired candidate. Execution's existing expiry check remains necessary for items that expire after ranking.

Cross-file impact: all ranking callers share the behavior; execution is reached through the execution API.

Verdict: **CONFIRMED** — high confidence.

#### Issue #8 Validation: LLM confidence is converted without validation

Quote check: LF/CRLF byte drift only; otherwise matched at `identity_reconciliation_service.py:174-194` and `220-277`.

Trace re-run: `"high"` raised `ValueError`; `"nan"` produced one proposal with NaN confidence. `reconcile_identities` does not catch parser failures.

Fix review: the proposed patch handles malformed strings, NaN, infinity, and out-of-range values, but it accepts `True` because `float(True) == 1.0`.

```diff
- confidence = float(group.get("confidence") or 0.0)
+ raw_confidence = group.get("confidence")
+ if isinstance(raw_confidence, bool):
+     continue
+ try:
+     confidence = float(raw_confidence or 0.0)
+ except (TypeError, ValueError):
+     continue
```

Keep the proposed finite/range/minimum gate after this conversion.

Cross-file impact: `_parse_groups` has one production caller, `reconcile_identities`, plus direct tests.

Verdict: **PARTIALLY CONFIRMED** — high confidence.

### 5.3 New Finding Surfaced During Validation

#### P1 Confirmed: Body-only evidence hashes conflate unrelated evidence

Location: `backend/src/contextedge/services/evidence_normalization.py:138-152`; `backend/src/contextedge/workers/extraction_tasks.py:213-221`.

Original code (verbatim):

```python
    body = raw_body_from_payload(payload)
    return hashlib.sha256(body.encode("utf-8", errors="replace")).hexdigest()
```

```python
            select(EvidenceItem).where(
                EvidenceItem.tenant_id == tenant_id,
                EvidenceItem.content_hash == h,
            )
```

Trace: a ticket payload and a knowledge-article payload with different titles/types but identical body text generated the same hash in a direct re-run. The second normalization selects the existing tenant-wide row and returns it as deduped rather than creating distinct evidence.

Migration `0026_dedup_uniqueness.py` enforces the same `(tenant_id, content_hash)` identity. Correcting Issue #2 only in the maintenance sweep therefore cannot make `content_hash` a safe entity identity. Replace the global body-hash uniqueness rule with a source/upstream-object identity key and update normalization lookup plus race handling in the same migration; retain body hash only as a similarity signal.

### 5.4 Overall Assessment

The eight underlying execution paths are real. The proposed fixes are not safe to apply as-is: Issues #2, #3, #5, and #8 need the validator changes above, and Issue #1's P0 severity is unsupported by the code in scope. The body-only evidence identity finding also requires a coordinated normalization and database-constraint migration before using content hash as a merge identity.
