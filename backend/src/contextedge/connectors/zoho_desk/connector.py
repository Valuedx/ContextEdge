"""Zoho Desk connector — OAuth2 refresh-token flow over the v1 REST API.

Covers the two evidence-bearing modules of a Zoho Desk help desk:
``tickets`` (the incident/request record and its email threads and
comments) and ``articles`` (the knowledge base — the resolution
knowledge that answers "has anyone fixed this before").

Everything in here that could have been guessed was instead **verified
against a live instance** (``desk.zoho.in``, org 60001911841, 629
articles). The findings that shaped the design, with the ones that
contradict the obvious implementation called out:

- ``limit`` is capped at **50**, not 100 — 51 answers
  ``422 UNPROCESSABLE_ENTITY: exceeds the range of '1-50'``. A page size
  copied from the ServiceNow connector would 422 on every call.
- **There is no modified-since filter** on the list endpoints.
  ``modifiedTimeRange`` is rejected as an extra query parameter. So
  incremental sync cannot be a server-side window the way ServiceNow's
  ``sys_updated_on>`` query is.
- ``sortBy=-modifiedTime`` **is** honored and returns strictly
  descending order — checked across all 13 pages of the live KB. That is
  what incremental sync is built on: walk newest-first and stop at the
  checkpoint (see ``_walk_desc``).
- Records sharing a ``modifiedTime`` come back in **ascending id**
  order inside that descending sequence. So a ServiceNow-style
  ``(time, id)`` compound cursor does not describe this API's ordering
  and cannot be used; the checkpoint is a timestamp plus the set of ids
  already emitted at it. ``_walk_desc`` documents why in full.
- ``from`` is 1-based offset paging.
- List rows carry ``summary`` but **not the body**. The article body
  (``answer``) and a ticket's ``description`` only arrive on the
  per-record detail call, so sync issues one detail call per changed
  record (bounded by ``DETAIL_FETCH_LIMIT``).
- Filters that work: ``categoryId``, ``status``, ``permission`` on
  articles; ``departmentId``, ``status``, ``assigneeId`` on tickets.
  Filters that are rejected outright on articles: ``departmentId``,
  ``locale``, ``category``.
- Missing scopes fail as ``403 SCOPE_MISMATCH``, per module. A token
  granted only ``Desk.articles.READ`` — which is exactly what the live
  instance's token has — must still sync the KB rather than failing
  discovery wholesale. See ``discover_objects``.

Credentials (``credentials``):

- ``client_id``, ``client_secret``, ``refresh_token`` — self-client or
  server-based OAuth app credentials.
- ``org_id`` — the ``orgId`` header every Desk call requires.
- ``data_center`` — ``com`` (default), ``in``, ``eu``, ``au``, ``jp``,
  ``ca``, ``sa``, ``uk``. Zoho accounts are pinned to the DC they were
  created in and a cross-DC call fails authentication, so this must
  match the portal's domain. ``accounts_url`` / ``api_base_url``
  override the derived pair for private/sandbox deployments.

Config (``source_config``, all optional):

- ``modules`` — subset of ``MODULES`` to sync (default: all).
- ``module_filters`` — per-module query params merged into every list
  call, e.g. ``{"tickets": {"status": "Open"}}``. Server-side filtering
  means the records never leave Zoho, so they cost no extraction and no
  storage — the same rationale as ServiceNow's ``table_filters``.
- ``per_department`` — when true, discovery enumerates departments and
  offers one syncable object per department instead of one for all
  tickets (needs ``Desk.settings.READ``).
- ``fetch_detail`` — set false to skip the per-record detail call and
  ingest list rows only (summary-level, no body). Cheaper, much worse
  retrieval; off by default.
- ``max_pages`` — page budget per sync invocation (default 20).
"""

from __future__ import annotations

import asyncio
import hashlib
import time
import weakref
from datetime import UTC, datetime
from typing import Any

import httpx
import structlog

from contextedge.connectors.base import (
    BackfillResult,
    BaseConnector,
    ChangeResult,
    Checkpoint,
    CredentialStatus,
    DateRange,
    DiscoveredObject,
    HydratedThread,
    IngestionEvent,
    RateLimitConfig,
)
from contextedge.connectors.zoho_desk.html_text import html_to_text

logger = structlog.get_logger()

# Zoho pins every account to the data center it was created in; the
# accounts host that issues the token and the Desk host that accepts it
# must belong to the same one. Verified live on the `in` pair.
#
# The API root includes ``/api/v1``. Dropping it does not 401 or 404 in
# a way that names the problem — Desk answers a bare ``/articles`` with
# an unexplained 404, which reads exactly like a missing OAuth scope.
API_PATH_PREFIX = "/api/v1"

DATA_CENTERS = {
    "com": ("https://accounts.zoho.com", "https://desk.zoho.com"),
    "in": ("https://accounts.zoho.in", "https://desk.zoho.in"),
    "eu": ("https://accounts.zoho.eu", "https://desk.zoho.eu"),
    "au": ("https://accounts.zoho.com.au", "https://desk.zoho.com.au"),
    "jp": ("https://accounts.zoho.jp", "https://desk.zoho.jp"),
    "ca": ("https://accounts.zohocloud.ca", "https://desk.zohocloud.ca"),
    "sa": ("https://accounts.zoho.sa", "https://desk.zoho.sa"),
    "uk": ("https://accounts.zoho.uk", "https://desk.zoho.uk"),
}

# Hard API ceiling — 51 is a 422, not a silent clamp.
PAGE_SIZE = 50
DEFAULT_MAX_PAGES = 20
# Per-record detail calls are the expensive part of a sync (one HTTP
# round trip each). Bounded per invocation; the rest arrive next tick
# because the checkpoint only advances over records actually emitted.
DETAIL_FETCH_LIMIT = 200

# The ticket list endpoint does NOT return `modifiedTime` in its default
# response. Articles do; tickets do not — verified against the live
# instance, where every ticket row came back with 35 fields and none of
# them was the one the entire incremental strategy depends on.
#
# The consequences were both silent. `fetch_changes` read an empty
# timestamp for every row, compared "" against the checkpoint, decided
# every ticket was older than it, and stopped on the first row — so
# incremental ticket sync returned zero, forever. `backfill` skipped its
# window comparisons entirely (they are guarded on a parsed time, and ""
# does not parse), so it returned rows regardless of the window asked
# for and looked like it was working.
#
# `fields` REPLACES the default projection rather than adding to it —
# requesting `fields=modifiedTime` returns two fields, not thirty-six —
# so every field the event mapper reads has to be named here. Anything
# omitted silently becomes None in the evidence, which is why this list
# is explicit rather than clever.
#
# An unrecognised name makes the whole call 500 rather than being
# ignored, so this set is the subset the live API actually accepts:
# `subStatus`, `statusText` and `isArchived` are returned in the DEFAULT
# projection but rejected when requested by name.
#
# Custom fields are deliberately absent. `cf` is accepted here but comes
# back null on the list endpoint — they exist only on
# `/tickets/{id}`, which `_hydrate_rows` already fetches. That detail
# call is what carries the per-ticket version field that knowledge
# applicability reads.
TICKET_FIELDS = ",".join(
    (
        "id",
        "modifiedTime",
        "createdTime",
        "closedTime",
        "dueDate",
        "subject",
        "status",
        "priority",
        "category",
        "subCategory",
        "channel",
        "classification",
        "departmentId",
        "productId",
        "ticketNumber",
        "webUrl",
        "isEscalated",
        "isOverDue",
        "email",
        "phone",
        "threadCount",
        "commentCount",
        "contactId",
        "assigneeId",
        "teamId",
        "accountId",
        "isSpam",
    )
)

