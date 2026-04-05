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
      return val ? new Date(val as string).toLocaleDateString() : "Never";
    },
  },
  {
    accessorKey: "updated_at",
    header: "Updated",
    cell: ({ row }) => new Date(row.getValue("updated_at")).toLocaleDateString(),
  },
];

export default function PlaybooksPage() {
  const { data = [], isLoading } = useQuery<Playbook[]>({
    queryKey: ["playbooks"],
    queryFn: () => api.get("/playbooks"),
  });

  return (
    <div className="space-y-6">
      <PageHeader
        title="Playbooks"
        description="Governed living playbooks and candidate review queue."
        actions={
          <Button>
            <Plus className="mr-2 h-4 w-4" />
            New Playbook
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
