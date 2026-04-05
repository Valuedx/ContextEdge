"""Shared helpers for converting raw payloads into normalized evidence fields."""

from __future__ import annotations

import hashlib


def evidence_title_from_payload(payload: dict | None) -> str:
    body = payload or {}
    return body.get("title") or body.get("subject") or "Untitled"


def evidence_body_from_payload(payload: dict | None) -> str:
    body = payload or {}
    return body.get("body") or body.get("body_text") or str(body)[:8000]


def evidence_content_hash_from_payload(payload: dict | None) -> str:
    body = evidence_body_from_payload(payload)
    return hashlib.sha256(body.encode("utf-8", errors="replace")).hexdigest()
