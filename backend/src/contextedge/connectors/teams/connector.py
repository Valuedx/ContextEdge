"""Microsoft Teams connector via Microsoft Graph API."""

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

GRAPH_BASE = "https://graph.microsoft.com/v1.0"


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
        if self._token and self._token_expires and self._token_expires > datetime.now(timezone.utc):
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
            self._token_expires = datetime.now(timezone.utc)
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
                    content={
                        "body": msg.get("body", {}).get("content", ""),
                        "content_type": msg.get("body", {}).get("contentType", "text"),
                        "subject": msg.get("subject"),
                        "from": msg.get("from", {}).get("user", {}).get("displayName"),
                        "from_email": msg.get("from", {}).get("user", {}).get("email"),
                        "importance": msg.get("importance"),
                    },
                    thread_id=msg["id"],
                    timestamp=datetime.fromisoformat(msg["createdDateTime"].replace("Z", "+00:00")),
                    metadata={"team_id": team_id, "channel_id": channel_id},
                )
            )

        next_link = data.get("@odata.nextLink")
        skip_token = None
        if next_link and "$skiptoken=" in next_link:
            skip_token = next_link.split("$skiptoken=")[-1].split("&")[0]

        return BackfillResult(
            events=events,
            new_checkpoint=Checkpoint(data={"skip_token": skip_token}) if skip_token else None,
            items_processed=len(events),
            has_more=next_link is not None,
        )

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
                    content={
                        "body": msg.get("body", {}).get("content", ""),
                        "content_type": msg.get("body", {}).get("contentType", "text"),
                        "subject": msg.get("subject"),
                        "from": msg.get("from", {}).get("user", {}).get("displayName"),
                    },
                    thread_id=msg["id"],
                    timestamp=datetime.fromisoformat(msg["createdDateTime"].replace("Z", "+00:00")) if msg.get("createdDateTime") else None,
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
        data = await self._graph_get(
            f"/teams/{team_id}/channels/{channel_id}/messages/{message_id}/replies",
        )
        messages = []
        for reply in data.get("value", []):
            messages.append({
                "id": reply["id"],
                "body": reply.get("body", {}).get("content", ""),
                "from": reply.get("from", {}).get("user", {}).get("displayName"),
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
