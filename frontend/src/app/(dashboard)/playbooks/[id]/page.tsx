"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import Link from "next/link";
import { useParams, useSearchParams } from "next/navigation";
import { useEffect, useRef, useState } from "react";
import { toast } from "sonner";
import { Loader2 } from "lucide-react";

import { ApplicabilityBadge } from "@/components/common/applicability";
import { PageHeader } from "@/components/common/page-header";
import {
  DetailPageSkeleton,
  DetailWideCardSkeleton,
} from "@/components/common/detail-page-skeleton";
import { Skeleton } from "@/components/ui/skeleton";
import { StatusBadge } from "@/components/common/status-badge";
import { Button } from "@/components/ui/button";
import { PlaybookSteps } from "@/components/common/playbook-steps";
import { PlaybookLifecycleActions } from "@/components/common/playbook-lifecycle-actions";
import { Card, CardAction, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Label } from "@/components/ui/label";
import { PlaybookEditor } from "@/components/playbooks/playbook-editor";
import { api } from "@/lib/api";
import { shouldOpenForkFromEditQuery } from "@/lib/playbook-steps";
import type {
  PoliciesOverview,
  Playbook,
  PlaybookVersion,
  PlaybookVersionDiff,
} from "@/lib/types";
import { useAuthStore } from "@/lib/stores/auth-store";
import { canEditAutomationMode, canEditPlaybook, canTransitionPlaybook } from "@/lib/roles";
import { GitCompare, RotateCcw, GitFork, Pencil, Sparkles, ListChecks, FileText, ChevronDown, ChevronUp, Search } from "lucide-react";

function formatTriggerLabel(key: string): string {
  return key
    .replace(/_/g, " ")
    .replace(/\b\w/g, (char) => char.toUpperCase());
}

function formatTriggerValue(value: unknown): string {
  if (value === null || value === undefined) return "";
  if (typeof value === "string" || typeof value === "number" || typeof value === "boolean") {
    return String(value);
  }
  if (Array.isArray(value)) {
    return value.map(formatTriggerValue).filter(Boolean).join(", ");
  }
  if (typeof value === "object") {
    return Object.entries(value as Record<string, unknown>)
      .map(([key, item]) => {
        const formatted = formatTriggerValue(item);
        return formatted ? `${formatTriggerLabel(key)}: ${formatted}` : "";
      })
      .filter(Boolean)
      .join("; ");
  }
  return String(value);
}

function triggerItems(value: unknown): string[] {
  if (Array.isArray(value)) {
    return value.map(formatTriggerValue).filter(Boolean);
  }
  const formatted = formatTriggerValue(value);
  return formatted ? [formatted] : [];
}

function TriggerConditionsSummary({
  triggerConditions,
}: {
  triggerConditions: Record<string, unknown>;
}) {
  const preferred = ["symptoms", "conditions", "entities"];
  const keys = [
    ...preferred.filter((key) => key in triggerConditions),
    ...Object.keys(triggerConditions).filter((key) => !preferred.includes(key)),
  ];
  const sections = keys
    .map((key) => ({ key, items: triggerItems(triggerConditions[key]) }))
    .filter((section) => section.items.length > 0);

  if (sections.length === 0) {
    return null;
  }

  return (
    <div className="rounded-lg border bg-muted/35 p-4">
      <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
        <h3 className="text-sm font-semibold text-foreground">Trigger conditions</h3>
        <span className="text-xs text-muted-foreground">Issue signals before execution</span>
      </div>
      <div className="grid gap-4 lg:grid-cols-[minmax(0,1.4fr)_minmax(220px,0.8fr)]">
        {sections.map((section) => (
          <div key={section.key} className="space-y-2">
            <p className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
              {formatTriggerLabel(section.key)}
            </p>
            <ul className="space-y-1.5">
              {section.items.map((item, index) => (
                <li key={`${section.key}-${index}`} className="flex gap-2 text-sm leading-6">
                  <span className="mt-2 h-1.5 w-1.5 shrink-0 rounded-full bg-primary" />
                  <span>{item}</span>
                </li>
              ))}
            </ul>
          </div>
        ))}
      </div>
    </div>
  );
}

