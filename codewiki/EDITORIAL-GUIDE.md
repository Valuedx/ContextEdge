# Editorial guide for codewiki explainers

This guide keeps every blueprint article readable for **business and product stakeholders** while preserving enough **engineering precision** that developers can jump from prose to code.

## Audience and voice

- Assume the reader understands **tickets, playbooks, evidence, and tenants** in plain language, but may not know FastAPI, Celery, or pgvector naming.
- Prefer **outcomes** (“analysts see deduplicated evidence”) before **mechanisms** (“hash-based dedupe in the service layer”).
- When you introduce a mechanism, add a short **“In code”** line or paragraph that names the package path and one or two anchor symbols (class or function).
- Avoid unexplained acronyms on first use; spell out once, then abbreviate if helpful (for example, “full-text search (FTS)”).

## Structure for each article

Use this skeleton so pages feel consistent:

1. **One-paragraph promise** — what the reader will understand after reading.
2. **Business picture** — what happens in the product without file names.
3. **Technical walkthrough** — ordered steps through the pipeline or subsystem.
4. **Design decisions** — 2–5 bullets: choice, rationale, tradeoff, and optional “see also” link to another codewiki page.
5. **Code map** — table: concern → module path → key class/function (optional third column: “when it runs”).
6. **Thread the example** — one short paragraph tying the shared scenario to this layer (see below).
7. **Further reading** — links to `docs/` and sibling codewiki files only when they add value.

## Shared example: “Acme VPN incident”

Use **one fictional but realistic thread** in every article so concepts stay grounded.

**Setup:** Tenant **Acme Corp** uses Jira, Teams, and email. An outage hits **Corporate VPN**. Multiple people file duplicates, chat in Teams, and an engineer emails a root-cause note.

**What we trace through the system:**

| Stage in the story | What to illustrate |
| --- | --- |
| Connectors / sync | Tickets and messages enter as **sources**; sync jobs pull or receive updates. |
| Ingestion | Raw payloads land in object storage; **evidence** rows represent normalized facts with provenance. |
| Search | An analyst searches “VPN gateway”; **hybrid** lexical + vector ranking applies **access control**. |
| AI extraction | Models propose **episodes** (what happened), **patterns** (recurring structure), and links between entities. |
| Playbooks | A **playbook version** captures approved steps; governance ties changes to review and policy. |
| Runtime / sessions | A **resolution session** records decisions and retrieval traces for audit. |
| Retention | Policies eventually expire or archive old raw blobs and metadata safely. |

Each article should include **at least one** sentence that explicitly names this scenario (for example, “When Acme’s duplicate VPN tickets arrive…”). Do not invent new primary examples per page unless the article is explicitly labeled “Additional scenarios.”

## Vocabulary alignment

Align terms with the product and codebase:

- **Evidence** — stored, queryable units with source metadata (not “documents” unless referring to attachments).
- **Episode** — a time-bounded narrative slice derived from evidence.
- **Pattern** — recurring structure learned or curated across episodes or evidence.
- **Playbook** — governed, versioned operational procedure; tie to human review where relevant.
- **Tenant** — isolation boundary; always mention when discussing security or search filters.

## Code references

- Prefer paths relative to repository root, for example `backend/src/contextedge/services/ingestion_persistence.py`.
- When citing existing code in review or PRs, use the project’s normal citation style in editor tooling; in markdown wiki pages, use fenced blocks only for short illustrative snippets, not entire files.
- After refactors, update the **Code map** table in the affected article; the narrative should remain stable if the architecture is unchanged.

## Diagrams

Use simple **sequence or flow** descriptions in prose first. Add Mermaid only when a diagram removes ambiguity (for example, API → service → worker → DB). Keep one diagram per article unless complexity demands two.

## Quality bar before marking an article “done”

- [ ] Business picture stands alone (could be read by a PM).
- [ ] At least three **design decision** bullets with explicit tradeoffs.
- [ ] **Code map** has five or more rows with real symbols from the repo.
- [ ] **Acme VPN incident** appears in the narrative.
- [ ] Links to `docs/` for setup, API details, or operations instead of copying them.
