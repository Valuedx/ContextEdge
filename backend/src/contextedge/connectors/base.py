from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


@dataclass
class CredentialStatus:
    valid: bool
    message: str = ""
    expires_at: datetime | None = None


@dataclass
class DiscoveredObject:
    external_id: str
    object_type: str
    display_name: str
    object_path: str | None = None
    owner_hint: str | None = None
    sensitivity_label: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class DateRange:
    start: datetime
    end: datetime


@dataclass
class Checkpoint:
    data: dict[str, Any]
    captured_at: datetime | None = None


@dataclass
class IngestionEvent:
    external_id: str
    source_type: str
    object_type: str
    content: dict[str, Any]
    thread_id: str | None = None
    timestamp: datetime | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class BackfillResult:
    events: list[IngestionEvent]
    new_checkpoint: Checkpoint | None = None
    items_processed: int = 0
    has_more: bool = False


@dataclass
class ChangeResult:
    events: list[IngestionEvent]
    new_checkpoint: Checkpoint
    items_processed: int = 0


@dataclass
class HydratedThread:
    thread_id: str
    messages: list[dict[str, Any]]
    participant_count: int = 0
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class RateLimitConfig:
    requests_per_second: float = 10.0
    burst_size: int = 20
    retry_after_header: str = "Retry-After"


class BaseConnector(ABC):
    """Abstract interface for all source connectors."""

    def __init__(self, source_config: dict[str, Any], credentials: dict[str, Any]):
        self.source_config = source_config
        self.credentials = credentials
        # Cooperative stop, set by the sync job. A backfill can spend a
        # quarter of an hour inside ONE `backfill()` call — the live Zoho
        # corpus took 913 seconds for a page walk plus 1,855 detail fetches
        # — so a control signal checked only between invocations would do
        # nothing for that entire time. Connectors that honour it call
        # `await self._check_control()` inside their loops; the default is
        # a no-op, so a connector that has not been taught still behaves
        # exactly as before.
        self._control_check: Any = None

    def set_control_check(self, check: Any) -> None:
        """Install the callback the loops consult. `check()` returns
        ``"pause"``, ``"cancel"``, or a falsy value to continue."""
        self._control_check = check

    async def _check_control(self) -> str | None:
        """The current signal, or None. Never raises: a control channel
        that fails must not take the sync down with it."""
        if self._control_check is None:
            return None
        try:
            return await self._control_check()
        except Exception:  # noqa: BLE001
            return None

    @abstractmethod
    async def validate_credentials(self) -> CredentialStatus:
        ...

    @abstractmethod
    async def discover_objects(self) -> list[DiscoveredObject]:
        ...

    @abstractmethod
    async def backfill(
        self,
        object_id: str,
        object_type: str,
        window: DateRange,
        checkpoint: Checkpoint | None = None,
    ) -> BackfillResult:
        ...

    @abstractmethod
    async def fetch_changes(
        self,
        object_id: str,
        object_type: str,
        checkpoint: Checkpoint,
    ) -> ChangeResult:
        ...

    @abstractmethod
    async def hydrate_thread(self, thread_ref: str) -> HydratedThread:
        ...

    def rate_limit_config(self) -> RateLimitConfig:
        return RateLimitConfig()
