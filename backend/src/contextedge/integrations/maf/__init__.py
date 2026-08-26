"""Microsoft Agent Framework adapter for Context Graph.

Client-only imports remain usable without installing the optional MAF extra.
Framework-backed objects are loaded when they are first requested.
"""

from __future__ import annotations

from typing import Any

from contextedge.integrations.maf.client import (
    CohortClient,
    ContextGraphClient,
    EdgeProposalClient,
    HttpContextGraphClient,
    InProcessCohortClient,
    InProcessContextGraphClient,
    InProcessEdgeProposalClient,
)

__all__ = [
    "CohortClient",
    "CohortTools",
    "ContextGraphClient",
    "ContextGraphMAFPlugin",
    "ContextGraphProvider",
    "ContextGraphTools",
    "EdgeProposalClient",
    "EdgeProposalTools",
    "HttpContextGraphClient",
    "HttpPlaybookRetrievalClient",
    "InProcessCohortClient",
    "InProcessContextGraphClient",
    "InProcessEdgeProposalClient",
    "InProcessPlaybookRetrievalClient",
    "PlaybookRetrievalClient",
    "PlaybookTools",
]

# Framework-backed names (need the MAF extra), resolved lazily.
_LAZY_TOOL_EXPORTS = (
    "CohortTools",
    "ContextGraphTools",
    "EdgeProposalTools",
    "PlaybookTools",
)


def __getattr__(name: str) -> Any:
    if name == "ContextGraphMAFPlugin":
        from contextedge.integrations.maf.plugin import ContextGraphMAFPlugin

        return ContextGraphMAFPlugin
    if name == "ContextGraphProvider":
        from contextedge.integrations.maf.provider import ContextGraphProvider

        return ContextGraphProvider
    if name in {
        "PlaybookRetrievalClient",
        "InProcessPlaybookRetrievalClient",
        "HttpPlaybookRetrievalClient",
    }:
        from contextedge.integrations.maf import playbook_client

        return getattr(playbook_client, name)
    if name in _LAZY_TOOL_EXPORTS:
        if name == "PlaybookTools":
            from contextedge.integrations.maf.playbook_tools import PlaybookTools

            return PlaybookTools
        from contextedge.integrations.maf import tools

        return getattr(tools, name)
    raise AttributeError(name)
