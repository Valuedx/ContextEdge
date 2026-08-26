import uuid
from datetime import datetime

from sqlalchemy import DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from contextedge.models.base import Base, TimestampMixin


class EvaluationDataset(Base, TimestampMixin):
    """Evaluation gold datasets; timestamps come from TimestampMixin."""

    __tablename__ = "evaluation_datasets"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    cases: Mapped[list] = mapped_column(JSONB, server_default="[]", nullable=False)


class EvaluationRun(Base, TimestampMixin):
    __tablename__ = "evaluation_runs"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    dataset_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("evaluation_datasets.id"), nullable=False
    )
    config: Mapped[dict] = mapped_column(JSONB, server_default="{}", nullable=False)
    status: Mapped[str] = mapped_column(String(30), default="pending", nullable=False)
    results: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class RetrievalFeedback(Base, TimestampMixin):
    """Runtime retrieval feedback; `created_at` is the submission time."""

    __tablename__ = "retrieval_feedback"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    match_id: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    playbook_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    playbook_version_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True, index=True
    )
    feedback_type: Mapped[str] = mapped_column(String(50), nullable=False)
    details: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    submitted_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)


class RuntimeMatchRecord(Base, TimestampMixin):
    """Durable runtime match — Redis stays the hot cache for /explain."""

    __tablename__ = "runtime_match_records"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_runtime_match_records_tenant_id_id"),
        UniqueConstraint("tenant_id", "match_id", name="uq_runtime_match_records_tenant_match"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    match_id: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    query_frame: Mapped[dict] = mapped_column(JSONB, server_default="{}", nullable=False)
    ranked_results: Mapped[list] = mapped_column(JSONB, server_default="[]", nullable=False)
    filters_applied: Mapped[dict] = mapped_column(JSONB, server_default="{}", nullable=False)
    calibrated_confidence: Mapped[float | None] = mapped_column(Float, nullable=True)


class RankingCalibrationConfig(Base, TimestampMixin):
    """Versioned RRF weights + isotonic map. Ranker reads; it does not write."""

    __tablename__ = "ranking_calibration_configs"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "id", name="uq_ranking_calibration_configs_tenant_id_id"
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="active")
    arm_weights: Mapped[dict] = mapped_column(JSONB, server_default="{}", nullable=False)
    isotonic_points: Mapped[list] = mapped_column(JSONB, server_default="[]", nullable=False)
    labels_used: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
