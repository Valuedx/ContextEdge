
from pydantic import BaseModel, Field


class PaginationParams(BaseModel):
    cursor: str | None = None
    limit: int = 50


class PaginatedResponse(BaseModel):
    items: list
    next_cursor: str | None = None
    total_count: int | None = None


class ErrorResponse(BaseModel):
    detail: str
    code: str | None = None


# Review C-02 / C-06: shared response shapes for the handful of
# endpoints that used to return raw ``dict`` instead of declaring a
# response_model. The trio covers ~all the previously-untyped routes.
# Endpoints with genuinely rich payloads (graph subgraph, decision
# effectiveness) keep their own bespoke schemas.


class StatusResponse(BaseModel):
    """Minimal ack — ``{"status": "<verb_past_tense>"}``."""

    status: str = Field(
        ...,
        description=(
            "Short past-tense verb: ``updated``, ``rejected``, "
            "``deleted``, ``accepted``, …"
        ),
    )
    detail: dict | None = Field(
        None,
        description="Optional unstructured supplemental detail.",
    )


class TaskDispatchResponse(BaseModel):
    """Response for endpoints that enqueue a background job."""

    status: str = Field(
        ...,
        description=(
            "Usually ``queued``. Mutations that persist + enqueue use "
            "``ingested`` or a verb reflecting the sync half."
        ),
    )
    task_id: str | None = Field(
        None,
        description="Celery task id, when the dispatch returned one.",
    )
    detail: dict | None = Field(
        None,
        description=(
            "Task-specific supplemental info — e.g. "
            '``{"object_count": 12}`` on backfill enqueue.'
        ),
    )


class MutationAckResponse(BaseModel):
    """Ack with the id of the created / updated entity."""

    status: str
    id: str = Field(..., description="UUID of the affected entity.")
