"use client";

/**
 * What the quality system found, for the reviewer who is about to decide.
 *
 * The panel's job is to be honest about three things that are easy to get
 * wrong, and each one is a deliberate design choice rather than a styling
 * preference:
 *
 * 1. **Coverage before verdict.** Most validators are not built yet, so the
 *    overall state is largely a statement about our own coverage rather than
 *    about the playbook. The header leads with "3 of 14 checks run" and only
 *    shows a verdict badge once the checks that produced it are worth
 *    something. Painting `inconclusive` as a warning on all 420 playbooks
 *    would teach reviewers to ignore the badge before it ever means anything.
 *
 * 2. **Structure above, not beside.** An empty procedure or a branch pointing
 *    at a step that does not exist makes the subject and coherence verdicts
 *    moot rather than merely accompanying them. It renders as a banner, never
 *    as a fourth peer group, so nobody reads "subject: pass" off an artifact
 *    with no procedure in it.
 *
 * 3. **Stale content is louder than a bad verdict.** When the playbook has
 *    been edited since it was assessed, the findings below describe text that
 *    is no longer on screen. A healthy-looking assessment about content
 *    nobody can see is worse than no assessment, so that case gets the top
 *    banner and the findings are visually demoted.
 */

import { useQuery } from "@tanstack/react-query";
import { AlertTriangle, FileWarning, History, ShieldQuestion } from "lucide-react";

import { StatusBadge } from "@/components/common/status-badge";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { api } from "@/lib/api";
import type {
  PlaybookQuality,
  PlaybookQualityFinding,
  PlaybookQualitySummary,
  QualitySeverity,
  QualityState,
} from "@/lib/types";

const SEVERITY_ORDER: QualitySeverity[] = ["critical", "major", "minor", "info"];

const GROUP_LABELS: Record<string, string> = {
  subject: "Subject & title",
  steps: "Steps",
  coherence: "Coherence",
};

const GROUP_HELP: Record<string, string> = {
  subject: "Does the title describe one evidenced operational subject?",
  steps: "Are the instructions accurate, complete, ordered, safe and verifiable?",
  coherence: "Do the title, symptoms, cause, steps and resolution describe one issue?",
};

/** Human wording for the reasons the backend stores as slugs. */
const STALE_REASONS: Record<string, string> = {
  content_changed: "the content changed",
  shell_edited: "the title or description was edited",
  steps_edited: "the steps were edited",
  source_changed: "a cited source changed",
  policy_changed: "the policy pack changed",
  ontology_changed: "the product ontology changed",
  validator_retired: "the validator that produced it was retired",
  forked_from_other_revision: "it was forked from another revision",
  rolled_back: "the playbook was rolled back",
};

function severityCount(
  counts: Partial<Record<QualitySeverity, number>>,
  severity: QualitySeverity,
): number {
  return counts[severity] ?? 0;
}

function GroupTile({ name, state }: { name: string; state: QualityState | null }) {
  return (
    <div className="rounded-md border bg-background p-3 space-y-1">
      <div className="flex items-center justify-between gap-2">
        <span className="text-xs font-medium">{GROUP_LABELS[name] ?? name}</span>
        {state ? (
          <StatusBadge status={state} />
        ) : (
          // Not evaluated is not the same as clean, and must not look like it.
          <span className="text-[10px] text-muted-foreground">not checked</span>
        )}
      </div>
      <p className="text-[11px] text-muted-foreground">{GROUP_HELP[name] ?? ""}</p>
    </div>
  );
}

function FindingRow({ finding }: { finding: PlaybookQualityFinding }) {
  const target =
    finding.target_kind === "step"
      ? `step ${finding.target_ref}`
      : finding.target_kind === "field"
        ? finding.target_ref
        : null;

  return (
    <div className="rounded-md border bg-background p-3 space-y-1">
      <div className="flex flex-wrap items-center gap-2">
        <StatusBadge status={finding.severity} />
        <span className="text-xs font-medium">{finding.category.replace(/_/g, " ")}</span>
        {target && (
          <span className="rounded bg-muted px-1.5 py-0.5 text-[10px] text-muted-foreground">
            {target}
          </span>
        )}
      </div>
      <p className="text-xs">{finding.explanation}</p>
      {finding.claim && (
        <p className="text-[11px] text-muted-foreground italic">“{finding.claim}”</p>
      )}
      <p className="text-[10px] text-muted-foreground">
        {finding.dimension.replace(/_/g, " ")} · {finding.validator}
        {finding.remediation_category
          ? ` · suggested fix: ${finding.remediation_category.replace(/_/g, " ")}`
          : ""}
      </p>
    </div>
  );
}

