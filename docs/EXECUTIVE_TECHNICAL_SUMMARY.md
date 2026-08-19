# ContextEdge — Technical Architecture & System Guide

**Accurate as of 2026-08-19.** Mechanism claims carry a `file:line` citation, verified against the code. Before treating anything here as production-ready capability, check [codewiki/KNOWN_GAPS.md](../codewiki/KNOWN_GAPS.md) — it is the honest list of what is scaffolding and what is live.

---

## 1. What is ContextEdge? (Simple Overview)

**ContextEdge** is an AI-powered system that remembers every IT incident your company has ever faced and learns from them.

Think of it like this: Every time a VPN gateway drops or a login system breaks, engineers scramble to fix it. They search old tickets, ask senior colleagues, and dig through chat threads. Most of this knowledge is lost after the incident is closed.

ContextEdge solves this by:
1. **Collecting** raw tickets, emails, and chat messages through its connectors
2. **Stitching** related evidence into complete incident stories (Episodes)
3. **Fingerprinting** each solved incident so the same problem is recognizable when it returns (Issue Signatures)
4. **Detecting** which problems keep repeating over months (Patterns)
5. **Creating** step-by-step fix guides (Playbooks)
6. **Feeding** this knowledge to AI agents so they arrive at the next incident already knowing the history

### Which connectors actually exist

Seven connectors are registered and callable (backend/src/contextedge/connectors/registry.py:100-110):

| Connector | Status |
|---|---|
| ServiceNow | Live — incidents, problems, changes, KB articles, service requests, alert rollups |
| Jira Service Management | Live |
| Gmail | Live |
| Microsoft Teams | Live |
| Zoho Desk | Live for tickets (verified against a real instance, 1,000-ticket backfill). Its knowledge-base module is discovered but **no article has ever been ingested** on the live tenant, so that half is code-verified and corpus-dark |
| ManageEngine ServiceDesk Plus | Implemented |
| SapphireIMS | Implemented but **config-mapped** — the vendor's endpoint contract is not public, so an operator must verify the defaults against their own instance before the first sync. Thread hydration is a no-op there |

`confluence`, `sharepoint`, and `exchange` appear in the source-picker catalog with status `planned` only. There is no Splunk or Slack connector; if you see those names in an older demo dataset, they are seeded rows, not ingestion.

---

## 2. How ContextEdge Builds Knowledge (The Lifecycle)

This is the most important part of the system. Everything in ContextEdge flows through these steps:

```text
STEP 1            STEP 2             STEP 2b            STEP 3              STEP 4
Raw Data ──────► Episode ────────► Signature ──────► Pattern ─────────► Playbook
(Evidence)       (Incident Story)   (Problem          (Repeating          (Fix Manual)
                                     fingerprint)      problem)
```

### The worked example we use everywhere

Every ContextEdge document traces the same incident so you can follow one record end to end:

> **The Acme VPN incident.** Tenant **Acme Corp** runs ServiceNow, Teams, and Gmail. On a Tuesday morning the corporate VPN starts dropping. ServiceNow incident **`INC0010427`** is filed — *"VPN tunnel flapping on `vpn-gw-east-01`"*. Several people file near-duplicate tickets, a Teams thread fills up with diagnosis, and an engineer emails a root-cause note that quotes the ticket number. The eventual cause: an expired gateway certificate. The fix: renew the certificate and restart RADIUS.

Do not invent a different example when extending these docs. One thread, traced consistently, is worth more than five vivid ones.

---

### Step 1: Evidence Collection, Normalization & Storage

**What is Evidence?**
Evidence is any raw piece of data that proves something happened — a ServiceNow ticket, an alert rollup, a chat message, or an email.

**What happens in this step?**
ContextEdge connects to your company's tools and automatically pulls in raw data. Each piece of raw data becomes an **Evidence Item** stored in the database.

**Real-world example — the Acme VPN incident:**

| Tool | What gets created | Example |
|------|------------------|---------|
| ServiceNow | A support ticket is filed | `INC0010427 — "VPN tunnel flapping on vpn-gw-east-01"` |
| Microsoft Teams | Engineers start diagnosing | `"Cert on vpn-gw-east-01 expired at 03:12 — renewing now"` |
| Gmail | An engineer emails a root-cause note | `"Re: INC0010427 — renewed the gateway cert, restarted RADIUS"` |

ContextEdge pulls all three in. For each one it stores the original text, the source timestamp, which system it came from, and a numeric fingerprint (a 3,072-number vector embedding) so it can be compared with other records later.

**The steps that actually run, in order** — all inside `_normalize` (backend/src/contextedge/workers/extraction_tasks.py:122):