function PlaybookLineageReferencesPanel({ playbookId }: { playbookId: string }) {
  const [showAllEpisodes, setShowAllEpisodes] = useState(false);
  const [showAllEvidence, setShowAllEvidence] = useState(false);
  const [searchQuery, setSearchQuery] = useState("");

  const { data, isLoading } = useQuery<{
    pattern: { id: string; title: string; confidence: number; episode_count: number } | null;
    episodes: Array<{ id: string; title: string; status: string; extraction_confidence: number }>;
    evidence_items: Array<{ id: string; title: string; evidence_type: string; source_type: string; display_id: string | null }>;
  }>({
    queryKey: ["playbook-references", playbookId],
    queryFn: () => api.get(`/playbooks/${playbookId}/references`),
  });

  if (isLoading) {
    return <Skeleton className="h-32 w-full rounded-lg" />;
  }

  if (!data || (!data.pattern && (!data.episodes || data.episodes.length === 0) && (!data.evidence_items || data.evidence_items.length === 0))) {
    return null;
  }

  const q = searchQuery.toLowerCase().trim();

  const filteredEpisodes = (data.episodes || []).filter(
    (ep) => !q || ep.title.toLowerCase().includes(q)
  );

  const filteredEvidence = (data.evidence_items || []).filter(
    (ev) =>
      !q ||
      ev.title.toLowerCase().includes(q) ||
      (ev.display_id && ev.display_id.toLowerCase().includes(q))
  );

  const visibleEpisodes = showAllEpisodes ? filteredEpisodes : filteredEpisodes.slice(0, 6);
  const visibleEvidence = showAllEvidence ? filteredEvidence : filteredEvidence.slice(0, 6);

  const totalItems = (data.episodes?.length ?? 0) + (data.evidence_items?.length ?? 0);

  return (
    <Card>
      <CardHeader className="pb-3">
        <CardTitle className="text-base font-semibold flex flex-wrap items-center justify-between gap-2">
          <span className="flex items-center gap-2">
            <GitFork className="h-4 w-4 text-primary" />
            Playbook Grounding & Lineage Trace
          </span>
          <div className="flex items-center gap-3">
            {totalItems > 6 && (
              <div className="relative">
                <Search className="absolute left-2.5 top-2.5 h-3.5 w-3.5 text-muted-foreground" />
                <input
                  type="text"
                  placeholder="Filter references…"
                  value={searchQuery}
                  onChange={(e) => setSearchQuery(e.target.value)}
                  className="h-8 w-44 rounded-md border border-input bg-background pl-8 pr-3 text-xs focus:border-primary focus:outline-none"
                />
              </div>
            )}
            <span className="text-xs text-muted-foreground font-normal hidden sm:inline">
              Click any item to view source record
            </span>
          </div>
        </CardTitle>
      </CardHeader>
      <CardContent className="space-y-4 text-sm">
        {/* 1. Source Pattern Reference */}
        {data.pattern ? (
          <div>
            <span className="text-[11px] font-semibold text-muted-foreground uppercase tracking-wider block mb-1.5">
              Source Pattern Reference
            </span>
            <Link
              href={`/patterns/${data.pattern.id}`}
              className="inline-flex items-center gap-2 rounded-md border bg-background px-3 py-1.5 font-medium text-primary transition-colors hover:bg-accent hover:text-accent-foreground hover:underline"
            >
              <Sparkles className="h-4 w-4 text-primary" />
              <span>{data.pattern.title}</span>
              <span className="rounded bg-muted px-1.5 py-0.5 text-[11px] font-normal text-muted-foreground">
                {(data.pattern.confidence * 100).toFixed(0)}% confidence
              </span>
            </Link>
          </div>
        ) : null}

        {/* 2. Contributing Episodes Reference List */}
        {data.episodes && data.episodes.length > 0 ? (
          <div>
            <div className="flex items-center justify-between mb-1.5">
              <span className="text-[11px] font-semibold text-muted-foreground uppercase tracking-wider">
                Contributing Episodes ({filteredEpisodes.length} of {data.episodes.length})
              </span>
              {filteredEpisodes.length > 6 && (
                <button
                  type="button"
                  onClick={() => setShowAllEpisodes(!showAllEpisodes)}
                  className="flex items-center gap-1 text-xs font-medium text-primary hover:underline"
                >
                  {showAllEpisodes ? (
                    <>Show Less <ChevronUp className="h-3 w-3" /></>
                  ) : (
                    <>+{filteredEpisodes.length - 6} more episodes <ChevronDown className="h-3 w-3" /></>
                  )}
                </button>
              )}
            </div>
            <div className={`flex flex-wrap gap-2 ${showAllEpisodes ? "max-h-64 overflow-y-auto pr-1" : ""}`}>
              {visibleEpisodes.map((ep) => (
                <Link
                  key={ep.id}
                  href={`/episodes/${ep.id}`}
                  className="inline-flex items-center gap-1.5 rounded-md border bg-background px-2.5 py-1 text-xs hover:border-primary hover:text-primary transition-colors"
                >
                  <ListChecks className="h-3.5 w-3.5 text-muted-foreground" />
                  <span className="font-medium">{ep.title}</span>
                  <StatusBadge status={ep.status} />
                </Link>
              ))}
            </div>
          </div>
        ) : null}

        {/* 3. Source Evidence Items Reference List */}
        {data.evidence_items && data.evidence_items.length > 0 ? (
          <div>
            <div className="flex items-center justify-between mb-1.5">
              <span className="text-[11px] font-semibold text-muted-foreground uppercase tracking-wider">
                Grounded Evidence Items ({filteredEvidence.length} of {data.evidence_items.length})
              </span>
              {filteredEvidence.length > 6 && (
                <button
                  type="button"
                  onClick={() => setShowAllEvidence(!showAllEvidence)}
                  className="flex items-center gap-1 text-xs font-medium text-primary hover:underline"
                >
                  {showAllEvidence ? (
                    <>Show Less <ChevronUp className="h-3 w-3" /></>
                  ) : (
                    <>+{filteredEvidence.length - 6} more evidence <ChevronDown className="h-3 w-3" /></>
                  )}
                </button>
              )}
            </div>
            <div className={`flex flex-wrap gap-2 ${showAllEvidence ? "max-h-64 overflow-y-auto pr-1" : ""}`}>
              {visibleEvidence.map((ev) => (
                <Link
                  key={ev.id}
                  href={`/evidence/${ev.id}`}
                  className="inline-flex items-center gap-1.5 rounded-md border bg-background px-2.5 py-1 text-xs hover:border-primary hover:text-primary transition-colors"
                >
                  <FileText className="h-3.5 w-3.5 text-muted-foreground" />
                  {ev.display_id ? (
                    <span className="font-mono text-primary font-semibold">#{ev.display_id}</span>
                  ) : null}
                  <span className="truncate max-w-[15rem]">{ev.title}</span>
                </Link>
              ))}
            </div>
          </div>
        ) : null}
      </CardContent>
    </Card>
  );
}

