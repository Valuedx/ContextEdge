"""Tests for the versioned prompt registry + per-tenant A/B routing
(W10-12.2)."""

from unittest.mock import patch
from uuid import uuid4

import pytest

from contextedge.ai import prompts as prompts_mod
from contextedge.ai.prompts import (
    Prompt,
    _invalidate_variants_cache,
    get_prompt,
    list_prompt_versions,
    register_prompt,
    resolve_version,
)


@pytest.fixture(autouse=True)
def _reset_registry():
    """Snapshot + restore the registry around each test so test-local
    registrations don't leak between tests or pollute real app state."""
    saved_registry = {k: dict(v) for k, v in prompts_mod._REGISTRY.items()}
    saved_defaults = dict(prompts_mod._DEFAULTS)
    _invalidate_variants_cache()
    yield
    prompts_mod._REGISTRY.clear()
    prompts_mod._REGISTRY.update(saved_registry)
    prompts_mod._DEFAULTS.clear()
    prompts_mod._DEFAULTS.update(saved_defaults)
    _invalidate_variants_cache()


def _p(name: str, version: str) -> Prompt:
    return Prompt(
        name=name, version=version,
        system=f"system-{name}-{version}",
        user_template=f"user-{name}-{version} {{x}}",
    )


def test_register_default_prompt_is_resolved_for_unknown_tenant():
    register_prompt(_p("testp", "v1"), default=True)
    assert resolve_version("testp") == "v1"
    assert get_prompt("testp").version == "v1"


def test_unknown_prompt_name_raises_keyerror():
    with pytest.raises(KeyError, match="no prompt registered"):
        resolve_version("not_a_real_prompt")


def test_registering_conflicting_default_raises():
    register_prompt(_p("conflict", "v1"), default=True)
    with pytest.raises(ValueError, match="already has default"):
        register_prompt(_p("conflict", "v2"), default=True)


def test_registering_same_default_twice_is_noop():
    """Idempotency: calling register_prompt with default=True on an
    already-registered default version shouldn't raise â€” the submodule
    may be imported multiple times across workers / tests."""
    p1 = _p("same", "v1")
    register_prompt(p1, default=True)
    register_prompt(p1, default=True)
    assert resolve_version("same") == "v1"


def test_list_versions_returns_sorted():
    register_prompt(_p("many", "v2"), default=True)
    register_prompt(_p("many", "v1"))
    register_prompt(_p("many", "v3"))
    assert list_prompt_versions("many") == ["v1", "v2", "v3"]


def test_per_tenant_override_beats_default():
    register_prompt(_p("t", "v1"), default=True)
    register_prompt(_p("t", "v2"))
    tenant_id = uuid4()
    variants = {str(tenant_id): {"t": "v2"}}

    with patch.object(
        prompts_mod.settings, "tenant_prompt_variants_json",
        __import__("json").dumps(variants),
    ):
        _invalidate_variants_cache()
        assert resolve_version("t", tenant_id) == "v2"
        assert resolve_version("t") == "v1"  # untenanted still default
        assert resolve_version("t", uuid4()) == "v1"  # other tenant still default


def test_override_to_unregistered_version_falls_back_to_default():
    """Typo in the variants config shouldn't crash ingest. Log +
    fall back."""
    register_prompt(_p("fallback", "v1"), default=True)
    tenant_id = uuid4()
    variants = {str(tenant_id): {"fallback": "v99-does-not-exist"}}

    with patch.object(
        prompts_mod.settings, "tenant_prompt_variants_json",
        __import__("json").dumps(variants),
    ):
        _invalidate_variants_cache()
        assert resolve_version("fallback", tenant_id) == "v1"


def test_malformed_variants_json_does_not_crash():
    """A bad JSON blob in config must not take down ingest â€” empty
    overrides on parse failure is the safe choice."""
    register_prompt(_p("robust", "v1"), default=True)
    with patch.object(
        prompts_mod.settings,
        "tenant_prompt_variants_json",
        "{this is not valid json",
    ):
        _invalidate_variants_cache()
        assert resolve_version("robust", uuid4()) == "v1"


def test_no_default_falls_back_to_last_registered():
    """If nobody set a default (bug), pick a version deterministically
    and log a warning rather than error."""
    register_prompt(_p("noprobt", "v1"))
    register_prompt(_p("noprobt", "v2"))
    # No default set.
    assert resolve_version("noprobt") == "v2"


def test_prompt_format_user_substitutes_variables():
    register_prompt(_p("fmt", "v1"), default=True)
    p = get_prompt("fmt")
    rendered = p.format_user(x="hello")
    assert "hello" in rendered


def test_real_relevance_prompt_registered():
    """Regression: the relevance prompt must register at import time
    or the normalize worker breaks on cold start."""
    # Importing the registry imports the relevance submodule which
    # registers v1 as default.
    assert "relevance" in prompts_mod._REGISTRY
    assert "v1" in prompts_mod._REGISTRY["relevance"]
    # v2 promoted 2026-08-07 (roadmap A2): same classification contract
    # plus the operational summary. A/B on live data: 6/6 normal tickets
    # kept their v1 labels; the 5 giant fused threads flipped
    # not_relevant -> operational at 0.90-0.95 (the F4 knowledge-loss fix).
    assert prompts_mod._DEFAULTS["relevance"] == "v2"


