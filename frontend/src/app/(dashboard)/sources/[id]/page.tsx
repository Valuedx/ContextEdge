"use client";

import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ColumnDef } from "@tanstack/react-table";
import Link from "next/link";
import { useParams } from "next/navigation";
import { Loader2, RefreshCw, KeyRound, Pause, Play, Square } from "lucide-react";
import { toast } from "sonner";

import { PageHeader } from "@/components/common/page-header";
import {
  DetailCardGridSkeleton,
  DetailPageSkeleton,
  DetailWideCardSkeleton,
} from "@/components/common/detail-page-skeleton";
import { DataTable } from "@/components/common/data-table";
import { DataTableSkeleton } from "@/components/common/data-table-skeleton";
import { StatusBadge } from "@/components/common/status-badge";
import { Button, buttonVariants } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { cn } from "@/lib/utils";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Separator } from "@/components/ui/separator";
import { api } from "@/lib/api";
import type { PoliciesOverview, Source, SourceObject, SyncRun } from "@/lib/types";
import { useAuthStore } from "@/lib/stores/auth-store";
import { hasRole, canListPoliciesForSource } from "@/lib/roles";
import { RotateCredentialsDialog } from "@/components/sources/rotate-credentials-dialog";

const syncColumns: ColumnDef<SyncRun>[] = [
  { accessorKey: "run_type", header: "Type" },
  {
    accessorKey: "status",
    header: "Status",
    cell: ({ row }) => <StatusBadge status={row.getValue("status")} />,
  },
  {
    accessorKey: "items_processed",
    header: "Items",
  },
  {
    accessorKey: "started_at",
    header: "Started",
    cell: ({ row }) => {
      const v = row.getValue("started_at") as string | null;
      return v ? new Date(v).toLocaleString() : "—";
    },
  },
  {
    accessorKey: "completed_at",
    header: "Completed",
    cell: ({ row }) => {
      const v = row.getValue("completed_at") as string | null;
      return v ? new Date(v).toLocaleString() : "—";
    },
  },
];

const POLICY_NONE = "__none__";