1. **A deterministic noise gate, before any AI is involved.** For messages pulled out of a conversation thread, ContextEdge first asks a rules question: is this a bounce notification, a quote with no new text, empty, or pure coordination? "Any update on the VPN?" is rejected as coordination-only — under 150 characters with no technical signal — and **no evidence row is created at all** (extraction_tasks.py:147-160). "Restarted IPSec on vpn-gw-east-01, tunnel stable" survives despite being short, because a hostname counts as a technical signal. On live data this gate rejected 47% of 18,907 messages before spending a single token. The raw message is kept, so tightening the rule later lets you re-judge every rejection exactly.
2. **Redaction.** Regex rules scrub API tokens, JWTs, bearer tokens, secret assignments, emails, phone numbers, SSNs, credit cards, AWS keys, and private-key blocks — in that order, secrets first, so a token is never half-masked (backend/src/contextedge/services/redaction_service.py:40-50, 179-191). Everything downstream — the classifier, the embedder, the extractors, and the database — only ever sees the redacted text.
3. **Deduplication.** Each evidence item gets a content hash taken on the **raw, pre-redaction** body, so tuning a redaction rule never breaks deduplication (backend/src/contextedge/services/evidence_normalization.py:138-152). A database-level partial unique index on `(tenant_id, content_hash)` backs it up; two workers racing on the same content resolve to one row without either paying for a second round of AI calls.
   **A repeat sync refreshes rather than duplicates.** When the Acme ticket is later closed, its body has not changed — so the hash matches, and ContextEdge updates the *existing* row's case state, root-cause facets, and knowledge state in place. That is exactly how "this ticket is now resolved" and "this article was retired" land.
4. **A relevance gate.** One fast AI call classifies the item. Only when the answer is "not relevant" **and** confidence is at least 0.75 does ContextEdge skip the expensive work (extraction_tasks.py:475-479). The threshold is deliberately conservative: missing a real incident costs far more than analyzing some noise. If the classifier itself fails, the item goes down the **full** path — failing open, not closed.
5. **Enrichment**: what kind of message this is, error-signature fingerprints (deterministic, and run even on skipped items — an "irrelevant" thread can still contain a pasted stack trace), identity resolution, decision extraction, and the parent embedding.
6. **Chunking.** Long records are also split into searchable segments with their own embeddings, so a 40-message thread can match on the one paragraph that matters instead of averaging out to nothing.

**What about very large payloads?**
Raw payloads over 32 KB are written to object storage and the database keeps only a small stub pointing at them (backend/src/contextedge/services/ingestion_persistence.py:16, 84-87). This keeps the database lean, and it has one consequence worth knowing: a SQL query that filters on the raw payload silently skips exactly the biggest records. When a column needs backfilling from payload data, re-sync rather than run SQL.

**In simple words:** Evidence = raw proof that something happened. Like collecting witness statements at a crime scene — except you throw away the ones that just say "any news?" before paying an expert to read them.

**Where in the code:**
- Ingestion connectors: `connectors/servicenow/`, `connectors/gmail/`, `connectors/teams/`, `connectors/jira_sm/`, `connectors/zoho_desk/`
- Noise gate: `services/message_filter.py`
- PII redaction: `services/redaction_service.py`
- Database tables: `raw_evidence_objects` (the untouched payload), `evidence_items` (metadata + redacted text), `evidence_chunks` (searchable segments + vector embeddings)

---

### Step 2: AI Episode Reconstruction

**What is an Episode?**
An Episode is one complete incident story. It takes those scattered evidence items from Step 1 and combines them into a single, clean summary that says: "Here's what happened, why it happened, and how it was fixed."

**How does it know which evidence items belong together?**
The correlation service (backend/src/contextedge/services/correlation_service.py:197) looks at two tiers of clues:

1. **Same ticket or thread reference — confidence 1.0, deterministic.** The email quotes `INC0010427`, so it lands in the same case as the ticket. Related-record references from the source system count too: a ServiceNow incident, its problem record, and the change blamed for it correlate at 1.0 **regardless of which one was ingested first**, because the reference and the record share an identifier namespace.
2. **Same systems within a 7-day window — 0.5 to 0.75, and heavily gated.** A shared entity like `vpn-gw-east-01` links two records only if the identity is already `resolved` or `verified`, is not a hub (an identity appearing on 200+ records carries zero signal), and the timestamps are present — a missing timestamp fails *closed*, producing no link. A rare, non-person entity scores 0.75; a common one 0.65. **A single shared person never links two incidents** — people work on many unrelated things.

There is also a veto: if two records each belong firmly to *different* cases, an identity-based link between them is deleted. "Same infrastructure, different incidents" is a real and common situation.

**What stops it from writing an episode too early, or too often?**

This is where most of the engineering lives, because episode synthesis is the single most expensive stage in the system. Before one model call happens, `_reconstruct` (extraction_tasks.py:995) runs six gates:

- **A 180-second debounce**, re-checked at run time — a thread that is still filling up is left alone, with a starvation guard so a never-quiet channel is still narrated within 30 minutes.
- **A minimum cluster of 3.** Most 1-2 item drafts were fragments a later sweep retired.
- **An advisory lock per cluster** — eight concurrent workers once minted eight identical episodes in 46 seconds.
- **Draft idempotency** on a fingerprint of the exact member set.
- **A growth gate** — re-narrating a cluster requires it to be at least 1.5× the size of an episode that already covers it.
- **An optional resolution gate** (off by default) that defers clusters with no sign of a fix anywhere in them.

**How does the AI extraction work?**
The evidence cluster is assembled first — the connected component over case links and correlation edges, bounded at 50 items, 3 hops, and a 30-day window, with legal-hold and pending-redaction records fenced out in the database query so they can never reach a model. Each item is labelled `[ev-1]`, `[ev-2]` and so on, wrapped in untrusted-content markers, and sent as one call.

