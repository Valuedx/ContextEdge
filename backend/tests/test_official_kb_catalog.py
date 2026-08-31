"""Official AE KB catalog: parse public pages, weekly refresh wiring."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from contextedge.services.official_kb_catalog import (
    CatalogDocument,
    build_version_catalog_body,
    catalog_enabled,
    catalog_event,
    estate_version_from_config,
    fetch_catalog,
    is_articles_source_object,
    latest_official_release,
    parse_docs_portal_releases,
    parse_kb_listing,
    run_official_knowledge_refresh,
)


DOCS_PORTAL_HTML = """
<select>
  <option>All Releases</option>
  <option>Release 8.5</option>
  <option>Release 8.4</option>
  <option>Release 8.2</option>
  <option>Release 8.1</option>
  <option>Release 8.0</option>
</select>
"""

KB_INDEX_HTML = """
<a href="/portal/en/kb/automationedge/general">General</a>
<a href="/portal/en/kb/articles/how-to-add-ciphers-in-newer-version-8-12-2025">How to add ciphers in newer version (V8.*).</a>
<a href="/portal/en/kb/articles/unable-to-sync-web-gui-plugin">Unable to Sync Web GUI Plugin in Version 7.x</a>
<a href="/portal/en/kb/automationedge#mainContainer">Skip to Content</a>
"""


def test_docs_portal_latest_is_8_5():
    """Verified against docs.automationedge.com on 2026-08-31."""
    releases = parse_docs_portal_releases(DOCS_PORTAL_HTML)
    assert releases[0] == "8.5"
    assert latest_official_release(releases) == "8.5"
    assert "8.2" in releases
    assert "8.3" not in releases


def test_kb_listing_keeps_versioned_titles_and_drops_chrome():
    text = parse_kb_listing(KB_INDEX_HTML)
    assert "How to add ciphers in newer version (V8.*)." in text
    assert "Unable to Sync Web GUI Plugin in Version 7.x" in text
    assert "Skip to Content" not in text
    assert "General" in text


def test_js_shell_listing_is_not_ingested_as_chrome():
    """A raw GET of the Zoho portal is a JS shell. Ingesting that as a
    KB index would churn every week without naming a new article."""
    assert parse_kb_listing("<html><body>Skip to Content</body></html>") == ""


def test_version_catalog_does_not_collapse_estate_8_2_3_into_docs_8_5():
    """Tickets on this estate record 8.2.3; the portal latest is 8.5.
    Collapsing those would send playbooks at the wrong major/minor."""
    body = build_version_catalog_body(
        latest_official="8.5",
        documented_releases=["8.5", "8.4", "8.2"],
        estate_version="8.2.3",
        versions_life_text="7.x Standard support",
    )
    assert "AutomationEdge 8.5" in body
    assert "AutomationEdge 8.2.3" in body
    assert "not the same line" in body


def test_catalog_event_is_documentation_not_a_ticket():
    event = catalog_event(
        CatalogDocument(
            key="version-catalog",
            title="AutomationEdge official product versions",
            body="body",
            url="https://docs.automationedge.com/",
            product_version="8.2.3",
        )
    )
    assert event.object_type == "official_catalog"
    assert event.content["evidence_type"] == "documentation"
    assert event.external_id == "official-catalog:version-catalog"


def test_articles_object_detection():
    assert is_articles_source_object("articles", "zoho_desk_module")
    assert is_articles_source_object("articles:dept", "zoho_desk_module")
    assert not is_articles_source_object("tickets", "zoho_desk_module")


def test_estate_version_prefers_source_config_over_settings():
    assert (
        estate_version_from_config(
            {"official_kb": {"current_version": "8.2.3"}}, "8.5"
        )
        == "8.2.3"
    )
    assert estate_version_from_config({}, "8.2.3") == "8.2.3"
    assert catalog_enabled({"official_kb": {"enabled": False}}, True) is False
    assert catalog_enabled({}, True) is True


class _FakeHttp:
    def __init__(self, pages: dict[str, tuple[int, bytes, str]]):
        self.pages = pages

    async def get(self, url: str, *, timeout: float, headers: dict[str, str]):
        if url not in self.pages:
            raise RuntimeError(f"unexpected url {url}")
        return self.pages[url]


@pytest.mark.asyncio
async def test_fetch_catalog_stamps_estate_and_docs_latest():
    http = _FakeHttp(
        {
            "https://docs.automationedge.com/": (200, DOCS_PORTAL_HTML.encode(), "text/html"),
            "https://automationedge.com/automationedge-versions-life/": (
                200,
                b"<h1>Versions Life</h1><p>7.x Standard support</p>",
                "text/html",
            ),
            "https://support.automationedge.com/portal/en/kb/automationedge": (
                200,
                KB_INDEX_HTML.encode(),
                "text/html",
            ),
            "https://support.automationedge.com/portal/en/kb/automationedge/documentation": (
                200,
                b"<a href='/portal/en/kb/articles/ig610'>Installation Guide for Version 6.1.0</a>",
                "text/html",
            ),
            "https://support.automationedge.com/portal/en/home": (
                503,
                b"down",
                "text/html",
            ),
            "https://automationedge.com/wp-content/uploads/2025/04/Compatibility-matrix.pdf": (
                200,
                b"%PDF-fake",
                "application/pdf",
            ),
            "https://community.automationedge.com/": (200, b"<html>forum</html>", "text/html"),
        }
    )

    def fake_pdf(data, *, filename):
        return "Java 21 / AE 8.x compatibility"

    import contextedge.services.official_kb_catalog as mod

    original = mod._pdf_to_text
    mod._pdf_to_text = lambda data, filename: fake_pdf(data, filename=filename)
    try:
        result = await fetch_catalog(http=http, estate_version="8.2.3")
    finally:
        mod._pdf_to_text = original

    assert result.latest_official_release == "8.5"
    keys = {doc.key for doc in result.documents}
    assert "version-catalog" in keys
    assert "compatibility-matrix" in keys
    assert "kb-index" in keys
    catalog = next(doc for doc in result.documents if doc.key == "version-catalog")
    assert catalog.product_version is None
    assert "8.5" in catalog.body
    assert "8.2.3" in catalog.body
    assert any("HTTP 503" in err or "support-home" in err for err in result.errors)


@pytest.mark.asyncio
async def test_weekly_refresh_queues_article_incremental_only():
    tenant = uuid4()
    source_id = uuid4()
    articles_id = uuid4()
    tickets_id = uuid4()
    queued: list[tuple[str, str, str]] = []

    source = SimpleNamespace(
        id=source_id,
        tenant_id=tenant,
        source_type="zoho_desk",
        is_active=True,
        config={"official_kb": {"enabled": False}},
    )
    articles = SimpleNamespace(
        id=articles_id,
        source_id=source_id,
        tenant_id=tenant,
        external_id="articles",
        object_type="zoho_desk_module",
        approved_for_sync=True,
    )
    tickets = SimpleNamespace(
        id=tickets_id,
        source_id=source_id,
        tenant_id=tenant,
        external_id="tickets",
        object_type="zoho_desk_module",
        approved_for_sync=True,
    )

    sources_result = MagicMock()
    sources_result.scalars.return_value.all.return_value = [source]
    objects_result = MagicMock()
    objects_result.scalars.return_value.all.return_value = [articles, tickets]
    db = MagicMock()
    db.execute = AsyncMock(side_effect=[sources_result, objects_result])

    out = await run_official_knowledge_refresh(
        db,
        "all",
        enqueue_article_sync=lambda s, o, t: queued.append((s, o, t)),
    )
    assert queued == [(str(source_id), str(articles_id), str(tenant))]
    assert out["article_syncs_queued"] == 1
    assert out["catalog"][0]["skipped"] == "disabled"


def test_beat_schedule_includes_weekly_kb_refresh():
    from celery.schedules import crontab

    from contextedge.workers.celery_app import celery_app

    entry = celery_app.conf.beat_schedule["refresh-official-knowledge-weekly"]
    assert entry["task"] == "sync.refresh_official_knowledge"
    assert entry["args"] == ("all",)
    assert isinstance(entry["schedule"], crontab)


def test_refresh_task_is_registered():
    from contextedge.workers import sync_tasks as _sync_tasks  # noqa: F401
    from contextedge.workers.celery_app import celery_app

    assert "sync.refresh_official_knowledge" in celery_app.tasks
