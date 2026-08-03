"""LLM provider abstraction using LiteLLM for multi-provider support."""

import json
import os
import re
import time
import uuid as _uuid
from typing import Any

import litellm
import structlog
from pydantic import BaseModel, ValidationError

from contextedge.ai.observability import build_messages, record_llm_usage
from contextedge.config import settings

litellm.set_verbose = False
logger = structlog.get_logger()

# Ensure Google API Key is set for LiteLLM
if settings.google_api_key:
    os.environ["GOOGLE_API_KEY"] = settings.google_api_key
    logger.debug("google_api_key_configured")

# Support Vertex AI via service account
if settings.google_application_credentials:
    os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = settings.google_application_credentials
    os.environ["VERTEX_LOCATION"] = settings.location
    logger.debug(
        "vertex_ai_credentials_configured",
        path=settings.google_application_credentials,
    )
else:
    logger.debug("vertex_ai_credentials_not_found")

# Retries for transient errors (e.g., 503 Service Unavailable). Every retry is
# a fully billed call, so this number multiplies the worst-case cost of any
# request — it is a cost knob as much as a resilience one. Configurable via
# LLM_NUM_RETRIES; see config.py for why the default came down from 5.
litellm.num_retries = settings.llm_num_retries

MODEL_ROUTING = {
    "classification": settings.default_classification_model,
    "extraction": settings.default_extraction_model,
    "embedding": settings.default_embedding_model,
}


def get_model_for_task(task: str) -> str:
    return MODEL_ROUTING.get(task, settings.default_extraction_model)


def resolve_thinking_budget(prompt_name: str | None, model: str | None) -> int | None:
    """Configured thinking budget for a prompt, or ``None`` for dynamic.

    ``None`` means "send nothing" — the provider keeps deciding, which is
    the behaviour that shipped. A budget is only returned when the prompt
    is configured *and* the model actually supports reasoning: sending
    the parameter to a non-reasoning model is a 400, and the model can
    change under a prompt via routing or the fallback.

    Keyed on ``prompt_name`` rather than ``task`` because task is coarse
    — classification covers relevance, identity, and adjudication, and
    those have very different tolerances for cutting reasoning.
    """
    if not prompt_name:
        return None
    budgets = getattr(settings, "llm_thinking_budgets", None) or {}
    if prompt_name not in budgets:
        return None
    try:
        budget = int(budgets[prompt_name])
    except (TypeError, ValueError):
        return None
    if budget < 0:
        return None

    try:
        import litellm

        if not litellm.supports_reasoning(model=model or ""):
            return None
    except Exception:  # noqa: BLE001 - never fail a call over a capability probe
        return None
    return budget


