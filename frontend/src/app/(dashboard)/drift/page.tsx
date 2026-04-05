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

function DriftSkeleton() {
  return (
    <div className="space-y-4">
      {Array.from({ length: 4 }).map((_, i) => (
        <div
          key={i}
          className="rounded-xl border border-black/10 bg-white/40 p-4 dark:border-white/10 dark:bg-white/[0.04]"
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
        description="Playbook-level signals from validation age, expiry, and recent negative retrieval feedback. Checks run on load and on a schedule via workers."
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
            No drift alerts for approved playbooks right now. Negative feedback thresholds, validation
            staleness, and expiry are evaluated when you refresh or when scheduled jobs run.
          </CardContent>
        </Card>
      ) : (
        <Card>
          <CardHeader>
            <CardTitle className="text-base">
              {alerts.length} playbook{alerts.length === 1 ? "" : "s"} flagged
            </CardTitle>
          </CardHeader>
          <CardContent className="space-y-3">
            <ul className="space-y-2">
              {alerts.map((a) => (
                <li
                  key={a.playbook_id}
                  className="flex flex-wrap items-start justify-between gap-3 rounded-xl border border-black/10 bg-white/40 px-4 py-3 backdrop-blur-sm dark:border-white/10 dark:bg-white/[0.04]"
                >
                  <div className="min-w-0">
                    <Link
                      href={`/playbooks/${a.playbook_id}`}
                      className="font-medium text-primary hover:underline"
                    >
                      {a.title}
                    </Link>
                    <p className="mt-2 font-mono text-xs text-muted-foreground">{a.playbook_id}</p>
                    <ul className="mt-2 list-inside list-disc text-sm text-muted-foreground">
                      {a.issues.map((issue) => (
                        <li key={issue}>{issue.replace(/_/g, " ")}</li>
                      ))}
                    </ul>
                  </div>
                  <StatusBadge status={a.severity} />
                </li>
              ))}
            </ul>
          </CardContent>
        </Card>
      )}
    </div>
  );
}
