"use client";

/** What /episodes/bulk-approve returns. Typed rather than `any` so a field
 *  rename shows up here instead of as an undefined at runtime. */
type BulkApproveResult = {
  approved_count?: number;
};

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ColumnDef } from "@tanstack/react-table";
import { Sparkles, Loader2, Trash2, CheckCircle2, FilterX, Search } from "lucide-react";

import { PageHeader } from "@/components/common/page-header";
import { PageToolbar } from "@/components/common/page-toolbar";
import { DataTable } from "@/components/common/data-table";
import { DataTableSkeleton } from "@/components/common/data-table-skeleton";
import { StatusBadge } from "@/components/common/status-badge";
import { api } from "@/lib/api";
import type { Episode, EpisodeReconstructQueuedResponse } from "@/lib/types";
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
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { toast } from "sonner";
import { usePagination } from "@/lib/hooks/use-pagination";
import { PaginationControls } from "@/components/common/pagination-controls";
import { ConfirmActionDialog } from "@/components/common/confirm-action-dialog";

function EpisodeActions({ episodeId, title, isApproved }: { episodeId: string; title: string; isApproved: boolean }) {
  const queryClient = useQueryClient();
  const [deleteOpen, setDeleteOpen] = useState(false);

  const approveMutation = useMutation({
    mutationFn: () => api.post(`/episodes/${episodeId}/approve`, {}),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["episodes"] });
      queryClient.invalidateQueries({ queryKey: ["patterns"] });
      queryClient.invalidateQueries({ queryKey: ["playbooks"] });
      toast.success(`Episode "${title}" approved! Pattern construction & playbook update triggered.`);
    },
    onError: (err: Error) => {
      toast.error(err.message || "Failed to approve episode");
    },
  });

  const deleteMutation = useMutation({
    mutationFn: () => api.delete(`/episodes/${episodeId}`),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["episodes"] });
      toast.success(`Episode "${title}" deleted`);
      setDeleteOpen(false);
    },
    onError: (err: Error) => {
      toast.error(err.message || "Failed to delete episode");
    },
  });

  return (
    <div className="flex items-center gap-1">
      <Button
        variant="ghost"
        size="icon"
        className={isApproved ? "text-emerald-500 hover:text-emerald-600" : "text-muted-foreground hover:text-emerald-500"}
        title={isApproved ? "Re-Approve & Update Pattern/Playbook" : "Approve Episode"}
        aria-label={isApproved ? "Re-approve episode" : "Approve episode"}
        onClick={(e) => {
          e.stopPropagation();
          approveMutation.mutate();
        }}
        disabled={approveMutation.isPending}
      >
        {approveMutation.isPending ? (
          <Loader2 className="h-4 w-4 animate-spin" />
        ) : (
          <CheckCircle2 className="h-4 w-4" />
        )}
      </Button>
      <Button
        variant="ghost"
        size="icon"
        className="text-muted-foreground hover:text-destructive"
        title="Delete Episode"
        aria-label="Delete episode"
        onClick={(e) => {
          e.stopPropagation();
          setDeleteOpen(true);
        }}
        disabled={deleteMutation.isPending}
      >
        {deleteMutation.isPending ? (
          <Loader2 className="h-4 w-4 animate-spin" />
        ) : (
          <Trash2 className="h-4 w-4" />
        )}
      </Button>
      <ConfirmActionDialog
        open={deleteOpen}
        onOpenChange={setDeleteOpen}
        title="Delete episode?"
        description={`This will permanently delete "${title}".`}
        confirmLabel="Delete episode"
        isPending={deleteMutation.isPending}
        onConfirm={() => deleteMutation.mutate()}
      />
    </div>
  );
}

const columns: ColumnDef<Episode>[] = [
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
    accessorKey: "ai_review",
    header: "AI verdict",
    enableSorting: false,
    cell: ({ row }) => {
      const review = row.original.ai_review;
      if (!review) return <span className="text-xs text-muted-foreground">—</span>;
      const approve = review.verdict === "approve";
      const label = review.auto_approved
        ? "auto-approved"
        : `${review.verdict} ${(review.confidence * 100).toFixed(0)}%`;
      const reasons = (review.reasons ?? []).join("; ");
      const floors = (review.failed_floors ?? []).join(", ");
      return (
        <span
          title={[reasons, floors && `floors: ${floors}`].filter(Boolean).join(" | ")}
          className={
            "inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium " +
            (review.auto_approved
              ? "bg-emerald-500/15 text-emerald-500"
              : approve
                ? "bg-sky-500/15 text-sky-500"
                : "bg-amber-500/15 text-amber-500")
          }
        >
          {label}
        </span>
      );
    },
  },
  {
    accessorKey: "created_at",
    header: "Created",
    cell: ({ row }) => new Date(row.getValue("created_at")).toLocaleString(),
  },
  {
    id: "actions",
    enableSorting: false,
    cell: ({ row }) => (
      <div className="flex justify-end">
        <EpisodeActions 
          episodeId={row.original.id} 
          title={row.original.title} 
          isApproved={row.original.reviewer_state === "approved"} 
        />
      </div>
    ),
  },
];

