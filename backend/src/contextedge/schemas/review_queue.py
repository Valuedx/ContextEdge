"""Schemas for the review-queue bundle endpoint.

The bundle composes session, top-pending decision, similar-decision
aggregate, execution runs, and recent operational events into a single
payload so the reviewer console can render the 7-zone layout in one round
trip instead of fanning out across endpoints.
"""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field

from contextedge.schemas.decision import DecisionResponse
from contextedge.schemas.execution import ExecutionRunResponse
from contextedge.schemas.session import ResolutionSessionResponse


class ConfidenceBadge(BaseModel):
    """Server-derived confidence badge so the UI doesn't re-implement thresholds.

    Thresholds: green > 0.8, amber 0.5–0.8, red < 0.5. `level` is null when
    the decision has no confidence score recorded.
    """

    score: float | None
    level: Literal["red", "amber", "green"] | None


class SimilarDecisionAggregate(BaseModel):
    """Provenance for ranked hypotheses — "based on N similar tickets"."""

    decision_type: str
    context_filters: dict = Field(default_factory=dict)
    total_count: int
    outcomes: dict[str, int] = Field(
        default_factory=dict,
        description="Counts grouped by DecisionOutcome.execution_result",
    )
    success_rate: float | None = Field(
        None,
        description="success / (success+failure+rejected+timeout+partial); null when no outcomes recorded",
    )


class OperationalEventBrief(BaseModel):
    id: UUID
    event_type: str
    entity_type: str
    entity_id: str | None
    occurred_at: datetime
    payload: dict

    model_config = {"from_attributes": True}


class ReviewQueueContext(BaseModel):
    """Bundle response powering the reviewer console's 7-zone render."""

    session: ResolutionSessionResponse
    top_decision: DecisionResponse | None
    top_decision_badge: ConfidenceBadge | None
    similar: SimilarDecisionAggregate | None
    decisions: list[DecisionResponse] = Field(default_factory=list)
    execution_runs: list[ExecutionRunResponse] = Field(default_factory=list)
    recent_events: list[OperationalEventBrief] = Field(default_factory=list)
