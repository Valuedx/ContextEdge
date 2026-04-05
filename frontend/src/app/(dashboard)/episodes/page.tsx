"use client";

import { useQuery } from "@tanstack/react-query";
import { ColumnDef } from "@tanstack/react-table";

import { PageHeader } from "@/components/common/page-header";
import { DataTable } from "@/components/common/data-table";
import { DataTableSkeleton } from "@/components/common/data-table-skeleton";
import { StatusBadge } from "@/components/common/status-badge";
import { api } from "@/lib/api";
import type { Episode } from "@/lib/types";
import Link from "next/link";

const columns: ColumnDef<Episode>[] = [
  {
    accessorKey: "title",
    header: "Title",
    cell: ({ row }) => (
      <Link href={`/episodes/${row.original.id}`} className="font-medium text-primary hover:underline">
        {row.getValue("title")}
      </Link>
    ),
  },
  {
    accessorKey: "status",
    header: "Status",
    cell: ({ row }) => <StatusBadge status={row.getValue("status")} />,
  },
  {
    accessorKey: "extraction_confidence",
    header: "Confidence",
    cell: ({ row }) => ((row.getValue("extraction_confidence") as number) * 100).toFixed(0) + "%",
  },
  {
    accessorKey: "reviewer_state",
    header: "Review",
    cell: ({ row }) => <StatusBadge status={row.getValue("reviewer_state")} />,
  },
  {
    accessorKey: "created_at",
    header: "Created",
    cell: ({ row }) => new Date(row.getValue("created_at")).toLocaleDateString(),
  },
];

export default function EpisodesPage() {
  const { data = [], isLoading } = useQuery<Episode[]>({
    queryKey: ["episodes"],
    queryFn: () => api.get("/episodes"),
  });

  return (
    <div className="space-y-6">
      <PageHeader title="Episodes" description="Reconstructed troubleshooting episodes from correlated evidence." />
      {isLoading ? (
        <DataTableSkeleton columns={5} />
      ) : (
        <DataTable columns={columns} data={data} />
      )}
    </div>
  );
}
