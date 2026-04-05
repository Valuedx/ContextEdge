"use client";

import { useQuery } from "@tanstack/react-query";
import Link from "next/link";
import { useParams } from "next/navigation";
import { PageHeader } from "@/components/common/page-header";
import {
  DetailPageSkeleton,
  DetailWideCardSkeleton,
} from "@/components/common/detail-page-skeleton";
import { Skeleton } from "@/components/ui/skeleton";
import { StatusBadge } from "@/components/common/status-badge";
import { buttonVariants } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Separator } from "@/components/ui/separator";
import { api } from "@/lib/api";
import type { Playbook, PlaybookVersion } from "@/lib/types";

export default function PlaybookDetailPage() {
  const params = useParams<{ id: string }>();
  const playbookId = params.id;

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
        <PageHeader title="Playbook" description="Not found." />
        <p className="text-sm text-destructive">{String((error as Error)?.message || "Missing")}</p>
        <Link href="/playbooks" className={cn(buttonVariants({ variant: "outline" }))}>
          Back to playbooks
        </Link>
      </div>
    );
  }

  const latest = versions[0];

  return (
    <div className="space-y-6">
      <PageHeader
        title={playbook.title}
        description={`Stable key ${playbook.stable_key} · ${playbook.automation_mode}`}
        actions={
          <Link href="/playbooks" className={cn(buttonVariants({ variant: "outline" }))}>
            All playbooks
          </Link>
        }
      />

      <div className="flex flex-wrap gap-2">
        <StatusBadge status={playbook.lifecycle_state} />
        <span className="rounded-md border px-2 py-0.5 text-xs capitalize">{playbook.risk_tier} risk</span>
      </div>

      {playbook.description && (
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

      <Separator />

      <div className="space-y-4">
        <h3 className="text-lg font-semibold">Versions</h3>
        {versions.length === 0 ? (
          <p className="text-sm text-muted-foreground">No published versions yet.</p>
        ) : (
          <div className="space-y-4">
            {versions.map((v) => (
              <Card key={v.id}>
                <CardHeader className="pb-2">
                  <CardTitle className="text-base flex items-center justify-between gap-2">
                    <span>v{v.semantic_version}</span>
                    <span className="text-xs font-normal text-muted-foreground">
                      {new Date(v.created_at).toLocaleString()}
                    </span>
                  </CardTitle>
                </CardHeader>
                <CardContent className="space-y-3 text-sm">
                  <div>
                    <p className="text-xs text-muted-foreground mb-1">Playbook confidence</p>
                    <p>{(v.playbook_confidence * 100).toFixed(0)}%</p>
                  </div>
                  {v.execution_confidence_guidance && (
                    <p className="text-muted-foreground">{v.execution_confidence_guidance}</p>
                  )}
                  <div>
                    <p className="text-xs text-muted-foreground mb-1">Trigger conditions</p>
                    <pre className="max-h-40 overflow-auto rounded-md bg-muted p-2 text-xs">
                      {JSON.stringify(v.trigger_conditions, null, 2)}
                    </pre>
                  </div>
                  <div>
                    <p className="text-xs text-muted-foreground mb-1">Steps ({Array.isArray(v.steps) ? v.steps.length : 0})</p>
                    <pre className="max-h-48 overflow-auto rounded-md bg-muted p-2 text-xs">
                      {JSON.stringify(v.steps, null, 2)}
                    </pre>
                  </div>
                </CardContent>
              </Card>
            ))}
          </div>
        )}
      </div>

      {latest && (
        <p className="text-xs text-muted-foreground">
          Showing {versions.length} version(s). Latest is v{latest.semantic_version}.
        </p>
      )}
    </div>
  );
}
