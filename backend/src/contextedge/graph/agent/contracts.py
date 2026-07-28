"""Versioned request, response, and internal Context Graph contracts."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class GraphNodeRef(StrictModel):
    type: str = Field(min_length=1, max_length=50)
    id: UUID

    @property
    def key(self) -> str:
        return f"{self.type}:{self.id}"


class AgentGraphBudget(StrictModel):
    max_nodes: int = Field(default=24, ge=1, le=100)
    max_relationships: int = Field(default=48, ge=0, le=250)
    max_depth: int = Field(default=2, ge=1, le=3)
    max_characters: int = Field(default=12_000, ge=500, le=50_000)


class AgentGraphRequest(StrictModel):
    query: str = Field(default="", max_length=4_000)
    seeds: list[GraphNodeRef] = Field(default_factory=list, max_length=20)
    session_id: UUID | None = None
    entities: list[str] = Field(default_factory=list, max_length=20)
    domain_id: UUID | None = None
    max_depth: int | None = Field(default=None, ge=1, le=3)
    budget: AgentGraphBudget | None = None
    profile: str = Field(default="maf.v1", min_length=1, max_length=50)
    as_of: datetime | None = None

    @field_validator("query")
    @classmethod
    def normalize_query(cls, value: str) -> str:
        return " ".join(value.split())

    @field_validator("entities")
    @classmethod
    def normalize_entities(cls, values: list[str]) -> list[str]:
        result: list[str] = []
        seen: set[str] = set()
        for raw in values:
            value = " ".join(raw.split())[:500]
            key = value.casefold()
            if value and key not in seen:
                seen.add(key)
                result.append(value)
        return result

    @field_validator("as_of")
    @classmethod
    def require_aware_as_of(cls, value: datetime | None) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("as_of must include a timezone")
        return value.astimezone(UTC)


class AgentGraphUsage(StrictModel):
    nodes: int
    relationships: int
    characters: int


class AgentGraphProvenance(StrictModel):
    source_type: str
    created_at: datetime | None = None
    updated_at: datetime | None = None
    current_state: bool = True


class AgentGraphNode(StrictModel):
    key: str
    type: str
    id: UUID
    label: str
    summary: str | None = None
    facts: dict[str, Any] = Field(default_factory=dict)
    confidence: float | None = None
    freshness: float | None = None
    relevance: float
    provenance: AgentGraphProvenance


class AgentGraphRelationship(StrictModel):
    source: str
    target: str
    type: str
    direction: Literal["outgoing"]
    weight: float
    confidence: float | None = None
    relevance: float
    metadata: dict[str, Any] = Field(default_factory=dict)


class AgentGraphSubset(StrictModel):
    schema_version: str = "1.0"
    profile: str
    projection_id: UUID
    generated_at: datetime
    query: str
    seeds: list[GraphNodeRef]
    nodes: list[AgentGraphNode]
    relationships: list[AgentGraphRelationship]
    budget: AgentGraphBudget
    usage: AgentGraphUsage
    truncated: bool = False
    truncation_reasons: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


@dataclass(frozen=True, slots=True)
class AgentGraphAccessScope:
    tenant_id: UUID
    principal_id: UUID
    principal_type: str
    roles: tuple[str, ...] = ()
    workspace_ids: tuple[UUID, ...] = ()
    domain_id: UUID | None = None
    allowed_domain_ids: tuple[UUID, ...] | None = None
    playbook_risk_cap: str = "high"


@dataclass(slots=True)
class RankedGraphSeed:
    ref: GraphNodeRef
    relevance: float = 1.0
    reason: str = "explicit"


@dataclass(slots=True)
class GraphEdgeRecord:
    source: GraphNodeRef
    target: GraphNodeRef
    type: str
    weight: float
    confidence: float | None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class HydratedGraphNode:
    ref: GraphNodeRef
    label: str
    summary: str | None
    facts: dict[str, Any]
    confidence: float | None
    freshness: float | None
    created_at: datetime | None
    updated_at: datetime | None
