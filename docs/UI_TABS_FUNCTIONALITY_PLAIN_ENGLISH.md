# ContextEdge UI Tabs - Plain English Functionality Guide

This document explains every main UI tab in simple business language. Each section covers what the tab is for, why it is needed, the main functionality available there, and a practical example.

## Project Logic Sequence For Demo

The sidebar order is the UI navigation order. For explaining the project logic in a demo, use this workflow order instead:

```text
1. Overview
2. Sources
3. Sync Operations
4. Evidence
5. Sessions
6. Runtime
7. Review Queue
8. Execution
9. Decisions
10. Episodes
11. Patterns
12. Playbooks
13. Negative Knowledge
14. Identities
15. Correlations
16. Graph Explorer
17. Contradictions
18. Drift
19. Evaluations
20. Policies
21. Audit Log
22. LLM Cost
23. Settings
```

**Simple explanation:**
Overview shows the current health of the system first. Sources define where data comes from, Sync Operations imports it, Evidence stores it, Sessions handle one live issue, Runtime suggests the best action, Review Queue lets a human approve or reject, Execution runs approved actions, and governance tabs keep everything safe and auditable.

**Short demo line:**
Overview shows status. Sources bring data. Sync imports it. Evidence stores it. Session opens a case. Runtime suggests a fix. Review Queue gets human approval. Execution runs the approved action. Decisions and Audit Log record what happened.

## 1. Overview

**What this tab is used for:**
The Overview tab is the main health snapshot of ContextEdge. It shows whether data is flowing into the platform, how much evidence has been collected, how many episodes and playbooks exist, and where attention may be needed.

**Why this tab is needed:**
Users need one starting page where they can quickly understand whether the system is healthy. Without this page, an operator would need to open Sources, Evidence, Episodes, and Playbooks separately just to know the current status.

**Main functionality:**

- Shows high-level counts for sources, evidence, episodes, and playbooks.
- Highlights ingestion health and freshness signals.
- Helps users decide which area needs attention first.
- Acts as the landing page for daily monitoring.

**Example:**
An admin logs in every morning and opens Overview. They see that evidence count increased overnight, but one source has not synced recently. They click into Sources or Sync Operations to investigate.

## 2. Sources

**What this tab is used for:**
The Sources tab manages all data sources connected to ContextEdge, such as local files, tickets, chat systems, email, ServiceNow, Jira, or other operational tools.

**Why this tab is needed:**
ContextEdge can only learn from operational evidence if it knows where to collect that evidence from. This tab is the control point for setting up and managing those connections.

**Main functionality:**

- Lists configured data sources.
- Adds new sources.
- Shows source type, sync mode, and status.
- Opens source details for policy assignment, credential rotation, discovery, and recent sync runs.
- Supports local folder ingest for demos or offline data imports.

**Example:**
An admin adds a folder containing exported incident logs. ContextEdge ingests those files and turns them into searchable evidence.

## 3. Sync Operations

**What this tab is used for:**
Sync Operations monitors background data collection jobs, backfills, retries, and failed sync items.

**Why this tab is needed:**
Source setup is not enough. Users also need to know whether data is actually being pulled successfully. This tab helps troubleshoot ingestion failures before they affect search, episodes, or playbooks.

**Main functionality:**

- Shows sync run history.
- Displays job status, failures, retries, and dead-letter items.
- Helps users identify stuck or failed ingestion jobs.
- Supports cleanup actions for old or failed sync records.

**Example:**
A Jira source was added yesterday, but no new tickets appear in Evidence. The admin opens Sync Operations and sees that the sync job failed because the credential expired.

## 4. Evidence

**What this tab is used for:**
The Evidence tab is the searchable library of raw and normalized operational records collected from all sources.

**Why this tab is needed:**
Every decision, episode, pattern, and playbook must be backed by real evidence. This tab lets users inspect the source material instead of trusting summaries blindly.

**Main functionality:**

- Searches and browses evidence across all sources.
- Shows evidence title, type, relevance, source, and ingest time.
- Opens evidence details with provenance, thread summary, attachments, and context.
- Supports deleting or bulk deleting evidence when allowed.
- Supports thread hydration when a thread is incomplete.

**Example:**
An analyst searches "VPN authentication failure" and finds Jira tickets, Teams messages, and logs that describe the same outage.

## 5. Sessions

**What this tab is used for:**
Sessions track governed investigation or resolution work for a specific live case.

**Why this tab is needed:**
When users work on a real incident, the platform needs to remember the context, decisions, evidence, and actions taken. Sessions provide that structured case workspace.

**Why this is important in a demo:**
This shows that ContextEdge does not treat every recommendation as a loose chat message. Each issue has a proper case file where the problem, related systems, decisions, approvals, and final outcome are stored together.

