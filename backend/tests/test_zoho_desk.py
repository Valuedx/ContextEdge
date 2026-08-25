"""Zoho Desk connector, HTML conversion, and reference enrichment.

Fixtures mirror payload shapes captured from a live instance
(``desk.zoho.in``, org 60001911841) — including the two findings that
contradict the obvious implementation: ``limit`` caps at 50, and records
sharing a ``modifiedTime`` arrive id-ASCENDING inside the
time-DESCENDING sequence.
"""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import httpx
import pytest

import contextedge.connectors.zoho_desk.connector as connector_module
from contextedge.connectors.base import Checkpoint, DateRange, HydratedThread
from contextedge.connectors.zoho_desk.connector import (
    PAGE_SIZE,
    ZohoDeskConnector,
    parse_zoho_datetime,
)
from contextedge.connectors.zoho_desk.html_text import html_to_text
from contextedge.services.zoho_desk_reference_service import (
    extract_entity_references,
    extract_tag_topics,
    extract_ticket_references,
    process_zoho_desk_references,
)

CREDENTIALS = {
    "client_id": "cid",
    "client_secret": "secret",
    "refresh_token": "rt",
    "org_id": "60001911841",
    "data_center": "in",
}


@pytest.mark.asyncio
async def test_fetch_ticket_context_uses_exact_id_and_complete_thread():
    connector = ZohoDeskConnector({}, CREDENTIALS)
    connector._get = AsyncMock(
        return_value={
            "id": "11270000062142113",
            "ticketNumber": "166356",
            "subject": "arraycopy index out of bounds",
            "description": "Workflow Executor failed.",
        }
    )
    connector.hydrate_thread = AsyncMock(
        return_value=HydratedThread(
            thread_id="zoho_ticket:11270000062142113",
            messages=[{"body": "Fixed the CSV field mapping."}],
            participant_count=2,
        )
    )

    result = await connector.fetch_ticket_context("11270000062142113")

    connector._get.assert_awaited_once()
    assert connector._get.await_args.args[0] == "/tickets/11270000062142113"
    connector.hydrate_thread.assert_awaited_once_with(
        "zoho_ticket:11270000062142113"
    )
    assert result["ticket"]["ticket_number"] == "166356"
    assert result["message_count"] == 1
    assert result["hydration_status"] == "complete"


@pytest.fixture(autouse=True)
def _clean_token_cache():
    """The access-token cache is process-wide by design, so it outlives a
    test the same way it outlives a Celery task. Cleared around each one
    so a token minted by an earlier test cannot satisfy a later test's
    call and hide the very refresh it was written to exercise."""
    connector_module._ACCESS_TOKEN_CACHE.clear()
    yield
    connector_module._ACCESS_TOKEN_CACHE.clear()


def _connector(config=None, credentials=None):
    connector = ZohoDeskConnector(config or {}, {**CREDENTIALS, **(credentials or {})})
    # Pre-seed the token cache so no test touches the accounts endpoint.
    connector._access_token = "tok"
    connector._token_expires_at = float("inf")
    return connector


def _article(article_id="11270000079869964", modified="2026-08-03T05:12:05.000Z", **kw):
    return {
        "id": article_id,
        "title": kw.get("title", "Unable to import workflow on the AutomationEdge Server"),
        "summary": kw.get("summary", "Import fails with a plugin error"),
        "answer": kw.get("answer", "<h3>Overview</h3><p>The system throws an error.</p>"),
        "status": "Published",
        "permission": "ALL",
        "locale": "en",
        "categoryId": "11270000018884717",
        "category": {"name": "REST Plugin", "id": "11270000018884717", "locale": "en"},
        "rootCategoryId": "11270000000046486",
        "departmentId": "11270000000010772",
        "author": {"name": "Apurva Birajdar", "id": "11270000000131155"},
        "owner": {"name": "Apurva Birajdar", "id": "11270000000131155"},
        "createdTime": "2026-07-01T06:48:54.000Z",
        "modifiedTime": modified,
        "permalink": "unable-to-import-workflow",
        "portalUrl": "https://support.automationedge.com/portal/en/kb/articles/x",
        "viewCount": 34,
        "likeCount": 1,
        "tags": kw.get("tags", ["workflow import on ae server", "workflow import"]),
    }


def _ticket(ticket_id="1892000000123456", modified="2026-08-01T12:00:00.000Z", **kw):
    return {
        "id": ticket_id,
        "ticketNumber": kw.get("ticketNumber", "4021"),
        "subject": "Users cannot log in to the VPN",
        "description": "<p>RADIUS timeouts reported by the field team.</p>",
        "resolution": kw.get("resolution", "<p>Restarted the RADIUS service.</p>"),
        "status": "Open",
        "priority": "High",
        "channel": "Email",
        "classification": kw.get("classification", "Incident"),
        "category": "Network",
        "departmentId": "11270000000010772",
        "product": {"name": "VPN Gateway", "id": "p1"},
        "account": {"name": "Acme Corp", "id": "a1"},
        "team": {"name": "Network Ops", "id": "t1"},
        "assignee": {"name": "Dana Reed", "email": "dana@example.com"},
        "contact": {"firstName": "Sam", "lastName": "Patel", "email": "sam@acme.example"},
        "createdTime": "2026-08-01T09:00:00.000Z",
        "modifiedTime": modified,
        "webUrl": "https://desk.zoho.in/agent/acme/it/tickets/details/1892000000123456",
        "relatedTickets": kw.get("relatedTickets", ["4019", "4020"]),
        "cf": {"cf_site": "Pune DC", "cf_blank": ""},
    }


# --- HTML → text -------------------------------------------------------------


def test_html_conversion_preserves_heading_structure():
    """The chunker splits KB articles on heading boundaries, so the
    heading hierarchy has to survive the conversion."""
    text = html_to_text(
        "<h2>Issue Description</h2><p>Connections drop.</p>"
        "<h3>Resolution</h3><ul><li>Restart</li><li>Verify</li></ul>"
    )
    assert text == (
        "## Issue Description\n\nConnections drop.\n\n"
        "### Resolution\n\n- Restart\n- Verify"
    )


def test_html_conversion_drops_machinery_and_empty_headings():
    """Zoho's editor wraps decorative banner images in an <h3>, which
    would otherwise emit a bare '###' the chunker reads as a section."""
    text = html_to_text(
        "<style>.x{color:red}</style><h3><img src='banner.png'></h3>"
        "<script>alert(1)</script><p>Real content.</p>"
    )
    assert "alert" not in text
    assert "color:red" not in text
    assert not text.startswith("#")
    assert "Real content." in text


def test_html_conversion_keeps_image_placeholders():
    """A body that is entirely screenshots must not normalize to an
    empty string — empty is indistinguishable from a fetch failure."""
    assert html_to_text("<p><img alt='Error dialog'></p>") == "[image: Error dialog]"
    assert html_to_text("<p><img src='x.png'></p>") == "[image]"


def test_html_conversion_edge_inputs():
    assert html_to_text(None) == ""
    assert html_to_text("") == ""
    assert html_to_text(123) == ""
    # Already-plain text is normalized, not round-tripped through markup.
    assert html_to_text("just  text") == "just text"
    # Malformed markup degrades instead of raising.
    assert "hello" in html_to_text("<p>hello<<<")


