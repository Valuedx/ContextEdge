"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ColumnDef } from "@tanstack/react-table";
import { Trash2 } from "lucide-react";
import { toast } from "sonner";

import { PageHeader } from "@/components/common/page-header";
import { DataTable } from "@/components/common/data-table";
import { DataTableSkeleton } from "@/components/common/data-table-skeleton";
import { StatusBadge } from "@/components/common/status-badge";
import { api } from "@/lib/api";
import type { SyncRun } from "@/lib/types";
import { Button } from "@/components/ui/button";

const columns: ColumnDef<SyncRun>[] = [
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
  const { data = [], isLoading } = useQuery<SyncRun[]>({
    queryKey: ["sync-runs"],
    queryFn: () => api.get("/sync-runs"),
  });

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
        <DataTableSkeleton columns={5} />
      ) : (
        <DataTable columns={columns} data={data} />
      )}
    </div>
  );
}
