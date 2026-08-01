"""Registered versions of the issue-signature extraction prompt.

Extracts the generalized problem fingerprint from an APPROVED episode
(backlog B3). The output feeds deterministic dedupe and applicability
matching — so the instruction pushes toward short, generic, reusable
values ("wireless_connectivity", "adapter_missing_after_resume"), not
prose. The LLM proposes; the schema gate and key normalization dispose.

Treat each version as immutable: edit = ship a new version.
"""

from contextedge.ai.prompts import Prompt, register_prompt

_V1_SYSTEM = """You extract a structured ISSUE SIGNATURE from a resolved IT incident story.

The signature is the generalized fingerprint of the problem — it must match future
incidents of the same kind on OTHER machines, so use short generic snake_case values,
never device names, hostnames, ticket numbers, or people.

Fields:
- "affected_capability": what stopped working, as a capability ("wireless_connectivity",
  "authentication", "email_delivery", "boot", "printing").
- "failing_component": the component class at fault if identified ("wifi_adapter_driver",
  "tls_certificate", "power_adapter", "group_policy"), else null.
- "failure_mode": how it fails ("adapter_missing_after_resume", "certificate_expired",
  "crash_on_launch", "boot_loop"), snake_case.
- "trigger_change": the change that triggered it if identified ("driver_update",
  "windows_patch", "policy_update"), else null.
- "environment": "production" | "corporate_managed" | "development" | null when unclear.
- "scope": "single_device" | "multiple_devices" | "site_wide" | "service_wide" | null.
- "confidence": 0.0-1.0 that this fingerprint is correct and generic.

Respond ONLY with a JSON object:
{
  "affected_capability": "...",
  "failing_component": "..." or null,
  "failure_mode": "...",
  "trigger_change": "..." or null,
  "environment": "..." or null,
  "scope": "..." or null,
  "confidence": 0.0-1.0
}"""

_V1_USER = """Incident story:

Title: {title}
Root cause: {root_cause}
Outcome: {outcome}
Steps:
{steps}"""


register_prompt(
    Prompt(
        name="issue_signature",
        version="v1",
        system=_V1_SYSTEM,
        user_template=_V1_USER,
    ),
    default=True,
)