**Correction to earlier revisions of this document:** episode output is **not** passed through `llm_complete_json_validated`. It goes through `llm_complete_json` plus a purpose-built schema gate that is *strict about structure and lenient about vocabulary* — a structurally broken episode is **dropped** with a warning rather than repaired, while an unrecognized step type quietly becomes `observation`. Citations are translated from `[ev-N]` labels back to real IDs afterward and any label the model invented is discarded, so **the model cannot mint a reference to evidence that does not exist**. Generation provenance (which prompt, which version, which model) is stamped by the code *after* validation, so the model cannot supply its own.

**Real-world example:**

```
Episode: "VPN users unable to connect — expired gateway certificate on vpn-gw-east-01"
Primary case ref: INC0010427

What happened (root cause):
  The TLS certificate on vpn-gw-east-01 expired at 03:12. IPSec tunnels
  began flapping and RADIUS authentication started rejecting sessions.

Final outcome:
  Certificate renewed and RADIUS restarted; tunnels stable since 09:40.

Evidence collected from:
  ├── ServiceNow ticket INC0010427       (the official record)
  ├── Teams thread                        (what was actually tried)
  └── Gmail root-cause note               (the external commitment)
```

**Who approves it?**
Every episode starts as `pending_review`. Approval is either a human action, or — when `EPISODE_AI_REVIEW` is enabled — an AI first pass. That setting has exactly three values: `off` (default, the stage does nothing), `advisory` (a verdict is stamped on the draft for the human queue; nothing is approved), and `auto_approve` (backend/src/contextedge/config.py:185-187).

Auto-approval requires the model verdict **and** four deterministic floors, all of which the model has no say over: at least 2 pieces of evidence, a final outcome of at least 20 characters, a verdict of exactly `approve`, and confidence at least 0.8 (backend/src/contextedge/services/episode_review_service.py:42-44, 89-101). An auto-approved episode is recorded with no reviewer, so it stays permanently distinguishable from a human approval. The review prompt is instructed to **default to hold whenever uncertain**.

**In simple words:** Episode = one complete incident story, built by combining clues from multiple tools. Like a detective writing one case report from witness statements, CCTV, and phone records — and refusing to file it until enough of the record has actually arrived.

**Where in the code:**
- Episode reconstruction: `workers/extraction_tasks.py:995` (`_reconstruct`)
- Cluster materialization: `services/episode_cluster_service.py`
- Correlation logic: `services/correlation_service.py:197`
- AI review: `services/episode_review_service.py`, swept hourly by `evaluation.ai_review_episodes`
- Database tables: `episodes`, `episode_steps`, `episode_evidence_links`

---

### Step 2b: Issue Signatures — recognizing the problem when it comes back

**What is an Issue Signature?**
When an episode is approved, one AI call distils it into a *generalized* problem fingerprint — deliberately stripped of hostnames, ticket numbers, and people. The Acme VPN episode becomes roughly:

```
affected_capability : remote_access
failing_component   : tls_certificate
failure_mode        : certificate_expired
key                 : remote_access|tls_certificate|certificate_expired
```

**Why it matters.** Six months later, when the same failure hits a different gateway, it mints a second episode under the *same key*. ContextEdge then attaches a low-confidence `recurrence` pointer from the new evidence back to the original `INC0010427` case (confidence 0.6, backend/src/contextedge/services/issue_signature_service.py:36).

**The important rule:** a recurrence link is a **precedent, never a merge**. The episode cluster resolver explicitly refuses to expand through recurrence links, because "similar problem" and "same occurrence" are different claims and collapsing them would destroy the recurrence signal itself. The trigger, environment, and scope are recorded but are *not* part of the key — the same failure triggered differently still counts as a recurrence.

**Where in the code:** `services/issue_signature_service.py`, dispatched as `evaluation.extract_issue_signature` (backend/src/contextedge/workers/signature_tasks.py:24). Tables: `issue_signatures`, `episode_issue_signatures`.

---

### Step 3: Pattern Detection

**What is a Pattern?**
A Pattern is a repeating problem. When the system notices that the same type of incident keeps happening over weeks or months, it groups those incidents together and says: "This is a recurring issue."

**How does it detect patterns?**
Clustering works on **approved, embedded** episodes only, and always within one domain scope. For each unassigned episode (backend/src/contextedge/workers/pattern_tasks.py:243-309):

1. **Does it belong to a pattern that already exists?** Look for a pattern whose member episodes sit within `PATTERN_MATCH_MAX_DISTANCE = 0.30`, then confirm with a short AI adjudication call before joining it.
2. **If not, form a new cluster** from similar unassigned episodes within `CLUSTER_GROUP_MAX_DISTANCE = 0.27`, and synthesize a pattern from them in one AI call. A single strong episode can seed a pattern on its own — better than silently dropping a valid approved episode; more episodes raise confidence later.

Both numbers were **recalibrated on 2026-08-19 against the live corpus** (pattern_tasks.py:36-60); older documents quoting 0.35 and 0.27's predecessor 0.20 are stale. The point of the recalibration is worth stating plainly, because it is a good example of measure-first discipline: the previous grouping threshold was so strict that **126 of 150 probed episodes could group with nothing** and each became a single-episode "pattern" — technically a pattern, operationally noise. Loosening it too far collapses the whole corpus into one blob. 0.27 is the measured knee, and the constants' comments record the full curve so nobody re-derives it from scratch.

