"""Microsoft Teams connector via Microsoft Graph API."""

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

GRAPH_BASE = "https://graph.microsoft.com/v1.0"


def _message_content(msg: dict) -> dict:
    """Conversational metadata the resolver layers depend on — captured
    at ingest because the Graph API already returns it and discarding it
    forecloses reply inheritance, bot/human authority, correction
    handling, and edit auditing downstream.

    - ``reply_to_id``: the deterministic thread-inheritance anchor.
    - ``is_bot`` / ``from_application``: bot cards (ServiceNow /
      monitoring / automation notifications) are structured payloads,
      not human assertions — downstream must tell them apart.
    - ``last_edited_at`` / ``is_deleted``: edits can invert meaning
      ("prod" → "UAT"); deletions must mark evidence withdrawn, not
      vanish silently.
    - ``attachments`` / ``mentions``: slimmed; screenshots and pasted
      files are frequent carriers of the only useful identifiers.
    """
    from_block = msg.get("from") or {}
    application = from_block.get("application") or {}
    user = from_block.get("user") or {}
    content = {
        "message_id": msg.get("id"),
        "reply_to_id": msg.get("replyToId"),
        "message_type": msg.get("messageType"),
        "body": (msg.get("body") or {}).get("content", ""),
        "content_type": (msg.get("body") or {}).get("contentType", "text"),
        "subject": msg.get("subject"),
        "from": user.get("displayName"),
        "from_email": user.get("email"),
        "is_bot": bool(application),
        "importance": msg.get("importance"),
        "last_edited_at": msg.get("lastEditedDateTime"),
        "is_deleted": bool(msg.get("deletedDateTime")),
    }
    if application:
        content["from_application"] = application.get("displayName")
    if msg.get("deletedDateTime"):
        content["deleted_at"] = msg.get("deletedDateTime")
    attachments = [
        {"name": a.get("name"), "content_type": a.get("contentType")}
        for a in (msg.get("attachments") or [])[:10]
        if isinstance(a, dict)
    ]
    if attachments:
        content["attachments"] = attachments
    mentions = [
        (m.get("mentioned") or {}).get("user", {}).get("displayName")
        for m in (msg.get("mentions") or [])[:10]
        if isinstance(m, dict)
    ]
    mentions = [m for m in mentions if m]
    if mentions:
        content["mentions"] = mentions
    return content


