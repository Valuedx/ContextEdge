"""Build source-system deep links from a Source row's config.

Powers the reviewer console's Zone 4 / Zone 5 drill-in: when the engineer
hovers or clicks an evidence card, the UI opens the originating ticket /
thread / message in its source system.

Resolution order:
1. `source_config.deep_link_template` — string with `{external_id}` /
   `{thread_id}` variables. Wins when present, which means admins can
   point at any URL shape without code changes.
2. Built-in defaults for known `source_type` values — `jira_sm`,
   `servicenow`, `gmail`. Teams deep links are intentionally skipped;
   they require tenant + team + channel context that isn't on the
   Source row.
3. `None` when neither path resolves — the UI degrades gracefully to
   a non-clickable card.
"""

from __future__ import annotations

from typing import Any


def _clean_base_url(value: str | None) -> str | None:
    if not value:
        return None
    return value.rstrip("/")


def _substitute(template: str, *, external_id: str | None, thread_id: str | None) -> str | None:
    """Substitute {external_id} / {thread_id} tokens.

    If the template requires a variable that isn't provided, returns None
    rather than leaking the literal `{external_id}` placeholder to the UI.
    """
    if "{external_id}" in template and external_id is None:
        return None
    if "{thread_id}" in template and thread_id is None:
        return None
    return template.format(
        external_id=external_id or "",
        thread_id=thread_id or "",
    )


def _default_link(
    source_type: str,
    source_config: dict[str, Any],
    *,
    external_id: str | None,
    thread_id: str | None,
) -> str | None:
    base = _clean_base_url(
        source_config.get("base_url")
        or source_config.get("instance_url")
        or source_config.get("tenant_url"),
    )

    if source_type == "jira_sm":
        if not external_id:
            return None
        # Jira issue keys ("INC-100"); instance like https://acme.atlassian.net.
        if not base:
            return None
        return f"{base}/browse/{external_id}"

    if source_type == "servicenow":
        if not external_id:
            return None
        if not base:
            return None
        # ServiceNow number-based deep link; works for incidents, requests, etc.
        return (
            f"{base}/nav_to.do?uri=task.do?sysparm_query=number={external_id}"
        )

    if source_type == "gmail":
        # Gmail prefers thread-level links; fall back to message-level.
        ref = thread_id or external_id
        if not ref:
            return None
        return f"https://mail.google.com/mail/u/0/#all/{ref}"

    # teams and anything else — admin must supply a template.
    return None


def build_source_deep_link(
    source_type: str | None,
    source_config: dict[str, Any] | None,
    external_id: str | None,
    *,
    thread_id: str | None = None,
) -> str | None:
    """Return a URL that opens the source record in its origin system, or
    None when we can't safely construct one."""
    if not source_type:
        return None
    cfg = source_config or {}

    template = cfg.get("deep_link_template")
    if isinstance(template, str) and template:
        return _substitute(template, external_id=external_id, thread_id=thread_id)

    return _default_link(
        source_type, cfg, external_id=external_id, thread_id=thread_id,
    )