// Allowed transitions come from the API (`playbook.allowed_transitions`).
//
// This was a hand-maintained copy of the backend's VALID_TRANSITIONS,
// under a comment saying it mirrored it, and it had drifted both ways:
// it offered candidate -> retired and under_review -> retired, which the
// backend rejects, and it omitted approved -> restricted, so the one
// control for narrowing a live playbook was unreachable from the UI.
//
// Two copies of a rule is one copy too many when only one of them is
// enforced.


function DiffDialog({
  playbookId,
  versionId,
  onClose,
}: {
  playbookId: string;
  versionId: string;
  onClose: () => void;
}) {
  const { data, isLoading } = useQuery<PlaybookVersionDiff>({
    queryKey: ["playbook-diff", playbookId, versionId],
    queryFn: () => api.get(`/playbooks/${playbookId}/versions/${versionId}/diff`),
  });

  return (
    <DialogContent className="max-w-2xl">
      <DialogHeader>
        <DialogTitle>Version diff</DialogTitle>
      </DialogHeader>
      {isLoading ? (
        <div className="flex items-center justify-center py-8">
          <Loader2 className="h-5 w-5 animate-spin text-muted-foreground" />
        </div>
      ) : data ? (
        <div className="space-y-3 text-sm">
          <p className="text-xs text-muted-foreground">
            {data.base_semantic_version
              ? `v${data.base_semantic_version} → v${data.target_semantic_version}`
              : `Initial version v${data.target_semantic_version}`}
          </p>
          {data.changed_fields.length > 0 && (
            <div className="flex flex-wrap gap-1">
              {data.changed_fields.map((f) => (
                <span key={f} className="rounded bg-muted px-2 py-0.5 text-xs">{f}</span>
              ))}
            </div>
          )}
          <pre className="max-h-80 overflow-auto rounded-md bg-muted p-3 text-xs whitespace-pre-wrap font-mono">
            {data.unified_diff || "No textual diff available."}
          </pre>
        </div>
      ) : null}
      <DialogFooter>
        <Button variant="outline" onClick={onClose}>Close</Button>
      </DialogFooter>
    </DialogContent>
  );
}

/**
 * What each automation mode actually permits, in the reviewer's terms
 * rather than the enum's. Mirrors `models/playbook.AUTOMATION_MODES`.
 *
 * These are ordered by autonomy, and the order is the point: the list
 * reads as a ladder so it is obvious that picking a lower rung is always
 * the safer choice.
 */
const AUTOMATION_MODES: { value: string; label: string; detail: string }[] = [
  {
    value: "suggest_only",
    label: "Suggest only",
    detail:
      "Nothing executes. Every caller is capped at read-only regardless of their role.",
  },
  {
    value: "shadow",
    label: "Shadow",
    detail:
      "Dry run. Steps are traced and approval requests are recorded for audit, but no tool actually runs.",
  },
  {
    value: "human_confirmed",
    label: "Human confirmed",
    detail: "One step at a time, each with an explicit human approval.",
  },
  {
    value: "supervised",
    label: "Supervised",
    detail:
      "Runs with a human watching. Anything above low side effect still needs per-step approval.",
  },
  {
    value: "full_auto",
    label: "Full auto",
    detail:
      "Runs without approval for admin roles, up to and including destructive steps.",
  },
];

const NO_POLICY = "__none__";

/** What an approval policy's config actually enforces, in plain terms. */
function describePolicy(config: Record<string, unknown>): string[] {
  const out: string[] = [];
  const roles = config.approver_roles;
  if (Array.isArray(roles) && roles.length > 0) {
    out.push(`Only ${roles.join(", ")} may decide approvals`);
  }
  if (config.forbid_self_approval) {
    out.push("The person who starts a run may not approve their own steps");
  }
  if (typeof config.require_approval_min_safety_class === "string") {
    out.push(
      `Anything at or above ${config.require_approval_min_safety_class} always needs approval`,
    );
  }
  if (typeof config.max_automation_mode === "string") {
    out.push(`Automation capped at ${config.max_automation_mode}`);
  }
  return out;
}