def test_html_conversion_truncates_on_a_line_boundary():
    body = "<p>" + ("word " * 400) + "</p>"
    out = html_to_text(body, max_chars=100)
    assert len(out) <= 100


# --- connector configuration -------------------------------------------------


def test_data_center_selects_matching_host_pair_with_api_prefix():
    """A missing /api/v1 prefix answers 404, which reads exactly like a
    missing OAuth scope — so the prefix is asserted, not assumed."""
    connector = _connector()
    assert connector.accounts_url == "https://accounts.zoho.in"
    assert connector.api_base_url == "https://desk.zoho.in/api/v1"

    default = _connector(credentials={"data_center": None})
    assert default.api_base_url == "https://desk.zoho.com/api/v1"

    unknown = _connector(credentials={"data_center": "atlantis"})
    assert unknown.api_base_url == "https://desk.zoho.com/api/v1"

    # An explicit override is taken as given — a proxy need not use
    # Zoho's path layout.
    override = _connector(credentials={"api_base_url": "https://proxy.local/desk/"})
    assert override.api_base_url == "https://proxy.local/desk"


@pytest.mark.asyncio
async def test_token_is_cached_and_force_remints():
    connector = ZohoDeskConnector({}, CREDENTIALS)
    calls = []

    class _Resp:
        status_code = 200

        def raise_for_status(self):
            pass

        def json(self):
            calls.append(1)
            return {"access_token": f"tok{len(calls)}", "expires_in": 3600,
                    "scope": "Desk.articles.READ"}

    class _Client:
        async def __aenter__(self):
            return SimpleNamespace(post=AsyncMock(return_value=_Resp()))

        async def __aexit__(self, *args):
            return False

    with patch(
        "contextedge.connectors.zoho_desk.connector.httpx.AsyncClient",
        return_value=_Client(),
    ):
        assert await connector._token() == "tok1"
        assert await connector._token() == "tok1"  # cached
        assert await connector._token(force=True) == "tok2"
    assert len(calls) == 2


@pytest.mark.asyncio
async def test_token_refresh_rejects_a_dead_refresh_token():
    """Zoho answers 200 with an error body rather than a 4xx; without an
    explicit check that becomes a silent all-403 sync."""
    connector = ZohoDeskConnector({}, CREDENTIALS)

    class _Resp:
        status_code = 200

        def raise_for_status(self):
            pass

        def json(self):
            return {"error": "invalid_code"}

    class _Client:
        async def __aenter__(self):
            return SimpleNamespace(post=AsyncMock(return_value=_Resp()))

        async def __aexit__(self, *args):
            return False

    with patch(
        "contextedge.connectors.zoho_desk.connector.httpx.AsyncClient",
        return_value=_Client(),
    ):
        with pytest.raises(ValueError, match="invalid_code"):
            await connector._token()


def _token_client(calls):
    """An accounts endpoint that hands out tok1, tok2, ... and counts."""

    class _Resp:
        status_code = 200

        def raise_for_status(self):
            pass

        def json(self):
            calls.append(1)
            return {
                "access_token": f"tok{len(calls)}",
                "expires_in": 3600,
                "scope": "Desk.tickets.READ",
            }

    class _Client:
        async def __aenter__(self):
            return SimpleNamespace(post=AsyncMock(return_value=_Resp()))

        async def __aexit__(self, *args):
            return False

    return patch(
        "contextedge.connectors.zoho_desk.connector.httpx.AsyncClient",
        return_value=_Client(),
    )


@pytest.mark.asyncio
async def test_one_token_serves_every_connector_built_from_the_same_credentials():
    """Each Celery task builds its own connector via ``get_connector``, so
    an instance-only cache is never reused. Zoho allows 5 refresh
    exchanges a minute and answers the sixth with empty result sets
    rather than an error — hydrating 20 threads in a loop returned 9 full
    threads and 11 that looked like empty tickets."""
    calls: list[int] = []
    with _token_client(calls):
        tokens = [
            await ZohoDeskConnector({}, CREDENTIALS)._token() for _ in range(20)
        ]

    assert tokens == ["tok1"] * 20
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_different_credentials_do_not_share_a_token():
    """Two tenants syncing the same Desk instance authenticate as
    themselves; sharing here would send one tenant's token with the
    other's calls."""
    calls: list[int] = []
    with _token_client(calls):
        first = await ZohoDeskConnector({}, CREDENTIALS)._token()
        second = await ZohoDeskConnector(
            {}, {**CREDENTIALS, "refresh_token": "other"}
        )._token()

    assert first != second
    assert len(calls) == 2


@pytest.mark.asyncio
async def test_a_forced_remint_invalidates_the_shared_token():
    """A 401 forces a remint. If the stale token stayed in the shared
    cache, the next connector would pick it up and 401 in turn."""
    calls: list[int] = []
    with _token_client(calls):
        await ZohoDeskConnector({}, CREDENTIALS)._token()
        assert await ZohoDeskConnector({}, CREDENTIALS)._token(force=True) == "tok2"
        # A fresh connector sees the replacement, not the token that 401'd.
        assert await ZohoDeskConnector({}, CREDENTIALS)._token() == "tok2"

    assert len(calls) == 2


@pytest.mark.asyncio
async def test_concurrent_first_calls_mint_once():
    """Without the lock, a worker starting several syncs at once spends
    its whole per-minute quota racing to mint the same token."""
    calls: list[int] = []
    with _token_client(calls):
        tokens = await asyncio.gather(
            *(ZohoDeskConnector({}, CREDENTIALS)._token() for _ in range(8))
        )

    assert tokens == ["tok1"] * 8
    assert len(calls) == 1


def test_the_shared_cache_does_not_hold_the_refresh_token():
    """The cache key identifies a credential set without keeping a second
    copy of the secret in a module global."""
    key = ZohoDeskConnector({}, CREDENTIALS)._token_cache_key()
    assert CREDENTIALS["refresh_token"] not in key
    assert CREDENTIALS["client_secret"] not in key


def test_locks_do_not_leak_across_event_loops():
    """Celery runs each task under its own ``asyncio.run``, and an
    ``asyncio.Lock`` binds to the loop it is first awaited on.

    The binding is what makes a single process-global lock a trap rather
    than an obvious bug: an UNCONTENDED acquire takes a fast path that
    never touches the loop, so one shared across tasks looks fine in
    tests and under light load, then raises ``bound to a different event
    loop`` the first time two syncs actually overlap — which is the only
    situation the lock exists for.
    """
    async def take():
        return connector_module._token_lock("key")

    assert asyncio.run(take()) is not asyncio.run(take())

    calls: list[int] = []

    def one_task():
        async def run():
            # force= so the call reaches the minting lock instead of
            # being answered by the shared token cache — an expired
            # token and a 401 replay both arrive here.
            return await ZohoDeskConnector({}, CREDENTIALS)._token(force=True)

        with _token_client(calls):
            return asyncio.run(run())

    # Separate loops, as separate Celery tasks would be.
    assert one_task() == "tok1"
    assert one_task() == "tok2"
    assert len(calls) == 2


@pytest.mark.asyncio
async def test_missing_credentials_named_explicitly():
    connector = ZohoDeskConnector({}, {"org_id": "1"})
    with pytest.raises(ValueError, match="client_id"):
        await connector._token()


