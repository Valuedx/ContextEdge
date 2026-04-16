"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ColumnDef } from "@tanstack/react-table";
import { Plus, Trash2, Loader2 } from "lucide-react";
import { toast } from "sonner";

import { PageHeader } from "@/components/common/page-header";
import { DataTable } from "@/components/common/data-table";
import { DataTableSkeleton } from "@/components/common/data-table-skeleton";
import { StatusBadge } from "@/components/common/status-badge";
import { Button } from "@/components/ui/button";
import { api } from "@/lib/api";
import type { Source } from "@/lib/types";
import Link from "next/link";

import { useState } from "react";
import { AddSourceDialog } from "@/components/sources/add-source-dialog";
import { usePagination } from "@/lib/hooks/use-pagination";
import { PaginationControls } from "@/components/common/pagination-controls";

function SourceActions({ sourceId, name }: { sourceId: string; name: string }) {
  const queryClient = useQueryClient();
  const mutation = useMutation({
    mutationFn: () => api.delete(`/sources/${sourceId}`),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["sources"] });
      toast.success(`Source "${name}" deleted`);
    },
    onError: (err: Error) => {
      toast.error(err.message || "Failed to delete source");
    },
  });

  return (
    <Button
      variant="ghost"
      size="icon"
      className="text-muted-foreground hover:text-destructive"
      onClick={() => {
        if (confirm(`Are you sure you want to delete "${name}"? This will also remove all associated evidence logs.`)) {
          mutation.mutate();
        }
      }}
      disabled={mutation.isPending}
    >
      {mutation.isPending ? (
        <Loader2 className="h-4 w-4 animate-spin" />
      ) : (
        <Trash2 className="h-4 w-4" />
      )}
    </Button>
  );
}

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
  {
    id: "actions",
    cell: ({ row }) => (
      <div className="flex justify-end">
        <SourceActions sourceId={row.original.id} name={row.original.display_name} />
      </div>
    ),
  },
];

export default function SourcesPage() {
  const [isAddDialogOpen, setIsAddDialogOpen] = useState(false);
  const pg = usePagination(50);
  const { data = [], isLoading } = useQuery<Source[]>({
    queryKey: ["sources", pg.page],
    queryFn: () => api.get("/sources", pg.params),
  });

  return (
    <div className="space-y-6">
      <PageHeader
        title="Sources"
        description="Manage connected data sources and their ingestion configuration."
        actions={
          <Button onClick={() => setIsAddDialogOpen(true)}>
            <Plus className="mr-2 h-4 w-4" />
            Add Source
          </Button>
        }
      />

      <AddSourceDialog open={isAddDialogOpen} onOpenChange={setIsAddDialogOpen} />
      {isLoading ? (
        <DataTableSkeleton columns={6} />
      ) : data.length === 0 ? (
        <div className="rounded-md border p-12 text-center text-muted-foreground">
          No sources configured yet. Add your first source to begin ingestion.
        </div>
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
