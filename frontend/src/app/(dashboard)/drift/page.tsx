"use client";

import { useQuery } from "@tanstack/react-query";
import Link from "next/link";

import { PageHeader } from "@/components/common/page-header";
import { StatusBadge } from "@/components/common/status-badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { api } from "@/lib/api";
import type { DriftAlert } from "@/lib/types";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useRouter } from "next/navigation";
import { Loader2, RefreshCw, Sparkles } from "lucide-react";
import { toast } from "sonner";

function DriftAction({ alert }: { alert: DriftAlert }) {
  const router = useRouter();
  const queryClient = useQueryClient();

  const regenerateMut = useMutation({
    mutationFn: (patternId: string) => api.post("/playbooks/generate", { pattern_id: patternId }),
    onSuccess: () => {
      toast.success("Playbook regenerated with updated pattern nodes!");
      queryClient.invalidateQueries({ queryKey: ["drift-alerts"] });
      queryClient.invalidateQueries({ queryKey: ["playbooks"] });
      queryClient.invalidateQueries({ queryKey: ["patterns"] });
      router.push(`/playbooks/${alert.playbook_id}`);
    },
    onError: (err: Error) => {
      toast.error(err.message || "Regeneration failed");
    },
  });

  if (!alert.pattern_id) {
    return (
      <Link href={`/playbooks/${alert.playbook_id}`}>
        <Button variant="outline" size="sm" className="gap-1 text-xs">
          View Playbook
        </Button>
      </Link>
    );
  }

  return (
    <div className="flex items-center gap-2">
      <Button
        variant="default"
        size="sm"
        className="bg-amber-600 hover:bg-amber-700 text-white gap-1.5 text-xs font-medium"
        onClick={() => alert.pattern_id && regenerateMut.mutate(alert.pattern_id)}
        disabled={regenerateMut.isPending}
      >
        {regenerateMut.isPending ? (
          <Loader2 className="h-3.5 w-3.5 animate-spin" />
        ) : (
          <RefreshCw className="h-3.5 w-3.5" />
        )}
        Verify & Regenerate
      </Button>
      <Link href={`/playbooks/${alert.playbook_id}`}>
        <Button variant="outline" size="sm" className="gap-1 text-xs">
          View
        </Button>
      </Link>
    </div>
  );
}

function DriftSkeleton() {
  return (
    <div className="space-y-4">
      {Array.from({ length: 4 }).map((_, i) => (
        <div
          key={i}
          className="rounded-lg border bg-card p-4 shadow-sm"
        >
          <div className="flex flex-wrap items-start justify-between gap-3">
            <div className="min-w-0 flex-1 space-y-2">
              <Skeleton className="h-5 w-48 max-w-full" />
              <Skeleton className="h-4 w-32" />
            </div>
            <Skeleton className="h-6 w-16 rounded-full" />
          </div>
          <Skeleton className="mt-3 h-4 w-full max-w-xl" />
        </div>
      ))}
    </div>
  );
}

export default function DriftPage() {
  const { data: alerts = [], isLoading, isError, error, refetch, isFetching } = useQuery({
    queryKey: ["drift-alerts"],
    queryFn: () => api.get<DriftAlert[]>("/drift/alerts"),
  });

  return (
    <div className="space-y-6">
      <PageHeader
        title="Drift & freshness"
        description="Playbook-level signals from validation age, pattern node additions, expiry, and negative retrieval feedback."
        actions={
          <Button variant="outline" size="sm" onClick={() => refetch()} disabled={isFetching}>
            {isFetching ? "Refreshing…" : "Refresh"}
          </Button>
        }
      />

      {isLoading ? (
        <DriftSkeleton />
      ) : isError ? (
        <Card className="border-rose-500/30 bg-rose-500/5">
          <CardContent className="py-6 text-sm text-destructive">
            {String((error as Error)?.message || "Could not load drift alerts")}
          </CardContent>
        </Card>
      ) : alerts.length === 0 ? (
        <Card>
          <CardContent className="py-10 text-center text-sm text-muted-foreground">
            No drift alerts for approved playbooks right now. Pattern node additions, negative feedback thresholds, validation staleness, and expiry are evaluated when you refresh or when scheduled jobs run.
          </CardContent>
        </Card>
      ) : (
        <Card>
          <CardHeader>
            <CardTitle className="text-base">
              {alerts.length} playbook{alerts.length === 1 ? "" : "s"} flagged for drift review
            </CardTitle>
          </CardHeader>
          <CardContent className="space-y-3">
            <ul className="space-y-3">
              {alerts.map((a) => (
                <li
                  key={a.playbook_id}
                  className="flex flex-wrap items-center justify-between gap-4 rounded-lg border bg-card p-4 shadow-sm"
                >
                  <div className="min-w-0 space-y-1.5">
                    <div className="flex items-center gap-2">
                      <Link
                        href={`/playbooks/${a.playbook_id}`}
                        className="font-semibold text-primary hover:underline text-base"
                      >
                        {a.title}
                      </Link>
                      <StatusBadge status={a.severity} />
                    </div>
                    <p className="font-mono text-xs text-muted-foreground">Playbook ID: {a.playbook_id}</p>
                    <ul className="space-y-1 text-sm text-muted-foreground">
                      {a.issues.map((issue) => (
                        <li key={issue} className="flex items-center gap-1.5 text-xs text-amber-500 font-medium">
                          <Sparkles className="h-3.5 w-3.5" />
                          {issue === "pattern_nodes_added_drift"
                            ? "New episodes/nodes added to pattern — Playbook requires verification & regeneration"
                            : issue.replace(/_/g, " ")}
                        </li>
                      ))}
                    </ul>
                  </div>

                  <DriftAction alert={a} />
                </li>
              ))}
            </ul>
          </CardContent>
        </Card>
      )}
    </div>
  );
}