# --- event mapping -----------------------------------------------------------


def test_article_event_maps_body_metadata_and_evidence_type():
    event = _connector()._event("articles", _article(), None)
    assert event.source_type == "zoho_desk"
    assert event.object_type == "articles"
    assert event.thread_id == "zoho_article:11270000079869964"
    assert event.content["_thread_id"] == event.thread_id
    # kb_article routes to the document chunker and to "document"
    # synthesis authority — not to the ticket paths.
    assert event.content["evidence_type"] == "kb_article"
    assert event.content["record_kind"] == "kb_article"
    assert event.content["description"] == "### Overview\n\nThe system throws an error."
    assert event.content["category_name"] == "REST Plugin"
    assert event.content["tags"] == ["workflow import on ae server", "workflow import"]
    assert event.timestamp.year == 2026 and event.timestamp.hour == 5


def test_article_falls_back_to_summary_when_detail_body_is_absent():
    """List rows carry summary but no answer; a skipped or failed detail
    call must still produce searchable evidence."""
    row = _article()
    row.pop("answer")
    event = _connector()._event("articles", row, None)
    assert event.content["description"] == "Import fails with a plugin error"


def test_ticket_event_maps_number_kind_and_merged_body():
    event = _connector()._event("tickets", _ticket(), None)
    assert event.thread_id == "zoho_ticket:1892000000123456"
    assert event.content["evidence_type"] == "ticket"
    assert event.content["record_kind"] == "incident"
    # ticket_number is the quotable number, distinct from the row id.
    assert event.content["ticket_number"] == "4021"
    assert event.content["ticket_id"] == "1892000000123456"
    # Description and resolution both matter for retrieval.
    assert "RADIUS timeouts" in event.content["description"]
    assert "Restarted the RADIUS service." in event.content["description"]
    assert event.content["product_name"] == "VPN Gateway"
    assert event.content["team_name"] == "Network Ops"
    assert event.content["account_name"] == "Acme Corp"
    assert event.content["assignee"] == "Dana Reed"
    assert event.content["reporter"] == "Sam Patel"  # first + last name
    assert event.content["related_tickets"] == ["4019", "4020"]
    # Empty custom fields are dropped; populated ones survive whole.
    assert event.content["cf"] == {"cf_site": "Pune DC"}


def test_ticket_kind_map_is_configurable():
    assert _connector()._event("tickets", _ticket(classification="Problem"), None).content[
        "record_kind"
    ] == "problem"
    remapped = _connector({"type_kind_map": {"escalation": "incident"}})
    assert remapped._event(
        "tickets", _ticket(classification="Escalation"), None
    ).content["record_kind"] == "incident"
    # An unrecognized classification defaults to incident rather than
    # inventing a kind the rest of the platform doesn't discriminate.
    assert _connector()._event("tickets", _ticket(classification="Zzz"), None).content[
        "record_kind"
    ] == "incident"


def test_event_requires_an_id():
    assert _connector()._event("articles", {"title": "no id"}, None) is None


def test_department_scoped_events_carry_the_department():
    event = _connector()._event("articles", _article(), "dept-7")
    assert event.content["department_id"] == "11270000000010772"  # payload wins
    event2 = _connector()._event("articles", {"id": "1"}, "dept-7")
    assert event2.content["department_id"] == "dept-7"


def test_datetime_parsing_tolerates_instance_formats():
    assert parse_zoho_datetime("2026-08-03T05:12:05.000Z").hour == 5
    assert parse_zoho_datetime("2026-08-03 05:12:05").hour == 5
    assert parse_zoho_datetime("2026-08-03T05:12:05+05:30").tzinfo is not None
    assert parse_zoho_datetime(1785585600).year == 2026
    assert parse_zoho_datetime(1785585600000).year == 2026
    assert parse_zoho_datetime("someday") is None
    assert parse_zoho_datetime(None) is None


# --- paging and checkpointing ------------------------------------------------


def _page(rows):
    return {"data": rows}


@pytest.mark.asyncio
async def test_walk_uses_the_api_page_ceiling_and_descending_sort():
    """51 is a 422 on the real API — a page size copied from the
    ServiceNow connector would fail every call."""
    connector = _connector()
    calls = []

    async def get(path, params=None):
        calls.append((path, params))
        return _page([])

    with patch.object(connector, "_get", side_effect=get):
        await connector.fetch_changes("articles", "zoho_desk_module", Checkpoint(data={}))

    assert PAGE_SIZE == 50
    assert calls[0][0] == "/articles"
    assert calls[0][1]["limit"] == 50
    assert calls[0][1]["sortBy"] == "-modifiedTime"
    assert calls[0][1]["from"] == 1


@pytest.mark.asyncio
async def test_fetch_changes_stops_at_the_checkpoint():
    connector = _connector({"fetch_detail": False})
    rows = [
        _article("a3", "2026-08-03T05:00:00.000Z"),
        _article("a2", "2026-08-02T05:00:00.000Z"),
        _article("a1", "2026-08-01T05:00:00.000Z"),
    ]

    async def get(path, params=None):
        return _page(rows)

    with patch.object(connector, "_get", side_effect=get):
        result = await connector.fetch_changes(
            "articles",
            "zoho_desk_module",
            Checkpoint(data={"last_updated": "2026-08-02T05:00:00.000Z",
                             "last_ids": ["a2"]}),
        )

    assert [e.external_id for e in result.events] == ["a3"]
    assert result.new_checkpoint.data == {
        "last_updated": "2026-08-03T05:00:00.000Z",
        "last_ids": ["a3"],
    }


@pytest.mark.asyncio
async def test_tied_timestamps_are_resolved_by_id_set_not_by_a_compound_cursor():
    """The live API returns records sharing a modifiedTime in ASCENDING
    id order inside the DESCENDING time sequence. A (time, id) compound
    cursor therefore never matches the response — it would trip the
    ordering guard, and stopping mid-tie would skip the rest of a bulk
    edit permanently. Only the unseen members of the tied group come
    back, and the walk does not stop inside the tie.
    """
    connector = _connector({"fetch_detail": False})
    tied = "2026-06-03T13:31:29.000Z"
    rows = [
        _article("11270000020748537", tied),   # ids ascend within the tie
        _article("11270000023882537", tied),
        _article("11270000030090915", tied),
        _article("older", "2026-06-01T00:00:00.000Z"),
    ]

    async def get(path, params=None):
        return _page(rows)

    with patch.object(connector, "_get", side_effect=get):
        result = await connector.fetch_changes(
            "articles",
            "zoho_desk_module",
            Checkpoint(data={"last_updated": tied,
                             "last_ids": ["11270000020748537"]}),
        )

    assert [e.external_id for e in result.events] == [
        "11270000023882537",
        "11270000030090915",
    ]
    assert result.new_checkpoint.data["last_updated"] == tied
    assert result.new_checkpoint.data["last_ids"] == [
        "11270000020748537",
        "11270000023882537",
        "11270000030090915",
    ]


