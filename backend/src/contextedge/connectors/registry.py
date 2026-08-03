from typing import Any

from contextedge.connectors.base import BaseConnector

CONNECTOR_CLASSES: dict[str, type[BaseConnector]] = {}


def _register_connectors():
    from contextedge.connectors.gmail.connector import GmailConnector
    from contextedge.connectors.jira_sm.connector import JiraSmConnector
    from contextedge.connectors.sapphireims.connector import SapphireIMSConnector
    from contextedge.connectors.servicenow.connector import ServiceNowConnector
    from contextedge.connectors.teams.connector import TeamsConnector

    CONNECTOR_CLASSES.update(
        {
            "teams": TeamsConnector,
            "gmail": GmailConnector,
            "servicenow": ServiceNowConnector,
            "jira_sm": JiraSmConnector,
            "sapphireims": SapphireIMSConnector,
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