**Main functionality:**

- Lists resolution sessions.
- Creates new sessions for active issues.
- Shows session status, case details, and decision traces.
- Links decisions and evidence to the session.
- Supports closing sessions when work is complete.

**Example:**
A support engineer opens a session for "User did not receive MG22 output." The session records the diagnosis, recommended action, approval, execution result, and final outcome.

**Simple demo explanation:**
Session is the case file for one issue. For example, "MG22 output not received" becomes one session. Everything about that issue is stored inside the session.

## 6. Runtime

**What this tab is used for:**
Runtime is a diagnostic screen for testing the live playbook retrieval system.

**Why this tab is needed:**
When an incident happens, downstream tools and agents call runtime APIs to find the best playbook. This tab lets admins and engineers test the same behavior directly from the UI.

**Main functionality:**

- Sends symptoms and entities to the live retrieval ranker.
- Returns ranked playbook matches.
- Shows match confidence and risk-aware results.
- Fetches explain details for a match when cached.
- Allows users to inspect published runtime playbook versions.
- Shows retrieval feedback.

**Example:**
An engineer enters "output not received, SMTP timeout, workflow completed." Runtime returns the "Resend output after SMTP timeout" playbook as the top match.

## 7. Review Queue

**What this tab is used for:**
The Review Queue is where humans review important AI-generated or system-generated decisions before they become trusted actions.

**Why this tab is needed:**
ContextEdge uses AI to reconstruct incidents, recommend decisions, and support automation. For safety and governance, high-impact decisions should not be accepted blindly. This tab gives experts a place to approve, reject, or modify recommendations.

**Why this is important in a demo:**
This proves that ContextEdge is not allowing AI to take important actions by itself. The system can suggest the best action, but a human reviewer still controls whether the action is accepted, changed, or rejected.

**Main functionality:**

- Lists pending decisions ranked by confidence.
- Shows decision context, rationale, similar past decisions, and evidence.
- Allows reviewers to approve a decision.
- Allows reviewers to reject a decision with structured reasons.
- Allows reviewers to modify a proposed action before approval.

**Example:**
The system recommends resending a workflow output instead of rerunning the full workflow. A reviewer checks the evidence, confirms that rerunning could create duplicate output, and approves the resend action.

**Simple demo explanation:**
Review Queue is the human approval screen. The AI suggests what should happen next, but the human makes the final decision.

- Approve means "Yes, continue."
- Modify means "Change the action first, then continue."
- Reject means "No, this suggestion is wrong."

## 8. Execution

**What this tab is used for:**
Execution manages human approval requests for automated playbook steps.

**Why this tab is needed:**
Some actions are safe to suggest but risky to execute automatically. Execution creates a controlled approval layer before those actions run.

**Main functionality:**

- Lists pending execution approval requests.
- Shows action details, risk, and requested inputs.
- Lets approved users allow, deny, or modify execution requests.
- Creates an audit trail for automation decisions.

**Example:**
A playbook wants to resend a production output email. Because it affects a business user, the step requires human approval. The approver checks the request and approves it.

## 9. Decisions

**What this tab is used for:**
Decisions show the reasoning trail behind recommendations, approvals, rejections, and selected actions.

**Why this tab is needed:**
For trust and auditability, users must be able to see not just what the system decided, but why it decided it.

**Main functionality:**

- Lists decision records.
- Shows decision status, intent, confidence, and rationale.
- Opens decision chains.
- Shows evidence, options considered, selected option, and outcomes.
- Helps teams inspect why one action was chosen over another.

**Example:**
The system considered two options: rerun the workflow or resend the existing output. The Decisions tab shows that rerun was rejected due to duplicate output risk, while resend was selected.

## 10. Episodes

**What this tab is used for:**
Episodes are reconstructed troubleshooting stories created from related evidence. They explain what happened, what was tried, what worked, and what the final outcome was.

**Why this tab is needed:**
Raw evidence is often messy. A ticket may have many comments, while the real fix may be hidden inside a chat thread. Episodes turn scattered records into a readable incident timeline.

**Main functionality:**

- Lists reconstructed troubleshooting episodes.
- Starts episode reconstruction from evidence.
- Opens episode details with ordered steps.
- Allows review and approval of reconstructed episodes.
- Helps trigger pattern clustering from approved or relevant episodes.

**Example:**
Five pieces of evidence from Jira, Teams, and ServiceNow are reconstructed into one episode: "VPN gateway memory leak - east-01." The episode shows observation, hypothesis, action, and verification steps.

## 11. Patterns

**What this tab is used for:**
Patterns show recurring operational issues discovered across multiple episodes or evidence items.

**Why this tab is needed:**
One incident is useful, but repeated incidents reveal a bigger problem. Patterns help teams identify common failures and convert them into reusable knowledge.