@pytest.mark.asyncio
async def test_replaying_a_checkpoint_yields_nothing():
    """Sync idempotence: the same checkpoint twice must not re-deliver."""
    connector = _connector({"fetch_detail": False})
    rows = [_article("a1", "2026-08-03T05:00:00.000Z")]

    async def get(path, params=None):
        return _page(rows)

    with patch.object(connector, "_get", side_effect=get):
        first = await connector.fetch_changes(
            "articles", "zoho_desk_module", Checkpoint(data={})
        )
        second = await connector.fetch_changes(
            "articles", "zoho_desk_module", first.new_checkpoint
        )

    assert len(first.events) == 1
    assert second.events == []
    assert second.new_checkpoint.data == first.new_checkpoint.data


@pytest.mark.asyncio
async def test_legacy_singular_checkpoint_is_honored():
    """A checkpoint written before the id-set model must keep working
    rather than resyncing the whole module."""
    connector = _connector({"fetch_detail": False})
    tied = "2026-08-03T05:00:00.000Z"

    async def get(path, params=None):
        return _page([_article("a1", tied), _article("a2", tied)])

    with patch.object(connector, "_get", side_effect=get):
        result = await connector.fetch_changes(
            "articles",
            "zoho_desk_module",
            Checkpoint(data={"last_updated": tied, "last_id": "a1"}),
        )
    assert [e.external_id for e in result.events] == ["a2"]


@pytest.mark.asyncio
async def test_out_of_order_page_stops_without_advancing_the_checkpoint():
    """Fail-closed: refetching next tick is safe, skipping is not."""
    connector = _connector({"fetch_detail": False})

    async def get(path, params=None):
        return _page([
            _article("a1", "2026-08-01T00:00:00.000Z"),
            _article("a2", "2026-08-03T00:00:00.000Z"),  # newer AFTER older
        ])

    checkpoint = Checkpoint(
        data={"last_updated": "2026-07-01T00:00:00.000Z", "last_ids": ["seed"]}
    )
    with patch.object(connector, "_get", side_effect=get):
        result = await connector.fetch_changes(
            "articles", "zoho_desk_module", checkpoint
        )

    assert result.events == []
    assert result.new_checkpoint.data["last_updated"] == "2026-07-01T00:00:00.000Z"
    assert result.new_checkpoint.data["last_ids"] == ["seed"]


@pytest.mark.asyncio
async def test_paging_continues_across_full_pages():
    connector = _connector({"fetch_detail": False, "max_pages": 5})
    page1 = [_article(f"a{i:03d}", f"2026-08-03T05:{59 - i:02d}:00.000Z")
             for i in range(PAGE_SIZE)]
    page2 = [_article("tail", "2026-07-01T00:00:00.000Z")]
    pages = [_page(page1), _page(page2)]
    seen = []

    async def get(path, params=None):
        seen.append(params["from"])
        return pages[len(seen) - 1]

    with patch.object(connector, "_get", side_effect=get):
        result = await connector.fetch_changes(
            "articles", "zoho_desk_module", Checkpoint(data={})
        )

    assert seen == [1, 51]  # 1-based offset advances by the page size
    assert len(result.events) == PAGE_SIZE + 1


@pytest.mark.asyncio
async def test_max_pages_bounds_one_invocation():
    connector = _connector({"fetch_detail": False, "max_pages": 2})
    full = [_article(f"a{i:03d}", f"2026-08-03T05:{59 - i:02d}:00.000Z")
            for i in range(PAGE_SIZE)]
    calls = []

    async def get(path, params=None):
        calls.append(params)
        return _page(full)

    with patch.object(connector, "_get", side_effect=get):
        await connector.fetch_changes("articles", "zoho_desk_module", Checkpoint(data={}))
    assert len(calls) == 2


@pytest.mark.asyncio
async def test_backfill_filters_to_the_window_and_seeds_incremental():
    from datetime import UTC, datetime

    connector = _connector({"fetch_detail": False})
    rows = [
        _article("too_new", "2026-09-01T00:00:00.000Z"),
        _article("inside", "2026-08-03T00:00:00.000Z"),
        _article("too_old", "2026-01-01T00:00:00.000Z"),
    ]

    async def get(path, params=None):
        return _page(rows)

    with patch.object(connector, "_get", side_effect=get):
        result = await connector.backfill(
            "articles",
            "zoho_desk_module",
            DateRange(
                start=datetime(2026, 7, 1, tzinfo=UTC),
                end=datetime(2026, 8, 15, tzinfo=UTC),
            ),
        )

    assert [e.external_id for e in result.events] == ["inside"]
    assert result.has_more is False
    # The seed is the newest record SEEN, not the newest kept — so a
    # record ahead of the window is not re-delivered by incremental.
    assert result.new_checkpoint.data["last_updated"] == "2026-09-01T00:00:00.000Z"


@pytest.mark.asyncio
async def test_backfill_resumes_by_offset_when_the_page_budget_runs_out():
    from datetime import UTC, datetime

    connector = _connector({"fetch_detail": False, "max_pages": 1})
    full = [_article(f"a{i:03d}", f"2026-08-03T05:{59 - i:02d}:00.000Z")
            for i in range(PAGE_SIZE)]

    async def get(path, params=None):
        return _page(full)

    with patch.object(connector, "_get", side_effect=get):
        result = await connector.backfill(
            "articles",
            "zoho_desk_module",
            DateRange(
                start=datetime(2026, 1, 1, tzinfo=UTC),
                end=datetime(2027, 1, 1, tzinfo=UTC),
            ),
        )

    assert result.has_more is True
    # An offset checkpoint, never a time cursor — a partial sweep must
    # not seed incremental sync.
    assert result.new_checkpoint.data == {"offset": 51}


@pytest.mark.asyncio
async def test_empty_window_seeds_a_time_cursor_not_an_empty_string():
    from datetime import UTC, datetime

    connector = _connector({"fetch_detail": False})

    async def get(path, params=None):
        return _page([])

    with patch.object(connector, "_get", side_effect=get):
        result = await connector.backfill(
            "articles",
            "zoho_desk_module",
            DateRange(
                start=datetime(2019, 1, 1, tzinfo=UTC),
                end=datetime(2019, 6, 1, tzinfo=UTC),
            ),
        )
    assert result.events == []
    assert result.new_checkpoint.data["last_updated"].startswith("2019-06-01")


@pytest.mark.asyncio
async def test_module_filters_apply_to_every_list_call():
    connector = _connector({"module_filters": {"articles": {"categoryId": "cat-1"}}})
    captured = []

    async def get(path, params=None):
        captured.append(params)
        return _page([])

    with patch.object(connector, "_get", side_effect=get):
        await connector.fetch_changes("articles", "zoho_desk_module", Checkpoint(data={}))
    assert captured[0]["categoryId"] == "cat-1"


@pytest.mark.asyncio
async def test_department_scoped_object_id_filters_the_query():
    connector = _connector({"fetch_detail": False})
    captured = []

    async def get(path, params=None):
        captured.append(params)
        return _page([])

    with patch.object(connector, "_get", side_effect=get):
        await connector.fetch_changes(
            "tickets:dept-9", "zoho_desk_module", Checkpoint(data={})
        )
    assert captured[0]["departmentId"] == "dept-9"


@pytest.mark.asyncio
async def test_unknown_module_is_rejected():
    with pytest.raises(ValueError, match="Unknown Zoho Desk module"):
        await _connector().fetch_changes(
            "widgets", "zoho_desk_module", Checkpoint(data={})
        )


