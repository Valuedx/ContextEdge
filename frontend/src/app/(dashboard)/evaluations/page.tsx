"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ColumnDef } from "@tanstack/react-table";
import { Loader2, Play, Plus } from "lucide-react";
import { useState } from "react";

import { PageHeader } from "@/components/common/page-header";
import { DataTable } from "@/components/common/data-table";
import { DataTableSkeleton } from "@/components/common/data-table-skeleton";
import { StatusBadge } from "@/components/common/status-badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Textarea } from "@/components/ui/textarea";
import { api } from "@/lib/api";
import type { EvaluationDataset, EvaluationRun } from "@/lib/types";
import { useAuthStore } from "@/lib/stores/auth-store";
import { canManageEval } from "@/lib/roles";
import { CheckCircle, XCircle } from "lucide-react";
import { Separator } from "@/components/ui/separator";

interface EvalCase {
  expected_stable_key: string | null;
  top_stable_key: string | null;
  top1_hit: boolean;
  top_k: Array<{ stable_key: string; score: number }>;
}

function EvalRunResults({ run, onClose }: { run: EvaluationRun; onClose: () => void }) {
  const r = run.results as {
    case_count?: number;
    top1_accuracy?: number;
    cases?: EvalCase[];
    error?: string;
  } | null;

  return (
    <Card>
      <CardHeader className="flex flex-row items-center justify-between">
        <CardTitle className="text-base">
          Run results <span className="font-mono text-xs font-normal text-muted-foreground ml-2">{run.id.slice(0, 8)}…</span>
        </CardTitle>
        <Button variant="outline" size="sm" onClick={onClose}>Close</Button>
      </CardHeader>
      <CardContent className="space-y-4">
        {!r ? (
          <p className="text-sm text-muted-foreground">No results yet — run is {run.status}.</p>
        ) : r.error ? (
          <p className="text-sm text-destructive">Error: {r.error}</p>
        ) : (
          <>
            <div className="flex flex-wrap gap-6 text-sm">
              <div>
                <p className="text-xs text-muted-foreground">Cases</p>
                <p className="text-2xl font-bold">{r.case_count ?? 0}</p>
              </div>
              <div>
                <p className="text-xs text-muted-foreground">Top-1 accuracy</p>
                <p className="text-2xl font-bold">
                  {r.top1_accuracy != null ? (r.top1_accuracy * 100).toFixed(1) + "%" : "—"}
                </p>
              </div>
            </div>
            {Array.isArray(r.cases) && r.cases.length > 0 && (
              <>
                <Separator />
                <div className="overflow-auto">
                  <table className="w-full text-xs">
                    <thead>
                      <tr className="border-b text-left text-muted-foreground">
                        <th className="pb-2 pr-4">#</th>
                        <th className="pb-2 pr-4">Expected</th>
                        <th className="pb-2 pr-4">Got</th>
                        <th className="pb-2 pr-4">Hit</th>
                        <th className="pb-2">Top-5 scores</th>
                      </tr>
                    </thead>
                    <tbody>
                      {r.cases.map((c, i) => (
                        <tr key={i} className="border-b last:border-0">
                          <td className="py-1.5 pr-4 text-muted-foreground">{i + 1}</td>
                          <td className="py-1.5 pr-4 font-mono">{c.expected_stable_key ?? "—"}</td>
                          <td className="py-1.5 pr-4 font-mono">{c.top_stable_key ?? "—"}</td>
                          <td className="py-1.5 pr-4">
                            {c.top1_hit ? (
                              <CheckCircle className="h-4 w-4 text-green-500" />
                            ) : (
                              <XCircle className="h-4 w-4 text-destructive" />
                            )}
                          </td>
                          <td className="py-1.5 text-muted-foreground">
                            {c.top_k.slice(0, 5).map((k) => `${k.stable_key}(${k.score.toFixed(2)})`).join(", ")}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </>
            )}
          </>
        )}
      </CardContent>
    </Card>
  );
}


const runColumns: ColumnDef<EvaluationRun>[] = [
  {
    accessorKey: "status",
    header: "Status",
    cell: ({ row }) => <StatusBadge status={row.getValue("status")} />,
  },
  {
    accessorKey: "dataset_id",
    header: "Dataset",
    cell: ({ row }) => (
      <span className="font-mono text-xs">{row.getValue("dataset_id")}</span>
    ),
  },
  {
    accessorKey: "created_at",
    header: "Created",
    cell: ({ row }) => new Date(row.getValue("created_at") as string).toLocaleString(),
  },
  {
    accessorKey: "completed_at",
    header: "Completed",
    cell: ({ row }) => {
      const v = row.getValue("completed_at") as string | null;
      return v ? new Date(v).toLocaleString() : "—";
    },
  },
];

const datasetColumns: ColumnDef<EvaluationDataset>[] = [
  { accessorKey: "name", header: "Name" },
  {
    accessorKey: "cases",
    header: "Cases",
    cell: ({ row }) => (Array.isArray(row.original.cases) ? row.original.cases.length : 0),
  },
  {
    accessorKey: "updated_at",
    header: "Updated",
    cell: ({ row }) => new Date(row.getValue("updated_at") as string).toLocaleString(),
  },
];

export default function EvaluationsPage() {
  const qc = useQueryClient();
  const roles = useAuthStore((s) => s.roles);
  const manage = canManageEval(roles);

  const [dsOpen, setDsOpen] = useState(false);
  const [runOpen, setRunOpen] = useState(false);
  const [dsName, setDsName] = useState("");
  const [dsDesc, setDsDesc] = useState("");
  const [dsCases, setDsCases] = useState("[]");
  const [runDatasetId, setRunDatasetId] = useState<string>("");
  const [expandedRun, setExpandedRun] = useState<EvaluationRun | null>(null);

  const { data: runs = [], isLoading: runsLoading } = useQuery({
    queryKey: ["eval-runs"],
    queryFn: () => api.get<EvaluationRun[]>("/evaluations/runs"),
  });

  const { data: datasets = [], isLoading: dsLoading } = useQuery({
    queryKey: ["eval-datasets"],
    queryFn: () => api.get<EvaluationDataset[]>("/evaluations/datasets"),
  });

  const createDs = useMutation({
    mutationFn: () => {
      let cases: unknown[] = [];
      try {
        const parsed = JSON.parse(dsCases);
        if (!Array.isArray(parsed)) throw new Error("Cases must be a JSON array");
        cases = parsed;
      } catch {
        throw new Error("Invalid JSON for cases");
      }
      return api.post<EvaluationDataset>("/evaluations/datasets", {
        name: dsName.trim(),
        description: dsDesc.trim() || null,
        cases,
      });
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["eval-datasets"] });
      setDsOpen(false);
      setDsName("");
      setDsDesc("");
      setDsCases("[]");
    },
  });

  const createRun = useMutation({
    mutationFn: () =>
      api.post<EvaluationRun>("/evaluations/runs", {
        dataset_id: runDatasetId,
        config: {},
      }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["eval-runs"] });
      setRunOpen(false);
      setRunDatasetId("");
    },
  });

  return (
    <div className="space-y-6">
      <PageHeader
        title="Evaluations"
        description="Replay historical cases against the current retrieval ranker."
        actions={
          manage ? (
            <div className="flex flex-wrap gap-2">
              <Button variant="outline" onClick={() => setDsOpen(true)}>
                <Plus className="mr-2 h-4 w-4" />
                New dataset
              </Button>
              <Dialog open={dsOpen} onOpenChange={setDsOpen}>
                <DialogContent className="max-w-lg">
                  <DialogHeader>
                    <DialogTitle>Create evaluation dataset</DialogTitle>
                  </DialogHeader>
                  <div className="space-y-3">
                    <div>
                      <Label htmlFor="ds-name">Name</Label>
                      <Input
                        id="ds-name"
                        value={dsName}
                        onChange={(e) => setDsName(e.target.value)}
                        placeholder="VPN disconnect regressions"
                      />
                    </div>
                    <div>
                      <Label htmlFor="ds-desc">Description (optional)</Label>
                      <Input
                        id="ds-desc"
                        value={dsDesc}
                        onChange={(e) => setDsDesc(e.target.value)}
                      />
                    </div>
                    <div>
                      <Label htmlFor="ds-cases">Cases (JSON array)</Label>
                      <Textarea
                        id="ds-cases"
                        className="font-mono text-xs min-h-[140px]"
                        value={dsCases}
                        onChange={(e) => setDsCases(e.target.value)}
                      />
                      <p className="text-xs text-muted-foreground mt-1">
                        Each case may include symptoms, entities, context, and expected_playbook_stable_key.
                      </p>
                    </div>
                  </div>
                  <DialogFooter>
                    <Button
                      disabled={!dsName.trim() || createDs.isPending}
                      onClick={() => createDs.mutate()}
                    >
                      {createDs.isPending && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
                      Create
                    </Button>
                  </DialogFooter>
                  {createDs.error && (
                    <p className="text-sm text-destructive">{String((createDs.error as Error).message)}</p>
                  )}
                </DialogContent>
              </Dialog>

              <Button disabled={datasets.length === 0} onClick={() => setRunOpen(true)}>
                <Play className="mr-2 h-4 w-4" />
                Run evaluation
              </Button>
              <Dialog open={runOpen} onOpenChange={setRunOpen}>
                <DialogContent>
                  <DialogHeader>
                    <DialogTitle>Start evaluation run</DialogTitle>
                  </DialogHeader>
                  <div className="space-y-3">
                    <Label>Dataset</Label>
                    <Select
                      value={runDatasetId}
                      onValueChange={(v) => setRunDatasetId(v ?? "")}
                    >
                      <SelectTrigger>
                        <SelectValue placeholder="Select dataset" />
                      </SelectTrigger>
                      <SelectContent>
                        {datasets.map((d) => (
                          <SelectItem key={d.id} value={d.id}>
                            {d.name}
                          </SelectItem>
                        ))}
                      </SelectContent>
                    </Select>
                  </div>
                  <DialogFooter>
                    <Button
                      disabled={!runDatasetId || createRun.isPending}
                      onClick={() => createRun.mutate()}
                    >
                      {createRun.isPending && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
                      Queue run
                    </Button>
                  </DialogFooter>
                  {createRun.error && (
                    <p className="text-sm text-destructive">{String((createRun.error as Error).message)}</p>
                  )}
                </DialogContent>
              </Dialog>
            </div>
          ) : undefined
        }
      />

      {!manage && (
        <p className="text-sm text-muted-foreground">
          Knowledge manager role is required to create datasets and start runs.
        </p>
      )}

      <Tabs defaultValue="runs">
        <TabsList variant="glass">
          <TabsTrigger value="runs">Runs</TabsTrigger>
          <TabsTrigger value="datasets">Datasets</TabsTrigger>
        </TabsList>

        <TabsContent value="runs" className="mt-3 space-y-4">
          {runsLoading ? (
            <DataTableSkeleton columns={5} />
          ) : runs.length === 0 ? (
            <Card>
              <CardContent className="py-8 text-sm text-muted-foreground">
                No evaluation runs yet.
              </CardContent>
            </Card>
          ) : (
            <>
              <DataTable
                columns={[
                  ...runColumns,
                  {
                    id: "actions",
                    header: "",
                    cell: ({ row }) => (
                      <Button variant="ghost" size="sm" onClick={() => setExpandedRun(row.original)}>
                        Results
                      </Button>
                    ),
                  },
                ]}
                data={runs}
              />
              {expandedRun && (
                <EvalRunResults run={expandedRun} onClose={() => setExpandedRun(null)} />
              )}
            </>
          )}
        </TabsContent>

        <TabsContent value="datasets" className="mt-3">
          {dsLoading ? (
            <DataTableSkeleton columns={3} />
          ) : datasets.length === 0 ? (
            <Card>
              <CardContent className="py-8 text-sm text-muted-foreground">
                No datasets yet.
              </CardContent>
            </Card>
          ) : (
            <DataTable columns={datasetColumns} data={datasets} />
          )}
        </TabsContent>
      </Tabs>
    </div>
  );
}
