"""Playbook retrieval port for MAF tools — Protocol + in-process + HTTP."""

from __future__ import annotations

from typing import Any, Protocol
from uuid import UUID

import httpx
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from contextedge.models.execution import ACTION_TYPES, SAFETY_CLASSES
from contextedge.models.pattern import NegativeKnowledgeItem
from contextedge.models.playbook import Playbook, PlaybookNegativeKnowledge, PlaybookVersion
from contextedge.schemas.playbook import PlaybookStep
from contextedge.search.hybrid_ranker import rank_playbooks
from contextedge.services.case_frame_service import build_case_frame
from contextedge.services.playbook_applicability import evaluate_trigger_conditions


def normalize_playbook_steps(raw: Any) -> list[dict[str, Any]]:
    """Coerce stored step JSON into the governed PlaybookStep shape (G4.3).

    Authors store a mix of ``instruction`` / ``text`` / ``action`` blobs.
    The diagnose tool must always expose ``safety_class``, ``requires_approval``,
    ``reversible``, ``rollback_hint``, ``verification``, ``tool_ref``, and
    ``inputs`` so the agent cannot treat truncated graph labels as the plan.
    """
    steps = raw if isinstance(raw, list) else []
    out: list[dict[str, Any]] = []
    for index, step in enumerate(steps):
        payload = dict(step) if isinstance(step, dict) else {"title": str(step)}
        payload.setdefault("index", index + 1)
        if not payload.get("title"):
            payload["title"] = (
                payload.get("instruction")
                or payload.get("text")
                or payload.get("action")
                or payload.get("description")
            )
        if payload.get("safety_class") not in SAFETY_CLASSES:
            payload["safety_class"] = None
        if payload.get("action_type") not in ACTION_TYPES:
            payload["action_type"] = None
        if "rollback_hint" not in payload:
            payload["rollback_hint"] = payload.get("rollback")
        if "tool_ref" not in payload:
            payload["tool_ref"] = payload.get("tool") or payload.get("connector")
        payload.setdefault("requires_approval", bool(payload.get("requires_approval")))
        payload.setdefault("reversible", bool(payload.get("reversible")))
        payload.setdefault("verification", bool(payload.get("verification")))
        payload.setdefault("inputs", payload.get("inputs") or {})
        try:
            parsed = PlaybookStep.model_validate(payload)
        except ValidationError:
            parsed = PlaybookStep(
                index=index + 1,
                title=str(payload.get("title") or f"Step {index + 1}"),
                inputs=payload.get("inputs") or {},
            )
        out.append(parsed.model_dump())
    return out


class PlaybookRetrievalClient(Protocol):
    async def match_playbooks(
        self,
        symptoms: list[str],
        entities: list[str],
        environment: dict,
        top_k: int,
    ) -> list[dict[str, Any]]: ...

    async def get_playbook(
        self, playbook_id: UUID, version_id: UUID
    ) -> dict[str, Any]: ...

    async def check_trigger_conditions(
        self,
        playbook_version_id: UUID,
        environment: dict,
        symptoms: list[str],
    ) -> dict[str, Any]: ...

    async def get_negative_knowledge(
        self, playbook_version_id: UUID
    ) -> dict[str, Any]: ...


def _ranked_payload(ranked) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for r in ranked:
        out.append(
            {
                "playbook_id": str(r.playbook.id),
                "playbook_title": r.playbook.title,
                "stable_key": r.playbook.stable_key,
                "playbook_version_id": str(r.playbook_version_id)
                if r.playbook_version_id
                else None,
                "semantic_version": r.semantic_version,
                "match_score": round(r.score, 4),
                "confidence": round(r.confidence, 4),
                "confidence_calibrated": (
                    round(r.confidence_calibrated, 4)
                    if r.confidence_calibrated is not None
                    else None
                ),
                "applicability": r.applicability,
                "applicability_factors": r.applicability_factors,
                "applicability_differences": r.applicability_differences,
                "selection_margin": r.selection_margin,
                "scoring_breakdown": r.breakdown,
                "risk_tier": r.playbook.risk_tier,
                "freshness_status": r.freshness_status,
            }
        )
    return out


