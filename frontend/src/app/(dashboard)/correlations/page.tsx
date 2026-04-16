"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ColumnDef } from "@tanstack/react-table";
import { useState } from "react";
import { toast } from "sonner";
import { Plus, Trash2, CheckCircle2 } from "lucide-react";

import { PageHeader } from "@/components/common/page-header";
import { DataTable } from "@/components/common/data-table";
import { DataTableSkeleton } from "@/components/common/data-table-skeleton";
import { PaginationControls } from "@/components/common/pagination-controls";
import { usePagination } from "@/lib/hooks/use-pagination";
import { Button } from "@/components/ui/button";
import {
  Dialog, DialogContent, DialogFooter, DialogHeader, DialogTitle,
} from "@/components/ui/dialog";
import { Label } from "@/components/ui/label";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { Badge } from "@/components/ui/badge";
import { api } from "@/lib/api";
import type { CorrelationEdge } from "@/lib/types";

const CORRELATION_TYPES = ["causal", "temporal", "semantic", "duplicate", "contradicts", "supports"];
const DECISIONS = ["accept", "reject", "merge", "split"] as const;

// ── Create Dialog ────────────────────────────────────────────────────────────

function CreateDialog({ onClose }: { onClose: () => void }) {
  const qc = useQueryClient();
  const [sourceId, setSourceId] = useState("");
  const [targetId, setTargetId] = useState("");
  const [corrType, setCorrType] = useState("semantic");
  const [confidence, setConfidence] = useState(0.5);
  const [explanation, setExplanation] = useState("");

  const mut = useMutation({
    mutationFn: () =>
      api.post("/correlations", {
        source_evidence_id: sourceId.trim(),
        target_evidence_id: targetId.trim(),
        correlation_type: corrType,
        confidence,
        explanation: explanation.trim() || null,
      }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["correlations"] });
      toast.success("Correlation created");
      onClose();
    },
    onError: (err: Error) => toast.error(err.message || "Create failed"),
  });

  const valid = sourceId.trim() && targetId.trim();

  return (
    <DialogContent>
      <DialogHeader>
        <DialogTitle>Create correlation</DialogTitle>
      </DialogHeader>
      <div className="space-y-4 text-sm">
        <div>
          <Label htmlFor="cor-src">Source evidence ID</Label>
          <Input
            id="cor-src"
            className="mt-1 font-mono text-xs"
            value={sourceId}
            onChange={(e) => setSourceId(e.target.value)}
            placeholder="UUID"
          />
        </div>
        <div>
          <Label htmlFor="cor-tgt">Target evidence ID</Label>
          <Input
            id="cor-tgt"
            className="mt-1 font-mono text-xs"
            value={targetId}
            onChange={(e) => setTargetId(e.target.value)}
            placeholder="UUID"
          />
        </div>
        <div>
          <Label>Type</Label>
          <select
            className="mt-1 w-full rounded-md border bg-background px-3 py-2 text-sm"
            value={corrType}
            onChange={(e) => setCorrType(e.target.value)}
          >
            {CORRELATION_TYPES.map((t) => (
              <option key={t} value={t}>{t}</option>
            ))}
          </select>
        </div>
        <div>
          <Label htmlFor="cor-conf">Confidence (0–1)</Label>
          <Input
            id="cor-conf"
            type="number"
            min="0"
            max="1"
            step="0.05"
            className="mt-1"
            value={confidence}
            onChange={(e) => setConfidence(parseFloat(e.target.value) || 0)}
          />
        </div>
        <div>
          <Label htmlFor="cor-exp">Explanation</Label>
          <Textarea
            id="cor-exp"
            className="mt-1"
            rows={2}
            value={explanation}
            onChange={(e) => setExplanation(e.target.value)}
          />
        </div>
      </div>
      <DialogFooter>
        <Button variant="outline" onClick={onClose}>Cancel</Button>
        <Button disabled={mut.isPending || !valid} onClick={() => mut.mutate()}>
          {mut.isPending ? "Creating…" : "Create"}
        </Button>
      </DialogFooter>
    </DialogContent>
  );
}

// ── Review Dialog ────────────────────────────────────────────────────────────

