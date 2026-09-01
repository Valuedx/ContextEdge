"use client";

/** What /patterns/deduplicate returns. */
type DeduplicateResult = {
  data?: {
    merged_episodes?: number;
    merged_patterns?: number;
    merged_playbooks?: number;
  };
};

import { useMutation, useQuery } from "@tanstack/react-query";
import { ColumnDef } from "@tanstack/react-table";
import { BookOpen, Loader2, Network, List, Trash2, BookCheck, RefreshCw, FilterX, Search } from "lucide-react";
import { useRouter } from "next/navigation";
import { useQueryClient } from "@tanstack/react-query";
import { useState, useMemo } from "react";

import { PageHeader } from "@/components/common/page-header";
import { PageToolbar } from "@/components/common/page-toolbar";
import { DataTable } from "@/components/common/data-table";
import { DataTableSkeleton } from "@/components/common/data-table-skeleton";
import { api } from "@/lib/api";
import type { Pattern } from "@/lib/types";
import Link from "next/link";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { toast } from "sonner";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { PatternGraph } from "@/components/patterns/pattern-graph";
import { usePagination } from "@/lib/hooks/use-pagination";
import { PaginationControls } from "@/components/common/pagination-controls";
import { ConfirmActionDialog } from "@/components/common/confirm-action-dialog";

function PatternActions({ pattern }: { pattern: Pattern }) {
  const router = useRouter();
  const queryClient = useQueryClient();
  const [deleteOpen, setDeleteOpen] = useState(false);

  const generateMutation = useMutation({
    mutationFn: () => api.post("/playbooks/generate", { pattern_id: pattern.id }),
    onSuccess: () => {
      toast.success("Playbook candidate updated!");
      queryClient.invalidateQueries({ queryKey: ["patterns"] });
      queryClient.invalidateQueries({ queryKey: ["playbooks"] });
      router.push("/playbooks");
    },
    onError: (err: Error) => {
      toast.error(err.message || "Failed to generate playbook");
    },
  });

  const deleteMutation = useMutation({
    mutationFn: () => api.delete(`/patterns/${pattern.id}`),
    onSuccess: () => {
      toast.success(`Pattern "${pattern.title}" deleted`);
      queryClient.invalidateQueries({ queryKey: ["patterns"] });
      setDeleteOpen(false);
    },
    onError: (err: Error) => {
      toast.error(err.message || "Failed to delete pattern");
    },
  });

  return (
    <div className="flex items-center gap-1.5">
      {pattern.has_playbook ? (
        <>
          {pattern.playbook_status === "review_needed" ? (
            <Button
              variant="ghost"
              size="sm"
              className="text-amber-500 hover:text-amber-400 hover:bg-amber-500/10 gap-1 font-normal text-xs"
              title="New episodes added — click to update Playbook"
              onClick={() => generateMutation.mutate()}
              disabled={generateMutation.isPending}
            >
              {generateMutation.isPending ? (
                <Loader2 className="h-3.5 w-3.5 animate-spin" />
              ) : (
                <RefreshCw className="h-3.5 w-3.5" />
              )}
              Update Playbook
            </Button>
          ) : null}

          <Button
            variant="ghost"
            size="sm"
            className="text-emerald-500 hover:text-emerald-400 hover:bg-emerald-500/10 gap-1 font-normal text-xs"
            title="Playbook Generated — Click to View"
            onClick={() => router.push(pattern.playbook_id ? `/playbooks/${pattern.playbook_id}` : "/playbooks")}
          >
            <BookCheck className="h-3.5 w-3.5 text-emerald-500" />
            View Playbook
          </Button>
        </>
      ) : (
        <Button
          variant="ghost"
          size="sm"
          className="gap-1 text-xs font-normal text-muted-foreground hover:text-primary"
          title="Generate Playbook from this pattern"
          onClick={() => generateMutation.mutate()}
          disabled={generateMutation.isPending}
        >
          {generateMutation.isPending ? (
            <Loader2 className="h-3.5 w-3.5 animate-spin" />
          ) : (
            <BookOpen className="h-3.5 w-3.5" />
          )}
          Generate Playbook
        </Button>
      )}

      <Button
        variant="ghost"
        size="icon"
        className="text-muted-foreground hover:text-red-500 h-8 w-8"
        title="Delete Pattern"
        aria-label="Delete pattern"
        onClick={() => setDeleteOpen(true)}
        disabled={deleteMutation.isPending}
      >
        {deleteMutation.isPending ? (
          <Loader2 className="h-3.5 w-3.5 animate-spin" />
        ) : (
          <Trash2 className="h-3.5 w-3.5" />
        )}
      </Button>
      <ConfirmActionDialog
        open={deleteOpen}
        onOpenChange={setDeleteOpen}
        title="Delete pattern?"
        description={`This will permanently delete "${pattern.title}".`}
        confirmLabel="Delete pattern"
        isPending={deleteMutation.isPending}
        onConfirm={() => deleteMutation.mutate()}
      />
    </div>
  );
}

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
    accessorKey: "playbook_status",
    header: "Playbook Status",
    cell: ({ row }) => {
      const p = row.original;
      if (!p.has_playbook) {
        return <span className="text-xs text-muted-foreground">Not Generated</span>;
      }
      if (p.playbook_status === "review_needed") {
        return <span className="inline-flex items-center rounded-full bg-amber-500/10 px-2 py-0.5 text-xs font-medium text-amber-500 border border-amber-500/20">Needs Sync</span>;
      }
      return <span className="inline-flex items-center rounded-full bg-emerald-500/10 px-2 py-0.5 text-xs font-medium text-emerald-500 border border-emerald-500/20">Generated</span>;
    },
  },
  {
    accessorKey: "created_at",
    header: "Created",
    cell: ({ row }) => {
      const val = row.getValue("created_at");
      return val ? new Date(val as string).toLocaleString() : "N/A";
    },
  },
  {
    id: "actions",
    enableSorting: false,
    cell: ({ row }) => (
      <div className="flex justify-end">
        <PatternActions pattern={row.original} />
      </div>
    ),
  },
];