async def llm_complete(
    prompt: str,
    task: str = "extraction",
    model: str | None = None,
    temperature: float = 0.1,
    max_tokens: int = 8192,
    response_format: dict | None = None,
    *,
    system_prompt: str | None = None,
    tenant_id: _uuid.UUID | str | None = None,
    db: Any | None = None,
    prompt_name: str | None = None,
    prompt_version: str | None = None,
    images: list[bytes] | None = None,
) -> str:
    """Call LLM with the appropriate model for the task type.

    ``images`` sends PNG bytes alongside the prompt as a multimodal turn.
    It is a parameter on this function rather than a separate vision
    client on purpose: budget enforcement, usage recording, the circuit
    breaker, the timeout, and the fallback model all live here, and
    vision calls are the *most* expensive per request — a parallel path
    would be the one kind of call that escaped the spend controls.

    New parameters vs. earlier signature:

    - ``system_prompt`` — when non-empty, emitted as a system message with
      ``cache_control: {"type": "ephemeral"}`` so Anthropic native caching
      kicks in and OpenAI's automatic prefix-cache has a stable block to
      hit. Callers that pass *only* ``prompt`` keep legacy behaviour.
    - ``tenant_id`` / ``db`` — optional instrumentation context. When
      passed, the call is recorded via ``record_llm_usage`` for per-tenant
      spend visibility. Background tasks that don't have a DB session
      handy can pass ``tenant_id`` alone; metrics still flow to Prometheus
      + structured logs, only the operational-event persist is skipped.
    - ``prompt_name`` / ``prompt_version`` — identifies the versioned
      prompt behind this call. Threaded into ``llm.usage`` events so the
      admin dashboard can break down cost + quality by prompt version.
      Callers using ``contextedge.ai.prompts.get_prompt`` get these for
      free; legacy callers pass ``None`` and events record ``null``.
    """
    model = model or get_model_for_task(task)

    # Per-tenant daily budget enforcement. When a tenant has a
    # ``tenant_llm_budgets`` row and the current-day usage has hit the
    # configured cap, raise ``TenantBudgetExceeded`` before spending
    # real tokens. A ``warn`` action logs + emits an event but allows
    # the call through (see services/tenant_budget_service).
    if tenant_id is not None and db is not None:
        try:
            tid = tenant_id if isinstance(tenant_id, _uuid.UUID) else _uuid.UUID(str(tenant_id))
            from contextedge.services.tenant_budget_service import (
                TenantBudgetExceeded,
                check_budget,
            )

            check = await check_budget(db, tid)
            if not check.allowed:
                if check.action == "block":
                    raise TenantBudgetExceeded(check)
                # warn: log, emit operational event, fall through.
                logger.warning(
                    "llm.budget_warning",
                    tenant_id=str(tid),
                    reason=check.reason,
                    current_tokens=check.current_tokens,
                    token_limit=check.token_limit,
                    current_cost_usd=check.current_cost_usd,
                    cost_cap_usd=check.cost_cap_usd,
                )
                try:
                    from contextedge.services.event_log_service import (
                        append_operational_event,
                    )

                    await append_operational_event(
                        db,
                        tenant_id=tid,
                        entity_type="tenant",
                        event_type="llm.budget_warning",
                        payload={
                            "reason": check.reason,
                            "current_tokens": check.current_tokens,
                            "token_limit": check.token_limit,
                            "current_cost_usd": check.current_cost_usd,
                            "cost_cap_usd": check.cost_cap_usd,
                            "model": model,
                            "task": task,
                        },
                    )
                except Exception as event_exc:
                    # Never let a failed audit write block the call.
                    logger.warning(
                        "llm.budget_warning_event_failed", error=str(event_exc),
                    )
        except ImportError:
            # The module is always importable at runtime; this except
            # branch exists so broken test mocks (no DB, no tenant
            # models) don't derail the main path.
            pass

    # Clamp generated tokens to the deployment ceiling. Callers pass whatever
    # their prompt might need; this is the backstop that stops one caller (or
    # one runaway prompt) from buying an 8k-token answer on every retry.
    effective_max_tokens = min(max_tokens, settings.llm_max_output_tokens)
    if effective_max_tokens < max_tokens:
        logger.debug(
            "llm.max_tokens_clamped",
            requested=max_tokens,
            allowed=effective_max_tokens,
            task=task,
        )

    messages = build_messages(
        system_prompt, prompt, cache_system=bool(system_prompt), images=images
    )
    kwargs: dict[str, Any] = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": effective_max_tokens,
    }
    if response_format:
        kwargs["response_format"] = response_format

    # NOTE: the thinking budget is resolved per ATTEMPT, not here. The
    # fallback model is a different model and may not support reasoning
    # at all; carrying a `thinking` kwarg computed for the primary model
    # into the fallback would turn the resilience path into a hard 400 —
    # a failure that only appears when the primary is already down.

    start = time.perf_counter()
    outcome = "ok"
    response = None

    # E1 resilience: bounded call + per-model circuit breaker + one
    # optional fallback attempt. Budget errors were raised above and
    # never touch the breaker.
    import asyncio as _asyncio

    from contextedge.ai.resilience import (
        LLM_CALL_TIMEOUT_SECONDS,
        LlmCircuitOpenError,
        breaker,
    )

    async def _attempt(attempt_model: str):
        breaker.check(attempt_model)
        attempt_kwargs = {**kwargs, "model": attempt_model}
        budget = resolve_thinking_budget(prompt_name, attempt_model)
        if budget is not None:
            # LiteLLM maps this onto the provider's native control
            # (Gemini thinking_config.thinking_budget, Anthropic
            # thinking). Sent only when this specific model supports
            # reasoning.
            attempt_kwargs["thinking"] = {
                "type": "enabled",
                "budget_tokens": budget,
            }
        try:
            result = await _asyncio.wait_for(
                litellm.acompletion(**attempt_kwargs),
                timeout=LLM_CALL_TIMEOUT_SECONDS,
            )
            breaker.record_success(attempt_model)
            return result
        except LlmCircuitOpenError:
            raise
        except Exception:
            breaker.record_failure(attempt_model)
            raise

    fallback_model = getattr(settings, "llm_fallback_model", None)
    try:
        try:
            response = await _attempt(model)
        except Exception as primary_exc:
            if fallback_model and fallback_model != model:
                logger.warning(
                    "llm.falling_back",
                    primary_model=model,
                    fallback_model=fallback_model,
                    error_type=type(primary_exc).__name__,
                )
                model = fallback_model  # usage records the serving model
                response = await _attempt(fallback_model)
            else:
                raise
        return response.choices[0].message.content or ""
    except Exception:
        outcome = "error"
        raise
    finally:
        duration_ms = int((time.perf_counter() - start) * 1000)
        # Record even on error — an errored call still consumed real tokens
        # on the provider side up to the failure point, and the outcome
        # counter gives us a first-class error-rate signal.
        try:
            await record_llm_usage(
                tenant_id=tenant_id,
                model=model,
                task=task,
                response=response,
                outcome=outcome,
                duration_ms=duration_ms,
                db=db,
                prompt_name=prompt_name,
                prompt_version=prompt_version,
            )
        except Exception as exc:
            logger.warning("llm.usage_record_failed", error=str(exc))


