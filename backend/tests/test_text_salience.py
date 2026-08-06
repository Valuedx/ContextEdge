"""A1 salient slicing: budget goes to substance, not to whatever leads.

The governing regression is roadmap F4: a fused email thread whose first
2,000 chars were scheduling chatter was classified not_relevant and a
complete resolution was discarded. These tests encode that failure shape
directly.
"""

from __future__ import annotations

from contextedge.ai.text_salience import salient_slice

# A miniature of the real F4 ticket: newest reply (pure chatter) on top,
# the technical substance buried below the fold.
FUSED_THREAD = "\n".join(
    [
        "Hello Vaibhav,",
        "",
        "Sure, we'll join the call at the scheduled time.",
        "",
        "Warm Regards,",
        "Snehal D | AutomationEdge",
        "",
        "From: Vaibhav C <redacted@example.com>",
        "Sent: Thursday, July 23, 2026 6:24 PM",
        "Subject: RE: workflow execution",
        "",
        "Hi, sounds good, let us plan for Thursday then. I will send the invite",
        "and we can discuss the plan for next quarter as well as the renewal.",
        "",
        "From: Snehal D",
        "Sent: Wednesday, July 22, 2026 2:11 PM",
        "",
        "After the browser upgrade the web drivers stopped matching and workflow",
        "execution failed with SessionNotCreatedException. Upload the",
        "corresponding web drivers through SysAdmin after browser upgrades and",
        "perform upgrades in a planned maintenance window before resuming",
        "workflow execution.",
    ]
)


def test_under_budget_passes_through_verbatim():
    text = "Short ticket body. Nothing to slice."
    assert salient_slice(text, 2000) == text


def test_substance_beats_leading_chatter():
    out = salient_slice(FUSED_THREAD, 220)
    assert "web drivers" in out
    assert "SessionNotCreatedException" in out
    assert "scheduled time" not in out


def test_budget_is_respected():
    assert len(salient_slice(FUSED_THREAD, 300)) <= 300
    assert len(salient_slice("x" * 10_000, 100)) <= 100


def test_selected_segments_keep_document_order():
    text = "\n\n".join(
        [
            "User reports the export job is failing intermittently.",
            "We met on Tuesday and agreed to sync again later in the week sometime.",
            "The job log shows error TimeoutException in the export service connection.",
        ]
    )
    out = salient_slice(text, 150)
    # Both technical segments survive; the report (head) precedes the log line.
    assert out.index("export job is failing") < out.index("TimeoutException")


def test_head_segment_is_always_represented():
    head = "Customer says the dashboard looks odd since yesterday morning."
    text = "\n\n".join([head, ("ERROR NullPointerException in render pipeline. " * 20).strip()])
    out = salient_slice(text, 400)
    assert out.startswith("Customer says the dashboard")


def test_all_boilerplate_falls_back_to_head_slice():
    text = "\n".join(["Hi team,", "Thanks!", "Warm Regards,", "-----"] * 200)
    out = salient_slice(text, 100)
    assert len(out) <= 100
    assert out  # never empty for non-empty input


def test_none_and_empty_are_safe():
    assert salient_slice(None, 100) == ""
    assert salient_slice("", 100) == ""
