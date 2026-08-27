"use client";

import type { ComponentType } from "react";
import { useQuery } from "@tanstack/react-query";
import Link from "next/link";
import {
  AlertTriangle,
  BookOpen,
  CheckCircle,
  Database,
  FileSearch,
  GitBranch,
  Sparkles,
} from "lucide-react";

import { PageHeader } from "@/components/common/page-header";
import { StatusBadge } from "@/components/common/status-badge";
import { buttonVariants } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { api } from "@/lib/api";
import type { OverviewStats, Playbook } from "@/lib/types";
import { cn } from "@/lib/utils";

function StatTile({
  label,
  value,
  hint,
  icon: Icon,
}: {
  label: string;
  value: string;
  hint?: string;
  icon: ComponentType<{ className?: string }>;
}) {
  return (
    <Card className="group relative overflow-hidden transition-transform duration-300 hover:-translate-y-0.5">
      <div className="pointer-events-none absolute -right-6 -top-6 h-24 w-24 rounded-full bg-violet-500/10 blur-2xl transition-opacity group-hover:opacity-100" />
      <CardHeader className="relative flex flex-row items-center justify-between pb-2">
        <CardTitle className="text-xs font-medium uppercase tracking-wider text-muted-foreground">
          {label}
        </CardTitle>
        <Icon className="h-4 w-4 text-violet-600 dark:text-violet-300/80" />
      </CardHeader>
      <CardContent className="relative">
        <div className="text-3xl font-semibold tabular-nums tracking-tight">{value}</div>
        {hint && <p className="mt-1 text-xs text-muted-foreground">{hint}</p>}
      </CardContent>
    </Card>
  );
}

function playbookNeedsAttention(pb: Playbook): string | null {
  const now = Date.now();
  if (pb.lifecycle_state === "candidate" || pb.lifecycle_state === "under_review") {
    return "Awaiting governance";
  }
  if (pb.lifecycle_state === "approved") {
    if (pb.expiry_at) {
      const exp = new Date(pb.expiry_at).getTime();
      const days = (exp - now) / 86400000;
      if (days <= 0) return "Past expiry";
      if (days <= 60) return `Expires in ${Math.ceil(days)}d`;
    }
    if (pb.last_validated_at) {
      const daysSince = (now - new Date(pb.last_validated_at).getTime()) / 86400000;
      if (daysSince > 90) return "Stale validation";
    } else {
      return "Never validated";
    }
  }
  return null;
}

function OverviewSkeleton() {
  return (
    <div className="space-y-8">
      <div className="glass-panel space-y-3 p-5">
        <Skeleton className="h-8 w-48 max-w-full" />
        <Skeleton className="h-4 w-full max-w-xl" />
      </div>
      <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-3">
        {Array.from({ length: 6 }).map((_, i) => (
          <div key={i} className="glass-panel space-y-4 p-5">
            <div className="flex justify-between">
              <Skeleton className="h-3 w-24" />
              <Skeleton className="h-4 w-4 rounded-md" />
            </div>
            <Skeleton className="h-9 w-20" />
            <Skeleton className="h-3 w-full max-w-[12rem]" />
          </div>
        ))}
      </div>
      <div className="glass-panel space-y-4 p-5">
        <Skeleton className="h-5 w-56" />
        <Skeleton className="h-4 w-full" />
        <Skeleton className="h-24 w-full rounded-lg" />
      </div>
    </div>
  );
}