def test_all_migrated_prompt_families_registered():
    """Every inline prompt that was migrated must have a v1 default
    registered at import time. Regression guard â€” forgetting to import
    a submodule in ``ai/prompts/__init__.py`` silently breaks the
    caller via a KeyError at first LLM call."""
    # identity is v3 since the extraction eval harness could decide it:
    # 19 labelled cases at 3 samples each gave junk 9.5% -> 0%, forbidden
    # 23 -> 3, stability 0.96 -> 1.00, and MISSING 0 -> 0. That last one
    # settled it â€” the entity-count fall that looked like recall loss on
    # an unlabelled sample was the junk being removed.
    #
    # identity_adjudication moved to v2 with the trigram candidate
    # change. Candidates are now found by similarity rather than
    # substring, which raised the share of mentions reaching the
    # adjudicator from 33% to 52% â€” and started routinely showing it
    # numbered siblings like MAILGW01/MAILGW02 that are textually near
    # and genuinely different machines. v1 said nothing about those.
    # Raising recall into a judge without telling it what the new
    # near-misses look like trades a silent fork for a silent wrong link.
    expected_defaults = {
        "episode": "v3",
        "decision": "v2",
        "identity": "v3",
        "identity_adjudication": "v2",
        "pattern": "v2",
        # v3 adds approved KB/SOP as a distinct input, step-level source
        # citations, and the conflicts block; v5 the grounded/best-practice
        # taxonomy; v6 causal sequencing, a minimal step set, and plain
        # language (A/B 2026-08-19). Superseded versions remain registered
        # and immutable for eval baselines.
        "playbook": "v6",
        "contradiction": "v1",
    }
    for name, default_version in expected_defaults.items():
        assert name in prompts_mod._REGISTRY, f"{name!r} not registered"
        assert default_version in prompts_mod._REGISTRY[name]
        assert prompts_mod._DEFAULTS.get(name) == default_version


@pytest.mark.parametrize(
    ("module_path", "entry", "expected_name", "expected_version"),
    [
        ("contextedge.ai.extractors.identity_extractor", "extract_identities", "identity", "v3"),
        ("contextedge.ai.extractors.decision_extractor", "extract_decisions", "decision", "v2"),
    ],
)
def test_migrated_extractors_forward_prompt_identity(
    module_path, entry, expected_name, expected_version
):
    """Each migrated extractor must forward the resolved ``prompt_name``
    + ``prompt_version`` to ``llm_complete_json``, so the ``llm.usage``
    event can attribute cost / quality to a specific version.

    Parameterised over the two async extractors whose signatures take
    a single content string. Episode / pattern / playbook follow the
    same pattern but need list-of-dicts fixtures â€” same regression
    guarantee, just not covered by this parametrization."""
    import asyncio
    import importlib
    from unittest.mock import patch as _patch

    module = importlib.import_module(module_path)
    captured: dict = {}

    async def fake(*args, **kwargs):
        captured.update(kwargs)
        return {"entities": [], "decisions": []}

    with _patch.object(module, "llm_complete_json", fake):
        asyncio.run(getattr(module, entry)("some content for extraction here"))

    assert captured.get("prompt_name") == expected_name
    assert captured.get("prompt_version") == expected_version


def test_episode_extractor_forwards_prompt_identity():
    """Episode extractor has a chunking wrapper â€” verify the inner
    ``_extract_from_chunk`` still threads prompt_name/version."""
    import asyncio
    from unittest.mock import patch as _patch

    from contextedge.ai.extractors import episode_extractor

    captured: dict = {}

    async def fake(*args, **kwargs):
        captured.update(kwargs)
        return {"episodes": []}

    with _patch.object(episode_extractor, "llm_complete_json", fake):
        asyncio.run(episode_extractor.reconstruct_episode(
            [{"title": "x", "body": "y", "source_type": "ticket", "timestamp": "t"}]
        ))

    assert captured.get("prompt_name") == "episode"
    assert captured.get("prompt_version") == "v3"


def test_relevance_classifier_passes_prompt_version_to_llm():
    """End-to-end regression: ``classify_relevance`` should forward
    the resolved prompt_name / version down to ``llm_complete_json``
    so it lands in the llm.usage event."""
    from contextedge.ai.classifiers import relevance as relevance_mod

    captured: dict = {}

    async def fake(*args, **kwargs):
        captured.update(kwargs)
        return {"classification": "not_relevant", "confidence": 0.9, "reasoning": "t"}

    with patch.object(relevance_mod, "llm_complete_json", fake):
        import asyncio
        asyncio.run(relevance_mod.classify_relevance(
            "t", "b", "jira", "ticket", tenant_id=uuid4(),
        ))

    assert captured.get("prompt_name") == "relevance"
    assert captured.get("prompt_version") == "v2"
