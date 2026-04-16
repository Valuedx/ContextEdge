"use client";

import { useMutation, useQuery } from "@tanstack/react-query";
import { ColumnDef } from "@tanstack/react-table";
import { BookOpen, Loader2, Network, List } from "lucide-react";
import { useRouter } from "next/navigation";

import { PageHeader } from "@/components/common/page-header";
import { DataTable } from "@/components/common/data-table";
import { DataTableSkeleton } from "@/components/common/data-table-skeleton";
import { api } from "@/lib/api";
import type { Pattern } from "@/lib/types";
import Link from "next/link";
import { Button } from "@/components/ui/button";
import { toast } from "sonner";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { PatternGraph } from "@/components/patterns/pattern-graph";
import { usePagination } from "@/lib/hooks/use-pagination";
import { PaginationControls } from "@/components/common/pagination-controls";

function PatternActions({ patternId }: { patternId: string; title: string }) {
  const router = useRouter();
  const generateMutation = useMutation({
    mutationFn: () => api.post("/playbooks/generate", { pattern_id: patternId }),
    onSuccess: () => {
      toast.success("Playbook candidate generated!");
      router.push("/playbooks");
    },
    onError: (err: Error) => {
      toast.error(err.message || "Failed to generate playbook");
    },
  });

  return (
    <Button
      variant="ghost"
      size="icon"
      className="text-muted-foreground hover:text-indigo-500"
      title="Generate Playbook"
      onClick={() => generateMutation.mutate()}
      disabled={generateMutation.isPending}
    >
      {generateMutation.isPending ? (
        <Loader2 className="h-4 w-4 animate-spin" />
      ) : (
        <BookOpen className="h-4 w-4" />
      )}
    </Button>
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
    accessorKey: "contradiction_score",
    header: "Contradictions",
    cell: ({ row }) => ((row.getValue("contradiction_score") as number) * 100).toFixed(0) + "%",
  },
  {
    accessorKey: "freshness_score",
    header: "Freshness",
    cell: ({ row }) => ((row.getValue("freshness_score") as number) * 100).toFixed(0) + "%",
  },
  {
    id: "actions",
    cell: ({ row }) => (
      <div className="flex justify-end">
        <PatternActions patternId={row.original.id} title={row.original.title} />
      </div>
    ),
  },
];

export default function PatternsPage() {
  const pg = usePagination(50);
  const { data = [], isLoading } = useQuery<Pattern[]>({
    queryKey: ["patterns", pg.page],
    queryFn: () => api.get("/patterns", pg.params),
  });

  return (
    <div className="space-y-6">
      <PageHeader title="Patterns" description="Operational patterns derived from episode clusters." />
      
      <Tabs defaultValue="list" className="w-full">
        <div className="flex justify-start mb-4">
          <TabsList>
            <TabsTrigger value="list" className="flex items-center gap-2">
              <List className="h-4 w-4" /> List View
            </TabsTrigger>
            <TabsTrigger value="graph" className="flex items-center gap-2">
              <Network className="h-4 w-4" /> Graph View (Clustering)
            </TabsTrigger>
          </TabsList>
        </div>

        <TabsContent value="list" className="border-none p-0 outline-none">
          {isLoading ? (
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
          )}
        </TabsContent>

        <TabsContent value="graph" className="border-none p-0 outline-none">
          {data.length > 0 ? (
            <div className="grid grid-cols-1 gap-6">
              {data.map((pattern) => (
                <div key={pattern.id} className="space-y-3">
                  <div className="flex items-center justify-between">
                    <h3 className="text-lg font-semibold text-slate-200">{pattern.title}</h3>
                    <div className="flex items-center gap-4 text-sm text-slate-400">
                      <span>{pattern.episode_count} Episodes</span>
                      <span>{((pattern.confidence) * 100).toFixed(0)}% Confidence</span>
                    </div>
                  </div>
                  <PatternGraph patternId={pattern.id} />
                </div>
              ))}
            </div>
          ) : (
             <div className="flex flex-col items-center justify-center p-12 border rounded-xl bg-slate-900/50">
                <Network className="h-12 w-12 text-slate-600 mb-4" />
                <p className="text-slate-400">Analyze an episode first to discover a pattern graph.</p>
             </div>
          )}
        </TabsContent>
      </Tabs>
    </div>
  );
}
