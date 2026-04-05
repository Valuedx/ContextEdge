"""Gmail connector via Google Workspace API.

Uses service account with domain-wide delegation for shared mailbox access.
Supports history-based incremental sync and thread-centric processing.
"""

from datetime import datetime, timezone
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

    async def _get_access_token(self, user_email: str) -> str:
        """Get delegated access token for the target mailbox.

        In production, this uses google-auth with service account JWT
        and domain-wide delegation. Simplified here for structure.
        """
        if self._access_token:
            return self._access_token

        from google.oauth2 import service_account  # type: ignore[import-untyped]
        from google.auth.transport.requests import Request  # type: ignore[import-untyped]

        creds = service_account.Credentials.from_service_account_info(
            self.credentials.get("service_account_json", {}),
            scopes=["https://www.googleapis.com/auth/gmail.readonly"],
            subject=user_email,
        )
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

    async def validate_credentials(self) -> CredentialStatus:
        try:
            user_email = self.source_config.get("mailbox_email", "")
            if not user_email:
                return CredentialStatus(valid=False, message="No mailbox_email configured")
            await self._get_access_token(user_email)
            return CredentialStatus(valid=True, message="Gmail API access verified")
        except Exception as e:
            return CredentialStatus(valid=False, message=str(e))

    async def discover_objects(self) -> list[DiscoveredObject]:
        mailboxes = self.source_config.get("mailbox_emails", [])
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
            except Exception:
                objects.append(
                    DiscoveredObject(
                        external_id=email,
                        object_type="gmail_mailbox",
                        display_name=f"{email} (access error)",
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
        query = f"after:{after_epoch} before:{before_epoch}"

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
                    thread_id=thread_summary["id"],
                    timestamp=datetime.fromtimestamp(
                        int(messages[0].get("internalDate", "0")) / 1000,
                        tz=timezone.utc,
                    ) if messages else None,
                    metadata={"mailbox": user_email},
                )
            )

        page_token = data.get("nextPageToken")
        history_id = data.get("resultSizeEstimate")

        return BackfillResult(
            events=events,
            new_checkpoint=Checkpoint(data={"page_token": page_token}) if page_token else None,
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
        history_id = checkpoint.data.get("history_id")
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

        for tid in changed_thread_ids:
            events.append(
                IngestionEvent(
                    external_id=tid,
                    source_type="gmail",
                    object_type="email_thread",
                    content={"thread_id": tid, "needs_hydration": True},
                    thread_id=tid,
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
            headers = {h["name"].lower(): h["value"] for h in msg.get("payload", {}).get("headers", [])}
            body = ""
            payload = msg.get("payload", {})
            if payload.get("body", {}).get("data"):
                import base64
                body = base64.urlsafe_b64decode(payload["body"]["data"]).decode("utf-8", errors="replace")
            elif payload.get("parts"):
                for part in payload["parts"]:
                    if part.get("mimeType") == "text/plain" and part.get("body", {}).get("data"):
                        body = base64.urlsafe_b64decode(part["body"]["data"]).decode("utf-8", errors="replace")
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