**When does clustering run?** There is deliberately **no scheduled job** for it. It is dispatched when episodes are approved — by a human or by the AI review sweep, once per affected domain — and by the manual `POST /api/v1/patterns/cluster` route. This matters operationally: on a system where nobody approves episodes, no patterns form.

**Two honest caveats.** The adjudication call **fails open** — during a provider outage it answers "yes, it matches" at confidence 0.75, so the distance probe alone decides membership until the provider recovers. And a full 100-episode pass runs as one long database transaction, so a late failure rolls back every row while the AI spend stays spent.

**Real-world example:**
Look at these six incidents over the past year — all VPN authentication failures:

| Date | Incident | What Went Wrong |
|------|----------|----------------|
| Sep 5, 2025 | INC0005230 | Gateway certificate expired; nobody was watching the expiry date |
| Nov 28, 2025 | INC0006110 | Certificate renewed but the service was never restarted, so the old one stayed loaded |
| Jan 19, 2026 | INC0007204 | Intermediate CA certificate expired on the RADIUS side |
| Apr 2, 2026 | INC0008431 | Certificate renewed on the primary gateway only; the standby failed over to an expired one |
| Jun 14, 2026 | INC0009812 | Automated renewal silently failed on a firewall rule change |
| Aug 19, 2026 | INC0010427 | Certificate on `vpn-gw-east-01` expired at 03:12; tunnels flapping |

The triggers differ every time, but the core problem is always the same: **VPN authentication fails because a certificate in the chain is expired or not loaded.**

ContextEdge groups these six episodes into one Pattern:

```
Pattern: "VPN authentication failure from expired or unloaded gateway certificate"
Confidence: 88%
Episode Count: 6
Date Range: September 2025 — August 2026

Common triggers found:
  • Certificate expiry with no monitoring on the expiry date
  • Renewal performed without restarting the consuming service
  • Renewal applied to the primary node only

Common fix steps found:
  1. Check the certificate chain and expiry on the gateway
  2. Renew the expiring certificate
  3. Restart the RADIUS / IPSec service so the new certificate loads
  4. Confirm the standby node carries the same certificate
  5. Verify a test authentication succeeds
```

**In simple words:** Pattern = a repeating problem that keeps coming back. Like a hospital noticing that every winter, 20 patients come in with the same symptoms — so they write a standard treatment plan.

**Where in the code:**
- Pattern clustering worker: `workers/pattern_tasks.py:422` (`pattern.cluster_episodes`)
- Persistence and domain-safety checks: `services/pattern_service.py`
- Database table: `patterns` (title, confidence, episode_count, trigger_conditions, root_causes, resolution_steps)
- Links: `pattern_evidence_links` (pattern → episode membership)

> **Domain safety.** A domain pass sees only that domain's episodes; the tenant-global pass sees only unscoped ones. This is enforced twice — at query time and again at persistence time, where a mismatched episode raises rather than being quietly absorbed. Because patterns are AI-*synthesized text* surfaced through a domain-filtered projection, a leak here would put one domain's incident narrative inside another domain's visible knowledge.

---

### Step 4: Playbook Creation

**What is a Playbook?**
A Playbook is a verified, step-by-step fix manual for a specific repeating problem. Once a Pattern is detected, the system drafts a Playbook. A senior engineer reviews and approves it. After approval, that playbook is what ContextEdge serves to humans and to AI agents.

**What goes into the draft?** Three different kinds of input, deliberately kept distinct:

1. **Empirical** — up to 12 episode summaries: what engineers actually did.
2. **Normative** — retrieved knowledge-base articles and SOPs: what the documentation says you *should* do.
3. **Negative** — up to 20 recorded "this did not work" items, so the draft does not re-propose a known dead end.

Keeping (1) and (2) separate is the whole point. In the Acme VPN case, the approved certificate-renewal SOP includes a *back up the current certificate first* step that engineers skipped every time under pressure. Because the SOP arrives as a distinct normative source, the generated playbook keeps that step and records the documented-versus-observed disagreement in a `conflicts` block for the reviewer, instead of silently averaging the two.

**The rules the AI does not get to overrule** (backend/src/contextedge/workers/pattern_tasks.py):

- **Risk is policy, not model output.** Each step's safety class sets a floor: read-only → low, low-side-effect → medium, high-side-effect or destructive → high, and an unrecognized class → high. The model's suggested risk tier may only *raise* it above that floor.
- **Grounding is structural.** A step that still carries a valid source citation is marked `grounded`; a step without one is **forced** to `best_practice` even if the model asserted otherwise.
- **Invented citations are dropped and counted.** Only labels actually shown to the model resolve; the rest are discarded and the count is recorded on the version.
- **A playbook with no steps is refused outright.** This guard exists because a truncated response whose complete-looking prefix survived JSON repair once shipped a playbook with **zero steps while reporting success**.
- **A pattern below 0.5 confidence does not get an automatic draft**, calibrated by reviewing 37 generated playbooks — below that line the output was structured but hollow.

**Real-world example:**