def repair_truncated_json(s: str) -> str:
    """Attempt to repair truncated JSON by closing open braces/brackets/quotes."""
    s = s.strip()
    if not s:
        return s

    # Remove trailing commas which frequently appear in truncated JSON
    s = re.sub(r',\s*$', '', s)

    stack = []
    is_in_string = False
    escaped = False

    for char in s:
        if is_in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                is_in_string = False
        else:
            if char == '"':
                is_in_string = True
            elif char == "{":
                stack.append("}")
            elif char == "[":
                stack.append("]")
            elif char == "}":
                if stack and stack[-1] == "}":
                    stack.pop()
            elif char == "]":
                if stack and stack[-1] == "]":
                    stack.pop()

    # Close unclosed string
    if is_in_string:
        s += '"'

    # Close unclosed objects/arrays in reverse order
    while stack:
        s += stack.pop()

    return s


def _salvage_truncated_entities_json(s: str) -> dict[str, list[dict]] | None:
    """Recover complete objects from a truncated ``{"entities": [...]}`` payload."""
    match = re.search(r'"entities"\s*:\s*\[', s)
    if not match:
        return None

    entities: list[dict] = []
    depth = 0
    start: int | None = None
    in_string = False
    escaped = False

    for idx in range(match.end(), len(s)):
        char = s[idx]
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue

        if char == '"':
            in_string = True
            continue
        if char == "{":
            if depth == 0:
                start = idx
            depth += 1
            continue
        if char != "}":
            continue

        if depth == 0:
            continue
        depth -= 1
        if depth == 0 and start is not None:
            try:
                entity = json.loads(s[start : idx + 1])
            except json.JSONDecodeError:
                start = None
                continue
            if isinstance(entity, dict):
                entities.append(entity)
            start = None

    return {"entities": entities} if entities else None


