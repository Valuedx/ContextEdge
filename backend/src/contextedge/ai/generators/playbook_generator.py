"""Playbook candidate generation from pattern clusters."""

from contextedge.ai.provider import llm_complete_json

PLAYBOOK_PROMPT = """You are generating a living playbook for an operational troubleshooting pattern.

Pattern Title: {pattern_title}
Pattern Description: {pattern_description}
Episode Count: {episode_count}

Episode Summaries:
{episode_summaries}

Negative Knowledge (steps that repeatedly fail):
{negative_knowledge}

Generate a structured playbook candidate with:
1. Trigger conditions: when should this playbook be invoked
2. Steps with branching logic: diagnostic and remediation flow
3. Risk assessment
4. Rollback notes from negative knowledge
5. Confidence breakdown

Respond in JSON:
{{
  "title": "...",
  "description": "...",
  "risk_tier": "low" | "medium" | "high" | "critical",
  "trigger_conditions": {{
    "symptoms": ["..."],
    "entities": ["..."],
    "conditions": ["..."]
  }},
  "steps": [
    {{
      "order": 1,
      "type": "diagnostic" | "action" | "check" | "branch" | "escalation",
      "text": "...",
      "expected_outcome": "...",
      "on_failure": "...",
      "evidence_quality": "high" | "medium" | "low"
    }}
  ],
  "branching_logic": {{
    "decision_points": [
      {{
        "after_step": 1,
        "condition": "...",
        "if_true_goto": 2,
        "if_false_goto": 3
      }}
    ]
  }},
  "inputs": ["..."],
  "outputs": ["..."],
  "rollback_notes": "...",
  "playbook_confidence": 0.0-1.0,
  "execution_confidence_guidance": "..."
}}
"""


async def generate_playbook_candidate(
    pattern_title: str,
    pattern_description: str,
    episode_count: int,
    episode_summaries: list[dict],
    negative_knowledge: list[str],
) -> dict:
    """Generate a playbook candidate from pattern analysis."""
    summaries_text = ""
    for i, ep in enumerate(episode_summaries[:10]):
        summaries_text += f"\n{i + 1}. {ep.get('title', 'Untitled')}"
        if ep.get("root_cause"):
            summaries_text += f"\n   Root cause: {ep['root_cause']}"
        if ep.get("outcome"):
            summaries_text += f"\n   Outcome: {ep['outcome']}"

    neg_text = "\n".join(f"- {nk}" for nk in negative_knowledge[:20]) if negative_knowledge else "None identified"

    prompt = PLAYBOOK_PROMPT.format(
        pattern_title=pattern_title,
        pattern_description=pattern_description or "",
        episode_count=episode_count,
        episode_summaries=summaries_text,
        negative_knowledge=neg_text,
    )

    return await llm_complete_json(prompt, task="extraction")
