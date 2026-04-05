"use client";

import { useQuery } from "@tanstack/react-query";
import { ColumnDef } from "@tanstack/react-table";
import { Plus } from "lucide-react";

import { PageHeader } from "@/components/common/page-header";
import { DataTable } from "@/components/common/data-table";
import { DataTableSkeleton } from "@/components/common/data-table-skeleton";
import { StatusBadge } from "@/components/common/status-badge";
import { Button } from "@/components/ui/button";
import { api } from "@/lib/api";
import type { Source } from "@/lib/types";
import Link from "next/link";

const columns: ColumnDef<Source>[] = [
  {
    accessorKey: "display_name",
    header: "Name",
    cell: ({ row }) => (
      <Link href={`/sources/${row.original.id}`} className="font-medium text-primary hover:underline">
        {row.getValue("display_name")}
      </Link>
    ),
  },
  { accessorKey: "source_type", header: "Type" },
  {
    accessorKey: "auth_status",
    header: "Auth",
    cell: ({ row }) => <StatusBadge status={row.getValue("auth_status")} />,
  },
  {
    accessorKey: "discovery_status",
    header: "Discovery",
    cell: ({ row }) => <StatusBadge status={row.getValue("discovery_status")} />,
  },
  { accessorKey: "sync_mode", header: "Sync Mode" },
  {
    accessorKey: "created_at",
    header: "Created",
    cell: ({ row }) => new Date(row.getValue("created_at")).toLocaleDateString(),
  },
];

export default function SourcesPage() {
  const { data = [], isLoading } = useQuery<Source[]>({
    queryKey: ["sources"],
    queryFn: () => api.get("/sources"),
  });

  return (
    <div className="space-y-6">
      <PageHeader
        title="Sources"
        description="Manage connected data sources and their ingestion configuration."
        actions={
          <Button>
            <Plus className="mr-2 h-4 w-4" />
            Add Source
          </Button>
        }
      />
      {isLoading ? (
        <DataTableSkeleton columns={6} />
      ) : data.length === 0 ? (
        <div className="rounded-md border p-12 text-center text-muted-foreground">
          No sources configured yet. Add your first source to begin ingestion.
        </div>
      ) : (
        <DataTable columns={columns} data={data} />
      )}
    </div>
  );
}
