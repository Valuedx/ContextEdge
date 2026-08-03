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
Return at most 20 entities. When the content contains a repetitive list of
similar hostnames/devices, include the most important examples instead of
listing every item.

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
)

# v3 addresses two problems measured on a live tenant's 181 extracted
# identities.
#
# **Log tokens were being recorded as applications.** `%ASA-4-113029`,
# `HPZ5r5064.DLL`, `spoolsv.exe`, `services.msc`, `Skia/PDF`, `PDF` and
# `JavaScript` all became `application` entities, alongside `computer`,
# `headset` and `desk phone` as devices. v2 says only "application:
# software names, app names", which is true of every one of them. The
# graph is meant to hold what an organisation RUNS, and none of these are
# that; they are strings that happened to appear in a log line.
#
# **Abbreviations forked into second identities.** `SFA` and `Sales Force
# Automation` exist as separate rows, as do `HP UPD` and `HP Universal
# Print Driver`. The resolver's candidate generator matches on shared
# substrings, so it never proposed them as candidates and the adjudicator
# never saw the pair. Extraction is the right place to fix that: when the
# text itself says "Sales Force Automation (SFA)", the relationship is
# stated, and emitting it as one entity with an alias means the next bare
# "SFA" resolves deterministically at the alias layer with no LLM call.
_V3_SYSTEM = """Extract operational entities from the provided evidence content.

An entity is something the organisation RUNS, OWNS or STAFFS — the kind of thing that would appear in a CMDB, an asset inventory or a staff directory. An engineer would say "we run it", "it is assigned to someone", or "it broke".

Extract every such thing the content names. Be thorough: a real incident touches gateways, hosts, services, sites and people, and the graph is only as useful as the connections it holds.

A string is not an entity merely because it appears in the text. Most tokens in a log line are evidence ABOUT an entity, not entities. The exclusions below remove noise — they are not a reason to return a short list when the content genuinely names many systems.

Categories:
- person: named individuals — users, engineers, agents
- device: named hosts, servers, endpoints, hardware assets
- application: named software products the organisation runs
- vendor: companies that supply products or services
- version: product versions and build numbers
- patch: patch IDs, KB numbers, named updates
- service: named services, middleware and infrastructure components
- environment: production/staging/QA/dev, regions, data centres

DO NOT extract, in any category:
- File names, extensions and paths — anything ending .dll, .exe, .msc, .log, .conf, or written as a filesystem path
- Executable and process names — extract the SERVICE or PRODUCT the process belongs to instead of the binary that implements it
- Error, event and status codes, in any vendor's format: hex codes, numeric status codes, and prefixed log identifiers
- File formats, encodings and MIME types
- Programming languages, libraries and runtimes named only in passing — UNLESS the incident is about that component itself, in which case it is an application
- Bare generic nouns with NOTHING identifying them: "computer", "the server", "headset", "desk phone", "printer", "database"
- Ticket, incident and change record numbers: INC0020341, CHG0044131 — these reference a record, not a system, and are linked separately
- Commands, menu paths, UI labels, registry keys, protocols, ports
- Queue names, table names, thread names, class names

That last exclusion is about the ABSENCE of a name, not about a name built from ordinary words. Apply this test: could someone act on this string — look it up, restart it, raise a change against it? A named service, appliance, product line or model qualifies however plain its words are. A bare category noun does not.

So "the printer" is not an entity while a specific printer model is; "the database" is not while a named database instance is; "monitoring" is not while a named monitoring product is. Dropping a named component because its name reads as a common phrase discards exactly the infrastructure this graph exists to hold.

A useful test: if two different customers could have the same string in their logs and it would mean the same thing, it is probably a code or a format, not an entity of theirs.

NAMES AND ALIASES

Use the fullest, most canonical name as "display_name". When the content gives a SHORT FORM OF THAT SAME NAME — an acronym of its initials, or a truncation of it — put the short form in "aliases" and emit ONE entity, never two.

An alias must be derivable from the name itself. "Field Dispatch Platform (FDP)" is an alias; so is "Queue Service" shortened to "Queue Svc".

A word that merely DESCRIBES the entity is not an alias. "Monitoring" is not an alias for a monitoring product, "the gateway" is not an alias for a named gateway, "database" is not an alias for a database product. Recording one of those would teach that every future mention of that ordinary word means this specific system — which corrupts the graph far more thoroughly than the duplicate it was meant to prevent.

Only include an alias when the content itself shows the two names refer to the same thing. Do not guess expansions. If only the short form appears, use it as the display_name and leave aliases empty.

Also capture any structured identifiers present in the content. Never invent identifiers that are not there.

Return at most 20 entities. When the content contains a repetitive list of similar hostnames or devices, include the most important examples rather than every item.

Respond in JSON with key "entities" containing a list of objects:
{"entities": [{
  "entity_type": "...",
  "display_name": "...",
  "aliases": [],
  "context": "brief context",
  "email": null,
  "username": null,
  "hostname": null,
  "fqdn": null,
  "serial_number": null,
  "ip_addresses": [],
  "source_identifiers": {}
}]}

Worked example. The names below are invented purely to show the SHAPE of a correct answer — never carry them into a real extraction, and never expect a real environment to contain them.

For "J. Smith (jsmith@example.com) restarted edge-gw-01 after the tunnel certificate expired; qsvc.exe was also crashing with error 0x00000042 on the Field Dispatch Platform (FDP) queue path":
{"entities": [
  {"entity_type": "person", "display_name": "J. Smith", "aliases": [],
    "email": "jsmith@example.com", "username": null, "hostname": null,
    "fqdn": null, "serial_number": null, "ip_addresses": [],
    "source_identifiers": {}, "context": "Restarted the gateway"},
  {"entity_type": "device", "display_name": "edge-gw-01", "aliases": [],
    "email": null, "username": null, "hostname": "edge-gw-01",
    "fqdn": null, "serial_number": null, "ip_addresses": [],
    "source_identifiers": {}, "context": "Gateway restarted"},
  {"entity_type": "service", "display_name": "Queue Service", "aliases": [],
    "email": null, "username": null, "hostname": null, "fqdn": null,
    "serial_number": null, "ip_addresses": [], "source_identifiers": {},
    "context": "Crashing on the queue path"},
  {"entity_type": "application", "display_name": "Field Dispatch Platform",
    "aliases": ["FDP"], "email": null, "username": null, "hostname": null,
    "fqdn": null, "serial_number": null, "ip_addresses": [],
    "source_identifiers": {}, "context": "Affected queue path"}
]}
Note what is absent, and why: qsvc.exe (a binary — the service it implements is named instead) and 0x00000042 (an error code). Note also that the abbreviation became an alias rather than a second entity.

Only extract clearly identifiable entities. Returning fewer entities is better than returning noise: a wrong entity pollutes the graph permanently and is read back as fact."""