# --- detail hydration --------------------------------------------------------


@pytest.mark.asyncio
async def test_detail_call_supplies_the_body_list_rows_omit():
    connector = _connector()
    list_row = _article()
    list_row.pop("answer")

    async def get(path, params=None):
        if path == "/articles":
            return _page([list_row])
        return {"answer": "<h2>Resolution</h2><p>Reinstall the plugin.</p>"}

    with patch.object(connector, "_get", side_effect=get):
        result = await connector.fetch_changes(
            "articles", "zoho_desk_module", Checkpoint(data={})
        )
    assert result.events[0].content["description"] == (
        "## Resolution\n\nReinstall the plugin."
    )


@pytest.mark.asyncio
async def test_detail_failure_degrades_to_the_list_row():
    connector = _connector()

    async def get(path, params=None):
        if path == "/articles":
            return _page([_article()])
        raise httpx.ConnectError("boom")

    with patch.object(connector, "_get", side_effect=get):
        result = await connector.fetch_changes(
            "articles", "zoho_desk_module", Checkpoint(data={})
        )
    assert len(result.events) == 1  # record kept, not dropped


@pytest.mark.asyncio
async def test_fetch_detail_false_skips_the_extra_round_trips():
    connector = _connector({"fetch_detail": False})
    paths = []

    async def get(path, params=None):
        paths.append(path)
        return _page([_article()])

    with patch.object(connector, "_get", side_effect=get):
        await connector.fetch_changes("articles", "zoho_desk_module", Checkpoint(data={}))
    assert paths == ["/articles"]


# --- discovery and validation ------------------------------------------------


def _http_error(status):
    request = httpx.Request("GET", "https://desk.zoho.in/api/v1/tickets")
    response = httpx.Response(status, request=request)
    return httpx.HTTPStatusError("err", request=request, response=response)


@pytest.mark.asyncio
async def test_discovery_skips_modules_the_token_cannot_read():
    """Verified necessary against the live instance: its token carries
    only Desk.articles.READ, so aborting discovery on the tickets 403
    would offer nothing from a portal with 629 syncable articles."""
    connector = _connector()

    async def get(path, params=None):
        if path.startswith("/tickets"):
            raise _http_error(403)
        if path == "/articles/count":
            return {"count": 629}
        return _page([_article()])

    with patch.object(connector, "_get", side_effect=get):
        objects = await connector.discover_objects()

    assert [o.external_id for o in objects] == ["articles"]
    assert objects[0].metadata["record_count"] == 629
    assert objects[0].object_type == "zoho_desk_module"


@pytest.mark.asyncio
async def test_discovery_propagates_real_failures():
    """A 500 is not "this module is unavailable" — it must not be
    silently swallowed into an empty discovery."""
    connector = _connector()

    async def get(path, params=None):
        raise _http_error(500)

    with patch.object(connector, "_get", side_effect=get):
        with pytest.raises(httpx.HTTPStatusError):
            await connector.discover_objects()


@pytest.mark.asyncio
async def test_discovery_honors_the_configured_module_subset():
    connector = _connector({"modules": ["articles", "nonsense"]})

    async def get(path, params=None):
        if path == "/articles/count":
            return {"count": 3}
        return _page([_article()])

    with patch.object(connector, "_get", side_effect=get):
        objects = await connector.discover_objects()
    assert [o.external_id for o in objects] == ["articles"]


@pytest.mark.asyncio
async def test_per_department_discovery_offers_one_object_per_department():
    connector = _connector({"modules": ["tickets"], "per_department": True})

    async def get(path, params=None):
        if path == "/departments":
            return _page([{"id": "d1", "name": "IT"}, {"id": "d2", "name": "Facilities"}])
        if path == "/ticketsCount":
            return {"count": 12}
        return _page([_ticket()])

    with patch.object(connector, "_get", side_effect=get):
        objects = await connector.discover_objects()

    assert [o.external_id for o in objects] == ["tickets:d1", "tickets:d2"]
    assert objects[0].metadata["department_name"] == "IT"


@pytest.mark.asyncio
async def test_per_department_falls_back_when_settings_scope_is_missing():
    connector = _connector({"modules": ["tickets"], "per_department": True})

    async def get(path, params=None):
        if path == "/departments":
            raise _http_error(403)
        if path == "/ticketsCount":
            return {"count": 12}
        return _page([_ticket()])

    with patch.object(connector, "_get", side_effect=get):
        objects = await connector.discover_objects()
    assert [o.external_id for o in objects] == ["tickets"]


@pytest.mark.asyncio
async def test_validate_credentials_reports_a_partial_scope_grant():
    connector = _connector()

    async def get(path, params=None):
        if path.startswith("/tickets"):
            raise _http_error(403)
        return _page([_article()])

    with patch.object(connector, "_token", AsyncMock(return_value="tok")):
        with patch.object(connector, "_get", side_effect=get):
            status = await connector.validate_credentials()

    assert status.valid is True
    assert "articles" in status.message
    assert "Desk.tickets.READ" in status.message


@pytest.mark.asyncio
async def test_validate_credentials_fails_when_no_module_is_readable():
    """A token that mints but grants no Desk scope is not usable
    credentials — it would look like an empty help desk."""
    connector = _connector()

    async def get(path, params=None):
        raise _http_error(403)

    with patch.object(connector, "_token", AsyncMock(return_value="tok")):
        with patch.object(connector, "_get", side_effect=get):
            status = await connector.validate_credentials()
    assert status.valid is False
    assert "no Desk module scope" in status.message


@pytest.mark.asyncio
async def test_probe_reports_scope_coverage_and_body_size():
    connector = _connector()

    async def get(path, params=None):
        if path.startswith("/tickets"):
            raise _http_error(403)
        if path == "/articles/count":
            return {"count": 629}
        if path == "/articles":
            return _page([_article()])
        return _article()

    with patch.object(connector, "_token", AsyncMock(return_value="tok")):
        with patch.object(connector, "_get", side_effect=get):
            report = await connector.probe_configuration()

    assert report["api_base_url"] == "https://desk.zoho.in/api/v1"
    assert report["modules"]["articles"]["readable"] is True
    assert report["modules"]["articles"]["count"] == 629
    assert report["modules"]["articles"]["body_chars"] > 0
    assert report["modules"]["tickets"]["readable"] is False
    assert "SCOPE_MISMATCH" in report["modules"]["tickets"]["error"]


# --- transport ---------------------------------------------------------------


class _Resp:
    def __init__(self, status, body=None, headers=None):
        self.status_code = status
        self.headers = headers or {}
        self.content = b"{}"
        self._body = body or {}

    def raise_for_status(self):
        pass

    def json(self):
        return self._body


def _client_returning(responses, attempts):
    async def fake_get(url, headers=None, params=None):
        attempts.append(url)
        return responses[min(len(attempts) - 1, len(responses) - 1)]

    class _Client:
        async def __aenter__(self):
            return SimpleNamespace(get=fake_get)

        async def __aexit__(self, *args):
            return False

    return _Client()