# Access tokens shared across every connector instance in this process,
# keyed by a hash of the credential set: {key: (token, expires_at, scope)}.
#
# Zoho caps refresh-token exchanges at 5/minute and live access tokens at
# 30 per refresh token, and exceeding either returns empty results rather
# than an error. A per-instance cache never survives, because each Celery
# task constructs its own connector.
_ACCESS_TOKEN_CACHE: dict[str, tuple[str, float, str]] = {}

# Minting locks, per event loop and then per credential set.
#
# An asyncio.Lock binds to the loop it is first awaited on and raises if
# awaited from another, and Celery runs every task under its own
# asyncio.run(). A single process-global lock would therefore work for
# the first task in a worker and fail for all the rest. Locking across
# loops is not needed anyway — they do not run concurrently.
#
# Weak-keyed so a finished loop takes its locks with it. Keying on
# id(loop) instead would eventually hand a new loop the dead loop's lock
# once CPython reused the address.
_TOKEN_LOCKS: weakref.WeakKeyDictionary[
    asyncio.AbstractEventLoop, dict[str, asyncio.Lock]
] = weakref.WeakKeyDictionary()


def _token_lock(cache_key: str) -> asyncio.Lock:
    """One minting at a time per credential set, within this loop."""
    per_loop = _TOKEN_LOCKS.setdefault(asyncio.get_running_loop(), {})
    lock = per_loop.get(cache_key)
    if lock is None:
        lock = per_loop[cache_key] = asyncio.Lock()
    return lock


def _is_fatal_for_the_run(exc: Exception) -> bool:
    """Whether a failure will hit every following record identically.

    Record-level fetching is deliberately fail-soft: one unreadable
    thread must not cost a ticket its other messages. Authentication and
    quota failures are different in kind — they are a statement about the
    caller, not about the record — and swallowing them turns every
    subsequent record into a plausible-looking empty one.

    That is not hypothetical. Zoho answers the sixth refresh-token
    exchange in a minute with an error body rather than a 4xx, and
    hydrating 20 ticket threads in a loop stored 9 full conversations and
    11 empty ones, twice, with the run reporting success both times.
    """
    if isinstance(exc, ValueError):
        # From _mint_token: missing credentials, a dead refresh token, or
        # the accounts endpoint answering 200 with an error — which is
        # what an exhausted refresh quota looks like.
        return True
    # 401 has already been re-minted and replayed once inside _get, so
    # reaching here means the credentials themselves are refused.
    return (
        isinstance(exc, httpx.HTTPStatusError)
        and exc.response.status_code in (401, 403, 429)
    )


MODULES: dict[str, dict[str, Any]] = {
    "tickets": {
        "label": "Tickets",
        "list_path": "/tickets",
        "count_path": "/ticketsCount",
        "scope": "Desk.tickets.READ",
        "evidence_type": "ticket",
        "thread_prefix": "zoho_ticket",
        # Sent on every list call for this module.
        "list_fields": TICKET_FIELDS,
    },
    "articles": {
        "label": "KB Articles",
        "list_path": "/articles",
        "count_path": "/articles/count",
        "scope": "Desk.articles.READ",
        "evidence_type": "kb_article",
        "thread_prefix": "zoho_article",
    },
}

# Zoho Desk ticket "classification"/type values → the shared thread
# vocabulary the rest of the platform discriminates on (change-risk,
# post-action verification). Overridable via config["type_kind_map"].
DEFAULT_TYPE_KIND_MAP = {
    "incident": "incident",
    "problem": "problem",
    "question": "question",
    "request": "service_request",
    "service request": "service_request",
    "feature": "service_request",
    "change": "change_request",
    "change request": "change_request",
}


