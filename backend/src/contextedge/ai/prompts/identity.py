"""Registered versions of the identity/entity-extraction prompt."""

from contextedge.ai.prompts import Prompt, register_prompt

_V1_SYSTEM = """Extract operational entities from the provided evidence content.

Extract entities in these categories:
- person: user names, support agents, engineers
- device: computer names, device models, serial numbers
- application: software names, app names
- vendor: vendor/product companies
- version: software/OS versions, build numbers
- patch: patch IDs, KB numbers, update names
- service: service names, infrastructure components
- environment: production/staging/dev, regions, data centers

Respond in JSON with key "entities" containing a list of objects:
{{"entities": [{{"entity_type": "...", "name": "...", "context": "brief context"}}]}}

Only extract clearly identifiable entities. Do not fabricate."""

_V1_USER = """Content:
{content}"""


register_prompt(
    Prompt(
        name="identity",
        version="v1",
        system=_V1_SYSTEM,
        user_template=_V1_USER,
    ),
)

# v2 adds structured identifiers so the layered resolver can match on
# strong signals (email, username, hostname) instead of display names.
_V2_SYSTEM = """Extract operational entities from the provided evidence content.

Extract entities in these categories:
- person: user names, support agents, engineers
- device: computer names, hostnames, device models
- application: software names, app names
- vendor: vendor/product companies
- version: software/OS versions, build numbers
- patch: patch IDs, KB numbers, update names
- service: service names, infrastructure components
- environment: production/staging/dev, regions, data centers

For each entity also capture any structured identifiers that appear in the
content. Never invent identifiers that are not present.

Respond in JSON with key "entities" containing a list of objects:
{"entities": [{
  "entity_type": "...",
  "display_name": "...",
  "context": "brief context",
  "email": null,
  "username": null,
  "hostname": null,
  "fqdn": null,
  "serial_number": null,
  "ip_addresses": [],
  "source_identifiers": {}
}]}

Example — for "J. Smith (jsmith@acme.com) restarted vpn-gw-east-01 after
the VPN certificate expired":
{"entities": [
  {"entity_type": "person", "display_name": "J. Smith",
    "email": "jsmith@acme.com", "username": null, "hostname": null,
    "fqdn": null, "serial_number": null, "ip_addresses": [],
    "source_identifiers": {}, "context": "Restarted the VPN gateway"},
  {"entity_type": "device", "display_name": "vpn-gw-east-01",
    "email": null, "username": null, "hostname": "vpn-gw-east-01",
    "fqdn": null, "serial_number": null, "ip_addresses": [],
    "source_identifiers": {}, "context": "VPN gateway restarted"}
]}

Only extract clearly identifiable entities. Do not fabricate."""
# NOTE: ``Prompt.system`` is never .format()ed (only the user template is),
# so system strings must use SINGLE braces — doubled braces reach the model
# literally. v1 predates this observation and is left as released.

register_prompt(
    Prompt(
        name="identity",
        version="v2",
        system=_V2_SYSTEM,
        user_template=_V1_USER,
    ),
    default=True,
)

# Candidate adjudication: the LLM judges between a small candidate list and
# may abstain. It never searches the database itself.
_ADJUDICATION_V1_SYSTEM = """You resolve whether an incoming operational entity is the same as one of the known candidate identities.

Rules:
- Choose "match" ONLY when the evidence clearly supports it (shared
  identifiers, department, related systems, or an obvious abbreviation of
  the same name).
- Choose "new_identity" when the incoming entity is clearly none of the
  candidates.
- Choose "needs_review" when you are unsure. Abstaining is always
  acceptable and preferred over guessing.
- Different people can share a name; a username or email match is far
  stronger evidence than a similar display name.

Respond in JSON:
{"decision": "match" | "new_identity" | "needs_review",
  "candidate_id": "<id of the matched candidate or null>",
  "confidence": 0.0-1.0,
  "reason": "one sentence"}"""

_ADJUDICATION_V1_USER = """Incoming entity:
{incoming}

Candidates:
{candidates}"""

register_prompt(
    Prompt(
        name="identity_adjudication",
        version="v1",
        system=_ADJUDICATION_V1_SYSTEM,
        user_template=_ADJUDICATION_V1_USER,
    ),
    default=True,
)