export default function SourceDetailPage() {
  const params = useParams<{ id: string }>();
  const id = params.id;
  const qc = useQueryClient();
  const roles = useAuthStore((s) => s.roles);
  const canDiscover = hasRole(roles, "domain_admin");
  const policyListOk = canListPoliciesForSource(roles);

  /** `undefined` = follow server; set when user edits selects */
  const [draftRetention, setDraftRetention] = useState<string | null | undefined>(undefined);
  const [draftClassification, setDraftClassification] = useState<string | null | undefined>(
    undefined
  );

  const { data: source, isLoading, error } = useQuery({
    queryKey: ["source", id],
    queryFn: () => api.get<Source>(`/sources/${id}`),
    enabled: !!id,
  });

  const { data: policiesData } = useQuery({
    queryKey: ["policies"],
    queryFn: () => api.get<PoliciesOverview>("/policies"),
    enabled: !!id && policyListOk,
  });

  const { data: syncRuns = [] } = useQuery({
    queryKey: ["source-sync-runs", id],
    queryFn: () => api.get<SyncRun[]>(`/sources/${id}/sync-runs`),
    enabled: !!id,
    // Polled while a run is active: a backfill writes nothing until its
    // connector call returns, so without this the page cannot tell a working
    // sync from a hung one — which is also what makes the controls legible.
    refetchInterval: (query) =>
      (query.state.data ?? []).some((r: SyncRun) => r.status === "running") ? 5000 : false,
  });

  const discoverMut = useMutation({
    mutationFn: () => api.post<SourceObject[]>(`/sources/${id}/discover`),
    onSuccess: (objects) => {
      qc.invalidateQueries({ queryKey: ["source", id] });
      qc.invalidateQueries({ queryKey: ["source-objects", id] });
      toast.success(
        objects?.length
          ? `Discovery complete — ${objects.length} object${objects.length !== 1 ? "s" : ""} found.`
          : "Discovery complete — no objects found for this source."
      );
    },
    onError: (err: Error) =>
      toast.error(`Discovery failed: ${err.message || "Unknown error"}`),
  });

  const [rotateDialogOpen, setRotateDialogOpen] = useState(false);

  // A backfill lives inside one connector call for minutes at a time — the
  // live Zoho corpus measured 913 seconds — so the run list is polled while
  // one is active, and the controls act on the run rather than the schedule.
  const runningRun = (syncRuns ?? []).find((r) => r.status === "running") ?? null;

  const controlMut = useMutation({
    mutationFn: (action: "pause" | "resume" | "cancel") =>
      api.post(`/sources/${id}/sync/control`, { action }),
    onSuccess: (_data, action) => {
      qc.invalidateQueries({ queryKey: ["source-sync-runs", id] });
      qc.invalidateQueries({ queryKey: ["source-objects", id] });
      toast.success(
        action === "resume"
          ? "Sync resumed — the next run continues from the last checkpoint."
          : action === "pause"
            ? "Pause signalled. The running sync stops at its next checkpoint and keeps what it has already fetched."
            : "Cancel signalled. Records already fetched are kept; nothing restarts."
      );
    },
    onError: (err: Error) => toast.error(err.message || "Control failed"),
  });

  const srvRetention = source?.retention_policy_id ?? null;
  const srvClassification = source?.classification_policy_id ?? null;
  const retentionId = draftRetention !== undefined ? draftRetention : srvRetention;
  const classificationId =
    draftClassification !== undefined ? draftClassification : srvClassification;

  const patchPoliciesMut = useMutation({
    mutationFn: () =>
      api.patch<Source>(`/sources/${id}`, {
        retention_policy_id: retentionId,
        classification_policy_id: classificationId,
      }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["source", id] });
      setDraftRetention(undefined);
      setDraftClassification(undefined);
    },
  });

  const policyDirty =
    !!source &&
    (retentionId !== srvRetention || classificationId !== srvClassification);

  const showPolicyCard =
    policyListOk ||
    !!(source?.retention_policy_id || source?.classification_policy_id);

  if (!id) return null;

  if (isLoading) {
    return (
      <div className="space-y-6">
        <DetailPageSkeleton actionSlots={2}>
          <DetailCardGridSkeleton count={2} />
          <DetailWideCardSkeleton lines={5} />
        </DetailPageSkeleton>
        <div className="space-y-3">
          <Skeleton className="h-6 w-48" />
          <DataTableSkeleton columns={5} rows={4} />
        </div>
      </div>
    );
  }

  if (error || !source) {
    return (
      <div className="space-y-6">
        <PageHeader title="Source" description="Source was not found or you lack access." />
        <p className="text-destructive text-sm">{String((error as Error)?.message || "Not found")}</p>
        <Link href="/sources" className={cn(buttonVariants({ variant: "outline" }))}>
          Back to sources
        </Link>
      </div>
    );
  }

  return (
    <div className="space-y-6">
      <PageHeader
        title={source.display_name}
        description={`${source.source_type} · ${source.sync_mode} sync`}
        actions={
          <div className="flex flex-wrap gap-2">
            <Link
              href={`/inventory/${id}`}
              className={cn(buttonVariants({ variant: "outline" }))}
            >
              Discovery inventory
            </Link>
            {canDiscover && runningRun && (
              <>
                <Button
                  variant="outline"
                  disabled={controlMut.isPending}
                  onClick={() => controlMut.mutate("pause")}
                  title="Stops at the next checkpoint. Records already fetched are kept."
                >
                  <Pause className="mr-2 h-4 w-4" />
                  Pause sync
                </Button>
                <Button
                  variant="outline"
                  disabled={controlMut.isPending}
                  onClick={() => controlMut.mutate("cancel")}
                  title="Ends the run. What was already ingested stays ingested."
                >
                  <Square className="mr-2 h-4 w-4" />
                  Cancel sync
                </Button>
              </>
            )}
            {canDiscover && !runningRun && (
              <Button
                variant="outline"
                disabled={controlMut.isPending}
                onClick={() => controlMut.mutate("resume")}
                title="Clears a pause so the next run continues from the checkpoint."
              >
                <Play className="mr-2 h-4 w-4" />
                Resume sync
              </Button>
            )}
            {canDiscover && (
              <Button
                variant="outline"
                onClick={() => setRotateDialogOpen(true)}
              >
                <KeyRound className="mr-2 h-4 w-4" />
                Rotate credentials
              </Button>
            )}
            {canDiscover && (
              <Button
                disabled={discoverMut.isPending}
                onClick={() => discoverMut.mutate()}
              >
                {discoverMut.isPending ? (
                  <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                ) : (
                  <RefreshCw className="mr-2 h-4 w-4" />
                )}
                Run discovery
              </Button>
            )}
          </div>
        }
      />

      <div className="grid gap-4 md:grid-cols-2">
        <Card>
          <CardHeader>
            <CardTitle className="text-base">Status</CardTitle>
          </CardHeader>
          <CardContent className="space-y-3 text-sm">
            <div className="flex flex-wrap gap-2">
              <StatusBadge status={source.auth_status} />
              <StatusBadge status={source.discovery_status} />
            </div>
            <Separator />
            <div>
              <span className="text-muted-foreground">Active</span>{" "}
              <span className="font-medium">{source.is_active ? "Yes" : "No"}</span>
            </div>
            <div>
              <span className="text-muted-foreground">Auth type</span>{" "}
              <span className="font-medium">{source.auth_type}</span>
            </div>
            {source.purpose && (
              <div>
                <span className="text-muted-foreground">Purpose</span>
                <p className="mt-1">{source.purpose}</p>
              </div>
            )}
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle className="text-base">Scope</CardTitle>
          </CardHeader>
          <CardContent className="space-y-2 text-sm">
            <div>
              <span className="text-muted-foreground">Workspace</span>{" "}
              <span className="font-mono text-xs">{source.workspace_id ?? "—"}</span>
            </div>
            <div>
              <span className="text-muted-foreground">Domain IDs</span>
              <p className="mt-1 font-mono text-xs break-all">
                {source.domain_ids?.length ? source.domain_ids.join(", ") : "—"}
              </p>
            </div>
          </CardContent>
        </Card>
      </div>

      {showPolicyCard && source && (
        <Card>
          <CardHeader>
            <CardTitle className="text-base">Policies</CardTitle>
            <p className="text-sm text-muted-foreground">
              Attach tenant retention and classification policies to this source. Create policies under{" "}
              <Link href="/policies" className="text-primary underline-offset-4 hover:underline">
                Policies
              </Link>
              .
            </p>
          </CardHeader>
          <CardContent className="space-y-4">
            {!policyListOk ? (
              <div className="space-y-2 text-sm">
                <p className="text-muted-foreground">
                  Tenant or domain admin role is required to load policy names. Current assignment IDs:
                </p>
                <div>
                  <span className="text-muted-foreground">Retention</span>{" "}
                  <span className="font-mono text-xs">
                    {source.retention_policy_id ?? "—"}
                  </span>
                </div>
                <div>
                  <span className="text-muted-foreground">Classification</span>{" "}
                  <span className="font-mono text-xs">
                    {source.classification_policy_id ?? "—"}
                  </span>
                </div>
              </div>
            ) : (
              <>
                <div className="space-y-2">
                  <Label htmlFor="retention-policy">Retention policy</Label>
                  <Select
                    value={retentionId ?? POLICY_NONE}
                    onValueChange={(v) =>
                      setDraftRetention(v === POLICY_NONE ? null : (v ?? null))
                    }
                    disabled={!canDiscover}
                  >
                    <SelectTrigger id="retention-policy" className="w-full max-w-md">
                      <SelectValue placeholder="None" />
                    </SelectTrigger>
                    <SelectContent>
                      <SelectItem value={POLICY_NONE}>None</SelectItem>
                      {(policiesData?.retention_policies ?? []).map((p) => (
                        <SelectItem key={p.id} value={p.id}>
                          {p.name}
                          {!p.is_active ? " (inactive)" : ""}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                </div>
                <div className="space-y-2">
                  <Label htmlFor="classification-policy">Classification policy</Label>
                  <Select
                    value={classificationId ?? POLICY_NONE}
                    onValueChange={(v) =>
                      setDraftClassification(v === POLICY_NONE ? null : (v ?? null))
                    }
                    disabled={!canDiscover}
                  >
                    <SelectTrigger id="classification-policy" className="w-full max-w-md">
                      <SelectValue placeholder="None" />
                    </SelectTrigger>
                    <SelectContent>
                      <SelectItem value={POLICY_NONE}>None</SelectItem>
                      {(policiesData?.classification_policies ?? []).map((p) => (
                        <SelectItem key={p.id} value={p.id}>
                          {p.name}
                          {!p.is_active ? " (inactive)" : ""}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                </div>
                {!canDiscover && (
                  <p className="text-sm text-muted-foreground">
                    Domain admin role is required to change policy assignment.
                  </p>
                )}
                {canDiscover && (
                  <div className="flex flex-col gap-2 sm:flex-row sm:items-center">
                    <Button
                      type="button"
                      size="sm"
                      disabled={!policyDirty || patchPoliciesMut.isPending}
                      onClick={() => patchPoliciesMut.mutate()}
                    >
                      {patchPoliciesMut.isPending ? "Saving…" : "Save policy assignment"}
                    </Button>
                    {patchPoliciesMut.isError && (
                      <p className="text-sm text-destructive">
                        {(patchPoliciesMut.error as Error).message}
                      </p>
                    )}
                  </div>
                )}
              </>
            )}
          </CardContent>
        </Card>
      )}

      <Card>
        <CardHeader>
          <CardTitle className="text-base">Configuration</CardTitle>
        </CardHeader>
        <CardContent>
          <pre className="max-h-64 overflow-auto rounded-md bg-muted p-3 text-xs">
            {JSON.stringify(source.config ?? {}, null, 2)}
          </pre>
        </CardContent>
      </Card>

      <div className="space-y-3">
        <h3 className="text-lg font-semibold">Recent sync runs</h3>
        {syncRuns.length === 0 ? (
          <p className="text-sm text-muted-foreground">No sync runs recorded yet.</p>
        ) : (
          <DataTable columns={syncColumns} data={syncRuns} />
        )}
      </div>

      <RotateCredentialsDialog
        source={source}
        open={rotateDialogOpen}
        onOpenChange={setRotateDialogOpen}
      />
    </div>
  );
}