class ZohoDeskConnector(BaseConnector):
    """Connector for the Zoho Desk v1 REST API."""

    MAX_ATTEMPTS = 3
    BACKOFF_BASE_SECONDS = 1.0
    MAX_RETRY_AFTER_SECONDS = 60.0
    # Refresh a little before the hour is up so a token does not expire
    # mid-page and turn a successful sync into a partial one.
    TOKEN_EXPIRY_SKEW_SECONDS = 120.0

    def __init__(self, source_config: dict[str, Any], credentials: dict[str, Any]):
        super().__init__(source_config, credentials)
        config = source_config or {}

        dc = str(credentials.get("data_center") or "com").strip().lower()
        default_accounts, default_api = DATA_CENTERS.get(dc, DATA_CENTERS["com"])
        self.accounts_url = str(
            credentials.get("accounts_url") or default_accounts
        ).rstrip("/")
        # An explicit api_base_url is taken as given (a private or proxied
        # deployment may not use Zoho's path layout); a derived one gets
        # the version prefix appended.
        override = credentials.get("api_base_url")
        self.api_base_url = (
            str(override).rstrip("/")
            if override
            else f"{default_api}{API_PATH_PREFIX}"
        )
        self.data_center = dc
        self.org_id = str(credentials.get("org_id") or "")

        self.module_filters = config.get("module_filters") or {}
        self.fetch_detail = config.get("fetch_detail", True) is not False
        try:
            self.max_pages = max(1, int(config.get("max_pages", DEFAULT_MAX_PAGES)))
        except (TypeError, ValueError):
            self.max_pages = DEFAULT_MAX_PAGES
        try:
            self.max_days = int(config.get("max_days")) if config.get("max_days") is not None else None
        except (TypeError, ValueError):
            self.max_days = None
        try:
            self.max_records = int(config.get("max_records")) if config.get("max_records") is not None else None
        except (TypeError, ValueError):
            self.max_records = None
        self.type_kind_map = {
            str(k).strip().lower(): str(v)
            for k, v in {
                **DEFAULT_TYPE_KIND_MAP,
                **(config.get("type_kind_map") or {}),
            }.items()
        }

        self._access_token: str | None = None
        self._token_expires_at: float = 0.0
        # Populated by the refresh grant; surfaced by probe_configuration
        # so an operator can see the granted scope without guessing.
        self._granted_scope: str = ""

    # --- auth ------------------------------------------------------------

    async def _token(self, *, force: bool = False) -> str:
        """Cached access token from the refresh-token grant.

        Cached at PROCESS level, not just on the instance. Every Celery
        task builds a fresh connector via ``get_connector``, so an
        instance-only cache is never reused and each task mints its own
        token.

        That is not a minor inefficiency. Zoho allows 5 refresh-token
        exchanges per minute and 30 live access tokens per refresh token,
        and exceeding either does not raise — the subsequent API calls
        simply return nothing. Hydrating 20 ticket threads in a loop
        produced 9 full threads and 11 that looked like empty tickets,
        twice, until the pattern gave it away. A bulk run would
        under-ingest silently and report success.

        Access tokens live an hour, so one serves thousands of records.
        """
        now = time.monotonic()
        if not force and self._access_token and now < self._token_expires_at:
            return self._access_token

        cache_key = self._token_cache_key()
        if not force:
            cached = _ACCESS_TOKEN_CACHE.get(cache_key)
            if cached and now < cached[1]:
                self._access_token, self._token_expires_at = cached[0], cached[1]
                self._granted_scope = cached[2]
                return cached[0]
        else:
            _ACCESS_TOKEN_CACHE.pop(cache_key, None)

        # One minting at a time per credential set. Without this, a
        # worker starting several syncs at once spends its whole
        # per-minute quota racing to mint the same token.
        async with _token_lock(cache_key):
            cached = _ACCESS_TOKEN_CACHE.get(cache_key)
            if not force and cached and time.monotonic() < cached[1]:
                self._access_token, self._token_expires_at = cached[0], cached[1]
                self._granted_scope = cached[2]
                return cached[0]
            return await self._mint_token(cache_key)

    def _token_cache_key(self) -> str:
        """Identity of a credential set, without holding the secret.

        Hashed so the process-wide cache does not keep a second copy of
        the refresh token lying around in a module global.
        """
        material = "|".join(
            str(self.credentials.get(key) or "")
            for key in ("client_id", "refresh_token")
        )
        return hashlib.sha256(material.encode("utf-8")).hexdigest()

    async def _mint_token(self, cache_key: str) -> str:
        """Exchange the refresh token and publish the result to the cache."""
        missing = [
            key
            for key in ("client_id", "client_secret", "refresh_token")
            if not self.credentials.get(key)
        ]
        if missing:
            raise ValueError(f"Zoho Desk credentials missing: {', '.join(missing)}")

        params = {
            "refresh_token": self.credentials["refresh_token"],
            "client_id": self.credentials["client_id"],
            "client_secret": self.credentials["client_secret"],
            "grant_type": "refresh_token",
        }
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(f"{self.accounts_url}/oauth/v2/token", params=params)
        resp.raise_for_status()
        data = resp.json()

        token = data.get("access_token")
        if not token:
            # Zoho answers 200 with {"error": "invalid_code"} on a dead
            # refresh token — raising here turns a silent all-403 sync
            # into a legible credential failure.
            raise ValueError(
                f"Zoho token refresh returned no access_token: {data.get('error') or data}"
            )
        self._access_token = str(token)
        try:
            lifetime = float(data.get("expires_in") or 3600)
        except (TypeError, ValueError):
            lifetime = 3600.0
        self._token_expires_at = time.monotonic() + max(
            60.0, lifetime - self.TOKEN_EXPIRY_SKEW_SECONDS
        )
        self._granted_scope = str(data.get("scope") or "")
        _ACCESS_TOKEN_CACHE[cache_key] = (
            self._access_token,
            self._token_expires_at,
            self._granted_scope,
        )
        logger.info(
            "zoho_desk.access_token_minted",
            expires_in=int(lifetime),
            cached_tokens=len(_ACCESS_TOKEN_CACHE),
        )
        return self._access_token

    async def _headers(self, *, force_token: bool = False) -> dict[str, str]:
        return {
            "Authorization": f"Zoho-oauthtoken {await self._token(force=force_token)}",
            "orgId": self.org_id,
            "Accept": "application/json",
        }

    # --- transport -------------------------------------------------------

    async def _get(self, path: str, params: dict | None = None) -> Any:
        """GET with retry/backoff, Retry-After handling, and one forced
        token re-mint on 401.

        4xx other than 401/429 raise immediately: a 403 SCOPE_MISMATCH or
        a 422 bad-parameter will not improve on retry, and burning the
        rate-limit budget re-asking makes the real failure slower to
        surface.
        """
        last_exc: Exception | None = None
        retried_auth = False
        force_token = False

        for attempt in range(1, self.MAX_ATTEMPTS + 1):
            try:
                async with httpx.AsyncClient(timeout=30.0) as client:
                    resp = await client.get(
                        f"{self.api_base_url}{path}",
                        headers=await self._headers(force_token=force_token),
                        params=params,
                    )
                # Consumed: a later 5xx retry in this same call must reuse
                # the token just minted, not mint another one. The token
                # endpoint is itself rate limited, so re-minting per
                # attempt turns one transient 500 into an auth outage.
                force_token = False

                if resp.status_code == 401 and not retried_auth:
                    # Token revoked or expired early — mint a fresh one
                    # and replay this request once.
                    retried_auth = True
                    force_token = True
                    self._access_token = None
                    continue

                if resp.status_code == 429 or resp.status_code >= 500:
                    delay = self._retry_delay(resp.headers.get("Retry-After"), attempt)
                    if attempt < self.MAX_ATTEMPTS:
                        await asyncio.sleep(delay)
                        continue

                resp.raise_for_status()
                if not resp.content:
                    return {}
                return resp.json()
            except (httpx.TransportError, httpx.HTTPStatusError) as exc:
                last_exc = exc
                if (
                    isinstance(exc, httpx.HTTPStatusError)
                    and exc.response.status_code < 500
                    and exc.response.status_code != 429
                ):
                    raise
                if attempt < self.MAX_ATTEMPTS:
                    await asyncio.sleep(
                        self.BACKOFF_BASE_SECONDS * (2 ** (attempt - 1))
                    )
        raise last_exc  # exhausted retries

    def _retry_delay(self, retry_after: str | None, attempt: int) -> float:
        backoff = self.BACKOFF_BASE_SECONDS * (2 ** (attempt - 1))
        if not retry_after:
            return min(backoff, self.MAX_RETRY_AFTER_SECONDS)
        try:
            return min(float(retry_after), self.MAX_RETRY_AFTER_SECONDS)
        except ValueError:
            return min(backoff, self.MAX_RETRY_AFTER_SECONDS)

    @staticmethod
    def _rows(data: Any) -> list[dict]:
        """Zoho list endpoints answer ``{"data": [...]}``; an empty result
        set is served as ``204`` with no body, which arrives here as
        ``{}``."""
        if isinstance(data, dict):
            rows = data.get("data")
            if isinstance(rows, list):
                return [row for row in rows if isinstance(row, dict)]
        if isinstance(data, list):
            return [row for row in data if isinstance(row, dict)]
        return []

    def _list_params(self, module: str, extra: dict | None = None) -> dict:
        params: dict[str, Any] = {}
        # Ask for the projection this module needs before anything else,
        # so a tenant's module_filters can still override it.
        list_fields = MODULES.get(module, {}).get("list_fields")
        if list_fields:
            params["fields"] = list_fields
        module_filter = self.module_filters.get(module)
        if isinstance(module_filter, dict):
            params.update({str(k): v for k, v in module_filter.items()})
        params.update(extra or {})
        return params

    # --- credential validation -------------------------------------------

    async def validate_credentials(self) -> CredentialStatus:
        """Valid when the token mints AND at least one module is readable.

        A token that mints but has no Desk scope is not usable
        credentials — it would discover zero objects and look like an
        empty help desk. The message names which modules are readable so
        the operator can see a partial grant for what it is.
        """
        try:
            await self._token(force=True)
        except Exception as exc:  # noqa: BLE001
            return CredentialStatus(valid=False, message=f"Token refresh failed: {exc}")

        readable, denied = [], []
        for module, meta in MODULES.items():
            try:
                await self._get(meta["list_path"], {"limit": 1})
                readable.append(module)
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code in (403, 404):
                    denied.append(f"{module} (needs {meta['scope']})")
                    continue
                return CredentialStatus(valid=False, message=f"{module}: {exc}")
            except Exception as exc:  # noqa: BLE001
                return CredentialStatus(valid=False, message=f"{module}: {exc}")

        if not readable:
            return CredentialStatus(
                valid=False,
                message=(
                    "Token is valid but grants no Desk module scope. Missing: "
                    + "; ".join(denied)
                ),
            )
        message = f"Zoho Desk access verified for: {', '.join(readable)}"
        if denied:
            message += f". Not granted: {'; '.join(denied)}"
        return CredentialStatus(valid=True, message=message)

    # --- discovery --------------------------------------------------------

    async def discover_objects(self) -> list[DiscoveredObject]:
        """One syncable object per readable module.

        A module the token cannot read is **skipped, not fatal** — the
        same rule the ServiceNow connector applies to ``em_alert`` on an
        instance without ITOM. Verified necessary here: the live
        instance's token carries only ``Desk.articles.READ``, so a
        connector that aborted discovery on the tickets 403 would offer
        nothing at all from a portal with 629 syncable articles.
        """
        requested = (self.source_config or {}).get("modules")
        wanted = (
            [m for m in requested if m in MODULES]
            if isinstance(requested, list) and requested
            else list(MODULES)
        )

        objects: list[DiscoveredObject] = []
        for module in wanted:
            meta = MODULES[module]
            try:
                await self._get(meta["list_path"], {"limit": 1})
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code in (403, 404):
                    logger.info(
                        "zoho_desk.module_unavailable",
                        module=module,
                        status=exc.response.status_code,
                        required_scope=meta["scope"],
                    )
                    continue
                raise

            count = await self._module_count(meta)

            if module == "tickets" and (self.source_config or {}).get("per_department"):
                departments = await self._departments()
                if departments:
                    objects.extend(
                        DiscoveredObject(
                            external_id=f"tickets:{dept['id']}",
                            object_type="zoho_desk_module",
                            display_name=f"Tickets — {dept.get('name') or dept['id']}",
                            metadata={
                                "module": "tickets",
                                "department_id": dept["id"],
                                "department_name": dept.get("name"),
                            },
                        )
                        for dept in departments
                    )
                    continue
                logger.info("zoho_desk.departments_unavailable", module=module)

            objects.append(
                DiscoveredObject(
                    external_id=module,
                    object_type="zoho_desk_module",
                    display_name=f"{meta['label']} ({module})",
                    metadata={"module": module, "record_count": count},
                )
            )
        return objects

    async def _module_count(self, meta: dict) -> int | None:
        """Record count when the module exposes one. Best-effort: a count
        endpoint the plan does not include must not fail discovery."""
        path = meta.get("count_path")
        if not path:
            return None
        try:
            data = await self._get(path)
        except Exception:  # noqa: BLE001
            return None
        if isinstance(data, dict):
            for key in ("count", "ticketsCount", "total"):
                value = data.get(key)
                if value is not None:
                    try:
                        return int(value)
                    except (TypeError, ValueError):
                        return None
        return None

    async def _departments(self) -> list[dict]:
        try:
            data = await self._get("/departments", {"limit": PAGE_SIZE})
        except Exception:  # noqa: BLE001 - needs Desk.settings.READ
            return []
        return [row for row in self._rows(data) if row.get("id")]

    # --- sync -------------------------------------------------------------

    @staticmethod
    def _split_object_id(object_id: str) -> tuple[str, str | None]:
        """``"tickets"`` or ``"tickets:<departmentId>"``."""
        if ":" in object_id:
            module, _, scope_id = object_id.partition(":")
            return (module if module in MODULES else "tickets"), (scope_id or None)
        return object_id, None

    async def _walk_desc(
        self,
        module: str,
        *,
        department_id: str | None,
        stop_at_time: str | None,
        stop_at_ids: set[str],
        newer_than_end: datetime | None,
        older_than_start: datetime | None,
        start_offset: int = 1,
    ) -> tuple[list[dict], str, set[str], bool, int]:
        """Walk a module newest-first, stopping at the checkpoint.

        This is the whole incremental strategy, and it exists because
        Zoho Desk has **no modified-since filter** (verified: the list
        endpoints reject ``modifiedTimeRange`` as an extra parameter).
        What the API does honor is ``sortBy=-modifiedTime``, so: read
        from the newest record backwards and stop at the checkpoint.

        Descending order is also what makes offset paging safe here. A
        record edited mid-walk jumps to position 1 and shifts everything
        after it one place *later*, so a concurrent update can only
        re-deliver a row — never skip one. (Ascending order plus offset
        shifts rows the other way, which is silent data loss; that is
        why the ServiceNow connector refuses ``sysparm_offset`` for
        incremental sync.) A *deletion* mid-walk shifts rows earlier and
        can skip one; the early stop keeps that window to the records
        changed since the last tick, and a backfill repairs it.

        **The checkpoint is a timestamp plus a boundary id set, not a
        compound cursor.** The obvious design — order by
        ``(modifiedTime, id)`` and stop at the last tuple, the way the
        ServiceNow connector keysets on ``(sys_updated_on, sys_id)`` —
        is wrong against this API, and measurably so: the live instance
        returns records that share a ``modifiedTime`` in *ascending* id
        order inside the descending-time sequence (three articles from
        one bulk edit at ``2026-06-03T13:31:29.000Z`` arrive
        low-id-first). Sorting by a descending tuple therefore never
        matches the response, so a tuple cursor would either trip the
        ordering guard on every call or, worse, stop mid-tie and skip the
        rest of a bulk edit permanently.

        So the only ordering relied on is the one the server actually
        provides — ``modifiedTime`` descending — and ties are resolved by
        remembering *which ids* were already emitted at the boundary
        timestamp. A row strictly older than the boundary stops the walk;
        a row at the boundary is emitted only if its id is new, and does
        not stop the walk, so the remainder of a tied group is always
        reached.

        Returns ``(rows, max_time, ids_at_max_time, hit_page_budget,
        next_offset)``.
        """
        meta = MODULES[module]
        collected: list[dict] = []
        max_time = stop_at_time or ""
        ids_at_max: set[str] = set(stop_at_ids) if stop_at_time else set()
        offset = max(1, start_offset)
        hit_budget = True

        for _page in range(self.max_pages):
            params = self._list_params(
                module,
                {"limit": PAGE_SIZE, "from": offset, "sortBy": "-modifiedTime"},
            )
            if department_id:
                params["departmentId"] = department_id

            rows = self._rows(await self._get(meta["list_path"], params))
            if not rows:
                hit_budget = False
                break

            # Fail-closed ordering guard, same contract as the ServiceNow
            # connector's page_order_violation: the early stop is only
            # sound if the server actually honored the sort. If a page
            # arrives out of order, stop without advancing the checkpoint
            # — refetching next tick is safe (dedup absorbs it), silently
            # skipping unreturned records is not. Checks descending
            # modifiedTime only, because that is the entire ordering
            # guarantee (see the tie-order note above).
            times = [_modified_time(row) for row in rows]

            # A row with no timestamp cannot be ordered, checkpointed or
            # windowed, and every downstream comparison degrades to a
            # silent wrong answer rather than an error: "" sorts as
            # oldest, so the checkpoint stop fires on the first row and
            # incremental sync returns nothing; and the window bounds are
            # guarded on a parsed time, so a backfill quietly ignores the
            # dates it was asked for.
            #
            # This is not hypothetical — it is exactly what the ticket
            # module did until `fields` was sent, and neither failure
            # surfaced anywhere. Refuse the page instead.
            if any(not t for t in times):
                logger.error(
                    "zoho_desk.missing_modified_time",
                    module=module,
                    page_size=len(rows),
                    without_time=sum(1 for t in times if not t),
                    hint="list projection is missing modifiedTime",
                )
                return [], stop_at_time or "", set(stop_at_ids), False, offset

            if times != sorted(times, reverse=True):
                logger.error(
                    "zoho_desk.page_order_violation",
                    module=module,
                    page_size=len(rows),
                )
                return [], stop_at_time or "", set(stop_at_ids), False, offset

            reached_checkpoint = False
            for row in rows:
                row_time = _modified_time(row)
                row_id = str(row.get("id") or "")

                if row_time > max_time:
                    max_time, ids_at_max = row_time, {row_id}
                elif row_time == max_time and row_time:
                    ids_at_max.add(row_id)

                if stop_at_time is not None:
                    if row_time < stop_at_time:
                        reached_checkpoint = True
                        break
                    if row_time == stop_at_time and row_id in stop_at_ids:
                        # Already delivered at this exact timestamp. Skip
                        # it but keep walking: the rest of the tied group
                        # may still be new.
                        continue

                modified = parse_zoho_datetime(row_time)
                if newer_than_end is not None and modified is not None and (
                    modified > newer_than_end
                ):
                    continue  # ahead of the backfill window; keep walking back
                if older_than_start is not None and modified is not None and (
                    modified < older_than_start
                ):
                    reached_checkpoint = True
                    break
                collected.append(row)
                if self.max_records is not None and len(collected) >= self.max_records:
                    collected = collected[:self.max_records]
                    reached_checkpoint = True
                    break

            offset += len(rows)
            if reached_checkpoint or len(rows) < PAGE_SIZE:
                hit_budget = False
                break

        return collected, max_time, ids_at_max, hit_budget, offset

    async def _hydrate_rows(self, module: str, rows: list[dict]) -> list[dict]:
        """Fetch the per-record detail that list rows omit.

        Verified live: an ``/articles`` list row has ``summary`` but no
        ``answer``; the body only exists on ``/articles/{id}``. Ingesting
        list rows alone would produce evidence whose body is a one-line
        teaser — searchable in name only. Detail failures degrade to the
        list row rather than dropping the record.
        """
        if not self.fetch_detail:
            return rows
        meta = MODULES[module]
        out: list[dict] = []
        for row in rows[:DETAIL_FETCH_LIMIT]:
            record_id = row.get("id")
            if not record_id:
                continue
            try:
                detail = await self._get(f"{meta['list_path']}/{record_id}")
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "zoho_desk.detail_fetch_failed",
                    module=module,
                    record_id=str(record_id),
                    error_type=type(exc).__name__,
                )
                out.append(row)
                continue
            out.append({**row, **detail} if isinstance(detail, dict) else row)
        out.extend(rows[DETAIL_FETCH_LIMIT:])
        return out

    async def backfill(
        self,
        object_id: str,
        object_type: str,
        window: DateRange,
        checkpoint: Checkpoint | None = None,
    ) -> BackfillResult:
        module, department_id = self._split_object_id(object_id)
        if module not in MODULES:
            raise ValueError(f"Unknown Zoho Desk module: {module}")

        start = window.start if window.start.tzinfo else window.start.replace(tzinfo=UTC)
        end = window.end if window.end.tzinfo else window.end.replace(tzinfo=UTC)
        offset = int((checkpoint.data or {}).get("offset", 1)) if checkpoint else 1

        rows, max_time, ids_at_max, hit_budget, next_offset = await self._walk_desc(
            module,
            department_id=department_id,
            stop_at_time=None,
            stop_at_ids=set(),
            newer_than_end=end,
            older_than_start=start,
            start_offset=offset,
        )
        rows = await self._hydrate_rows(module, rows)
        events = [self._event(module, row, department_id) for row in rows]
        events = [e for e in events if e is not None]

        if hit_budget:
            # Page budget spent inside the window — resume from the same
            # offset next call. The time checkpoint is only written on
            # the final page so a resumed backfill cannot seed
            # incremental sync from a partial sweep.
            new_checkpoint = Checkpoint(data={"offset": next_offset})
        else:
            if not max_time:
                # Nothing in the window. Seed at the window end (clamped
                # to now) so incremental does not re-scan history it has
                # already been told is empty.
                max_time = min(end, datetime.now(UTC)).strftime(ZOHO_TS_FORMAT)
                ids_at_max = set()
            new_checkpoint = Checkpoint(data=_checkpoint_data(max_time, ids_at_max))

        return BackfillResult(
            events=events,
            new_checkpoint=new_checkpoint,
            items_processed=len(events),
            has_more=hit_budget,
        )

    async def fetch_changes(
        self,
        object_id: str,
        object_type: str,
        checkpoint: Checkpoint,
    ) -> ChangeResult:
        module, department_id = self._split_object_id(object_id)
        if module not in MODULES:
            raise ValueError(f"Unknown Zoho Desk module: {module}")

        stop_at_time, stop_at_ids = _read_checkpoint(checkpoint.data)
        older_than_start = (
            datetime.now(UTC) - timedelta(days=self.max_days)
            if self.max_days is not None and self.max_days > 0
            else None
        )

        rows, max_time, ids_at_max, _hit_budget, _offset = await self._walk_desc(
            module,
            department_id=department_id,
            stop_at_time=stop_at_time,
            stop_at_ids=stop_at_ids,
            newer_than_end=None,
            older_than_start=older_than_start,
        )
        rows = await self._hydrate_rows(module, rows)
        events = [self._event(module, row, department_id) for row in rows]
        events = [e for e in events if e is not None]

        if max_time < stop_at_time:
            # Ordering guard tripped, or the module came back empty:
            # never move the checkpoint backwards.
            max_time, ids_at_max = stop_at_time, stop_at_ids

        return ChangeResult(
            events=events,
            new_checkpoint=Checkpoint(data=_checkpoint_data(max_time, ids_at_max)),
            items_processed=len(events),
        )

    # --- event mapping ----------------------------------------------------

    def ticket_kind(self, ticket: dict) -> str:
        """Normalized record kind from the Desk classification/type."""
        for field in ("classification", "category", "channel"):
            raw = str(ticket.get(field) or "").strip().lower()
            if raw in self.type_kind_map:
                return self.type_kind_map[raw]
        return "incident"

    def _event(
        self, module: str, row: dict, department_id: str | None
    ) -> IngestionEvent | None:
        record_id = row.get("id")
        if record_id in (None, ""):
            return None
        record_id = str(record_id)

        if module == "articles":
            content = self._article_content(row)
            kind = "kb_article"
        else:
            content = self._ticket_content(row)
            kind = content["record_kind"]

        meta = MODULES[module]
        content["evidence_type"] = meta["evidence_type"]
        content["zoho_module"] = module
        if department_id and not content.get("department_id"):
            # The record's own department wins; the object's scope is the
            # fallback. Not `setdefault` — the content builders always
            # write the key, so it exists as None when absent upstream.
            content["department_id"] = department_id

        thread_id = f"{meta['thread_prefix']}:{record_id}"
        content["_thread_id"] = thread_id

        return IngestionEvent(
            external_id=record_id,
            source_type="zoho_desk",
            object_type=module,
            content=content,
            thread_id=thread_id,
            timestamp=parse_zoho_datetime(row.get("modifiedTime"))
            or parse_zoho_datetime(row.get("createdTime")),
            metadata={"module": module, "record_kind": kind},
        )

    def _ticket_content(self, row: dict) -> dict:
        """Canonical ticket payload.

        ``ticket_number`` is the human-readable number agents quote in
        chat and email; it is the key ``ticket_bridge_service`` registers
        so a Teams message saying "#4021" attaches to this case. It is
        NOT the same field as ``id`` (an opaque 18-digit row id), and
        using the id would make every quoted number unresolvable.
        """
        description = html_to_text(row.get("description"))
        resolution = html_to_text(row.get("resolution"))
        body_parts = [part for part in (description, resolution) if part]
        content = {
            "ticket_id": str(row.get("id") or ""),
            "ticket_number": _text(row.get("ticketNumber")),
            "title": _text(row.get("subject")),
            "summary": _text(row.get("subject")),
            "description": "\n\n".join(body_parts) or _text(row.get("subject")),
            "resolution": resolution or None,
            "status": _text(row.get("status")) or None,
            "sub_status": _text(row.get("subStatus")) or None,
            "priority": _text(row.get("priority")) or None,
            "channel": _text(row.get("channel")) or None,
            "classification": _text(row.get("classification")) or None,
            "category": _text(row.get("category")) or None,
            "sub_category": _text(row.get("subCategory")) or None,
            "department_id": _text(row.get("departmentId")) or None,
            "product_id": _text(row.get("productId")) or None,
            "product_name": _nested_name(row.get("product")),
            "account_name": _nested_name(row.get("account")),
            "team_name": _nested_name(row.get("team")),
            "assignee": _nested_name(row.get("assignee")),
            "assignee_email": _nested(row.get("assignee"), "email"),
            "reporter": _nested_name(row.get("contact")),
            "reporter_email": _nested(row.get("contact"), "email"),
            "created_at": _text(row.get("createdTime")) or None,
            "updated_at": _text(row.get("modifiedTime")) or None,
            "closed_at": _text(row.get("closedTime")) or None,
            "due_date": _text(row.get("dueDate")) or None,
            "is_escalated": bool(row.get("isEscalated")),
            "is_overdue": bool(row.get("isOverDue")),
            "web_url": _text(row.get("webUrl")) or None,
            "tags": _string_list(row.get("tags")),
            "related_tickets": _string_list(
                row.get("relatedTickets") or row.get("linkedTickets")
            ),
            "attachment_refs": _attachment_refs(row.get("attachments")),
        }
        content["record_kind"] = self.ticket_kind(row)
        content["cf"] = _custom_fields(row.get("cf"))
        return content

    def _article_content(self, row: dict) -> dict:
        """Canonical KB-article payload.

        The body is ``answer`` converted to heading-preserving text so
        the document chunker splits on the author's own sections; the
        list-row ``summary`` is the fallback when the detail call was
        skipped or failed.
        """
        answer = html_to_text(row.get("answer"))
        summary = html_to_text(row.get("summary"))
        category = row.get("category") if isinstance(row.get("category"), dict) else {}
        return {
            "article_id": str(row.get("id") or ""),
            "title": _text(row.get("title")),
            "summary": _text(row.get("title")),
            "description": answer or summary,
            "article_summary": summary or None,
            "status": _text(row.get("status")) or None,
            "permission": _text(row.get("permission")) or None,
            "locale": _text(row.get("locale")) or None,
            "category_id": _text(row.get("categoryId")) or None,
            "category_name": _text(category.get("name")) or None,
            "root_category_id": _text(row.get("rootCategoryId")) or None,
            "department_id": _text(row.get("departmentId")) or None,
            "author": _nested_name(row.get("author")),
            "owner": _nested_name(row.get("owner")),
            "created_at": _text(row.get("createdTime")) or None,
            "updated_at": _text(row.get("modifiedTime")) or None,
            "reviewed_at": _text(row.get("reviewedTime")) or None,
            "permalink": _text(row.get("permalink")) or None,
            "web_url": _text(row.get("portalUrl") or row.get("webUrl")) or None,
            "view_count": _int(row.get("viewCount")),
            "like_count": _int(row.get("likeCount")),
            "dislike_count": _int(row.get("dislikeCount")),
            "latest_version": _text(row.get("latestVersion")) or None,
            "tags": _string_list(row.get("tags")),
            "record_kind": "kb_article",
            "attachment_refs": _attachment_refs(row.get("attachments")),
        }

    # --- hydration --------------------------------------------------------

    THREAD_FETCH_LIMIT = 50

    async def hydrate_thread(self, thread_ref: str) -> HydratedThread:
        """Ticket conversation: email threads + agent comments, merged.

        Zoho splits a ticket's conversation across two endpoints, and
        both matter: ``/threads`` is the customer-facing email exchange,
        ``/comments`` is the internal agent discussion (the ServiceNow
        work-notes equivalent — usually where the actual diagnosis is).
        Thread *bodies* are not on the thread list response, so each
        thread needs its own detail call, bounded by
        ``THREAD_FETCH_LIMIT``.

        KB articles have no conversation: the article body ingested at
        sync time is the content, so hydration is a no-op — the same
        contract as the SapphireIMS connector and the ServiceNow alert
        rollups.
        """
        prefix, _, record_id = thread_ref.partition(":")
        if prefix == "zoho_article" or not record_id:
            return HydratedThread(
                thread_id=thread_ref,
                messages=[],
                participant_count=0,
                metadata={"hydration": "not_applicable"},
            )

        messages: list[dict] = []

        # Whether each endpoint answered, as distinct from what it
        # answered. A ticket with no conversation and a ticket we could
        # not read both produce zero messages, and downstream there is
        # nothing left to tell them apart — so the difference is decided
        # here rather than inferred later.
        listing: list[dict] = []
        listing_error: Exception | None = None
        try:
            listing = self._rows(
                await self._get(
                    f"/tickets/{record_id}/threads", {"limit": PAGE_SIZE}
                )
            )
        except Exception as exc:  # noqa: BLE001
            if _is_fatal_for_the_run(exc):
                raise
            logger.warning(
                "zoho_desk.thread_list_failed",
                ticket_id=record_id,
                error_type=type(exc).__name__,
            )
            listing_error = exc

        for entry in listing[: self.THREAD_FETCH_LIMIT]:
            entry_id = entry.get("id")
            detail = entry
            if entry_id:
                try:
                    fetched = await self._get(
                        f"/tickets/{record_id}/threads/{entry_id}"
                    )
                    if isinstance(fetched, dict):
                        detail = {**entry, **fetched}
                except Exception as exc:  # noqa: BLE001
                    logger.warning(
                        "zoho_desk.thread_detail_failed",
                        ticket_id=record_id,
                        thread_id=str(entry_id),
                        error_type=type(exc).__name__,
                    )
            body = html_to_text(detail.get("content") or detail.get("summary"))
            if not body:
                continue
            messages.append(
                {
                    "id": str(entry_id or ""),
                    "body": body,
                    "type": "thread",
                    "direction": _text(detail.get("direction")) or None,
                    "channel": _text(detail.get("channel")) or None,
                    "from": _thread_author(detail),
                    "timestamp": _text(detail.get("createdTime")) or None,
                }
            )

        comments: list[dict] = []
        comment_error: Exception | None = None
        try:
            comments = self._rows(
                await self._get(
                    f"/tickets/{record_id}/comments", {"limit": PAGE_SIZE}
                )
            )
        except Exception as exc:  # noqa: BLE001
            if _is_fatal_for_the_run(exc):
                raise
            logger.warning(
                "zoho_desk.comment_list_failed",
                ticket_id=record_id,
                error_type=type(exc).__name__,
            )
            comment_error = exc

        for comment in comments:
            body = html_to_text(comment.get("content"))
            if not body:
                continue
            messages.append(
                {
                    "id": str(comment.get("id") or ""),
                    "body": body,
                    # Public/private is the work-notes distinction; the
                    # normalizer treats "comment" like a Jira comment.
                    "type": "comment",
                    "is_public": bool(comment.get("isPublic")),
                    "from": _nested_name(comment.get("commenter"))
                    or _text(comment.get("commenterId")),
                    "timestamp": _text(comment.get("commentedTime"))
                    or _text(comment.get("createdTime"))
                    or None,
                }
            )

        if listing_error is not None and comment_error is not None:
            # Neither endpoint was readable. Returning an empty thread
            # would be a statement about the ticket, and nothing here
            # supports one — the caller stores it as hydrated and the
            # conversation is never fetched again.
            raise listing_error

        messages.sort(key=lambda m: m.get("timestamp") or "")
        participants = {m.get("from") for m in messages if m.get("from")}
        return HydratedThread(
            thread_id=thread_ref,
            messages=messages,
            participant_count=len(participants),
            metadata={"ticket_id": record_id, "message_count": len(messages)},
        )

    # --- operator diagnostics ---------------------------------------------

    # Attachment download is opt-in. Bytes cost bandwidth on every sync
    # and land in object storage under the tenant's retention policy, so
    # it is the operator's call, not a connector default. Enable with
    # ``source_config["download_attachments"] = true``.
    MAX_ATTACHMENT_BYTES = 10 * 1024 * 1024
    MAX_ATTACHMENTS_PER_RECORD = 10

    async def fetch_attachments(
        self, module: str, record_id: str, refs: list[dict]
    ) -> list[dict]:
        """Download attachment bytes for one record, base64 for ingest.

        Returns entries in the shape ``register_attachment_artifacts``
        consumes (``filename`` / ``mime_type`` / ``content_base64``) —
        the metadata-only ``attachment_refs`` shape is deliberately NOT
        that shape, because an entry without content is silently skipped
        by the registrar and would look like attachment support while
        registering nothing.

        Fail-soft per attachment: one oversized or unreadable file must
        not cost the record its other attachments.
        """
        import base64

        if not (self.source_config or {}).get("download_attachments"):
            return []

        meta = MODULES.get(module)
        if meta is None:
            return []

        out: list[dict] = []
        for ref in refs[: self.MAX_ATTACHMENTS_PER_RECORD]:
            attachment_id = ref.get("id")
            if not attachment_id:
                continue
            size = ref.get("size")
            if isinstance(size, int) and size > self.MAX_ATTACHMENT_BYTES:
                logger.info(
                    "zoho_desk.attachment_too_large",
                    record_id=record_id,
                    name=ref.get("name"),
                    size=size,
                )
                continue
            try:
                content = await self._get_bytes(
                    f"{meta['list_path']}/{record_id}/attachments/"
                    f"{attachment_id}/content"
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "zoho_desk.attachment_download_failed",
                    record_id=record_id,
                    attachment_id=str(attachment_id),
                    error_type=type(exc).__name__,
                )
                continue
            if not content or len(content) > self.MAX_ATTACHMENT_BYTES:
                continue
            out.append(
                {
                    "filename": ref.get("name") or f"attachment-{attachment_id}",
                    "mime_type": _guess_mime(ref.get("name")),
                    "content_base64": base64.b64encode(content).decode("ascii"),
                }
            )
        return out

    async def _get_bytes(self, path: str) -> bytes:
        """Binary GET. Separate from ``_get`` because attachment content
        is not JSON and must not be parsed as such."""
        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.get(
                f"{self.api_base_url}{path}", headers=await self._headers()
            )
        resp.raise_for_status()
        return resp.content

    async def probe_configuration(self, sample_limit: int = 3) -> dict:
        """Read-only setup report: which modules this token can read, what
        the granted scope string is, and whether detail calls return a
        body. Mirrors the SapphireIMS probe — operators verify the wiring
        without reading worker logs.
        """
        report: dict[str, Any] = {
            "data_center": self.data_center,
            "api_base_url": self.api_base_url,
            "org_id": self.org_id,
            "modules": {},
        }
        try:
            await self._token(force=True)
            report["token"] = {"ok": True, "granted_scope": self._granted_scope}
        except Exception as exc:  # noqa: BLE001
            report["token"] = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
            return report

        for module, meta in MODULES.items():
            entry: dict[str, Any] = {"required_scope": meta["scope"]}
            try:
                rows = self._rows(
                    await self._get(
                        meta["list_path"],
                        self._list_params(module, {"limit": sample_limit}),
                    )
                )
                entry["readable"] = True
                entry["rows_seen"] = len(rows)
                entry["count"] = await self._module_count(meta)
                if rows:
                    detailed = await self._hydrate_rows(module, rows[:1])
                    event = self._event(module, detailed[0], None) if detailed else None
                    entry["body_chars"] = (
                        len(event.content.get("description") or "") if event else 0
                    )
                    entry["sample_title"] = event.content.get("title") if event else None
            except httpx.HTTPStatusError as exc:
                entry["readable"] = False
                entry["status"] = exc.response.status_code
                entry["error"] = (
                    "SCOPE_MISMATCH — grant "
                    f"{meta['scope']} and re-issue the refresh token"
                    if exc.response.status_code == 403
                    else str(exc)
                )
            except Exception as exc:  # noqa: BLE001
                entry["readable"] = False
                entry["error"] = f"{type(exc).__name__}: {exc}"
            report["modules"][module] = entry
        return report

    def rate_limit_config(self) -> RateLimitConfig:
        # Zoho Desk meters by request *weight* per org rather than a flat
        # RPS (the live instance reports X-Rate-Limit-Remaining-v3 in the
        # hundreds of thousands). 5 rps with a small burst leaves ample
        # headroom for the per-record detail calls sync issues.
        return RateLimitConfig(requests_per_second=5.0, burst_size=10)


