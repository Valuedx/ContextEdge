"use client";

import { useQuery } from "@tanstack/react-query";
import { ColumnDef } from "@tanstack/react-table";

import { PageHeader } from "@/components/common/page-header";
import { DataTable } from "@/components/common/data-table";
import { DataTableSkeleton } from "@/components/common/data-table-skeleton";
import { api } from "@/lib/api";
import type { Pattern } from "@/lib/types";
import Link from "next/link";

const columns: ColumnDef<Pattern>[] = [
  {
    accessorKey: "title",
    header: "Title",
    cell: ({ row }) => (
      <Link href={`/patterns/${row.original.id}`} className="font-medium text-primary hover:underline">
        {row.getValue("title")}
      </Link>
    ),
  },
  { accessorKey: "pattern_type", header: "Type" },
  { accessorKey: "episode_count", header: "Episodes" },
  {
    accessorKey: "confidence",
    header: "Confidence",
    cell: ({ row }) => ((row.getValue("confidence") as number) * 100).toFixed(0) + "%",
  },
  {
    accessorKey: "contradiction_score",
    header: "Contradictions",
    cell: ({ row }) => ((row.getValue("contradiction_score") as number) * 100).toFixed(0) + "%",
  },
  {
    accessorKey: "freshness_score",
    header: "Freshness",
    cell: ({ row }) => ((row.getValue("freshness_score") as number) * 100).toFixed(0) + "%",
  },
];

export default function PatternsPage() {
  const { data = [], isLoading } = useQuery<Pattern[]>({
    queryKey: ["patterns"],
    queryFn: () => api.get("/patterns"),
  });

  return (
    <div className="space-y-6">
      <PageHeader title="Patterns" description="Operational patterns derived from episode clusters." />
      {isLoading ? (
        <DataTableSkeleton columns={6} />
      ) : (
        <DataTable columns={columns} data={data} />
      )}
    </div>
  );
}
