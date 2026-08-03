"""Registered versions of the knowledge-applicability extraction prompt.

Reads a knowledge article and states *where it applies*: which component,
which deployment model, which environment, which versions.

This began as a lexical extractor and the measurements are why it is not
one any more. Against a live 18-article corpus the rules read
``Apache License 2.0`` as a product version, the IP address
``10.10.10.51`` as version 10.10.10, ``activemq-client-5.15.8`` as a
component named "client", and ``Non-Prod`` as production — the exact
opposite of its meaning. Deployment and environment matched 0% of
articles, because engineers convey "on-premise" by telling you to edit a
file on the server, never by writing the word.

And one class of error had no lexical fix at all: "applies to 8.0 **and
later**" is a range. Read as a point version it produces a confident
version-conflict warning against a 9.12 environment — a fabricated
warning emitted by the feature whose entire purpose is preventing
confidently wrong citations.

Cost was the original argument for rules, and it was answered by moving
the work rather than simplifying it: extraction runs ONCE per article at
ingest and is persisted, not per article per retrieval. One call per
document, for the life of the document.
"""

from contextedge.ai.prompts import Prompt, register_prompt

_V1_SYSTEM = """You read IT knowledge articles (KB articles, SOPs, runbooks, product documentation) and extract WHERE THE ARTICLE APPLIES.

You are not summarising the article and not judging its quality. You are answering one question: if an engineer is holding this article, what environment must they be in for it to be correct?

Return ONLY a JSON object matching this schema:
{
  "components": ["<product or subsystem this article is about, lowercase>"],
  "deployment": "cloud" | "onprem" | "both" | "unknown",
  "environments": ["production" | "staging" | "qa" | "development"],
  "product_versions": {"<product name, lowercase>": "<version>"},
  "version_floor": {"<product name, lowercase>": "<earliest version this applies to>"},
  "version_ceiling": {"<product name, lowercase>": "<last version this applies to>"},
  "confidence": <float 0.0-1.0>
}

Rules:

COMPONENTS — the specific product or subsystem the article is about, not the general technology area. Prefer the name a vendor would use. Include bundled components only when the article is genuinely about them. Omit rather than guess.

DEPLOYMENT — decide from what the steps REQUIRE, not from whether the words appear:
- "onprem" if the reader must touch a file system, edit a config file, restart a service, run a shell command, access a server, or use an installer.
- "cloud" if the work happens entirely in a hosted admin UI or a vendor-hosted API, or the article names a SaaS/cloud edition.
- "both" if the article covers both, or is conceptual with no environment-specific steps.
- "unknown" only when there is genuinely no signal.
This is the field most often left blank by naive extraction. Infer it.

ENVIRONMENTS — include a tier ONLY when the article is SCOPED to it ("for production systems only", "do not run this in production"). An article that merely mentions production in passing is not scoped to it — return an empty list. Never infer a tier from the fact that most work happens in production.

VERSIONS — distinguish carefully:
- product_versions: the exact version the article was written against.
- version_floor: when the article says "X and later", "requires X or above", "from X onwards".
- version_ceiling: when it says "before X", "up to X", "removed in X".
An article saying "applies to 8.0 and later" has version_floor 8.0 and NO product_versions entry. Getting this wrong invents version conflicts, so prefer floor/ceiling whenever the article expresses a range.

NEVER treat these as product versions: IP addresses (10.10.10.51), licence versions ("Apache License 2.0"), protocol or spec versions, port numbers, dates, error codes, step numbers, file sizes.

Return empty collections and "unknown" freely. Silence is a correct answer and is treated as "applies broadly". A guess is not — it becomes a warning shown to an engineer during an incident."""

_V1_USER = """Extract the applicability of this knowledge article.

Title: {title}
Content:
{body}"""


register_prompt(
    Prompt(
        name="knowledge_applicability",
        version="v1",
        system=_V1_SYSTEM,
        user_template=_V1_USER,
    ),
    default=True,
)
