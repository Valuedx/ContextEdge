"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ColumnDef } from "@tanstack/react-table";
import { Plus, Trash2, Loader2, Pencil } from "lucide-react";
import { toast } from "sonner";

import { PageHeader } from "@/components/common/page-header";
import { DataTable } from "@/components/common/data-table";
import { DataTableSkeleton } from "@/components/common/data-table-skeleton";
import { StatusBadge } from "@/components/common/status-badge";
import { Button } from "@/components/ui/button";
import { api } from "@/lib/api";
import type { Source } from "@/lib/types";
import Link from "next/link";

import { useMemo, useState } from "react";
import { AddSourceDialog } from "@/components/sources/add-source-dialog";
import { EditSourceDialog } from "@/components/sources/edit-source-dialog";
import { usePagination } from "@/lib/hooks/use-pagination";
import { PaginationControls } from "@/components/common/pagination-controls";
import { ConfirmActionDialog } from "@/components/common/confirm-action-dialog";

function SourceActions({
  source,
  onEdit,
}: {
  source: Source;
  onEdit: (source: Source) => void;
}) {
  const queryClient = useQueryClient();
  const [deleteOpen, setDeleteOpen] = useState(false);
  const mutation = useMutation({
    mutationFn: () => api.delete(`/sources/${source.id}`),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["sources"] });
      toast.success(`Source "${source.display_name}" deleted`);
      setDeleteOpen(false);
    },
    onError: (err: Error) => {
      toast.error(err.message || "Failed to delete source");
    },
  });

  return (
    <div className="flex justify-end gap-1">
      <Button
        variant="ghost"
        size="icon"
        className="text-muted-foreground hover:text-primary"
        title="Edit source"
        aria-label={`Edit ${source.display_name}`}
        onClick={() => onEdit(source)}
      >
        <Pencil className="h-4 w-4" />
      </Button>
      <Button
        variant="ghost"
        size="icon"
        className="text-muted-foreground hover:text-destructive"
        title="Delete source"
        aria-label={`Delete ${source.display_name}`}
        onClick={() => setDeleteOpen(true)}
        disabled={mutation.isPending}
      >
        {mutation.isPending ? (
          <Loader2 className="h-4 w-4 animate-spin" />
        ) : (
          <Trash2 className="h-4 w-4" />
        )}
      </Button>
      <ConfirmActionDialog
        open={deleteOpen}
        onOpenChange={setDeleteOpen}
        title="Delete source?"
        description={`This will delete "${source.display_name}" and remove its associated evidence logs.`}
        confirmLabel="Delete source"
        isPending={mutation.isPending}
        onConfirm={() => mutation.mutate()}
      />
    </div>
  );
}

function createColumns(onEdit: (source: Source) => void): ColumnDef<Source>[] {
  return [
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
      cell: ({ row }) => new Date(row.getValue("created_at")).toLocaleString(),
    },
    {
      id: "actions",
      cell: ({ row }) => <SourceActions source={row.original} onEdit={onEdit} />,
    },
  ];
}

export default function SourcesPage() {
  const [isAddDialogOpen, setIsAddDialogOpen] = useState(false);
  const [editingSource, setEditingSource] = useState<Source | null>(null);
  const columns = useMemo(() => createColumns(setEditingSource), []);
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
      {editingSource && (
        <EditSourceDialog
          key={editingSource.id}
          source={editingSource}
          open={!!editingSource}
          onOpenChange={(open) => {
            if (!open) setEditingSource(null);
          }}
        />
      )}
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