export default function EpisodesPage() {
  const [isReconstructing, setIsReconstructing] = useState(false);
  const [isClustering, setIsClustering] = useState(false);
  const [selectedIds, setSelectedIds] = useState<string[]>([]);

  const [reviewerTab, setReviewerTab] = useState<string>("all");
  const [statusFilter, setStatusFilter] = useState<string>("all");
  const [search, setSearch] = useState<string>("");
  const [appliedQuery, setAppliedQuery] = useState<string>("");

  const pg = usePagination(50);
  const queryClient = useQueryClient();
  // Review priority is the default: newest-first buried the resolution-
  // bearing multi-evidence drafts (the ones worth a reviewer's first hour)
  // beneath the last trickle of fragments after every bulk ingest.
  const [sortMode, setSortMode] = useState<"review_priority" | "newest">("review_priority");

  const { data = [], isLoading, isFetching } = useQuery<Episode[]>({
    queryKey: ["episodes", pg.page, sortMode, reviewerTab, statusFilter, appliedQuery],
    queryFn: () => {
      const params: Record<string, string> = {
        ...pg.params,
        sort: sortMode,
      };
      if (appliedQuery.trim()) {
        params.q = appliedQuery.trim();
      }
      if (reviewerTab !== "all") {
        params.reviewer_state = reviewerTab;
        if (reviewerTab === "superseded") {
          params.include_superseded = "true";
        }
      }
      if (statusFilter !== "all") {
        params.status = statusFilter;
      }
      return api.get("/episodes", params);
    },
  });

  const clearAllFilters = () => {
    setSearch("");
    setAppliedQuery("");
    setStatusFilter("all");
    setReviewerTab("all");
    pg.reset();
  };

  const hasActiveFilters = Boolean(
    appliedQuery.trim() || reviewerTab !== "all" || statusFilter !== "all"
  );

  const bulkApproveMutation = useMutation({
    mutationFn: (ids: string[]) =>
      api.post<BulkApproveResult>("/episodes/bulk-approve", { ids }),
    onSuccess: (res: BulkApproveResult) => {
      queryClient.invalidateQueries({ queryKey: ["episodes"] });
      queryClient.invalidateQueries({ queryKey: ["patterns"] });
      queryClient.invalidateQueries({ queryKey: ["playbooks"] });
      toast.success(`${res.approved_count ?? selectedIds.length} episodes approved! Auto pattern creation & playbook updates triggered.`);
      setSelectedIds([]);
    },
    onError: (err: Error) => {
      toast.error(err.message || "Bulk approval failed");
    },
  });

  const handleReconstruct = async () => {
    try {
      setIsReconstructing(true);
      const res = await api.post<EpisodeReconstructQueuedResponse>("/episodes/reconstruct", {});
      const tid = res.task_id ? `${res.task_id.slice(0, 8)}…` : "unknown";
      const evidenceCount = res.detail?.evidence_count ?? 0;
      toast.success(
        `Reconstruction queued for ${evidenceCount} evidence item${evidenceCount === 1 ? "" : "s"}. Celery task ${tid} — refresh episodes after the worker finishes.`,
      );
    } catch (err) {
      toast.error((err as Error).message || "Failed to trigger reconstruction");
    } finally {
      setIsReconstructing(false);
    }
  };

  const [isAiReviewing, setIsAiReviewing] = useState(false);
  const handleAiReview = async () => {
    try {
      setIsAiReviewing(true);
      const res = await api.post<{ status: string; task_id?: string; detail?: { mode?: string; limit?: number } }>(
        "/episodes/ai-review",
        {},
      );
      const mode = res.detail?.mode ?? "advisory";
      toast.success(
        mode === "auto_approve"
          ? "AI review queued — drafts clearing the model verdict AND the deterministic floors will be auto-approved; everything else stays in your queue with the verdict attached."
          : "AI review queued in advisory mode — every draft gets an AI verdict for your review; nothing is approved automatically.",
      );
      queryClient.invalidateQueries({ queryKey: ["episodes"] });
    } catch (err) {
      toast.error((err as Error).message || "Failed to dispatch AI review");
    } finally {
      setIsAiReviewing(false);
    }
  };

  const handleConstructPattern = async () => {
    try {
      setIsClustering(true);
      const res = await api.post<{ task_id: string; domain_id: string }>("/patterns/cluster", {});
      const tid = res.task_id ? `${res.task_id.slice(0, 8)}…` : "unknown";
      toast.success(
        `Pattern construction queued. Celery task ${tid} is looking for approved episodes to cluster. Check the Patterns page in a few moments.`,
      );
    } catch (err) {
      toast.error((err as Error).message || "Failed to trigger pattern construction");
    } finally {
      setIsClustering(false);
    }
  };

  return (
    <div className="space-y-6">
      <PageHeader 
        title="Episodes" 
        description="Reconstructed troubleshooting episodes from correlated evidence." 
        actions={
          <div className="flex items-center gap-2">
            {selectedIds.length > 0 && (
              <Button
                type="button"
                variant="default"
                size="sm"
                className="bg-emerald-600 hover:bg-emerald-700 text-white"
                onClick={() => {
                  bulkApproveMutation.mutate(selectedIds);
                }}
                disabled={bulkApproveMutation.isPending}
              >
                {bulkApproveMutation.isPending ? (
                  <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                ) : (
                  <CheckCircle2 className="mr-2 h-4 w-4" />
                )}
                Approve Selected ({selectedIds.length})
              </Button>
            )}

            <Button
              type="button"
              variant="outline"
              size="sm"
              onClick={handleAiReview}
              disabled={isAiReviewing}
              title="AI first-pass review of pending drafts. Approval only happens when EPISODE_AI_REVIEW=auto_approve AND deterministic floors pass; otherwise verdicts are advisory annotations for your queue."
            >
              {isAiReviewing ? (
                <Loader2 className="mr-2 h-4 w-4 animate-spin" />
              ) : (
                <Sparkles className="mr-2 h-4 w-4" />
              )}
              AI Review
            </Button>
            <Button
              onClick={handleConstructPattern}
              disabled={isClustering}
              variant="outline"
              size="sm"
            >
              {isClustering ? (
                <Loader2 className="mr-2 h-4 w-4 animate-spin" />
              ) : (
                <Sparkles className="mr-2 h-4 w-4" />
              )}
              Construct Pattern
            </Button>
            <Button 
              onClick={handleReconstruct} 
              disabled={isReconstructing}
              size="sm"
            >
              {isReconstructing ? (
                <Loader2 className="mr-2 h-4 w-4 animate-spin" />
              ) : (
                <Sparkles className="mr-2 h-4 w-4" />
              )}
              Reconstruct
            </Button>
          </div>
        }
      />

      <div className="flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between">
        <Tabs
          value={reviewerTab}
          onValueChange={(val) => {
            setReviewerTab(val);
            pg.reset();
          }}
          className="w-full sm:w-auto"
        >
          <TabsList className="bg-black/[0.03] dark:bg-white/[0.04] p-1 border border-border/50">
            <TabsTrigger value="all">All</TabsTrigger>
            <TabsTrigger value="unreviewed">Drafts / Unreviewed</TabsTrigger>
            <TabsTrigger value="approved">Approved</TabsTrigger>
            <TabsTrigger value="rejected">Rejected</TabsTrigger>
            <TabsTrigger value="superseded">Superseded</TabsTrigger>
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
              onClick={() => queryClient.invalidateQueries({ queryKey: ["episodes"] })}
              disabled={isFetching}
            >
              {isFetching ? (
                <Loader2 className="mr-1.5 h-3.5 w-3.5 animate-spin" />
              ) : (
                <Sparkles className="mr-1.5 h-3.5 w-3.5" />
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
            placeholder="Search episodes by title, problem, or resolution..."
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            className="pl-8 text-xs"
          />
        </form>

        <div className="flex items-center gap-2">
          <Select
            value={statusFilter}
            onValueChange={(v) => {
              setStatusFilter(v);
              pg.reset();
            }}
          >
            <SelectTrigger className="w-[140px] text-xs">
              <SelectValue placeholder="Status" />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="all">All Statuses</SelectItem>
              <SelectItem value="draft">Draft</SelectItem>
              <SelectItem value="complete">Complete</SelectItem>
              <SelectItem value="failed">Failed</SelectItem>
            </SelectContent>
          </Select>

          <Select
            value={sortMode}
            onValueChange={(v: "review_priority" | "newest") => {
              setSortMode(v);
              pg.reset();
            }}
          >
            <SelectTrigger className="w-[150px] text-xs">
              <SelectValue placeholder="Sort" />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="review_priority">Review Priority</SelectItem>
              <SelectItem value="newest">Newest First</SelectItem>
            </SelectContent>
          </Select>
        </div>
      </PageToolbar>

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
