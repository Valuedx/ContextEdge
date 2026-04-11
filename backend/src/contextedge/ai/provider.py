"""LLM provider abstraction using LiteLLM for multi-provider support."""

import json
from typing import Any

import os
import litellm

from contextedge.config import settings

litellm.set_verbose = False

# Ensure Google API Key is set for LiteLLM
if settings.google_api_key:
    prefix = settings.google_api_key[:10]
    print(f"DEBUG: Using Google API Key starting with: {prefix}...")
    os.environ["GOOGLE_API_KEY"] = settings.google_api_key

# Support Vertex AI via service account
if settings.google_application_credentials:
    print(f"DEBUG: Using Vertex AI credentials: {settings.google_application_credentials}")
    os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = settings.google_application_credentials
    os.environ["VERTEX_LOCATION"] = settings.location
    # LiteLLM also looks for vertex_project but usually can derive from JSON key.
    # If it fails, we may need to parse the JSON for the project_id.
else:
    print("DEBUG: No Vertex AI (Google Application Credentials) found.")

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
    """Generate embedding vector for text. Returns a 3072-dimensional vector."""
    model = model or get_model_for_task("embedding")
    # LiteLLM maps 'dimensions' -> outputDimensionality for Vertex AI
    response = await litellm.aembedding(model=model, input=[text], dimensions=3072)
    embedding = response.data[0]["embedding"]
    if len(embedding) != 3072:
        raise ValueError(
            f"Embedding model '{model}' returned {len(embedding)} dimensions, "
            f"but 3072 are required. Use a model that supports 3072 dims "
            f"(e.g. vertex_ai/gemini-embedding-001 or text-embedding-3-large)."
        )
    return embedding


async def generate_embeddings_batch(texts: list[str], model: str | None = None) -> list[list[float]]:
    """Generate embeddings for a batch of texts. Returns 3072-dimensional vectors."""
    model = model or get_model_for_task("embedding")
    # LiteLLM maps 'dimensions' -> outputDimensionality for Vertex AI
    response = await litellm.aembedding(model=model, input=texts, dimensions=3072)
    embeddings = [item["embedding"] for item in response.data]
    if embeddings and len(embeddings[0]) != 3072:
        raise ValueError(
            f"Embedding model '{model}' returned {len(embeddings[0])} dimensions, "
            f"but 3072 are required. Use a model that supports 3072 dims "
            f"(e.g. vertex_ai/gemini-embedding-001 or text-embedding-3-large)."
        )
    return embeddings