class InProcessPlaybookRetrievalClient:
    """Uses the request-scoped session so RLS and tenancy already apply."""

    def __init__(
        self,
        db: AsyncSession,
        tenant_id: UUID,
        *,
        domain_id: UUID | None = None,
        max_risk_tier: str | None = None,
        allowed_domain_ids: list[UUID] | None = None,
        caller_roles: list[str] | None = None,
    ):
        self.db = db
        self.tenant_id = tenant_id
        self.domain_id = domain_id
        self.max_risk_tier = max_risk_tier
        self.allowed_domain_ids = allowed_domain_ids
        self.caller_roles = caller_roles

    async def match_playbooks(
        self,
        symptoms: list[str],
        entities: list[str],
        environment: dict,
        top_k: int,
    ) -> list[dict[str, Any]]:
        frame = build_case_frame(
            symptoms=symptoms,
            entities=entities,
            environment=environment,
            domain_id=self.domain_id,
        )
        ranked = await rank_playbooks(
            self.db,
            tenant_id=self.tenant_id,
            query_text=frame.symptom_text,
            entities=entities,
            top_k=top_k,
            domain_id=self.domain_id,
            max_risk_tier=self.max_risk_tier,
            allowed_domain_ids=self.allowed_domain_ids,
            caller_roles=self.caller_roles,
            case_frame=frame,
            environment=environment,
        )
        return _ranked_payload(ranked)

    async def get_playbook(
        self, playbook_id: UUID, version_id: UUID
    ) -> dict[str, Any]:
        from sqlalchemy import select

        playbook = (
            await self.db.execute(
                select(Playbook).where(
                    Playbook.id == playbook_id,
                    Playbook.tenant_id == self.tenant_id,
                    Playbook.lifecycle_state == "approved",
                )
            )
        ).scalar_one_or_none()
        if playbook is None:
            return {
                "error": {
                    "code": "playbook_not_found",
                    "message": "Approved playbook not found for this tenant.",
                }
            }
        version = (
            await self.db.execute(
                select(PlaybookVersion).where(
                    PlaybookVersion.id == version_id,
                    PlaybookVersion.playbook_id == playbook_id,
                    PlaybookVersion.published_at.is_not(None),
                )
            )
        ).scalar_one_or_none()
        if version is None:
            return {
                "error": {
                    "code": "version_not_found",
                    "message": "Published playbook version not found; pass the version_id from match_playbooks.",
                }
            }
        return {
            "playbook_id": str(playbook.id),
            "playbook_version_id": str(version.id),
            "stable_key": playbook.stable_key,
            "title": playbook.title,
            "semantic_version": version.semantic_version,
            "trigger_conditions": version.trigger_conditions,
            "steps": normalize_playbook_steps(version.steps),
            "rollback_notes": version.rollback_notes,
            "verification_policy": version.verification_policy,
            "conflicts": version.conflicts,
            "inputs": version.inputs,
            "outputs": version.outputs,
            "risk_tier": playbook.risk_tier,
            "automation_mode": playbook.automation_mode,
        }

    async def check_trigger_conditions(
        self,
        playbook_version_id: UUID,
        environment: dict,
        symptoms: list[str],
    ) -> dict[str, Any]:
        from sqlalchemy import select

        version = (
            await self.db.execute(
                select(PlaybookVersion).where(
                    PlaybookVersion.id == playbook_version_id,
                    PlaybookVersion.tenant_id == self.tenant_id,
                    PlaybookVersion.published_at.is_not(None),
                )
            )
        ).scalar_one_or_none()
        if version is None:
            return {
                "error": {
                    "code": "version_not_found",
                    "message": "Published playbook version not found.",
                }
            }
        playbook = await self.db.get(Playbook, version.playbook_id)
        frame = build_case_frame(symptoms=symptoms, environment=environment)
        verdict = evaluate_trigger_conditions(version, frame, playbook=playbook)
        return {
            "level": verdict.level,
            "matched_factors": verdict.matched_factors,
            "differences": verdict.differences,
            "review_required": verdict.review_required,
            "drop": verdict.drop,
            "drop_reason": verdict.drop_reason,
        }

    async def get_negative_knowledge(
        self, playbook_version_id: UUID
    ) -> dict[str, Any]:
        from sqlalchemy import select

        version = (
            await self.db.execute(
                select(PlaybookVersion).where(
                    PlaybookVersion.id == playbook_version_id,
                    PlaybookVersion.tenant_id == self.tenant_id,
                )
            )
        ).scalar_one_or_none()
        if version is None:
            return {
                "error": {
                    "code": "version_not_found",
                    "message": "Playbook version not found.",
                }
            }
        rows = (
            await self.db.execute(
                select(NegativeKnowledgeItem)
                .join(
                    PlaybookNegativeKnowledge,
                    PlaybookNegativeKnowledge.negative_knowledge_id
                    == NegativeKnowledgeItem.id,
                )
                .where(
                    PlaybookNegativeKnowledge.tenant_id == self.tenant_id,
                    PlaybookNegativeKnowledge.playbook_id == version.playbook_id,
                    (
                        PlaybookNegativeKnowledge.playbook_version_id.is_(None)
                        | (
                            PlaybookNegativeKnowledge.playbook_version_id
                            == playbook_version_id
                        )
                    ),
                )
            )
        ).scalars().all()
        items = [
            {
                "id": str(item.id),
                "step_text": item.step_text,
                "failure_reason": item.failure_reason,
                "status": item.status,
                "evidence_refs": item.evidence_refs,
            }
            for item in rows
        ]
        return {
            "items": items,
            "conflicts": version.conflicts,
            "empty_means": (
                "No linked negative knowledge — do not invent anti-patterns."
            ),
        }


