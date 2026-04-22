"""Registered versions of the contradiction-confirmation prompt.

The contradiction scanner already split its prompt into a stable
system block and dynamic user block for prompt-cache efficiency
(see ``contradiction_service._llm_confirms_contradiction``). This
registration records the current prompt text under a named version
so the admin dashboard can attribute contradiction-detection cost
and quality to a specific revision, and so a future ``v2`` can be
A/B-tested per-tenant via ``settings.tenant_prompt_variants_json``.
"""

from contextedge.ai.prompts import Prompt, register_prompt

_V1_SYSTEM = """Decide whether the knowledge-base text contradicts the operational step. Return JSON with keys contradiction (boolean) and reason (string). Only mark contradiction=true when the KB explicitly recommends or asserts something that directly conflicts with the step's instruction. Tangential references or unrelated guidance are not contradictions."""

_V1_USER = """Operational step:
{step_text}

Knowledge-base text:
{kb_text}"""


register_prompt(
    Prompt(
        name="contradiction",
        version="v1",
        system=_V1_SYSTEM,
        user_template=_V1_USER,
    ),
    default=True,
)
