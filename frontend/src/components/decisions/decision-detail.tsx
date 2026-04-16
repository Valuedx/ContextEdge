"use client";

import Link from "next/link";
import { ExternalLink } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Separator } from "@/components/ui/separator";
import { StatusBadge } from "@/components/common/status-badge";
import type { Decision } from "@/lib/types";

function ConfidenceGauge({ value }: { value: number | null }) {
  if (value == null) return <span className="text-muted-foreground">—</span>;
  const pct = Math.round(value * 100);
  const color =
    pct >= 80 ? "text-green-400" : pct >= 50 ? "text-yellow-400" : "text-red-400";
  return <span className={`font-mono text-sm font-semibold ${color}`}>{pct}%</span>;
}

function RiskBadge({ level }: { level: string | null }) {
  if (!level) return null;
  const variant =
    level === "high"
      ? "destructive"
      : level === "medium"
        ? "secondary"
        : "outline";
  return <Badge variant={variant as "destructive" | "secondary" | "outline"}>{level}</Badge>;
}

export function DecisionDetail({ decision }: { decision: Decision }) {
  const latestOutcome = decision.outcomes[decision.outcomes.length - 1];

  return (
    <Card>
      <CardHeader className="pb-3">
        <div className="flex items-center gap-2 flex-wrap">
          <Badge variant="outline">{decision.decision_type}</Badge>
          <Badge variant="secondary">{decision.agent_step}</Badge>
          <Badge variant={decision.actor_type === "human" ? "default" : "secondary"}>
            {decision.actor_type}
          </Badge>
          <StatusBadge status={decision.status} />
          <ConfidenceGauge value={decision.confidence} />
          <span className="ml-auto text-xs text-muted-foreground">
            {new Date(decision.created_at).toLocaleString()}
          </span>
        </div>
        {decision.compact_trace && (
          <CardTitle className="text-sm font-normal mt-2">
            {decision.compact_trace}
          </CardTitle>
        )}
      </CardHeader>
      <CardContent className="space-y-4 text-sm">
        {Object.keys(decision.context_snapshot).length > 0 && (
          <div>
            <p className="text-xs text-muted-foreground mb-1 font-medium">Context</p>
            <div className="grid gap-1 md:grid-cols-2">
              {Object.entries(decision.context_snapshot).map(([k, v]) => (
                <div key={k} className="flex gap-2">
                  <span className="text-muted-foreground font-mono text-xs">{k}:</span>
                  <span className="font-mono text-xs truncate">{String(v)}</span>
                </div>
              ))}
            </div>
          </div>
        )}

        {decision.evidence_summary.length > 0 && (
          <div>
            <p className="text-xs text-muted-foreground mb-1 font-medium">Evidence used</p>
            <ul className="space-y-1">
              {decision.evidence_summary.map((ref, i) => (
                <li key={i} className="flex items-center gap-2 text-xs">
                  <Badge variant="outline" className="text-[10px]">
                    {ref.ref_type}
                  </Badge>
                  <span className="font-mono truncate">{ref.ref_id}</span>
                  {ref.description && (
                    <span className="text-muted-foreground truncate">{ref.description}</span>
                  )}
                </li>
              ))}
            </ul>
          </div>
        )}

        {decision.options.length > 0 && (
          <>
            <Separator />
            <div>
              <p className="text-xs text-muted-foreground mb-2 font-medium">
                Options ({decision.options.length})
              </p>
              <div className="space-y-2">
                {decision.options.map((opt) => (
                  <div
                    key={opt.id}
                    className={`rounded-md border p-3 space-y-1 ${
                      opt.selected ? "border-amber-500/50 bg-amber-500/5" : ""
                    }`}
                  >
                    <div className="flex items-center gap-2">
                      <span className="font-mono text-xs font-medium">{opt.action}</span>
                      {opt.selected && (
                        <Badge variant="default" className="text-[10px]">
                          selected
                        </Badge>
                      )}
                      <RiskBadge level={opt.risk_level} />
                      {opt.suitability != null && (
                        <span className="ml-auto text-xs text-muted-foreground">
                          suitability: {Math.round(opt.suitability * 100)}%
                        </span>
                      )}
                    </div>
                    {opt.rejection_reason && (
                      <p className="text-xs text-muted-foreground">
                        Rejected: {opt.rejection_reason}
                      </p>
                    )}
                    {opt.preconditions.length > 0 && (
                      <p className="text-xs text-muted-foreground">
                        Preconditions: {opt.preconditions.join(", ")}
                      </p>
                    )}
                  </div>
                ))}
              </div>
            </div>
          </>
        )}

        {decision.rationale_summary && (
          <div>
            <p className="text-xs text-muted-foreground mb-1 font-medium">Reasoning</p>
            <p className="text-xs whitespace-pre-wrap">{decision.rationale_summary}</p>
          </div>
        )}

        {decision.uncertainty_notes && (
          <div>
            <p className="text-xs text-muted-foreground mb-1 font-medium">
              Uncertainty
            </p>
            <p className="text-xs whitespace-pre-wrap">{decision.uncertainty_notes}</p>
          </div>
        )}

        {(decision.approval_required || decision.policy_refs.length > 0 || decision.human_override) && (
          <div>
            <p className="text-xs text-muted-foreground mb-1 font-medium">Governance</p>
            <div className="flex gap-2 flex-wrap">
              {decision.approval_required && (
                <Badge variant="destructive">approval required</Badge>
              )}
              {decision.human_override && (
                <Badge variant="secondary">human override</Badge>
              )}
              {decision.policy_refs.map((pr, i) => (
                <Badge key={i} variant="outline" className="font-mono text-[10px]">
                  {pr}
                </Badge>
              ))}
            </div>
          </div>
        )}

        {latestOutcome && (
          <>
            <Separator />
            <div>
              <p className="text-xs text-muted-foreground mb-1 font-medium">Outcome</p>
              <div className="rounded-md border p-3 space-y-1">
                <div className="flex items-center gap-2">
                  <span className="font-mono text-xs">{latestOutcome.action_executed}</span>
                  <StatusBadge status={latestOutcome.execution_result} />
                  {latestOutcome.follow_up_needed && (
                    <Badge variant="secondary">follow-up needed</Badge>
                  )}
                </div>
                {latestOutcome.feedback_received && (
                  <p className="text-xs text-muted-foreground">
                    Feedback: {latestOutcome.feedback_received}
                  </p>
                )}
                {latestOutcome.follow_up_decision_id && (
                  <Link
                    href={`/decisions?id=${latestOutcome.follow_up_decision_id}`}
                    className="text-xs text-primary hover:underline inline-flex items-center gap-1"
                  >
                    View follow-up decision
                    <ExternalLink className="h-3 w-3" />
                  </Link>
                )}
              </div>
            </div>
          </>
        )}

        <div className="flex gap-2 pt-2">
          <Link
            href={`/graph-explorer?node_type=decision&node_id=${decision.id}`}
            className="text-xs text-primary hover:underline inline-flex items-center gap-1"
          >
            View in Graph
            <ExternalLink className="h-3 w-3" />
          </Link>
          {decision.parent_decision_id && (
            <Link
              href={`/decisions?id=${decision.parent_decision_id}`}
              className="text-xs text-primary hover:underline inline-flex items-center gap-1"
            >
              Parent decision
              <ExternalLink className="h-3 w-3" />
            </Link>
          )}
        </div>
      </CardContent>
    </Card>
  );
}