/**
 * One fetch shared by the panel and the inline step findings.
 *
 * Both need the same payload, and react-query dedupes on the key, so the step
 * list gets its findings without a second request or a prop drilled through
 * the page.
 */
export function usePlaybookQuality(playbookId: string) {
  return useQuery({
    queryKey: ["playbook-quality", playbookId],
    queryFn: () => api.get<PlaybookQuality>(`/playbooks/${playbookId}/quality`),
  });
}

export function QualityPanel({ playbookId }: { playbookId: string }) {
  const { data, isLoading, isError, error } = usePlaybookQuality(playbookId);

  if (isLoading) {
    return (
      <Card>
        <CardHeader>
          <CardTitle>Quality</CardTitle>
          <CardDescription className="text-xs">Loading assessment…</CardDescription>
        </CardHeader>
      </Card>
    );
  }

  if (isError) {
    return (
      <Card className="border-dashed shadow-none">
        <CardHeader>
          <CardTitle>Quality</CardTitle>
          <CardDescription className="text-xs">
            Could not load the assessment
            {error instanceof Error ? `: ${error.message}` : ""}. This says
            nothing about the playbook — only that the check could not be read.
          </CardDescription>
        </CardHeader>
      </Card>
    );
  }

  if (!data) return null;

  const { summary, findings, readiness } = data;

  // Never assessed at all. Distinct from assessed-and-clean, and the copy has
  // to say so — an empty panel reads as approval.
  if (!data.assessment_id) {
    return (
      <Card className="border-dashed shadow-none">
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <ShieldQuestion className="h-4 w-4" aria-hidden />
            Quality
          </CardTitle>
          <CardDescription className="text-xs">
            This playbook has never been assessed. That is not the same as
            having passed — nothing has looked at it yet.
          </CardDescription>
        </CardHeader>
      </Card>
    );
  }

  const { coverage } = summary;
  const blocking =
    severityCount(summary.finding_counts, "critical") +
    severityCount(summary.finding_counts, "major");
  const stale = Boolean(summary.stale_reason) || summary.state === "stale";
  const moved = !summary.matches_current_content;

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex flex-wrap items-center justify-between gap-2">
          <span>Quality</span>
          <span className="flex items-center gap-2">
            {/* Coverage leads. The verdict is only as meaningful as the number
                of checks behind it, and right now that number is small. */}
            <span className="text-[11px] font-normal text-muted-foreground">
              {coverage.decided} of {coverage.total} checks run
            </span>
            {summary.state && <StatusBadge status={summary.state} />}
          </span>
        </CardTitle>
        <CardDescription className="text-xs">
          {coverage.undecided > 0 ? (
            <>
              {coverage.undecided} of {coverage.total} checks are not built yet, so
              this is a partial picture rather than a verdict. Findings below are
              real; their absence is not evidence of quality.
            </>
          ) : (
            <>All checks ran against the current content.</>
          )}
          {summary.evaluation_mode === "shadow" && (
            <> Assessment is advisory — it does not block approval.</>
          )}
        </CardDescription>
      </CardHeader>

      <CardContent className="space-y-3">
        {/* Loudest first: findings about content that is no longer on screen. */}
        {moved && (
          <div className="flex items-start gap-2 rounded-md border border-amber-500/40 bg-amber-500/10 p-3">
            <History className="mt-0.5 h-4 w-4 shrink-0" aria-hidden />
            <p className="text-xs">
              <span className="font-medium">This assessment is out of date.</span>{" "}
              The playbook has been edited since it was assessed, so everything
              below describes an earlier version of the text. Send it to review
              again to get a current result.
            </p>
          </div>
        )}

        {stale && !moved && (
          <div className="flex items-start gap-2 rounded-md border border-amber-500/40 bg-amber-500/10 p-3">
            <History className="mt-0.5 h-4 w-4 shrink-0" aria-hidden />
            <p className="text-xs">
              <span className="font-medium">Marked stale</span> because{" "}
              {STALE_REASONS[summary.stale_reason ?? ""] ?? summary.stale_reason}.
              The findings still stand; what changed is whether they are still
              the whole picture.
            </p>
          </div>
        )}

        {/* Structure is a precondition, so it sits above the three groups and
            is only shown when it has something to say. */}
        {summary.structure && summary.structure !== "pass" && (
          <div className="flex items-start gap-2 rounded-md border border-destructive/40 bg-destructive/10 p-3">
            <FileWarning className="mt-0.5 h-4 w-4 shrink-0" aria-hidden />
            <p className="text-xs">
              <span className="font-medium">
                The artifact itself is malformed ({summary.structure}).
              </span>{" "}
              Fix this first — while it holds, the verdicts below are about a
              procedure that cannot be followed as written.
            </p>
          </div>
        )}

        <div className="grid gap-2 sm:grid-cols-3">
          {(["subject", "steps", "coherence"] as const).map((name) => (
            <GroupTile key={name} name={name} state={summary.groups[name]} />
          ))}
        </div>

        {readiness.blocked_reason && (
          <p className="text-[11px] text-muted-foreground">
            Would not pass the publication gate: {readiness.blocked_reason.replace(/_/g, " ")}.
            The gate is not switched on yet.
          </p>
        )}

        {findings.length === 0 ? (
          <p className="text-xs text-muted-foreground">
            No findings recorded. With {coverage.undecided} checks still unbuilt,
            read that as “nothing the current checks can see”, not as a clean bill.
          </p>
        ) : (
          <div className="space-y-2">
            <p className="flex items-center gap-2 text-xs font-medium">
              {blocking > 0 && <AlertTriangle className="h-3.5 w-3.5" aria-hidden />}
              {findings.length} finding{findings.length === 1 ? "" : "s"}
              {blocking > 0 && (
                <span className="text-muted-foreground">
                  · {blocking} would fail a check
                </span>
              )}
            </p>
            <div className={moved ? "space-y-2 opacity-60" : "space-y-2"}>
              {findings.map((finding) => (
                <FindingRow key={finding.id} finding={finding} />
              ))}
            </div>
          </div>
        )}

        <p className="text-[10px] text-muted-foreground">
          {summary.assessed_at
            ? `Assessed ${new Date(summary.assessed_at).toLocaleString()}`
            : "Assessment time not recorded"}
          {data.validator_bundle_version ? ` · ${data.validator_bundle_version}` : ""}
        </p>
      </CardContent>
    </Card>
  );
}