export default function PatternsPage() {
  const [search, setSearch] = useState<string>("");
  const [appliedQuery, setAppliedQuery] = useState<string>("");
  const [statusTab, setStatusTab] = useState<string>("all");
  const [viewMode, setViewMode] = useState<string>("list");

  const pg = usePagination(50);
  const queryClient = useQueryClient();

  const { data: rawData = [], isLoading, isFetching } = useQuery<Pattern[]>({
    queryKey: ["patterns", pg.page, appliedQuery],
    queryFn: () => {
      const params: Record<string, string> = { ...pg.params };
      if (appliedQuery.trim()) {
        params.q = appliedQuery.trim();
      }
      return api.get("/patterns", params);
    },
  });

  const data = useMemo(() => {
    if (statusTab === "all") return rawData;
    if (statusTab === "generated") {
      return rawData.filter((p) => p.has_playbook && p.playbook_status !== "review_needed");
    }
    if (statusTab === "review_needed") {
      return rawData.filter((p) => p.playbook_status === "review_needed");
    }
    if (statusTab === "none") {
      return rawData.filter((p) => !p.has_playbook);
    }
    return rawData;
  }, [rawData, statusTab]);

  const clearAllFilters = () => {
    setSearch("");
    setAppliedQuery("");
    setStatusTab("all");
    pg.reset();
  };

  const hasActiveFilters = Boolean(appliedQuery.trim() || statusTab !== "all");

  const dedupMutation = useMutation({
    mutationFn: () => api.post<DeduplicateResult>("/patterns/deduplicate", {}),
    onSuccess: (res: DeduplicateResult) => {
      const mergedEps = res?.data?.merged_episodes || 0;
      const mergedPats = res?.data?.merged_patterns || 0;
      const mergedPbs = res?.data?.merged_playbooks || 0;
      toast.success(
        `Deduplication complete! Merged ${mergedEps} duplicate episodes, ${mergedPats} patterns, and ${mergedPbs} playbooks.`
      );
      queryClient.invalidateQueries({ queryKey: ["episodes"] });
      queryClient.invalidateQueries({ queryKey: ["patterns"] });
      queryClient.invalidateQueries({ queryKey: ["playbooks"] });
    },
    onError: (err: Error) => {
      toast.error(err.message || "Failed to deduplicate patterns");
    },
  });

  return (
    <div className="space-y-6">
      <PageHeader
        title="Patterns"
        description="Operational patterns derived from episode clusters."
        actions={
          <div className="flex items-center gap-2">
            <Button
              variant="outline"
              size="sm"
              className="gap-2 text-xs"
              onClick={() => dedupMutation.mutate()}
              disabled={dedupMutation.isPending}
            >
              {dedupMutation.isPending ? (
                <Loader2 className="h-3.5 w-3.5 animate-spin" />
              ) : (
                <RefreshCw className="h-3.5 w-3.5" />
              )}
              Clean & Deduplicate
            </Button>
          </div>
        }
      />
      
      <div className="flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between">
        <Tabs
          value={statusTab}
          onValueChange={(val) => {
            if (val) {
              setStatusTab(val);
              pg.reset();
            }
          }}
          className="w-full sm:w-auto"
        >
          <TabsList className="bg-black/[0.03] dark:bg-white/[0.04] p-1 border border-border/50">
            <TabsTrigger value="all">All Patterns</TabsTrigger>
            <TabsTrigger value="generated">With Playbook</TabsTrigger>
            <TabsTrigger value="review_needed">Needs Sync</TabsTrigger>
            <TabsTrigger value="none">No Playbook</TabsTrigger>
          </TabsList>
        </Tabs>

        <Tabs
          value={viewMode}
          onValueChange={(val) => {
            if (val) setViewMode(val);
          }}
          className="w-full sm:w-auto"
        >
          <TabsList className="bg-black/[0.03] dark:bg-white/[0.04] p-1 border border-border/50">
            <TabsTrigger value="list" className="gap-1.5 text-xs">
              <List className="h-3.5 w-3.5" /> List
            </TabsTrigger>
            <TabsTrigger value="graph" className="gap-1.5 text-xs">
              <Network className="h-3.5 w-3.5" /> Graph View
            </TabsTrigger>
          </TabsList>
        </Tabs>
      </div>

      <PageToolbar
        actions={
          <>
            <Button
              type="button"
              size="sm"
              variant="outline"
              onClick={() => queryClient.invalidateQueries({ queryKey: ["patterns"] })}
              disabled={isFetching}
            >
              {isFetching ? (
                <Loader2 className="mr-1.5 h-3.5 w-3.5 animate-spin" />
              ) : (
                <RefreshCw className="mr-1.5 h-3.5 w-3.5" />
              )}
              Refresh
            </Button>
            {hasActiveFilters && (
              <Button type="button" size="sm" variant="ghost" onClick={clearAllFilters} className="text-xs">
                <FilterX className="mr-1.5 h-3.5 w-3.5" />
                Clear Filters
              </Button>
            )}
          </>
        }
      >
        <form
          onSubmit={(e) => {
            e.preventDefault();
            setAppliedQuery(search);
            pg.reset();
          }}
          className="relative min-w-[220px] flex-1"
        >
          <Search className="absolute left-2.5 top-1/2 -translate-y-1/2 h-3.5 w-3.5 text-muted-foreground" />
          <Input
            type="search"
            placeholder="Search patterns by title, type, or root cause description..."
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            className="pl-8 text-xs"
          />
        </form>
      </PageToolbar>

      {viewMode === "list" ? (
        isLoading ? (
          <DataTableSkeleton columns={6} />
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
        )
      ) : (
        data.length > 0 ? (
          <div className="grid grid-cols-1 gap-6">
            {data.map((pattern) => (
              <div key={pattern.id} className="space-y-3">
                <div className="flex items-center justify-between">
                  <h3 className="text-lg font-semibold text-foreground">{pattern.title}</h3>
                  <div className="flex items-center gap-4 text-sm text-muted-foreground">
                    <span>{pattern.episode_count} Episodes</span>
                    <span>{((pattern.confidence) * 100).toFixed(0)}% Confidence</span>
                  </div>
                </div>
                <PatternGraph patternId={pattern.id} />
              </div>
            ))}
          </div>
        ) : (
          <div className="flex flex-col items-center justify-center rounded-lg border bg-card p-12 shadow-sm">
            <Network className="mb-4 h-12 w-12 text-muted-foreground" />
            <p className="text-muted-foreground">No patterns match your filter.</p>
          </div>
        )
      )}
    </div>
  );
}
