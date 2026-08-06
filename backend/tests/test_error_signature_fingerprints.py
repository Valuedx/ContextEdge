"""D1 error-signature fingerprints: deterministic normalization.

The service's value proposition is exact-match joins across occurrences,
so the tests hammer one property above all: two occurrences of the same
failure with different instance data (ids, hosts, paths, sizes) MUST
produce the same signature_key, and different failures must not.
Precision over recall: conversational uses of "failed" must not mint
signatures — a junk signature poisons exact-match lookups.
"""

from __future__ import annotations

from contextedge.services.error_signature_service import (
    MAX_SIGNATURES_PER_EVIDENCE,
    extract_error_fingerprints,
    normalize_error_line,
    signature_key_for_error,
)

# --- normalization ----------------------------------------------------------


def test_instance_data_normalizes_away():
    a = normalize_error_line(
        "SSLHandshakeException on host 10.0.4.17:8443 for request "
        "c1f8a2e4-1234-4a5b-9c0d-aabbccddeeff after 30000 ms"
    )
    b = normalize_error_line(
        "SSLHandshakeException on host 192.168.1.9:9443 for request "
        "9e8d7c6b-4321-4f5e-8a0b-ffeeddccbbaa after 45000 ms"
    )
    assert a == b


def test_short_version_numbers_survive_normalization():
    # "TLS 1.2" is diagnostic identity, not instance data — stripping it
    # would merge distinct failures ("missing TLS 1.2" vs "missing TLS 1.3").
    out = normalize_error_line("handshake failed: server requires TLS 1.2")
    assert "1.2" in out


def test_paths_and_quoted_strings_strip():
    out = normalize_error_line(
        r"FileNotFoundError: 'C:\Agents\prod-7\drivers\chromedriver.exe' missing"
    )
    assert "chromedriver" not in out
    assert "<str>" in out or "<path>" in out


# --- extraction: what mints a signature and what must not -------------------


def test_same_failure_different_instances_share_a_key():
    text_a = "2026-08-01 ERROR SSLHandshakeException: no cipher suites in common with peer 10.0.0.1"
    text_b = "2026-08-06 ERROR SSLHandshakeException: no cipher suites in common with peer 172.16.0.9"
    (fp_a,) = extract_error_fingerprints(text_a)
    (fp_b,) = extract_error_fingerprints(text_b)
    assert fp_a["signature_key"] == fp_b["signature_key"]
    assert fp_a["error_type"] == "SSLHandshakeException"


def test_different_failures_get_different_keys():
    (fp_a,) = extract_error_fingerprints("ORA-01555 snapshot too old: rollback segment")
    (fp_b,) = extract_error_fingerprints("ORA-00060 deadlock detected while waiting for resource")
    assert fp_a["signature_key"] != fp_b["signature_key"]


def test_conversational_failure_language_is_not_an_error():
    text = (
        "Hi team, the approval process failed to move forward last week.\n"
        "We tried but were unable to schedule the call. It was an error on our side.\n"
        "Thanks and Regards"
    )
    assert extract_error_fingerprints(text) == []


def test_log_error_lines_are_fingerprinted():
    text = "[2026-08-07 01:02:03] ERROR com.ae.mailgw.QueueWriter: disk full, cannot persist message"
    (fp,) = extract_error_fingerprints(text)
    assert fp["error_type"] == "LOG_ERROR"
    assert "disk" in fp["normalized_message"].lower()


def test_http_5xx_and_vendor_codes_classify():
    (http_fp,) = extract_error_fingerprints("upstream returned HTTP 503 during checkout call")
    assert http_fp["error_type"] == "HTTP_503"
    (ora_fp,) = extract_error_fingerprints("job aborted: ORA-12154 TNS could not resolve service name")
    # The code number IS the identity — ORA-12154 must not merge with ORA-00060.
    assert ora_fp["error_type"] == "ORA_12154"


def test_signature_cap_per_evidence():
    lines = "\n".join(
        f"ERROR Widget{chr(65 + i)}Exception: broke uniquely in module {chr(65 + i)}x"
        for i in range(10)
    )
    fps = extract_error_fingerprints(lines)
    assert len(fps) == MAX_SIGNATURES_PER_EVIDENCE


def test_duplicate_lines_dedupe_to_one_fingerprint():
    line = "ERROR SSLHandshakeException: no cipher suites in common"
    fps = extract_error_fingerprints("\n".join([line] * 20))
    assert len(fps) == 1


def test_key_shape_is_stable_and_bounded():
    key = signature_key_for_error(
        "SSLHandshakeException", "no cipher suites in common with peer <host>"
    )
    assert key == "SSLHANDSHAKEEXCEPTION_NO_CIPHER_SUITES_COMMON_PEER"
    assert len(key) <= 120
