"""Shared temporal predicates for Context Graph edge queries."""

from datetime import UTC, datetime, timedelta

from fastapi import HTTPException, status
from sqlalchemy import and_, or_
from sqlalchemy.sql.elements import ColumnElement

from contextedge.models.pattern import GraphEdge


def normalize_graph_as_of(as_of: datetime | None) -> datetime | None:
    if as_of is None:
        return None
    if as_of.tzinfo is None or as_of.utcoffset() is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="as_of must include a timezone offset",
        )
    normalized = as_of.astimezone(UTC)
    if normalized > datetime.now(UTC) + timedelta(minutes=5):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="as_of cannot be in the future",
        )
    return normalized


def edge_valid_at(as_of: datetime | None) -> ColumnElement[bool]:
    """Return the validity predicate for current or point-in-time traversal."""
    if as_of is None:
        return GraphEdge.valid_to.is_(None)
    return and_(
        GraphEdge.valid_from <= as_of,
        or_(GraphEdge.valid_to.is_(None), GraphEdge.valid_to > as_of),
    )
