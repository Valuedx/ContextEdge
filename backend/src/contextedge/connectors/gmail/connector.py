"""Gmail connector via Google Workspace API.

Uses service account with domain-wide delegation for shared mailbox access.
Supports history-based incremental sync and thread-centric processing.
"""

from datetime import UTC, datetime
from typing import Any

import httpx

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

GMAIL_BASE = "https://gmail.googleapis.com/gmail/v1"


class GmailConnector(BaseConnector):
    """Connector for Gmail shared mailboxes via Google Workspace API."""

    def __init__(self, source_config: dict[str, Any], credentials: dict[str, Any]):
        super().__init__(source_config, credentials)
        self._access_token: str | None = None

    async def _get_access_token(self, user_email: str | None = None) -> str:
        """Get access token for Gmail API.

        Supports both:
        1. Service Account with Domain-Wide Delegation (requires user_email to impersonate)
        2. Personal OAuth2 Token (Authorized User flow, user_email ignored)
        """
        if self._access_token:
            return self._access_token

        from google.auth.transport.requests import Request  # type: ignore[import-untyped]
        from google.oauth2 import service_account  # type: ignore[import-untyped]
        from google.oauth2.credentials import Credentials  # type: ignore[import-untyped]

        scopes = ["https://www.googleapis.com/auth/gmail.readonly"]

        # 1. Try Personal OAuth2 Token (token.json)
        user_info = self.credentials.get("user_oauth2_info")
        if user_info:
            creds = Credentials.from_authorized_user_info(user_info, scopes=scopes)
        else:
            # 2. Fallback to Service Account
            creds = service_account.Credentials.from_service_account_info(
                self.credentials.get("service_account_json", {}),
                scopes=scopes,
                subject=user_email,
            )

        if not creds.valid:
            creds.refresh(Request())

        self._access_token = creds.token
        return self._access_token

    async def _gmail_get(self, path: str, user_email: str, params: dict | None = None) -> dict:
        token = await self._get_access_token(user_email)
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                f"{GMAIL_BASE}{path}",
                headers={"Authorization": f"Bearer {token}"},
                params=params,
            )
            resp.raise_for_status()
            return resp.json()

    def _get_mailbox_list(self) -> list[str]:
        """Return a normalised list of mailbox emails from config.

        Handles both the singular key (mailbox_email) that the frontend
        sends and the plural key (mailbox_emails) for backwards compatibility.
        """
        # Prefer the plural form if present
        plural = self.source_config.get("mailbox_emails")
        if plural:
            if isinstance(plural, list):
                return [e for e in plural if e]
            return [str(plural)]

        # Fall back to the singular form sent by the Add Source dialog
        singular = self.source_config.get("mailbox_email")
        if singular:
            return [str(singular)]

        return []

    def _get_relevance_keywords(self) -> list[str]:
        """Get keywords from config or use defaults."""
        keywords = self.source_config.get("relevance_keywords")
        if not keywords:
            return []
        if isinstance(keywords, str):
            return [k.strip() for k in keywords.split(",") if k.strip()]
        return [str(k).strip() for k in keywords if str(k).strip()]

    def _matches_keywords(self, text: str, keywords: list[str]) -> bool:
        """Check if any keyword is present in the text."""
        if not keywords:
            return True
        text_lower = text.lower()
        return any(kw.lower() in text_lower for kw in keywords)

    async def validate_credentials(self) -> CredentialStatus:
        try:
            mailboxes = self._get_mailbox_list()
            # For personal accounts, we can validate using "me" profile
            if not mailboxes and self.credentials.get("user_oauth2_info"):
                await self._get_access_token()
                return CredentialStatus(valid=True, message="Personal Gmail access verified")

            if not mailboxes:
                return CredentialStatus(valid=False, message="No mailbox_email configured")

            await self._get_access_token(mailboxes[0])
            return CredentialStatus(valid=True, message="Gmail API access verified")
        except Exception as e:
            return CredentialStatus(valid=False, message=str(e))

    async def discover_objects(self) -> list[DiscoveredObject]:
        mailboxes = self._get_mailbox_list()

        # For personal OAuth2, if no mailbox is configured, we discover the "me" profile
        if not mailboxes and self.credentials.get("user_oauth2_info"):
            mailboxes = ["me"]

        objects: list[DiscoveredObject] = []
        for email in mailboxes:
            try:
                profile = await self._gmail_get(f"/users/{email}/profile", email)
                objects.append(
                    DiscoveredObject(
                        external_id=email,
                        object_type="gmail_mailbox",
                        display_name=email,
                        metadata={
                            "messages_total": profile.get("messagesTotal", 0),
                            "threads_total": profile.get("threadsTotal", 0),
                            "history_id": profile.get("historyId"),
                        },
                    )
                )
            except Exception as exc:
                objects.append(
                    DiscoveredObject(
                        external_id=email,
                        object_type="gmail_mailbox",
                        display_name=f"{email} (access error)",
                        metadata={"error": str(exc)},
                    )
                )
        return objects

    async def backfill(
        self,
        object_id: str,
        object_type: str,
        window: DateRange,
        checkpoint: Checkpoint | None = None,
    ) -> BackfillResult:
        user_email = object_id
        events: list[IngestionEvent] = []

        after_epoch = int(window.start.timestamp())
        before_epoch = int(window.end.timestamp())

        keywords = self._get_relevance_keywords()
        query = f"after:{after_epoch} before:{before_epoch}"
        if keywords:
            kw_query = " OR ".join(keywords)
            query += f" ({kw_query})"

        params: dict[str, str] = {"q": query, "maxResults": "50"}
        if checkpoint and checkpoint.data.get("page_token"):
            params["pageToken"] = checkpoint.data["page_token"]

        data = await self._gmail_get(f"/users/{user_email}/threads", user_email, params)

        for thread_summary in data.get("threads", []):
            thread_data = await self._gmail_get(
                f"/users/{user_email}/threads/{thread_summary['id']}",
                user_email,
                {"format": "metadata", "metadataHeaders": "Subject,From,To,Date"},
            )
            messages = thread_data.get("messages", [])
            headers_map = {}
            for msg in messages:
                for h in msg.get("payload", {}).get("headers", []):
                    headers_map[h["name"].lower()] = h["value"]

            events.append(
                IngestionEvent(
                    external_id=thread_summary["id"],
                    source_type="gmail",
                    object_type="email_thread",
                    content={
                        "subject": headers_map.get("subject", ""),
                        "from": headers_map.get("from", ""),
                        "to": headers_map.get("to", ""),
                        "message_count": len(messages),
                        "snippet": thread_data.get("snippet", ""),
                    },
                    thread_id=f"{user_email}:{thread_summary['id']}",
                    timestamp=datetime.fromtimestamp(
                        int(messages[0].get("internalDate", "0")) / 1000,
                        tz=UTC,
                    ) if messages else None,
                    metadata={"mailbox": user_email},
                )
            )

        page_token = data.get("nextPageToken")

        if page_token:
            new_checkpoint = Checkpoint(data={"page_token": page_token})
        else:
            profile = await self._gmail_get(f"/users/{user_email}/profile", user_email)
            new_checkpoint = Checkpoint(data={"history_id": profile["historyId"]})

        return BackfillResult(
            events=events,
            new_checkpoint=new_checkpoint,
            items_processed=len(events),
            has_more=page_token is not None,
        )

    async def fetch_changes(
        self,
        object_id: str,
        object_type: str,
        checkpoint: Checkpoint,
    ) -> ChangeResult:
        user_email = object_id
        history_id = checkpoint.data.get("history_id") if checkpoint else None
        events: list[IngestionEvent] = []

        if not history_id:
            profile = await self._gmail_get(f"/users/{user_email}/profile", user_email)
            return ChangeResult(
                events=[],
                new_checkpoint=Checkpoint(data={"history_id": profile["historyId"]}),
            )

        data = await self._gmail_get(
            f"/users/{user_email}/history",
            user_email,
            {"startHistoryId": str(history_id), "historyTypes": "messageAdded"},
        )

        changed_thread_ids: set[str] = set()
        for history in data.get("history", []):
            for added in history.get("messagesAdded", []):
                tid = added.get("message", {}).get("threadId")
                if tid:
                    changed_thread_ids.add(tid)

        keywords = self._get_relevance_keywords()
        for tid in changed_thread_ids:
            # For incremental sync, we check metadata first if keywords are set
            if keywords:
                try:
                    thread_meta = await self._gmail_get(
                        f"/users/{user_email}/threads/{tid}",
                        user_email,
                        {"format": "metadata", "metadataHeaders": "Subject"},
                    )
                    snippet = thread_meta.get("snippet", "")
                    subject = ""
                    first_payload = thread_meta.get("messages", [{}])[0].get("payload", {})
                    for h in first_payload.get("headers", []):
                        if h["name"].lower() == "subject":
                            subject = h["value"]

                    if not self._matches_keywords(f"{subject} {snippet}", keywords):
                        continue
                except Exception:
                    # If we can't fetch metadata, conservative approach is to ingest or skip
                    # Here we skip to avoid noise if filtering is requested
                    continue

            events.append(
                IngestionEvent(
                    external_id=tid,
                    source_type="gmail",
                    object_type="email_thread",
                    content={"thread_id": tid, "needs_hydration": True},
                    thread_id=f"{user_email}:{tid}",
                    metadata={"mailbox": user_email},
                )
            )

        new_history_id = data.get("historyId", history_id)
        return ChangeResult(
            events=events,
            new_checkpoint=Checkpoint(data={"history_id": new_history_id}),
            items_processed=len(events),
        )

    async def hydrate_thread(self, thread_ref: str) -> HydratedThread:
        parts = thread_ref.split(":")
        user_email, thread_id = parts[0], parts[1]

        data = await self._gmail_get(
            f"/users/{user_email}/threads/{thread_id}",
            user_email,
            {"format": "full"},
        )

        messages = []
        participants: set[str] = set()
        for msg in data.get("messages", []):
            headers = {
                h["name"].lower(): h["value"]
                for h in msg.get("payload", {}).get("headers", [])
            }
            body = ""
            payload = msg.get("payload", {})
            if payload.get("body", {}).get("data"):
                import base64
                body = base64.urlsafe_b64decode(payload["body"]["data"]).decode(
                    "utf-8", errors="replace"
                )
            elif payload.get("parts"):
                for part in payload["parts"]:
                    if part.get("mimeType") == "text/plain" and part.get("body", {}).get("data"):
                        body = base64.urlsafe_b64decode(part["body"]["data"]).decode(
                            "utf-8", errors="replace"
                        )
                        break

            from_addr = headers.get("from", "")
            participants.add(from_addr)
            messages.append({
                "id": msg["id"],
                "from": from_addr,
                "to": headers.get("to", ""),
                "subject": headers.get("subject", ""),
                "body": body,
                "timestamp": msg.get("internalDate"),
            })

        return HydratedThread(
            thread_id=thread_ref,
            messages=messages,
            participant_count=len(participants),
        )

    def rate_limit_config(self) -> RateLimitConfig:
        return RateLimitConfig(requests_per_second=5.0, burst_size=10)
