"""Tests for the ingest-time PII / secret redaction pass (W5-6.3)."""

import pytest

from contextedge.services.redaction_service import (
    redact,
    redact_evidence_fields,
)


def test_email_is_redacted():
    out, counts = redact("contact me at alice@example.com please")
    assert "alice@example.com" not in out
    assert "[REDACTED:EMAIL]" in out
    assert counts == {"EMAIL": 1}


def test_multiple_emails_counted():
    out, counts = redact("cc a@a.com and b@b.io")
    assert "a@a.com" not in out
    assert "b@b.io" not in out
    assert counts == {"EMAIL": 2}


def test_phone_various_formats_redacted():
    samples = [
        "call me at (415) 555-1234",
        "reach me: 415-555-1234",
        "415.555.1234 is my cell",
        "+1 415 555 1234 works too",
    ]
    for sample in samples:
        out, counts = redact(sample)
        assert counts.get("PHONE", 0) >= 1, f"no phone match in: {sample!r} → {out!r}"
        assert "[REDACTED:PHONE]" in out


def test_ssn_is_redacted():
    out, counts = redact("SSN: 123-45-6789, please verify")
    assert "123-45-6789" not in out
    assert counts == {"SSN": 1}


def test_credit_card_like_is_redacted():
    out, counts = redact("card 4111 1111 1111 1111 on file")
    assert "4111" not in out
    assert counts.get("CREDIT_CARD", 0) >= 1


def test_aws_access_key_is_redacted():
    out, counts = redact("export AWS_ACCESS_KEY_ID=AKIAIOSFODNN7EXAMPLE")
    assert "AKIAIOSFODNN7EXAMPLE" not in out
    assert counts["AWS_ACCESS_KEY"] == 1


def test_private_key_block_is_redacted_across_newlines():
    block = (
        "debug log\n"
        "-----BEGIN RSA PRIVATE KEY-----\n"
        "MIIBOgIBAAJBALKKdHhQXo\n"
        "-----END RSA PRIVATE KEY-----\n"
        "trailing info"
    )
    out, counts = redact(block)
    assert "MIIBOgIBAAJBALKKdHhQXo" not in out
    assert "PRIVATE KEY-----" not in out
    assert counts["PRIVATE_KEY"] == 1
    # Surrounding narrative must be preserved.
    assert "debug log" in out
    assert "trailing info" in out


def test_mixed_content_counts_each_kind_separately():
    text = (
        "user alice@example.com called 415-555-1234 from credit card "
        "4111 1111 1111 1111 while SSH-ing with AKIAIOSFODNN7EXAMPLE"
    )
    out, counts = redact(text)
    assert "alice@example.com" not in out
    assert "415-555-1234" not in out
    assert "AKIAIOSFODNN7EXAMPLE" not in out
    assert counts.get("EMAIL") == 1
    assert counts.get("PHONE") == 1
    assert counts.get("AWS_ACCESS_KEY") == 1


def test_empty_and_none_input_are_passthrough():
    assert redact(None) == (None, {})
    assert redact("") == ("", {})


def test_clean_text_has_zero_counts():
    out, counts = redact("server restarted successfully, no issues")
    assert out == "server restarted successfully, no issues"
    assert counts == {}


def test_disabled_is_identity():
    text = "alice@example.com 415-555-1234"
    out, counts = redact(text, enabled=False)
    assert out == text
    assert counts == {}


def test_redact_evidence_fields_merges_counts():
    rt, rb, counts = redact_evidence_fields(
        "Ticket from alice@example.com",
        "body mentions bob@example.org and 415-555-1234",
    )
    assert "alice@example.com" not in rt
    assert "bob@example.org" not in rb
    assert "415-555-1234" not in rb
    assert counts["EMAIL"] == 2
    assert counts["PHONE"] == 1


def test_redact_evidence_fields_disabled_is_passthrough():
    rt, rb, counts = redact_evidence_fields(
        "x alice@example.com", "y bob@b.io", enabled=False,
    )
    assert rt == "x alice@example.com"
    assert rb == "y bob@b.io"
    assert counts == {}


def test_redact_idempotent():
    """Running redaction twice should produce the same output (the
    placeholder tokens themselves must not match any rule)."""
    text = "alice@example.com called 415-555-1234"
    once, _ = redact(text)
    twice, twice_counts = redact(once)
    assert twice == once
    assert twice_counts == {}
