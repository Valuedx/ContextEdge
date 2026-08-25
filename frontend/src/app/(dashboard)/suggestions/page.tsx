"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ColumnDef } from "@tanstack/react-table";
import { toast } from "sonner";
import { Check, ExternalLink, Eye, Loader2, Sparkles, X } from "lucide-react";
import Link from "next/link";
import { useState } from "react";

import { PageHeader } from "@/components/common/page-header";
import { DataTable } from "@/components/common/data-table";
import { DataTableSkeleton } from "@/components/common/data-table-skeleton";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { api } from "@/lib/api";
import type { EvidenceItem, EvidenceItemDetail } from "@/lib/types";

type SemanticSuggestion = {
  id: string;
  evidence_id_low: string;
  evidence_id_high: string;
  similarity: number;
  corroborators: string[];
  status: string;
  created_at: string;
};

type FleetSuggestion = {
  id: string;
  change_ref: string;
  member_count: number;
  member_evidence_ids: string[];
  status: string;
  created_at: string;
};

function EvidenceSideCard({
  label,
  evidenceId,
}: {
  label: string;
  evidenceId: string;
}) {
  const { data: item, isLoading, error } = useQuery<EvidenceItemDetail>({
    queryKey: ["evidence-detail", evidenceId],
    queryFn: () => api.get<EvidenceItemDetail>(`/evidence/${evidenceId}`),
    enabled: !!evidenceId,
  });

  if (isLoading) {
    return (
      <div className="space-y-3 rounded-lg border bg-card p-4">
        <Skeleton className="h-5 w-32" />
        <Skeleton className="h-6 w-full" />
        <Skeleton className="h-16 w-full" />
        <Skeleton className="h-36 w-full" />
      </div>
    );
  }

  if (error || !item) {
    return (
      <div className="rounded-lg border border-destructive/30 bg-destructive/10 p-4 text-xs text-destructive">
        Failed to load evidence ({evidenceId.slice(0, 8)}): {(error as Error)?.message || "Record not found"}
      </div>
    );
  }

  const displayId = item.source_reference?.display_id || item.source_reference?.external_id;
  const sourceUrl = item.source_reference?.url;

  return (
    <div className="flex h-full flex-col space-y-3 rounded-lg border bg-card/70 p-4 shadow-sm">
      <div className="flex items-center justify-between gap-2 border-b pb-2.5">
        <div className="flex flex-wrap items-center gap-1.5">
          <Badge variant="secondary" className="text-xs font-semibold uppercase tracking-wider">
            {label}
          </Badge>
          {item.source_type && (
            <Badge variant="outline" className="text-xs capitalize">
              {item.source_type}
            </Badge>
          )}
          {item.evidence_type && (
            <Badge variant="outline" className="text-xs">
              {item.evidence_type}
            </Badge>
          )}
        </div>
        <Link
          href={`/evidence/${item.id}`}
          target="_blank"
          className="inline-flex items-center gap-1 text-xs text-primary hover:underline"
          title="Open complete evidence record in new tab"
        >
          <span>Open record</span>
          <ExternalLink className="h-3 w-3" />
        </Link>
      </div>

      <div>
        <h4 className="text-sm font-semibold leading-snug text-foreground">
          {item.title || item.body_summary || `Evidence ${item.id.slice(0, 8)}`}
        </h4>
        <div className="mt-1 flex flex-wrap items-center gap-x-3 gap-y-1 font-mono text-xs text-muted-foreground">
          <span>UUID: {item.id.slice(0, 8)}…</span>
          {displayId && (
            <span>
              Ticket:{" "}
              {sourceUrl ? (
                <a
                  href={sourceUrl}
                  target="_blank"
                  rel="noreferrer"
                  className="text-primary underline hover:text-primary/80"
                >
                  {displayId}
                </a>
              ) : (
                displayId
              )}
            </span>
          )}
          {item.created_at_source && (
            <span>Created: {new Date(item.created_at_source).toLocaleDateString()}</span>
          )}
        </div>
      </div>

      {item.body_summary && (
        <div className="rounded-md border border-border/50 bg-muted/40 p-2.5 text-xs text-foreground/90">
          <span className="mb-1 block text-[10px] font-bold uppercase tracking-wider text-muted-foreground">
            Summary
          </span>
          <p className="line-clamp-3 leading-relaxed">{item.body_summary}</p>
        </div>
      )}

      <div className="flex min-h-0 flex-1 flex-col">
        <span className="mb-1 block text-[10px] font-bold uppercase tracking-wider text-muted-foreground">
          Raw Evidence Body
        </span>
        <div className="h-44 select-text overflow-y-auto rounded-md border bg-background/80 p-2.5 font-mono text-xs leading-relaxed text-foreground whitespace-pre-wrap">
          {item.body_text || item.body_summary || "No raw text available for this record."}
        </div>
      </div>
    </div>
  );
}