/** Compact state for a list row. Reads the same summary the panel does. */
export function QualityCell({
  summary,
}: {
  summary: PlaybookQualitySummary | null | undefined;
}) {
  if (!summary || !summary.state) {
    return <span className="text-xs text-muted-foreground">—</span>;
  }
  const blocking =
    severityCount(summary.finding_counts, "critical") +
    severityCount(summary.finding_counts, "major");
  return (
    <span className="flex items-center gap-1.5">
      <StatusBadge status={summary.state} />
      {!summary.matches_current_content && (
        // The row-level version of the banner above: this verdict is about
        // text that has since moved.
        <span title="Assessed before the latest edit" className="text-[10px] text-amber-600">
          out of date
        </span>
      )}
      {blocking > 0 && (
        <span className="text-[10px] text-muted-foreground">{blocking}</span>
      )}
    </span>
  );
}

/** Findings that belong to one step, for inline display in the step list. */
export function findingsForStep(
  findings: PlaybookQualityFinding[] | undefined,
  stepId: string | undefined,
): PlaybookQualityFinding[] {
  if (!findings || !stepId) return [];
  return findings
    .filter((f) => f.target_kind === "step" && f.target_ref === stepId)
    .sort(
      (a, b) => SEVERITY_ORDER.indexOf(a.severity) - SEVERITY_ORDER.indexOf(b.severity),
    );
}