/**
 * Governance: automation mode and the approval policy bound to it.
 *
 * One panel rather than two because they constrain each other — a policy
 * can cap automation mode, so choosing them apart invites saving a
 * combination the API will reject.
 *
 * Both were unreachable from the UI. Automation mode was rendered in
 * four places and editable in none, so every generated playbook stayed
 * at `suggest_only` — which caps every caller at read_only — and the
 * per-step approval machinery below it could never engage. Approval
 * policies could be authored on the policies page but never bound to
 * anything, so `forbid_self_approval` and `approver_roles` were written
 * and never applied.
 *
 * Restricted to tenant_admin. Attaching a policy only ever adds
 * constraints, but the same control detaches one, and clearing it drops
 * the two-person rule and the autonomy ceiling in a single action.
 */
function GovernancePanel({ playbook }: { playbook: Playbook }) {
  const qc = useQueryClient();
  const roles = useAuthStore((s) => s.roles);
  const editable = canEditAutomationMode(roles);

  const [pendingMode, setPendingMode] = useState<string | null>(null);
  const [pendingPolicy, setPendingPolicy] = useState<string | null>(null);

  const { data: policies } = useQuery<PoliciesOverview>({
    queryKey: ["policies"],
    queryFn: () => api.get<PoliciesOverview>("/policies"),
    enabled: editable,
  });
  // Inactive policies fail closed at execution and are rejected at bind
  // time, so offering them would only produce an error later.
  const approvalPolicies = (policies?.approval_policies ?? []).filter(
    (p) => p.is_active,
  );

  const currentMode =
    AUTOMATION_MODES.find((m) => m.value === playbook.automation_mode) ?? null;
  const boundPolicy =
    approvalPolicies.find((p) => p.id === playbook.approval_policy_id) ?? null;

  const mut = useMutation({
    mutationFn: (body: Record<string, unknown>) =>
      api.patch(`/playbooks/${playbook.id}`, body),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["playbook", playbook.id] });
      qc.invalidateQueries({ queryKey: ["playbooks"] });
      toast.success("Governance updated");
      setPendingMode(null);
      setPendingPolicy(null);
    },
    onError: (err: Error) => {
      // The API rejects a mode above the bound policy's ceiling. Surface
      // its own reason — "policy caps this at supervised" is actionable,
      // "update failed" is not.
      toast.error(err.message || "Could not update governance");
    },
  });

  const modeChanged = pendingMode !== null && pendingMode !== playbook.automation_mode;
  const policySelection = pendingPolicy ?? playbook.approval_policy_id ?? NO_POLICY;
  const policyChanged =
    pendingPolicy !== null &&
    pendingPolicy !== (playbook.approval_policy_id ?? NO_POLICY);

  const apply = () => {
    const body: Record<string, unknown> = {};
    if (modeChanged) body.automation_mode = pendingMode;
    if (policyChanged) {
      body.approval_policy_id = pendingPolicy === NO_POLICY ? null : pendingPolicy;
    }
    if (Object.keys(body).length > 0) mut.mutate(body);
  };

  const previewPolicy =
    approvalPolicies.find((p) => p.id === policySelection) ?? boundPolicy;

  return (
    <div className="rounded-lg border p-4 space-y-4">
      <div>
        <h3 className="text-sm font-semibold">Governance</h3>
        <p className="mt-1 text-xs text-muted-foreground">
          {editable
            ? "Whether this playbook may act on a real system, and who must approve when it does."
            : "Whether this playbook may act on a real system, and who must approve when it does. Only a tenant administrator can change these."}
        </p>
      </div>

      <div className="grid gap-4 sm:grid-cols-2">
        <div className="space-y-2">
          <div className="flex flex-wrap items-center gap-2">
            <span className="text-xs text-muted-foreground">Automation</span>
            <StatusBadge status={playbook.automation_mode} />
          </div>
          {currentMode && (
            <p className="text-xs text-muted-foreground">{currentMode.detail}</p>
          )}
          {editable && (
            <div>
              <Label htmlFor="automation-mode" className="text-xs">
                Change to
              </Label>
              <Select
                value={pendingMode ?? playbook.automation_mode}
                onValueChange={(v) => setPendingMode(v ?? null)}
              >
                <SelectTrigger id="automation-mode" className="mt-1">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {AUTOMATION_MODES.map((mode) => (
                    <SelectItem key={mode.value} value={mode.value}>
                      {mode.label}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
          )}
        </div>

        <div className="space-y-2">
          <div className="flex flex-wrap items-center gap-2">
            <span className="text-xs text-muted-foreground">Approval policy</span>
            {playbook.approval_policy_id ? (
              <span className="rounded border px-1.5 py-0.5 text-[11px]">
                {boundPolicy?.name ?? "attached"}
              </span>
            ) : (
              <span className="text-xs">
                None — role and automation mode alone decide gating
              </span>
            )}
          </div>
          {editable && (
            <div>
              <Label htmlFor="approval-policy" className="text-xs">
                Bind policy
              </Label>
              <Select
                value={policySelection}
                onValueChange={(v) => setPendingPolicy(v ?? NO_POLICY)}
              >
                <SelectTrigger id="approval-policy" className="mt-1">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value={NO_POLICY}>None</SelectItem>
                  {approvalPolicies.map((policy) => (
                    <SelectItem key={policy.id} value={policy.id}>
                      {policy.name}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
          )}
          {/* What binding it actually does. A policy name alone tells a
              reviewer nothing about the rules they are switching on. */}
          {previewPolicy && describePolicy(previewPolicy.config).length > 0 && (
            <ul className="space-y-0.5">
              {describePolicy(previewPolicy.config).map((rule) => (
                <li key={rule} className="text-xs text-muted-foreground">
                  • {rule}
                </li>
              ))}
            </ul>
          )}
          {editable && approvalPolicies.length === 0 && (
            <p className="text-xs text-muted-foreground">
              No active approval policies exist yet — create one on the
              Policies page.
            </p>
          )}
        </div>
      </div>

      {editable && (
        <div className="flex items-center gap-2">
          <Button
            disabled={mut.isPending || (!modeChanged && !policyChanged)}
            onClick={apply}
          >
            {mut.isPending && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
            Apply
          </Button>
          {modeChanged && (
            <span className="text-xs text-muted-foreground">
              {AUTOMATION_MODES.find((m) => m.value === pendingMode)?.detail}
            </span>
          )}
        </div>
      )}
    </div>
  );
}

/**
 * The approved knowledge this version was generated from, each with the
 * applicability verdict as it stood at generation time.
 *
 * A reviewer asking "which SOP does this implement" needs more than a
 * list of titles: an article flagged as written for a release this
 * estate does not run still informed the playbook, and that caveat was
 * computed and shown to the model. Dropping it here would leave the
 * reviewer approving steps grounded in a document nobody told them was
 * out of scope.
 */
function KnowledgeSourcesPanel({ version }: { version: PlaybookVersion }) {
  const refs = version.evidence_refs ?? null;
  const knowledge = refs?.knowledge;

  // Versions generated before applicability was recorded carry
  // knowledge_ids but no verdicts. Say so rather than rendering a
  // verdict-less list that looks like everything checked out.
  if (!knowledge || knowledge.length === 0) {
    const count = refs?.knowledge_ids?.length ?? 0;
    if (count === 0) {
      return (
        <div className="rounded-lg border border-dashed p-4">
          <h3 className="text-sm font-semibold">Approved knowledge used</h3>
          <p className="mt-1 text-xs text-muted-foreground">
            None. This playbook reflects observed practice only — no KB
            article or SOP was matched to the pattern.
          </p>
        </div>
      );
    }
    return (
      <div className="rounded-lg border p-4">
        <h3 className="text-sm font-semibold">Approved knowledge used</h3>
        <p className="mt-1 text-xs text-muted-foreground">
          {count} document{count === 1 ? "" : "s"}. Applicability was not
          recorded for this version, so whether they match this
          environment is unknown here.
        </p>
      </div>
    );
  }

  const flagged = knowledge.filter(
    (doc) => doc.applicability_verdict === "mismatch",
  ).length;

  return (
    <div className="rounded-lg border p-4 space-y-3">
      <div>
        <h3 className="text-sm font-semibold">
          Approved knowledge used
          {flagged > 0 && (
            <span className="ml-2 rounded-full bg-amber-500/20 px-2 py-0.5 text-[10px] font-medium">
              {flagged} flagged
            </span>
          )}
        </h3>
        <p className="mt-1 text-xs text-muted-foreground">
          What the organisation says should be done, as matched to this
          pattern. A flagged document still informed the playbook — it was
          ranked lower, not withheld.
        </p>
      </div>

      <div className="space-y-2">
        {knowledge.map((doc) => (
          <div
            key={doc.evidence_id}
            className="rounded-md border bg-background p-3 space-y-1"
          >
            <div className="flex items-start justify-between gap-2">
              <Link
                href={`/evidence/${doc.evidence_id}`}
                className="text-sm font-medium hover:underline"
              >
                {doc.title || doc.evidence_id}
              </Link>
              <ApplicabilityBadge verdict={doc.applicability_verdict} />
            </div>
            {doc.evidence_type && (
              <p className="text-[11px] text-muted-foreground">
                {doc.evidence_type}
              </p>
            )}
            {doc.applicability_notes && doc.applicability_notes.length > 0 && (
              <ul className="mt-1 space-y-0.5">
                {doc.applicability_notes.map((note, index) => (
                  <li key={index} className="text-xs text-muted-foreground">
                    • {note}
                  </li>
                ))}
              </ul>
            )}
          </div>
        ))}
      </div>
    </div>
  );
}

function ConflictsPanel({ version }: { version: PlaybookVersion }) {
  const conflicts = version.conflicts;

  // null and [] mean different things and must read differently.
  // null: this version predates knowledge being an input, so the
  // comparison never ran. Rendering "no conflicts" there would claim a
  // check was performed and passed.
  if (conflicts === null || conflicts === undefined) {
    return (
      <div className="rounded-lg border border-dashed p-4">
        <h3 className="text-sm font-semibold">Documented vs. observed</h3>
        <p className="mt-1 text-xs text-muted-foreground">
          Not assessed — this version was generated before approved
          knowledge was compared against observed practice.
        </p>
      </div>
    );
  }

  if (conflicts.length === 0) {
    return (
      <div className="rounded-lg border p-4">
        <h3 className="text-sm font-semibold">Documented vs. observed</h3>
        <p className="mt-1 text-xs text-muted-foreground">
          No disagreement found between the approved procedure and what
          engineers actually did.
        </p>
      </div>
    );
  }

  return (
    <div className="rounded-lg border border-amber-500/40 bg-amber-500/5 p-4 space-y-3">
      <div>
        <h3 className="text-sm font-semibold">
          Documented vs. observed
          <span className="ml-2 rounded-full bg-amber-500/20 px-2 py-0.5 text-[10px] font-medium">
            {conflicts.length} to review
          </span>
        </h3>
        <p className="mt-1 text-xs text-muted-foreground">
          The generator did not choose between these. Preferring the SOP
          ignores runs that succeeded doing something else; preferring
          practice deletes a safeguard.
        </p>
      </div>

      <div className="space-y-3">
        {conflicts.map((conflict, index) => (
          <div key={index} className="rounded-md border bg-background p-3 space-y-2">
            {conflict.topic && (
              <p className="text-sm font-medium">{conflict.topic}</p>
            )}
            <div className="grid gap-2 sm:grid-cols-2 text-xs">
              <div>
                <span className="text-muted-foreground">Documented procedure</span>
                <p className="mt-0.5 whitespace-pre-wrap">
                  {conflict.documented || "—"}
                </p>
              </div>
              <div>
                <span className="text-muted-foreground">Observed practice</span>
                <p className="mt-0.5 whitespace-pre-wrap">
                  {conflict.observed || "—"}
                </p>
              </div>
            </div>
            {conflict.recommendation && (
              <p className="text-xs">
                <span className="text-muted-foreground">Recommended check: </span>
                {conflict.recommendation}
              </p>
            )}
            {conflict.source_refs && conflict.source_refs.length > 0 && (
              <div className="flex flex-wrap gap-1 pt-1">
                {conflict.source_refs.map((ref) => (
                  <span
                    key={ref.id}
                    title={ref.title || ref.id}
                    className="rounded border px-1.5 py-0.5 text-[10px] font-mono"
                  >
                    {ref.kind === "knowledge" ? "KB" : "EP"} {ref.title || ref.label}
                  </span>
                ))}
              </div>
            )}
          </div>
        ))}
      </div>
    </div>
  );
}

function versionOptionLabel(version: PlaybookVersion, playbook: Playbook): string {
  const bits = [`v${version.semantic_version}`];
  if (version.id === playbook.current_version_id) bits.push("main");
  bits.push(version.published_at ? "published" : "draft");
  return bits.join(" · ");
}

export default function PlaybookDetailPage() {
  const params = useParams<{ id: string }>();
  const searchParams = useSearchParams();
  const playbookId = params.id;
  const roles = useAuthStore((s) => s.roles);
  const canEdit = canEditPlaybook(roles);
  const [diffVersion, setDiffVersion] = useState<string | null>(null);
  const [selectedVersionId, setSelectedVersionId] = useState<string | null>(null);
  const [mode, setMode] = useState<"view" | "edit">(
    searchParams.get("edit") === "1" ? "edit" : "view",
  );
  const [forkOpen, setForkOpen] = useState(false);
  const autoForkRef = useRef(false);
  const wantsEdit = searchParams.get("edit") === "1";
  const qc = useQueryClient();

  const { data: playbook, isLoading, error } = useQuery({
    queryKey: ["playbook", playbookId],
    queryFn: () => api.get<Playbook>(`/playbooks/${playbookId}`),
    enabled: !!playbookId,
  });

  const { data: versions = [] } = useQuery({
    queryKey: ["playbook-versions", playbookId],
    queryFn: () => api.get<PlaybookVersion[]>(`/playbooks/${playbookId}/versions`),
    enabled: !!playbookId,
  });

  const rollbackMut = useMutation({
    mutationFn: (versionId: string) =>
      api.post(`/playbooks/${playbookId}/rollback`, { target_version_id: versionId }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["playbook-versions", playbookId] });
      qc.invalidateQueries({ queryKey: ["playbook", playbookId] });
      toast.success("Playbook rolled back successfully");
    },
    onError: (err: Error) => toast.error(err.message || "Rollback failed"),
  });

  const forkMut = useMutation({
    mutationFn: (versionId: string) =>
      api.post<PlaybookVersion>(`/playbooks/${playbookId}/versions/${versionId}/draft`, {}),
    onSuccess: (draft) => {
      qc.invalidateQueries({ queryKey: ["playbook-versions", playbookId] });
      qc.invalidateQueries({ queryKey: ["playbook", playbookId] });
      setSelectedVersionId(draft.id);
      setMode("edit");
      setForkOpen(false);
      toast.success(`Opened draft v${draft.semantic_version} as the main version`);
    },
    onError: (err: Error) => toast.error(err.message || "Could not create draft"),
  });

  useEffect(() => {
    if (autoForkRef.current || !playbook || versions.length === 0) return;
    const main =
      versions.find((v) => v.id === playbook.current_version_id) ?? versions[0];
    const locked = ["retired", "deprecated"].includes(playbook.lifecycle_state);
    if (
      !shouldOpenForkFromEditQuery({
        wantsEdit,
        canEdit,
        lifecycleLocked: locked,
        currentVersionIsPublished: Boolean(main?.published_at),
      })
    ) {
      return;
    }
    autoForkRef.current = true;
    setForkOpen(true);
  }, [playbook, versions, canEdit, wantsEdit]);

  if (!playbookId) return null;

  if (isLoading) {
    return (
      <DetailPageSkeleton>
        <Skeleton className="h-4 w-full max-w-xl" />
        <div className="h-px w-full bg-border" />
        <div className="space-y-4">
          <Skeleton className="h-6 w-36" />
          <DetailWideCardSkeleton lines={6} />
          <DetailWideCardSkeleton lines={6} />
        </div>
      </DetailPageSkeleton>
    );
  }

  if (error || !playbook) {
    return (
      <div className="space-y-4">
        <PageHeader
          title="Playbook"
          description="Not found."
          backHref="/playbooks"
          backLabel="Playbooks"
        />
        <p className="text-sm text-destructive">{String((error as Error)?.message || "Missing")}</p>
      </div>
    );
  }

  const mainVersion =
    versions.find((v) => v.id === playbook.current_version_id) ?? versions[0];
  const selectedVersion = versions.find((v) => v.id === selectedVersionId) ?? mainVersion;
  const selectedIsMain = Boolean(
    selectedVersion && selectedVersion.id === playbook.current_version_id,
  );
  const selectedIsDraft = Boolean(selectedVersion && !selectedVersion.published_at);
  const lifecycleLocked = ["retired", "deprecated"].includes(playbook.lifecycle_state);
  const editing = mode === "edit" && canEdit && selectedIsMain && selectedIsDraft && !lifecycleLocked;
  const hasTransitions = (playbook.allowed_transitions ?? []).length > 0;

  const editTitle = !canEdit
    ? "Knowledge managers can edit playbooks"
    : lifecycleLocked
      ? `Playbooks in ${playbook.lifecycle_state} cannot be edited`
      : !selectedIsMain
        ? "Switch to the main version to edit"
        : selectedVersion?.published_at
          ? "Edit as a new draft. Runtime keeps serving the published version until the draft is approved."
          : "Edit this draft";

  return (
    <div className="space-y-6">
      <PageHeader
        title={playbook.title}
        description={`Stable key ${playbook.stable_key} · ${playbook.automation_mode}`}
        backHref="/playbooks"
        backLabel="Playbooks"
        actions={
          <div className="flex items-center gap-2">
            {selectedVersion?.playbook_confidence !== undefined || playbook.confidence !== undefined ? (
              <div className="flex items-center gap-1.5 rounded-md bg-emerald-500/10 border border-emerald-500/20 px-3 py-1.5 text-xs font-semibold text-emerald-600 dark:text-emerald-400">
                <Sparkles className="h-3.5 w-3.5" />
                Score: {(((selectedVersion?.playbook_confidence ?? playbook.confidence ?? 0.8)) * 100).toFixed(0)}%
              </div>
            ) : null}
            {hasTransitions && !editing && (
              <PlaybookLifecycleActions playbook={playbook} showPermissionHint />
            )}
          </div>
        }
      />

      <Dialog open={!!diffVersion} onOpenChange={(o) => { if (!o) setDiffVersion(null); }}>
        {diffVersion && playbookId && (
          <DiffDialog
            playbookId={playbookId}
            versionId={diffVersion}
            onClose={() => setDiffVersion(null)}
          />
        )}
      </Dialog>

      <Dialog open={forkOpen} onOpenChange={setForkOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Edit as a new draft</DialogTitle>
          </DialogHeader>
          <p className="text-sm text-muted-foreground">
            The published version stays the live procedure. Runtime, search embeddings, and
            Support Copilot keep using it until this new draft is approved. The new draft
            becomes the main version for editing.
          </p>
          <DialogFooter>
            <Button variant="outline" onClick={() => setForkOpen(false)}>
              Cancel
            </Button>
            <Button
              disabled={!selectedVersion || forkMut.isPending}
              onClick={() => selectedVersion && forkMut.mutate(selectedVersion.id)}
            >
              {forkMut.isPending && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
              Create draft
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <div className="flex flex-wrap gap-2">
        <StatusBadge status={playbook.lifecycle_state} />
        <span className="rounded-md border px-2 py-0.5 text-xs capitalize">{playbook.risk_tier} risk</span>
      </div>

      {selectedVersion ? (
        <Card>
          <CardHeader className="pb-2">
            <CardTitle className="text-base">
              Procedure steps
            </CardTitle>
            <CardAction className="flex flex-wrap items-center justify-end gap-2">
              <Select
                value={selectedVersion.id}
                onValueChange={(value) => {
                  if (value) setSelectedVersionId(value);
                  if (editing) setMode("view");
                }}
                disabled={editing}
              >
                <SelectTrigger className="h-8 w-[220px]">
                  <span className="truncate">
                    {versionOptionLabel(selectedVersion, playbook)}
                  </span>
                </SelectTrigger>
                <SelectContent>
                  {versions.map((version) => (
                    <SelectItem key={version.id} value={version.id}>
                      {versionOptionLabel(version, playbook)}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
              <Button
                variant="outline"
                size="sm"
                className="h-8"
                disabled={editing}
                onClick={() => setDiffVersion(selectedVersion.id)}
              >
                <GitCompare className="mr-1 h-3.5 w-3.5" />
                Diff
              </Button>
              {selectedVersion.id !== playbook.current_version_id && canTransitionPlaybook(roles) && (
                <Button
                  variant="outline"
                  size="sm"
                  className="h-8"
                  disabled={rollbackMut.isPending || editing}
                  onClick={() => rollbackMut.mutate(selectedVersion.id)}
                >
                  <RotateCcw className="mr-1 h-3.5 w-3.5" />
                  Rollback
                </Button>
              )}
              {canEdit && !editing && (
                <Button
                  size="sm"
                  className="h-8"
                  disabled={lifecycleLocked || !selectedIsMain}
                  title={editTitle}
                  onClick={() => {
                    if (selectedVersion.published_at) {
                      setForkOpen(true);
                      return;
                    }
                    setMode("edit");
                  }}
                >
                  <Pencil className="mr-1 h-3.5 w-3.5" />
                  {selectedVersion.published_at ? "Edit as new draft" : "Edit"}
                </Button>
              )}
              <span className="basis-full text-right text-xs font-normal text-muted-foreground">
                {versionOptionLabel(selectedVersion, playbook)} — {Array.isArray(selectedVersion.steps) ? selectedVersion.steps.length : 0} steps
              </span>
            </CardAction>
          </CardHeader>
          <CardContent className="space-y-4">
            {editing ? (
              <PlaybookEditor
                playbook={playbook}
                version={selectedVersion}
                onCancel={() => setMode("view")}
                onSaved={(next) => {
                  setSelectedVersionId(next.id);
                  setMode("view");
                }}
              />
            ) : (
              <>
                <TriggerConditionsSummary triggerConditions={selectedVersion.trigger_conditions} />
                <PlaybookSteps steps={selectedVersion.steps} />
                <div className="border-t pt-4 text-xs text-muted-foreground">
                  <div className="space-y-2">
                    <p>
                      <span className="font-medium text-foreground">Confidence</span>{" "}
                      {(selectedVersion.playbook_confidence * 100).toFixed(0)}%
                    </p>
                    {selectedVersion.execution_confidence_guidance && (
                      <p>{selectedVersion.execution_confidence_guidance}</p>
                    )}
                    {selectedVersion.last_edit_note ? (
                      <p>
                        <span className="font-medium text-foreground">Last edit note</span>{" "}
                        {selectedVersion.last_edit_note}
                      </p>
                    ) : null}
                    <p>{new Date(selectedVersion.created_at).toLocaleString()}</p>
                  </div>
                </div>
              </>
            )}
          </CardContent>
        </Card>
      ) : (
        <Card>
          <CardContent className="text-sm text-muted-foreground">
            No published versions yet.
          </CardContent>
        </Card>
      )}

      {!editing && playbook.description && (
        <p className="text-sm text-muted-foreground whitespace-pre-wrap">{playbook.description}</p>
      )}

      <div className="grid gap-4 md:grid-cols-2 text-sm">
        <div>
          <span className="text-muted-foreground">Domain</span>{" "}
          <span className="font-mono text-xs">{playbook.domain_id ?? "—"}</span>
        </div>
        <div>
          <span className="text-muted-foreground">Last validated</span>{" "}
          {playbook.last_validated_at
            ? new Date(playbook.last_validated_at).toLocaleString()
            : "Never"}
        </div>
        {playbook.expiry_at && (
          <div>
            <span className="text-muted-foreground">Expires</span>{" "}
            {new Date(playbook.expiry_at).toLocaleString()}
          </div>
        )}
      </div>

      <PlaybookLineageReferencesPanel playbookId={playbook.id} />
      <GovernancePanel playbook={playbook} />
      {selectedVersion && <KnowledgeSourcesPanel version={selectedVersion} />}
      {selectedVersion && <ConflictsPanel version={selectedVersion} />}
    </div>
  );
}