async def llm_complete_json(
    prompt: str,
    task: str = "extraction",
    model: str | None = None,
    temperature: float = 0.0,
    *,
    system_prompt: str | None = None,
    tenant_id: _uuid.UUID | str | None = None,
    db: Any | None = None,
    prompt_name: str | None = None,
    prompt_version: str | None = None,
) -> dict | list:
    """Call LLM and parse JSON response with robust repair for truncation.

    Accepts the same caching + instrumentation kwargs as ``llm_complete``.
    Callers are encouraged to split their prompts into a static
    ``system_prompt`` (instructions + schema) and a dynamic ``prompt``
    (the evidence to classify/extract). This enables Anthropic prompt
    caching and maximises OpenAI's automatic prefix-cache hit rate.
    """
    # Increase token limit for extraction tasks to avoid truncation
    max_tokens = 16384 if task == "extraction" else 8192

    result = await llm_complete(
        prompt,
        task=task,
        model=model,
        temperature=temperature,
        max_tokens=max_tokens,
        response_format={"type": "json_object"},
        system_prompt=system_prompt,
        tenant_id=tenant_id,
        db=db,
        prompt_name=prompt_name,
        prompt_version=prompt_version,
    )
    try:
        return json.loads(result)
    except json.JSONDecodeError:
        # Robust parsing: strip markdown blocks and find the first { / last }
        cleaned = result
        # Remove markdown wrappers
        cleaned = re.sub(r"```(?:json)?\s*", "", cleaned)
        cleaned = re.sub(r"```\s*", "", cleaned)

        # Locate the JSON content start
        start = cleaned.find("{")
        if start == -1:
            start = cleaned.find("[")

        if start != -1:
            # First try finding the last closing delimiter
            end = max(cleaned.rfind("}"), cleaned.rfind("]"))
            if end != -1 and end > start:
                candidate = cleaned[start : end + 1]
                try:
                    return json.loads(candidate)
                except json.JSONDecodeError:
                    pass

            # If that failed, it's likely truncated. Try to repair it.
            try:
                repaired = repair_truncated_json(cleaned[start:])
                return json.loads(repaired)
            except (json.JSONDecodeError, Exception) as exc2:
                salvaged = _salvage_truncated_entities_json(cleaned[start:])
                if salvaged is not None:
                    logger.warning(
                        "llm_json_salvaged_truncated_entities",
                        task=task,
                        model=model or get_model_for_task(task),
                        entity_count=len(salvaged["entities"]),
                    )
                    return salvaged
                logger.error(
                    "llm_json_repair_failed",
                    task=task,
                    model=model or get_model_for_task(task),
                    error=str(exc2),
                    snippet=cleaned[:500],
                )
                raise ValueError(f"LLM returned invalid JSON for task '{task}'") from exc2

        logger.error(
            "llm_json_parse_failed",
            task=task,
            model=model or get_model_for_task(task),
            raw=result[:500],
        )
        raise ValueError(f"LLM returned invalid JSON for task '{task}'")




def _format_validation_errors(err: ValidationError, limit: int = 5) -> str:
    """Render the first ``limit`` Pydantic errors as a bulleted list
    for inclusion in the repair prompt. Keep it short — too much
    error detail crowds the model's context with its own mistakes."""
    lines: list[str] = []
    for e in err.errors()[:limit]:
        loc = ".".join(str(p) for p in e.get("loc") or [])
        msg = e.get("msg", "")
        kind = e.get("type", "")
        lines.append(f"- `{loc}`: {msg} (type: {kind})")
    if len(err.errors()) > limit:
        lines.append(f"- ... {len(err.errors()) - limit} more")
    return "\n".join(lines)


