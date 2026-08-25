"""Generate docs/10_API_Documentation.md from the live OpenAPI schema.

Run from ``backend/``:

    python generate_docs.py

Every endpoint, method, path, auth requirement, and model name comes from
``app.openapi()`` — nothing is guessed from filenames or invented. Re-run
after adding or changing routes; the output file is fully overwritten and
must not be hand-edited.
"""

from __future__ import annotations

import sys
from collections import defaultdict
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent
SRC_DIR = BACKEND_DIR / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

OUT_FILE = BACKEND_DIR.parent / "docs" / "10_API_Documentation.md"

HEADER = """# ContextEdge — API Documentation

> Generated from the FastAPI OpenAPI schema by `backend/generate_docs.py`.
> Do not hand-edit — re-run the script after route changes.
> The interactive reference is always available at `/docs` (Swagger UI)
> and `/redoc` on a running backend.

## API Overview

- **Base URL**: `/api/v1`
- **Authentication**: JWT Bearer token in the `Authorization` header
  (obtained via `POST /api/v1/auth/login` with `{"username", "password"}`),
  or `X-Service-Token` for configured service accounts.
- **Error format**: `{"detail": "message"}` with a matching HTTP status.
"""


def _ref_name(schema: dict | None) -> str | None:
    if not isinstance(schema, dict):
        return None
    ref = schema.get("$ref")
    if isinstance(ref, str):
        return ref.rsplit("/", 1)[-1]
    items = schema.get("items")
    if isinstance(items, dict):
        inner = _ref_name(items)
        if inner:
            return f"list[{inner}]"
    return schema.get("type")


def _request_model(operation: dict) -> str | None:
    body = operation.get("requestBody") or {}
    content = body.get("content") or {}
    json_content = content.get("application/json") or {}
    return _ref_name(json_content.get("schema"))


def _response_model(operation: dict) -> str | None:
    responses = operation.get("responses") or {}
    for status in ("200", "201", "202"):
        content = (responses.get(status) or {}).get("content") or {}
        json_content = content.get("application/json") or {}
        name = _ref_name(json_content.get("schema"))
        if name:
            return name
    return None


def _group_key(path: str) -> str:
    trimmed = path.removeprefix("/api/v1").strip("/")
    return trimmed.split("/", 1)[0] if trimmed else "root"


def main() -> None:
    from contextedge.main import app

    schema = app.openapi()
    paths = schema.get("paths", {})

    grouped: dict[str, list[tuple[str, str, dict]]] = defaultdict(list)
    for path, operations in sorted(paths.items()):
        for method, operation in operations.items():
            if method.lower() not in ("get", "post", "put", "patch", "delete"):
                continue
            grouped[_group_key(path)].append((method.upper(), path, operation))

    lines: list[str] = [HEADER]
    for group in sorted(grouped):
        lines.append(f"\n## `{group}`\n")
        for method, path, operation in grouped[group]:
            summary = operation.get("summary") or ""
            lines.append(f"### `{method} {path}`\n")
            if summary:
                lines.append(f"{summary}\n")
            description = (operation.get("description") or "").strip()
            if description and description != summary:
                lines.append(f"{description}\n")
            secured = bool(operation.get("security", schema.get("security")))
            details: list[str] = []
            details.append(
                f"- **Auth**: {'required' if secured else 'not required'}"
            )
            request_model = _request_model(operation)
            if request_model:
                details.append(f"- **Request body**: `{request_model}`")
            response_model = _response_model(operation)
            if response_model:
                details.append(f"- **Response**: `{response_model}`")
            params = [
                p.get("name")
                for p in operation.get("parameters") or []
                if isinstance(p, dict) and p.get("in") == "query"
            ]
            if params:
                details.append(
                    "- **Query parameters**: " + ", ".join(f"`{p}`" for p in params)
                )
            lines.extend(details)
            lines.append("")

    total_endpoints = sum(len(ops) for ops in grouped.values())
    lines.append(
        f"\n---\n\n*{total_endpoints} endpoints across {len(grouped)} groups, "
        "generated from the OpenAPI schema.*\n"
    )

    OUT_FILE.write_text("\n".join(lines), encoding="utf-8")
    print(f"Generated {OUT_FILE} ({total_endpoints} endpoints)")


if __name__ == "__main__":
    main()
