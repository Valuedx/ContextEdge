"use client";

import { useQuery } from "@tanstack/react-query";
import { ChevronDown } from "lucide-react";

import { SearchableSelect } from "@/components/common/searchable-select";
import { api } from "@/lib/api";
import { cn } from "@/lib/utils";
import type {
  CanonicalIdentity,
  Decision,
  Episode,
  EvidenceItem,
  ExecutionRunBrief,
  Pattern,
  PendingApproval,
  Playbook,
  PoliciesOverview,
  ResolutionSessionResponse,
  TenantPolicyRecord,
  User,
} from "@/lib/types";

const NO_NODE = "__no_node__";

export type GraphNodeOption = {
  id: string;
  label: string;
  meta?: string;
};

function compactId(id: string): string {
  return id.length > 8 ? id.slice(0, 8) : id;
}

function dateLabel(value?: string | null): string {
  if (!value) return "";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? "" : date.toLocaleString();
}

function sessionLabel(session: ResolutionSessionResponse): string {
  const caseId = session.external_case_ids[0];
  const symptom = session.symptoms[0] || "No symptoms";
  return caseId ? `${caseId} - ${symptom}` : symptom;
}

function decisionLabel(decision: Decision): string {
  return decision.compact_trace || decision.rationale_summary || decision.decision_type;
}

function flattenPolicies(data: PoliciesOverview): TenantPolicyRecord[] {
  return [
    ...(data.retention_policies || []),
    ...(data.classification_policies || []),
    ...(data.access_policies || []),
    ...(data.approval_policies || []),
  ];
}

export async function loadGraphNodeOptions(nodeType: string): Promise<GraphNodeOption[]> {
  switch (nodeType) {
    case "pattern": {
      const rows = await api.get<Pattern[]>("/patterns", { limit: "200" });
      return rows.map((row) => ({
        id: row.id,
        label: row.title,
        meta: `${row.pattern_type} - ${Math.round(row.confidence * 100)}%`,
      }));
    }
    case "episode": {
      const rows = await api.get<Episode[]>("/episodes", { limit: "200" });
      return rows.map((row) => ({
        id: row.id,
        label: row.title,
        meta: `${row.status} - ${dateLabel(row.created_at)}`,
      }));
    }
    case "playbook": {
      const rows = await api.get<Playbook[]>("/playbooks", { limit: "200" });
      return rows.map((row) => ({
        id: row.id,
        label: row.title,
        meta: `${row.risk_tier} - ${row.automation_mode}`,
      }));
    }
    case "evidence": {
      const rows = await api.get<EvidenceItem[]>("/evidence", { limit: "200" });
      return rows.map((row) => ({
        id: row.id,
        label: row.title || row.body_summary || "Untitled evidence",
        meta: `${row.evidence_type} - ${dateLabel(row.ingested_at)}`,
      }));
    }
    case "identity": {
      const rows = await api.get<CanonicalIdentity[]>("/identities", { limit: "200" });
      return rows.map((row) => ({
        id: row.id,
        label: row.canonical_name,
        meta: row.entity_type,
      }));
    }
    case "session": {
      const rows = await api.get<ResolutionSessionResponse[]>("/sessions", { limit: "200" });
      return rows.map((row) => ({
        id: row.id,
        label: sessionLabel(row),
        meta: `${row.status} - ${dateLabel(row.created_at)}`,
      }));
    }
    case "execution_run": {
      const rows = await api.get<ExecutionRunBrief[]>("/execution/runs", { limit: "200" });
      return rows.map((row) => ({
        id: row.id,
        label: row.outcome_summary || `Execution ${compactId(row.id)}`,
        meta: `${row.status} - ${row.automation_mode}`,
      }));
    }
    case "approval_request": {
      const rows = await api.get<PendingApproval[]>("/execution/approvals/pending", {
        limit: "200",
      });
      return rows.map((row) => ({
        id: row.id,
        label: row.requested_action,
        meta: `${row.status} - ${row.safety_class}`,
      }));
    }
    case "user": {
      const rows = await api.get<User[]>("/users", { limit: "200" });
      return rows.map((row) => ({
        id: row.id,
        label: row.display_name || row.email,
        meta: row.email,
      }));
    }
    case "decision": {
      const rows = await api.get<Decision[]>("/decisions", { limit: "200" });
      return rows.map((row) => ({
        id: row.id,
        label: decisionLabel(row),
        meta: `${row.decision_type} - ${row.status}`,
      }));
    }
    case "tenant_policy":
    case "action_policy": {
      const data = await api.get<PoliciesOverview>("/policies");
      return flattenPolicies(data).map((row) => ({
        id: row.id,
        label: row.name,
        meta: row.description || (row.is_active ? "Active policy" : "Inactive policy"),
      }));
    }
    default:
      return [];
  }
}

export function GraphNodePicker({
  className,
  nodeId,
  nodeType,
  nodeTypes,
  onNodeIdChange,
  onNodeTypeChange,
  showType = true,
  nodeLabel = "Node name",
}: {
  className?: string;
  nodeId: string;
  nodeType: string;
  nodeTypes: readonly string[];
  onNodeIdChange: (value: string) => void;
  onNodeTypeChange?: (value: string) => void;
  showType?: boolean;
  nodeLabel?: string;
}) {
  const { data: options = [], error, isLoading } = useQuery<GraphNodeOption[], Error>({
    queryKey: ["graph-node-picker", nodeType],
    queryFn: () => loadGraphNodeOptions(nodeType),
  });
  const selectedOption = options.find((option) => option.id === nodeId);
  const selectOptions = [
    { value: NO_NODE, label: "No node selected" },
    ...(nodeId && !selectedOption
      ? [{ value: nodeId, label: `Selected node ${compactId(nodeId)}` }]
      : []),
    ...options.map((option) => ({
      value: option.id,
      label: option.label,
      meta: option.meta,
    })),
  ];

  return (
    <div className={cn("flex min-w-0 items-end gap-2.5", className)}>
      {showType && (
        <div className="flex shrink-0 flex-col gap-1">
          <label className="text-xs font-medium text-muted-foreground">Node type</label>
          <div className="relative">
            <select
              value={nodeType}
              onChange={(event) => {
                onNodeTypeChange?.(event.target.value);
                onNodeIdChange("");
              }}
              className="h-8 appearance-none rounded-md border border-input bg-background pl-2.5 pr-8 text-xs font-medium capitalize outline-none transition-colors hover:border-slate-400 focus-visible:border-ring focus-visible:ring-2 focus-visible:ring-ring/20"
            >
              {nodeTypes.map((type) => (
                <option key={type} value={type} className="capitalize">
                  {type.replace(/_/g, " ")}
                </option>
              ))}
            </select>
            <ChevronDown className="pointer-events-none absolute right-2.5 top-1/2 -translate-y-1/2 size-3.5 text-muted-foreground opacity-60" />
          </div>
        </div>
      )}

      <div className="flex min-w-44 sm:min-w-60 flex-1 flex-col gap-1">
        <label className="text-xs font-medium text-muted-foreground">{nodeLabel}</label>
        <SearchableSelect
          value={nodeId || NO_NODE}
          options={selectOptions}
          loading={isLoading}
          placeholder="Select by name"
          searchPlaceholder={`Search ${nodeType.replace(/_/g, " ")}...`}
          emptyText={`No ${nodeType.replace(/_/g, " ")} records found.`}
          disabled={isLoading}
          onValueChange={(value) => onNodeIdChange(value === NO_NODE ? "" : value)}
        />
        {error ? (
          <p className="text-xs text-destructive">{error.message}</p>
        ) : null}
      </div>
    </div>
  );
}