@pytest.mark.asyncio
async def test_retry_honors_retry_after_on_429():
    connector = _connector()
    attempts = []
    with (
        patch(
            "contextedge.connectors.zoho_desk.connector.httpx.AsyncClient",
            return_value=_client_returning(
                [_Resp(429, headers={"Retry-After": "0"}), _Resp(200, {"ok": True})],
                attempts,
            ),
        ),
        patch("contextedge.connectors.zoho_desk.connector.asyncio.sleep", AsyncMock()),
    ):
        assert await connector._get("/articles") == {"ok": True}
    assert len(attempts) == 2


@pytest.mark.asyncio
async def test_401_remints_the_token_once_and_replays():
    connector = _connector()
    attempts = []
    with patch(
        "contextedge.connectors.zoho_desk.connector.httpx.AsyncClient",
        return_value=_client_returning([_Resp(401), _Resp(200, {"ok": True})], attempts),
    ):
        with patch.object(
            ZohoDeskConnector, "_token", AsyncMock(return_value="fresh")
        ) as token_mock:
            assert await connector._get("/articles") == {"ok": True}
    assert len(attempts) == 2
    assert token_mock.await_args.kwargs["force"] is True


@pytest.mark.asyncio
async def test_a_401_then_5xx_does_not_remint_the_token_twice():
    """The token endpoint is itself rate limited, so re-minting on every
    retry attempt turns one transient 500 into an auth outage."""
    connector = _connector()
    attempts = []
    with (
        patch(
            "contextedge.connectors.zoho_desk.connector.httpx.AsyncClient",
            return_value=_client_returning(
                [_Resp(401), _Resp(500), _Resp(200, {"ok": True})], attempts
            ),
        ),
        patch("contextedge.connectors.zoho_desk.connector.asyncio.sleep", AsyncMock()),
        patch.object(
            ZohoDeskConnector, "_token", AsyncMock(return_value="fresh")
        ) as token_mock,
    ):
        assert await connector._get("/articles") == {"ok": True}

    assert len(attempts) == 3
    forced = [c.kwargs.get("force") for c in token_mock.await_args_list]
    assert forced.count(True) == 1  # exactly one re-mint, on the 401


@pytest.mark.asyncio
async def test_403_is_not_retried():
    """Burning the rate-limit budget re-asking a scope question makes the
    real failure slower to surface."""
    connector = _connector()
    attempts = []

    async def fake_get(url, headers=None, params=None):
        attempts.append(url)
        request = httpx.Request("GET", url)
        response = httpx.Response(403, request=request)
        raise httpx.HTTPStatusError("forbidden", request=request, response=response)

    class _Client:
        async def __aenter__(self):
            return SimpleNamespace(get=fake_get)

        async def __aexit__(self, *args):
            return False

    with patch(
        "contextedge.connectors.zoho_desk.connector.httpx.AsyncClient",
        return_value=_Client(),
    ):
        with pytest.raises(httpx.HTTPStatusError):
            await connector._get("/tickets")
    assert len(attempts) == 1


def test_rows_extraction_tolerates_empty_and_odd_shapes():
    connector = _connector()
    assert connector._rows({"data": [{"a": 1}, "junk"]}) == [{"a": 1}]
    assert connector._rows({}) == []          # 204 no-content
    assert connector._rows([{"b": 2}]) == [{"b": 2}]
    assert connector._rows(None) == []


# --- hydration ---------------------------------------------------------------


@pytest.mark.asyncio
async def test_hydrate_merges_email_threads_and_agent_comments():
    """Zoho splits the conversation across two endpoints and both
    matter: /threads is the customer exchange, /comments is where the
    diagnosis usually is."""
    connector = _connector()

    async def get(path, params=None):
        if path.endswith("/threads"):
            return _page([{"id": "t1"}])
        if "/threads/" in path:
            return {
                "id": "t1",
                "content": "<p>VPN is down for the Pune office.</p>",
                "direction": "in",
                "author": {"name": "Sam Patel"},
                "createdTime": "2026-08-01T09:05:00.000Z",
            }
        if path.endswith("/comments"):
            return _page([
                {
                    "id": "c1",
                    "content": "<p>RADIUS service had crashed.</p>",
                    "isPublic": False,
                    "commenter": {"name": "Dana Reed"},
                    "commentedTime": "2026-08-01T10:00:00.000Z",
                }
            ])
        return {}

    with patch.object(connector, "_get", side_effect=get):
        hydrated = await connector.hydrate_thread("zoho_ticket:1892000000123456")

    assert [m["type"] for m in hydrated.messages] == ["thread", "comment"]
    assert hydrated.messages[0]["body"] == "VPN is down for the Pune office."
    assert hydrated.messages[1]["body"] == "RADIUS service had crashed."
    assert hydrated.messages[1]["is_public"] is False
    assert hydrated.participant_count == 2


@pytest.mark.asyncio
async def test_hydrate_survives_one_endpoint_failing():
    connector = _connector()

    async def get(path, params=None):
        if path.endswith("/threads"):
            raise httpx.ConnectError("down")
        if path.endswith("/comments"):
            return _page([{"id": "c1", "content": "note", "commenter": {"name": "D"}}])
        return {}

    with patch.object(connector, "_get", side_effect=get):
        hydrated = await connector.hydrate_thread("zoho_ticket:1")
    assert len(hydrated.messages) == 1


@pytest.mark.asyncio
async def test_an_exhausted_token_quota_fails_hydration_instead_of_emptying_it():
    """Zoho answers the sixth refresh exchange in a minute with an error
    body, and ``_mint_token`` turns that into a ValueError. Absorbed as
    "this ticket had no conversation", it stored 9 full threads and 11
    empty ones out of 20 while reporting success — so it has to reach the
    caller."""
    connector = ZohoDeskConnector({}, CREDENTIALS)

    async def get(path, params=None):
        raise ValueError("Zoho token refresh returned no access_token: Access Denied")

    with patch.object(connector, "_get", side_effect=get):
        with pytest.raises(ValueError, match="Access Denied"):
            await connector.hydrate_thread("zoho_ticket:1")


@pytest.mark.asyncio
async def test_a_refused_token_fails_hydration_instead_of_emptying_it():
    """A 401 has already been re-minted and replayed inside ``_get``, so
    one arriving here means the credentials are refused — which will be
    just as true for every ticket after this one."""
    connector = _connector()
    request = httpx.Request("GET", "https://desk.zoho.in/api/v1/tickets/1/threads")

    async def get(path, params=None):
        raise httpx.HTTPStatusError(
            "unauthorized",
            request=request,
            response=httpx.Response(401, request=request),
        )

    with patch.object(connector, "_get", side_effect=get):
        with pytest.raises(httpx.HTTPStatusError):
            await connector.hydrate_thread("zoho_ticket:1")


@pytest.mark.asyncio
async def test_both_endpoints_unreadable_raises_rather_than_reporting_no_messages():
    """One endpoint down is survivable — the other still carries part of
    the conversation. Neither readable is not evidence of an empty
    ticket, and the caller marks a returned thread hydrated and never
    fetches it again."""
    connector = _connector()

    async def get(path, params=None):
        raise httpx.ConnectError("down")

    with patch.object(connector, "_get", side_effect=get):
        with pytest.raises(httpx.ConnectError):
            await connector.hydrate_thread("zoho_ticket:1")


