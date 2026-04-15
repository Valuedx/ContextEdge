"use client";

import { Badge } from "@/components/ui/badge";
import { StatusBadge } from "@/components/common/status-badge";
import type { Decision } from "@/lib/types";

export function DecisionChain({
  decisions,
  onSelect,
}: {
  decisions: Decision[];
  onSelect?: (d: Decision) => void;
}) {
  if (decisions.length === 0) {
    return (
      <p className="text-sm text-muted-foreground">No decisions in this chain.</p>
    );
  }

  return (
    <div className="relative ml-3">
      <div className="absolute left-0 top-0 bottom-0 w-0.5 bg-border" />
      <div className="space-y-4">
        {decisions.map((d, idx) => (
          <div key={d.id} className="relative pl-6">
            <div className="absolute left-[-4px] top-2 h-2.5 w-2.5 rounded-full border-2 border-background bg-amber-400" />
            <button
              type="button"
              className="w-full text-left rounded-md border p-3 hover:border-amber-500/50 transition-colors space-y-1"
              onClick={() => onSelect?.(d)}
            >
              <div className="flex items-center gap-2 flex-wrap">
                <Badge variant="outline" className="text-[10px]">
                  {d.decision_type}
                </Badge>
                <Badge variant="secondary" className="text-[10px]">
                  {d.agent_step}
                </Badge>
                <StatusBadge status={d.status} />
                {d.confidence != null && (
                  <span className="text-[10px] font-mono text-muted-foreground">
                    {Math.round(d.confidence * 100)}%
                  </span>
                )}
                <span className="ml-auto text-[10px] text-muted-foreground">
                  {new Date(d.created_at).toLocaleString()}
                </span>
              </div>
              {d.compact_trace && (
                <p className="text-xs truncate">{d.compact_trace}</p>
              )}
              {d.outcomes.length > 0 && (
                <div className="flex items-center gap-1 mt-1">
                  <StatusBadge status={d.outcomes[d.outcomes.length - 1].execution_result} />
                </div>
              )}
            </button>
          </div>
        ))}
      </div>
    </div>
  );
}
