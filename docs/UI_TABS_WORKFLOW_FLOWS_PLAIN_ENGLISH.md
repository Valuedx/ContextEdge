# ContextEdge UI Tabs - Plain English Workflow Flows

This document explains the workflow flow for every UI tab. Use it in demos when someone asks, "How does this tab work in the pipeline?"

## Full System Flow

```text
Sources
  -> Sync Operations
  -> Evidence
  -> Correlations / Identities
  -> Episodes
  -> Patterns
  -> Playbooks
  -> Runtime
  -> Sessions
  -> Review Queue
  -> Execution
  -> Decisions
  -> Audit Log

Governance checks around the flow:
Negative Knowledge, Contradictions, Drift, Evaluations, Policies, LLM Cost, Settings, Graph Explorer
```

**Simple demo line:**
Data enters from Sources, becomes Evidence, related Evidence becomes Episodes, repeated Episodes become Patterns, Patterns create Playbooks, Runtime recommends a Playbook, human reviews it, Execution runs it, and Decisions/Audit Log record what happened.

## 1. Overview

```text
Backend counts and health data
  -> Overview dashboard
  -> User sees system status
```

**Used for:** First health check.

**Example:** Admin opens Overview and sees evidence count increased, but one source sync failed.

## 2. Sources

```text
Admin creates source
  -> Stores source config and credentials
  -> Source is ready for discovery/backfill/sync
```

**Used for:** Define where data comes from.

**Example:** Add ServiceNow, Jira, Gmail, Teams, or local folder as a source.

## 3. Sync Operations

```text
Source exists
  -> Sync job runs
  -> Raw records are fetched
  -> Success/failure/retry is recorded
```

**Used for:** Monitor data import jobs.

**Example:** Jira sync fails because token expired. Sync Operations shows the failed run.

## 4. Evidence

```text
Raw source record
  -> Normalize format
  -> Redact sensitive data
  -> Classify relevance
  -> Create embedding
  -> Store as Evidence
```

**Used for:** Store searchable facts.

**Example:** ServiceNow ticket, SMTP log, and Teams message become evidence records.

## 5. Sessions

```text
One live issue starts
  -> Create session
  -> Add symptoms/entities/context
  -> Runtime and decisions attach to the session
  -> Session becomes the case file
```

**Used for:** Track one problem from start to finish.

**Example:** "MG22 output not received" becomes one session.

## 6. Runtime

```text
User enters symptoms/entities
  -> Runtime builds memory context
  -> Searches evidence, patterns, and playbooks
  -> Ranks playbook matches
  -> Shows recommended action
```

**Used for:** Find the best playbook for a current issue.

**Example:** Symptoms mention MG22 and SMTP timeout. Runtime suggests "Resend existing output."

## 7. Review Queue

```text
System suggests important action
  -> Action waits for human review
  -> Reviewer checks context/evidence
  -> Reviewer approves, modifies, or rejects
```

**Used for:** Human approval before trusting important AI/system actions.

**Example:** Reviewer approves resend, but rejects full workflow rerun.

## 8. Execution

```text
Approved action/playbook step
  -> Execution run starts
  -> Approval request may be created for risky step
  -> Step completes, fails, or is aborted
  -> Result is recorded
```

**Used for:** Run or track approved playbook actions safely.

**Example:** Resend output email is executed after approval.

## 9. Decisions

```text
Runtime/reviewer chooses an action
  -> Decision record is created
  -> Options, rationale, confidence, and outcome are stored
  -> Future similar cases can reuse this learning
```

**Used for:** Explain what was decided and why.

**Example:** Decision says resend was chosen because rerun could duplicate finance output.

## 10. Episodes

```text
Related evidence IDs
  -> Backend sends title/body/time to LLM
  -> LLM groups same-issue evidence
  -> Episode story and steps are created
  -> Episode embedding is stored
```

**Used for:** Convert scattered evidence into one incident story.

**Important code behavior:** Max 20 evidence items are sent per LLM call. Each evidence body is limited to 2000 characters.

**Example:** Ticket, workflow log, SMTP log, and mailbox check become one MG22 episode.

## 11. Patterns

```text
Approved episodes with embeddings
  -> Compare episode embedding distance
  -> Similar episodes are grouped
  -> LLM summarizes the group into a Pattern
  -> Pattern links back to episodes
```

**Used for:** Find repeated problems.

**Technical rule:** `cosine_distance < 0.20` means similar enough for the same pattern.

**Example:** Multiple MG22 delivery failure episodes become one MG22 delivery failure pattern.

## 12. Playbooks

```text
Pattern is found
  -> LLM generates candidate playbook
  -> Negative Knowledge is included to avoid bad steps
  -> Human reviews lifecycle
  -> Approved playbook becomes available to Runtime
```

**Used for:** Turn repeated problems into reusable fix steps.

**Example:** Pattern creates playbook "Resend MG22 existing output after SMTP timeout."