def _build_repair_prompt(
    original_prompt: str,
    raw_output: Any,
    err: ValidationError | None,
    schema: type[BaseModel],
) -> str:
    """Build the retry prompt. Intentionally short — we want the model
    to re-read the schema + its previous mistake, not to re-digest the
    original evidence which it already has cached on the provider side."""
    try:
        schema_json = json.dumps(schema.model_json_schema(), indent=2)
    except Exception:
        schema_json = schema.__name__

    raw_snippet = json.dumps(raw_output, default=str)[:2000] if raw_output is not None else ""
    if err is None:
        error_detail = "Your previous response could not be parsed as JSON at all."
    else:
        error_detail = (
            "Your previous response parsed as JSON but did not match the "
            "required schema. Validation errors:\n" + _format_validation_errors(err)
        )

    return (
        f"Your previous response was rejected.\n\n"
        f"{error_detail}\n\n"
        f"Required schema (JSON Schema):\n```json\n{schema_json}\n```\n\n"
        f"Your previous (invalid) response:\n```json\n{raw_snippet}\n```\n\n"
        f"Original request:\n{original_prompt}\n\n"
        f"Return ONLY valid JSON conforming to the schema. No prose, no "
        f"markdown, no explanation — just the JSON."
    )


async def llm_complete_json_validated[T: BaseModel](
    prompt: str,
    schema: type[T],
    task: str = "extraction",
    model: str | None = None,
    temperature: float = 0.0,
    *,
    system_prompt: str | None = None,
    tenant_id: _uuid.UUID | str | None = None,
    db: Any | None = None,
    max_retries: int = 1,
    prompt_name: str | None = None,
    prompt_version: str | None = None,
) -> T:
    """Parse LLM output against a Pydantic schema, with a bounded repair retry.

    Drops into place for callers of ``llm_complete_json`` that want
    hard guarantees on the return shape. On the first failure (either
    the raw output isn't valid JSON, or it is but fails Pydantic
    validation) the wrapper builds a repair prompt including the raw
    response + the exact validation errors + the target JSON schema,
    and re-sends once at ``temperature=0``. Retries beyond the first
    are deliberately not supported — two LLM calls per extraction is
    already a real cost line; callers who still need more should
    upgrade the prompt, not tune the retry budget.

    Raises ``ValueError`` if parsing fails after the retry budget.
    """
    raw: Any = None
    first_err: ValidationError | None = None
    try:
        raw = await llm_complete_json(
            prompt,
            task=task,
            model=model,
            temperature=temperature,
            system_prompt=system_prompt,
            tenant_id=tenant_id,
            db=db,
            prompt_name=prompt_name,
            prompt_version=prompt_version,
        )
        return schema.model_validate(raw)
    except ValidationError as err:
        first_err = err
        logger.warning(
            "llm.schema_validation_failed",
            task=task,
            schema=schema.__name__,
            error_count=len(err.errors()),
            first_error=err.errors()[0] if err.errors() else None,
        )
        if max_retries <= 0:
            raise
    except ValueError:
        # llm_complete_json raises ValueError on un-parseable JSON.
        # Treat it the same as a validation failure — the repair prompt
        # just has no prior JSON to quote back.
        logger.warning("llm.json_parse_failed_retrying", task=task, schema=schema.__name__)
        if max_retries <= 0:
            raise

    repair = _build_repair_prompt(prompt, raw, first_err, schema)
    second = await llm_complete_json(
        repair,
        task=task,
        model=model,
        temperature=0.0,
        system_prompt=system_prompt,
        tenant_id=tenant_id,
        db=db,
        prompt_name=prompt_name,
        prompt_version=prompt_version,
    )
    try:
        return schema.model_validate(second)
    except ValidationError as err:
        logger.error(
            "llm.schema_validation_failed_after_retry",
            task=task,
            schema=schema.__name__,
            error_count=len(err.errors()),
        )
        raise ValueError(
            f"LLM output failed schema '{schema.__name__}' validation after retry"
        ) from err


