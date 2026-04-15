"use client";

import { useQuery } from "@tanstack/react-query";
import { ColumnDef } from "@tanstack/react-table";
import { useState } from "react";
import { useSearchParams } from "next/navigation";
import { Link2 } from "lucide-react";

import { PageHeader } from "@/components/common/page-header";
import { DataTable } from "@/components/common/data-table";
import { DataTableSkeleton } from "@/components/common/data-table-skeleton";
import { StatusBadge } from "@/components/common/status-badge";
import { PaginationControls } from "@/components/common/pagination-controls";
import { usePagination } from "@/lib/hooks/use-pagination";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
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
import { DecisionDetail } from "@/components/decisions/decision-detail";
import { DecisionChain } from "@/components/decisions/decision-chain";
import { api } from "@/lib/api";
import type { Decision, DecisionChainResponse } from "@/lib/types";

const DECISION_TYPES = [
  "classify_issue",
  "verify_dependency",
  "restart_workflow",
  "request_approval",
  "escalate_to_human",
  "create_ticket",
  "defer",
  "ask_clarifying_question",
  "execute_playbook",
  "select_playbook",
  "approve",
  "deny",
];

const AGENT_STEPS = ["diagnostics", "remediation", "evaluation", "triage"];

const columns: ColumnDef<Decision>[] = [
  {
    accessorKey: "decision_type",
    header: "Type",
    cell: ({ row }) => (
      <Badge variant="outline">{row.getValue("decision_type")}</Badge>
    ),
  },
  {
    accessorKey: "agent_step",
    header: "Step",
    cell: ({ row }) => (
      <Badge variant="secondary">{row.getValue("agent_step")}</Badge>
    ),
  },
  {
    accessorKey: "actor_type",
    header: "Actor",
    cell: ({ row }) => row.getValue("actor_type"),
  },
  {
    accessorKey: "compact_trace",
    header: "Summary",
    cell: ({ row }) => {
      const trace = row.getValue("compact_trace") as string | null;
      return trace ? (
        <span className="text-xs truncate max-w-[300px] block">{trace}</span>
      ) : (
        "—"
      );
    },
  },
  {
    accessorKey: "confidence",
    header: "Conf.",
    cell: ({ row }) => {
      const v = row.getValue("confidence") as number | null;
      return v != null ? `${Math.round(v * 100)}%` : "—";
    },
  },
  {
    accessorKey: "status",
    header: "Status",
    cell: ({ row }) => <StatusBadge status={row.getValue("status")} />,
  },
  {
    accessorKey: "created_at",
    header: "Created",
    cell: ({ row }) =>
      new Date(row.getValue("created_at") as string).toLocaleString(),
  },
];

export default function DecisionsPage() {
  const searchParams = useSearchParams();
  const initialId = searchParams.get("id");

  const [selected, setSelected] = useState<Decision | null>(null);
  const [typeFilter, setTypeFilter] = useState<string>("");
  const [stepFilter, setStepFilter] = useState<string>("");
  const [sessionFilter, setSessionFilter] = useState<string>("");
  const [activeTab, setActiveTab] = useState("detail");
  const pg = usePagination(50);

  const queryParams: Record<string, string> = { ...pg.params };
  if (typeFilter) queryParams.decision_type = typeFilter;
  if (stepFilter) queryParams.agent_step = stepFilter;
  if (sessionFilter) queryParams.session_id = sessionFilter;

  const { data = [], isLoading } = useQuery<Decision[]>({
    queryKey: ["decisions", pg.page, typeFilter, stepFilter, sessionFilter],
    queryFn: () => api.get("/decisions", queryParams),
  });

  const chainQuery = useQuery<DecisionChainResponse>({
    queryKey: ["decision-chain", selected?.id],
    queryFn: () => api.get(`/decisions/${selected!.id}/chain`),
    enabled: !!selected && activeTab === "chain",
  });

  const detailQuery = useQuery<Decision>({
    queryKey: ["decision-detail", initialId],
    queryFn: () => api.get(`/decisions/${initialId}`),
    enabled: !!initialId && !selected,
  });

  const displayDecision = selected ?? detailQuery.data ?? null;

  return (
    <div className="space-y-6">
      <PageHeader
        title="Decisions"
        description="First-class decision traces with evidence, options, reasoning, and outcomes."
      />

      <div className="flex flex-wrap gap-3 items-end">
        <div className="space-y-1">
          <Label className="text-xs">Decision type</Label>
          <Select value={typeFilter} onValueChange={setTypeFilter}>
            <SelectTrigger className="w-[180px]">
              <SelectValue placeholder="All types" />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="">All types</SelectItem>
              {DECISION_TYPES.map((t) => (
                <SelectItem key={t} value={t}>
                  {t}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
        <div className="space-y-1">
          <Label className="text-xs">Agent step</Label>
          <Select value={stepFilter} onValueChange={setStepFilter}>
            <SelectTrigger className="w-[150px]">
              <SelectValue placeholder="All steps" />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="">All steps</SelectItem>
              {AGENT_STEPS.map((s) => (
                <SelectItem key={s} value={s}>
                  {s}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
        <div className="space-y-1">
          <Label className="text-xs">Session ID</Label>
          <Input
            className="w-[280px] font-mono text-xs"
            placeholder="Filter by session..."
            value={sessionFilter}
            onChange={(e) => setSessionFilter(e.target.value)}
          />
        </div>
      </div>

      {isLoading ? (
        <DataTableSkeleton columns={7} />
      ) : data.length === 0 ? (
        <div className="rounded-md border p-12 text-center text-muted-foreground">
          No decisions recorded yet. Decisions are created automatically during
          playbook execution, runtime matching, and approval workflows.
        </div>
      ) : (
        <>
          <DataTable
            columns={[
              ...columns,
              {
                id: "actions",
                header: "",
                cell: ({ row }) => (
                  <Button
                    variant="ghost"
                    size="sm"
                    onClick={() =>
                      setSelected(
                        selected?.id === row.original.id ? null : row.original,
                      )
                    }
                  >
                    {selected?.id === row.original.id ? "Hide" : "View"}
                  </Button>
                ),
              },
            ]}
            data={data}
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

      {displayDecision && (
        <div className="space-y-4">
          <Tabs value={activeTab} onValueChange={setActiveTab}>
            <TabsList>
              <TabsTrigger value="detail">Detail</TabsTrigger>
              <TabsTrigger value="chain">
                <Link2 className="mr-1 h-3.5 w-3.5" />
                Chain
              </TabsTrigger>
            </TabsList>
            <TabsContent value="detail" className="mt-4">
              <DecisionDetail decision={displayDecision} />
            </TabsContent>
            <TabsContent value="chain" className="mt-4">
              {chainQuery.isLoading ? (
                <p className="text-sm text-muted-foreground">Loading chain…</p>
              ) : chainQuery.data ? (
                <DecisionChain
                  decisions={chainQuery.data.decisions}
                  onSelect={setSelected}
                />
              ) : (
                <p className="text-sm text-muted-foreground">
                  Select a decision to view its chain.
                </p>
              )}
            </TabsContent>
          </Tabs>

          <Button
            variant="ghost"
            size="sm"
            onClick={() => setSelected(null)}
          >
            Dismiss
          </Button>
        </div>
      )}
    </div>
  );
}
