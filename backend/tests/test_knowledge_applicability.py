"""Applicability: does this article apply to THIS environment?

Semantic similarity answers "same subject". It does not answer "same
system", and the gap between those is where a confidently wrong citation
comes from.

Nothing here may be product-specific. The vocabulary is derived per
tenant from the entity graph, so these tests deliberately use several
unrelated technology stacks.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from contextedge.services.knowledge_applicability_service import (
    APPLIES,
    MISMATCH,
    UNKNOWN,
    Applicability,
    applicability_from_payload,
    compare,
    extract_applicability_llm,
    describe_target,
    extract_applicability,
    extract_platform_versions,
    normalize_environment,
    parse_version_spec,
    tenant_environment_inventory,
    tenant_vocabulary,
    versions_compatible,
    versions_from_custom_fields,
)

NETWORK_VOCAB = {"globalprotect", "netscaler", "citrix vda", "vmware esxi"}
ITSM_VOCAB = {"activemq", "tomcat", "process studio", "rest plugin"}


# --- product agnosticism -----------------------------------------------------


def test_vocabulary_drives_components_not_a_hardcoded_product_list():
    """A hardcoded list serves exactly one customer and silently degrades
    for every other."""
    text = "GlobalProtect tunnel fails on the NetScaler gateway"
    assert extract_applicability(text, NETWORK_VOCAB).components == {
        "globalprotect",
        "netscaler",
    }
    # Same text, a tenant whose estate has none of those terms.
    assert extract_applicability(text, ITSM_VOCAB).components == set()


def test_two_unrelated_stacks_both_work():
    for vocab, article, incident, expected in [
        (NETWORK_VOCAB, "Citrix VDA registration failure",
         "NetScaler gateway certificate expiry", MISMATCH),
        (ITSM_VOCAB, "Monitor ActiveMQ through JConsole",
         "ActiveMQ broker down, queues stalled", APPLIES),
    ]:
        match = compare(
            extract_applicability(article, vocab),
            extract_applicability(incident, vocab),
        )
        assert match.verdict == expected


@pytest.mark.asyncio
async def test_vocabulary_comes_from_the_tenant_entity_graph():
    """Whatever the tenant actually runs, growing as their estate does —
    nothing to configure on day one."""
    db = SimpleNamespace(
        execute=AsyncMock(
            return_value=SimpleNamespace(
                scalars=lambda: SimpleNamespace(
                    all=lambda: ["VPN Gateway", "Citrix VDA", "db", "01", "  "]
                )
            )
        )
    )
    vocab = await tenant_vocabulary(db, "tenant")
    assert "vpn gateway" in vocab
    assert "citrix vda" in vocab
    # Short or numeric names identify one machine, not a component class.
    assert "db" not in vocab
    assert "01" not in vocab


@pytest.mark.asyncio
async def test_vocabulary_failure_is_not_a_gate():
    db = SimpleNamespace(execute=AsyncMock(side_effect=RuntimeError("no table")))
    assert await tenant_vocabulary(db, "tenant") == set()


# --- the rule that matters most ----------------------------------------------


def test_an_article_silent_about_version_is_not_penalised():
    """Two thirds of real articles state no version. Treating silence as
    inapplicability would discard most of a corpus, and unversioned
    usually means broadly applicable."""
    article = extract_applicability("Restart the broker service", ITSM_VOCAB)
    target = Applicability(components={"activemq"}, product_versions={"activemq": "7.5"})
    match = compare(article, target)
    assert match.rank_penalty == 1.0
    assert match.version_conflict is None


def test_a_mismatch_demotes_but_never_removes():
    """An article for an older release is often the only guidance that
    exists. Dropping it leaves the reviewer with nothing and no idea
    anything was withheld."""
    match = compare(
        extract_applicability("Citrix VDA registration", NETWORK_VOCAB),
        extract_applicability("NetScaler certificate expiry", NETWORK_VOCAB),
    )
    assert match.verdict == MISMATCH
    assert match.rank_penalty > 1.0
    assert match.rank_penalty < 10  # a penalty, not an exclusion


def test_every_demotion_carries_its_reason():
    """Demoting without saying why leaves a reviewer unable to tell a
    low-ranked article from an irrelevant one."""
    match = compare(
        extract_applicability("Citrix VDA registration", NETWORK_VOCAB),
        extract_applicability("NetScaler certificate expiry", NETWORK_VOCAB),
    )
    assert match.notes()
    assert "citrix vda" in match.notes()[0]
    assert "netscaler" in match.notes()[0]


# --- versions ----------------------------------------------------------------


def test_versions_are_compared_per_product_not_globally():
    """An article naming Tomcat 9 is not in conflict with an environment
    on platform 7 — they are versions of different things."""
    article = extract_applicability("Requires Tomcat 9.0.65", ITSM_VOCAB)
    target = extract_applicability("Platform 7.5.1 in production", ITSM_VOCAB)
    assert compare(article, target).version_conflict is None


def test_same_product_different_major_conflicts():
    article = extract_applicability("GlobalProtect 6.2 client setup", NETWORK_VOCAB)
    target = extract_applicability("GlobalProtect 5.1 in production", NETWORK_VOCAB)
    match = compare(article, target)
    assert match.verdict == MISMATCH
    assert match.version_conflict == ("6.2", "5.1")


def test_same_product_same_major_does_not_conflict():
    article = extract_applicability("Tomcat 9.0.65 tuning", ITSM_VOCAB)
    target = extract_applicability("Tomcat 9.0.71 running", ITSM_VOCAB)
    assert compare(article, target).version_conflict is None


def test_version_ahead_of_the_environment_is_the_damaging_direction():
    """An article documenting a release the environment has not reached
    names menu paths and endpoints that do not exist there yet."""
    match = compare(
        extract_applicability("Applies to Jira 9.12 and later", {"jira"}),
        extract_applicability("Jira 9.4 in production", {"jira"}),
    )
    assert match.verdict == MISMATCH
    assert match.version_ahead_of_environment is True
    assert "does not have yet" in " ".join(match.notes())


def test_an_older_article_within_the_same_major_still_applies():
    """Most guidance survives a minor upgrade, and the older article is
    frequently the only one that exists."""
    match = compare(
        extract_applicability("Jira 9.4 reindex procedure", {"jira"}),
        extract_applicability("Jira 9.12 in QA", {"jira"}),
    )
    assert match.version_conflict is None
    assert match.rank_penalty == 1.0


def test_a_whole_major_behind_is_flagged_but_flagged_lightly():
    match = compare(
        extract_applicability("Jira 8.0 reindex procedure", {"jira"}),
        extract_applicability("Jira 9.12 in QA", {"jira"}),
    )
    assert match.version_conflict == ("8.0", "9.12")
    assert match.version_ahead_of_environment is False
    # Lighter than the article-ahead direction.
    assert match.rank_penalty < 1.35


def test_minor_versions_are_compared_numerically_not_as_text():
    """9.12 is later than 9.4. String or float comparison says the
    opposite, and a major-only comparison sees no difference at all —
    which is the exact drift that separates one environment from
    another."""
    assert compare(
        extract_applicability("Jira 9.12 feature", {"jira"}),
        extract_applicability("Jira 9.4 running", {"jira"}),
    ).version_ahead_of_environment is True
    assert compare(
        extract_applicability("Jira 9.4 feature", {"jira"}),
        extract_applicability("Jira 9.12 running", {"jira"}),
    ).version_ahead_of_environment is False


# --- deployment model: cloud vs on-premise ------------------------------------


def test_cloud_and_onprem_are_a_hard_mismatch():
    """Not a dated article — an unperformable one. Cloud has no config
    file to edit and no service to restart, so no version of a cloud
    article applies to a self-hosted estate."""
    match = compare(
        extract_applicability(
            "Jira Cloud: configure this from the site admin console", {"jira"}
        ),
        extract_applicability("Jira on-premise data center install", {"jira"}),
    )
    assert match.verdict == MISMATCH
    assert match.deployment_conflict == ("cloud", "onprem")
    # Heavier than any version gap, because version cannot fix it.
    assert match.rank_penalty >= 1.5
    assert "may not exist" in " ".join(match.notes())


def test_matching_deployment_models_do_not_conflict():
    match = compare(
        extract_applicability("Jira Cloud automation rules", {"jira"}),
        extract_applicability("our Jira cloud site", {"jira"}),
    )
    assert match.deployment_conflict is None
    assert match.verdict == APPLIES


def test_an_article_comparing_both_models_is_never_demoted():
    """"Unlike Cloud, the server edition stores attachments on disk" is
    an on-premise article. Requiring both sides to be scoped to a single
    model is what makes a 1.5x penalty safe on a lexical signal."""
    match = compare(
        extract_applicability(
            "Unlike Jira Cloud, the on-premise server edition writes to disk",
            {"jira"},
        ),
        extract_applicability("Jira on-premises attachment storage full", {"jira"}),
    )
    assert match.deployment_conflict is None
    assert match.rank_penalty == 1.0


def test_bare_server_does_not_mark_a_document_onprem():
    """"The server rebooted" appears in most infrastructure writing;
    treating it as the Server *edition* would mark a whole corpus."""
    assert extract_applicability("the server rebooted overnight").deployments == set()


# --- environment tiers --------------------------------------------------------


def test_the_same_article_gets_different_verdicts_per_environment():
    """The point of the environment facet. One tenant, one article, two
    environments on different releases — and only one of them applies."""
    inventory = {"production": {"jira": "9.4"}, "qa": {"jira": "9.12"}}
    article = extract_applicability("Applies to Jira 9.12 and later", {"jira"})

    def verdict(tier):
        target = describe_target(
            pattern_title="Jira reindex fails",
            custom_fields={"cf_environment": tier},
            environment_inventory=inventory,
            vocabulary={"jira"},
        )
        return compare(article, target)

    assert verdict("Prod").verdict == MISMATCH
    assert verdict("UAT").verdict == APPLIES


def test_environment_labels_are_normalised_to_tiers():
    for label, tier in [
        ("PRD", "production"), ("Prod", "production"), ("live", "production"),
        ("UAT", "qa"), ("SIT", "qa"), ("Test", "qa"),
        ("pre-prod", "staging"), ("STG", "staging"),
        ("Sandbox", "development"), ("DEV", "development"),
    ]:
        assert normalize_environment(label) == tier


def test_a_qualifier_that_inverts_the_meaning_wins():
    """"Pre-Prod" contains "prod". Matching the substring would file the
    staging estate's incidents against production's release."""
    assert normalize_environment("Pre-Prod EU") == "staging"
    assert normalize_environment("preprod") == "staging"


def test_negated_labels_name_no_tier_rather_than_the_opposite_one():
    """"Non-Prod" says which environment this is NOT. Reading the "prod"
    inside it is exactly inverted, and guessing a tier would pull the
    version from the wrong environment."""
    assert normalize_environment("Non-Prod") is None
    assert normalize_environment("non production") is None


def test_instance_numbers_are_not_tiers():
    assert normalize_environment("QA2") == "qa"
    assert normalize_environment("DEV-01") == "development"
    assert normalize_environment("prod-eu-west") == "production"


def test_an_unknown_environment_label_is_not_guessed():
    """An unmapped label compared against a tier would manufacture
    conflicts out of a naming convention nobody told us about."""
    assert normalize_environment("Ring 4") is None
    assert normalize_environment("") is None
    assert normalize_environment(None) is None


def test_environment_alone_barely_demotes():
    """Most articles name an environment incidentally rather than being
    scoped to it."""
    match = compare(
        extract_applicability("in production you would also drain the node"),
        extract_applicability("failure seen in the QA environment"),
    )
    assert match.environment_conflict is not None
    assert match.rank_penalty < 1.2


def test_prod_does_not_match_inside_the_word_product():
    assert extract_applicability("the product documentation").environments == set()


def test_a_ticket_environment_field_outranks_prose():
    target = describe_target(
        pattern_title="reindex fails in the dev environment sandbox",
        custom_fields={"cf_environment": "Production"},
        vocabulary={"jira"},
    )
    assert target.environments == {"production"}


def test_ci_environment_is_used_when_no_field_exists():
    target = describe_target(
        pattern_title="Jira reindex fails",
        ci_traits={"environment": "PRD"},
        vocabulary={"jira"},
    )
    assert target.environments == {"production"}


@pytest.mark.asyncio
async def test_inventory_comes_from_the_entity_graph():
    """`Entity.environment` is already populated by the CMDB and ticket
    connectors, so the estate map needs no maintenance."""
    db = SimpleNamespace(
        execute=AsyncMock(
            return_value=SimpleNamespace(
                all=lambda: [
                    ("Jira", "Production", None, {"version": "9.4"}),
                    ("Jira", "UAT", None, {"version": "9.12"}),
                    ("Confluence", "prod", "8.5", None),
                    ("Ring 4 host", "Ring 4", None, {"version": "1.0"}),
                    ("Unversioned", "prod", None, None),
                ]
            )
        )
    )
    inventory = await tenant_environment_inventory(db, "tenant")
    assert inventory["production"] == {"jira": "9.4", "confluence": "8.5"}
    assert inventory["qa"] == {"jira": "9.12"}
    assert "Ring 4" not in inventory  # unmapped tier, not guessed


@pytest.mark.asyncio
async def test_inventory_failure_is_not_a_gate():
    db = SimpleNamespace(execute=AsyncMock(side_effect=RuntimeError("no column")))
    assert await tenant_environment_inventory(db, "tenant") == {}


def test_a_ticket_version_field_outranks_the_inventory_for_its_own_product():
    """The inventory says what usually runs there; the field says what
    this ticket is about — but only once someone has said which product
    the field describes."""
    target = describe_target(
        pattern_title="Jira reindex fails",
        custom_fields={"cf_environment": "Prod", "cf_product_version": "9.12"},
        environment_inventory={"production": {"jira": "9.4"}},
        version_product="Jira",
        vocabulary={"jira"},
    )
    assert target.product_versions["jira"] == "9.12"


def test_a_platform_version_is_not_applied_to_bundled_components():
    """Bundled components version independently. A ticket stamped 7.5.1
    was compared against an article about ActiveMQ 5.15.8 and reported a
    version conflict — 5.15.8 being simply ActiveMQ's own numbering
    inside a 7.5.1 platform. A confident, entirely fabricated warning,
    found on live data.
    """
    target = describe_target(
        pattern_title="ActiveMQ broker down, queue not draining",
        custom_fields={"cf_product_version": "7.5.1"},
        vocabulary={"activemq"},
    )
    assert "activemq" in target.components
    assert "activemq" not in target.product_versions

    article = Applicability(
        components={"activemq"}, product_versions={"activemq": "5.15.8"}
    )
    match = compare(article, target)
    assert match.version_conflict is None
    assert match.verdict == APPLIES  # same component, no invented conflict


@pytest.mark.parametrize(
    "text",
    [
        "upgraded to 7.5.1 last night",
        "see step 3.1 on page 2.4",
        "error code 5.2 returned",
    ],
)
def test_prose_noise_does_not_become_a_product(text):
    """Without this, "upgraded to 7.5.1" yields the product "upgraded"."""
    assert extract_applicability(text).product_versions == {}


# --- structured version fields -----------------------------------------------


def test_version_comes_from_a_custom_field_by_name_shape():
    """The slug is per-portal, so the match is on the name's shape."""
    assert versions_from_custom_fields({"cf_product_version": "7.5.1"}) == {"7.5.1"}
    assert versions_from_custom_fields({"cf_app_build": "v8.0"}) == {"8.0"}
    assert versions_from_custom_fields({"cf_site": "Pune DC"}) == set()
    assert versions_from_custom_fields(None) == set()
    assert versions_from_custom_fields(
        {"cf_automation_egde_version_1": "7*"}
    ) == {"7*"}
    assert versions_from_custom_fields({"version": "8.x"}) == {"8.x"}
    assert versions_from_custom_fields({"version": "8.*"}) == {"8.*"}


