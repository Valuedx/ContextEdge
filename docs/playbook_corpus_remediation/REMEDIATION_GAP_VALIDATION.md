# Independent Validation of the 2026-08-26 Playbook Remediation

**Scope:** verification of the claims in the "Database-side playbook changes — final summary" for
`AEProdSupport`, and a gap report on playbooks that are still wrong relative to their linked ticket.

**Date:** 2026-08-26 · **Validator:** independent re-derivation from artefacts, not from the summary.

> **Status — the repair is written and rehearsed, but not yet run against the live database.**
> This session can read and write files on the machine; it has no shell there, so it cannot execute.
> Everything below was validated against a Postgres fixture rebuilt step-for-step from
> `remediation_decisions.jsonl` (same 420 playbooks, same 1,847 steps).

## Runbook

```
cd D:\ContextEdge_pro\ContextEdge

python backend/scripts/fix_remediation_defects.py            # dry run - changes nothing
python backend/scripts/fix_remediation_defects.py --apply    # commits
python backend/scripts/verify_playbook_corpus.py             # independent read-only re-check
```

All three read `DATABASE_URL_SYNC` from `.env`. The verifier writes
`docs/playbook_corpus_remediation/corpus_verification.json`.

**Rehearsal result — this is what you should see:**

| Step | Result |
|---|---|
| dry run | 72 playbooks · 93 step edits · 2 re-inserted · 0 skipped |
| `--apply` | same, committed; active steps 1,847 → 1,849 |
| re-run `--apply` | 0 changes, 0 skipped — idempotent |
| tamper test (hand-edit a repaired step, re-run) | 1 skipped and named — the guard refuses to overwrite |
| verify → check C | **residual defects: 0**, 5 ticket-verified edits excluded |

Two bugs surfaced in the repair script during that rehearsal and are fixed: it printed its report
before committing, so piping to `head` closed stdout mid-print and silently rolled the apply back;
and it matched steps by index, so re-inserting a step shifted later indices and produced a spurious
"skipped" on the second run. It now commits first and matches by text.

Files added or changed in the repo:

| Path | What |
|---|---|
| `backend/scripts/remediate_playbook_corpus.py` | `unhedge()` rewritten — the root cause |
| `backend/scripts/fix_remediation_defects.py` | the repair, dry-run by default |
| `backend/scripts/verify_playbook_corpus.py` | read-only verification, writes a JSON verdict |
| `backend/scripts/score_concreteness.py` | `re.IGNORECASE` bug fixed — see G12 |
| `docs/playbook_corpus_remediation/remediation_defect_patch.json` | the 95 pre-computed replacements |
| `docs/playbook_corpus_remediation/playbook_original_steps.json` | pre-remediation step texts, what the verifier diffs against |

---

## 0. What was verified, and what was not

| Source | Used as | Status |
|---|---|---|
| `data/playbook_remediation_backup_2026-08-26/aeprodsupport_playbook_tables.dump` | pre-change ground truth (restored and queried) | ✅ authoritative |
| `docs/playbook_corpus_remediation/remediation_decisions.jsonl` (440 rows) | the applied decision set incl. `new_step_texts` | ✅ authoritative |
| `docs/playbook_corpus_remediation/apply_summary.json` | the script's self-report | ⚠️ self-reported |
| `ALL_440_PLAYBOOKS_CORPUS_DEEP_AUDIT.md` | per-playbook ticket root cause + outcome | ✅ used as evidence |
| `PLAYBOOK_SPECIFICITY_AUDIT.md` | pre-change concreteness bands | ✅ used as evidence |
| `backend/src/contextedge/**` | retrieval, embedding and lifecycle semantics | ✅ read |
| **Live `AEProdSupport` database** | post-change state | ❌ **not reachable from this session** |

Because the live database could not be queried, the post-change state was **reconstructed** from
`remediation_decisions.jsonl` plus the documented correction pass. That reconstruction is sound:
the reconstructed corpus has **1,847 steps across 420 active playbooks**, which matches
`apply_summary.json` exactly. Anything below marked ⓛ needs a live-DB confirmation.

---

## 1. Claims that check out

