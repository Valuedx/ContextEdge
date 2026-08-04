from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field


class SourceCreate(BaseModel):
    source_type: str = Field(
        ...,
        pattern=(
            r"^(local_file|teams|gmail|servicenow|jira_sm|manageengine"
            r"|sapphireims|zoho_desk|confluence|sharepoint|exchange)$"
        ),
    )
    display_name: str = Field(..., min_length=1, max_length=255)
    purpose: str | None = None
    workspace_id: UUID | None = None
    domain_ids: list[UUID] = Field(default_factory=list)
    auth_type: str = "oauth2"
    sync_mode: str = "incremental"
    config: dict = Field(default_factory=dict)
    credentials: dict = Field(default_factory=dict)
    classification_policy_id: UUID | None = None
    retention_policy_id: UUID | None = None


class SourceTypeResponse(BaseModel):
    """One selectable source type, and whether it can actually sync.

    ``connector_available`` is the field the picker must gate on. A type
    with ``false`` is accepted by ``SourceCreate`` but has no connector,
    so a source created against it fails at discovery, not at creation.
    """

    source_type: str
    label: str
    connector_available: bool
    status: str
    description: str = ""


class SourceUpdate(BaseModel):
    display_name: str | None = Field(None, min_length=1, max_length=255)
    purpose: str | None = None
    domain_ids: list[UUID] | None = None
    sync_mode: str | None = None
    config: dict | None = None
    is_active: bool | None = None
    classification_policy_id: UUID | None = None
    retention_policy_id: UUID | None = None


class SourceResponse(BaseModel):
    id: UUID
    tenant_id: UUID
    workspace_id: UUID | None
    domain_ids: list
    source_type: str
    display_name: str
    owner_user_id: UUID
    purpose: str | None
    auth_type: str
    auth_status: str
    discovery_status: str
    sync_mode: str
    classification_policy_id: UUID | None = None
    retention_policy_id: UUID | None = None
    config: dict
    is_active: bool
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class SourceObjectResponse(BaseModel):
    id: UUID
    source_id: UUID
    tenant_id: UUID
    object_type: str
    external_id: str
    display_name: str
    object_path: str | None
    owner_hint: str | None
    sensitivity_label: str | None
    approved_for_backfill: bool
    approved_for_sync: bool
    backfill_window_days: int | None
    steady_state_sync_enabled: bool
    last_checkpoint_at: datetime | None
    last_successful_sync_at: datetime | None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class SourceObjectApproval(BaseModel):
    approved_for_backfill: bool | None = None
    approved_for_sync: bool | None = None
    backfill_window_days: int | None = Field(None, ge=1, le=365)


class SyncRunResponse(BaseModel):
    id: UUID
    source_id: UUID
    source_object_id: UUID | None
    tenant_id: UUID
    run_type: str
    status: str
    started_at: datetime | None
    completed_at: datetime | None
    items_processed: int
    errors: dict | None
    created_at: datetime

    model_config = {"from_attributes": True}


class BackfillRequest(BaseModel):
    source_object_ids: list[UUID]
    window_days: int = Field(90, ge=1, le=365)


class LocalFilePayload(BaseModel):
    filename: str
    content: str
    content_type: str = "text/plain"
    metadata: dict = Field(default_factory=dict)


class LocalIngestRequest(BaseModel):
    source_id: UUID
    files: list[LocalFilePayload]
    # What kind of content this batch is. Uploads are the one ingestion
    # path with a human present, so the document kind is asked rather
    # than guessed — an SOP PDF looks like any other file from its name,
    # and mistyping it costs knowledge authority and long-term memory
    # placement. Per-file `metadata.evidence_type` overrides this.
    evidence_type: str | None = Field(
        None,
        description=(
            "Content kind for the whole batch: kb_article, sop, "
            "documentation, runbook, postmortem, transcript, document, "
            "or message."
        ),
    )


class CredentialRotateRequest(BaseModel):
    credentials: dict = Field(default_factory=dict)
    auth_type: str | None = None


class SourceCredentialResponse(BaseModel):
    id: UUID
    source_id: UUID
    auth_type: str
    status: str
    rotated_at: datetime | None
    created_at: datetime

    model_config = {"from_attributes": True}