class HttpPlaybookRetrievalClient:
    def __init__(
        self,
        base_url: str,
        *,
        bearer_token: str | None = None,
        service_token: str | None = None,
        timeout: float = 15.0,
        client: httpx.AsyncClient | None = None,
        allow_insecure_http: bool = False,
    ):
        self.base_url = base_url.rstrip("/")
        scheme = self.base_url.split("://", 1)[0].lower() if "://" in self.base_url else ""
        if scheme != "https" and not (allow_insecure_http and scheme == "http"):
            raise ValueError(
                "HttpPlaybookRetrievalClient requires an https:// base_url; pass "
                "allow_insecure_http=True to use http:// in local development."
            )
        self.bearer_token = bearer_token
        self.service_token = service_token
        self.timeout = timeout
        self.client = client

    def _headers(self) -> dict[str, str]:
        headers: dict[str, str] = {}
        if self.bearer_token:
            headers["Authorization"] = f"Bearer {self.bearer_token}"
        if self.service_token:
            headers["X-Service-Token"] = self.service_token
        return headers

    async def _request(self, method: str, path: str, **kwargs) -> Any:
        owns = self.client is None
        client = self.client or httpx.AsyncClient(timeout=self.timeout)
        try:
            response = await client.request(
                method,
                f"{self.base_url}{path}",
                headers=self._headers(),
                **kwargs,
            )
            response.raise_for_status()
            return response.json()
        finally:
            if owns:
                await client.aclose()

    async def match_playbooks(
        self,
        symptoms: list[str],
        entities: list[str],
        environment: dict,
        top_k: int,
    ) -> list[dict[str, Any]]:
        data = await self._request(
            "POST",
            "/api/v1/runtime/match",
            json={
                "symptoms": symptoms,
                "entities": entities,
                "environment": environment,
                "top_k": top_k,
            },
        )
        return list(data.get("results") or [])

    async def get_playbook(
        self, playbook_id: UUID, version_id: UUID
    ) -> dict[str, Any]:
        versions = await self._request(
            "GET", f"/api/v1/playbooks/{playbook_id}/versions"
        )
        for row in versions or []:
            if str(row.get("id")) == str(version_id):
                return row
        return {
            "error": {
                "code": "version_not_found",
                "message": "Published playbook version not found.",
            }
        }

    async def check_trigger_conditions(
        self,
        playbook_version_id: UUID,
        environment: dict,
        symptoms: list[str],
    ) -> dict[str, Any]:
        return await self._request(
            "POST",
            "/api/v1/agent/trigger-check",
            json={
                "playbook_version_id": str(playbook_version_id),
                "environment": environment,
                "symptoms": symptoms,
            },
        )

    async def get_negative_knowledge(
        self, playbook_version_id: UUID
    ) -> dict[str, Any]:
        return await self._request(
            "GET",
            f"/api/v1/agent/negative-knowledge/{playbook_version_id}",
        )
