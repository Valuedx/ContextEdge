"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ColumnDef } from "@tanstack/react-table";
import { Sparkles, Loader2, Trash2, Brain } from "lucide-react";

import { PageHeader } from "@/components/common/page-header";
import { DataTable } from "@/components/common/data-table";
import { DataTableSkeleton } from "@/components/common/data-table-skeleton";
import { StatusBadge } from "@/components/common/status-badge";
import { api } from "@/lib/api";
import type { Episode } from "@/lib/types";
import Link from "next/link";
import { useState } from "react";
import { Button } from "@/components/ui/button";
import { toast } from "sonner";
import { useRouter } from "next/navigation";

function EpisodeActions({ episodeId, title }: { episodeId: string; title: string }) {
  const queryClient = useQueryClient();
  const router = useRouter();

  const deleteMutation = useMutation({
    mutationFn: () => api.delete(`/episodes/${episodeId}`),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["episodes"] });
      toast.success(`Episode "${title}" deleted`);
    },
    onError: (err: any) => {
      toast.error(err.message || "Failed to delete episode");
    },
  });

  const analyzeMutation = useMutation({
    mutationFn: () => api.post("/patterns/discover", { episode_ids: [episodeId] }),
    onSuccess: () => {
      toast.success("Knowledge pattern discovered!");
      router.push("/patterns");
    },
    onError: (err: any) => {
      toast.error(err.message || "Pattern analysis failed");
    },
  });

  return (
    <div className="flex gap-2">
      <Button
        variant="ghost"
        size="icon"
        className="text-muted-foreground hover:text-primary"
        title="Analyze Pattern"
        onClick={(e) => {
          e.stopPropagation();
          analyzeMutation.mutate();
        }}
        disabled={analyzeMutation.isPending}
      >
        {analyzeMutation.isPending ? (
          <Loader2 className="h-4 w-4 animate-spin" />
        ) : (
          <Brain className="h-4 w-4" />
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
    cell: ({ row }) => new Date(row.getValue("created_at")).toLocaleDateString(),
  },
  {
    id: "actions",
    cell: ({ row }) => (
      <div className="flex justify-end">
        <EpisodeActions episodeId={row.original.id} title={row.original.title} />
      </div>
    ),
  },
];

export default function EpisodesPage() {
  const queryClient = useQueryClient();
  const [isReconstructing, setIsReconstructing] = useState(false);
  
  const { data = [], isLoading } = useQuery<Episode[]>({
    queryKey: ["episodes"],
    queryFn: () => api.get("/episodes"),
  });

  const handleReconstruct = async () => {
    try {
      setIsReconstructing(true);
      const res = await api.post("/episodes/reconstruct", {});
      toast.success(`Reconstruction complete: ${res.evidence_count} items synthesized!`);
      // Refresh immediately since the backend is now synchronous
      queryClient.invalidateQueries({ queryKey: ["episodes"] });
    } catch (err: any) {
      toast.error(err.message || "Failed to trigger reconstruction");
    } finally {
      setIsReconstructing(false);
    }
  };

  return (
    <div className="space-y-6">
      <PageHeader 
        title="Episodes" 
        description="Reconstructed troubleshooting episodes from correlated evidence." 
        actions={
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
