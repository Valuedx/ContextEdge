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

import { AlertTriangle, ArrowRight, FileText, Layers, Lightbulb } from "lucide-react";

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
}

// Step kinds carry different risk. A remediation step changes the
// system; a diagnostic one only looks at it, and colouring them alike
// hides the distinction a reviewer is actually assessing.
const TYPE_STYLES: Record<string, string> = {
  diagnostic: "bg-sky-500/10 text-sky-300 border-sky-500/30",
  remediation: "bg-amber-500/10 text-amber-300 border-amber-500/30",
  verification: "bg-emerald-500/10 text-emerald-300 border-emerald-500/30",
  escalation: "bg-fuchsia-500/10 text-fuchsia-300 border-fuchsia-500/30",
  communication: "bg-slate-500/10 text-slate-300 border-slate-500/30",
};

const QUALITY_STYLES: Record<string, string> = {
  high: "text-emerald-400",
  medium: "text-amber-400",
  low: "text-rose-400",
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
        <label className="mb-2 flex items-center gap-2 text-xs text-slate-400">
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
            className="rounded-lg border border-slate-800 bg-slate-900/50 p-3"
          >
            <div className="flex items-start gap-3">
              <span className="mt-0.5 w-6 shrink-0 text-right font-mono text-xs text-slate-500">
                {step.order ?? index + 1}
              </span>
              <div className="min-w-0 flex-1 space-y-2">
                <div className="flex flex-wrap items-center gap-2">
                  {step.type && (
                    <span
                      className={cn(
                        "rounded border px-1.5 py-0.5 text-[10px] font-semibold uppercase tracking-wide",
                        TYPE_STYLES[type] ??
                          "bg-slate-500/10 text-slate-300 border-slate-500/30",
                      )}
                    >
                      {step.type}
                    </span>
                  )}
                  {step.grounding_status === "non_grounded" && (
                    <span
                      className="inline-flex items-center gap-1 rounded border border-violet-500/40 bg-violet-500/10 px-1.5 py-0.5 text-[10px] font-semibold uppercase tracking-wide text-violet-300"
                      title={
                        step.reason ||
                        "Expert recommendation — not explicitly present in the source material."
                      }
                    >
                      <Lightbulb className="h-3 w-3" />
                      Best Practice (Non-Grounded)
                    </span>
                  )}
                  {step.evidence_quality && (
                    <span
                      className={cn(
                        "text-[10px] uppercase tracking-wide",
                        QUALITY_STYLES[step.evidence_quality.toLowerCase()] ??
                          "text-slate-400",
                      )}
                      title="How well the source evidence supports this step"
                    >
                      {step.evidence_quality} evidence
                    </span>
                  )}
                </div>

                <p className="text-sm text-slate-100">{step.text || "(no instruction text)"}</p>

                {step.expected_outcome && (
                  <p className="flex items-start gap-1.5 text-xs text-slate-400">
                    <ArrowRight className="mt-0.5 h-3 w-3 shrink-0 text-emerald-400" />
                    <span>{step.expected_outcome}</span>
                  </p>
                )}

                {step.on_failure && (
                  <p className="flex items-start gap-1.5 text-xs text-amber-300/90">
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
                        className="inline-flex items-center gap-1 rounded border border-slate-700 bg-slate-800/60 px-1.5 py-0.5 text-[10px] text-slate-300"
                        title={ref.title || undefined}
                      >
                        {ref.kind === "knowledge" ? (
                          <FileText className="h-3 w-3" />
                        ) : (
                          <Layers className="h-3 w-3" />
                        )}
                        {ref.label || ref.kind || "source"}
                        {ref.title && (
                          <span className="max-w-[16rem] truncate text-slate-400">
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
