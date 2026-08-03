"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ColumnDef } from "@tanstack/react-table";
import { RefreshCw, Trash2, Loader2 } from "lucide-react";

import { PageHeader } from "@/components/common/page-header";
import { DataTable } from "@/components/common/data-table";
import { DataTableSkeleton } from "@/components/common/data-table-skeleton";
import { StatusBadge } from "@/components/common/status-badge";
import { api } from "@/lib/api";
import type { EvidenceItem } from "@/lib/types";
import Link from "next/link";
import { useState } from "react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { toast } from "sonner";
import { usePagination } from "@/lib/hooks/use-pagination";
import { PaginationControls } from "@/components/common/pagination-controls";

function EvidenceActions({ evidenceId }: { evidenceId: string; title: string }) {
  const queryClient = useQueryClient();
  const mutation = useMutation({
    mutationFn: () => api.delete(`/evidence/${evidenceId}`),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["evidence"] });
      toast.success("Evidence record deleted");
    },
    onError: (err: Error) => {
      toast.error(err.message || "Failed to delete record");
    },
  });

  return (
    <Button
      variant="ghost"
      size="icon"
      className="text-muted-foreground hover:text-destructive"
      onClick={() => {
        if (confirm(`Permanently delete this evidence record?`)) {
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

const columns: ColumnDef<EvidenceItem>[] = [
  {
    id: "select",
    header: ({ table }) => (
      <input
        type="checkbox"
        className="h-4 w-4 rounded border-black/20 bg-white/50 text-primary focus:ring-primary dark:border-white/20 dark:bg-white/5"
        checked={table.getIsAllPageRowsSelected()}
        onChange={(e) => table.toggleAllPageRowsSelected(!!e.target.checked)}
        aria-label="Select all"
      />
    ),
    cell: ({ row }) => (
      <input
        type="checkbox"
        className="h-4 w-4 rounded border-black/20 bg-white/50 text-primary focus:ring-primary dark:border-white/20 dark:bg-white/5"
        checked={row.getIsSelected()}
        onChange={(e) => row.toggleSelected(!!e.target.checked)}
        aria-label="Select row"
      />
    ),
    enableSorting: false,
    enableHiding: false,
  },
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
    accessorKey: "source_type",
    header: "Source",
    cell: ({ row }) => row.original.source_type || "unknown",
  },
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
  {
    id: "actions",
    cell: ({ row }) => (
      <div className="flex justify-end">
        <EvidenceActions evidenceId={row.original.id} title={row.original.title || "Untitled"} />
      </div>
    ),
  },
];

export default function EvidencePage() {
  const [search, setSearch] = useState("");
  const [appliedQuery, setAppliedQuery] = useState("");
  const [selectedIds, setSelectedIds] = useState<string[]>([]);
  const pg = usePagination(50);
  const queryClient = useQueryClient();

  const bulkDeleteMutation = useMutation({
    mutationFn: (ids: string[]) => api.post("/evidence/bulk-delete", { ids }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["evidence"] });
      toast.success(`${selectedIds.length} records deleted`);
      setSelectedIds([]);
    },
    onError: (err: Error) => {
      toast.error(err.message || "Bulk delete failed");
    },
  });

  const { data = [], isLoading, isFetching } = useQuery<EvidenceItem[]>({
    queryKey: ["evidence", appliedQuery, pg.page],
    queryFn: () => {
      const params: Record<string, string> = { ...pg.params };
      if (appliedQuery.trim()) params.query = appliedQuery.trim();
      return api.get("/evidence", params);
    },
  });

  return (
    <div className="space-y-6">
      <PageHeader title="Evidence Explorer" description="Search and browse operational evidence across all sources." />
      <div className="flex max-w-xl flex-col gap-2 sm:flex-row sm:items-center">
        <Input
          placeholder="Full-text search (optional)…"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter") setAppliedQuery(search.trim());
          }}
          className="font-mono text-sm"
        />
        <div className="flex shrink-0 gap-2">
          <Button
            type="button"
            variant="outline"
            onClick={() => queryClient.invalidateQueries({ queryKey: ["evidence"] })}
            disabled={isFetching}
          >
            {isFetching ? (
              <Loader2 className="mr-2 h-4 w-4 animate-spin" />
            ) : (
              <RefreshCw className="mr-2 h-4 w-4" />
            )}
            Refresh
          </Button>
          <Button
            type="button"
            variant="secondary"
            onClick={() => {
              pg.reset();
              setAppliedQuery(search.trim());
            }}
          >
            Search
          </Button>
          <Button
            type="button"
            variant="outline"
            onClick={() => {
              setSearch("");
              setAppliedQuery("");
              pg.reset();
            }}
          >
            Clear Search
          </Button>

          <Button
            type="button"
            variant="destructive"
            onClick={() => {
              const confirmText = prompt("Type 'PURGE' to permanently delete ALL evidence records:");
              if (confirmText === "PURGE") {
                api.delete("/evidence/purge")
                  .then(() => {
                    toast.success("All evidence records purged");
                    queryClient.invalidateQueries({ queryKey: ["evidence"] });
                  })
                  .catch((err) => toast.error(err.message || "Purge failed"));
              }
            }}
          >
            Purge All
          </Button>

          {selectedIds.length > 0 && (
            <Button
              type="button"
              variant="destructive"
              onClick={() => {
                if (confirm(`Delete ${selectedIds.length} selected records?`)) {
                  bulkDeleteMutation.mutate(selectedIds);
                }
              }}
              disabled={bulkDeleteMutation.isPending}
            >
              {bulkDeleteMutation.isPending ? (
                <Loader2 className="mr-2 h-4 w-4 animate-spin" />
              ) : (
                <Trash2 className="mr-2 h-4 w-4" />
              )}
              Delete Selected
            </Button>
          )}
        </div>
      </div>
      {isLoading ? (
        <DataTableSkeleton columns={6} />
      ) : (
        <>
          <DataTable 
            columns={columns} 
            data={data} 
            onSelectionChange={setSelectedIds}
          />
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
