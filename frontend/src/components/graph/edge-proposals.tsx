"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowRight, Check, Loader2, X } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { api } from "@/lib/api";

interface EdgeProposal {
  edge_id: string;
  source_ci: string;
  target_ci: string;
  rationale: string;
  evidence_ids: string[];
  proposed_at: string | null;
}

// Agent-discovered dependencies never enter the agent projection
// unreviewed — this queue is how they become authored topology
// (approve) or audit history (reject). Requires knowledge_manager.
export function EdgeProposals() {
  const qc = useQueryClient();
  const [acting, setActing] = useState<string | null>(null);

  const { data, isLoading, error } = useQuery<{ proposals: EdgeProposal[] }>({
    queryKey: ["edge-proposals"],
    queryFn: () => api.get("/graph/edge-proposals"),
    retry: false,
  });

  const review = useMutation({
    mutationFn: ({ edgeId, verdict }: { edgeId: string; verdict: "approve" | "reject" }) =>
      api.post(`/graph/edge-proposals/${edgeId}/${verdict}`, {}),
    onMutate: ({ edgeId }) => setActing(edgeId),
    onSettled: () => setActing(null),
    onSuccess: (_data, { verdict }) => {
      qc.invalidateQueries({ queryKey: ["edge-proposals"] });
      toast.success(
        verdict === "approve"
          ? "Approved — authored depends_on edge created"
          : "Rejected — proposal closed",
      );
    },
    onError: (err: Error) => toast.error(err.message || "Review failed"),
  });

  if (isLoading) {
    return <p className="text-sm text-muted-foreground">Loading proposals…</p>;
  }
  if (error) {
    return (
      <div className="rounded-md border p-8 text-center text-sm text-muted-foreground">
        Could not load proposals — reviewing dependency proposals requires the
        knowledge manager role.
      </div>
    );
  }
  const proposals = data?.proposals ?? [];
  if (proposals.length === 0) {
    return (
      <div className="rounded-md border p-12 text-center text-muted-foreground">
        No pending dependency proposals. Agents record discovered dependencies
        here during investigations; approving one makes it authored topology.
      </div>
    );
  }

  return (
    <Card>
      <CardHeader className="pb-3">
        <CardTitle className="text-sm">
          Proposed dependencies ({proposals.length})
        </CardTitle>
      </CardHeader>
      <CardContent className="space-y-3">
        {proposals.map((p) => (
          <div key={p.edge_id} className="rounded-md border p-3 space-y-2">
            <div className="flex items-center gap-2 flex-wrap">
              <span className="font-mono text-xs font-medium">{p.source_ci}</span>
              <ArrowRight className="h-3 w-3 text-muted-foreground" />
              <span className="font-mono text-xs font-medium">{p.target_ci}</span>
              <Badge variant="outline" className="text-[10px]">
                depends_on (proposed)
              </Badge>
              {p.evidence_ids.length > 0 && (
                <Badge variant="secondary" className="text-[10px]">
                  {p.evidence_ids.length} evidence ref
                  {p.evidence_ids.length !== 1 ? "s" : ""}
                </Badge>
              )}
              <span className="ml-auto text-[10px] text-muted-foreground">
                {p.proposed_at ? new Date(p.proposed_at).toLocaleString() : ""}
              </span>
            </div>
            {p.rationale && <p className="text-xs">{p.rationale}</p>}
            <div className="flex gap-2">
              <Button
                size="sm"
                variant="outline"
                disabled={acting === p.edge_id}
                onClick={() => review.mutate({ edgeId: p.edge_id, verdict: "approve" })}
              >
                {acting === p.edge_id ? (
                  <Loader2 className="mr-1.5 h-3.5 w-3.5 animate-spin" />
                ) : (
                  <Check className="mr-1.5 h-3.5 w-3.5" />
                )}
                Approve
              </Button>
              <Button
                size="sm"
                variant="ghost"
                disabled={acting === p.edge_id}
                onClick={() => review.mutate({ edgeId: p.edge_id, verdict: "reject" })}
              >
                <X className="mr-1.5 h-3.5 w-3.5" />
                Reject
              </Button>
            </div>
          </div>
        ))}
      </CardContent>
    </Card>
  );
}