**Main functionality:**

- Lists detected operational patterns.
- Shows pattern type, confidence, episode count, and freshness.
- Links patterns to supporting evidence.
- Opens pattern details and graph views.
- Can generate a playbook from a pattern.

**Example:**
ContextEdge detects that five incidents were caused by SMTP timeout after output generation. It creates a pattern showing that the successful fix was to resend the existing output instead of rerunning the workflow.

## 12. Playbooks

**What this tab is used for:**
Playbooks are governed step-by-step procedures for resolving operational problems.

**Why this tab is needed:**
The main goal of ContextEdge is to turn past operational experience into trusted, reusable, evidence-backed actions. Playbooks are the durable output of that process.

**Main functionality:**

- Lists candidate, approved, restricted, deprecated, expired, or retired playbooks.
- Opens playbook details and versions.
- Shows stable key, automation mode, lifecycle state, and risk tier.
- Supports lifecycle transitions such as review, approval, restriction, or retirement.
- Supports version comparison and rollback.

**Example:**
A playbook called "Resend output after SMTP timeout" is approved after review. The next time a similar issue occurs, runtime retrieval can recommend this playbook.

## 13. Negative Knowledge

**What this tab is used for:**
Negative Knowledge stores steps, fixes, and approaches that are ineffective, conditional, deprecated, or prohibited.

**Why this tab is needed:**
Good operational memory should remember failures too. This prevents the system and users from repeating actions that did not work or should not be used.

**Main functionality:**

- Lists negative knowledge entries.
- Adds new "do not do this" guidance.
- Edits or deletes existing entries.
- Categorizes entries as ineffective, conditional, deprecated, or prohibited.
- Feeds safer retrieval and recommendations.

**Example:**
For "output not received" cases, the team records: "Do not rerun the full workflow unless output generation failed, because rerun may create duplicate reports."

## 14. Identities

**What this tab is used for:**
Identities manage canonical entities and aliases found across different evidence sources.

**Why this tab is needed:**
The same person, system, workflow, or service may appear under different names in tickets, logs, and chat. Identity resolution connects those aliases so ContextEdge understands they refer to the same thing.

**Main functionality:**

- Lists canonical identities.
- Shows aliases and linked evidence.
- Allows users to merge duplicates.
- Allows users to edit identity metadata.
- Improves search, correlation, graph links, and episode reconstruction.

**Example:**
"john.smith," "J. Smith," and "jsmith" appear in different tools. The Identities tab merges them into one canonical person.

## 15. Correlations

**What this tab is used for:**
Correlations manage links between evidence items that are related causally, temporally, semantically, or structurally.

**Why this tab is needed:**
Important incidents often span multiple systems. Correlations connect the dots so ContextEdge can reconstruct the full story.

**Main functionality:**

- Lists correlation edges between evidence records.
- Creates new correlations.
- Deletes incorrect correlations.
- Accepts, rejects, splits, or merges correlation decisions.
- Supports investigation, episode reconstruction, and graph building.

**Example:**
A monitoring alert, Jira ticket, and Teams thread all mention the same outage. Correlations link them together so the episode extractor treats them as one incident.

## 16. Graph Explorer

**What this tab is used for:**
Graph Explorer visually explores relationships between evidence, episodes, playbooks, decisions, sessions, users, entities, and actions.

**Why this tab is needed:**
Some operational questions are relationship questions. A table can show records, but a graph shows how records connect.

**Main functionality:**

- Shows graph statistics.
- Explores subgraphs by entity type, entity ID, depth, and domain.
- Shows neighbors around a selected node.
- Lets users click through connected nodes.
- Helps inspect context used by agents and reviewers.

**Example:**
An auditor opens Graph Explorer for a resolution session and sees the evidence, decision, approval request, execution run, and outcome connected in one view.

## 17. Contradictions

**What this tab is used for:**
Contradictions show conflicts between trusted playbooks and newer or competing evidence.

**Why this tab is needed:**
Operational knowledge can become stale. If a new ticket or knowledge-base article says the old fix is wrong, the system must surface that conflict before users continue relying on outdated guidance.

**Main functionality:**

- Lists detected contradictions.
- Shows conflict severity and status.
- Compares playbook knowledge against evidence.
- Allows users to mark contradictions as reviewed or resolved.

**Example:**
An approved playbook says to restart a service, but a new vendor bulletin says restarting causes data loss for the latest version. The contradiction is flagged for review.

## 18. Drift

**What this tab is used for:**
Drift shows playbooks that may be stale, expired, unvalidated, or receiving negative feedback.

**Why this tab is needed:**
Operational procedures change. A playbook that worked six months ago may become unsafe or ineffective. Drift keeps approved knowledge current.

