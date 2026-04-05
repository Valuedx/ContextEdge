import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from contextedge.models.base import Base, TenantScopedMixin


class Source(Base, TenantScopedMixin):
    __tablename__ = "sources"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False, index=True
    )
    workspace_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("workspaces.id"), nullable=True
    )
    domain_ids: Mapped[list] = mapped_column(JSONB, server_default="[]", nullable=False)
    source_type: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    display_name: Mapped[str] = mapped_column(String(255), nullable=False)
    owner_user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=False
    )
    purpose: Mapped[str | None] = mapped_column(Text, nullable=True)
    auth_type: Mapped[str] = mapped_column(String(50), nullable=False, default="oauth2")
    auth_status: Mapped[str] = mapped_column(
        String(30), nullable=False, default="pending"
    )
    discovery_status: Mapped[str] = mapped_column(
        String(30), nullable=False, default="not_started"
    )
    sync_mode: Mapped[str] = mapped_column(
        String(30), nullable=False, default="incremental"
    )
    classification_policy_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenant_policies.id", ondelete="SET NULL"), nullable=True
    )
    retention_policy_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenant_policies.id", ondelete="SET NULL"), nullable=True
    )
    residency_region: Mapped[str | None] = mapped_column(String(50), nullable=True)
    config: Mapped[dict] = mapped_column(JSONB, server_default="{}", nullable=False)
    is_active: Mapped[bool] = mapped_column(default=True, nullable=False)

    source_objects: Mapped[list["SourceObject"]] = relationship(back_populates="source")
    credentials: Mapped[list["SourceCredential"]] = relationship(back_populates="source")
    sync_runs: Mapped[list["SyncRun"]] = relationship(back_populates="source")


class SourceObject(Base, TenantScopedMixin):
    __tablename__ = "source_objects"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False, index=True
    )
    source_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("sources.id"), nullable=False, index=True
    )
    object_type: Mapped[str] = mapped_column(String(50), nullable=False)
    external_id: Mapped[str] = mapped_column(String(500), nullable=False)
    display_name: Mapped[str] = mapped_column(String(255), nullable=False)
    object_path: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    owner_hint: Mapped[str | None] = mapped_column(String(255), nullable=True)
    sensitivity_label: Mapped[str | None] = mapped_column(String(50), nullable=True)
    approved_for_backfill: Mapped[bool] = mapped_column(default=False, nullable=False)
    approved_for_sync: Mapped[bool] = mapped_column(default=False, nullable=False)
    backfill_window_days: Mapped[int | None] = mapped_column(Integer, nullable=True)
    steady_state_sync_enabled: Mapped[bool] = mapped_column(default=False, nullable=False)
    last_checkpoint_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_successful_sync_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    metadata_extra: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    source: Mapped["Source"] = relationship(back_populates="source_objects")
    checkpoints: Mapped[list["SyncCheckpoint"]] = relationship(back_populates="source_object")


class SourceCredential(Base):
    __tablename__ = "source_credentials"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    source_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("sources.id"), nullable=False
    )
    auth_type: Mapped[str] = mapped_column(String(50), nullable=False)
    encrypted_credentials: Mapped[bytes] = mapped_column(nullable=False)
    status: Mapped[str] = mapped_column(String(30), default="active", nullable=False)
    rotated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    source: Mapped["Source"] = relationship(back_populates="credentials")


class SyncCheckpoint(Base):
    __tablename__ = "sync_checkpoints"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    source_object_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("source_objects.id"), nullable=False, index=True
    )
    checkpoint_data: Mapped[dict] = mapped_column(JSONB, nullable=False)
    captured_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    source_object: Mapped["SourceObject"] = relationship(back_populates="checkpoints")


class SyncRun(Base):
    __tablename__ = "sync_runs"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    source_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("sources.id"), nullable=False, index=True
    )
    source_object_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("source_objects.id"), nullable=True
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False, index=True
    )
    run_type: Mapped[str] = mapped_column(String(30), nullable=False)
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="pending")
    started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    items_processed: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    errors: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    source: Mapped["Source"] = relationship(back_populates="sync_runs")