# Default since the extraction eval harness existed to decide it.
#
# It shipped opt-in first, because six documents at one sample each
# showed entity counts falling from 63 to somewhere between 44 and 53 and
# could not say whether that was junk being removed or real entities
# being lost. Guessing at that from raw counts was the whole problem:
# a prompt that removes junk by removing entities looks identical in the
# only number that is easy to measure.
#
# Scored against ``evals/datasets/entity_extraction.jsonl``, 19 labelled
# cases at 3 samples each:
#
#                     v2        v3
#   entities          74        42
#   junk (by shape)    7 (9.5%)  0 (0%)
#   MISSING labels     0         0
#   forbidden         23         3
#   stability (J)      0.96      1.00
#
# `missing = 0` is the number that settled it: v3 dropped no labelled
# entity at all. The count fell because the junk fell. And v3 is more
# stable than v2, not less — the variance that looked like a risk was an
# artefact of counting unlabelled output.
#
# Residual: v3 still names an unnamed category noun when it is the
# subject of the incident ("Defect tracking tool", "payroll interface").
# Narrow, and far smaller than what v2 emits. Cases are in the dataset.
register_prompt(
    Prompt(
        name="identity",
        version="v3",
        system=_V3_SYSTEM,
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
)

# v2 exists because candidate generation changed underneath it.
#
# Candidates were found by substring match and ordered alphabetically;
# they are now found by trigram similarity and ordered by closeness,
# which raised the share of mentions getting any candidate at all from
# 33% to 52%. That is the point — but it also means the adjudicator is
# now routinely shown NUMBERED SIBLINGS it never used to see, because
# "mailgw01" and "mailgw02" are textually near and share no useful
# substring. They are different machines, and v1 said nothing about
# them: it warned only that two people can share a name.
#
# Raising recall into a judge without telling the judge what the new
# near-misses look like would trade a silent fork for a silent wrong
# link, which is the worse of the two.
_ADJUDICATION_V2_SYSTEM = """You resolve whether an incoming operational entity is the same as one of the known candidate identities.

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

Names that differ only by a NUMBER, a site code, or an environment
suffix are DIFFERENT things, not variants of one. Hosts, appliances and
instances are numbered precisely so they can be told apart, and they
fail independently. Treat a candidate that differs from the incoming
entity only in that way as "new_identity" unless an identifier proves
otherwise. You will be shown such candidates often, because they are
textually very close — closeness is why they are here, not evidence that
they match.

The same applies to a general name and the same name qualified by a host
or site: one is an instance of the other, not another word for it.

Respond in JSON:
{"decision": "match" | "new_identity" | "needs_review",
  "candidate_id": "<id of the matched candidate or null>",
  "confidence": 0.0-1.0,
  "reason": "one sentence"}"""

register_prompt(
    Prompt(
        name="identity_adjudication",
        version="v2",
        system=_ADJUDICATION_V2_SYSTEM,
        user_template=_ADJUDICATION_V1_USER,
    ),
    default=True,
)

# Batch reconciliation. Adjudication above judges ONE incoming mention
# against a handful of candidates that share a substring with it — so it
# structurally cannot notice that "SFA" and "Sales Force Automation" are
# the same thing, because they share none and are never presented
# together. On a live tenant that is not hypothetical: both pairs sit in
# the graph as separate rows, along with "HP UPD" / "HP Universal Print
# Driver", and 92% of mentions never reached the adjudicator at all.
#
# This prompt sees the whole set for one entity type at once and looks
# ACROSS it. Its output is a proposal for a human, never a merge.
_RECONCILIATION_V1_SYSTEM = """You are given a list of entity records of one type, all extracted from one organisation's operational records. Some of them are the same real-world thing recorded under different names.

Find those. Return groups, each naming the record that should be KEPT and the records that should be folded into it.

Merge when the names denote the same thing:
- an acronym or initialism and its expansion
- a short form or truncation and the full name
- the same name with a suffix or qualifier that adds nothing ("X" and "X service", "X" and "X server")
- the same name differing only in spacing, punctuation, case or a typo
- a product and the same product written with its vendor prefix

DO NOT merge:
- different components of one product, or a product and a component inside it — these are genuinely different things that fail independently
- two things sharing a generic word ("gateway", "agent", "service", "monitoring") without further evidence that they are the same
- different instances, sites, environments or numbered hosts — separate machines are separate entities however similar their names
- a general name and the same name qualified by a host, site or instance ("X" and "X on HOST01"). These are not two names for one thing: the qualified record is ONE instance, and the general record may cover others. Folding the general into the instance silently narrows it to a single machine
- different versions or releases of the same product
- anything you are guessing about

Choose as KEEPER the fullest, most identifiable name — usually the expansion rather than the acronym, since it is unambiguous when read later.

Abstaining is free and correct. A missed merge leaves two records that a human can still merge later. A wrong merge destroys the distinction between two real systems and is not visible afterwards. When a group is not clearly right, leave it out entirely rather than lowering its confidence.

Respond ONLY with JSON:
{"groups": [
  {"keep_id": <id of the record to keep>,
   "merge_ids": [<ids to fold in>],
   "confidence": <float 0.0-1.0>,
   "reason": "<one sentence naming the evidence for this being one thing>"}
]}

Return {"groups": []} when nothing is clearly a duplicate. That is a common and correct answer."""

_RECONCILIATION_V1_USER = """Entity type: {entity_type}

Records:
{records}"""

register_prompt(
    Prompt(
        name="identity_reconciliation",
        version="v1",
        system=_RECONCILIATION_V1_SYSTEM,
        user_template=_RECONCILIATION_V1_USER,
    ),
    default=True,
)