@pytest.mark.asyncio
async def test_a_genuinely_empty_ticket_still_hydrates_empty():
    """The distinction being drawn is unreadable vs. empty — a ticket
    that really has no threads and no comments must stay a success, or
    every quiet ticket becomes a retry forever."""
    connector = _connector()

    with patch.object(connector, "_get", side_effect=AsyncMock(return_value=_page([]))):
        hydrated = await connector.hydrate_thread("zoho_ticket:1")

    assert hydrated.messages == []
    assert hydrated.metadata["message_count"] == 0


@pytest.mark.asyncio
async def test_hydrate_is_a_noop_for_articles():
    hydrated = await _connector().hydrate_thread("zoho_article:99")
    assert hydrated.messages == []
    assert hydrated.metadata["hydration"] == "not_applicable"


# --- platform wiring ---------------------------------------------------------


def test_registry_and_schema_accept_zoho_desk():
    from contextedge.connectors.registry import get_connector, supported_source_types
    from contextedge.schemas.source import SourceCreate

    assert "zoho_desk" in supported_source_types()
    assert isinstance(get_connector("zoho_desk", {}, CREDENTIALS), ZohoDeskConnector)
    SourceCreate(source_type="zoho_desk", display_name="Acme Zoho")


def test_ticket_bridge_registers_the_quotable_number_not_the_row_id():
    from contextedge.services.ticket_bridge_service import (
        TICKET_SOURCE_TYPES,
        ticket_display_number,
    )

    assert "zoho_desk" in TICKET_SOURCE_TYPES
    payload = _connector()._event("tickets", _ticket(), None).content
    assert ticket_display_number("zoho_desk", payload) == "4021"
    # A KB article has no ticket number and must register nothing.
    article = _connector()._event("articles", _article(), None).content
    assert ticket_display_number("zoho_desk", article) is None


def test_conversational_mentions_of_zoho_numbers_are_a_known_gap():
    """Documents the one capability Zoho does not get.

    Zoho ticket numbers are bare integers, and the shared token regex
    deliberately never matches those ("order #12345 is unrelated" is an
    explicit assertion in test_ticket_bridging.py). So a Teams message
    quoting "#4021" does not attach to the Zoho ticket's case. The
    ticket's own registration and primary membership are unaffected —
    this is the conversational direction only. Asserted here so the gap
    is visible and a future fix has a test to flip.
    """
    from contextedge.services.ticket_bridge_service import extract_ticket_tokens

    assert extract_ticket_tokens("please check #4021 today") == []
    # The prefixed forms other sources use are unaffected.
    assert extract_ticket_tokens("INC0010427 and ITOPS-101") == [
        "INC0010427",
        "ITOPS-101",
    ]


def test_case_link_candidates_include_ids_and_numbers_not_shared_infrastructure():
    from contextedge.services.correlation_service import extract_case_link_candidates

    payload = _connector()._event("tickets", _ticket(), None).content
    candidates = extract_case_link_candidates(
        source_type="zoho_desk",
        raw_object=SimpleNamespace(external_id="1892000000123456"),
        raw_payload=payload,
    )
    assert ("zoho_desk", "1892000000123456") in candidates  # row id
    assert ("zoho_desk", "4021") in candidates              # quotable number
    assert ("zoho_desk", "4019") in candidates              # related ticket
    # Mass-merge guard: shared infrastructure is never a case-link key.
    for value in ("VPN Gateway", "Acme Corp", "Network Ops", "Network"):
        assert ("zoho_desk", value) not in candidates


def test_kb_articles_resolve_to_the_document_chunker():
    """A KB article's headings are the meaningful split boundaries, so it
    must not take the ticket path just because its source also emits
    tickets."""
    from contextedge.services.chunkers import get_chunker

    # Routes to the structure-driven document chunker (phase 4c). It was
    # the attachment chunker before that existed — the attachment
    # chunker's markdown-heading path remains the fallback when no
    # structured elements are available.
    assert get_chunker("zoho_desk", "kb_article").name == "document"
    assert get_chunker("zoho_desk", "ticket").name == "ticket"
    # Existing resolutions are unchanged.
    assert get_chunker("jira_sm", "issue").name == "ticket"
    assert get_chunker("unknown", "attachment").name == "attachment"
    assert get_chunker("gmail", "message").name == "thread"


def test_kb_articles_carry_document_authority_not_ticket_authority():
    from contextedge.workers.extraction_tasks import (
        INLINE_CHUNK_SOURCE_ALLOWLIST,
        resolve_synthesis_role,
    )

    assert "zoho_desk" in INLINE_CHUNK_SOURCE_ALLOWLIST
    assert resolve_synthesis_role("zoho_desk", None) == "ticket"
    assert resolve_synthesis_role("zoho_desk", None, "kb_article") == "document"
    assert resolve_synthesis_role("zoho_desk", None, "ticket") == "ticket"
    # An explicit source override still wins over both.
    assert resolve_synthesis_role(
        "zoho_desk", {"synthesis_role": "monitoring"}, "kb_article"
    ) == "monitoring"
    # Existing behavior for other sources is unchanged.
    assert resolve_synthesis_role("servicenow", None) == "ticket"
    assert resolve_synthesis_role("teams", {}) == "working_discussion"


def test_deep_link_needs_portal_and_org_slug_and_picks_the_module():
    from contextedge.services.source_deep_link_service import build_source_deep_link

    config = {"portal_url": "https://desk.zoho.in/", "org_slug": "acme"}
    assert build_source_deep_link(
        "zoho_desk", config, "123", thread_id="zoho_ticket:123"
    ) == "https://desk.zoho.in/support/acme/ShowHomePage.do#Cases/dv/123"
    assert build_source_deep_link(
        "zoho_desk", config, "123", thread_id="zoho_article:123"
    ) == "https://desk.zoho.in/support/acme/ShowHomePage.do#Solutions/dv/123"
    # Missing per-portal values degrade to a non-clickable card rather
    # than emitting a URL that 404s.
    assert build_source_deep_link("zoho_desk", {"portal_url": "https://x"}, "1") is None
    assert build_source_deep_link("zoho_desk", config, None) is None
    # The generic template escape hatch still wins.
    assert build_source_deep_link(
        "zoho_desk", {"deep_link_template": "https://z/{external_id}"}, "7"
    ) == "https://z/7"


# --- reference enrichment ----------------------------------------------------


def test_ticket_reference_validation_dedupe_and_cap():
    assert extract_ticket_references(
        {"related_tickets": ["4019", "4019", "bad id!", "4020"]}
    ) == ["4019", "4020"]
    assert extract_ticket_references({"related_tickets": "not-a-list"}) == []
    assert extract_ticket_references({}) == []
    assert extract_ticket_references(
        {"related_tickets": [str(i) for i in range(40)]}
    ) == [str(i) for i in range(20)]


def test_entity_references_are_namespaced_by_kind():
    payload = _connector()._event("tickets", _ticket(), None).content
    refs = {r.sys_id: r for r in extract_entity_references(payload)}
    assert refs["product:vpn gateway"].entity_type == "business_service"
    assert refs["product:vpn gateway"].edge_type == "affects_ci"
    assert refs["team:network ops"].entity_type == "assignment_group"
    assert refs["team:network ops"].edge_type == "assigned_to_group"
    assert refs["account:acme corp"].entity_type == "customer_account"
    # A product and a team sharing a name cannot collide.
    collide = extract_entity_references({"product_name": "Support", "team_name": "Support"})
    assert {r.sys_id for r in collide} == {"product:support", "team:support"}
    assert extract_entity_references({"product_name": "   "}) == []


