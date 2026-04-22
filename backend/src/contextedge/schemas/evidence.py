from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field


class EvidenceItemResponse(BaseModel):
    id: UUID
    tenant_id: UUID
    source_id: UUID
    evidence_type: str
    title: str | None
    body_summary: str | None
    relevance_state: str
    relevance_score: float | None
    delta_signal: str | None = None
    created_at_source: datetime | None
    ingested_at: datetime
    created_at: datetime

    model_config = {"from_attributes": True}


class EvidenceItemDetail(EvidenceItemResponse):
    body_text: str | None
    workspace_id: UUID | None
    domain_id: UUID | None
    source_object_id: UUID | None
    thread_id: UUID | None
    sensitivity_label: str | None
    access_policy_id: UUID | None = None
    canonical_entity_refs: dict | None
    baseline_ref: dict | None = None
    delta_signal: str | None = None


class EvidenceAccessPolicyUpdate(BaseModel):
    """Body for PATCH …/access-policy. Send null to clear."""

    access_policy_id: UUID | None = Field(
        ...,
        description="Tenant access policy id, or null to remove assignment",
    )


class ThreadResponse(BaseModel):
    id: UUID
    tenant_id: UUID
    source_id: UUID
    external_thread_id: str
    title: str | None
    participant_count: int
    message_count: int
    first_message_at: datetime | None
    last_message_at: datetime | None
    hydration_status: str
    relevance_state: str
    created_at: datetime

    model_config = {"from_attributes": True}


class EpisodeResponse(BaseModel):
    id: UUID
    tenant_id: UUID
    domain_id: UUID | None
    title: str
    status: str
    extraction_confidence: float
    root_cause_summary: str | None
    final_outcome: str | None
    reviewer_state: str
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class EpisodeDetail(EpisodeResponse):
    workspace_id: UUID | None
    primary_case_ref: str | None
    reviewer_user_id: UUID | None
    evidence_ids: list | None
    entity_refs: dict | None
    steps: list["EpisodeStepResponse"] = []


class EpisodeStepResponse(BaseModel):
    id: UUID
    episode_id: UUID
    step_order: int
    step_type: str
    text: str
    observation: str | None
    result_state: str
    failed_flag: bool
    successful_flag: bool
    extraction_confidence: float
    evidence_refs: list | None

    model_config = {"from_attributes": True}


class EpisodeStepUpdate(BaseModel):
    step_order: int | None = Field(None, ge=0)
    step_type: str | None = None
    text: str | None = None
    observation: str | None = None
    result_state: str | None = None
    failed_flag: bool | None = None
    successful_flag: bool | None = None
    extraction_confidence: float | None = Field(None, ge=0.0, le=1.0)
    evidence_refs: list | None = None


class EpisodeUpdate(BaseModel):
    title: str | None = None
    status: str | None = None
    reviewer_state: str | None = None
    reviewer_user_id: UUID | None = None
    root_cause_summary: str | None = None
    final_outcome: str | None = None


class EvidenceSearchParams(BaseModel):
    query: str | None = None
    source_type: str | None = None
    domain_id: UUID | None = None
    relevance_state: str | None = None
    from_date: datetime | None = None
    to_date: datetime | None = None
    limit: int = Field(50, ge=1, le=200)
    offset: int = Field(0, ge=0)


class ReconstructRequest(BaseModel):
    evidence_ids: list[UUID] | None = None
    domain_id: UUID | None = None


class EvidenceBulkDeleteRequest(BaseModel):
    ids: list[UUID]


class AttachmentArtifactResponse(BaseModel):
    id: UUID
    evidence_id: UUID
    filename: str
    mime_type: str | None
    size_bytes: int | None
    object_storage_key: str
    extracted_text: str | None
    extraction_status: str
    parser_type: str | None
    parser_confidence: float | None
    extraction_error: str | None
    parser_metadata: dict | None
    extracted_at: datetime | None
    created_at: datetime

    model_config = {"from_attributes": True}