```
Playbook: "Restore VPN authentication after gateway certificate expiry"
Risk Level: Medium (approval required before any restart step)
Status: Approved by Network SME
Linked Pattern: "VPN authentication failure from expired or unloaded gateway certificate"

Steps:
  Step 1: Check certificate chain and expiry on the gateway     [grounded, kb-1]
  Step 2: Back up the current certificate and private key       [grounded, kb-1]
  Step 3: Renew and install the gateway certificate             [grounded, kb-1]
  Step 4: Restart the RADIUS / IPSec service to load it         [grounded, ep-2]
  Step 5: Confirm the standby node carries the same certificate [best practice]
  Step 6: Verify a test authentication succeeds end to end      [grounded, ep-3]

Conflicts recorded for the reviewer:
  • Documented procedure requires a certificate backup (Step 2);
    observed practice in 4 of 6 episodes skipped it.
```

**Who uses the Playbook?**
- **Human engineers** look it up on the dashboard during an incident.
- **AI agents** (via Microsoft Agent Framework) retrieve it as memory. Every MAF tool today is read-or-propose; **there is no executor on this branch**. The execution service is a ledger driven by external callers, wrapped in approval binding, an attempt ledger, and trust profiles — safety scaffolding built ahead of the thing it will govern.

**In simple words:** Playbook = a verified fix manual. Like a fire department's standard operating procedure — and like a good SOP, it tells you which steps are backed by evidence and which are a sensible default someone added.

**Where in the code:**
- Generation worker: `workers/pattern_tasks.py:446` (`pattern.generate_playbook_candidate`)
- Knowledge retrieval into the prompt: `services/knowledge_retrieval_service.py`
- Versioning and lifecycle: `services/playbook_service.py`
- Database tables: `playbooks` (metadata), `playbook_versions` (versioned steps, citations, conflicts), `playbook_evidence_links` (provenance)
- Graph edge: `playbook ──derived_from──► pattern`

> **The manual generate route is not the same path.** `POST /api/v1/playbooks/generate` deliberately skips the confidence floor (it exists for patterns below it, and for humans who disagree with the floor) — but it also skips knowledge retrieval, the risk floor, the empty-steps guard, and playbook embedding, and its episode citations do not resolve. Use the worker path unless you specifically need the override.

---

### How It All Connects (The Complete Picture)

```text
STEP 1: Raw Data Comes In
  ServiceNow ticket INC0010427 ─┐
  Teams diagnosis thread        ─┼──► These are EVIDENCE items (raw proof)
  Gmail root-cause note         ─┘

STEP 2: AI Groups Related Evidence
  Evidence + Evidence + Evidence ──► This becomes an EPISODE (one incident story)

STEP 2b: The Problem Gets a Fingerprint
  Episode ──► ISSUE SIGNATURE (remote_access|tls_certificate|certificate_expired)
              so the same failure is recognizable when it returns

STEP 3: System Finds Repeating Problems
  Episode + Episode + Episode + ... ──► This becomes a PATTERN (repeating issue)

STEP 4: Fix Guide is Created
  Pattern ──► This gets a PLAYBOOK (step-by-step fix manual)
```

On the dashboard graph view, it looks like this (read left to right):

```text
[ PLAYBOOK ] ──► [ PATTERN ] ──► [ EPISODE ] ──► [ EVIDENCE ]
   (fix guide)    (repeating     (one incident    (raw ticket,
                   problem)       story)           thread, email)
```

---

## 3. System Architecture

### Architectural Model
ContextEdge is a **Modular Monolith** — one FastAPI backend, one PostgreSQL database, one deployment. All core logic runs together in one application.

```text
 ┌─────────────────────────────────────────────────────────────────────────────┐
 │                         ContextEdge Modular Monolith                        │
 │                                                                             │
 │   ┌─────────────────────────────────────────────────────────────────────┐   │
 │   │                       FastAPI Backend Application                   │   │
 │   │                                                                     │   │
 │   │  ┌──────────────┐   ┌──────────────┐   ┌─────────────────────────┐  │   │
 │   │  │  API Routers │   │ Service Layer│   │ Ingestion Connectors    │  │   │
 │   │  │  (/api/v1)   │───│ (Playbooks,  │───│ (servicenow, jira_sm,   │  │   │
 │   │  │              │   │  Decisions)  │   │  gmail, teams,          │  │   │
 │   │  │              │   │              │   │  zoho_desk, sapphireims,│  │   │
 │   │  │              │   │              │   │  manageengine)          │  │   │
 │   │  └──────────────┘   └──────────────┘   └────────────┬────────────┘  │   │
 │   └─────────────────────────────────────────────────────│───────────────┘   │
 └─────────────────────────────────────────────────────────│───────────────────┘
                                                           │
                        HTTP REST / OAuth API Calls        │
             ┌─────────────────────────────────────────────┴───────────┐
             │                                                         │
             ▼                                                         ▼
  ┌──────────────────────┐                                  ┌────────────────────┐
  │  ServiceNow (Cloud)  │                                  │   Gmail / Google   │
  │  (Incidents/Tickets) │                                  │   (Emails/Threads) │
  └──────────────────────┘                                  └────────────────────┘
```

Alongside the API sit **eight Celery worker queues** consuming the same database: `default`, `sync`, `hydration`, `extraction`, `correlation`, `embedding`, `pattern`, `evaluation` (backend/src/contextedge/workers/celery_app.py:226-280). The `correlation` and `embedding` lanes exist because a single shared queue let bulk ingestion starve everything downstream of it, *silently* — evidence arrived, and episodes and search results simply never appeared. A deployment that does not consume all eight reproduces that failure.