**Main functionality:**

- Lists drift alerts.
- Shows signals such as validation age, expiry, and negative retrieval feedback.
- Helps users decide which playbooks need review.
- Supports scheduled drift checks through background workers.

**Example:**
A VPN playbook has not been validated in 180 days and recently received three negative feedback events. Drift flags it for review.

## 19. Evaluations

**What this tab is used for:**
Evaluations replay historical cases against the current retrieval and ranking logic.

**Why this tab is needed:**
Teams need to know whether ContextEdge is recommending the right playbooks. Evaluations test quality before changes affect real users.

**Main functionality:**

- Lists evaluation datasets.
- Creates datasets for test cases.
- Starts evaluation runs.
- Shows pass/fail and ranking results.
- Helps compare current retrieval behavior against expected answers.

**Example:**
A knowledge manager creates a dataset of 20 past VPN incidents. After a ranker change, they run an evaluation to confirm the correct VPN playbook still appears at the top.

## 20. Policies

**What this tab is used for:**
Policies define governance rules for retention, classification, access, retrieval, and approval gates.

**Why this tab is needed:**
Different organizations and domains have different compliance, access, and safety requirements. Policies let admins control how ContextEdge behaves.

**Main functionality:**

- Lists tenant policies.
- Creates new policies.
- Edits or deletes existing policies.
- Manages retention, classification, access, and approval rules.
- Supports governance for sources, evidence, retrieval, and execution.

**Example:**
A tenant admin creates a policy requiring human approval before any medium-risk production remediation step can run.

## 21. Audit Log

**What this tab is used for:**
The Audit Log records important user and system actions.

**Why this tab is needed:**
For compliance and accountability, teams must know who changed what, who approved what, and who accessed sensitive information.

**Main functionality:**

- Lists audit events.
- Tracks admin, reviewer, retrieval, and policy actions.
- Supports filtering by event details.
- Provides an accountability trail for governance reviews.

**Example:**
During an audit, the compliance team checks who approved a production remediation action and when it was executed.

## 22. LLM Cost

**What this tab is used for:**
LLM Cost shows token usage, estimated spend, cache-hit rate, model/task breakdown, and tenant budget status.

**Why this tab is needed:**
AI calls cost money. Admins need visibility and budget controls so usage does not grow unexpectedly.

**Main functionality:**

- Shows estimated cost and token usage.
- Shows prompt, cached prompt, and completion token breakdown.
- Shows model and task-level usage.
- Displays daily budget status.
- Allows tenant admins to configure token and cost caps.
- Supports warn or block behavior when a budget is exceeded.

**Example:**
After enabling a new extraction workflow, the tenant admin sees daily LLM cost increase. They set a daily budget in warn mode first, then switch to block mode once the limit looks correct.

## 23. Settings

**What this tab is used for:**
Settings manages organization-level configuration such as tenant, workspaces, domains, and users.

**Why this tab is needed:**
ContextEdge is multi-tenant and role-aware. Settings gives admins one place to manage the structure of the organization inside the platform.

**Main functionality:**

- Shows tenant information.
- Lists workspaces.
- Creates new workspaces.
- Lists domains.
- Creates new domains.
- Lists users.
- Supports organization and access setup.

**Example:**
A tenant admin creates a new "Network Operations" domain so VPN-related evidence, playbooks, and policies can be managed separately from Finance Operations.

## End-to-End Example: One Incident Across Tabs

Scenario: A business user reports, "I did not receive my MG22 output today."

1. **Sources** defines where ContextEdge can collect evidence from, such as workflow logs, tickets, and chat.
2. **Sync Operations** confirms the latest logs and tickets were ingested successfully.
3. **Evidence** shows the workflow completed, but the email delivery step failed with an SMTP timeout.
4. **Correlations** links the log, ticket, and chat conversation together.
5. **Episodes** reconstructs the troubleshooting timeline.
6. **Patterns** recognizes that this issue has happened before.
7. **Negative Knowledge** warns not to rerun the whole workflow because it may create duplicate output.
8. **Runtime** recommends the approved playbook for resending the existing output.
9. **Decisions** records why resend was chosen instead of rerun.
10. **Execution** asks for human approval before taking the action.
11. **Review Queue** lets the reviewer approve, reject, or modify the proposed decision.
12. **Sessions** stores the full case history.
13. **Graph Explorer** shows the connected evidence, decision, approval, action, and outcome.
14. **Audit Log** records who approved the action.
15. **Drift** later checks whether the playbook remains current.
16. **Evaluations** tests whether the same issue still retrieves the correct playbook.
17. **LLM Cost** shows the cost of AI extraction and retrieval for this workflow.
18. **Policies** and **Settings** control who can do each action and under which rules.