def test_an_explicitly_configured_field_wins():
    fields = {"cf_unguessable_slug": "7.2", "cf_version": "9.9"}
    assert versions_from_custom_fields(fields, "cf_unguessable_slug") == {"7.2"}


def test_the_structured_field_overrides_prose():
    """A stack trace mentioning 7.4.1 must not outvote the field where
    someone recorded that this ticket is about 7.5.1."""
    target = describe_target(
        pattern_title="Broker failure",
        pattern_description="stack trace shows GlobalProtect 6.2 in the frames",
        custom_fields={"cf_product_version": "7.5.1"},
        vocabulary=NETWORK_VOCAB,
    )
    assert target.versions == {"7.5.1"}


def test_ci_traits_contribute_platform_facts():
    target = describe_target(
        pattern_title="Broker down",
        ci_traits={"os": "Windows Server 2019"},
        vocabulary=ITSM_VOCAB,
    )
    assert "windows" in target.platforms


# --- stated ranges ------------------------------------------------------------
#
# The failure a lexical extractor could not be fixed out of: "applies to
# 8.0 and later" is a range. Read as a point version it becomes "written
# for 8.0", a major behind a 9.12 environment, and the feature emits a
# version-conflict warning about an article that explicitly covers the
# reader's release.


def test_a_floor_includes_every_later_release():
    article = applicability_from_payload(
        {"components": ["jira"], "version_floor": {"jira": "8.0"}}
    )
    target = Applicability(components={"jira"}, product_versions={"jira": "9.12"})
    match = compare(article, target)
    assert match.version_conflict is None
    assert match.rank_penalty == 1.0


