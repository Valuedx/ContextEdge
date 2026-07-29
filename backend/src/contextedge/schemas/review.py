from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field


class ContradictionResponse(BaseModel):
    id: UUID
    tenant_id: UUID
    source_a_ref: str
    source_b_ref: str
    contradiction_type: str
    description: str | None
    resolution_status: str
    resolved_by: UUID | None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class ContradictionStatusUpdate(BaseModel):
    resolution_status: str = Field(..., min_length=1, max_length=30)
    description: str | None = None


class NotificationResponse(BaseModel):
    id: UUID
    tenant_id: UUID
    user_id: UUID | None
    notification_type: str
    title: str
    body: str
    metadata: dict = Field(default_factory=dict, alias="metadata_extra")
    is_read: bool
    read_at: datetime | None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True, "populate_by_name": True}


class NotificationReadUpdate(BaseModel):
    is_read: bool = True


class NegativeKnowledgeCreate(BaseModel):
    domain_id: UUID | None = None
    step_text: str = Field(..., min_length=1)
    failure_reason: str | None = None
    status: str = Field("ineffective", min_length=1, max_length=30)
    evidence_refs: list | None = None


class NegativeKnowledgeUpdate(BaseModel):
    step_text: str | None = None
    failure_reason: str | None = None
    status: str | None = Field(None, min_length=1, max_length=30)
    evidence_refs: list | None = None


class NegativeKnowledgeResponse(BaseModel):
    id: UUID
    tenant_id: UUID
    domain_id: UUID | None
    step_text: str
    failure_reason: str | None
    status: str
    evidence_refs: list | None
    created_by: str | None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class IdentityAliasResponse(BaseModel):
    id: UUID
    canonical_identity_id: UUID
    alias_text: str
    source_id: UUID | None
    confidence: float
    created_by: str | None
    created_at: datetime

    model_config = {"from_attributes": True}


class IdentityResponse(BaseModel):
    id: UUID
    tenant_id: UUID
    entity_type: str
    canonical_name: str
    # Reviewers must be able to tell a parked guess from a trusted identity.
    resolution_state: str = "resolved"
    resolution_confidence: float | None = None
    resolution_method: str | None = None
    metadata_extra: dict | None
    is_active: bool
    created_at: datetime
    updated_at: datetime
    aliases: list[IdentityAliasResponse] = Field(default_factory=list)

    model_config = {"from_attributes": True}


class IdentityUpdate(BaseModel):
    canonical_name: str | None = None
    entity_type: str | None = None
    # Allows a reviewer to promote needs_review/provisional to
    # verified/resolved (validated against RESOLUTION_STATES in the route).
    resolution_state: str | None = None
    metadata_extra: dict | None = None
    is_active: bool | None = None
    add_aliases: list[str] = Field(default_factory=list)


class IdentityMergeRequest(BaseModel):
    primary_identity_id: UUID
    duplicate_identity_id: UUID


class CorrelationEdgeCreate(BaseModel):
    source_evidence_id: UUID
    target_evidence_id: UUID
    correlation_type: str = Field(..., min_length=1, max_length=30)
    confidence: float = Field(0.5, ge=0.0, le=1.0)
    explanation: str | None = None


class CorrelationEdgeUpdate(BaseModel):
    correlation_type: str | None = Field(None, min_length=1, max_length=30)
    confidence: float | None = Field(None, ge=0.0, le=1.0)
    explanation: str | None = None


class CorrelationEdgeResponse(BaseModel):
    id: UUID
    tenant_id: UUID
    source_evidence_id: UUID
    target_evidence_id: UUID
    correlation_type: str
    confidence: float
    explanation: str | None
    created_by: str | None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class CorrelationDecisionRequest(BaseModel):
    decision: str = Field(..., pattern=r"^(accept|reject|merge|split)$")
    confidence: float | None = Field(None, ge=0.0, le=1.0)
    explanation: str | None = None
    replacement_edges: list[CorrelationEdgeCreate] = Field(default_factory=list)


class PatternEvidenceLinkCreate(BaseModel):
    episode_id: UUID | None = None
    evidence_id: UUID | None = None
    link_type: str = Field(..., min_length=1, max_length=50)
    weight: float = Field(1.0, ge=0.0)


class PatternEvidenceLinkResponse(BaseModel):
    id: UUID
    pattern_id: UUID
    episode_id: UUID | None
    evidence_id: UUID | None
    link_type: str
    weight: float
    created_at: datetime

    model_config = {"from_attributes": True}


class RetrievalFeedbackResponse(BaseModel):
    id: UUID
    tenant_id: UUID
    match_id: str | None
    playbook_id: UUID | None
    feedback_type: str
    details: dict | None
    submitted_by: UUID | None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class PolicyAssignmentRequest(BaseModel):
    resource_type: str = Field(..., pattern=r"^(evidence|source|playbook)$")
    resource_id: UUID
    policy_type: str = Field(..., pattern=r"^(access|classification|retention|approval)$")
    policy_id: UUID


class PolicyAssignmentResponse(BaseModel):
    resource_type: str
    resource_id: UUID
    policy_type: str
    policy_id: UUID
