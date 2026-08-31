"""Official AutomationEdge knowledge catalog — public pages, not the Desk API.

Zoho Desk already incrementally syncs KB *articles*. These URLs are the
parts that API never sees: the public category index, the documentation
portal's current release, the versions-life / EOL table, and the
compatibility matrix PDF. A weekly job re-fetches them, upserts a few
stable documentation rows, and kicks article incremental sync so new or
edited articles land the same week they are published.

Detected live 2026-08-31:

- ``docs.automationedge.com`` version selector: **Release 8.5** (latest),
  then 8.4, 8.2, 8.1, 8.0. There is no 8.3 in the picker.
- Support-portal KB still mixes 7.x and 8.* articles (e.g. "Unable to
  Sync Web GUI Plugin in Version 7.x", "How to add ciphers in newer
  version (V8.*)").
- ``automationedge.com/automationedge-versions-life`` is stale: it still
  lists 7.x as Standard Support and does not mention 8.x.
- Ticket custom field on this estate commonly records **8.2.3** — a
  patch on 8.2, not the docs-portal latest. The catalog stores both
  facts rather than collapsing them into one number.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from typing import Any, Protocol
from uuid import UUID

import structlog

from contextedge.connectors.base import IngestionEvent
from contextedge.connectors.zoho_desk.html_text import html_to_text

logger = structlog.get_logger()

DOCS_PORTAL_URL = "https://docs.automationedge.com/"
KB_INDEX_URL = "https://support.automationedge.com/portal/en/kb/automationedge"
KB_DOCUMENTATION_URL = (
    "https://support.automationedge.com/portal/en/kb/automationedge/documentation"
)
SUPPORT_HOME_URL = "https://support.automationedge.com/portal/en/home"
VERSIONS_LIFE_URL = "https://automationedge.com/automationedge-versions-life/"
COMPATIBILITY_MATRIX_URL = (
    "https://automationedge.com/wp-content/uploads/2025/04/Compatibility-matrix.pdf"
)
COMMUNITY_URL = "https://community.automationedge.com/"

# Public pages the weekly job reads. Community is optional: the forum is
# JS-heavy and not approved KB, so a timeout must not fail the rest.
CATALOG_PAGES: tuple[dict[str, Any], ...] = (
    {
        "key": "docs-portal",
        "url": DOCS_PORTAL_URL,
        "title": "AutomationEdge documentation portal — current releases",
        "kind": "docs_portal",
        "optional": False,
    },
    {
        "key": "versions-life",
        "url": VERSIONS_LIFE_URL,
        "title": "AutomationEdge versions life / support levels",
        "kind": "html",
        "optional": False,
    },
    {
        "key": "kb-index",
        "url": KB_INDEX_URL,
        "title": "AutomationEdge public knowledge base index",
        "kind": "kb_listing",
        "optional": False,
    },
    {
        "key": "kb-documentation",
        "url": KB_DOCUMENTATION_URL,
        "title": "AutomationEdge public documentation category",
        "kind": "kb_listing",
        "optional": False,
    },
    {
        "key": "support-home",
        "url": SUPPORT_HOME_URL,
        "title": "AutomationEdge support portal home",
        "kind": "html",
        "optional": True,
    },
    {
        "key": "compatibility-matrix",
        "url": COMPATIBILITY_MATRIX_URL,
        "title": "AutomationEdge compatibility matrix",
        "kind": "pdf",
        "optional": False,
    },
    {
        "key": "community",
        "url": COMMUNITY_URL,
        "title": "AutomationEdge community (pointer, not article bodies)",
        "kind": "html",
        "optional": True,
    },
)

_RELEASE_OPTION_RE = re.compile(
    r"Release\s+(\d+\.\d+(?:\.\d+)?)", re.IGNORECASE
)
_KB_ARTICLE_HREF_RE = re.compile(
    r'href="([^"]*(?:/portal/en/kb/articles/)[^"]*)"[^>]*>([^<]{3,200})',
    re.IGNORECASE,
)
_KB_CATEGORY_HREF_RE = re.compile(
    r'href="([^"]*(?:/portal/en/kb/automationedge/)[^"]*)"[^>]*>([^<]{3,80})',
    re.IGNORECASE,
)

USER_AGENT = "ContextEdge-knowledge-refresh/1.0"
FETCH_TIMEOUT_SECONDS = 25.0
MAX_BODY_CHARS = 80_000


class CatalogHttp(Protocol):
    async def get(
        self, url: str, *, timeout: float, headers: dict[str, str]
    ) -> tuple[int, bytes, str]:
        """Return ``(status_code, body, content_type)``."""


@dataclass(frozen=True)
class CatalogDocument:
    key: str
    title: str
    body: str
    url: str
    product_version: str | None = None
    documented_releases: tuple[str, ...] = ()


@dataclass
class CatalogFetchResult:
    documents: list[CatalogDocument] = field(default_factory=list)
    latest_official_release: str | None = None
    errors: list[str] = field(default_factory=list)


def parse_docs_portal_releases(html: str) -> list[str]:
    """Version picker on docs.automationedge.com: 'Release 8.5', …"""
    found: list[str] = []
    seen: set[str] = set()
    for match in _RELEASE_OPTION_RE.finditer(html or ""):
        raw = match.group(1)
        if raw in seen:
            continue
        seen.add(raw)
        found.append(raw)
    return _sort_releases(found)


def latest_official_release(releases: list[str]) -> str | None:
    ordered = _sort_releases(releases)
    return ordered[0] if ordered else None


def parse_kb_listing(html: str) -> str:
    """Stable listing: categories and article titles, sorted.

    Chrome and cookie banners change every fetch; the article set is
    what a reviewer cares about when asking 'is there a new KB?'.
    """
    categories: list[str] = []
    articles: list[str] = []
    skip = {"skip to content", "skip to menu", "skip to footer", "more", "home"}
    for _href, label in _KB_CATEGORY_HREF_RE.findall(html or ""):
        name = _clean_label(label)
        if name.lower() in skip or not name:
            continue
        if name not in categories:
            categories.append(name)
    for _href, label in _KB_ARTICLE_HREF_RE.findall(html or ""):
        name = _clean_label(label)
        if name.lower() in skip or not name:
            continue
        if name not in articles:
            articles.append(name)
    categories.sort(key=str.lower)
    articles.sort(key=str.lower)
    lines = ["# Public knowledge base listing", ""]
    if categories:
        lines.append("## Categories")
        lines.extend(f"- {name}" for name in categories)
        lines.append("")
    if articles:
        lines.append("## Articles on this page")
        lines.extend(f"- {name}" for name in articles)
    if not articles:
        # The public portal is JS-rendered. A raw HTTP GET often returns
        # chrome ("Skip to Content", cookie banners) and no article hrefs.
        # Dumping that text would churn the catalog every week without
        # naming a single new KB. Article bodies are synced via the Desk
        # API; skip the listing rather than ingest navigation chrome.
        return ""
    return "\n".join(lines).strip()


def build_version_catalog_body(
    *,
    latest_official: str | None,
    documented_releases: list[str],
    estate_version: str | None,
    versions_life_text: str,
) -> str:
    """One document a playbook can retrieve for 'what is current AE?'."""
    lines = [
        "# AutomationEdge official product versions",
        "",
        "This document is refreshed weekly from AutomationEdge public pages.",
        "It is not a substitute for a KB article's own Affected Version.",
        "",
        "## Current releases",
    ]
    if latest_official:
        lines.append(
            f"- Official documentation portal latest: AutomationEdge {latest_official}"
        )
    else:
        lines.append("- Official documentation portal latest: not detected")
    if documented_releases:
        lines.append(
            "- Documented portal releases: "
            + ", ".join(f"AutomationEdge {item}" for item in documented_releases)
        )
    if estate_version:
        lines.append(
            f"- Estate ticket product version (operator): AutomationEdge {estate_version}"
        )
        if latest_official and estate_version.split(".")[0:2] != latest_official.split(".")[0:2]:
            lines.append(
                f"- Note: tickets on this estate record {estate_version}, which is "
                f"not the same line as the docs-portal latest ({latest_official}). "
                "Match playbooks to the ticket version; use the docs latest only "
                "when advising an upgrade."
            )
    lines.extend(["", f"Source: {DOCS_PORTAL_URL}", "", "## Versions life / support levels"])
    lines.append(versions_life_text.strip() or "(page had no extractable text)")
    lines.append(f"\nSource: {VERSIONS_LIFE_URL}")
    lines.append(
        "\nIf this table does not mention 8.x, treat it as stale relative to "
        "the documentation portal version picker."
    )
    return "\n".join(lines).strip()


def _sort_releases(releases: list[str]) -> list[str]:
    def key(raw: str) -> tuple[int, ...]:
        parts = []
        for bit in raw.split("."):
            try:
                parts.append(int(bit))
            except ValueError:
                parts.append(0)
        return tuple(parts)

    unique = list(dict.fromkeys(releases))
    return sorted(unique, key=key, reverse=True)


def _clean_label(label: str) -> str:
    return " ".join((label or "").split()).strip()


def _pdf_to_text(data: bytes, *, filename: str) -> str:
    from contextedge.services.documents.base import render_elements_to_text
    from contextedge.services.documents.pdf import PdfDocumentParser

    parsed = PdfDocumentParser().parse(data, filename=filename)
    text = render_elements_to_text(parsed.elements, max_chars=MAX_BODY_CHARS)
    return text.strip() or f"(PDF had no extractable text layer: {filename})"


class HttpxCatalogHttp:
    async def get(
        self, url: str, *, timeout: float, headers: dict[str, str]
    ) -> tuple[int, bytes, str]:
        import httpx

        async with httpx.AsyncClient(follow_redirects=True, timeout=timeout) as client:
            response = await client.get(url, headers=headers)
            content_type = response.headers.get("content-type", "")
            return response.status_code, response.content, content_type


async def fetch_catalog(
    *,
    http: CatalogHttp | None = None,
    estate_version: str | None = None,
    pages: tuple[dict[str, Any], ...] = CATALOG_PAGES,
) -> CatalogFetchResult:
    """Fetch the public pages and assemble documentation bodies.

    Network failures on optional pages are recorded, not raised. A
    required page that fails is recorded and skipped so the rest of the
    catalog still lands.
    """
    client = http or HttpxCatalogHttp()
    headers = {"User-Agent": USER_AGENT, "Accept": "*/*"}
    result = CatalogFetchResult()
    fetched: dict[str, str] = {}

    for page in pages:
        key = str(page["key"])
        url = str(page["url"])
        optional = bool(page.get("optional"))
        kind = str(page.get("kind") or "html")
        try:
            status, data, content_type = await client.get(
                url, timeout=FETCH_TIMEOUT_SECONDS, headers=headers
            )
            if status >= 400:
                raise RuntimeError(f"HTTP {status}")
            if kind == "pdf" or "pdf" in (content_type or "").lower() or url.lower().endswith(".pdf"):
                fetched[key] = _pdf_to_text(data, filename=url.rsplit("/", 1)[-1])
            else:
                html = data.decode("utf-8", errors="replace")
                if kind == "docs_portal":
                    releases = parse_docs_portal_releases(html)
                    result.latest_official_release = latest_official_release(releases)
                    fetched[key] = html
                    fetched["_releases"] = "\n".join(releases)
                elif kind == "kb_listing":
                    fetched[key] = parse_kb_listing(html)
                else:
                    fetched[key] = html_to_text(html, max_chars=MAX_BODY_CHARS)
        except Exception as exc:  # noqa: BLE001 — a dead community page must not abort KB refresh
            message = f"{key}: {exc}"
            result.errors.append(message)
            logger.warning("official_kb_catalog.fetch_failed", key=key, url=url, error=str(exc))
            if not optional:
                continue

    releases = [item for item in (fetched.get("_releases") or "").split("\n") if item]
    latest = result.latest_official_release or latest_official_release(releases)
    result.latest_official_release = latest

    result.documents.append(
        CatalogDocument(
            key="version-catalog",
            title="AutomationEdge official product versions",
            body=build_version_catalog_body(
                latest_official=latest,
                documented_releases=releases,
                estate_version=estate_version,
                versions_life_text=fetched.get("versions-life", ""),
            ),
            url=DOCS_PORTAL_URL,
            documented_releases=tuple(releases),
        )
    )
    if fetched.get("compatibility-matrix"):
        result.documents.append(
            CatalogDocument(
                key="compatibility-matrix",
                title="AutomationEdge compatibility matrix",
                body=fetched["compatibility-matrix"],
                url=COMPATIBILITY_MATRIX_URL,
            )
        )
    for key in ("kb-index", "kb-documentation"):
        page = next((item for item in pages if item["key"] == key), None)
        if not page or not fetched.get(key):
            continue
        result.documents.append(
            CatalogDocument(
                key=key,
                title=str(page["title"]),
                body=fetched[key],
                url=str(page["url"]),
            )
        )
    return result


def catalog_event(document: CatalogDocument) -> IngestionEvent:
    """Connector-shaped event so persist + normalize stay on the existing path."""
    content = {
        "title": document.title,
        "body_text": document.body,
        "web_url": document.url,
        "status": "Published",
        "record_kind": "documentation",
        "evidence_type": "documentation",
        "catalog_key": document.key,
    }
    if document.product_version:
        content["product_version"] = document.product_version
    return IngestionEvent(
        external_id=f"official-catalog:{document.key}",
        source_type="zoho_desk",
        object_type="official_catalog",
        content=content,
        thread_id=None,
        timestamp=None,
        metadata={"catalog_key": document.key, "record_kind": "documentation"},
    )


def is_articles_source_object(external_id: str | None, object_type: str | None) -> bool:
    ext = (external_id or "").lower()
    otype = (object_type or "").lower()
    return otype == "articles" or ext == "articles" or ext.startswith("articles:")


def estate_version_from_config(config: dict | None, settings_value: str | None) -> str | None:
    """Operator-stated running version (ticket CF, e.g. 8.2.3)."""
    block = (config or {}).get("official_kb") if isinstance(config, dict) else None
    if isinstance(block, dict):
        raw = block.get("current_version") or block.get("estate_version")
        if isinstance(raw, str) and raw.strip():
            return raw.strip()
    if settings_value and settings_value.strip():
        return settings_value.strip()
    return None


def catalog_enabled(config: dict | None, settings_enabled: bool) -> bool:
    block = (config or {}).get("official_kb") if isinstance(config, dict) else None
    if isinstance(block, dict) and "enabled" in block:
        return bool(block.get("enabled"))
    return settings_enabled


async def upsert_catalog_documents(
    db: Any,
    *,
    tenant_id: UUID,
    source_id: UUID,
    source_object_id: UUID | None,
    documents: list[CatalogDocument],
) -> dict[str, Any]:
    """Insert first-time catalog rows; patch existing ones in place.

    A changed public KB index is the weekly signal. Re-running normalize
    on a new content hash would mint a second documentation row for the
    same catalog key, so edits update the existing evidence instead.
    """
    from contextedge.services.ingestion_persistence import persist_ingestion_events

    created_ids: list[UUID] = []
    updated_ids: list[UUID] = []
    unchanged = 0

    for document in documents:
        event = catalog_event(document)
        existing = await _existing_catalog_evidence(
            db, tenant_id=tenant_id, source_id=source_id, external_id=event.external_id,
            title=document.title,
        )
        if existing is not None and (existing.body_text or "") == document.body:
            unchanged += 1
            continue

        _created, _skipped, new_ids = await persist_ingestion_events(
            db,
            tenant_id=tenant_id,
            source_id=source_id,
            source_object_id=source_object_id,
            events=[event],
        )
        if existing is not None:
            _apply_catalog_update(existing, document, new_raw_id=new_ids[0] if new_ids else None)
            updated_ids.append(existing.id)
            continue
        created_ids.extend(new_ids)

    return {
        "created": len(created_ids),
        "updated": len(updated_ids),
        "unchanged": unchanged,
        "normalize_raw_ids": [str(item) for item in created_ids],
        "rechunk_evidence_ids": [str(item) for item in updated_ids],
    }


async def _existing_catalog_evidence(
    db: Any,
    *,
    tenant_id: UUID,
    source_id: UUID,
    external_id: str,
    title: str,
) -> Any:
    from sqlalchemy import select

    from contextedge.models.evidence import EvidenceItem, RawEvidenceObject

    raw_id = (
        await db.execute(
            select(RawEvidenceObject.id)
            .where(
                RawEvidenceObject.tenant_id == tenant_id,
                RawEvidenceObject.source_id == source_id,
                RawEvidenceObject.external_id == external_id,
            )
            .order_by(RawEvidenceObject.stored_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    if raw_id is not None:
        found = (
            await db.execute(
                select(EvidenceItem).where(EvidenceItem.raw_object_ref == raw_id)
            )
        ).scalar_one_or_none()
        if found is not None:
            return found
    return (
        await db.execute(
            select(EvidenceItem)
            .where(
                EvidenceItem.tenant_id == tenant_id,
                EvidenceItem.source_id == source_id,
                EvidenceItem.title == title,
            )
            .limit(1)
        )
    ).scalar_one_or_none()


def _apply_catalog_update(evidence: Any, document: CatalogDocument, *, new_raw_id: UUID | None) -> None:
    evidence.body_text = document.body
    evidence.title = document.title[:500]
    evidence.content_hash = hashlib.sha256(document.body.encode("utf-8")).hexdigest()
    evidence.chunked_at = None
    evidence.embedding = None
    if new_raw_id is not None:
        evidence.raw_object_ref = new_raw_id
    facets = dict(evidence.source_facets or {})
    facets["catalog_key"] = document.key
    if document.product_version:
        facets["version"] = document.product_version
    evidence.source_facets = facets
    # Catalog pages list many releases. Stamping one platform version
    # would make retrieval treat the matrix / version list as applying
    # only to that patch — the opposite of what they are.
    evidence.applicability = {
        "extracted_by": "catalog",
        "product_versions": (
            {"_platform": document.product_version} if document.product_version else {}
        ),
    }


async def run_official_knowledge_refresh(
    db: Any,
    tenant_id: str,
    *,
    http: CatalogHttp | None = None,
    enqueue_article_sync=None,
) -> dict[str, Any]:
    """Per active Zoho Desk source: queue article incremental + upsert catalog.

    ``enqueue_article_sync`` is injected so tests don't hit Celery.
    """
    from sqlalchemy import select

    from contextedge.config import settings
    from contextedge.models.source import Source, SourceObject

    enqueue = enqueue_article_sync or _enqueue_article_sync
    query = select(Source).where(
        Source.source_type == "zoho_desk",
        Source.is_active.is_(True),
    )
    if tenant_id != "all":
        query = query.where(Source.tenant_id == UUID(str(tenant_id)))
    sources = (await db.execute(query)).scalars().all()

    summary: dict[str, Any] = {
        "sources": 0,
        "article_syncs_queued": 0,
        "latest_official_release": None,
        "catalog": [],
        "normalize": [],
        "rechunk": [],
        "errors": [],
    }
    for source in sources:
        summary["sources"] += 1
        objects = (
            await db.execute(
                select(SourceObject).where(
                    SourceObject.source_id == source.id,
                    SourceObject.tenant_id == source.tenant_id,
                    SourceObject.approved_for_sync.is_(True),
                )
            )
        ).scalars().all()
        articles = [
            obj
            for obj in objects
            if is_articles_source_object(obj.external_id, obj.object_type)
        ]
        for obj in articles:
            enqueue(str(source.id), str(obj.id), str(source.tenant_id))
            summary["article_syncs_queued"] += 1

        if not catalog_enabled(source.config, settings.official_kb_refresh_enabled):
            summary["catalog"].append({"source_id": str(source.id), "skipped": "disabled"})
            continue
        estate = estate_version_from_config(
            source.config, settings.official_kb_estate_version
        )
        fetched = await fetch_catalog(http=http, estate_version=estate)
        summary["errors"].extend(fetched.errors)
        if fetched.latest_official_release:
            summary["latest_official_release"] = fetched.latest_official_release
        host = articles[0] if articles else (objects[0] if objects else None)
        if host is None:
            summary["catalog"].append(
                {"source_id": str(source.id), "skipped": "no_approved_source_object"}
            )
            continue
        stats = await upsert_catalog_documents(
            db,
            tenant_id=source.tenant_id,
            source_id=source.id,
            source_object_id=host.id,
            documents=fetched.documents,
        )
        stats["source_id"] = str(source.id)
        stats["tenant_id"] = str(source.tenant_id)
        summary["catalog"].append(stats)
        if stats["normalize_raw_ids"]:
            summary["normalize"].append(
                (str(source.tenant_id), stats["normalize_raw_ids"])
            )
        if stats["rechunk_evidence_ids"]:
            summary["rechunk"].append(
                (str(source.tenant_id), stats["rechunk_evidence_ids"])
            )
    return summary


def _enqueue_article_sync(source_id: str, object_id: str, tenant_id: str) -> None:
    from contextedge.workers.sync_tasks import run_incremental_sync

    run_incremental_sync.delay(source_id, object_id, tenant_id)