| Claim | Verdict | Evidence |
|---|---|---|
| 440 playbooks reviewed, IDs match the corpus | ✅ | decision IDs are a bijection with the 440 rows in the backup |
| 187 KEEP / 233 IMPROVE / 20 SUPPRESS | ✅ | recount of the JSONL |
| KEEP playbooks were genuinely left alone | ✅ | **0** of 187 have any text difference from the backup |
| No playbook, pattern or episode hard-deleted | ✅ | apply summary + no delete path exercised |
| Versions 630 → 863 | ✅ | backup holds exactly 630; 630 + 233 = 863 |
| No new steps invented | ✅ | step-count delta is **only ever 0, −1, −2 or −3** — never positive |
| Total steps 1,847 after | ✅ | independently recomputed |
| Hedging genuinely reduced | ✅ | `such as` / `e.g.` steps **231 → 23** |
| 35 unverified version suffixes existed | ✅ | exactly 35 suffixed steps across 25 playbooks in the JSONL |
| `lexical_search_text` needed refreshing | ✅ | it does contain step text (all steps of a playbook appear in it) |
| In-place edits were legal despite the immutability trigger | ✅ | `trg_playbook_versions_steps_immutable` fires on **published** rows only; the new versions have `published_at IS NULL` |

---

## 2. Gap report

Findings are ordered by operational severity, not by size.

### G1 — BLOCKER: none of this reaches an agent. Zero playbooks are retrievable today.

Every agent-facing retrieval arm requires `lifecycle_state = 'approved'` **and** a published version:

- `search/playbook_candidates.py:55` — `if pb.lifecycle_state != "approved": return False`, applied to
  every arm; the same filter is repeated at lines 166, 207, 316, 399.
- `search/hybrid_ranker.py:76-101, 384-391` — drops any playbook with no version where
  `published_at IS NOT NULL`.
- `api/v1/runtime.py:329-335` — same for stable-key lookup.

The backup shows the pre-change corpus was already **440/440 `candidate`, 0 published versions**
(`revert_approved_playbooks.py` had put it there). The remediation script inserts the 233 new versions
**without** `published_at`/`published_by` (`remediate_playbook_corpus.py:1085-1118`) and the summary
states no auto-approval was performed.

**Consequence:** `POST /api/v1/runtime/match` returns **zero** playbooks for this tenant. The
distinction the summary draws — "retired playbooks are hidden from agent retrieval (approved-only)" —
is true but vacuous: *all* 420 are hidden. Suppressing 20 playbooks changes nothing an engineer sees.
Meanwhile `GET /api/v1/playbooks` has no default lifecycle filter (`api/v1/playbooks.py:82-204`), so
the admin list still returns retired ones.

### G2–G5 — root cause: one function, four defects.

All of G2, G3 and G4 come from `remediate_playbook_corpus.py :: unhedge()` (lines 649–673 as shipped):

| # | Line | Defect | Symptom |
|---|---|---|---|
| 1 | 665 | `\(?\s*` consumes the space before the hedge; `repl` returns no leading space | `an external API tool such as Postman` → `…API toolPostman` |
| 2 | 665 | `([^)\n]+)` runs greedily to the next `)` or end of line, so returning one coordinate discards the sentence remainder | `…passing flags such as --disable-gpu and --disable-software-rasterizer or deploying an updated runner configuration JAR.` → `…passing flags--disable-gpu.` |
| 3 | 654 | `.strip(".,;")` removes the terminal full stop | 71 steps lost their final `.` |
| 4 | 671 | `re.sub(r"\s+([,.;])", r"\1", …)` deletes the space before **any** period, including a filename's leading dot — and it runs on every step of an IMPROVE playbook, hedge or not | `delete the psplugins and .process-studio directories` → `…and.process-studio directories` |

**Repair scope, after classifying every edited step against its pre-change original:**

| Class | Steps | Fix |
|---|---:|---|
| whitespace collapse only (defect 4) | 27 | re-insert the space; hedging left as-is |
| sentence damaged by the de-hedge (defects 1–3) | 51 | restore the original wording |
| terminal period only (defect 3) | 15 | restore the full stop |
| deleted step that carried the fix action | 2 | re-insert |
| **total** | **95 steps / 72 playbooks** | |

