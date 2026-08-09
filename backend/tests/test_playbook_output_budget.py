"""The output budget that silently emptied a playbook.

Playbook generation asked for 16384 output tokens and got 4096, because
the global cost ceiling was applied as an absolute maximum rather than a
default. The response ran out of budget partway through the steps array;
the JSON-repair path salvaged the complete prefix; title, description and
risk_tier all arrived intact. A playbook with ZERO steps was persisted as
a review candidate and the task returned {"status": "ok"}.

Nothing downstream had any reason to suspect the artifact was empty,
which is what made it worth two tests rather than one: the budget must be
right, AND an empty result must never again be storable as a success.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

import pytest

from contextedge.config import settings


def _effective(requested: int, task: str) -> int:
    """The clamp as ``provider.llm_complete`` applies it."""
    ceiling = settings.llm_task_output_tokens.get(task, settings.llm_max_output_tokens)
    return min(requested, ceiling)


def test_playbook_generation_is_not_clamped_to_the_global_default():
    """The regression. 16384 -> 4096 was enough to truncate every
    playbook whose prompt included knowledge and conflicts."""
    assert _effective(16384, "playbook") == 16384
    assert _effective(16384, "playbook") > settings.llm_max_output_tokens


def test_extraction_is_not_clamped_either():
    """The same regression, in the task the first fix walked past.

    ``llm_complete_json`` asks for 16384 on extraction AND playbook "to
    avoid truncation", and this map is what overrides it — so exempting
    only playbook left episode reconstruction requesting 16384 and
    receiving 4096. Observed live: completion_tokens 4082 against the
    4096 ceiling, of which reasoning_tokens 3930, leaving ~150 tokens of
    answer. The JSON stopped mid-object, repair failed at the same
    offset on every attempt, and the episode was discarded.
    """
    assert _effective(16384, "extraction") == 16384
    assert _effective(16384, "extraction") > settings.llm_max_output_tokens


def test_other_tasks_keep_the_global_cost_ceiling():
    """The cap is a real cost guard and stays the default. Only tasks
    whose correct answer is genuinely longer opt out."""
    for task in ("relevance", "classification", "identity", ""):
        assert _effective(16384, task) == settings.llm_max_output_tokens


def test_a_caller_asking_for_less_than_the_ceiling_still_gets_less():
    """The override raises the ceiling; it does not inflate requests."""
    assert _effective(512, "playbook") == 512


def test_the_override_is_configurable_per_deployment():
    with patch.object(settings, "llm_task_output_tokens", {"playbook": 2048}):
        assert _effective(16384, "playbook") == 2048


def test_playbook_has_its_own_task_name_for_attribution():
    """It shared "extraction", so it also shared a token budget sized for
    something else and disappeared into another line on the cost
    dashboard."""
    import inspect

    from contextedge.ai.generators import playbook_generator

    source = inspect.getsource(playbook_generator.generate_playbook_candidate)
    assert 'task="playbook"' in source


def test_the_json_task_budget_covers_playbook():
    """``llm_complete_json`` picks the requested size by task; playbook
    must land in the large bucket or the ceiling raise achieves nothing."""
    import inspect

    from contextedge.ai import provider

    source = inspect.getsource(provider.llm_complete_json)
    assert '"playbook"' in source


def test_the_request_and_the_ceiling_agree_on_which_tasks_are_long():
    """The two halves disagreeing is the bug itself, twice over.

    ``llm_complete_json`` decides what to ask for and this map decides
    what may be granted. A task in the first list but not the second
    requests a large budget, is silently cut to the default, and returns
    a truncated answer that reads as a considered one — with nothing in
    between to report the difference.
    """
    import inspect

    from contextedge.ai import provider

    source = inspect.getsource(provider.llm_complete_json)
    asks_large = {
        t for t in ("extraction", "playbook", "pattern") if f'"{t}"' in source
    }
    assert asks_large <= set(settings.llm_task_output_tokens)


@pytest.mark.asyncio
@pytest.mark.parametrize("target_state", ["under_review", "approved"])
async def test_a_stepless_playbook_cannot_enter_the_governance_path(target_state):
    """The creation guard does not cover what is already stored.

    Versions authored directly through the API default `steps` to an
    empty list, and rows predating the guard are still there. Sending an
    empty draft to review costs a reviewer their time to discover there
    is nothing to read; approving one produces something that looks like
    a certified procedure and executes as a no-op.
    """
    from unittest.mock import AsyncMock, Mock
    from uuid import uuid4

    from contextedge.services.playbook_service import (
        InvalidTransitionError,
        transition_playbook,
    )

    version_id = uuid4()
    playbook = SimpleNamespace(
        id=uuid4(),
        tenant_id=uuid4(),
        lifecycle_state="candidate" if target_state == "under_review" else "under_review",
        current_version_id=version_id,
        approver_user_id=None,
        last_validated_at=None,
        embedding=None,
    )
    version = SimpleNamespace(
        id=version_id,
        playbook_id=playbook.id,
        semantic_version="0.1.0",
        steps=[],
        published_at=None,
        published_by=None,
    )
    db = SimpleNamespace(
        get=AsyncMock(return_value=version), add=Mock(), flush=AsyncMock()
    )

    with pytest.raises(InvalidTransitionError, match="no steps"):
        await transition_playbook(db, playbook, target_state, uuid4())

    # And it did not half-apply the transition on the way out.
    assert playbook.lifecycle_state != target_state


def test_a_stepless_version_cannot_be_executed():
    """Covers rows approved before the transition guard existed.

    Without this a stepless version starts a run, creates no step_runs,
    requests no approvals and reports success — an execution record
    attesting to work nobody did, which is worse than an error.
    """
    import inspect

    from contextedge.services import execution_service

    source = inspect.getsource(execution_service.start_execution)
    guard = source.index("there is nothing to execute")
    loop = source.index("for idx, step_data in enumerate(steps)")
    assert guard < loop


def test_a_stepless_result_is_a_failure_not_a_candidate():
    """The guard that matters most.

    An empty playbook in the review queue costs a reviewer's time to
    discover it is worthless, and it reads as the generator's considered
    opinion rather than a dropped response.
    """
    import inspect

    from contextedge.workers import pattern_tasks

    source = inspect.getsource(pattern_tasks.generate_playbook_candidate)
    guard = source.index("no_steps_generated")
    persist = source.index("playbook = Playbook(")
    # The guard has to return BEFORE anything is written.
    assert guard < persist
