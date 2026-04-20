"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ColumnDef } from "@tanstack/react-table";
import Link from "next/link";
import { useParams } from "next/navigation";
import { Loader2, RefreshCw, AlertTriangle, SearchX, CheckCircle2, History } from "lucide-react";
import { toast } from "sonner";
import { useState } from "react";

import { PageHeader } from "@/components/common/page-header";
import { DataTable } from "@/components/common/data-table";
import { DataTableSkeleton } from "@/components/common/data-table-skeleton";
import { StatusBadge } from "@/components/common/status-badge";
import { Button, buttonVariants } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { cn } from "@/lib/utils";
import { api } from "@/lib/api";
import type { Source, SourceObject } from "@/lib/types";
import { useAuthStore } from "@/lib/stores/auth-store";
import { isTenantAdmin, canDiscoverSources } from "@/lib/roles";

export default function DiscoveryPage() {
  const params = useParams<{ id: string }>();
  const sourceId = params.id;
  const qc = useQueryClient();
  const roles = useAuthStore((s) => s.roles);
  const canApprove = isTenantAdmin(roles);
  const canDiscover = canDiscoverSources(roles);

  // Fetch the parent source so we can show discovery_status and auth_status
  const { data: source } = useQuery({
    queryKey: ["source", sourceId],
    queryFn: () => api.get<Source>(`/sources/${sourceId}`),
    enabled: !!sourceId,
  });

  const { data: objects = [], isLoading } = useQuery({
    queryKey: ["source-objects", sourceId],
    queryFn: () => api.get<SourceObject[]>(`/sources/${sourceId}/objects`),
    enabled: !!sourceId,
  });

  const discoverMut = useMutation({
    mutationFn: () => api.post<SourceObject[]>(`/sources/${sourceId}/discover`),
    onSuccess: (result) => {
      qc.invalidateQueries({ queryKey: ["source", sourceId] });
      qc.invalidateQueries({ queryKey: ["source-objects", sourceId] });
      toast.success(
        result?.length
          ? `Discovery complete — ${result.length} object${result.length !== 1 ? "s" : ""} found.`
          : "Discovery ran successfully but no objects were found for this source."
      );
    },
    onError: (err: Error) =>
      toast.error(`Discovery failed: ${err.message || "Unknown error"}`),
  });

  const toggleMut = useMutation({
    mutationFn: ({
      objectId,
      body,
    }: {
      objectId: string;
      body: { approved_for_sync?: boolean; approved_for_backfill?: boolean };
    }) => api.patch(`/sources/${sourceId}/objects/${objectId}`, body),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["source-objects", sourceId] }),
  });

  const [backfillDialogOpen, setBackfillDialogOpen] = useState(false);
  const [windowDays, setWindowDays] = useState(90);

  const backfillMut = useMutation({
    mutationFn: (objectIds: string[]) => 
      api.post(`/sources/${sourceId}/backfill`, {
        source_object_ids: objectIds,
        window_days: windowDays,
      }),
    onSuccess: (res: any) => {
      toast.success(`Backfill started for ${res.object_count} objects.`);
      setBackfillDialogOpen(false);
    },
    onError: (err: Error) => toast.error(`Failed to start backfill: ${err.message}`),
  });

  const handleStartBackfill = () => {
    const approvedIds = objects
      .filter((o) => o.approved_for_backfill)
      .map((o) => o.id);
    
    if (approvedIds.length === 0) {
      toast.error("No objects are approved for backfill. Please approve at least one mailbox first.");
      return;
    }
    
    backfillMut.mutate(approvedIds);
  };

  const columns: ColumnDef<SourceObject>[] = [
    { accessorKey: "object_type", header: "Type" },
    {
      accessorKey: "display_name",
      header: "Name",
      cell: ({ row }) => (
        <div>
          <div className="font-medium">{row.getValue("display_name")}</div>
          <div className="text-xs text-muted-foreground font-mono">{row.original.external_id}</div>
        </div>
      ),
    },
    {
      accessorKey: "approved_for_sync",
      header: "Sync",
      cell: ({ row }) => {
        const o = row.original;
        if (!canApprove) {
          return o.approved_for_sync ? "Yes" : "No";
        }
        return (
          <Button
            variant="outline"
            size="sm"
            disabled={toggleMut.isPending}
            onClick={() =>
              toggleMut.mutate({
                objectId: o.id,
                body: { approved_for_sync: !o.approved_for_sync },
              })
            }
          >
            {o.approved_for_sync ? "Revoke sync" : "Approve sync"}
          </Button>
        );
      },
    },
    {
      accessorKey: "approved_for_backfill",
      header: "Backfill",
      cell: ({ row }) => {
        const o = row.original;
        if (!canApprove) {
          return o.approved_for_backfill ? "Yes" : "No";
        }
        return (
          <Button
            variant="outline"
            size="sm"
            disabled={toggleMut.isPending}
            onClick={() =>
              toggleMut.mutate({
                objectId: o.id,
                body: { approved_for_backfill: !o.approved_for_backfill },
              })
            }
          >
            {o.approved_for_backfill ? "Revoke backfill" : "Approve backfill"}
          </Button>
        );
      },
    },
    {
      accessorKey: "last_successful_sync_at",
      header: "Last sync",
      cell: ({ row }) => {
        const v = row.getValue("last_successful_sync_at") as string | null;
        return v ? new Date(v).toLocaleString() : "—";
      },
    },
  ];

  if (!sourceId) return null;

  // ── Empty state helper ────────────────────────────────────────────────────
  const renderEmptyState = () => {
    const discoveryStatus = source?.discovery_status;
    const authStatus = source?.auth_status;

    // Auth is broken — can't discover
    if (authStatus === "failed") {
      return (
        <div className="flex flex-col items-center gap-4 rounded-xl border border-destructive/30 bg-destructive/5 px-8 py-10 text-center">
          <AlertTriangle className="h-10 w-10 text-destructive" />
          <div>
            <p className="text-sm font-semibold text-destructive">
              Source authentication failed
            </p>
            <p className="mt-1 text-sm text-muted-foreground">
              The source credentials are invalid or have expired. Rotate credentials
              on the source detail page, then run discovery again.
            </p>
          </div>
          <Link
            href={`/sources/${sourceId}`}
            className={cn(buttonVariants({ variant: "outline", size: "sm" }))}
          >
            Go to source settings
          </Link>
        </div>
      );
    }

    // Discovery ran but connector returned 0 objects
    if (discoveryStatus === "completed") {
      return (
        <div className="flex flex-col items-center gap-4 rounded-xl border border-border bg-muted/40 px-8 py-10 text-center">
          <CheckCircle2 className="h-10 w-10 text-muted-foreground" />
          <div>
            <p className="text-sm font-semibold">Discovery already ran — no objects found</p>
            <p className="mt-1 text-sm text-muted-foreground">
              The connector did not return any channels, mailboxes, or resources.
              Check the source configuration and run discovery again.
            </p>
          </div>
          {canDiscover && (
            <Button
              size="sm"
              disabled={discoverMut.isPending}
              onClick={() => discoverMut.mutate()}
            >
              {discoverMut.isPending ? (
                <Loader2 className="mr-2 h-4 w-4 animate-spin" />
              ) : (
                <RefreshCw className="mr-2 h-4 w-4" />
              )}
              Run discovery again
            </Button>
          )}
        </div>
      );
    }

    // Not yet run — prompt the user to kick it off
    return (
      <div className="flex flex-col items-center gap-4 rounded-xl border border-border bg-muted/40 px-8 py-10 text-center">
        <SearchX className="h-10 w-10 text-muted-foreground" />
        <div>
          <p className="text-sm font-semibold">No discovered objects yet</p>
          <p className="mt-1 text-sm text-muted-foreground">
            Run discovery to let ContextEdge enumerate channels, mailboxes, or
            resources available in this source.
          </p>
        </div>
        {canDiscover ? (
          <Button
            disabled={discoverMut.isPending}
            onClick={() => discoverMut.mutate()}
          >
            {discoverMut.isPending ? (
              <Loader2 className="mr-2 h-4 w-4 animate-spin" />
            ) : (
              <RefreshCw className="mr-2 h-4 w-4" />
            )}
            {discoverMut.isPending ? "Running discovery…" : "Run discovery"}
          </Button>
        ) : (
          <p className="text-xs text-muted-foreground">
            Domain admin role required to run discovery.
          </p>
        )}
      </div>
    );
  };

  return (
    <div className="space-y-6">
      <PageHeader
        title="Discovery inventory"
        description="Discovered objects for this source. Approve channels or mailboxes for sync and backfill."
        actions={
          <div className="flex flex-wrap gap-2">
            {/* Backfill Trigger */}
            {objects.some((o) => o.approved_for_backfill) && canApprove && (
              <Button
                variant="default"
                size="sm"
                className="bg-indigo-600 hover:bg-indigo-700 text-white"
                onClick={() => setBackfillDialogOpen(true)}
              >
                <History className="mr-2 h-4 w-4" />
                Start backfill
              </Button>
            )}

            {/* Re-run discovery from the inventory page if objects already exist */}
            {objects.length > 0 && canDiscover && (
              <Button
                variant="outline"
                size="sm"
                disabled={discoverMut.isPending}
                onClick={() => discoverMut.mutate()}
              >
                {discoverMut.isPending ? (
                  <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                ) : (
                  <RefreshCw className="mr-2 h-4 w-4" />
                )}
                Refresh
              </Button>
            )}
            <Link
              href={`/sources/${sourceId}`}
              className={cn(buttonVariants({ variant: "outline" }))}
            >
              Back to source
            </Link>
          </div>
        }
      />

      {/* Source status pills */}
      {source && (
        <div className="flex flex-wrap gap-2">
          <StatusBadge status={source.auth_status} />
          <StatusBadge status={source.discovery_status} />
        </div>
      )}

      {!canApprove && (
        <p className="text-sm text-muted-foreground">
          Tenant admin role is required to change approval flags.
        </p>
      )}

      {isLoading ? (
        <DataTableSkeleton columns={5} />
      ) : objects.length === 0 ? (
        renderEmptyState()
      ) : (
        <DataTable columns={columns} data={objects} />
      )}

      {/* Backfill Config Dialog */}
      <Dialog open={backfillDialogOpen} onOpenChange={setBackfillDialogOpen}>
        <DialogContent className="sm:max-w-[425px]">
          <DialogHeader>
            <DialogTitle>Configure Backfill</DialogTitle>
            <DialogDescription>
              Choose how many days of history you want to fetch from the approved mailboxes.
            </DialogDescription>
          </DialogHeader>
          <div className="grid gap-4 py-4">
            <div className="grid grid-cols-4 items-center gap-4">
              <Label htmlFor="window" className="text-right">
                History
              </Label>
              <div className="col-span-3 flex items-center gap-2">
                <Input
                  id="window"
                  type="number"
                  value={windowDays}
                  onChange={(e) => setWindowDays(parseInt(e.target.value) || 0)}
                  className="w-20"
                />
                <span className="text-sm text-muted-foreground">days</span>
              </div>
            </div>
            <p className="text-xs text-muted-foreground">
              Note: Larger windows will take longer to process and may be subject to API rate limits.
            </p>
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setBackfillDialogOpen(false)}>
              Cancel
            </Button>
            <Button 
                onClick={handleStartBackfill} 
                disabled={backfillMut.isPending}
                className="bg-indigo-600 hover:bg-indigo-700"
            >
              {backfillMut.isPending ? (
                <Loader2 className="mr-2 h-4 w-4 animate-spin" />
              ) : (
                <History className="mr-2 h-4 w-4" />
              )}
              {backfillMut.isPending ? "Starting…" : "Start Now"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
