from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field


class DecisionTraceEventCreate(BaseModel):
    event_type: str
    inputs: dict = Field(default_factory=dict)
    outputs: dict = Field(default_factory=dict)
    reasoning: str | None = None
    confidence: float | None = None


class DecisionTraceEventResponse(BaseModel):
    id: UUID
    session_id: UUID
    tenant_id: UUID
    event_type: str
    inputs: dict
    outputs: dict
    reasoning: str | None
    confidence: float | None
    created_at: datetime

    model_config = {"from_attributes": True}


class ResolutionSessionCreate(BaseModel):
    domain_id: UUID | None = None
    symptoms: list[str] = Field(default_factory=list)
    entities: list[str] = Field(default_factory=list)
    external_case_ids: list[str] = Field(default_factory=list)
    notes: str | None = None


class ResolutionSessionResponse(BaseModel):
    id: UUID
    tenant_id: UUID
    domain_id: UUID | None
    initiated_by: UUID | None
    status: str
    symptoms: list
    entities: list
    external_case_ids: list
    notes: str | None
    closed_at: datetime | None
    created_at: datetime
    updated_at: datetime
    trace_events: list[DecisionTraceEventResponse] = Field(default_factory=list)

    model_config = {"from_attributes": True}
