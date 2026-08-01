"""Registered versions of the message-function classifier prompt.

Classifies what a conversational message is DOING (backlog A1). The
label feeds three deterministic consumers: the dissociation veto on
reply inheritance, correction supersession (A2), and the
negative-evidence store (A7) — so the vocabulary is closed and the
instruction is conservative: a wrong "dissociation" label costs a
missed inheritance (cheap); a wrong "status_update" on a real
dissociation glues unrelated cases (expensive).

Treat each version as immutable: edit = ship a new version.
"""

from contextedge.ai.prompts import Prompt, register_prompt

MESSAGE_FUNCTIONS = (
    "status_update",
    "question",
    "correction",
    "dissociation",
    "resolution_confirmation",
    "noise",
)

_V1_SYSTEM = """You classify what a message in an IT-operations conversation is DOING.

Valid functions:
- "status_update": reports progress, observations, or state ("Certificate renewed, monitoring", "Still failing after restart").
- "question": asks for information or action ("Has it recovered?", "Can someone check the gateway?").
- "correction": corrects an earlier statement in the conversation ("Correction - it's Mary's ticket, not John's", "Actually that was the EMEA gateway, not east").
- "dissociation": explicitly states that this topic/issue is NOT the one being discussed or NOT related to a referenced ticket ("Different issue - is the ordering DB also down?", "This isn't about the VPN thing", "Not related to INC0010427").
- "resolution_confirmation": confirms the issue is resolved ("Confirmed working now", "Users are back online, closing").
- "noise": greetings, thanks, scheduling, or content with no operational function.

Rules:
- Classify the message's PRIMARY function. A question that also reports status is a "question" only if the ask dominates.
- "correction" and "dissociation" require EXPLICIT language changing or severing a link to earlier context. New information alone is a "status_update".
- Be conservative with "dissociation": only when the message clearly severs the topic from the surrounding conversation or a named ticket.

Respond ONLY with a JSON object:
{
  "function": "status_update" | "question" | "correction" | "dissociation" | "resolution_confirmation" | "noise",
  "confidence": <float between 0.0 and 1.0>
}"""

_V1_USER = """Classify this message:

Source: {source_type}
Title: {title}
Content:
{body}"""


register_prompt(
    Prompt(
        name="message_function",
        version="v1",
        system=_V1_SYSTEM,
        user_template=_V1_USER,
    ),
    default=True,
)
