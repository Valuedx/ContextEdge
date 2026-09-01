"""Tests for tenant JSON loading — no product terms in defaults."""

from __future__ import annotations

from pathlib import Path

from contextedge.quality.seed_data import (
    clear_quality_data_cache,
    list_quality_profiles,
    load_ontology_payload,
    load_policy_pack_payload,
    load_quality_data,
)

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "quality"


class TestSeedData:
    def setup_method(self) -> None:
        clear_quality_data_cache()

    def test_default_templates_are_empty(self):
        pack = load_quality_data("default_policy_pack")
        ont = load_quality_data("default_ontology")
        assert pack.get("rules") == []
        assert ont.get("terms") == []

    def test_automationedge_profile_is_opt_in(self):
        assert "automationedge" in list_quality_profiles()
        pack = load_policy_pack_payload(profile="automationedge")
        ont = load_ontology_payload(profile="automationedge")
        assert len(pack.get("rules") or []) > 0
        assert len(ont.get("terms") or []) > 0
        # Defaults stay empty even when profile exists
        assert load_quality_data("default_policy_pack").get("rules") == []

    def test_custom_path_override(self):
        pack = load_policy_pack_payload(path=FIXTURES / "sample_policy_pack.json")
        assert pack["version"] == "test-pack-1.0.0"
        assert len(pack["rules"]) == 2