class TeamsConnector(BaseConnector):
    """Connector for Microsoft Teams via Graph API.

    Supports team/channel discovery, delta-based incremental sync,
    and thread hydration with replies.
    """

    def __init__(self, source_config: dict[str, Any], credentials: dict[str, Any]):
        super().__init__(source_config, credentials)
        self._token: str | None = None
        self._token_expires: datetime | None = None

    async def _get_token(self) -> str:
        if self._token and self._token_expires and self._token_expires > datetime.now(UTC):
            return self._token

        tenant_id = self.credentials["tenant_id"]
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"https://login.microsoftonline.com/{tenant_id}/oauth2/v2.0/token",
                data={
                    "grant_type": "client_credentials",
                    "client_id": self.credentials["client_id"],
                    "client_secret": self.credentials["client_secret"],
                    "scope": "https://graph.microsoft.com/.default",
                },
            )
            resp.raise_for_status()
            data = resp.json()
            self._token = data["access_token"]
            self._token_expires = datetime.now(UTC)
            return self._token

    async def _graph_get(self, path: str, params: dict | None = None) -> dict:
        token = await self._get_token()
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                f"{GRAPH_BASE}{path}",
                headers={"Authorization": f"Bearer {token}"},
                params=params,
            )
            resp.raise_for_status()
            return resp.json()

    async def validate_credentials(self) -> CredentialStatus:
        try:
            await self._get_token()
            await self._graph_get("/teams", params={"$top": "1"})
            return CredentialStatus(valid=True, message="Graph API access verified")
        except Exception as e:
            return CredentialStatus(valid=False, message=str(e))

    async def discover_objects(self) -> list[DiscoveredObject]:
        objects: list[DiscoveredObject] = []
        data = await self._graph_get("/teams", params={"$select": "id,displayName,description"})
        for team in data.get("value", []):
            channels = await self._graph_get(
                f"/teams/{team['id']}/channels",
                params={"$select": "id,displayName,membershipType"},
            )
            for ch in channels.get("value", []):
                objects.append(
                    DiscoveredObject(
                        external_id=f"{team['id']}:{ch['id']}",
                        object_type="teams_channel",
                        display_name=f"{team['displayName']} / {ch['displayName']}",
                        object_path=f"/teams/{team['id']}/channels/{ch['id']}",
                        metadata={
                            "team_id": team["id"],
                            "team_name": team["displayName"],
                            "channel_id": ch["id"],
                            "membership_type": ch.get("membershipType"),
                        },
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
        team_id, channel_id = object_id.split(":")
        events: list[IngestionEvent] = []

        resume_url = checkpoint.data.get("next_link") if checkpoint else None
        if resume_url:
            data = await self._graph_get(resume_url.replace(GRAPH_BASE, ""))
        else:
            params: dict[str, str] = {
                "$orderby": "lastModifiedDateTime desc",
                "$top": "50",
                "$filter": f"lastModifiedDateTime ge {window.start.isoformat()}",
            }
            if checkpoint and checkpoint.data.get("skip_token"):
                params["$skiptoken"] = checkpoint.data["skip_token"]

            data = await self._graph_get(
                f"/teams/{team_id}/channels/{channel_id}/messages",
                params=params,
            )

        for msg in data.get("value", []):
            events.append(
                IngestionEvent(
                    external_id=msg["id"],
                    source_type="teams",
                    object_type="channel_message",
                    content=_message_content(msg),
                    thread_id=f"{team_id}:{channel_id}:{msg['id']}",
                    timestamp=datetime.fromisoformat(msg["createdDateTime"].replace("Z", "+00:00")),
                    metadata={"team_id": team_id, "channel_id": channel_id},
                )
            )

        next_link = data.get("@odata.nextLink")
        skip_token = None
        if next_link and "$skiptoken=" in next_link:
            skip_token = next_link.split("$skiptoken=")[-1].split("&")[0]

        if skip_token:
            new_checkpoint = Checkpoint(data={"skip_token": skip_token})
        elif not next_link:
            delta_link = await self._fetch_initial_delta_link(team_id, channel_id)
            new_checkpoint = Checkpoint(data={"delta_link": delta_link})
        else:
            new_checkpoint = Checkpoint(data={"next_link": next_link})

        return BackfillResult(
            events=events,
            new_checkpoint=new_checkpoint,
            items_processed=len(events),
            has_more=next_link is not None,
        )

    async def _fetch_initial_delta_link(self, team_id: str, channel_id: str) -> str:
        """Follow delta pagination until we obtain a stable deltaLink."""
        data = await self._graph_get(
            f"/teams/{team_id}/channels/{channel_id}/messages/delta",
        )
        max_pages = 20
        for _ in range(max_pages):
            if "@odata.deltaLink" in data:
                return data["@odata.deltaLink"]
            nxt = data.get("@odata.nextLink")
            if not nxt:
                break
            data = await self._graph_get(nxt.replace(GRAPH_BASE, ""))
        return data.get("@odata.deltaLink", "")

    async def fetch_changes(
        self,
        object_id: str,
        object_type: str,
        checkpoint: Checkpoint,
    ) -> ChangeResult:
        team_id, channel_id = object_id.split(":")
        events: list[IngestionEvent] = []

        delta_link = checkpoint.data.get("delta_link")
        if delta_link:
            data = await self._graph_get(delta_link.replace(GRAPH_BASE, ""))
        else:
            data = await self._graph_get(
                f"/teams/{team_id}/channels/{channel_id}/messages/delta",
            )

        for msg in data.get("value", []):
            events.append(
                IngestionEvent(
                    external_id=msg["id"],
                    source_type="teams",
                    object_type="channel_message",
                    content=_message_content(msg),
                    thread_id=f"{team_id}:{channel_id}:{msg['id']}",
                    timestamp=(
                        datetime.fromisoformat(
                            msg["createdDateTime"].replace("Z", "+00:00")
                        )
                        if msg.get("createdDateTime")
                        else None
                    ),
                    metadata={"team_id": team_id, "channel_id": channel_id},
                )
            )

        new_delta = data.get("@odata.deltaLink", delta_link)
        return ChangeResult(
            events=events,
            new_checkpoint=Checkpoint(data={"delta_link": new_delta}),
            items_processed=len(events),
        )

    async def hydrate_thread(self, thread_ref: str) -> HydratedThread:
        parts = thread_ref.split(":")
        team_id, channel_id, message_id = parts[0], parts[1], parts[2]

        root = await self._graph_get(
            f"/teams/{team_id}/channels/{channel_id}/messages/{message_id}",
        )
        root_content = _message_content(root)
        messages = [{
            "id": root["id"],
            "body": root_content["body"],
            "subject": root_content["subject"],
            "from": root_content["from"],
            "is_bot": root_content["is_bot"],
            "reply_to_id": None,
            "timestamp": root.get("createdDateTime"),
        }]

        replies_data = await self._graph_get(
            f"/teams/{team_id}/channels/{channel_id}/messages/{message_id}/replies",
        )
        for reply in replies_data.get("value", []):
            reply_content = _message_content(reply)
            messages.append({
                "id": reply["id"],
                "body": reply_content["body"],
                "from": reply_content["from"],
                "is_bot": reply_content["is_bot"],
                "reply_to_id": reply_content["reply_to_id"] or root["id"],
                "timestamp": reply.get("createdDateTime"),
            })

        participants = {m.get("from") for m in messages if m.get("from")}
        return HydratedThread(
            thread_id=thread_ref,
            messages=messages,
            participant_count=len(participants),
        )

    def rate_limit_config(self) -> RateLimitConfig:
        return RateLimitConfig(requests_per_second=5.0, burst_size=10)
