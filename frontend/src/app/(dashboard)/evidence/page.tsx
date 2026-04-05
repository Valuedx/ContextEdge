"use client";

import { useQuery } from "@tanstack/react-query";
import { ColumnDef } from "@tanstack/react-table";

import { PageHeader } from "@/components/common/page-header";
import { DataTable } from "@/components/common/data-table";
import { DataTableSkeleton } from "@/components/common/data-table-skeleton";
import { StatusBadge } from "@/components/common/status-badge";
import { api } from "@/lib/api";
import type { EvidenceItem } from "@/lib/types";
import Link from "next/link";

const columns: ColumnDef<EvidenceItem>[] = [
  {
    accessorKey: "title",
    header: "Title",
    cell: ({ row }) => (
      <Link href={`/evidence/${row.original.id}`} className="font-medium text-primary hover:underline">
        {row.getValue("title") || "Untitled"}
      </Link>
    ),
  },
  { accessorKey: "evidence_type", header: "Type" },
  {
    accessorKey: "relevance_state",
    header: "Relevance",
    cell: ({ row }) => <StatusBadge status={row.getValue("relevance_state")} />,
  },
  {
    accessorKey: "relevance_score",
    header: "Score",
    cell: ({ row }) => {
      const val = row.getValue("relevance_score") as number | null;
      return val !== null ? (val * 100).toFixed(0) + "%" : "—";
    },
  },
  {
    accessorKey: "ingested_at",
    header: "Ingested",
    cell: ({ row }) => new Date(row.getValue("ingested_at")).toLocaleString(),
  },
];

export default function EvidencePage() {
  const { data = [], isLoading } = useQuery<EvidenceItem[]>({
    queryKey: ["evidence"],
    queryFn: () => api.get("/evidence"),
  });

  return (
    <div className="space-y-6">
      <PageHeader title="Evidence Explorer" description="Search and browse operational evidence across all sources." />
      {isLoading ? (
        <DataTableSkeleton columns={5} />
      ) : (
        <DataTable columns={columns} data={data} />
      )}
    </div>
  );
}