Steps produced by `VERIFIED_REPLACEMENTS` are excluded from the repair — those five edits are correct
(see G6). The sections below describe each defect class in detail.

### G2 — HIGH: whitespace collapse. 27 steps.

Removing `such as` / `e.g.` / `for example` did not preserve the surrounding whitespace, so words
fused together and file extensions lost their leading space. Sample:

| Playbook | Before | After |
|---|---|---|
| Advanced REST Client SSL Handshake Failure | `…an external API tool such as Postman to confirm…` | `…an external API toolPostman to confirm…` |
| AutomationEdge Plugin and Library Compatibility | `…delete the psplugins and .process-studio directories…` | `…delete the psplugins and.process-studio directories…` |
| AutomationEdge Plugin and Library Compatibility | `Open the .psw workflow file…` | `Open the.psw workflow file…` |
| Chrome Browser Automation Compatibility | `…quarantined by antivirus software such as Trend Micro.` | `…quarantined by antivirus softwareTrend Micro` |
| Client-Side Network and Firewall Restrictions | `…inside the .psrc configuration file…` | `…inside the.psrc configuration file…` |
| Excel Plugin Protected File Handling | `(.xls, .xlsx, or .xlsb)` | `(.xls,.xlsx, or.xlsb)` |
| Post-Deployment UI Asset Configuration Drift | `…one of .jpg, .png, .svg, or .gif.` | `…one of.jpg,.png,.svg, or.gif.` |
| AutomationEdge Process Studio Project and Workspace Recovery | `update the .settings file` | `update the.settings file` |

Full list of affected playbooks (29 steps): AutomationEdge Process Studio Project and Workspace
Recovery (4), Excel Plugin Protected File Handling and Engine Configuration (4), AutomationEdge Plugin
and Library Compatibility Troubleshooting (2), AutomationEdge Process Studio Launch Failure
Remediation (2), Client-Side Network and Firewall Restrictions Troubleshooting (2), Handling
Unsupported File Types in Message Parsing Workflows (2), Post-Deployment UI Asset Configuration Drift
Remediation (2), Process Studio Resource Contention and Performance Degradation (2), Workflow
Operation Failure Due to Plugin or Environment Configuration Issues (2), Advanced REST Client SSL
Handshake Failure (1), AutomationEdge File Upload and Configuration Troubleshooting (1), Chrome
Browser Automation Compatibility and Configuration Drift (1), Intermittent Process Output Mismatch Due
to Plugin Configuration (1), New Feature Planning and Guidance (1), Process Studio and Plugin
Connectivity/Synchronization/Registration Troubleshooting (1), RPA Workflow Failures During Excel File
Operations (1).

This matters more than a typo: these are the exact strings an engineer copies — `.psw`, `.psrc`,
`.process-studio`, `.pluginconf` — and they now read as part of the preceding word.

A related cosmetic symptom of the same regex: **71 steps lost their terminal full stop.**

### G3 — HIGH: three steps were truncated, losing real content.

| Playbook | Content lost |
|---|---|
| Automated Browser Instability Post Chrome Update | `--disable-software-rasterizer or deploying an updated runner configuration JAR` — 11 words. The step now ends `…passing flags--disable-gpu.` |
| Missing Application Dependency JAR After Deployment | `are absent from the WEB-INF/lib directory` — the step now ends mid-sentence: `…confirm if required dependency JAR files,snakeyaml-1.33.jar` |
| Incorrect Microsoft Graph API Endpoint for SharePoint | `or environment property files` — narrows the search to `web.xml` only |

### G4 — HIGH: removing `such as` turns an *example* into an *assertion*. 51 steps.

