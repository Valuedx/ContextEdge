"""LLM provider abstraction using LiteLLM for multi-provider support."""

import json
from typing import Any

import litellm

from contextedge.config import settings

litellm.set_verbose = False

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
    max_tokens: int = 4096,
    response_format: dict | None = None,
) -> str:
    """Call LLM with the appropriate model for the task type."""
    model = model or get_model_for_task(task)
    kwargs: dict[str, Any] = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    if response_format:
        kwargs["response_format"] = response_format

    response = await litellm.acompletion(**kwargs)
    return response.choices[0].message.content or ""


async def llm_complete_json(
    prompt: str,
    task: str = "extraction",
    model: str | None = None,
    temperature: float = 0.0,
) -> dict | list:
    """Call LLM and parse JSON response."""
    result = await llm_complete(
        prompt,
        task=task,
        model=model,
        temperature=temperature,
        response_format={"type": "json_object"},
    )
    return json.loads(result)


async def generate_embedding(text: str, model: str | None = None) -> list[float]:
    """Generate embedding vector for text."""
    model = model or get_model_for_task("embedding")
    response = await litellm.aembedding(model=model, input=[text])
    return response.data[0]["embedding"]


async def generate_embeddings_batch(texts: list[str], model: str | None = None) -> list[list[float]]:
    """Generate embeddings for a batch of texts."""
    model = model or get_model_for_task("embedding")
    response = await litellm.aembedding(model=model, input=texts)
    return [item["embedding"] for item in response.data]