# --- module-level helpers ----------------------------------------------------

ZOHO_TS_FORMAT = "%Y-%m-%dT%H:%M:%S.000Z"

# How many boundary ids a checkpoint carries. Bounded so a single
# timestamp shared by a mass update cannot grow the checkpoint without
# limit. Overflowing degrades to re-delivering the boundary group next
# tick, which dedup absorbs — never to skipping it.
MAX_BOUNDARY_IDS = 500

# Before any checkpoint exists. Compared lexically against ISO-8601
# timestamps, so it must be ISO-shaped rather than an empty string.
EPOCH_SENTINEL = "0000-01-01T00:00:00.000Z"


def _modified_time(row: dict) -> str:
    """Sort key: the raw ISO-8601 string, compared lexically.

    ISO-8601 with a fixed ``.000Z`` suffix sorts lexically exactly as it
    sorts chronologically, so string comparison matches the order the
    API returns without parsing every row.
    """
    return str(row.get("modifiedTime") or "")


def _read_checkpoint(data: dict | None) -> tuple[str, set[str]]:
    """``(boundary_timestamp, ids_already_seen_at_it)``.

    Tolerates the singular ``last_id`` form so a checkpoint written by
    an earlier build keeps working instead of resyncing from scratch.
    """
    d = data or {}
    stop_at_time = str(d.get("last_updated") or EPOCH_SENTINEL)
    raw_ids = d.get("last_ids")
    if isinstance(raw_ids, list):
        ids = {str(value) for value in raw_ids if value not in (None, "")}
    else:
        ids = set()
    legacy = d.get("last_id")
    if legacy not in (None, ""):
        ids.add(str(legacy))
    return stop_at_time, ids


