from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest

from contextedge.api.v1 import overview
from .conftest import make_user


class _MockScalarResult:
    def __init__(self, value):
        self._value = value

    def scalar(self):
        return self._value


@pytest.mark.asyncio
async def test_get_overview_stats_counts():
    counts = [1, 1, 450, 120, 25, 10, 5]
    mock_db = Mock()
    mock_db.execute = AsyncMock(side_effect=[_MockScalarResult(c) for c in counts])

    user = make_user()
    res = await overview.get_overview_stats(
        db=mock_db,
        user=user,
    )

    assert res.active_sources == 1
    assert res.connected_sources == 1
    assert res.total_evidence == 450
    assert res.total_episodes == 120
    assert res.pending_episodes == 25
    assert res.approved_playbooks == 10
    assert res.candidate_playbooks == 5
    assert mock_db.execute.call_count == 7
