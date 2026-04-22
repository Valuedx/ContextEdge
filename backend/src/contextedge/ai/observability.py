"""LLM observability — Prometheus counters + structured logs + operational events.

Every LLM call should flow through ``record_llm_usage`` after completion so
that four things happen consistently:

1. **Prometheus counters** — realtime metrics exposed on ``/metrics``, used
   by Grafana dashboards and alerting.
2. **Structured logs** — one ``llm.usage`` log line per call with tenant,
   model, task, and token breakdown for ad-hoc debugging.
3. **Operational events** — persisted to ``operational_events`` so the
   admin cost dashboard can query historical spend over arbitrary windows
   without scraping Prometheus.
4. **Cache-hit awareness** — OpenAI returns ``cached_tokens`` under
   ``prompt_tokens_details`` when its automatic prefix cache hits; Anthropic
   returns ``cache_read_input_tokens`` when ``cache_control`` blocks hit.
   Both are normalised into a single ``cached_tokens`` counter here so the
   dashboard shows one cache-hit-rate metric regardless of provider.

The helper is provider-agnostic: it reads whatever token fields the LiteLLM
response exposes and writes zeros for anything missing. Callers never branch
on provider identity.
"""

from __future__ import annotations

import uuid
from typing import Any

import structlog
from prometheus_client import Counter

logger = structlog.get_logger()

# --- Prometheus counters --------------------------------------------------
# Labels kept deliberately small (Prometheus cardinality is a real cost).
# tenant_id is a label because per-tenant cost is the first question every
# enterprise customer asks; task and model are small bounded sets.

LLM_TOKENS_TOTAL = Counter(
    "contextedge_llm_tokens_total",
    "Tokens consumed by LLM calls, by type (prompt/completion/cached).",
    labelnames=("tenant_id", "model", "task", "token_type"),
)

LLM_REQUESTS_TOTAL = Counter(
    "contextedge_llm_requests_total",
    "LLM requests issued, labelled by tenant/model/task/outcome.",
    labelnames=("tenant_id", "model", "task", "outcome"),
)


# --- Helper to normalise usage from LiteLLM response -----------------------


def _safe_int(value: Any) -> int:
    """Coerce a possibly-None / possibly-missing attribute to int."""
    try:
        if value is None:
            return 0
        return int(value)
    except (ValueError, TypeError):
        return 0


def extract_usage(response: Any) -> dict[str, int]:
    """Return ``{prompt_tokens, completion_tokens, cached_tokens, total_tokens}``
    from a LiteLLM completion response, normalising across providers.

    OpenAI exposes cached tokens under ``usage.prompt_tokens_details.cached_tokens``.
    Anthropic exposes them under ``usage.cache_read_input_tokens``.
    Missing fields → 0, never raise.
    """
    usage = getattr(response, "usage", None)
    if usage is None:
        return {"prompt_tokens": 0, "completion_tokens": 0, "cached_tokens": 0, "total_tokens": 0}

    prompt = _safe_int(getattr(usage, "prompt_tokens", 0))
    completion = _safe_int(getattr(usage, "completion_tokens", 0))
    cached = 0

    # OpenAI path — usage.prompt_tokens_details.cached_tokens
    details = getattr(usage, "prompt_tokens_details", None)
    if details is not None:
        cached = _safe_int(getattr(details, "cached_tokens", 0))

    # Anthropic path — usage.cache_read_input_tokens (LiteLLM surface)
    if cached == 0:
        cached = _safe_int(getattr(usage, "cache_read_input_tokens", 0))

    return {
        "prompt_tokens": prompt,
        "completion_tokens": completion,
        "cached_tokens": cached,
        "total_tokens": prompt + completion,
    }


# --- Main recorder ---------------------------------------------------------


