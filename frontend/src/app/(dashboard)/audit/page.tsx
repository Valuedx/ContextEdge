"use client";

import { PageHeader } from "@/components/common/page-header";
import { useQuery } from "@tanstack/react-query";
import { api } from "@/lib/api";
import type { AuditLog } from "@/lib/types";
import { DataTable } from "@/components/common/data-table";
import { DataTableSkeleton } from "@/components/common/data-table-skeleton";
import { ColumnDef } from "@tanstack/react-table";

const columns: ColumnDef<AuditLog>[] = [
  { accessorKey: "timestamp", header: "Time", cell: ({ row }) => new Date(row.getValue("timestamp")).toLocaleString() },
  { accessorKey: "action", header: "Action" },
  { accessorKey: "actor_email", header: "Actor" },
  { accessorKey: "resource_type", header: "Resource Type" },
  { accessorKey: "resource_id", header: "Resource ID" },
];

export default function AuditPage() {
  const { data = [], isLoading } = useQuery<AuditLog[]>({
    queryKey: ["audit-logs"],
    queryFn: () => api.get("/audit-logs"),
  });

  return (
    <div className="space-y-6">
      <PageHeader title="Audit Log" description="Track admin, reviewer, retrieval, and policy actions." />
      {isLoading ? (
        <DataTableSkeleton columns={5} />
      ) : (
        <DataTable columns={columns} data={data} />
      )}
    </div>
  );
}