function SemanticComparisonDialog({
  suggestion,
  open,
  onOpenChange,
  onAccept,
  onReject,
  isDeciding,
}: {
  suggestion: SemanticSuggestion | null;
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onAccept: (id: string) => void;
  onReject: (id: string) => void;
  isDeciding: boolean;
}) {
  if (!suggestion) return null;

  const simPct = Math.round(suggestion.similarity * 100);

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="flex max-h-[92vh] max-w-5xl flex-col overflow-hidden p-0 sm:max-w-5xl">
        <DialogHeader className="border-b bg-card p-4 sm:p-5 pb-3">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <div>
              <DialogTitle className="flex items-center gap-2 text-base font-semibold">
                <Sparkles className="h-4 w-4 text-primary" />
                Semantic Suggestion Comparison
              </DialogTitle>
              <DialogDescription className="mt-0.5 text-xs text-muted-foreground">
                Compare Evidence A (Left) and Evidence B (Right) to determine if they correlate to the same incident.
              </DialogDescription>
            </div>
            <div className="flex flex-wrap items-center gap-1.5">
              <Badge
                variant="outline"
                className={`px-2.5 py-1 text-xs font-semibold ${
                  simPct >= 80
                    ? "border-emerald-500/30 bg-emerald-500/15 text-emerald-600 dark:text-emerald-400"
                    : simPct >= 70
                    ? "border-sky-500/30 bg-sky-500/15 text-sky-600 dark:text-sky-400"
                    : "border-amber-500/30 bg-amber-500/15 text-amber-600 dark:text-amber-400"
                }`}
              >
                {simPct}% Similarity Match
              </Badge>
              {suggestion.corroborators.map((c) => (
                <Badge key={c} variant="secondary" className="text-xs">
                  {c.replace(/_/g, " ")}
                </Badge>
              ))}
            </div>
          </div>
        </DialogHeader>

        <div className="flex-1 overflow-y-auto p-4 sm:p-5">
          <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
            <EvidenceSideCard label="Evidence A" evidenceId={suggestion.evidence_id_low} />
            <EvidenceSideCard label="Evidence B" evidenceId={suggestion.evidence_id_high} />
          </div>
        </div>

        <DialogFooter className="flex flex-col items-center justify-between gap-3 border-t bg-muted/30 p-3 sm:flex-row sm:p-4">
          <p className="text-center text-xs text-muted-foreground sm:text-left">
            Accepting mints a verified correlation edge. Rejecting dismisses this pairing permanently.
          </p>
          <div className="flex w-full items-center justify-end gap-2 sm:w-auto">
            <Button
              variant="outline"
              size="sm"
              className="border-destructive/30 text-xs text-destructive hover:bg-destructive/10"
              disabled={isDeciding}
              onClick={() => {
                onReject(suggestion.id);
                onOpenChange(false);
              }}
            >
              {isDeciding ? <Loader2 className="mr-1.5 h-3.5 w-3.5 animate-spin" /> : <X className="mr-1.5 h-3.5 w-3.5" />}
              Reject Pair
            </Button>
            <Button
              size="sm"
              className="bg-emerald-600 text-xs text-white hover:bg-emerald-500"
              disabled={isDeciding}
              onClick={() => {
                onAccept(suggestion.id);
                onOpenChange(false);
              }}
            >
              {isDeciding ? <Loader2 className="mr-1.5 h-3.5 w-3.5 animate-spin" /> : <Check className="mr-1.5 h-3.5 w-3.5" />}
              Accept &amp; Create Edge
            </Button>
          </div>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

function SemanticQueue() {
  const qc = useQueryClient();
  const [selectedSuggestion, setSelectedSuggestion] = useState<SemanticSuggestion | null>(null);

  const { data = [], isLoading } = useQuery<SemanticSuggestion[]>({
    queryKey: ["suggestions", "pending"],
    queryFn: () => api.get("/correlations/suggestions", { status: "pending" }),
  });

  const allEvidenceIds = Array.from(
    new Set(data.flatMap((s) => [s.evidence_id_low, s.evidence_id_high])),
  );

  const { data: evidenceMap = new Map<string, EvidenceItem>() } = useQuery({
    queryKey: ["suggestions-evidence-map", allEvidenceIds.join(",")],
    queryFn: async () => {
      const entries = await Promise.allSettled(
        allEvidenceIds.map(async (id) => {
          const item = await api.get<EvidenceItem>(`/evidence/${id}`);
          return [id, item] as const;
        }),
      );
      const map = new Map<string, EvidenceItem>();
      for (const entry of entries) {
        if (entry.status === "fulfilled" && entry.value[1]) {
          map.set(entry.value[0], entry.value[1]);
        }
      }
      return map;
    },
    enabled: allEvidenceIds.length > 0,
  });

  const decide = useMutation({
    mutationFn: ({ id, action }: { id: string; action: "accept" | "reject" }) =>
      api.post(`/correlations/suggestions/${id}/${action}`, {}),
    onSuccess: (_data, vars) => {
      qc.invalidateQueries({ queryKey: ["suggestions"] });
      toast.success(vars.action === "accept" ? "Suggestion accepted — correlation edge created" : "Suggestion rejected");
      if (selectedSuggestion?.id === vars.id) {
        setSelectedSuggestion(null);
      }
    },
    onError: (err: Error) => toast.error(err.message || "Decision failed"),
  });

  const columns: ColumnDef<SemanticSuggestion>[] = [
    {
      accessorKey: "evidence_id_low",
      header: "Evidence A",
      cell: ({ row }) => {
        const id = row.original.evidence_id_low;
        const item = evidenceMap.get(id);
        return (
          <button
            type="button"
            onClick={() => setSelectedSuggestion(row.original)}
            className="group/ev block max-w-[200px] text-left"
            title="Click to compare pair side-by-side"
          >
            <span className="block truncate text-xs font-medium text-foreground group-hover/ev:text-primary group-hover/ev:underline">
              {item?.title || item?.body_summary || `Evidence ${id.slice(0, 8)}`}
            </span>
            <span className="block truncate font-mono text-[11px] text-muted-foreground">
              {item?.source_type ? `${item.source_type} · ` : ""}{id.slice(0, 8)}…
            </span>
          </button>
        );
      },
    },
    {
      accessorKey: "evidence_id_high",
      header: "Evidence B",
      cell: ({ row }) => {
        const id = row.original.evidence_id_high;
        const item = evidenceMap.get(id);
        return (
          <button
            type="button"
            onClick={() => setSelectedSuggestion(row.original)}
            className="group/ev block max-w-[200px] text-left"
            title="Click to compare pair side-by-side"
          >
            <span className="block truncate text-xs font-medium text-foreground group-hover/ev:text-primary group-hover/ev:underline">
              {item?.title || item?.body_summary || `Evidence ${id.slice(0, 8)}`}
            </span>
            <span className="block truncate font-mono text-[11px] text-muted-foreground">
              {item?.source_type ? `${item.source_type} · ` : ""}{id.slice(0, 8)}…
            </span>
          </button>
        );
      },
    },
    {
      accessorKey: "similarity",
      header: "Similarity",
      cell: ({ row }) => {
        const pct = Math.round((row.getValue("similarity") as number) * 100);
        return (
          <span
            className={`inline-flex items-center rounded-full px-2 py-0.5 text-xs font-semibold ${
              pct >= 80
                ? "bg-emerald-500/15 text-emerald-600 dark:text-emerald-400"
                : pct >= 70
                ? "bg-sky-500/15 text-sky-600 dark:text-sky-400"
                : "bg-amber-500/15 text-amber-600 dark:text-amber-400"
            }`}
          >
            {pct}%
          </span>
        );
      },
    },
    {
      accessorKey: "corroborators",
      header: "Corroborators",
      cell: ({ row }) => (
        <div className="flex flex-wrap gap-1">
          {(row.getValue("corroborators") as string[]).map((c) => (
            <Badge key={c} variant="outline" className="text-xs">
              {c.split(":")[0]}
            </Badge>
          ))}
        </div>
      ),
    },
    {
      id: "actions",
      header: "Actions",
      cell: ({ row }) => (
        <div className="flex items-center gap-1.5">
          <Button
            variant="outline"
            size="sm"
            className="h-7 gap-1 px-2.5 text-xs font-normal"
            title="Compare Evidence A and B side-by-side"
            onClick={() => setSelectedSuggestion(row.original)}
          >
            <Eye className="h-3.5 w-3.5 text-muted-foreground" />
            <span>Compare</span>
          </Button>
          <Button
            variant="ghost"
            size="icon"
            className="h-7 w-7 text-emerald-600 hover:bg-emerald-500/10 hover:text-emerald-700"
            title="Accept — creates a correlation edge"
            disabled={decide.isPending}
            onClick={() => decide.mutate({ id: row.original.id, action: "accept" })}
          >
            <Check className="h-3.5 w-3.5" />
          </Button>
          <Button
            variant="ghost"
            size="icon"
            className="h-7 w-7 text-destructive hover:bg-destructive/10 hover:text-destructive"
            title="Reject — permanent for this pair"
            disabled={decide.isPending}
            onClick={() => decide.mutate({ id: row.original.id, action: "reject" })}
          >
            <X className="h-3.5 w-3.5" />
          </Button>
        </div>
      ),
    },
  ];

  if (isLoading) return <DataTableSkeleton columns={5} />;
  if (data.length === 0)
    return (
      <div className="rounded-md border p-10 text-center text-sm text-muted-foreground">
        No pending semantic suggestions.
      </div>
    );

  return (
    <>
      <DataTable columns={columns} data={data} />
      <SemanticComparisonDialog
        suggestion={selectedSuggestion}
        open={Boolean(selectedSuggestion)}
        onOpenChange={(open) => {
          if (!open) setSelectedSuggestion(null);
        }}
        onAccept={(id) => decide.mutate({ id, action: "accept" })}
        onReject={(id) => decide.mutate({ id, action: "reject" })}
        isDeciding={decide.isPending}
      />
    </>
  );
}

function FleetQueue() {
  const qc = useQueryClient();
  const [selectedFleet, setSelectedFleet] = useState<FleetSuggestion | null>(null);

  const { data = [], isLoading } = useQuery<FleetSuggestion[]>({
    queryKey: ["fleet-suggestions", "pending"],
    queryFn: () => api.get("/correlations/fleet-suggestions", { status: "pending" }),
  });

  const decide = useMutation({
    mutationFn: ({ id, action }: { id: string; action: "accept" | "reject" }) =>
      api.post(`/correlations/fleet-suggestions/${id}/${action}`, {}),
    onSuccess: (_data, vars) => {
      qc.invalidateQueries({ queryKey: ["fleet-suggestions"] });
      toast.success(
        vars.action === "accept" ? "Fleet group accepted — parent case created" : "Fleet group rejected",
      );
      if (selectedFleet?.id === vars.id) {
        setSelectedFleet(null);
      }
    },
    onError: (err: Error) => toast.error(err.message || "Decision failed"),
  });

  const columns: ColumnDef<FleetSuggestion>[] = [
    {
      accessorKey: "change_ref",
      header: "Blamed change",
      cell: ({ row }) => (
        <button
          type="button"
          onClick={() => setSelectedFleet(row.original)}
          className="text-left font-mono text-xs text-foreground hover:text-primary hover:underline truncate max-w-[200px] block"
          title="Click to view member incident records"
        >
          {row.getValue("change_ref")}
        </button>
      ),
    },
    {
      accessorKey: "member_count",
      header: "Incidents",
      cell: ({ row }) => (
        <button
          type="button"
          onClick={() => setSelectedFleet(row.original)}
          className="inline-flex items-center gap-1 hover:opacity-80"
        >
          <Badge variant="secondary">{row.getValue("member_count")} incidents</Badge>
        </button>
      ),
    },
    {
      id: "actions",
      header: "Actions",
      cell: ({ row }) => (
        <div className="flex items-center gap-1.5">
          <Button
            variant="outline"
            size="sm"
            className="h-7 gap-1 px-2.5 text-xs font-normal"
            title="Inspect member incident IDs"
            onClick={() => setSelectedFleet(row.original)}
          >
            <Eye className="h-3.5 w-3.5 text-muted-foreground" />
            <span>Inspect</span>
          </Button>
          <Button
            variant="ghost"
            size="icon"
            className="h-7 w-7 text-emerald-600 hover:bg-emerald-500/10 hover:text-emerald-700"
            title="Accept — groups these incidents under one parent case"
            disabled={decide.isPending}
            onClick={() => decide.mutate({ id: row.original.id, action: "accept" })}
          >
            <Check className="h-3.5 w-3.5" />
          </Button>
          <Button
            variant="ghost"
            size="icon"
            className="h-7 w-7 text-destructive hover:bg-destructive/10 hover:text-destructive"
            title="Reject — permanent for this change"
            disabled={decide.isPending}
            onClick={() => decide.mutate({ id: row.original.id, action: "reject" })}
          >
            <X className="h-3.5 w-3.5" />
          </Button>
        </div>
      ),
    },
  ];

  if (isLoading) return <DataTableSkeleton columns={3} />;
  if (data.length === 0)
    return (
      <div className="rounded-md border p-10 text-center text-sm text-muted-foreground">
        No pending fleet groups.
      </div>
    );

  return (
    <>
      <DataTable columns={columns} data={data} />
      {selectedFleet && (
        <Dialog open={Boolean(selectedFleet)} onOpenChange={(open) => !open && setSelectedFleet(null)}>
          <DialogContent className="max-w-lg">
            <DialogHeader>
              <DialogTitle className="text-base font-semibold">
                Fleet Group: {selectedFleet.change_ref}
              </DialogTitle>
              <DialogDescription className="text-xs text-muted-foreground">
                Review all {selectedFleet.member_count} member incidents associated with this blamed change reference.
              </DialogDescription>
            </DialogHeader>

            <div className="space-y-2 py-2 max-h-60 overflow-y-auto">
              <span className="text-[11px] font-semibold uppercase tracking-wider text-muted-foreground">
                Member Evidence Records
              </span>
              <div className="divide-y rounded-md border bg-card">
                {selectedFleet.member_evidence_ids.map((id) => (
                  <div key={id} className="flex items-center justify-between p-2.5 text-xs">
                    <span className="font-mono text-muted-foreground">{id}</span>
                    <Link
                      href={`/evidence/${id}`}
                      target="_blank"
                      className="text-primary hover:underline inline-flex items-center gap-1"
                    >
                      <span>Open</span>
                      <ExternalLink className="h-3 w-3" />
                    </Link>
                  </div>
                ))}
              </div>
            </div>

            <DialogFooter className="flex items-center justify-between">
              <Button
                variant="outline"
                size="sm"
                className="text-destructive hover:bg-destructive/10 border-destructive/30 text-xs"
                disabled={decide.isPending}
                onClick={() => decide.mutate({ id: selectedFleet.id, action: "reject" })}
              >
                Reject Group
              </Button>
              <Button
                size="sm"
                className="bg-emerald-600 hover:bg-emerald-500 text-white text-xs"
                disabled={decide.isPending}
                onClick={() => decide.mutate({ id: selectedFleet.id, action: "accept" })}
              >
                Accept Group
              </Button>
            </DialogFooter>
          </DialogContent>
        </Dialog>
      )}
    </>
  );
}

type ReviewIdentity = {
  id: string;
  canonical_name: string;
  entity_type: string;
  resolution_state: string;
  resolution_confidence: number | null;
  resolution_method: string | null;
};

function IdentityQueue() {
  const qc = useQueryClient();
  const { data = [], isLoading } = useQuery<ReviewIdentity[]>({
    queryKey: ["identities", "needs_review"],
    queryFn: () => api.get("/identities", { resolution_state: "needs_review" }),
  });

  const decide = useMutation({
    mutationFn: ({ id, action }: { id: string; action: "resolve" | "deactivate" }) =>
      api.patch(
        `/identities/${id}`,
        action === "resolve" ? { resolution_state: "resolved" } : { is_active: false },
      ),
    onSuccess: (_data, vars) => {
      qc.invalidateQueries({ queryKey: ["identities"] });
      toast.success(vars.action === "resolve" ? "Identity marked resolved" : "Identity deactivated");
    },
    onError: (err: Error) => toast.error(err.message || "Update failed"),
  });

  const columns: ColumnDef<ReviewIdentity>[] = [
    {
      accessorKey: "canonical_name",
      header: "Name",
      cell: ({ row }) => <span className="text-sm">{row.getValue("canonical_name")}</span>,
    },
    {
      accessorKey: "entity_type",
      header: "Type",
      cell: ({ row }) => (
        <Badge variant="outline" className="text-xs">{row.getValue("entity_type")}</Badge>
      ),
    },
    {
      accessorKey: "resolution_confidence",
      header: "Confidence",
      cell: ({ row }) => {
        const v = row.getValue("resolution_confidence") as number | null;
        return <span className="text-sm">{v == null ? "—" : `${(v * 100).toFixed(0)}%`}</span>;
      },
    },
    {
      id: "actions",
      header: "",
      cell: ({ row }) => (
        <div className="flex items-center gap-1">
          <Button
            variant="ghost"
            size="icon"
            title="Mark resolved — the identity becomes trusted for correlation"
            disabled={decide.isPending}
            onClick={() => decide.mutate({ id: row.original.id, action: "resolve" })}
          >
            <Check className="h-3.5 w-3.5 text-green-600" />
          </Button>
          <Button
            variant="ghost"
            size="icon"
            title="Deactivate — a bad extraction, removed from matching"
            disabled={decide.isPending}
            onClick={() => decide.mutate({ id: row.original.id, action: "deactivate" })}
          >
            <X className="h-3.5 w-3.5 text-destructive" />
          </Button>
        </div>
      ),
    },
  ];

  if (isLoading) return <DataTableSkeleton columns={4} />;
  if (data.length === 0)
    return (
      <div className="rounded-md border p-10 text-center text-sm text-muted-foreground">
        No identities waiting for review.
      </div>
    );
  return <DataTable columns={columns} data={data} />;
}

export default function SuggestionsPage() {
  return (
    <div className="space-y-6">
      <PageHeader
        title="Suggestions"
        description="Knowledge and correlation decision queues: review AI-discovered semantic incident pairs, fleet change groups, and identity resolution candidates."
      />
      <section className="space-y-3">
        <h2 className="text-sm font-medium">Semantic suggestions</h2>
        <SemanticQueue />
      </section>
      <section className="space-y-3">
        <h2 className="text-sm font-medium">Fleet groups</h2>
        <FleetQueue />
      </section>
      <section className="space-y-3">
        <h2 className="text-sm font-medium">Identities needing review</h2>
        <IdentityQueue />
      </section>
    </div>
  );
}
