"""Entity/identity extraction from evidence content."""

from contextedge.ai.provider import llm_complete_json

IDENTITY_PROMPT = """Extract operational entities from the following evidence content.

Content:
{content}

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

Only extract clearly identifiable entities. Do not fabricate.
"""


async def extract_identities(content: str) -> list[dict]:
    """Extract operational entities from evidence text."""
    if not content or len(content.strip()) < 10:
        return []

    prompt = IDENTITY_PROMPT.format(content=content[:4000])
    result = await llm_complete_json(prompt, task="classification")
    return result.get("entities", [])
