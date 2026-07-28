"""Microsoft Agent Framework adapter for Context Graph.

Client-only imports remain usable without installing the optional MAF extra.
Framework-backed objects are loaded when they are first requested.
"""

from __future__ import annotations

from typing import Any

from contextedge.integrations.maf.client import (
    ContextGraphClient,
    HttpContextGraphClient,
    InProcessContextGraphClient,
)

__all__ = [
    "ContextGraphClient",
    "ContextGraphMAFPlugin",
    "ContextGraphProvider",
    "ContextGraphTools",
    "HttpContextGraphClient",
    "InProcessContextGraphClient",
]


def __getattr__(name: str) -> Any:
    if name == "ContextGraphMAFPlugin":
        from contextedge.integrations.maf.plugin import ContextGraphMAFPlugin

        return ContextGraphMAFPlugin
    if name == "ContextGraphProvider":
        from contextedge.integrations.maf.provider import ContextGraphProvider

        return ContextGraphProvider
    if name == "ContextGraphTools":
        from contextedge.integrations.maf.tools import ContextGraphTools

        return ContextGraphTools
    raise AttributeError(name)
