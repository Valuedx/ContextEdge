"""What produced a generated artifact (F5).

Prompt and model versions have always reached the cost/observability plane —
``record_llm_usage`` puts them on an ``llm.usage`` operational event. They never
reached the artifact itself, so "which prompt version wrote this episode?"
required a correlation-id join, and only worked when the caller happened to
have a ``db`` session in scope.

Generators stamp this dict on the row they produce. It is deliberately small:
identity of the generating configuration, not a copy of the usage record.

``model_requested`` is the model the task ROUTES to, which is not always the
model that served the call — E1's circuit breaker can substitute
``settings.llm_fallback_model`` mid-call, and only ``llm.usage`` sees that
substitution. ``correlation_id`` is carried for exactly that reason: it is the
join key back to the usage events for this unit of work, where the serving
model and the token counts live. Naming the field ``model`` would have been
a small, quiet lie.
"""

from __future__ import annotations

from typing import Any

GENERATION_PROVENANCE_KEY = "_generation"


def generation_provenance(
    prompt: Any,
    *,
    task: str,
    model_requested: str | None = None,
) -> dict[str, str | None]:
    """Identity of the configuration that generated an artifact.

    ``prompt`` is a registered ``Prompt`` (name + version); ``task`` is the
    routing key ``get_model_for_task`` resolves. Everything is optional at the
    reader's end — a row generated before this shipped simply has NULL, which
    is why nothing here invents a default.
    """
    from contextedge.ai.provider import get_model_for_task
    from contextedge.middleware.request_context import current_correlation_id

    correlation_id = current_correlation_id()
    return {
        "prompt_name": getattr(prompt, "name", None),
        "prompt_version": getattr(prompt, "version", None),
        "task": task,
        "model_requested": model_requested or get_model_for_task(task),
        "correlation_id": str(correlation_id) if correlation_id else None,
    }