## 13. Negative Knowledge

```text
Bad/risky action is known
  -> Store as Negative Knowledge
  -> Runtime and playbook generation use it
  -> Bad action is avoided or lowered in confidence
```

**Used for:** Remember what not to do.

**Example:** "Do not rerun MG22 full workflow unless generation failed; it may duplicate output."

## 14. Identities

```text
Evidence mentions names/entities
  -> Extract entity names
  -> Normalize aliases
  -> Link aliases to canonical identity
  -> Search, graph, and correlation improve
```

**Used for:** Understand that different names can mean the same real thing.

**Example:** "MG22", "MG22 workflow", and "Monthly GL output job" can point to the same workflow identity.

## 15. Correlations

```text
New evidence arrives
  -> System compares it with related evidence
  -> Correlation edge is created
  -> Related evidence can trigger episode reconstruction
```

**Used for:** Connect evidence records that belong together.

**Example:** ServiceNow ticket, SMTP log, and mailbox evidence are linked because they describe the same MG22 issue.

## 16. Graph Explorer

```text
Records and graph edges exist
  -> User selects node/type/domain/depth
  -> Backend traverses graph with bounded BFS
  -> UI shows connected records
```

**Used for:** See relationships between sessions, evidence, episodes, patterns, playbooks, decisions, users, and actions.

**Example:** Open MG22 session graph and see evidence, decision, playbook, approval, and execution connected.

## 17. Contradictions

```text
Approved playbook step
  -> Step embedding finds top KB evidence
  -> Token overlap checks common important words
  -> LLM confirms direct conflict
  -> Contradiction row is created
  -> Human reviews status
```

**Used for:** Catch conflicting knowledge.

**Example:** Playbook says "rerun MG22"; KB says "do not rerun MG22 because duplicate output risk."

## 18. Drift

```text
Approved playbooks exist
  -> Scheduled drift check runs
  -> Checks age, expiry, validation, and negative feedback
  -> Drift alert is created
```

**Used for:** Find old playbooks that may no longer be safe.

**Example:** A playbook has not been validated for 180 days, so Drift flags it.

## 19. Evaluations

```text
Create test dataset
  -> Run evaluation
  -> System tests retrieval/recommendation
  -> Compare actual answer with expected answer
```

**Used for:** Test if AI/search logic is working correctly.

**Example:** MG22 test case should return "resend existing output", not "rerun full workflow."

## 20. Policies

```text
Admin creates policy
  -> Assign policy to source/evidence/playbook
  -> Backend enforces access, retention, classification, or approval rule
```

**Used for:** Governance and control.

**Example:** Medium-risk production actions require human approval.

## 21. Audit Log

```text
Important user/system action happens
  -> Audit event is written
  -> Audit Log shows who did what and when
```

**Used for:** Compliance and traceability.

**Example:** Audit Log records who approved an MG22 resend action.

## 22. LLM Cost

```text
LLM or embedding call happens
  -> Usage event is recorded
  -> Cost and token usage are aggregated
  -> Budget status is shown
```

**Used for:** Track AI cost and budget limits.

**Example:** Admin sees episode reconstruction used extraction tokens today.

## 23. Settings

```text
Admin configures tenant/workspace/domain/users
  -> Roles and domain setup are stored
  -> Other tabs use this configuration
```

**Used for:** Organization setup.

**Example:** Admin creates "Finance Operations" domain and assigns users.

## MG22 End-To-End Demo Flow

```text
1. Sources: Add ticket/log/email sources.
2. Sync Operations: Import latest records.
3. Evidence: Store MG22 ticket, workflow log, SMTP log, mailbox check.
4. Identities: Normalize MG22 workflow and finance mailbox.
5. Correlations: Link the MG22 evidence records.
6. Episodes: Build one incident story from related evidence.
7. Patterns: Group similar MG22 episodes from history.
8. Playbooks: Create approved resend playbook from pattern.
9. Negative Knowledge: Store "do not rerun full workflow".
10. Runtime: Recommend resend playbook for new MG22 issue.
11. Sessions: Store the live MG22 case.
12. Review Queue: Human approves resend recommendation.
13. Execution: Resend action is performed or tracked.
14. Decisions: Store why resend was chosen.
15. Graph Explorer: Show all connected records.
16. Contradictions: Flag old rerun guidance if it conflicts.
17. Drift: Later check if playbook is stale.
18. Evaluations: Test that MG22 still returns correct playbook.
19. Policies: Enforce approval and access rules.
20. Audit Log: Record all important actions.
21. LLM Cost: Show AI usage cost.
22. Settings: Manage domains/users.
23. Overview: Show overall system health.
```

**One-line demo story:**
For MG22 output not received, ContextEdge collects evidence, builds an episode, finds a recurring pattern, recommends the safe resend playbook, waits for human approval, executes the approved action, and records everything for audit.
