"use client";

/**
 * A playbook's steps, rendered as a procedure rather than as JSON.
 *
 * The detail page was showing `JSON.stringify(steps)` inside a 12rem
 * scrollable box, so a generated playbook read as a summary with no
 * steps — the steps were there, just unreadable, and everything that
 * makes them reviewable was buried: what each step expects to happen,
 * what to do when it doesn't, and which episode or KB article it was
 * drawn from.
 *
 * Those citations are the point. A reviewer approving a procedure needs
 * to see it is grounded in real incidents rather than invented, and a
 * step whose `source_refs` are empty is exactly the one to question.
 */

import { useState } from "react";

import { AlertTriangle, ArrowRight, FileText, Layers, Lightbulb, PencilLine } from "lucide-react";

import { cn } from "@/lib/utils";

export interface PlaybookStepRef {
  id?: string;
  kind?: string;
  label?: string;
  title?: string;
}

export interface PlaybookStep {
  text?: string;
  type?: string;
  order?: number;
  status?: string;
  on_failure?: string;
  expected_outcome?: string;
  evidence_quality?: string;
  source_refs?: PlaybookStepRef[];
  // Grounded vs best-practice taxonomy (prompt v5 + structural
  // enforcement): "grounded" steps carry validated source_refs;
  // "non_grounded" steps are expert recommendations and must never be
  // presented as if the sources stated them.
  grounding_status?: string;
  step_classification?: string;
  reason?: string;
  human_edited?: boolean;
}

// Step kinds carry different risk. A remediation step changes the
// system; a diagnostic one only looks at it, and colouring them alike
// hides the distinction a reviewer is actually assessing.
const TYPE_STYLES: Record<string, string> = {
  diagnostic: "border-sky-200 bg-sky-50 text-sky-700 dark:border-sky-500/30 dark:bg-sky-500/10 dark:text-sky-300",
  remediation: "border-amber-200 bg-amber-50 text-amber-800 dark:border-amber-500/30 dark:bg-amber-500/10 dark:text-amber-300",
  verification: "border-emerald-200 bg-emerald-50 text-emerald-700 dark:border-emerald-500/30 dark:bg-emerald-500/10 dark:text-emerald-300",
  escalation: "border-fuchsia-200 bg-fuchsia-50 text-fuchsia-700 dark:border-fuchsia-500/30 dark:bg-fuchsia-500/10 dark:text-fuchsia-300",
  communication: "border-slate-200 bg-slate-50 text-slate-700 dark:border-slate-500/30 dark:bg-slate-500/10 dark:text-slate-300",
};

const QUALITY_STYLES: Record<string, string> = {
  high: "text-emerald-700 dark:text-emerald-400",
  medium: "text-amber-700 dark:text-amber-400",
  low: "text-rose-700 dark:text-rose-400",
};

/** By declared order, not array position — the two disagree in stored data. */
export function sortSteps(steps: PlaybookStep[]): PlaybookStep[] {
  return [...steps].sort((a, b) => {
    const ao = typeof a.order === "number" ? a.order : Number.MAX_SAFE_INTEGER;
    const bo = typeof b.order === "number" ? b.order : Number.MAX_SAFE_INTEGER;
    return ao - bo;
  });
}

