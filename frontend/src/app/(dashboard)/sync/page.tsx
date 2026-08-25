"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ColumnDef } from "@tanstack/react-table";
import { Loader2, Trash2 } from "lucide-react";
import { toast } from "sonner";
import { useState } from "react";

import { PageHeader } from "@/components/common/page-header";
import { DataTable } from "@/components/common/data-table";
import { DataTableSkeleton } from "@/components/common/data-table-skeleton";
import { StatusBadge } from "@/components/common/status-badge";
import { api } from "@/lib/api";
import type { Source, SyncRun } from "@/lib/types";
import { Button } from "@/components/ui/button";
import { ConfirmActionDialog } from "@/components/common/confirm-action-dialog";

type SyncRunRow = SyncRun & {
  source_name: string;
  source_type: string | null;
};

const columns: ColumnDef<SyncRunRow>[] = [
  {
    accessorKey: "source_name",
    header: "Source",
    cell: ({ row }) => (
      <div>
        <div className="font-medium">{row.original.source_name}</div>
        {row.original.source_type && (
          <div className="text-xs text-muted-foreground">
            {row.original.source_type}
          </div>
        )}
      </div>
    ),
  },
  { accessorKey: "run_type", header: "Type" },
  {
    accessorKey: "status",
    header: "Status",
    cell: ({ row }) => <StatusBadge status={row.getValue("status")} />,
  },
  { accessorKey: "items_processed", header: "Items" },
  {
    accessorKey: "started_at",
    header: "Started",
    cell: ({ row }) => {
      const val = row.getValue("started_at");
      return val ? new Date(val as string).toLocaleString() : "—";
    },
  },
  {
    accessorKey: "completed_at",
    header: "Completed",
    cell: ({ row }) => {
      const val = row.getValue("completed_at");
      return val ? new Date(val as string).toLocaleString() : "—";
    },
  },
  {
    id: "actions",
    cell: ({ row }) => (
      <div className="flex justify-end">
        <SyncAction runId={row.original.id} />
      </div>
    ),
  },
];

function SyncAction({ runId }: { runId: string }) {
  const queryClient = useQueryClient();
  const [deleteOpen, setDeleteOpen] = useState(false);
  const deleteMutation = useMutation({
    mutationFn: () => api.delete(`/sync-runs/${runId}`),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["sync-runs"] });
      toast.success("Sync log deleted");
      setDeleteOpen(false);
    },
    onError: (err: Error) => toast.error(err.message || "Delete failed"),
  });

  return (
    <>
      <Button
        variant="ghost"
        size="icon"
        className="text-muted-foreground hover:text-destructive"
        title="Delete sync log"
        aria-label="Delete sync log"
        onClick={() => setDeleteOpen(true)}
        disabled={deleteMutation.isPending}
      >
        {deleteMutation.isPending ? (
          <Loader2 className="h-4 w-4 animate-spin" />
        ) : (
          <Trash2 className="h-4 w-4" />
        )}
      </Button>
      <ConfirmActionDialog
        open={deleteOpen}
        onOpenChange={setDeleteOpen}
        title="Delete sync log?"
        description="This removes the selected sync run log from history."
        confirmLabel="Delete log"
        isPending={deleteMutation.isPending}
        onConfirm={() => deleteMutation.mutate()}
      />
    </>
  );
}

export default function SyncPage() {
  const queryClient = useQueryClient();
  const [clearHistoryOpen, setClearHistoryOpen] = useState(false);
  const { data: syncRuns = [], isLoading: runsLoading } = useQuery<SyncRun[]>({
    queryKey: ["sync-runs"],
    queryFn: () => api.get("/sync-runs"),
  });
  const purgeMutation = useMutation({
    mutationFn: () => api.delete("/sync-runs/purge"),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["sync-runs"] });
      toast.success("Sync history cleared");
      setClearHistoryOpen(false);
    },
    onError: (err: Error) => toast.error(err.message || "Clear history failed"),
  });
  const { data: sources = [], isLoading: sourcesLoading } = useQuery<Source[]>({
    queryKey: ["sources", "sync-page"],
    queryFn: () => api.get("/sources", { limit: "200" }),
  });

  const sourceById = new Map(sources.map((source) => [source.id, source]));
  const rows: SyncRunRow[] = syncRuns.map((run) => {
    const source = sourceById.get(run.source_id);
    return {
      ...run,
      source_name: source?.display_name ?? run.source_id.slice(0, 8) + "...",
      source_type: source?.source_type ?? null,
    };
  });
  const isLoading = runsLoading || sourcesLoading;

  return (
    <div className="space-y-6">
      <PageHeader
        title="Sync Operations"
        description="Monitor sync jobs, backfills, retries, and dead-letter items."
        actions={
            <Button
              variant="destructive"
              onClick={() => setClearHistoryOpen(true)}
              disabled={purgeMutation.isPending}
            >
              {purgeMutation.isPending && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
              Clear History
            </Button>
          }
      />
      <ConfirmActionDialog
        open={clearHistoryOpen}
        onOpenChange={setClearHistoryOpen}
        title="Clear sync history?"
        description="This deletes all sync operation logs. Source configuration and ingested evidence are not deleted."
        confirmLabel="Clear history"
        isPending={purgeMutation.isPending}
        onConfirm={() => purgeMutation.mutate()}
      />
      {isLoading ? (
        <DataTableSkeleton columns={6} />
      ) : (
        <DataTable columns={columns} data={rows} />
      )}
    </div>
  );
}