def _checkpoint_data(max_time: str, ids_at_max: set[str]) -> dict:
    ids = sorted(ids_at_max)
    if len(ids) > MAX_BOUNDARY_IDS:
        logger.warning(
            "zoho_desk.boundary_ids_truncated",
            timestamp=max_time,
            seen=len(ids),
            kept=MAX_BOUNDARY_IDS,
        )
        ids = ids[:MAX_BOUNDARY_IDS]
    return {"last_updated": max_time, "last_ids": ids}


def _text(value: object) -> str:
    return str(value).strip() if value not in (None, "") else ""


def _int(value: object) -> int | None:
    try:
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def _nested(value: object, key: str) -> str | None:
    """Zoho embeds related records as objects (``assignee``, ``contact``,
    ``product``). Absent relations arrive as ``None``, not as ``{}``."""
    if isinstance(value, dict):
        return _text(value.get(key)) or None
    return None


def _nested_name(value: object) -> str | None:
    if not isinstance(value, dict):
        return None
    first = _text(value.get("firstName"))
    last = _text(value.get("lastName"))
    full = " ".join(part for part in (first, last) if part)
    return _text(value.get("name")) or full or _text(value.get("email")) or None


def _string_list(value: object, limit: int = 20) -> list[str]:
    """Tags and related-ticket refs arrive as a list of strings, a list of
    ``{"name": ...}`` objects, or a comma-joined string."""
    out: list[str] = []
    if isinstance(value, list):
        for item in value[:limit]:
            if isinstance(item, dict):
                text = _text(item.get("name") or item.get("id") or item.get("ticketNumber"))
            else:
                text = _text(item)
            if text and text not in out:
                out.append(text)
    elif isinstance(value, str):
        for part in value.split(",")[:limit]:
            text = part.strip()
            if text and text not in out:
                out.append(text)
    elif value not in (None, ""):
        out.append(str(value))
    return out