async def generate_embedding(
    text: str,
    model: str | None = None,
    *,
    tenant_id: _uuid.UUID | str | None = None,
    db: Any | None = None,
) -> list[float]:
    """Generate embedding vector for text. Returns a 3072-dimensional vector.

    Enforces the per-tenant budget gate like ``llm_complete`` — embeddings
    are real spend, and the agent seed resolver triggers one per turn (also
    reachable in a loop via the model-invokable graph tool), so a blocked
    tenant must block here too.
    """
    model = model or get_model_for_task("embedding")

    if tenant_id is not None and db is not None:
        try:
            tid = tenant_id if isinstance(tenant_id, _uuid.UUID) else _uuid.UUID(str(tenant_id))
            from contextedge.services.tenant_budget_service import (
                TenantBudgetExceeded,
                check_budget,
            )

            check = await check_budget(db, tid)
            if not check.allowed and check.action == "block":
                raise TenantBudgetExceeded(check)
            if not check.allowed:
                logger.warning(
                    "llm.budget_warning",
                    tenant_id=str(tid),
                    reason=check.reason,
                    task="embedding",
                )
        except ImportError:
            pass  # mirrors llm_complete's broken-test-mock tolerance
    # LiteLLM maps 'dimensions' -> outputDimensionality for Vertex AI
    # gemini-embedding-001 supports up to 3072
    kwargs = {"model": model, "input": [text]}
    if "gemini-embedding" not in model:
        kwargs["dimensions"] = 3072

    start = time.perf_counter()
    outcome = "ok"
    response = None
    try:
        response = await litellm.aembedding(**kwargs)
        embedding = response.data[0]["embedding"]
        if len(embedding) != 3072:
            outcome = "error"
            raise ValueError(
                f"Embedding model '{model}' returned {len(embedding)} dimensions, "
                f"but 3072 are required. Use a model that supports 3072 dims "
                f"(e.g. vertex_ai/gemini-embedding-004 or text-embedding-3-large)."
            )
        return embedding
    except Exception:
        outcome = "error"
        raise
    finally:
        duration_ms = int((time.perf_counter() - start) * 1000)
        try:
            await record_llm_usage(
                tenant_id=tenant_id,
                model=model,
                task="embedding",
                response=response,
                outcome=outcome,
                duration_ms=duration_ms,
                db=db,
            )
        except Exception as exc:
            logger.warning("embedding.usage_record_failed", error=str(exc))


async def generate_embeddings_batch(
    texts: list[str], model: str | None = None
) -> list[list[float]]:
    """Generate embeddings for a batch of texts. Returns 3072-dimensional vectors.

    Requests are split into chunks of ``settings.embedding_max_batch_size``.
    Callers hand in a whole document's worth of chunks, so an uncapped call can
    be arbitrarily large — and a provider-side size rejection arrives *after*
    the tokens are spent. Splitting bounds the blast radius of any single
    request; the token cost of the work itself is unchanged.
    """
    model = model or get_model_for_task("embedding")

    limit = settings.embedding_max_batch_size
    if len(texts) > limit:
        out: list[list[float]] = []
        for start_idx in range(0, len(texts), limit):
            out.extend(await generate_embeddings_batch(texts[start_idx : start_idx + limit], model))
        return out
    # LiteLLM maps 'dimensions' -> outputDimensionality for Vertex AI
    # gemini-embedding-001 supports up to 3072
    kwargs = {"model": model, "input": texts}
    if "gemini-embedding" not in model:
        kwargs["dimensions"] = 3072

    start = time.perf_counter()
    outcome = "ok"
    response = None
    try:
        response = await litellm.aembedding(**kwargs)
        embeddings = [item["embedding"] for item in response.data]
        if embeddings and len(embeddings[0]) != 3072:
            outcome = "error"
            raise ValueError(
                f"Embedding model '{model}' returned {len(embeddings[0])} dimensions, "
                f"but 3072 are required. Use a model that supports 3072 dims "
                f"(e.g. vertex_ai/gemini-embedding-004 or text-embedding-3-large)."
            )
        return embeddings
    except Exception:
        outcome = "error"
        raise
    finally:
        duration_ms = int((time.perf_counter() - start) * 1000)
        try:
            # Batch usage accumulates across N inputs; recorded as a single
            # request with aggregated prompt_tokens to keep per-request
            # counters meaningful.
            await record_llm_usage(
                tenant_id=None,  # batch embedding callers don't carry tenant today
                model=model,
                task="embedding",
                response=response,
                outcome=outcome,
                duration_ms=duration_ms,
            )
        except Exception as exc:
            logger.warning("embedding_batch.usage_record_failed", error=str(exc))
