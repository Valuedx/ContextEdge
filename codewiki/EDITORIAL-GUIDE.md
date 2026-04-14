# Editorial guide for codewiki explainers

This guide keeps every blueprint article readable for **business and product stakeholders** while preserving enough **engineering precision** that developers can jump from prose to code.

## Audience and voice

- Assume the reader understands **tickets, playbooks, evidence, and tenants** in plain language, but may not know FastAPI, Celery, or pgvector naming.
- Prefer **outcomes** ("analysts see deduplicated evidence") before **mechanisms** ("hash-based dedupe in the service layer").
- When you introduce a mechanism, add a short **"In code"** line or paragraph that names the package path and one or two anchor symbols (class or function).
- Avoid unexplained acronyms on first use; spell out once, then abbreviate if helpful (for example, "full-text search (FTS)").
- Lead business sections with **why this matters** to the organization, not with how the system achieves it.

## Structure for each article

Use this skeleton so pages feel consistent:

1. **One-paragraph promise** — what the reader will understand after reading.
2. **Business picture** — what happens in the product without file names. Lead with the business outcome, not the mechanism. A PM should be able to read this section alone and explain the value to a stakeholder.
3. **Technical walkthrough** — ordered steps through the pipeline or subsystem.
4. **Example: Acme VPN data at this stage** — concrete input and output data (JSON blocks or structured text) showing what enters and exits this layer. See "Concrete example inputs and outputs" below.
5. **Design decisions** — 2-5 bullets: choice, rationale, tradeoff, and optional "see also" link to another codewiki page.
6. **Code map** — table: concern -> module path -> key class/function (optional third column: "when it runs").
7. **Acme VPN incident (this layer)** — one short paragraph tying the shared scenario to this layer (see below).
8. **Further reading** — links to `docs/` and sibling codewiki files only when they add value.

## Shared example: "Acme VPN incident"

Use **one fictional but realistic thread** in every article so concepts stay grounded.

**Setup:** Tenant **Acme Corp** uses Jira, Teams, and email. An outage hits **Corporate VPN**. Multiple people file duplicates, chat in Teams, and an engineer emails a root-cause note.

**What we trace through the system:**

| Stage in the story | What to illustrate |
| --- | --- |
| Connectors / sync | Tickets and messages enter as **sources**; sync jobs pull or receive updates. |
| Ingestion | Raw payloads land in object storage; **evidence** rows represent normalized facts with provenance. |
| Search | An analyst searches "VPN gateway"; **hybrid** lexical + vector ranking applies **access control**. |
| AI extraction | Models propose **episodes** (what happened), **patterns** (recurring structure), and links between entities. |
| Playbooks | A **playbook version** captures approved steps; governance ties changes to review and policy. |
| Runtime / sessions | A **resolution session** records decisions and retrieval traces for audit. |
| Retention | Policies eventually expire or archive old raw blobs and metadata safely. |

Each article should include **at least one** sentence that explicitly names this scenario (for example, "When Acme's duplicate VPN tickets arrive..."). Do not invent new primary examples per page unless the article is explicitly labeled "Additional scenarios."

## Concrete example inputs and outputs

Every article must include a section titled **"Example: Acme VPN data at this stage"** placed between the **Technical walkthrough** and **Design decisions**. This section should show **what goes in** and **what comes out** at the layer the article covers, using short JSON blocks or structured text that a PM or operator can follow without reading code.

Guidelines:

- Use the same incident thread (Jira ticket JIRA-4521, Teams thread, engineer email) across all articles so readers can trace one record end to end.
- Keep each JSON block to **10-20 lines**; truncate with `...` if the real object is larger.
- Label inputs and outputs clearly: "**Input** (what arrives)" and "**Output** (what the system produces)".
- When the article covers a transformation (e.g., raw -> normalized), show both shapes side by side.
- When the article covers a query (e.g., search, runtime match), show the request and the response.
- Prefer realistic field names from the actual data model; do not invent new ones.
- The example section should stand alone: a reader who skips the technical walkthrough should still understand the data shape from the examples.

## Vocabulary alignment

Align terms with the product and codebase:

- **Evidence** — stored, queryable units with source metadata (not "documents" unless referring to attachments).
- **Episode** — a time-bounded narrative slice derived from evidence.
- **Pattern** — recurring structure learned or curated across episodes or evidence.
- **Playbook** — governed, versioned operational procedure; tie to human review where relevant.
- **Tenant** — isolation boundary; always mention when discussing security or search filters.

## Code references

- Prefer paths relative to repository root, for example `backend/src/contextedge/services/ingestion_persistence.py`.
- When citing existing code in review or PRs, use the project's normal citation style in editor tooling; in markdown wiki pages, use fenced blocks only for short illustrative snippets, not entire files.
- After refactors, update the **Code map** table in the affected article; the narrative should remain stable if the architecture is unchanged.

## Diagrams

Use simple **sequence or flow** descriptions in prose first. Add Mermaid only when a diagram removes ambiguity (for example, API -> service -> worker -> DB). Keep one diagram per article unless complexity demands two.

## Quality bar before marking an article "done"

- [ ] Business picture stands alone (could be read by a PM).
- [ ] At least three **design decision** bullets with explicit tradeoffs.
- [ ] **Code map** has five or more rows with real symbols from the repo.
- [ ] **Example: Acme VPN data at this stage** section present with concrete input/output blocks.
- [ ] **Acme VPN incident** appears in the narrative.
- [ ] Links to `docs/` for setup, API details, or operations instead of copying them.
