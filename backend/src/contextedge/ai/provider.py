"""LLM provider abstraction using LiteLLM for multi-provider support."""

import json
import os
import re
import time
import uuid as _uuid
from typing import Any

import litellm
import structlog

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

# Enable retries for transient errors (e.g., 503 Service Unavailable)
litellm.num_retries = 5

MODEL_ROUTING = {
    "classification": settings.default_classification_model,
    "extraction": settings.default_extraction_model,
    "embedding": settings.default_embedding_model,
}


def get_model_for_task(task: str) -> str:
    return MODEL_ROUTING.get(task, settings.default_extraction_model)


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
) -> str:
    """Call LLM with the appropriate model for the task type.

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
    """
    model = model or get_model_for_task(task)
    messages = build_messages(system_prompt, prompt, cache_system=bool(system_prompt))
    kwargs: dict[str, Any] = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    if response_format:
        kwargs["response_format"] = response_format

    start = time.perf_counter()
    outcome = "ok"
    response = None
    try:
        response = await litellm.acompletion(**kwargs)
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


async def llm_complete_json(
    prompt: str,
    task: str = "extraction",
    model: str | None = None,
    temperature: float = 0.0,
    *,
    system_prompt: str | None = None,
    tenant_id: _uuid.UUID | str | None = None,
    db: Any | None = None,
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


async def generate_embedding(
    text: str,
    model: str | None = None,
    *,
    tenant_id: _uuid.UUID | str | None = None,
    db: Any | None = None,
) -> list[float]:
    """Generate embedding vector for text. Returns a 3072-dimensional vector."""
    model = model or get_model_for_task("embedding")
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


async def generate_embeddings_batch(texts: list[str], model: str | None = None) -> list[list[float]]:
    """Generate embeddings for a batch of texts. Returns 3072-dimensional vectors."""
    model = model or get_model_for_task("embedding")
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