**Why this design?**
- All data lives in one database — no network calls between microservices
- Graph traversal is indexed SQL against one table, not a second datastore to operate
- Simple to deploy and manage

---

## 4. User Roles & Access Controls

ContextEdge has 5 user roles:

| Role | Who is this person? | What can they do? |
|------|-------------------|------------------|
| **Support Engineers** | L1/L2 Support | Search for playbooks and evidence during active incidents |
| **Knowledge Managers** | L3 SMEs / Senior Engineers | Review, edit, and approve AI-generated episodes and playbooks |
| **Domain Admins** | Team Leads | Configure which channels and data sources to ingest |
| **Tenant Admins** | Platform Admins | Manage security settings, user roles, API keys, and LLM budgets |
| **AI Service Accounts** | Autonomous Systems | Query ContextEdge memory. They cannot execute anything — every agent-facing tool is read-or-propose, and no executor exists on this branch |

The backend role names are `platform_super_admin`, `tenant_admin`, `domain_admin`, `knowledge_manager`, and `playbook_reviewer`.

**Two caveats worth stating to anyone planning around this model:**

1. **`platform_super_admin`, `tenant_admin`, and `admin` pass every role check** on the backend (backend/src/contextedge/deps.py:37-44).
2. **Role grants are effectively tenant-wide.** The database records a scope on each role binding, but nothing enforces it — a "domain admin for Networking" holds that role across the whole tenant on every gated route. Single-domain tenants are unaffected; multi-domain tenants must plan for it. Finer scoping exists only through service-token domain allowlists, which *are* enforced. This is recorded openly in [codewiki/KNOWN_GAPS.md](../codewiki/KNOWN_GAPS.md) rather than half-fixed, because a partially honoured scoping change is worse than a documented limitation.

Note also that hiding a nav item in the dashboard is **user experience, not access control** — the API is the enforcement point.

---

## 5. Microsoft Agent Framework (MAF) Integration

**What is MAF?**
MAF is Microsoft's framework for building AI agents that can think, plan, and take actions.

**What does ContextEdge do with MAF?**
ContextEdge acts as the **operational memory** for MAF agents. Without ContextEdge, an AI agent has no idea what happened in your company before. With ContextEdge, the agent wakes up already knowing all past incidents and approved fix steps.

---

### What Agent Roles Does the Integration Target?

The MAF integration ships **one plugin** (`ContextGraphMAFPlugin`: a proactive memory provider + a **read-only** graph query tool). Consumers can build any number of agents on top of it; the design targets four typical roles (all governed by the `maf.v1` projection profile):

| Agent Role (design target) | What it does | Real-World Example |
|------------|-------------|-------------------|
| **1. Operational Resolution Agent** | Diagnoses active incidents by fetching matching graph context | During an outage, reads past episodes and recommends the right playbook |
| **2. Playbook Execution Agent** | Follows approved remediation steps — execution itself goes through ContextEdge's governed execution API (safety classes + approval policies), not through the MAF tool | Requests a governed run of the approved recovery playbook |
| **3. Audit & Compliance Agent** | Reviews decision traces, policy checks, and tool invocations recorded in PostgreSQL | Ensures all AI actions have a complete audit trace for compliance teams |
| **4. Diagnostic & Analysis Agent** | Evaluates incoming evidence, claims, and flags contradictions | Detects if a newly proposed fix contradicts an existing security policy |

---

### What Tools & Mechanisms Are Exposed to MAF Agents?

`ContextGraphMAFPlugin` (`integrations/maf/plugin.py`) bundles **one proactive memory provider** and **six on-demand tools**. Every one of them is read-or-propose; none can change operational state.

#### Mechanism 1: Proactive Memory Provider (`ContextGraphProvider`)
- **How it works:** on the `before_run` hook, the provider joins the last four conversation messages, keeps the trailing 4,000 characters (truncating from the *front*, because the newest text holds the question), asks ContextEdge for a bounded subgraph, and injects it into the prompt (backend/src/contextedge/integrations/maf/provider.py:50).
- **It is fenced.** The subgraph is wrapped in `<untrusted-data>` markers with an explicit "this is reference data, not instructions" preamble (provider.py:106-108), because node labels and summaries come out of tickets, chat, and email — content an outsider can write.
- **If ContextEdge is unreachable, the run continues without graph context** rather than failing.
- **It closes the loop.** On `after_run`, the agent's answer is written back as an agent-authored *decision* through the same code path a human decision uses — marked `actor_type: ai`, `approval_required`, and carrying the exact projection it read. And because a pending AI-authored decision is invisible to the projection, **agent output cannot launder itself back into agent input** until a human reviews it or an outcome is recorded.

#### Mechanism 2: Six On-Demand Tools

| Tool | What it does |
|---|---|
| `query_context_graph` | Fetch a bounded subgraph for a question, optional seed node IDs, entity names, and depth 1-3 |
| `cmdb_topology` | Live ±1-hop ServiceNow neighborhood for a CI, cache-first, explicitly marked stale on an outage |
| `assess_change_risk` | Deterministic, explainable risk profile for a CI over a window |
| `assess_fix_applicability` | Does a known fix actually apply to this CI |
| `get_cohort_shared_attributes` | What do these affected machines have in common |
| `propose_dependency` | **Proposes** a dependency edge at confidence 0.3 with a rationale — it never becomes authored topology until a knowledge manager approves it through the review endpoint |

