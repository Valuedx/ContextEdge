"use client";

import { useQuery, useQueryClient } from "@tanstack/react-query";
import { ColumnDef } from "@tanstack/react-table";
import { Trash2 } from "lucide-react";
import { toast } from "sonner";

import { PageHeader } from "@/components/common/page-header";
import { DataTable } from "@/components/common/data-table";
import { DataTableSkeleton } from "@/components/common/data-table-skeleton";
import { StatusBadge } from "@/components/common/status-badge";
import { api } from "@/lib/api";
import type { Source, SyncRun } from "@/lib/types";
import { Button } from "@/components/ui/button";

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
  return (
    <Button
      variant="ghost"
      size="icon"
      className="text-muted-foreground hover:text-destructive"
      onClick={() => {
        if (confirm("Delete this sync run log?")) {
          api.delete(`/sync-runs/${runId}`).then(() => {
            queryClient.invalidateQueries({ queryKey: ["sync-runs"] });
            toast.success("Sync log deleted");
          }).catch(err => toast.error(err.message));
        }
      }}
    >
      <Trash2 className="h-4 w-4" />
    </Button>
  );
}

export default function SyncPage() {
  const queryClient = useQueryClient();
  const { data: syncRuns = [], isLoading: runsLoading } = useQuery<SyncRun[]>({
    queryKey: ["sync-runs"],
    queryFn: () => api.get("/sync-runs"),
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
              onClick={() => {
                if (confirm("Delete ALL sync operation logs?")) {
                  api.delete("/sync-runs/purge").then(() => {
                    queryClient.invalidateQueries({ queryKey: ["sync-runs"] });
                    toast.success("Sync history cleared");
                  }).catch(err => toast.error(err.message));
                }
              }}
            >
              Clear History
            </Button>
          }
      />
      {isLoading ? (
        <DataTableSkeleton columns={6} />
      ) : (
        <DataTable columns={columns} data={rows} />
      )}
    </div>
  );
}