def _attachment_refs(value: object, limit: int = 25) -> list[dict]:
    """Attachment *metadata* only — deliberately not the bytes.

    Stored under ``attachment_refs`` rather than ``attachments`` because
    ``register_attachment_artifacts`` treats an ``attachments`` entry
    without content or a storage key as a no-op, so filing metadata
    there would look like attachment support while registering nothing.
    Downloading attachment bodies at sync time is a bandwidth and
    retention decision for the operator, not a connector default.
    """
    if not isinstance(value, list):
        return []
    out: list[dict] = []
    for item in value[:limit]:
        if not isinstance(item, dict):
            continue
        name = _text(item.get("name") or item.get("fileName"))
        if not name:
            continue
        out.append(
            {
                "id": _text(item.get("id")) or None,
                "name": name,
                "size": _int(item.get("size")),
                "href": _text(item.get("href")) or None,
            }
        )
    return out


_MIME_BY_SUFFIX = {
    "pdf": "application/pdf",
    "png": "image/png",
    "jpg": "image/jpeg",
    "jpeg": "image/jpeg",
    "gif": "image/gif",
    "txt": "text/plain",
    "log": "text/plain",
    "csv": "text/csv",
    "json": "application/json",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
}


def _guess_mime(filename: object) -> str:
    """Extension-based content type.

    Zoho's attachment list does not carry a MIME type, and the artifact
    parser dispatches on it — an ``application/octet-stream`` default
    would send every PDF down the unsupported path.
    """
    name = str(filename or "").lower()
    suffix = name.rsplit(".", 1)[-1] if "." in name else ""
    return _MIME_BY_SUFFIX.get(suffix, "application/octet-stream")


