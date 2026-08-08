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
    "InProcessCohortClient",
    "InProcessContextGraphClient",
    "InProcessEdgeProposalClient",
]

# Framework-backed names (need the MAF extra), resolved lazily.
_LAZY_TOOL_EXPORTS = ("CohortTools", "ContextGraphTools", "EdgeProposalTools")


def __getattr__(name: str) -> Any:
    if name == "ContextGraphMAFPlugin":
        from contextedge.integrations.maf.plugin import ContextGraphMAFPlugin

        return ContextGraphMAFPlugin
    if name == "ContextGraphProvider":
        from contextedge.integrations.maf.provider import ContextGraphProvider

        return ContextGraphProvider
    if name in _LAZY_TOOL_EXPORTS:
        from contextedge.integrations.maf import tools

        return getattr(tools, name)
    raise AttributeError(name)