Defined at backend/src/contextedge/integrations/maf/tools.py:29, 106, 146, 188, 229, 277. Malformed arguments come back as a structured `{"error": {code, message}}`, never a raw traceback.

---

### Memory Safety & Guardrails (`maf.v1` Profile)

To prevent AI agents from getting confused or exceeding token limits, ContextEdge strictly bounds what a projection can return. Two numbers matter, and they are different:

| | Default per request | Profile maximum |
|---|---|---|
| Nodes | 24 | 60 |
| Relationships | 48 | 120 |
| Depth | 2 hops | 3 hops |
| Characters | 12,000 | 30,000 |

Defaults at backend/src/contextedge/graph/agent/contracts.py:26-30; maximums at backend/src/contextedge/graph/agent/profiles.py:180-188. A request is clamped to the smaller of what it asked for and the profile maximum, so quoting only the maximum overstates a normal call by more than double.

Beyond the size caps:

- **Relevance decays per hop** (`hop_decay = 0.72`), multiplied by edge weight and confidence and clamped at 1.0, so a boosted multi-hop path can never outrank the seeds it came from.
- **Visibility is fail-closed per node type.** A playbook must be approved, have a current version, not be expired, and sit inside the caller's risk cap. An episode must be approved. Evidence must pass the knowledge-lifecycle check and must not be legal-hold or pending-redaction. A wrong-tenant row is invisible by construction.
- **Some relationships are deliberately not traversable.** `mentions_identity` is excluded because it fans out 40-70 edges per handful of tickets — the budget would be spent on identity hubs instead of on topology the agent can reason about. Each exclusion carries its stated reason, and a test enforces that every registered edge type is either projected or excluded-with-a-reason.
- **Truncation is reported, not hidden.** The response carries usage counts, a `truncated` flag, and the reasons; an empty projection says "No authorized graph seeds were resolved."

---

**Summary:**
- **Without MAF integration:** The web dashboard still works for humans, but AI agents are blind to company history.
- **With MAF integration:** agents get automatic fenced memory plus six read-or-propose tools under strict caps. There is no write-capable tool and no executor on this branch, so any remediation remains a human or external-system action recorded through the governed execution ledger.

---

## 6. Multi-Topic Filtering & Quality Control

Sometimes a single ticket contains unrelated topics (a printer issue mixed into a VPN ticket), and sometimes a thread is 90% pleasantries. ContextEdge handles this with layered safeguards, cheapest first:

1. **A deterministic noise gate** drops non-diagnostic thread messages before any AI call at all — 47% of live messages, measured (`services/message_filter.py`).
2. **Cross-message quote stripping** at hydration removes text already seen earlier in the same thread. This runs at hydration because only that stage holds the whole thread in arrival order; on live data 89% of "substantive" text turned out to be repetition.
3. **Per-source chunking** splits long tickets and threads into topic-coherent segments (`services/chunkers/`, migration `0030`), so retrieval matches the relevant paragraph instead of the whole mixed-topic ticket.
4. **Diversity at search time.** The chunk search deliberately spreads results across *different* records before ranking, so forty near-identical chunks from one noisy thread cannot crowd out three distinct sources.
5. **Contradiction detection**: conflicting extracted facts route the item to the Review Queue rather than being silently reconciled.
6. **Negative knowledge**: when a reviewer records "this step did not work" or "B is not related to A", that judgement is stored and fed back into later drafts.
7. **Knowledge lifecycle**: an article whose source system marked it draft, in review, or retired is **withheld from retrieval entirely**. A citation is what makes a stale article dangerous — it reads as though somebody checked.

---

## 7. Context Graph & Database Storage

### How the Knowledge Graph Works
ContextEdge stores relationships between all its data in a graph structure:

```text
[ PLAYBOOK ] ──(derived_from)──► [ PATTERN ] ◄──(belongs_to)── [ EPISODE ] ──(episode_evidence_links)──► [ EVIDENCE ]
```

These relationships live in one PostgreSQL table, `graph_edges` (backend/src/contextedge/models/pattern.py:174-273). The columns that matter:

```sql
graph_edges (
  id                UUID PRIMARY KEY,
  tenant_id         UUID NOT NULL,
  domain_id         UUID,          -- domain scoping for the projection
  source_node_type  VARCHAR,       -- 'evidence', 'episode', 'pattern', 'playbook', 'entity', ...
  source_node_id    UUID,
  edge_type         VARCHAR,       -- 'derived_from', 'belongs_to', 'supported_by', 'affects_ci', ...
  target_node_type  VARCHAR,
  target_node_id    UUID,
  weight            FLOAT,         -- traversal importance
  confidence        FLOAT,         -- belief that the relationship is true
  metadata_extra    JSONB,
  valid_from        TIMESTAMPTZ,   -- temporal validity
  valid_to          TIMESTAMPTZ    -- NULL means currently active
)
```

Three things are worth an executive's attention here:

