"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ColumnDef } from "@tanstack/react-table";
import { toast } from "sonner";
import { Check, X } from "lucide-react";

import { PageHeader } from "@/components/common/page-header";
import { DataTable } from "@/components/common/data-table";
import { DataTableSkeleton } from "@/components/common/data-table-skeleton";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { api } from "@/lib/api";

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

function SemanticQueue() {
  const qc = useQueryClient();
  const { data = [], isLoading } = useQuery<SemanticSuggestion[]>({
    queryKey: ["suggestions", "pending"],
    queryFn: () => api.get("/correlations/suggestions", { status: "pending" }),
  });

  const decide = useMutation({
    mutationFn: ({ id, action }: { id: string; action: "accept" | "reject" }) =>
      api.post(`/correlations/suggestions/${id}/${action}`, {}),
    onSuccess: (_data, vars) => {
      qc.invalidateQueries({ queryKey: ["suggestions"] });
      toast.success(vars.action === "accept" ? "Suggestion accepted — edge created" : "Suggestion rejected");
    },
    onError: (err: Error) => toast.error(err.message || "Decision failed"),
  });

  const columns: ColumnDef<SemanticSuggestion>[] = [
    {
      accessorKey: "evidence_id_low",
      header: "Evidence A",
      cell: ({ row }) => (
        <span className="font-mono text-xs truncate max-w-[140px] block">{row.getValue("evidence_id_low")}</span>
      ),
    },
    {
      accessorKey: "evidence_id_high",
      header: "Evidence B",
      cell: ({ row }) => (
        <span className="font-mono text-xs truncate max-w-[140px] block">{row.getValue("evidence_id_high")}</span>
      ),
    },
    {
      accessorKey: "similarity",
      header: "Similarity",
      cell: ({ row }) => (
        <span className="text-sm">{((row.getValue("similarity") as number) * 100).toFixed(0)}%</span>
      ),
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
      header: "",
      cell: ({ row }) => (
        <div className="flex items-center gap-1">
          <Button
            variant="ghost"
            size="icon"
            title="Accept — creates a correlation edge"
            disabled={decide.isPending}
            onClick={() => decide.mutate({ id: row.original.id, action: "accept" })}
          >
            <Check className="h-3.5 w-3.5 text-green-600" />
          </Button>
          <Button
            variant="ghost"
            size="icon"
            title="Reject — permanent for this pair"
            disabled={decide.isPending}
            onClick={() => decide.mutate({ id: row.original.id, action: "reject" })}
          >
            <X className="h-3.5 w-3.5 text-destructive" />
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
  return <DataTable columns={columns} data={data} />;
}

function FleetQueue() {
  const qc = useQueryClient();
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
    },
    onError: (err: Error) => toast.error(err.message || "Decision failed"),
  });

  const columns: ColumnDef<FleetSuggestion>[] = [
    {
      accessorKey: "change_ref",
      header: "Blamed change",
      cell: ({ row }) => (
        <span className="font-mono text-xs truncate max-w-[200px] block">{row.getValue("change_ref")}</span>
      ),
    },
    {
      accessorKey: "member_count",
      header: "Incidents",
      cell: ({ row }) => <Badge variant="secondary">{row.getValue("member_count")}</Badge>,
    },
    {
      id: "actions",
      header: "",
      cell: ({ row }) => (
        <div className="flex items-center gap-1">
          <Button
            variant="ghost"
            size="icon"
            title="Accept — groups these incidents under one parent case"
            disabled={decide.isPending}
            onClick={() => decide.mutate({ id: row.original.id, action: "accept" })}
          >
            <Check className="h-3.5 w-3.5 text-green-600" />
          </Button>
          <Button
            variant="ghost"
            size="icon"
            title="Reject — permanent for this change"
            disabled={decide.isPending}
            onClick={() => decide.mutate({ id: row.original.id, action: "reject" })}
          >
            <X className="h-3.5 w-3.5 text-destructive" />
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
  return <DataTable columns={columns} data={data} />;
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
        title="Review queues"
        description="Human decision queues: semantic evidence pairs (accept creates an edge; reject is permanent), fleet incident groups (accept mints a parent case), and identities the resolver parked for review."
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