def test_a_floor_still_excludes_earlier_releases():
    article = applicability_from_payload(
        {"components": ["jira"], "version_floor": {"jira": "9.12"}}
    )
    target = Applicability(components={"jira"}, product_versions={"jira": "9.4"})
    match = compare(article, target)
    assert match.verdict == MISMATCH
    assert match.version_ahead_of_environment is True


def test_a_ceiling_excludes_later_releases():
    article = applicability_from_payload(
        {"components": ["jira"], "version_ceiling": {"jira": "8.9"}}
    )
    target = Applicability(components={"jira"}, product_versions={"jira": "9.12"})
    assert compare(article, target).verdict == MISMATCH


def test_a_stated_range_wins_over_the_point_rule():
    """Both present: the range is what the author actually said."""
    article = applicability_from_payload(
        {
            "components": ["jira"],
            "product_versions": {"jira": "8.0"},
            "version_floor": {"jira": "8.0"},
        }
    )
    target = Applicability(components={"jira"}, product_versions={"jira": "9.12"})
    assert compare(article, target).version_conflict is None


# --- extraction payloads ------------------------------------------------------


def test_a_payload_round_trips():
    original = applicability_from_payload(
        {
            "components": ["jira"],
            "deployment": "cloud",
            "environments": ["production"],
            "product_versions": {"jira": "9.4"},
            "version_floor": {"jira": "9.0"},
        }
    )
    restored = applicability_from_payload(original.to_payload())
    assert restored.components == {"jira"}
    assert restored.deployments == {"cloud"}
    assert restored.environments == {"production"}
    assert restored.product_versions == {"jira": "9.4"}
    assert restored.version_floor == {"jira": "9.0"}


