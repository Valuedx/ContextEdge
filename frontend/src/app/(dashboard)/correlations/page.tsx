"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ColumnDef } from "@tanstack/react-table";
import { useState } from "react";
import { toast } from "sonner";
import { CheckCircle2, Plus, Trash2 } from "lucide-react";

import { PageHeader } from "@/components/common/page-header";
import { DataTable } from "@/components/common/data-table";
import { DataTableSkeleton } from "@/components/common/data-table-skeleton";
import { PaginationControls } from "@/components/common/pagination-controls";
import { usePagination } from "@/lib/hooks/use-pagination";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Label } from "@/components/ui/label";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { Badge } from "@/components/ui/badge";
import { SearchableSelect } from "@/components/common/searchable-select";
import { api } from "@/lib/api";
import type { CorrelationEdge, EvidenceItem } from "@/lib/types";

const CORRELATION_TYPES = [
  "causal",
  "temporal",
  "semantic",
  "duplicate",
  "contradicts",
  "supports",
];
const DECISIONS = ["accept", "reject", "merge", "split"] as const;

function shortId(id: string): string {
  return id.length > 8 ? id.slice(0, 8) : id;
}

function evidenceTitle(item: EvidenceItem | undefined, fallbackId: string): string {
  if (!item) return `Evidence ${shortId(fallbackId)}`;
  return item.title || item.body_summary || "Untitled evidence";
}

function evidenceMeta(item: EvidenceItem): string {
  return `${item.evidence_type} - ${new Date(item.ingested_at).toLocaleString()}`;
}

function EvidenceSelect({
  disabledId,
  evidence,
  isLoading,
  label,
  onValueChange,
  placeholder,
  value,
}: {
  disabledId?: string;
  evidence: EvidenceItem[];
  isLoading: boolean;
  label: string;
  onValueChange: (value: string) => void;
  placeholder: string;
  value: string;
}) {
  return (
    <div>
      <Label>{label}</Label>
      <SearchableSelect
        value={value}
        onValueChange={onValueChange}
        disabled={isLoading || evidence.length === 0}
        loading={isLoading}
        placeholder={placeholder}
        searchPlaceholder="Search evidence..."
        emptyText="No evidence found."
        className="mt-1"
        options={evidence.map((item) => ({
          value: item.id,
          label: evidenceTitle(item, item.id),
          meta: evidenceMeta(item),
          disabled: item.id === disabledId,
        }))}
      />
    </div>
  );
}

function CreateDialog({
  evidence,
  evidenceLoading,
  onClose,
}: {
  evidence: EvidenceItem[];
  evidenceLoading: boolean;
  onClose: () => void;
}) {
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

  const valid = sourceId.trim() && targetId.trim() && sourceId !== targetId;

  return (
    <DialogContent className="max-w-2xl">
      <DialogHeader>
        <DialogTitle>Create correlation</DialogTitle>
      </DialogHeader>
      <div className="space-y-4 text-sm">
        <EvidenceSelect
          evidence={evidence}
          isLoading={evidenceLoading}
          label="Source evidence"
          placeholder="Select source evidence"
          value={sourceId}
          disabledId={targetId}
          onValueChange={setSourceId}
        />
        <EvidenceSelect
          evidence={evidence}
          isLoading={evidenceLoading}
          label="Target evidence"
          placeholder="Select target evidence"
          value={targetId}
          disabledId={sourceId}
          onValueChange={setTargetId}
        />
        {evidence.length === 0 && !evidenceLoading ? (
          <p className="text-xs text-muted-foreground">
            No evidence found yet. Create or sync evidence before adding a correlation.
          </p>
        ) : null}
        <div>
          <Label>Type</Label>
          <select
            className="mt-1 w-full rounded-md border bg-background px-3 py-2 text-sm"
            value={corrType}
            onChange={(e) => setCorrType(e.target.value)}
          >
            {CORRELATION_TYPES.map((type) => (
              <option key={type} value={type}>
                {type}
              </option>
            ))}
          </select>
        </div>
        <div>
          <Label htmlFor="cor-conf">Confidence (0-1)</Label>
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
        <Button variant="outline" onClick={onClose}>
          Cancel
        </Button>
        <Button disabled={mut.isPending || !valid} onClick={() => mut.mutate()}>
          {mut.isPending ? "Creating..." : "Create"}
        </Button>
      </DialogFooter>
    </DialogContent>
  );
}