async def record_llm_usage(
    *,
    tenant_id: uuid.UUID | str | None,
    model: str,
    task: str,
    response: Any | None = None,
    usage: dict[str, int] | None = None,
    outcome: str = "ok",
    duration_ms: int | None = None,
    db: Any | None = None,
) -> dict[str, int]:
    """Record a completed LLM call across Prometheus, logs, and optionally DB.

    Callers should pass either ``response`` (a LiteLLM completion object) or
    ``usage`` (a pre-extracted dict). Passing a DB session additionally
    persists an ``OperationalEvent(event_type="llm.usage")`` so the admin
    cost dashboard can query historical spend — we write to events instead
    of a dedicated table to avoid a new migration and to keep tenant
    scoping consistent with the rest of the audit model.

    Returns the extracted usage dict so callers can log it with their own
    context if desired.
    """
    if usage is None:
        usage = extract_usage(response)
    tenant_label = str(tenant_id) if tenant_id is not None else "unknown"

    # Prometheus — one Counter.inc per token type keeps the metric easy to
    # sum in Grafana (``sum by (tenant_id) (rate(contextedge_llm_tokens_total[5m]))``).
    LLM_TOKENS_TOTAL.labels(tenant_label, model, task, "prompt").inc(usage["prompt_tokens"])
    LLM_TOKENS_TOTAL.labels(tenant_label, model, task, "completion").inc(usage["completion_tokens"])
    LLM_TOKENS_TOTAL.labels(tenant_label, model, task, "cached").inc(usage["cached_tokens"])
    LLM_REQUESTS_TOTAL.labels(tenant_label, model, task, outcome).inc()

    # Structured log — one line per call with everything in one place.
    log_context = {
        "tenant_id": tenant_label,
        "model": model,
        "task": task,
        "prompt_tokens": usage["prompt_tokens"],
        "completion_tokens": usage["completion_tokens"],
        "cached_tokens": usage["cached_tokens"],
        "outcome": outcome,
    }
    if duration_ms is not None:
        log_context["duration_ms"] = duration_ms
    logger.info("llm.usage", **log_context)

    # Operational event — optional, skipped when no db is passed (e.g. from
    # background tasks that don't have a session handy). Callers that want
    # dashboard visibility should pass db.
    if db is not None and tenant_id is not None:
        try:
            from contextedge.services.event_log_service import append_operational_event

            tid = tenant_id if isinstance(tenant_id, uuid.UUID) else uuid.UUID(str(tenant_id))
            await append_operational_event(
                db,
                tenant_id=tid,
                entity_type="llm_usage",
                event_type="llm.usage",
                payload={
                    "model": model,
                    "task": task,
                    "prompt_tokens": usage["prompt_tokens"],
                    "completion_tokens": usage["completion_tokens"],
                    "cached_tokens": usage["cached_tokens"],
                    "total_tokens": usage["total_tokens"],
                    "outcome": outcome,
                    "duration_ms": duration_ms,
                },
            )
        except Exception as exc:
            # Never let observability failure break the actual LLM work.
            logger.warning("llm.usage_event_failed", error=str(exc))

    return usage


# --- Prompt shaping --------------------------------------------------------


def build_messages(
    system_prompt: str | None,
    user_prompt: str,
    cache_system: bool = True,
) -> list[dict[str, Any]]:
    """Build a LiteLLM-compatible messages list with an explicit system/user
    split, optionally marking the system block for prompt caching.

    Why the split matters:
    - OpenAI automatically caches repeated prefixes of ≥1024 tokens on GPT-4o
      family. Putting the static instructions in ``system`` and the dynamic
      evidence in ``user`` gives the automatic cache a stable prefix to hit.
    - Anthropic does not cache automatically — the caller must mark blocks
      with ``cache_control: {"type": "ephemeral"}``. When ``cache_system`` is
      True and the system prompt is non-empty, we emit a multi-block content
      with cache_control on the system block. LiteLLM routes this to
      Anthropic's native caching.

    On OpenAI models the ``cache_control`` marker is silently ignored, so
    callers can pass ``cache_system=True`` unconditionally.
    """
    messages: list[dict[str, Any]] = []
    if system_prompt:
        if cache_system:
            messages.append(
                {
                    "role": "system",
                    "content": [
                        {
                            "type": "text",
                            "text": system_prompt,
                            "cache_control": {"type": "ephemeral"},
                        }
                    ],
                }
            )
        else:
            messages.append({"role": "system", "content": system_prompt})
    messages.append({"role": "user", "content": user_prompt})
    return messages
