"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ColumnDef } from "@tanstack/react-table";
import { Sparkles, Loader2, Trash2, CheckCircle2 } from "lucide-react";

import { PageHeader } from "@/components/common/page-header";
import { DataTable } from "@/components/common/data-table";
import { DataTableSkeleton } from "@/components/common/data-table-skeleton";
import { StatusBadge } from "@/components/common/status-badge";
import { api } from "@/lib/api";
import type { Episode, EpisodeReconstructQueuedResponse } from "@/lib/types";
import Link from "next/link";
import { useState } from "react";
import { Button } from "@/components/ui/button";
import { toast } from "sonner";
import { usePagination } from "@/lib/hooks/use-pagination";
import { PaginationControls } from "@/components/common/pagination-controls";

function EpisodeActions({ episodeId, title, isApproved }: { episodeId: string; title: string; isApproved: boolean }) {
  const queryClient = useQueryClient();

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
        onClick={(e) => {
          e.stopPropagation();
          if (confirm(`Are you sure you want to delete episode "${title}"?`)) {
            deleteMutation.mutate();
          }
        }}
        disabled={deleteMutation.isPending}
      >
        {deleteMutation.isPending ? (
          <Loader2 className="h-4 w-4 animate-spin" />
        ) : (
          <Trash2 className="h-4 w-4" />
        )}
      </Button>
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

  const pg = usePagination(50);
  const queryClient = useQueryClient();

  const { data = [], isLoading } = useQuery<Episode[]>({
    queryKey: ["episodes", pg.page],
    queryFn: () => api.get("/episodes", pg.params),
  });

  const bulkApproveMutation = useMutation({
    mutationFn: (ids: string[]) => api.post("/episodes/bulk-approve", { ids }),
    onSuccess: (res: any) => {
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
          <div className="flex flex-wrap items-center gap-3">
            {selectedIds.length > 0 && (
              <Button
                type="button"
                variant="default"
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
              onClick={handleConstructPattern} 
              disabled={isClustering}
              variant="outline"
              className="border-indigo-500/50 text-indigo-400 hover:bg-indigo-500/10 hover:text-indigo-300"
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
              className="bg-gradient-to-r from-indigo-600 to-purple-600 hover:from-indigo-700 hover:to-purple-700"
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
