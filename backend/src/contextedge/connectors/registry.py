from dataclasses import dataclass
from typing import Any

from contextedge.connectors.base import BaseConnector

CONNECTOR_CLASSES: dict[str, type[BaseConnector]] = {}


@dataclass(frozen=True)
class SourceTypeInfo:
    """One entry in the catalog the source-creation UI renders from."""

    source_type: str
    label: str
    connector_available: bool
    # "available" — a connector is registered and the type can sync.
    # "planned"   — the API accepts the type but no connector exists yet;
    #               creating one would succeed and then fail at sync.
    # "manual"    — no connector by design (local_file is an upload path).
    status: str
    description: str = ""


# Display metadata for every source type the API accepts. This exists so
# the "which sources can I add" question has ONE answer.
#
# It was added because the two lists had silently drifted apart in both
# directions: the picker offered Confluence, SharePoint, and Exchange —
# none of which have a connector, so creating one succeeded and then died
# at sync with "Unknown source type" — while SapphireIMS and Zoho Desk
# had working, tested connectors that could not be selected at all.
#
# ``connector_available`` is computed from the registry rather than
# written here, so a new connector cannot be registered and left out of
# the picker, and a label cannot claim a connector that does not exist.
# test_source_type_catalog.py asserts the catalog and the registry agree.
_SOURCE_TYPE_LABELS: dict[str, tuple[str, str, str]] = {
    # source_type: (label, status_when_no_connector, description)
    "local_file": (
        "Local Directory / Files",
        "manual",
        "Upload documents directly; no external system to connect.",
    ),
    "gmail": ("Gmail", "planned", "Shared or delegated mailbox via a service account."),
    "teams": ("MS Teams", "planned", "Channel messages and replies via Microsoft Graph."),
    "servicenow": ("ServiceNow", "planned", "Incidents, problems, changes, requests, and KB."),
    "jira_sm": ("Jira Service Management", "planned", "Issues, comments, and linked records."),
    "sapphireims": (
        "SapphireIMS",
        "planned",
        "Service desk tickets via a config-mapped REST contract.",
    ),
    "zoho_desk": (
        "Zoho Desk",
        "planned",
        "Tickets and knowledge-base articles. Needs a per-module OAuth scope.",
    ),
    "confluence": ("Confluence", "planned", "Not yet implemented."),
    "sharepoint": ("SharePoint", "planned", "Not yet implemented."),
    "exchange": ("Exchange", "planned", "Not yet implemented."),
}


def source_type_catalog() -> list[SourceTypeInfo]:
    """Every source type the API accepts, with whether it can actually sync.

    Order follows ``_SOURCE_TYPE_LABELS`` so the picker is stable across
    reloads rather than reordering with dict iteration.
    """
    available = set(supported_source_types())
    catalog: list[SourceTypeInfo] = []
    for source_type, (label, absent_status, description) in _SOURCE_TYPE_LABELS.items():
        has_connector = source_type in available
        catalog.append(
            SourceTypeInfo(
                source_type=source_type,
                label=label,
                connector_available=has_connector,
                status="available" if has_connector else absent_status,
                description=description,
            )
        )
    return catalog


def _register_connectors():
    from contextedge.connectors.gmail.connector import GmailConnector
    from contextedge.connectors.jira_sm.connector import JiraSmConnector
    from contextedge.connectors.sapphireims.connector import SapphireIMSConnector
    from contextedge.connectors.servicenow.connector import ServiceNowConnector
    from contextedge.connectors.teams.connector import TeamsConnector
    from contextedge.connectors.zoho_desk.connector import ZohoDeskConnector

    CONNECTOR_CLASSES.update(
        {
            "teams": TeamsConnector,
            "gmail": GmailConnector,
            "servicenow": ServiceNowConnector,
            "jira_sm": JiraSmConnector,
            "sapphireims": SapphireIMSConnector,
            "zoho_desk": ZohoDeskConnector,
        }
    )


def get_connector(
    source_type: str, source_config: dict[str, Any], credentials: dict[str, Any]
) -> BaseConnector:
    if not CONNECTOR_CLASSES:
        _register_connectors()

    cls = CONNECTOR_CLASSES.get(source_type)
    if not cls:
        raise ValueError(f"Unknown source type: {source_type}")
    return cls(source_config, credentials)


def supported_source_types() -> list[str]:
    if not CONNECTOR_CLASSES:
        _register_connectors()
    return list(CONNECTOR_CLASSES.keys())
