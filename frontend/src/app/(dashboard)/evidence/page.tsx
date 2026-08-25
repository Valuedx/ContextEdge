"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ColumnDef } from "@tanstack/react-table";
import { RefreshCw, Trash2, Loader2, FilterX } from "lucide-react";

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
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
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
    id: "record",
    header: "Record",
    accessorFn: (row) => row.source_reference?.display_id || "",
    cell: ({ row }) => {
      const ref = row.original.source_reference;
      if (!ref?.display_id) {
        return <span className="text-muted-foreground text-xs">—</span>;
      }
      const label = `#${ref.display_id}`;
      return ref.url ? (
        <a
          href={ref.url}
          target="_blank"
          rel="noopener noreferrer"
          onClick={(event) => event.stopPropagation()}
          className="font-mono text-xs text-primary hover:underline truncate block max-w-[12rem]"
          title={ref.display_id}
        >
          {label}
        </a>
      ) : (
        <span
          className="font-mono text-xs truncate block max-w-[12rem]"
          title={ref.display_id}
        >
          {label}
        </span>
      );
    },
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
    accessorKey: "created_at_source",
    header: "Closed Date",
    sortingFn: (rowA, rowB, columnId) => {
      const valA = rowA.getValue(columnId) as string | null;
      const valB = rowB.getValue(columnId) as string | null;
      const timeA = valA ? new Date(valA).getTime() : 0;
      const timeB = valB ? new Date(valB).getTime() : 0;
      return timeA - timeB;
    },
    cell: ({ row }) => {
      const val = row.getValue("created_at_source") as string | null;
      return val ? new Date(val).toLocaleString() : "—";
    },
  },
  {
    accessorKey: "ingested_at",
    header: "Fetched",
    sortingFn: (rowA, rowB, columnId) => {
      const valA = rowA.getValue(columnId) as string | null;
      const valB = rowB.getValue(columnId) as string | null;
      const timeA = valA ? new Date(valA).getTime() : 0;
      const timeB = valB ? new Date(valB).getTime() : 0;
      return timeA - timeB;
    },
    cell: ({ row }) => new Date(row.getValue("ingested_at")).toLocaleString(),
  },
  {
    id: "actions",
    enableSorting: false,
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
  const [evidenceTypeFilter, setEvidenceTypeFilter] = useState("all");
  const [relevanceFilter, setRelevanceFilter] = useState("all");
  const [sourceTypeFilter, setSourceTypeFilter] = useState("all");
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
    queryKey: ["evidence", appliedQuery, evidenceTypeFilter, relevanceFilter, sourceTypeFilter, pg.page],
    queryFn: () => {
      const params: Record<string, string> = { ...pg.params };
      if (appliedQuery.trim()) params.query = appliedQuery.trim();
      if (evidenceTypeFilter !== "all") params.evidence_type = evidenceTypeFilter;
      if (relevanceFilter !== "all") params.relevance_state = relevanceFilter;
      if (sourceTypeFilter !== "all") params.source_type = sourceTypeFilter;
      return api.get("/evidence", params);
    },
  });

  const clearAllFilters = () => {
    setSearch("");
    setAppliedQuery("");
    setEvidenceTypeFilter("all");
    setRelevanceFilter("all");
    setSourceTypeFilter("all");
    pg.reset();
  };

  return (
    <div className="space-y-6">
      <PageHeader title="Evidence Explorer" description="Search and browse operational evidence across all sources." />
      
      {/* Header Controls & Filters Bar */}
      <div className="flex flex-wrap items-center justify-between gap-3 rounded-lg border bg-card p-4 shadow-sm">
        <div className="flex flex-wrap items-center gap-3 flex-1 min-w-[280px]">
          {/* Search Input */}
          <div className="w-full sm:w-64">
            <Input
              placeholder="Search record #, title, or text…"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter") {
                  pg.reset();
                  setAppliedQuery(search.trim());
                }
              }}
              className="font-mono text-sm"
            />
          </div>

          {/* Type Filter */}
          <Select value={evidenceTypeFilter} onValueChange={(v) => { pg.reset(); setEvidenceTypeFilter(v ?? "all"); }}>
            <SelectTrigger className="w-36">
              <SelectValue placeholder="All Types" />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="all">All Types</SelectItem>
              <SelectItem value="ticket">Ticket</SelectItem>
              <SelectItem value="kb_article">KB Article</SelectItem>
              <SelectItem value="slack_message">Slack</SelectItem>
              <SelectItem value="teams_message">Teams</SelectItem>
              <SelectItem value="email">Email</SelectItem>
              <SelectItem value="sop">SOP</SelectItem>
            </SelectContent>
          </Select>

          {/* Relevance Filter */}
          <Select value={relevanceFilter} onValueChange={(v) => { pg.reset(); setRelevanceFilter(v ?? "all"); }}>
            <SelectTrigger className="w-40">
              <SelectValue placeholder="All Relevance" />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="all">All Relevance</SelectItem>
              <SelectItem value="operational">Operational</SelectItem>
              <SelectItem value="possibly_relevant">Possibly Relevant</SelectItem>
              <SelectItem value="not_relevant">Not Relevant</SelectItem>
              <SelectItem value="unclassified">Unclassified</SelectItem>
            </SelectContent>
          </Select>

          {/* Source Type Filter */}
          <Select value={sourceTypeFilter} onValueChange={(v) => { pg.reset(); setSourceTypeFilter(v ?? "all"); }}>
            <SelectTrigger className="w-36">
              <SelectValue placeholder="All Sources" />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="all">All Sources</SelectItem>
              <SelectItem value="zoho_desk">Zoho Desk</SelectItem>
              <SelectItem value="servicenow">ServiceNow</SelectItem>
              <SelectItem value="teams">Teams</SelectItem>
              <SelectItem value="email">Email</SelectItem>
            </SelectContent>
          </Select>

          {/* Search Button */}
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

          {/* Clear Filters Button */}
          {(appliedQuery || evidenceTypeFilter !== "all" || relevanceFilter !== "all" || sourceTypeFilter !== "all") && (
            <Button
              type="button"
              variant="outline"
              onClick={clearAllFilters}
              className="text-xs"
            >
              <FilterX className="mr-1.5 h-3.5 w-3.5" />
              Clear Filters
            </Button>
          )}
        </div>

        {/* Right Action Buttons */}
        <div className="flex items-center gap-2">
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
        <DataTableSkeleton columns={7} />
      ) : (
        <>
          <DataTable 
            columns={columns} 
            data={data} 
            defaultSorting={[{ id: "created_at_source", desc: true }]}
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