function ReviewDialog({
  item,
  onClose,
}: {
  item: CorrelationEdge;
  onClose: () => void;
}) {
  const qc = useQueryClient();
  const [decision, setDecision] = useState<typeof DECISIONS[number]>("accept");
  const [confidence, setConfidence] = useState(item.confidence);
  const [explanation, setExplanation] = useState(item.explanation ?? "");

  const mut = useMutation({
    mutationFn: () =>
      api.post(`/correlations/${item.id}/decision`, {
        decision,
        confidence,
        explanation: explanation.trim() || null,
      }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["correlations"] });
      toast.success("Decision recorded");
      onClose();
    },
    onError: (err: Error) => toast.error(err.message || "Decision failed"),
  });

  return (
    <DialogContent>
      <DialogHeader>
        <DialogTitle>Review correlation</DialogTitle>
      </DialogHeader>
      <div className="space-y-4 text-sm">
        <div className="rounded-md bg-muted px-3 py-2 space-y-1">
          <p className="text-xs text-muted-foreground">Source → Target</p>
          <p className="font-mono text-xs break-all">{item.source_evidence_id}</p>
          <p className="text-xs text-muted-foreground">→</p>
          <p className="font-mono text-xs break-all">{item.target_evidence_id}</p>
        </div>
        <div>
          <p className="text-xs text-muted-foreground mb-1">
            Type: <Badge variant="outline" className="text-xs">{item.correlation_type}</Badge>
          </p>
        </div>
        <div>
          <Label>Decision</Label>
          <div className="mt-1 flex flex-wrap gap-2">
            {DECISIONS.map((d) => (
              <Button
                key={d}
                size="sm"
                variant={decision === d ? "default" : "outline"}
                onClick={() => setDecision(d)}
              >
                {d}
              </Button>
            ))}
          </div>
        </div>
        <div>
          <Label htmlFor="cor-conf">Confidence (0–1)</Label>
          <Input
            id="cor-conf"
            type="number"
            min="0"
            max="1"
            step="0.05"
            className="mt-1"
            value={confidence}
            onChange={(e) => setConfidence(parseFloat(e.target.value) || 0)}
          />
        </div>
        <div>
          <Label htmlFor="rev-exp">Explanation</Label>
          <Textarea
            id="rev-exp"
            className="mt-1"
            rows={2}
            value={explanation}
            onChange={(e) => setExplanation(e.target.value)}
          />
        </div>
      </div>
      <DialogFooter>
        <Button variant="outline" onClick={onClose}>Cancel</Button>
        <Button disabled={mut.isPending} onClick={() => mut.mutate()}>
          {mut.isPending ? "Submitting…" : "Submit decision"}
        </Button>
      </DialogFooter>
    </DialogContent>
  );
}

// ── Page ────────────────────────────────────────────────────────────────────

export default function CorrelationsPage() {
  const pg = usePagination(50);
  const [creating, setCreating] = useState(false);
  const [reviewing, setReviewing] = useState<CorrelationEdge | null>(null);
  const qc = useQueryClient();

  const { data = [], isLoading } = useQuery<CorrelationEdge[]>({
    queryKey: ["correlations", pg.page],
    queryFn: () => api.get("/correlations", pg.params),
  });

  const del = useMutation({
    mutationFn: (id: string) => api.delete(`/correlations/${id}`),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["correlations"] });
      toast.success("Correlation deleted");
    },
    onError: (err: Error) => toast.error(err.message || "Delete failed"),
  });

  const columns: ColumnDef<CorrelationEdge>[] = [
    {
      accessorKey: "correlation_type",
      header: "Type",
      cell: ({ row }) => (
        <Badge variant="outline" className="text-xs">{row.getValue("correlation_type")}</Badge>
      ),
    },
    {
      accessorKey: "source_evidence_id",
      header: "Source",
      cell: ({ row }) => (
        <span className="font-mono text-xs truncate max-w-[140px] block">{row.getValue("source_evidence_id")}</span>
      ),
    },
    {
      accessorKey: "target_evidence_id",
      header: "Target",
      cell: ({ row }) => (
        <span className="font-mono text-xs truncate max-w-[140px] block">{row.getValue("target_evidence_id")}</span>
      ),
    },
    {
      accessorKey: "confidence",
      header: "Confidence",
      cell: ({ row }) => (
        <span className="text-sm">{((row.getValue("confidence") as number) * 100).toFixed(0)}%</span>
      ),
    },
    {
      accessorKey: "explanation",
      header: "Explanation",
      cell: ({ row }) => (
        <span className="text-xs text-muted-foreground line-clamp-1 max-w-[180px]">
          {row.getValue("explanation") ?? "—"}
        </span>
      ),
    },
    {
      id: "actions",
      header: "",
      cell: ({ row }) => (
        <div className="flex items-center gap-1">
          <Button variant="ghost" size="icon" onClick={() => setReviewing(row.original)}>
            <CheckCircle2 className="h-3.5 w-3.5" />
          </Button>
          <Button
            variant="ghost"
            size="icon"
            onClick={() => del.mutate(row.original.id)}
            disabled={del.isPending}
          >
            <Trash2 className="h-3.5 w-3.5 text-destructive" />
          </Button>
        </div>
      ),
    },
  ];

  return (
    <div className="space-y-6">
      <PageHeader
        title="Correlations"
        description="Evidence correlation edges — causal, temporal, semantic, and structural links between evidence items."
        actions={
          <Button size="sm" onClick={() => setCreating(true)}>
            <Plus className="mr-1.5 h-4 w-4" />
            Add correlation
          </Button>
        }
      />

      {isLoading ? (
        <DataTableSkeleton columns={6} />
      ) : data.length === 0 ? (
        <div className="rounded-md border p-10 text-center text-sm text-muted-foreground">
          No correlations found.
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

      <Dialog open={creating} onOpenChange={(o) => { if (!o) setCreating(false); }}>
        {creating && <CreateDialog onClose={() => setCreating(false)} />}
      </Dialog>

      <Dialog open={!!reviewing} onOpenChange={(o) => { if (!o) setReviewing(null); }}>
        {reviewing && <ReviewDialog item={reviewing} onClose={() => setReviewing(null)} />}
      </Dialog>
    </div>
  );
}