def test_kb_category_becomes_a_knowledge_entity():
    payload = _connector()._event("articles", _article(), None).content
    refs = extract_entity_references(payload)
    assert [(r.sys_id, r.entity_type, r.edge_type) for r in refs] == [
        ("kb_category:rest plugin", "knowledge_category", "documents")
    ]


def test_tag_topics_are_normalized_and_deduped():
    assert extract_tag_topics({"tags": ["  VPN  outage ", "vpn Outage", "radius"]}) == [
        "VPN outage",
        "radius",
    ]
    assert extract_tag_topics({"tags": "not-a-list"}) == []
    assert extract_tag_topics({}) == []


@pytest.mark.asyncio
async def test_process_creates_ticket_entity_and_topic_edges():
    tenant_id = uuid4()
    evidence = SimpleNamespace(id=uuid4(), domain_id=None)
    linked_evidence_id = uuid4()

    async def resolve(db, tid, ticket_id):
        return linked_evidence_id if ticket_id == "4019" else None

    with (
        patch(
            "contextedge.services.zoho_desk_reference_service._resolve_evidence_for_ticket_id",
            side_effect=resolve,
        ),
        patch(
            "contextedge.services.zoho_desk_reference_service._ensure_entity",
            AsyncMock(return_value=SimpleNamespace(id=uuid4())),
        ) as ensure_entity_mock,
        patch(
            "contextedge.services.zoho_desk_reference_service.ensure_edge",
            AsyncMock(),
        ) as edge_mock,
    ):
        payload = _connector()._event("tickets", _ticket(), None).content
        payload["tags"] = ["vpn"]
        counts = await process_zoho_desk_references(
            SimpleNamespace(), tenant_id, evidence, payload
        )

    assert counts["ticket_edges"] == 1
    assert counts["unresolved_refs"] == 1       # 4020 not ingested yet
    assert counts["entity_edges"] == 3          # product + team + account
    assert counts["topic_edges"] == 1
    assert ensure_entity_mock.await_args.kwargs["external_system"] == "zoho_desk"
    assert edge_mock.await_args_list[0].args[6] == "related_ticket"


@pytest.mark.asyncio
async def test_process_never_self_links():
    tenant_id = uuid4()
    evidence = SimpleNamespace(id=uuid4(), domain_id=None)

    with (
        patch(
            "contextedge.services.zoho_desk_reference_service._resolve_evidence_for_ticket_id",
            AsyncMock(return_value=evidence.id),
        ),
        patch(
            "contextedge.services.zoho_desk_reference_service.ensure_edge", AsyncMock()
        ) as edge_mock,
    ):
        counts = await process_zoho_desk_references(
            SimpleNamespace(), tenant_id, evidence, {"related_tickets": ["4019"]}
        )
    assert counts["ticket_edges"] == 0
    assert edge_mock.await_count == 0


# --- the ticket list projection ----------------------------------------------
#
# The ticket list endpoint does not return `modifiedTime` in its default
# response. Articles do; tickets do not. Both failures that caused were
# silent:
#
#   fetch_changes read "" for every row, compared it against the
#   checkpoint, decided every ticket was older, and stopped on the first
#   row — incremental ticket sync returned zero, forever.
#
#   backfill skipped its window comparisons (guarded on a parsed time,
#   and "" does not parse) and returned rows regardless of the dates
#   asked for, which looked exactly like success.


def test_the_ticket_list_asks_for_modified_time():
    """The whole incremental strategy is sortBy=-modifiedTime plus an
    early stop, so the field it sorts and stops on has to be requested."""
    from contextedge.connectors.zoho_desk.connector import TICKET_FIELDS

    assert "modifiedTime" in TICKET_FIELDS.split(",")


def test_the_projection_covers_every_field_the_mapper_reads():
    """`fields` REPLACES the default projection — asking for one field
    returns one field. Anything omitted here becomes None in the
    evidence silently."""
    from contextedge.connectors.zoho_desk.connector import TICKET_FIELDS

    requested = set(TICKET_FIELDS.split(","))
    for field in (
        "id", "subject", "status", "priority", "category", "subCategory",
        "channel", "classification", "departmentId", "ticketNumber",
        "createdTime", "closedTime", "dueDate", "webUrl",
    ):
        assert field in requested, f"{field} is read by the mapper but not requested"


def test_fields_rejected_by_the_live_api_are_not_requested():
    """An unrecognised name makes the whole call 500 rather than being
    ignored. These three are present in the DEFAULT projection but
    rejected when named."""
    from contextedge.connectors.zoho_desk.connector import TICKET_FIELDS

    requested = set(TICKET_FIELDS.split(","))
    for field in ("subStatus", "statusText", "isArchived"):
        assert field not in requested


def test_custom_fields_are_not_requested_from_the_list():
    """`cf` is accepted on the list endpoint and comes back null. Custom
    fields exist only on /tickets/{id}, which _hydrate_rows fetches —
    and that detail call is what carries the per-ticket version field
    knowledge applicability reads."""
    from contextedge.connectors.zoho_desk.connector import TICKET_FIELDS

    assert "cf" not in TICKET_FIELDS.split(",")


def test_articles_keep_the_default_projection():
    """Articles already return modifiedTime, so narrowing their
    projection would only risk dropping fields for no gain."""
    from contextedge.connectors.zoho_desk.connector import MODULES

    assert "list_fields" in MODULES["tickets"]
    assert "list_fields" not in MODULES["articles"]


def test_the_projection_is_sent_on_list_calls():
    from contextedge.connectors.zoho_desk.connector import ZohoDeskConnector

    connector = ZohoDeskConnector({}, {})
    assert "fields" in connector._list_params("tickets", {"limit": 50})
    assert "fields" not in connector._list_params("articles", {"limit": 50})


def test_a_tenant_filter_can_still_override_the_projection():
    from contextedge.connectors.zoho_desk.connector import ZohoDeskConnector

    connector = ZohoDeskConnector(
        {"module_filters": {"tickets": {"fields": "id,modifiedTime"}}}, {}
    )
    assert connector._list_params("tickets", {})["fields"] == "id,modifiedTime"


@pytest.mark.asyncio
async def test_a_page_missing_timestamps_is_refused_not_silently_mishandled():
    """Without this the failure is invisible: "" sorts as oldest, so the
    checkpoint stop fires on row one and the window bounds are skipped.
    Refusing the page is recoverable; a wrong answer is not."""
    from contextedge.connectors.zoho_desk.connector import ZohoDeskConnector

    connector = ZohoDeskConnector({"max_pages": 1}, {})

    async def _fake_get(path, params=None):
        return {"data": [{"id": "1", "subject": "no timestamp"}]}

    connector._get = _fake_get
    rows, max_time, ids, hit_budget, offset = await connector._walk_desc(
        "tickets",
        department_id=None,
        stop_at_time="2026-01-01T00:00:00.000Z",
        stop_at_ids=set(),
        newer_than_end=None,
        older_than_start=None,
    )
    assert rows == []
    # The checkpoint must not move on a page we refused to trust.
    assert max_time == "2026-01-01T00:00:00.000Z"