function ReviewDialog({
  evidenceById,
  item,
  onClose,
}: {
  evidenceById: Map<string, EvidenceItem>;
  item: CorrelationEdge;
  onClose: () => void;
}) {
  const qc = useQueryClient();
  const [decision, setDecision] = useState<(typeof DECISIONS)[number]>("accept");
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
        <div className="space-y-1 rounded-md bg-muted px-3 py-2">
          <p className="text-xs text-muted-foreground">Source to target</p>
          <p className="font-medium">
            {evidenceTitle(evidenceById.get(item.source_evidence_id), item.source_evidence_id)}
          </p>
          <p className="text-xs text-muted-foreground">to</p>
          <p className="font-medium">
            {evidenceTitle(evidenceById.get(item.target_evidence_id), item.target_evidence_id)}
          </p>
        </div>
        <p className="text-xs text-muted-foreground">
          Type: <Badge variant="outline" className="text-xs">{item.correlation_type}</Badge>
        </p>
        <div>
          <Label>Decision</Label>
          <div className="mt-1 flex flex-wrap gap-2">
            {DECISIONS.map((itemDecision) => (
              <Button
                key={itemDecision}
                size="sm"
                variant={decision === itemDecision ? "default" : "outline"}
                onClick={() => setDecision(itemDecision)}
              >
                {itemDecision}
              </Button>
            ))}
          </div>
        </div>
        <div>
          <Label htmlFor="rev-conf">Confidence (0-1)</Label>
          <Input
            id="rev-conf"
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
        <Button variant="outline" onClick={onClose}>
          Cancel
        </Button>
        <Button disabled={mut.isPending} onClick={() => mut.mutate()}>
          {mut.isPending ? "Submitting..." : "Submit decision"}
        </Button>
      </DialogFooter>
    </DialogContent>
  );
}

export default function CorrelationsPage() {
  const pg = usePagination(50);
  const [creating, setCreating] = useState(false);
  const [reviewing, setReviewing] = useState<CorrelationEdge | null>(null);
  const qc = useQueryClient();

  const { data = [], isLoading } = useQuery<CorrelationEdge[]>({
    queryKey: ["correlations", pg.page],
    queryFn: () => api.get("/correlations", pg.params),
  });

  const { data: evidence = [], isLoading: evidenceLoading } = useQuery<EvidenceItem[]>({
    queryKey: ["evidence", "correlations-selector"],
    queryFn: () => api.get("/evidence", { limit: "200" }),
  });

  const evidenceById = new Map(evidence.map((item) => [item.id, item]));

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
      cell: ({ row }) => {
        const id = row.getValue("source_evidence_id") as string;
        const item = evidenceById.get(id);
        return (
          <div className="max-w-[280px]">
            <span className="block truncate text-sm font-medium">
              {evidenceTitle(item, id)}
            </span>
            {item ? (
              <span className="text-xs text-muted-foreground">{item.evidence_type}</span>
            ) : null}
          </div>
        );
      },
    },
    {
      accessorKey: "target_evidence_id",
      header: "Target",
      cell: ({ row }) => {
        const id = row.getValue("target_evidence_id") as string;
        const item = evidenceById.get(id);
        return (
          <div className="max-w-[280px]">
            <span className="block truncate text-sm font-medium">
              {evidenceTitle(item, id)}
            </span>
            {item ? (
              <span className="text-xs text-muted-foreground">{item.evidence_type}</span>
            ) : null}
          </div>
        );
      },
    },
    {
      accessorKey: "confidence",
      header: "Confidence",
      cell: ({ row }) => (
        <span className="text-sm">
          {((row.getValue("confidence") as number) * 100).toFixed(0)}%
        </span>
      ),
    },
    {
      accessorKey: "explanation",
      header: "Explanation",
      cell: ({ row }) => (
        <span className="line-clamp-1 max-w-[240px] text-xs text-muted-foreground">
          {row.getValue("explanation") ?? "-"}
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
        description="Evidence links that show related issues, matching symptoms, or supporting facts."
        actions={
          <Button size="sm" onClick={() => setCreating(true)}>
            <Plus className="mr-1.5 h-4 w-4" />
            Add correlation
          </Button>
        }
      />

      {isLoading || evidenceLoading ? (
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

      <Dialog open={creating} onOpenChange={(open) => { if (!open) setCreating(false); }}>
        {creating && (
          <CreateDialog
            evidence={evidence}
            evidenceLoading={evidenceLoading}
            onClose={() => setCreating(false)}
          />
        )}
      </Dialog>

      <Dialog open={!!reviewing} onOpenChange={(open) => { if (!open) setReviewing(null); }}>
        {reviewing && (
          <ReviewDialog
            evidenceById={evidenceById}
            item={reviewing}
            onClose={() => setReviewing(null)}
          />
        )}
      </Dialog>
    </div>
  );
}