def test_both_and_unknown_deployment_are_no_constraint():
    """Storing "both" as {cloud, onprem} would overlap everything — the
    same outcome by a more confusing route."""
    for value in ("both", "unknown", None, "nonsense"):
        assert applicability_from_payload({"deployment": value}).deployments == set()


def test_a_hallucinated_tier_or_platform_is_dropped():
    """The model is told to use a fixed vocabulary. Anything outside it
    would compare against nothing and quietly never match."""
    payload = {"environments": ["prod-eu", "production"], "platforms": ["banana"]}
    facets = applicability_from_payload(payload)
    assert facets.environments == {"production"}
    assert facets.platforms == set()


def test_junk_versions_in_a_payload_are_dropped():
    facets = applicability_from_payload(
        {"product_versions": {"jira": "latest", "tomcat": "9.0.65"}}
    )
    assert facets.product_versions == {"tomcat": "9.0.65"}


@pytest.mark.asyncio
async def test_extraction_failure_returns_none_so_ingest_continues():
    """An article that fails extraction must still be ingested, chunked
    and retrievable — it just ranks without applicability."""
    with patch(
        "contextedge.ai.provider.llm_complete_json",
        AsyncMock(side_effect=RuntimeError("provider down")),
    ):
        assert await extract_applicability_llm("t", "b") is None