export function PlaybookSteps({
  steps,
  className,
}: {
  steps: unknown;
  className?: string;
}) {
  const list: PlaybookStep[] = Array.isArray(steps) ? (steps as PlaybookStep[]) : [];
  const [hideBestPractice, setHideBestPractice] = useState(false);

  if (list.length === 0) {
    return (
      <p className={cn("text-sm text-muted-foreground", className)}>
        This version has no steps. It cannot be sent for review or executed.
      </p>
    );
  }

  const bestPracticeCount = list.filter(
    (s) => s.grounding_status === "non_grounded",
  ).length;
  const visible = hideBestPractice
    ? list.filter((s) => s.grounding_status !== "non_grounded")
    : list;

  return (
    <div className={className}>
      {bestPracticeCount > 0 && (
        <label className="mb-2 flex items-center gap-2 text-xs text-muted-foreground">
          <input
            type="checkbox"
            checked={hideBestPractice}
            onChange={(e) => setHideBestPractice(e.target.checked)}
            className="h-3 w-3"
          />
          Hide best-practice steps ({bestPracticeCount} of {list.length} are
          expert recommendations, not from source evidence)
        </label>
      )}
    <ol className="space-y-3">
      {sortSteps(visible).map((step, index) => {
        const type = (step.type || "").toLowerCase();
        const refs = Array.isArray(step.source_refs) ? step.source_refs : [];
        return (
          <li
            key={`${step.order ?? index}-${(step.text || "").slice(0, 24)}`}
            className="rounded-lg border bg-card p-4 shadow-sm"
          >
            <div className="flex items-start gap-3">
              <span className="mt-0.5 w-6 shrink-0 text-right font-mono text-xs text-muted-foreground">
                {step.order ?? index + 1}
              </span>
              <div className="min-w-0 flex-1 space-y-2">
                <div className="flex flex-wrap items-center gap-2">
                  {step.type && (
                    <span
                      className={cn(
                        "rounded border px-1.5 py-0.5 text-[10px] font-semibold uppercase tracking-wide",
                        TYPE_STYLES[type] ??
                          "border-slate-200 bg-slate-50 text-slate-700 dark:border-slate-500/30 dark:bg-slate-500/10 dark:text-slate-300",
                      )}
                    >
                      {step.type}
                    </span>
                  )}
                  {step.grounding_status === "non_grounded" && (
                    <span
                      className="inline-flex items-center gap-1 rounded border border-violet-200 bg-violet-50 px-1.5 py-0.5 text-[10px] font-semibold uppercase tracking-wide text-violet-700 dark:border-violet-500/40 dark:bg-violet-500/10 dark:text-violet-300"
                      title={
                        step.reason ||
                        "Expert recommendation — not explicitly present in the source material."
                      }
                    >
                      <Lightbulb className="h-3 w-3" />
                      Best Practice (Non-Grounded)
                    </span>
                  )}
                  {step.human_edited && (
                    <span
                      className="inline-flex items-center gap-1 rounded border border-orange-200 bg-orange-50 px-1.5 py-0.5 text-[10px] font-semibold uppercase tracking-wide text-orange-800 dark:border-orange-500/40 dark:bg-orange-500/10 dark:text-orange-300"
                      title="A knowledge manager edited this grounded step after generation. Citations are unchanged."
                    >
                      <PencilLine className="h-3 w-3" />
                      Edited
                    </span>
                  )}
                  {step.evidence_quality && (
                    <span
                      className={cn(
                        "text-[10px] uppercase tracking-wide",
                        QUALITY_STYLES[step.evidence_quality.toLowerCase()] ??
                          "text-muted-foreground",
                      )}
                      title="How well the source evidence supports this step"
                    >
                      {step.evidence_quality} evidence
                    </span>
                  )}
                </div>

                <p className="text-sm font-medium leading-6 text-foreground">{step.text || "(no instruction text)"}</p>

                {step.expected_outcome && (
                  <p className="flex items-start gap-1.5 text-xs text-muted-foreground">
                    <ArrowRight className="mt-0.5 h-3 w-3 shrink-0 text-emerald-600 dark:text-emerald-400" />
                    <span>{step.expected_outcome}</span>
                  </p>
                )}

                {step.on_failure && (
                  <p className="flex items-start gap-1.5 text-xs text-amber-700 dark:text-amber-300/90">
                    <AlertTriangle className="mt-0.5 h-3 w-3 shrink-0" />
                    <span>{step.on_failure}</span>
                  </p>
                )}

                {/* Provenance. A step citing nothing is the one to question. */}
                {refs.length > 0 && (
                  <div className="flex flex-wrap items-center gap-1.5 pt-0.5">
                    {refs.map((ref, i) => (
                      <span
                        key={ref.id || `${ref.label}-${i}`}
                        className="inline-flex items-center gap-1 rounded border bg-muted px-1.5 py-0.5 text-[10px] text-muted-foreground"
                        title={ref.title || undefined}
                      >
                        {ref.kind === "knowledge" ? (
                          <FileText className="h-3 w-3" />
                        ) : (
                          <Layers className="h-3 w-3" />
                        )}
                        {ref.label || ref.kind || "source"}
                        {ref.title && (
                          <span className="max-w-[16rem] truncate text-muted-foreground">
                            · {ref.title}
                          </span>
                        )}
                      </span>
                    ))}
                  </div>
                )}
              </div>
            </div>
          </li>
        );
      })}
    </ol>
    </div>
  );
}
