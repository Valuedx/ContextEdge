"use client";

import { useQuery } from "@tanstack/react-query";
import { usePagination } from "@/lib/hooks/use-pagination";
import { PaginationControls } from "@/components/common/pagination-controls";
import { ColumnDef } from "@tanstack/react-table";
import { Plus } from "lucide-react";

import { PageHeader } from "@/components/common/page-header";
import { DataTable } from "@/components/common/data-table";
import { DataTableSkeleton } from "@/components/common/data-table-skeleton";
import { StatusBadge } from "@/components/common/status-badge";
import { buttonVariants } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import { api } from "@/lib/api";
import type { Playbook } from "@/lib/types";
import Link from "next/link";

const columns: ColumnDef<Playbook>[] = [
  {
    accessorKey: "title",
    header: "Title",
    cell: ({ row }) => (
      <Link href={`/playbooks/${row.original.id}`} className="font-medium text-primary hover:underline">
        {row.getValue("title")}
      </Link>
    ),
  },
  {
    accessorKey: "lifecycle_state",
    header: "State",
    cell: ({ row }) => <StatusBadge status={row.getValue("lifecycle_state")} />,
  },
  { accessorKey: "risk_tier", header: "Risk" },
  { accessorKey: "automation_mode", header: "Automation" },
  {
    accessorKey: "last_validated_at",
    header: "Validated",
    cell: ({ row }) => {
      const val = row.getValue("last_validated_at");
      return val ? new Date(val as string).toLocaleString() : "Never";
    },
  },
  {
    accessorKey: "updated_at",
    header: "Updated",
    cell: ({ row }) => new Date(row.getValue("updated_at")).toLocaleString(),
  },
];

export default function PlaybooksPage() {
  const pg = usePagination(50);
  const { data = [], isLoading } = useQuery<Playbook[]>({
    queryKey: ["playbooks", pg.page],
    queryFn: () => api.get("/playbooks", pg.params),
  });

  return (
    <div className="space-y-6">
      <PageHeader
        title="Playbooks"
        description="Governed living playbooks and candidate review queue."
        actions={
          <Link href="/patterns" className={cn(buttonVariants(), "")}>
            <Plus className="mr-2 h-4 w-4" />
            Generate from Pattern
          </Link>
        }
      />
      {isLoading ? (
        <DataTableSkeleton columns={5} />
      ) : (
        <>
          <DataTable columns={columns} data={data} />
          <PaginationControls
            page={pg.page}
            pageSize={pg.pageSize}
            count={data.length}
            onPrev={pg.prevPage}
            onNext={pg.nextPage}
          />
        </>
      )}
    </div>
  );
}