@pytest.mark.asyncio
async def test_a_non_dict_response_is_rejected():
    with patch(
        "contextedge.ai.provider.llm_complete_json", AsyncMock(return_value=["nope"])
    ):
        assert await extract_applicability_llm("t", "b") is None


def test_zoho_major_wildcards_parse_and_match_the_same_major():
    assert parse_version_spec("7*").major == 7
    assert parse_version_spec("7*").wildcard is True
    assert parse_version_spec("8.x").wildcard is True
    assert parse_version_spec("V8.*").raw == "8.*"
    assert versions_compatible("7.6.3", "7*") is True
    assert versions_compatible("8.2.3", "7*") is False
    assert versions_compatible("8.x", "8.2.4") is True
    assert versions_compatible("8.4.0", "8.2.3") is False


def test_ticket_ae_version_is_compared_against_kb_ae_version():
    """Ticket field is ``_platform``; KB articles often name ``ae``."""
    ticket = describe_target(custom_fields={"version": "7*"})
    article = Applicability(product_versions={"ae": "7.6.3"})
    assert compare(article, ticket).version_conflict is None
    mismatch = compare(Applicability(product_versions={"ae": "8.2.3"}), ticket)
    assert mismatch.verdict == MISMATCH
    assert mismatch.version_conflict == ("8.2.3", "7*")


def test_affected_version_heading_becomes_platform_version():
    found = extract_platform_versions(
        "Unable to Sync Web GUI Plugin in Version 7.x",
        "### Affected Version\n7.x\n### Resolution Steps\nRestart the agent.",
    )
    assert found["_platform"] in {"7.x", "7*"}
    roundtrip = applicability_from_payload(
        {"product_versions": {"_platform": "8*"}, "components": []}
    )
    assert roundtrip.product_versions["_platform"] == "8*"


# --- safe degradation --------------------------------------------------------


def test_no_vocabulary_degrades_to_todays_behaviour():
    """Day one, before the entity graph has anything in it."""
    match = compare(
        extract_applicability("Some article"), extract_applicability("Some incident")
    )
    assert match.verdict == UNKNOWN
    assert match.rank_penalty == 1.0


def test_empty_inputs_are_handled():
    assert extract_applicability(None).is_silent()
    assert extract_applicability("").is_silent()
    assert compare(Applicability(), Applicability()).verdict == UNKNOWN
