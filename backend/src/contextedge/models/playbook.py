import uuid
from datetime import datetime

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    CheckConstraint,
    Computed,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, TSVECTOR, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from contextedge.models.base import Base, TenantOwnedMixin, TenantScopedMixin

# The operating modes a playbook can run under. Kept in order of
# increasing permissiveness so code can reason about "more automation"
# monotonically.
#
# - ``suggest_only``   — generate a recommendation; never execute.
# - ``shadow``         — execute through the motions but tool calls are
#                         recorded as dry-runs (no real side effects).
#                         Use this to validate a playbook against real
#                         production traffic before flipping to
#                         supervised / full_auto. Approval requests are
#                         still created for audit visibility but tool
#                         invocations short-circuit to a shadow outcome.
# - ``human_confirmed`` — execute one step at a time with explicit
#                         human approval per step.
# - ``supervised``     — execute with a human-in-loop watching;
#                         high_side_effect+ actions still require
#                         per-step approval.
# - ``full_auto``      — execute without approval for admin roles up
#                         through destructive safety class.
AUTOMATION_MODES = (
    "suggest_only",
    "shadow",
    "human_confirmed",
    "supervised",
    "full_auto",
)


def is_shadow_mode(automation_mode: str | None) -> bool:
    """Return True when the given mode indicates shadow execution.

    Centralised so callers that need to branch on "is this a dry-run?"
    don't open-code the string comparison and silently diverge.
    """
    return automation_mode == "shadow"


class Playbook(Base, TenantScopedMixin):
    __tablename__ = "playbooks"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "stable_key",
            name="uq_playbooks_tenant_stable_key",
        ),
        CheckConstraint(
            "risk_tier IN ('minimal', 'low', 'medium', 'high', 'critical', 'restricted')",
            name="ck_playbooks_risk_tier",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    workspace_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    domain_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("domains.id"),
        nullable=True,
    )
    stable_key: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    search_tsvector: Mapped[object | None] = mapped_column(
        TSVECTOR,
        Computed(
            "to_tsvector('english', coalesce(title, '') || ' ' || coalesce(description, ''))",
            persisted=True,
        ),
        deferred=True,
    )
    # Semantic fingerprint of title + description + the newest
    # *published* version's trigger conditions and step titles
    # (migration 0035). Unpublished drafts pointed at by
    # current_version_id are not embedded. Nullable: a playbook with no
    # embedding simply doesn't appear in semantic seeds and falls back
    # to FTS. Written by services/playbook_embedding.py.
    embedding = mapped_column(Vector(3072), nullable=True)
    lexical_search_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    lifecycle_state: Mapped[str] = mapped_column(
        String(30),
        default="candidate",
        nullable=False,
        index=True,
    )
    risk_tier: Mapped[str] = mapped_column(String(20), default="medium", nullable=False)
    automation_mode: Mapped[str] = mapped_column(String(30), default="suggest_only", nullable=False)
    approval_policy_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tenant_policies.id", ondelete="SET NULL"),
        nullable=True,
    )
    owner_user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    reviewer_user_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    approver_user_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    current_version_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    last_validated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    expiry_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    pattern_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("patterns.id"),
        nullable=True,
    )

    versions: Mapped[list["PlaybookVersion"]] = relationship(back_populates="playbook")
    approvals: Mapped[list["PlaybookApproval"]] = relationship(back_populates="playbook")


class PlaybookNegativeKnowledge(Base, TenantOwnedMixin):
    """Explicit playbook↔negative-knowledge link (N8)."""

    __tablename__ = "playbook_negative_knowledge"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "id", name="uq_playbook_negative_knowledge_tenant_id_id"
        ),
        UniqueConstraint(
            "tenant_id",
            "playbook_id",
            "negative_knowledge_id",
            name="uq_pb_nk_tenant_playbook_item",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    playbook_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("playbooks.id", ondelete="CASCADE"), nullable=False
    )
    playbook_version_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )
    negative_knowledge_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("negative_knowledge_items.id", ondelete="CASCADE"),
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class PlaybookVersion(Base, TenantOwnedMixin):
    __tablename__ = "playbook_versions"
    __table_args__ = (
        UniqueConstraint(
            "playbook_id",
            "semantic_version",
            name="uq_playbook_versions_playbook_semantic_version",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    playbook_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("playbooks.id"),
        nullable=False,
        index=True,
    )
    semantic_version: Mapped[str] = mapped_column(String(20), nullable=False)
    trigger_conditions: Mapped[dict] = mapped_column(JSONB, server_default="{}", nullable=False)
    branching_logic: Mapped[dict] = mapped_column(JSONB, server_default="{}", nullable=False)
    inputs: Mapped[dict] = mapped_column(JSONB, server_default="[]", nullable=False)
    outputs: Mapped[dict] = mapped_column(JSONB, server_default="[]", nullable=False)
    steps: Mapped[list] = mapped_column(JSONB, server_default="[]", nullable=False)
    rollback_notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    evidence_refs: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    # Where the approved KB/SOP and observed practice disagree (0050).
    # NULL means "not assessed" — versions generated before knowledge was
    # an input carry NULL rather than an empty list, which would claim
    # the comparison was made and found nothing.
    conflicts: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    playbook_confidence: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    execution_confidence_guidance: Mapped[str | None] = mapped_column(Text, nullable=True)
    verification_policy: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    # What generated this version (0055) — see Episode.generation_provenance.
    # NULL on hand-authored versions, which is the truthful answer for them.
    generation_provenance: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    published_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    # Draft editing (0093). Published rows stay immutable (trigger on
    # steps); these columns track in-place edits of unpublished drafts.
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )
    revision: Mapped[int] = mapped_column(
        Integer, server_default="1", default=1, nullable=False
    )
    created_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    last_edited_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    derived_from_version_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("playbook_versions.id", ondelete="SET NULL"),
        nullable=True,
    )

    playbook: Mapped["Playbook"] = relationship(back_populates="versions")
    evidence_links: Mapped[list["PlaybookEvidenceLink"]] = relationship(back_populates="version")


class PlaybookEvidenceLink(Base, TenantOwnedMixin):
    __tablename__ = "playbook_evidence_links"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    playbook_version_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("playbook_versions.id"),
        nullable=False,
        index=True,
    )
    evidence_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        # ON DELETE SET NULL — preserves the "this playbook version cited
        # evidence that has since been purged" audit record (review F-19,
        # migration 0027). A hard-delete of the evidence leaves the link
        # row with a NULL evidence_id.
        ForeignKey("evidence_items.id", ondelete="SET NULL"),
        nullable=True,
    )
    episode_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    link_type: Mapped[str] = mapped_column(String(50), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    version: Mapped["PlaybookVersion"] = relationship(back_populates="evidence_links")


class PlaybookApproval(Base, TenantOwnedMixin):
    __tablename__ = "playbook_approvals"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    playbook_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("playbooks.id"),
        nullable=False,
        index=True,
    )
    playbook_version_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("playbook_versions.id"),
        nullable=True,
    )
    approver_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    action: Mapped[str] = mapped_column(String(30), nullable=False)
    comments: Mapped[str | None] = mapped_column(Text, nullable=True)
    decided_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    playbook: Mapped["Playbook"] = relationship(back_populates="approvals")
