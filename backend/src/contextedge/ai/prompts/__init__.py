"""Versioned prompt registry (W10-12.2).

Before this module, prompts lived inline in each extractor / classifier
as bare string constants. That made three things impossible:

- **A/B test a prompt** against the previous version on a subset of
  tenants without redeploying or branching code.
- **Attribute LLM spend or quality regression** to a specific prompt
  version — ``llm.usage`` events carried only ``model`` + ``task``, not
  which revision of the prompt was active when the call ran.
- **Roll back a prompt independently of the code** — a bad prompt went
  out with the rest of the release.

The registry here is deliberately small: a ``Prompt`` dataclass pairs a
``name`` + ``version`` with its text, and ``get_prompt(name, tenant_id)``
returns the active version for a given tenant. Per-tenant overrides are
config-driven via ``settings.tenant_prompt_variants_json`` so operators
can flip a tenant to a variant without a migration.

``prompt_version`` is threaded into ``record_llm_usage`` so the admin
cost dashboard can break down cost and quality by version. Callers
that opt out (pass nothing) keep the old behaviour; legacy inline
prompts continue to work until they're migrated one at a time.
"""

from __future__ import annotations

import json
import uuid as _uuid
from dataclasses import dataclass

import structlog

from contextedge.config import settings

logger = structlog.get_logger()


@dataclass(frozen=True)
class Prompt:
    """A versioned prompt. ``system`` + ``user_template`` split so the
    system block is prompt-cacheable at the provider."""

    name: str
    version: str
    system: str
    user_template: str

    def format_user(self, **kwargs) -> str:
        return self.user_template.format(**kwargs)


# name → {version → Prompt}. Registered via ``register_prompt`` so the
# registry stays declarative and discoverable via ``list_prompt_versions``.
_REGISTRY: dict[str, dict[str, Prompt]] = {}

# name → default version string. The "baseline" for a given prompt name.
# Variant routing overrides this per tenant.
_DEFAULTS: dict[str, str] = {}


def register_prompt(prompt: Prompt, *, default: bool = False) -> None:
    """Register a prompt version. When ``default=True`` it becomes the
    version returned by ``get_prompt`` for tenants without a variant
    override. Registering two defaults for the same name raises — we
    want a single explicit default per prompt."""
    _REGISTRY.setdefault(prompt.name, {})[prompt.version] = prompt
    if default:
        existing = _DEFAULTS.get(prompt.name)
        if existing is not None and existing != prompt.version:
            raise ValueError(
                f"prompt '{prompt.name}' already has default version "
                f"{existing!r}; refusing to silently override with {prompt.version!r}",
            )
        _DEFAULTS[prompt.name] = prompt.version


def list_prompt_versions(name: str) -> list[str]:
    return sorted((_REGISTRY.get(name) or {}).keys())


# --- Per-tenant variant routing --------------------------------------------


_TENANT_VARIANTS_CACHE: dict[str, dict[str, str]] | None = None


def _load_tenant_variants() -> dict[str, dict[str, str]]:
    """Parse ``settings.tenant_prompt_variants_json`` once. Format:

    .. code-block:: json

        {"<tenant-uuid>": {"relevance": "v2", "episode": "v3"}}

    Returns an empty dict on malformed JSON so a bad config can never
    crash the ingest path — we log loudly instead."""
    global _TENANT_VARIANTS_CACHE
    if _TENANT_VARIANTS_CACHE is not None:
        return _TENANT_VARIANTS_CACHE
    raw = getattr(settings, "tenant_prompt_variants_json", "") or "{}"
    try:
        parsed = json.loads(raw)
        if not isinstance(parsed, dict):
            raise ValueError("tenant_prompt_variants_json must be an object")
        _TENANT_VARIANTS_CACHE = {
            str(k): {str(n): str(v) for n, v in (overrides or {}).items()}
            for k, overrides in parsed.items()
        }
    except (json.JSONDecodeError, ValueError) as exc:
        logger.error(
            "prompt_variants_config_invalid", error=str(exc),
        )
        _TENANT_VARIANTS_CACHE = {}
    return _TENANT_VARIANTS_CACHE


def _invalidate_variants_cache() -> None:
    """Drop the parsed variants cache. Exposed for tests and for the
    (not-yet-built) admin "reload config" endpoint."""
    global _TENANT_VARIANTS_CACHE
    _TENANT_VARIANTS_CACHE = None


def resolve_version(
    name: str,
    tenant_id: _uuid.UUID | str | None = None,
) -> str:
    """Return the active version string for ``name`` given ``tenant_id``.

    Precedence (most → least specific):
    1. ``tenant_prompt_variants_json[tenant_id][name]`` — per-tenant override.
    2. The default version registered via ``register_prompt(..., default=True)``.

    Unknown names raise ``KeyError`` — fail loud rather than silently
    fall back to a stale version.
    """
    if name not in _REGISTRY:
        raise KeyError(f"no prompt registered under name {name!r}")

    if tenant_id is not None:
        tid = str(tenant_id)
        variants = _load_tenant_variants().get(tid) or {}
        override = variants.get(name)
        if override is not None:
            if override in _REGISTRY[name]:
                return override
            logger.warning(
                "prompt_variant_not_registered_falling_back",
                name=name, requested=override, tenant_id=tid,
            )

    default = _DEFAULTS.get(name)
    if default is None:
        # No default set — take the alphabetically-last registered
        # version as a last resort. Keeps things moving but loudly.
        versions = list_prompt_versions(name)
        logger.warning(
            "prompt_has_no_default_using_latest",
            name=name, chosen=versions[-1] if versions else None,
        )
        return versions[-1]
    return default


def get_prompt(
    name: str,
    tenant_id: _uuid.UUID | str | None = None,
) -> Prompt:
    """Resolve + fetch the ``Prompt`` for the caller. The returned
    object carries the version string in ``.version`` — pass it to
    ``llm_complete*`` so the LLM-usage event records which version
    was active."""
    version = resolve_version(name, tenant_id)
    return _REGISTRY[name][version]


# Import submodules so their ``register_prompt`` calls execute at
# package import time. Adding a new prompt family = adding a new
# submodule here.
from contextedge.ai.prompts import (  # noqa: E402, F401
    contradiction,
    decision,
    episode,
    identity,
    message_function,
    pattern,
    playbook,
    relevance,
)
