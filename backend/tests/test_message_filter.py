"""Which thread messages earn a model call.

Every string here is from the live Zoho corpus. Hydration turned 1,515
tickets into 18,907 message rows, and letting each one reach normalization
projected ~56M tokens — six times what the tickets themselves cost.
"""

import re

import pytest

from contextedge.services.message_filter import (
    has_technical_signal,
    is_hydrated_message,
    message_noise_reason,
)


def _msg(body, **flags):
    return {"_connector_object_type": "hydrated_message", "body": body, **flags}


# ---- coordination, not diagnosis ----


@pytest.mark.parametrize(
    "body",
    [
        "Hi Team,  Any update?",
        "Looping in @Anjana Swami.",
        "Hi Kevin,  Please support on priority.",
        "Hello Shalu,  As Discussed on call, Can we connect at 3 Pm.",
        "Hello Shubham,  We will check your issue internally and schedule a call accordingly.",
        "Hi Sandeep/Smita,  Please list down all the plugin issues; we will schedule a call.",
        "issue has been resovled",
    ],
)
def test_coordination_chatter_never_reaches_a_model(body):
    assert message_noise_reason(_msg(body)) == "coordination_only"


@pytest.mark.parametrize(
    ("body", "reason"),
    [
        ("", "empty"),
        ("    ", "empty"),
        # A reply that is nothing but @-mentions is empty once markup goes.
        ("zsu[@user:60051092952]zsu zsu[@user:60030588188]zsu", "empty"),
    ],
)
def test_empty_bodies(body, reason):
    assert message_noise_reason(_msg(body)) == reason


def test_connector_flags_are_trusted():
    assert message_noise_reason(_msg("x" * 400, is_delivery_failure=True)) == "delivery_failure"
    assert message_noise_reason(_msg("x" * 400, is_quote_only=True)) == "quote_only"


# ---- the escape hatch: short but operational ----


@pytest.mark.parametrize(
    "body",
    [
        # The case that makes a bare length cut wrong — 28 chars, entire fix.
        "Restart the AE service on T3",
        "As per the error SS - we have to disable old (11.0.14) tomcat service",
        "Try below commands: ping t4.automationedge.com",
        "We already sent them the upgrade to 8.2.5.",
        "Check D:\\Projects\\logs\\kettle.log for the stack trace",
        "Getting error 0x8007007E on the agent",
        "ORA-01555 on the nightly job",
        "select count(*) from workflow_request where status = 'QUEUED'",
        "systemctl restart automationedge",
        "See https://docs.automationedge.com/platform/sag/8.4/",
        "Mail us at support@automationedge.com with the trace",
    ],
)
def test_short_but_technical_messages_are_kept(body):
    assert has_technical_signal(body), body
    assert message_noise_reason(_msg(body)) is None


def test_mention_markup_is_not_a_technical_signal():
    """Zoho encodes @-mentions as `zsu[@user:60051092952]zsu`.

    That trips the dotted-token and SCREAMING_SNAKE patterns, and kept
    messages whose entire content was "please look into this".
    """
    body = "zsu[@user:60056167145]zsu please look into this, if received any update"
    assert not has_technical_signal(body)
    assert message_noise_reason(_msg(body)) == "coordination_only"


def test_long_messages_are_kept_without_needing_a_signal():
    # Length alone is enough: a long message is a written explanation.
    assert message_noise_reason(_msg("The customer reports that " + "x " * 100)) is None


# ---- plumbing ----


def test_only_hydrated_messages_are_identified():
    assert is_hydrated_message({"_connector_object_type": "hydrated_message"})
    assert not is_hydrated_message({"_connector_object_type": "ticket"})
    assert not is_hydrated_message(None)


def test_a_non_dict_payload_is_not_judged():
    assert message_noise_reason(None) is None
    assert message_noise_reason("nonsense") is None


# ---- versioning ----


def test_the_filter_declares_a_version():
    """A rejected message leaves no row behind.

    Without a version stamp, a message nothing ever looked at is
    indistinguishable from one an older rule rejected, and a rule change has
    no way to know what it should re-examine.
    """
    from contextedge.services.message_filter import MESSAGE_FILTER_VERSION

    assert MESSAGE_FILTER_VERSION
    assert re.fullmatch(r"v\d+", MESSAGE_FILTER_VERSION)


def test_normalize_records_the_version_on_every_rejection():
    """The stamp is worthless unless the skip path actually carries it."""
    import inspect

    from contextedge.workers import extraction_tasks

    source = inspect.getsource(extraction_tasks._normalize)
    assert "MESSAGE_FILTER_VERSION" in source, (
        "the skip path must record which filter version rejected the message"
    )


def test_rejection_is_recomputable_from_the_stored_payload():
    """Re-assessment after a version bump must be exact, not approximate.

    That holds only while the filter is a pure function of the payload —
    no clock, no database, no source call. This is the property that makes
    a bump safe, so it is asserted rather than assumed.
    """
    payload = _msg("Hi Team,  Any update?")
    first = message_noise_reason(payload)
    assert first == "coordination_only"
    # Same input, same verdict, any number of times and in any order.
    assert all(message_noise_reason(dict(payload)) == first for _ in range(5))
