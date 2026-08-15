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

# Deliberately a separate metric rather than another `token_type` label on
# LLM_TOKENS_TOTAL: reasoning tokens are a *subset* of completion tokens, so
# adding them as a label value would silently double-count any dashboard that
# sums across token_type.
LLM_REASONING_TOKENS_TOTAL = Counter(
    "contextedge_llm_reasoning_tokens_total",
    "Thinking tokens, a subset of completion tokens (not additive).",
    labelnames=("tenant_id", "model", "task"),
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
    """Return ``{prompt_tokens, completion_tokens, reasoning_tokens,
    cached_tokens, total_tokens}`` from a LiteLLM completion response,
    normalising across providers.

    OpenAI exposes cached tokens under ``usage.prompt_tokens_details.cached_tokens``.
    Anthropic exposes them under ``usage.cache_read_input_tokens``.
    Missing fields → 0, never raise.

    ``reasoning_tokens`` is a **breakdown of** ``completion_tokens``, not an
    addition to it. Thinking models report it under
    ``completion_tokens_details.reasoning_tokens``, and LiteLLM normalises every
    provider onto the OpenAI convention where the thinking tokens are already
    counted inside ``completion_tokens`` — verified against
    ``vertex_ai/gemini-2.5-flash``: completion 362 = 110 text + 252 reasoning,
    with ``total_tokens`` equal to prompt + completion. Cost must therefore
    price ``completion_tokens`` once; reasoning is reported so operators can see
    how much of the output spend was thinking rather than answer.
    """
    usage = getattr(response, "usage", None)
    if usage is None:
        return {
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "reasoning_tokens": 0,
            "cached_tokens": 0,
            "total_tokens": 0,
        }

    prompt = _safe_int(getattr(usage, "prompt_tokens", 0))
    completion = _safe_int(getattr(usage, "completion_tokens", 0))
    cached = 0

    # Thinking tokens, when the model reports them. Already inside `completion`.
    completion_details = getattr(usage, "completion_tokens_details", None)
    reasoning = _safe_int(getattr(completion_details, "reasoning_tokens", 0))

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
        "reasoning_tokens": reasoning,
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
    prompt_name: str | None = None,
    prompt_version: str | None = None,
    db: Any | None = None,
    subject_type: str | None = None,
    subject_id: Any | None = None,
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
    # Callers may hand in a usage dict built before reasoning tokens were
    # tracked, so read it defensively rather than assuming the key exists.
    reasoning_tokens = int(usage.get("reasoning_tokens", 0) or 0)
    tenant_label = str(tenant_id) if tenant_id is not None else "unknown"

    # Prometheus — one Counter.inc per token type keeps the metric easy to
    # sum in Grafana (``sum by (tenant_id) (rate(contextedge_llm_tokens_total[5m]))``).
    LLM_TOKENS_TOTAL.labels(tenant_label, model, task, "prompt").inc(usage["prompt_tokens"])
    LLM_TOKENS_TOTAL.labels(tenant_label, model, task, "completion").inc(usage["completion_tokens"])
    LLM_TOKENS_TOTAL.labels(tenant_label, model, task, "cached").inc(usage["cached_tokens"])
    LLM_REQUESTS_TOTAL.labels(tenant_label, model, task, outcome).inc()
    if reasoning_tokens:
        LLM_REASONING_TOKENS_TOTAL.labels(tenant_label, model, task).inc(reasoning_tokens)

    # Structured log — one line per call with everything in one place.
    # Correlation IDs are pulled from the request-context ContextVar so the
    # same log line can be grepped across HTTP → Celery → LLM boundaries.
    from contextedge.middleware.request_context import (
        current_causation_id,
        current_correlation_id,
        current_request_id,
    )

    log_context = {
        "tenant_id": tenant_label,
        "model": model,
        "task": task,
        "prompt_tokens": usage["prompt_tokens"],
        "completion_tokens": usage["completion_tokens"],
        "reasoning_tokens": reasoning_tokens,
        "cached_tokens": usage["cached_tokens"],
        "outcome": outcome,
    }
    if duration_ms is not None:
        log_context["duration_ms"] = duration_ms
    if prompt_name is not None:
        log_context["prompt_name"] = prompt_name
    if prompt_version is not None:
        log_context["prompt_version"] = prompt_version
    request_id = current_request_id()
    correlation_id = current_correlation_id()
    causation_id = current_causation_id()
    if request_id is not None:
        log_context["request_id"] = str(request_id)
    if correlation_id is not None:
        log_context["correlation_id"] = str(correlation_id)
    if causation_id is not None:
        log_context["causation_id"] = str(causation_id)
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
                # F5: when the call is ABOUT an existing row, the event is
                # anchored to it, so "what did we spend on this evidence
                # item?" is a direct query. Generation calls have no subject
                # yet and keep the generic ``llm_usage`` anchor.
                entity_type=subject_type or "llm_usage",
                entity_id=str(subject_id) if subject_id is not None else None,
                event_type="llm.usage",
                payload={
                    "model": model,
                    "task": task,
                    "prompt_tokens": usage["prompt_tokens"],
                    "completion_tokens": usage["completion_tokens"],
                    # Subset of completion_tokens — see extract_usage.
                    "reasoning_tokens": reasoning_tokens,
                    "cached_tokens": usage["cached_tokens"],
                    "total_tokens": usage["total_tokens"],
                    "outcome": outcome,
                    "duration_ms": duration_ms,
                    "prompt_name": prompt_name,
                    "prompt_version": prompt_version,
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
    images: list[bytes] | None = None,
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

    if images:
        # Multimodal user turn. Images follow the text so the instruction
        # is read first, and are sent as data URIs rather than links —
        # the source document is tenant data and must not be fetched from
        # a URL the provider resolves.
        import base64

        blocks: list[dict[str, Any]] = [{"type": "text", "text": user_prompt}]
        for image in images:
            encoded = base64.b64encode(image).decode("ascii")
            blocks.append(
                {
                    "type": "image_url",
                    "image_url": {"url": f"data:image/png;base64,{encoded}"},
                }
            )
        messages.append({"role": "user", "content": blocks})
        return messages

    messages.append({"role": "user", "content": user_prompt})
    return messages
