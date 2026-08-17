"""What may become a canonical identity.

Every string in this file is taken from the live Zoho corpus, where 134
evidence items produced 605 canonical identities and identity work was 78%
of all model spend. The junk cases are things the graph actually contains;
the admitted cases are real systems sitting next to them, which is why the
gate cannot simply be a length limit.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest

from contextedge.services.identity_candidacy import (
    identity_rejection_reason,
    looks_like_a_name,
)
from contextedge.services.identity_normalizer import normalize_extracted_entity
from contextedge.services.identity_service import resolve_extracted_entities


def _entity(entity_type: str, name: str, **identifiers):
    return normalize_extracted_entity(
        {"entity_type": entity_type, "display_name": name, **identifiers}
    )


# ---- rule 2: names vs descriptions ----


@pytest.mark.parametrize(
    "name",
    [
        # Proper-noun signal.
        "Quartz",
        "Active Directory",
        "Single Sign-On",
        "AE Portal",
        # All-caps and alphanumeric identifiers.
        "MFA",
        "BROKER1",
        # Internal capitals, underscores, dots.
        "NewWorkflowQueue",
        "SystemResourceMonitor",
        "EBT_Card_Shadow_Credit",
        "support.automationedge.com",
        "WS-Federation",
        "vpn-gw-east-01",
    ],
)
def test_real_names_are_admitted(name):
    assert looks_like_a_name(name)


@pytest.mark.parametrize(
    "name",
    [
        # Lowercase common nouns — the largest junk class in the live table.
        "the project",
        "screenshot",
        "customer query",
        "query timeout",
        "server",
        "relay server",
        "backend",
        "file system",
        # Descriptions of a problem, not names of a thing.
        "Workflow Request Not Executing in Production issue",
        "server-side validation rules (restvalidation.json) for Email Configuration module",
        "AutomationEdge Process Studio connection test functionality",
        "Request ID 5360 for workflow CAMS_WBR9_DownloadRequest",
        # Not English — the rules must be structural, not a stop-word list.
        "Execution Metrics no Portal do AE não abre issue",
        # Quoted UI text and possessives.
        "Click 'View Documents' issue",
        "DocEdge: Azure OCR Configuration plugin's error handling",
        # Version strings wearing a noun.
        "client 8.2.5",
        "8.4.0 release",
        # A single character resolves against everything.
        "A",
        "",
        "   ",
    ],
)
def test_descriptions_are_rejected(name):
    assert not looks_like_a_name(name)


# ---- rule 1: types that are facets, not identities ----


@pytest.mark.parametrize(
    ("entity_type", "name"),
    [
        ("environment", "Production"),
        ("environment", "India"),
        ("version", "8.2.5"),
        ("patch", "8.5.0"),
        ("vendor", "Cognizant"),
    ],
)
def test_facet_types_never_become_identities(entity_type, name):
    assert identity_rejection_reason(_entity(entity_type, name)) == "facet_type"


def test_unknown_type_is_rejected():
    # normalize_extracted_entity defaults a missing type to "unknown"; an
    # unplaceable name is exactly what must not enter the resolution space.
    assert identity_rejection_reason(_entity("", "Something")) == "unsupported_type"


@pytest.mark.parametrize("entity_type", ["person", "device", "application", "service"])
def test_identity_bearing_types_pass_with_a_real_name(entity_type):
    assert identity_rejection_reason(_entity(entity_type, "ActiveMQ")) is None


def test_bad_name_rejected_even_for_a_good_type():
    assert identity_rejection_reason(_entity("service", "query timeout")) == "not_a_name"


# ---- the override: a strong identifier beats both rules ----


def test_strong_identifier_admits_a_facet_type():
    entity = _entity("vendor", "the vendor", email="ops@example.com")
    assert identity_rejection_reason(entity) is None


def test_strong_identifier_admits_an_unname_like_display_name():
    entity = _entity("service", "the mail relay", fqdn="mailgw01.corp.example.com")
    assert identity_rejection_reason(entity) is None


def test_an_invented_external_id_does_not_override_the_gate():
    """Both of these got through the first version of this gate.

    The extractor emits `source_identifiers` inferred from prose and the
    normalizer stores each as an `external_id` alias. On the live corpus that
    minted canonical identities for the versions "11.0.6" and "8.2.0", whose
    entire claim to being identities was an id keyed on "apache tomcat" and
    "onprem". An id is only as authoritative as whatever issued it.
    """
    for name, system in (("11.0.6", "apache tomcat"), ("8.2.0", "onprem")):
        entity = _entity("version", name, source_identifiers={system: name})
        assert identity_rejection_reason(entity) == "facet_type"


def test_a_connector_issued_external_id_still_matches_an_existing_identity():
    """The exclusion must not break real external ids.

    Layers 1-2 run before this gate, so an authoritative id still resolves to
    an identity that already exists. The gate only governs CREATION.
    """
    from contextedge.services.identity_candidacy import OVERRIDING_IDENTIFIERS

    assert "external_id" not in OVERRIDING_IDENTIFIERS
    assert {"email", "fqdn", "hostname"} <= OVERRIDING_IDENTIFIERS


# ---- integration: a rejected entity costs no adjudication call ----


@pytest.mark.asyncio
async def test_gated_entities_never_reach_the_adjudicator():
    """The point of the gate is the call it does not make.

    Without the gate every one of these reaches candidate generation and
    then the LLM adjudicator, which on the live corpus was 451 calls and
    663k tokens — 788 of every 867 output tokens spent reasoning.
    """
    db = SimpleNamespace(execute=AsyncMock(), add=lambda obj: None, flush=AsyncMock())

    extracted = [
        {"entity_type": "environment", "display_name": "Production"},
        {"entity_type": "version", "display_name": "8.2.5"},
        {"entity_type": "vendor", "display_name": "Cognizant"},
        {"entity_type": "service", "display_name": "query timeout"},
        {"entity_type": "service", "display_name": "the project"},
        {"entity_type": "person", "display_name": "A"},
    ]

    with (
        patch(
            "contextedge.services.identity_service._find_strong_identifier_match",
            new=AsyncMock(return_value=None),
        ),
        patch(
            "contextedge.services.identity_service._find_exact_alias_match",
            new=AsyncMock(return_value=None),
        ),
        patch(
            "contextedge.services.identity_service._candidate_identities",
            new=AsyncMock(return_value=[]),
        ) as candidates,
        patch(
            "contextedge.services.identity_service._adjudicate_candidates",
            new=AsyncMock(return_value=None),
        ) as adjudicate,
        patch(
            "contextedge.services.identity_service._create_identity",
            new=AsyncMock(),
        ) as create,
    ):
        resolved = await resolve_extracted_entities(db, uuid4(), extracted)

    assert resolved == []
    assert candidates.await_count == 0
    assert adjudicate.await_count == 0
    # Nor was a provisional row written for any of them.
    assert create.await_count == 0


@pytest.mark.asyncio
async def test_a_known_identity_still_matches_in_lower_case():
    """The gate must not stop the graph recognising what it already knows.

    Prose does not preserve casing: a ticket says "acme vpn" for the node
    stored as "Acme VPN". That string fails the shape rule — no capital,
    no identifier punctuation — so a gate placed above the deterministic
    layers would refuse to resolve an identity the graph already holds.
    It sits below them instead, and this is the case that proves it.
    """
    canonical = SimpleNamespace(
        id=uuid4(),
        canonical_name="Acme VPN",
        entity_type="service",
        resolution_state="resolved",
    )
    alias = SimpleNamespace(times_observed=3, last_seen_at=None)
    db = SimpleNamespace(execute=AsyncMock(), add=lambda obj: None, flush=AsyncMock())

    with (
        patch(
            "contextedge.services.identity_service._find_strong_identifier_match",
            new=AsyncMock(return_value=None),
        ),
        patch(
            "contextedge.services.identity_service._find_exact_alias_match",
            new=AsyncMock(return_value=(canonical, alias)),
        ),
        patch(
            "contextedge.services.identity_service._candidate_identities",
            new=AsyncMock(return_value=[]),
        ) as candidates,
    ):
        resolved = await resolve_extracted_entities(
            db, uuid4(), [{"entity_type": "service", "name": "acme vpn"}]
        )

    assert len(resolved) == 1
    assert resolved[0]["matched_via"] == "alias_exact"
    assert resolved[0]["canonical_name"] == "Acme VPN"
    # Still no adjudication: it matched deterministically.
    assert candidates.await_count == 0


@pytest.mark.asyncio
async def test_a_real_entity_still_flows_through():
    """The gate must not close on everything — regression guard for a
    rule tightened until the pipeline silently stops resolving."""
    db = SimpleNamespace(execute=AsyncMock(), add=lambda obj: None, flush=AsyncMock())

    with (
        patch(
            "contextedge.services.identity_service._find_strong_identifier_match",
            new=AsyncMock(return_value=None),
        ),
        patch(
            "contextedge.services.identity_service._find_exact_alias_match",
            new=AsyncMock(return_value=None),
        ),
        patch(
            "contextedge.services.identity_service._candidate_identities",
            new=AsyncMock(return_value=[]),
        ) as candidates,
        patch(
            "contextedge.services.identity_service._create_identity",
            new=AsyncMock(
                return_value=SimpleNamespace(
                    id=uuid4(),
                    canonical_name="ActiveMQ",
                    entity_type="service",
                    resolution_state="provisional",
                )
            ),
        ),
        patch(
            "contextedge.services.identity_service._record_resolution_decision",
            new=AsyncMock(),
        ),
    ):
        resolved = await resolve_extracted_entities(
            db, uuid4(), [{"entity_type": "service", "display_name": "ActiveMQ"}]
        )

    assert len(resolved) == 1
    assert candidates.await_count == 1