export default function OverviewPage() {
  const overview = useQuery({
    queryKey: ["overview-stats"],
    queryFn: async () => {
      const [stats, playbooks] = await Promise.all([
        api.get<OverviewStats>("/overview/stats"),
        api.get<Playbook[]>("/playbooks", { limit: "20" }),
      ]);
      return { stats, playbooks };
    },
  });

  const stats = overview.data?.stats;
  const playbooks = overview.data?.playbooks ?? [];

  const activeSources = stats?.active_sources ?? 0;
  const connectedSources = stats?.connected_sources ?? 0;
  const totalEvidence = stats?.total_evidence ?? 0;
  const totalEpisodes = stats?.total_episodes ?? 0;
  const pendingEpisodes = stats?.pending_episodes ?? 0;
  const approvedPlaybooks = stats?.approved_playbooks ?? 0;
  const candidatePlaybooks = stats?.candidate_playbooks ?? 0;

  const driftRows =
    playbooks
      .map((pb) => ({ pb, reason: playbookNeedsAttention(pb) }))
      .filter((x) => x.reason !== null)
      .slice(0, 8);

  return (
    <div className="space-y-8">
      <PageHeader
        title="Overview"
        description="Ingestion health, review queues, and playbook lifecycle at a glance."
      />

      {overview.isLoading ? (
        <OverviewSkeleton />
      ) : overview.isError ? (
        <div className="glass-panel border-rose-500/30 bg-rose-500/5 py-6 text-center text-sm text-rose-200">
          Could not load overview stats. Check the API and your session.
        </div>
      ) : (
        <>
          <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-3">
            <StatTile
              label="Active sources"
              value={activeSources.toLocaleString()}
              hint={`${connectedSources.toLocaleString()} connected`}
              icon={Database}
            />
            <StatTile
              label="Evidence items"
              value={totalEvidence.toLocaleString()}
              hint="Tickets & primary records"
              icon={FileSearch}
            />
            <StatTile
              label="Episodes"
              value={totalEpisodes.toLocaleString()}
              hint={`${pendingEpisodes.toLocaleString()} pending review`}
              icon={GitBranch}
            />
            <StatTile
              label="Approved playbooks"
              value={approvedPlaybooks.toLocaleString()}
              hint={`${candidatePlaybooks.toLocaleString()} in candidate / review`}
              icon={BookOpen}
            />
            <StatTile
              label="Pending reviews"
              value={(pendingEpisodes + candidatePlaybooks).toLocaleString()}
              hint="Episodes + playbook candidates"
              icon={AlertTriangle}
            />
            <StatTile
              label="Healthy syncs"
              value={connectedSources.toLocaleString()}
              hint="Sources with connected auth"
              icon={CheckCircle}
            />
          </div>

          <Card>
            <CardHeader className="flex flex-row items-center justify-between gap-4">
              <div className="flex items-center gap-2">
                <Sparkles className="h-5 w-5 text-cyan-300/90" />
                <CardTitle className="text-lg">Drift &amp; freshness signals</CardTitle>
              </div>
              <div className="flex flex-wrap gap-2">
                <Link
                  href="/drift"
                  className={cn(buttonVariants({ variant: "outline", size: "sm" }))}
                >
                  Drift dashboard
                </Link>
                <Link
                  href="/playbooks"
                  className={cn(buttonVariants({ variant: "outline", size: "sm" }))}
                >
                  Open playbooks
                </Link>
              </div>
            </CardHeader>
            <CardContent className="space-y-3">
              <p className="text-sm text-muted-foreground">
                Heuristic flags from playbook metadata (expiry, validation age, lifecycle). Backend
                drift jobs can extend this with feedback-driven alerts.
              </p>
              {driftRows.length === 0 ? (
                <p className="rounded-lg border bg-card py-8 text-center text-sm text-muted-foreground shadow-sm">
                  No playbooks flagged right now.
                </p>
              ) : (
                <ul className="space-y-2">
                  {driftRows.map(({ pb, reason }) => (
                    <li
                      key={pb.id}
                      className="flex flex-wrap items-center justify-between gap-3 rounded-lg border bg-card px-4 py-3 shadow-sm"
                    >
                      <div className="min-w-0">
                        <Link
                          href={`/playbooks/${pb.id}`}
                          className="font-medium text-primary hover:underline"
                        >
                          {pb.title}
                        </Link>
                        <div className="mt-1 flex flex-wrap gap-2">
                          <StatusBadge status={pb.lifecycle_state} />
                          <span className="text-xs text-muted-foreground font-mono">
                            {pb.stable_key}
                          </span>
                        </div>
                      </div>
                      <span className="text-sm text-muted-foreground">{reason}</span>
                    </li>
                  ))}
                </ul>
              )}
            </CardContent>
          </Card>
        </>
      )}
    </div>
  );
}