This is the failure mode the summary claims was avoided ("unhedged only when the ticket already named
the value"). The tell is an example that begins with an article: deleting the marker leaves two bare
noun phrases butted together — *"the preferred technology a Python script was blocked"*, *"an
alternative bypass asset—an updated User Acceptance Testing (UAT or production license file—"*. An
example beginning with a bare identifier is unaffected — *"driver file RedshiftJDBC42-\*.jar is
present"* reads correctly, and the repair leaves those alone.

It hit 8 of the 9 playbooks the deep audit had rated *Ready for Production*:

| Playbook | After |
|---|---|
| AutomationEdge Component Startup… | `confirm compatibility,using Java 11 where Java 8 is unsupported.` |
| Remediating Plugin Sync Failures | `Trigger synchronization of the plugin the Web GUI plugin from Process Studio…` |
| GUI Automation and Spy Troubleshooting | `…whether the required browser extension the Chrome extension is installed…` |
| Intermittent Workflow Stalling | `…un-terminated sub-processes 7-Zip or Selenium browser drivers.` |
| AutomationEdge ActiveMQ Configuration | `Check for syntax errorsan extra '/' in <policyEntry…` |

Only *AutomationEdge Redshift Connectivity Failure* — the specificity audit's exemplar — survived the
pass cleanly.

### G5 — HIGH: two deleted "backup" steps carried the actual fix.

32 backup steps were deleted (26 from `medium`-risk playbooks, 1 from a `high`-risk one). Two of them
were compound steps whose *remediation action* went with the backup clause:

- **GUI Automation and Spy Troubleshooting in Process Studio** — deleted:
  *"If encountering javassist/sync errors: Take a backup of the existing javassist JAR file from the
  Process Studio/Agent lib directory. **Copy the latest javassist JAR file into the lib folder,
  replacing the existing file.**"* The javassist JAR swap is the fix; the playbook no longer contains it.
- **AutomationEdge Process Studio Project and Workspace Recovery** — deleted:
  *"If logs report that .pluginsconf is tampered or corrupted, navigate to the conf folder… Take a
  backup of the .pluginsconf file **and delete it**…"* Deleting `.pluginsconf` is the ticket's actual
  resolution.

Separately: deleting the backup clause from config-editing playbooks on `medium`/`high` risk tiers
(`server.xml`, `cacerts`, `web.xml`, `ae.properties`) removes the rollback instruction from
destructive procedures. That is a policy decision, not a bug — but it was not stated as one.

### G6 — MEDIUM: the value corrections hold up. The summary claims a fifth that was never applied.

> **Correction to an earlier draft of this report.** Reading
> `strip_unverified_version_suffixes.VERIFIED_REPLACEMENTS` rather than inferring from the summary
> changes this finding substantially. The replacement strings are well scoped, and the log4j edit is the
> right way round — an earlier draft called it inverted. It is not.

| Correction as written in the code | Ticket evidence | Verdict |
|---|---|---|
| `ActiveMQ transport port 61616 or 61614` → `ActiveMQ transport port 61614` | #313308: *"slave failed to bind TCP port 61614 during takeover"* | ✅ correct |
| `replace the flagged vulnerable dependency JAR files…` → `replace log4j-core-2.25.3.jar in the ActiveMQ library path **with a patched log4j-core JAR**` | #428145: VAPT flagged 2.25.3 as the *vulnerable* artefact | ✅ correct direction |
| `the target release version required to patch the issue` → `the patched version from the finding (**AutomationEdge 8.2.5 for AppSec, or PostgreSQL 15 when the finding is PostgreSQL 11**)` | #272213 / #277768 | ✅ conditions match their tickets |
| Agent upgrade branch → **plugin release 4.5** | #219894 is a **ServiceNow Plugin 4.4** defect, fix promised in an *upcoming* 4.5 | 🔴 **no such entry exists in the code** |

What remains open: the summary lists *"Agent upgrade bug branch → plugin release 4.5 (219894)"* as one
of four applied corrections, but `VERIFIED_REPLACEMENTS` contains only three titles. It was either never
applied or applied out of band with no record — and 219894 is a ServiceNow-plugin defect, not an
agent-upgrade one, so it should not be applied now without a re-read.

Worth noting rather than fixing: both VAPT tickets closed with the upgrade *pending* or UAT-only, so
those steps name an intended target, not a verified production fix. And log4j-core 2.25.3 is a recent
release — worth one human glance that the scan really reported it. `fix_remediation_defects.py` prints
all four for review and edits none of them.

### G7 — MEDIUM: 48 of the 53 agreed-worthless playbooks are still in the active corpus.

The deep audit's corrected triage names **53 playbooks that both audits agree have zero concrete and
zero product-based content** — "suppress or label, highest priority". The remediation suppressed
**5** of them. The other 48 remain `candidate` — 22 as untouched KEEPs. Examples still live:
*Chrome Extension Installation Blocked by Network Proxy*, *Automation Edge Agent Insufficient
Administrative Privileges*, *Email Sent Without Intended Attachment*, *T3 User Login Failure Due to
Password Issues*, *Login Failure After License Renewal*, *Agent Enters Unknown State Causing Delivery
Failures*.

Conversely the 20 that *were* retired are almost all inquiry / denied-feature-request playbooks —
the right call — with one conflict: **SSO Concurrent Session Restriction Misunderstanding Resolution**
was rated `CORRECT & PRODUCT-BASED / Ready for Production` by the deep audit and was retired anyway.

### G8 — MEDIUM: "no unsupported filler to strip" is not true for 97 of the 187 KEEPs.

Cross-tabulating the decision file against the deep audit:

| Deep-audit verdict | KEEP | IMPROVE | SUPPRESS |
|---|---:|---:|---:|
| CRITICAL GAP (Full Rewrite Required) | **109** | 125 | 19 |
| DEFICIENT | 34 | 57 | 0 |
| MOSTLY PRODUCT-BASED | 25 | 42 | 0 |
| CORRECT & PRODUCT-BASED | 19 | 9 | 1 |

- **97 KEEP playbooks still carry 130 steps the deep audit labels `AI_PADDING`.** The KEEP rationale
  recorded in the decision file is *"Steps already match the linked issue/resolution; no unsupported
  filler to strip."*
- **109 KEEP playbooks contain zero `PRODUCT_BASED` steps at all.**

The deep audit itself concedes `CRITICAL GAP` is over-applied to ~200 playbooks, so 109 is not 109
defects — but it is 109 playbooks where two documents disagree and nobody adjudicated.

### G9 — MEDIUM: 33 escalation paths and 9 verification steps were deleted.

Of the 98 steps removed from active playbooks: **33 escalation**, **32 backup**, **9 verification**,
1 UAT, 23 other. Escalation removal affects playbooks like *Agent Failure Due to Server Configuration
Discrepancy*, *Bot Operational and Integration Troubleshooting*, *Browser Automation Failure Due to
Environmental Security Policies* — the step that told an engineer what to do when the playbook fails
is gone. Verification removal includes *License Invalidation Due to Server Hardware Replacement*
(conf 0.95, medium risk), which lost *"Verify that license details display properly in AEUI and
confirm that Process Studio launches without license invalidation errors"* — the only confirmation
the fix worked.

**17 active playbooks are now down to two steps or fewer**, including 5 on `medium` risk tier
(*Audit Log Purging Failure*, *Insecure Tomcat Auto-Deployment Remediation*, *Missing or Incorrect
Certificate Deployment*, *Monitoring Incident Data Ingestion Failure*, plus *Missing or Disabled Vault
Connection* which lost its only validation step).

### G10 — MEDIUM: the "remaining quality" bands in `apply_summary.json` are not comparable to the audits.

`apply_summary.json` reports 109 playbooks at 0% concrete after remediation.
`PLAYBOOK_SPECIFICITY_AUDIT.md` reported 61 before. Read side by side that looks like a catastrophic
regression. It is not — they come from **different scorers**.

Settled by scoring the pre-change backup with the scorer that actually produced `apply_summary.json`
(`remediate_playbook_corpus.remaining_quality`, concrete = `extract_coords(t) or AE_PRODUCT.search(t)`):

| Band | Pre-change (440 pb) | After remediation (420 pb) | After repair (420 pb) |
|---|---:|---:|---:|
| 0% concrete | 127 | 109 | 112 |
| 1–33% | 41 | 32 | 34 |
| 34–66% | 99 | 100 | 98 |
| 67–100% | 173 | 179 | 176 |
| generic / bare-inspect steps | 69 | 13 | 13 |
| hedged steps | 231 | 23 | 73 |
| concrete steps | 983 / 2,006 = 49.0% | — | 943 / 1,849 = 51.0% |

**There was never a regression.** On one scorer the 0% band went 127 → 109 across the remediation and
sits at 112 after the repair — the +3 is restored hedges coming back, which is the correct trade.
Hedged steps end at 73 rather than 23 for the same reason: 50 sentences that were only readable *with*
their hedge got it back.

What the numbers also say: the remediation removed filler — generic/bare-inspect 69 → 13 is a real win —
but barely moved specificity, 49.0% → 51.0%. Consistent with its policy of not inventing content, and it
means the corpus's central defect, vagueness, is untouched.

### G11 — MEDIUM: retrieval is now internally inconsistent — lexical sees the new steps, vector does not.

- `remediate_playbook_corpus.py:1131-1143` writes `lexical_search_text` as
  `title + description + ALL new step texts`, capped at **8,000** chars.
- The application's own composer `services/playbook_embedding.py:56-78` builds
  `title + description + trigger_conditions + first 20 step *labels*`, capped at **4,000** chars.

So `lexical_search_text` no longer matches what the app would generate, and the next PATCH or approval
of any of those playbooks will silently rewrite it to a different shape. `playbooks.embedding` was not
recomputed at all, so the lexical arm (R2) matches the remediated steps while the vector arm (R1)
still matches pre-remediation text. Re-running the backfill worker will not fix it: it resolves the
newest **published** version, and the 233 new versions are unpublished.

### G12 — MEDIUM (new): `score_concreteness.py` reports every step concrete. It always has.

`backend/scripts/score_concreteness.py:29` compiles all twelve patterns with `re.IGNORECASE`.
Pattern 10 is the CamelCase detector, `r"\b[A-Z][a-z0-9]+[A-Z][a-zA-Z0-9]*\b"`. Under `IGNORECASE`
that reduces to "a word of three or more letters", so it matches every ordinary English word.

Run against the pre-change corpus the script returns **2,006 of 2,006 steps concrete — 100%, all 440
playbooks in the 67–100% band.** A meaningless all-clear, and it would pass any "% concrete" publish
gate built on it.

Fixed: the case-dependent pattern is now compiled separately. But even corrected it scores the
pre-change corpus at 47.7% / bands 130-75-100-135, which does **not** reproduce the specificity audit's
published 59.7% / 61-78-132-169. So **four** concreteness definitions exist across this repo and its
audit documents, and no committed code regenerates the specificity audit's headline numbers. The fixed
script now says so in its own output.

---

## 3. Recommended order of work

1. **Run the repair and the verifier** (G2–G5, part of G11) — the three commands in the Runbook above.
   95 steps in 72 playbooks, then an independent re-check that should print *residual defects: 0*.
2. **Read the four value corrections** (G6) — three are right; the fourth (plugin 4.5 / ticket 219894)
   is claimed in the summary but absent from the code.
3. **Stop quoting the old concreteness bands** (G10, G12). Four scorers, none reproducing the
   specificity audit. Make `verify_playbook_corpus.py`'s series the one of record and retire the rest.
4. **Decide the publication path** (G1). Nothing above has any operational effect until playbooks are
   moved `candidate → under_review → approved` *and* their current versions are published. Recompute
   embeddings at that point (G11), and add `% concrete` as a publish gate as both audits recommend.
5. **Adjudicate the 109 KEEP-vs-CRITICAL-GAP conflicts** (G8) and suppress or rewrite the remaining
   48 agreed-worthless playbooks (G7).
6. **Restate `apply_summary.json`'s quality section** with the scorer named, or drop the band table
   (G10).

## 4. Reproducing this validation

```
pg_restore / read  data/playbook_remediation_backup_2026-08-26/aeprodsupport_playbook_tables.dump
                   → 440 playbooks, 630 versions, 2,006 steps in current versions
join               docs/playbook_corpus_remediation/remediation_decisions.jsonl on playbook_id
model after-state  new_step_texts, with the " Linked tickets specify: …" suffix stripped
                   → 420 playbooks, 1,847 steps  (matches apply_summary.json)
cross-reference    ALL_440_PLAYBOOKS_CORPUS_DEEP_AUDIT.md  (per-playbook ticket RC + outcome)
                   PLAYBOOK_SPECIFICITY_AUDIT.md           (pre-change concreteness bands)
```

Items marked ⓛ require reading the live `AEProdSupport` rows; everything else is derived from the
artefacts above.