def _custom_fields(value: object, limit: int = 40) -> dict:
    """Zoho custom fields arrive under ``cf`` keyed ``cf_<slug>``. Kept
    whole (bounded) rather than mapped: which custom fields matter is
    per-portal, and dropping them here would make them unrecoverable
    downstream."""
    if not isinstance(value, dict):
        return {}
    out: dict[str, Any] = {}
    for key, raw in list(value.items())[:limit]:
        if raw in (None, "", [], {}):
            continue
        out[str(key)] = raw if isinstance(raw, (str, int, float, bool)) else str(raw)
    return out


def _thread_author(detail: dict) -> str | None:
    author = detail.get("author")
    if isinstance(author, dict):
        return _nested_name(author) or _text(author.get("email")) or None
    return _text(detail.get("fromEmailAddress")) or None


def parse_zoho_datetime(value: object) -> datetime | None:
    """Zoho serializes timestamps as ``2026-08-03T05:12:05.000Z``.

    Tolerant of offset forms and bare ``YYYY-MM-DD HH:MM:SS`` because
    custom-field and older-API values are not always normalized; naive
    results are stamped UTC, which is what the API documents.
    """
    if value in (None, ""):
        return None
    if isinstance(value, (int, float)):
        seconds = float(value)
        if seconds > 1e12:  # epoch millis
            seconds /= 1000.0
        try:
            return datetime.fromtimestamp(seconds, tz=UTC)
        except (OverflowError, OSError, ValueError):
            return None
    text = str(value).strip()
    for parser in (
        lambda t: datetime.fromisoformat(t.replace("Z", "+00:00")),
        lambda t: datetime.strptime(t, "%Y-%m-%d %H:%M:%S"),
    ):
        try:
            parsed = parser(text)
        except ValueError:
            continue
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
    return None
