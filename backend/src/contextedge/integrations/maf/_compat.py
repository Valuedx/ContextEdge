"""Lazy Microsoft Agent Framework imports with one actionable error."""

try:
    from agent_framework import (
        ContextProvider,
        FunctionInvocationContext,
        SessionContext,
        tool,
    )
except ImportError as exc:  # pragma: no cover - exercised without the optional extra
    raise ImportError(
        "Microsoft Agent Framework support requires `pip install contextedge[maf]`."
    ) from exc

__all__ = [
    "ContextProvider",
    "FunctionInvocationContext",
    "SessionContext",
    "tool",
]