- **`weight` and `confidence` are separate on purpose.** How much a relationship should matter when walking the graph is not the same question as how sure we are it is true. Conflating them was a real bug that got fixed.
- **The edge vocabulary is closed.** Every writable edge type is declared in a registry and enforced at write time. Before that, a typo produced a real, queryable edge that the agent projection silently ignored — the graph knew something nobody could see, and nothing failed.
- **Edges are time-aware.** A relationship that ends is closed with `valid_to` rather than deleted, so you can ask what the graph looked like at a past moment. The honest caveat, which the system states in its own response: a historical query combines historical *edges* with *current* node facts.

When someone opens the Pattern graph view, `graph/queries.py` walks these edges with an iterative breadth-first search — one indexed query per hop, capped at 3 hops and bounded at 250 nodes / 500 edges for the payload.

---

## 8. Cost Control and Monitoring

### Prometheus
- **API performance**: request counts, response times, error rates
- **AI model metrics**: `contextedge_llm_tokens_total` (split by prompt / completion / cached), `contextedge_llm_requests_total` by outcome, and `contextedge_llm_reasoning_tokens_total` as a **separate metric** rather than a token-type label — reasoning tokens are a *subset* of completion tokens, and a label would double-count every sum
- **System health**: database connection pool usage, worker queue depth

### Per-tenant LLM budgets

Every model call checks the tenant's daily budget **before spending**. A tenant with no explicit budget row falls back to deployment defaults of 2,000,000 tokens/day and $25/day with action `block` (backend/src/contextedge/config.py:194-198) — before that, "no row" meant "no limit", so a fresh tenant was the only uncapped one. Usage is summed from the day's own `llm.usage` audit events, so there is no second counter to drift.

The operator-visible symptom of a hit budget is worth memorizing: **chunks stuck un-embedded, with usage events showing `outcome = budget_exceeded`.** Nothing crashes; retrieval just quietly stops improving. `GET /api/v1/admin/pipeline-health` exists for exactly this — it reads queue depth per lane *plus* in-flight unacknowledged work, and counts the graph chain end to end so the first zero in the sequence is the diagnosis.

### Where the money actually goes

Two measured facts shape most of the design above: episode synthesis was **29% of all tokens with 71% of its output later superseded** (hence the six gates before a synthesis call), and identity work was **78% of model spend** before a candidacy gate was added in front of it. Every "skip" in this document is there because someone measured what it cost not to skip.

---

## 9. System Component Map

| Step | What Happens | Code Location |
|------|-------------|--------------|
| 1. Ingestion | Pull tickets/emails/chats/KB from the seven registered connectors | `connectors/servicenow/`, `jira_sm/`, `gmail/`, `teams/`, `zoho_desk/`, `manageengine/`, `sapphireims/` |
| 2. Raw persistence | Store the payload; offload above 32 KB to object storage | `services/ingestion_persistence.py`, `services/object_store.py` |
| 3. Noise gate | Drop non-diagnostic thread messages before any AI call | `services/message_filter.py` |
| 4. Redaction | Remove tokens, keys, emails, SSNs — before embedding or any LLM call | `services/redaction_service.py` |
| 5. Normalization | Derive title/body/hash, dedupe, classify, enrich, embed | `workers/extraction_tasks.py:122` |
| 6. Thread hydration | Pull the full conversation and strip cross-message repetition | `workers/hydration_tasks.py` |
| 7. Chunk + embed | Split into searchable segments, embed in batches of 32 | `services/chunkers/`, `workers/chunk_tasks.py` |
| 8. Correlation | Case links (deterministic) + gated identity co-occurrence | `services/correlation_service.py:197` |
| 9. Episode synthesis | Gates, then one call turns a cluster into a story | `workers/extraction_tasks.py:995` |
| 10. Episode review | Advisory verdict or gated auto-approval | `services/episode_review_service.py` |
| 11. Issue signature | Generalized fingerprint + recurrence precedent link | `services/issue_signature_service.py` |
| 12. Pattern detection | Group similar episodes into patterns | `workers/pattern_tasks.py:422` |
| 13. Playbook drafting | Empirical + normative + negative inputs, then deterministic gates | `workers/pattern_tasks.py:446` |
| 14. Graph wiring | Create and close edges | `graph/builder.py` |
| 15. Search | Chunk-level vector search + full-text + graph signals, with abstention | `search/vector_search.py`, `search/hybrid_ranker.py:213` |
| 16. Graph traversal | Per-hop BFS for graph views | `graph/queries.py` |
| 17. MAF agent memory | Fenced projection + six read-or-propose tools | `integrations/maf/provider.py`, `integrations/maf/tools.py` |
| 18. Decision logging | Record AI/human decisions for audit | `services/decision_trace_service.py` |

---

## 10. What to Read Next

| You want | Read |
|---|---|
| The layered architecture, in onboarding language | [02_Project_Architecture.md](02_Project_Architecture.md) |
| Subsystem map, flows, data model | [TECHNICAL_BLUEPRINT.md](TECHNICAL_BLUEPRINT.md) |
| Route-by-route HTTP behaviour | [API.md](API.md) |
| Running it, worker commands, troubleshooting | [RUNBOOK.md](RUNBOOK.md) |
| **What is scaffolding rather than live capability** | [codewiki/KNOWN_GAPS.md](../codewiki/KNOWN_GAPS.md) |

---
*Accurate as of 2026-08-19.*
