"use client";

import { useQuery } from "@tanstack/react-query";
import { ColumnDef } from "@tanstack/react-table";

import { PageHeader } from "@/components/common/page-header";
import { DataTable } from "@/components/common/data-table";
import { DataTableSkeleton } from "@/components/common/data-table-skeleton";
import { StatusBadge } from "@/components/common/status-badge";
import { api } from "@/lib/api";
import type { SyncRun } from "@/lib/types";

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
];

export default function SyncPage() {
  const { data = [], isLoading } = useQuery<SyncRun[]>({
    queryKey: ["sync-runs"],
    queryFn: () => api.get("/sync-runs"),
  });

  return (
    <div className="space-y-6">
      <PageHeader
        title="Sync Operations"
        description="Monitor sync jobs, backfills, retries, and dead-letter items."
      />
      {isLoading ? (
        <DataTableSkeleton columns={5} />
      ) : (
        <DataTable columns={columns} data={data} />
      )}
    </div>
  );
}
