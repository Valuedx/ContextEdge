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
    default=True,
)
