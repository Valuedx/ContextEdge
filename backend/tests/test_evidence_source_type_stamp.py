"""`evidence_items.source_type` is stamped at construction, not as a
side effect of chunking.

The column was written in exactly one place: the chunking dispatch, which
sits behind the `not_relevant >= 0.75` extraction gate. Two things followed,
and neither announced itself.

Confidently-irrelevant evidence never got a source_type at all. Measured on
the first live ServiceNow ingest, 11 of 106 rows, and on the zoho_desk corpus,
3,805 of 10,547 -- 36%. The split was exact in both: every row that skipped
extraction was NULL, every row that did not was stamped. The 43 `not_relevant`
rows that *did* carry a source_type are the tell -- those scored below 0.75,
so they never skipped, so they reached the stamp. Any grouping or filter over
source silently omits that population, which is precisely the evidence a
reviewer auditing "what did this connector discard" wants to see.

The second consequence is larger. The message-function gate reads
`ev.source_type` about ninety lines ABOVE the only place that set it, so on a
freshly constructed row the value was always NULL and the gate was always
False. `classify_message_function` has one caller, behind that gate, so the
A1 classifier never ran on the ingest path -- confirmed against the live
corpus, where 0 of 10,547 rows carry a `message_function`. Its four consumers
(correction supersession, the dissociation veto, reply inheritance, and
telemetry-based outcome verification) have therefore all been reading NULL.

These are ordering tests because that is what the defect was: the write
existed, it just happened after both readers.
"""

from __future__ import annotations

import inspect

from contextedge.workers import extraction_tasks


def test_normalize_stamps_source_type_at_construction():
    """The regression this module exists for."""
    source = inspect.getsource(extraction_tasks._normalize)

    assert 'source_type=getattr(src, "source_type", None)' in source


def test_stamp_precedes_the_message_function_gate():
    """The gate reads the column; the column must already be written.

    This is the assertion that would have caught the dormant classifier:
    a stamp placed after its reader is indistinguishable from no stamp.
    """
    source = inspect.getsource(extraction_tasks._normalize)

    stamped_at = source.index('source_type=getattr(src, "source_type", None)')
    gate_at = source.index("MESSAGE_FUNCTION_SOURCE_TYPES")

    assert stamped_at < gate_at


def test_stamp_precedes_the_extraction_gate():
    """Irrelevant evidence skips everything after `skip_extraction`, so a
    stamp on that side of the branch never runs for it."""
    source = inspect.getsource(extraction_tasks._normalize)

    stamped_at = source.index('source_type=getattr(src, "source_type", None)')
    skip_at = source.index("skip_extraction = (")

    assert stamped_at < skip_at


def test_chunking_stamp_survives_as_a_backstop_only():
    """Rows normalized before the fix reach chunking with the column still
    NULL and would never match the chunker registry. The backstop stays --
    but it is documented as a backstop, so nobody restores it as the only
    writer and quietly reopens both bugs."""
    source = inspect.getsource(extraction_tasks._dispatch_chunking)

    assert "if not ev.source_type:" in source
    assert "backstop" in source.lower()
