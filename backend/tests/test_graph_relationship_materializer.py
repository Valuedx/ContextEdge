from __future__ import annotations

from uuid import uuid4

import pytest

from contextedge.graph.agent.materializer import GraphRelationshipMaterializer


class EmptyAsyncRows:
    def __aiter__(self):
        return self

    async def __anext__(self):
        raise StopAsyncIteration


class StreamingDb:
    def __init__(self):
        self.execution_options = []

    async def stream_scalars(self, statement):
        self.execution_options.append(statement.get_execution_options())
        return EmptyAsyncRows()

    async def stream(self, statement):
        self.execution_options.append(statement.get_execution_options())
        return EmptyAsyncRows()


@pytest.mark.asyncio
async def test_reconciliation_uses_bounded_server_side_streams():
    db = StreamingDb()

    result = await GraphRelationshipMaterializer(db).reconcile_tenant(
        uuid4(),
        batch_size=37,
    )

    assert result.relationships_seen == 0
    assert db.execution_options
    assert all(options["yield_per"] == 37 for options in db.execution_options)
