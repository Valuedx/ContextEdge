"""Deterministic case frame for playbook retrieval.

Two representations, never mixed: ``symptom_text`` is embedded alone;
``lexical_terms`` are OR-composed. Identifiers, environment and optional
signature ids are carried through so later stages can gate on them.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass, field

from contextedge.graph.agent.repository import extract_identifier_tokens
from contextedge.search.pg_fts import _FTS_TOKEN_RE

_MAX_LEXICAL = 24
_WORD_RE = re.compile(r"[A-Za-z]{4,}")


@dataclass(frozen=True, slots=True)
class CaseFrame:
    symptom_text: str
    lexical_terms: list[str]
    identifier_tokens: list[str]
    error_signature_id: uuid.UUID | None = None
    issue_signature_id: uuid.UUID | None = None
    failing_component: str | None = None
    failure_mode: str | None = None
    ci_entity_ids: list[uuid.UUID] = field(default_factory=list)
    environment: dict = field(default_factory=dict)
    domain_id: uuid.UUID | None = None


def build_case_frame(
    *,
    symptoms: list[str] | None = None,
    entities: list[str] | None = None,
    context: str | None = None,
    environment: dict | None = None,
    domain_id: uuid.UUID | None = None,
    query_text: str | None = None,
    error_signature_id: uuid.UUID | None = None,
    issue_signature_id: uuid.UUID | None = None,
    failing_component: str | None = None,
    failure_mode: str | None = None,
    ci_entity_ids: list[uuid.UUID] | None = None,
) -> CaseFrame:
    """Build a case frame from structured ticket fields. No LLM call."""
    symptom_parts = [s.strip() for s in (symptoms or []) if s and s.strip()]
    if context and context.strip():
        symptom_parts.append(context.strip())
    symptom_text = " ".join(symptom_parts).strip()
    if not symptom_text and query_text:
        symptom_text = query_text.strip()

    blob = " ".join(
        [
            symptom_text,
            " ".join(e.strip() for e in (entities or []) if e and e.strip()),
            query_text or "",
        ]
    )
    identifiers = extract_identifier_tokens(blob)
    lexical: list[str] = []
    seen: set[str] = set()
    for token in identifiers:
        key = token.lower()
        if key not in seen:
            seen.add(key)
            lexical.append(token.lower())
    for raw in (entities or []):
        key = raw.strip().lower()
        if key and key not in seen:
            seen.add(key)
            lexical.append(key)
    for word in _WORD_RE.findall(symptom_text)[-16:]:
        key = word.lower()
        if key not in seen:
            seen.add(key)
            lexical.append(key)
        if len(lexical) >= _MAX_LEXICAL:
            break
    if len(lexical) < _MAX_LEXICAL:
        for raw in _FTS_TOKEN_RE.findall(blob):
            key = raw.lower()
            if key not in seen:
                seen.add(key)
                lexical.append(key)
            if len(lexical) >= _MAX_LEXICAL:
                break

    env = dict(environment or {})
    return CaseFrame(
        symptom_text=symptom_text[:4_000],
        lexical_terms=lexical[:_MAX_LEXICAL],
        identifier_tokens=identifiers,
        error_signature_id=error_signature_id,
        issue_signature_id=issue_signature_id,
        failing_component=failing_component or _env_str(env, "failing_component"),
        failure_mode=failure_mode or _env_str(env, "failure_mode"),
        ci_entity_ids=list(ci_entity_ids or []),
        environment=env,
        domain_id=domain_id,
    )


def _env_str(env: dict, key: str) -> str | None:
    value = env.get(key)
    if value is None:
        return None
    text = str(value).strip()
    return text or None


async def resolve_case_frame(
    db,
    tenant_id: uuid.UUID,
    frame: CaseFrame,
) -> CaseFrame:
    """Fill signature and CI ids from the diagnostic indexes (GAP-12)."""
    issue_id = frame.issue_signature_id
    error_id = frame.error_signature_id
    failing_component = frame.failing_component
    failure_mode = frame.failure_mode
    ci_ids = list(frame.ci_entity_ids)

    try:
        if issue_id is None or failing_component is None or failure_mode is None:
            issue_id, failing_component, failure_mode = await _lookup_issue_signature(
                db,
                tenant_id,
                frame,
                issue_id=issue_id,
                failing_component=failing_component,
                failure_mode=failure_mode,
            )
        if error_id is None:
            error_id = await _lookup_error_signature(db, tenant_id, frame)
            if error_id is not None and issue_id is None:
                issue_id, failing_component, failure_mode = await _lookup_issue_signature(
                    db,
                    tenant_id,
                    frame,
                    issue_id=None,
                    failing_component=failing_component,
                    failure_mode=failure_mode,
                    error_signature_id=error_id,
                )
        if not ci_ids:
            from contextedge.services.identity_service import resolve_identity_ids_for_terms

            terms = list(frame.identifier_tokens or []) + list(frame.lexical_terms or [])[:8]
            ci_ids = list(await resolve_identity_ids_for_terms(db, tenant_id, terms))
    except Exception:
        return frame

    if (
        issue_id == frame.issue_signature_id
        and error_id == frame.error_signature_id
        and failing_component == frame.failing_component
        and failure_mode == frame.failure_mode
        and ci_ids == list(frame.ci_entity_ids)
    ):
        return frame
    return CaseFrame(
        symptom_text=frame.symptom_text,
        lexical_terms=frame.lexical_terms,
        identifier_tokens=frame.identifier_tokens,
        error_signature_id=error_id,
        issue_signature_id=issue_id,
        failing_component=failing_component,
        failure_mode=failure_mode,
        ci_entity_ids=ci_ids,
        environment=frame.environment,
        domain_id=frame.domain_id,
    )


async def _lookup_issue_signature(
    db,
    tenant_id: uuid.UUID,
    frame: CaseFrame,
    *,
    issue_id: uuid.UUID | None,
    failing_component: str | None,
    failure_mode: str | None,
    error_signature_id: uuid.UUID | None = None,
) -> tuple[uuid.UUID | None, str | None, str | None]:
    from sqlalchemy import func, select

    from contextedge.models.issue_signature import IssueSignature
    from contextedge.search.pg_fts import or_composed_websearch_tsquery

    if issue_id is not None:
        row = await db.get(IssueSignature, issue_id)
        if row is None:
            return issue_id, failing_component, failure_mode
        return (
            row.id,
            failing_component or row.failing_component,
            failure_mode or row.failure_mode,
        )

    filters = [IssueSignature.tenant_id == tenant_id]
    if error_signature_id is not None:
        filters.append(IssueSignature.error_signature_id == error_signature_id)
        row = (
            await db.execute(
                select(IssueSignature).where(*filters).limit(1)
            )
        ).scalar_one_or_none()
        if row is None:
            return None, failing_component, failure_mode
        return (
            row.id,
            failing_component or row.failing_component,
            failure_mode or row.failure_mode,
        )

    query = " ".join(frame.lexical_terms) or frame.symptom_text
    tsquery = or_composed_websearch_tsquery(query)
    if tsquery is None:
        return None, failing_component, failure_mode
    sig_tsvector = func.to_tsvector(
        "english",
        func.replace(
            func.concat_ws(
                " ",
                IssueSignature.affected_capability,
                IssueSignature.failing_component,
                IssueSignature.failure_mode,
                IssueSignature.trigger_change,
            ),
            "_",
            " ",
        ),
    )
    rank = func.ts_rank(sig_tsvector, tsquery)
    row = (
        await db.execute(
            select(IssueSignature)
            .where(*filters, sig_tsvector.op("@@")(tsquery))
            .order_by(rank.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    if row is None:
        return None, failing_component, failure_mode
    return (
        row.id,
        failing_component or row.failing_component,
        failure_mode or row.failure_mode,
    )


async def _lookup_error_signature(
    db,
    tenant_id: uuid.UUID,
    frame: CaseFrame,
) -> uuid.UUID | None:
    from sqlalchemy import or_, select

    from contextedge.models.error_signature import ErrorSignature

    terms = [t.lower() for t in (frame.identifier_tokens + frame.lexical_terms)[:8] if len(t) >= 4]
    if not terms:
        return None
    clauses = []
    for term in terms[:6]:
        like = f"%{term}%"
        clauses.append(ErrorSignature.signature_key.ilike(like))
        clauses.append(ErrorSignature.normalized_message.ilike(like))
        clauses.append(ErrorSignature.display_name.ilike(like))
    row = (
        await db.execute(
            select(ErrorSignature.id)
            .where(
                ErrorSignature.tenant_id == tenant_id,
                ErrorSignature.is_active.is_(True),
                or_(*clauses),
            )
            .limit(1)
        )
    ).scalar_one_or_none()
    return row
