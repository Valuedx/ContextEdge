"use client";

import { Suspense, useMemo, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { BarChart3, BrainCircuit, GitBranch, GitPullRequest, Network } from "lucide-react";

import { PageHeader } from "@/components/common/page-header";
import { AgentContextPreview } from "@/components/graph/agent-context-preview";
import { EdgeProposals } from "@/components/graph/edge-proposals";
import { NODE_TYPE_OPTIONS } from "@/components/graph/graph-constants";
import { GraphNeighbors } from "@/components/graph/graph-neighbors";
import { GraphQueryControls } from "@/components/graph/graph-query-controls";
import { GraphStats } from "@/components/graph/graph-stats";
import { GraphSubgraph } from "@/components/graph/graph-subgraph";
import {
  backLabelForPath,
  safeInternalReturnPath,
} from "@/components/graph/graph-node-routes";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";

const TAB_NAMES = ["stats", "subgraph", "neighbors", "agent-context", "proposals"] as const;
type TabName = (typeof TAB_NAMES)[number];
const UUID_PATTERN =
  /^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;

function asLocalDateTime(value: string | null): string {
  if (!value) return "";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "";
  const offset = date.getTimezoneOffset() * 60_000;
  return new Date(date.getTime() - offset).toISOString().slice(0, 16);
}

function GraphExplorerContent() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const initialType = searchParams.get("node_type") ?? undefined;
  const requestedId = searchParams.get("node_id") ?? undefined;
  const initialId =
    requestedId && UUID_PATTERN.test(requestedId) ? requestedId : undefined;
  const requestedTab = searchParams.get("tab");
  const validTab = TAB_NAMES.includes(requestedTab as TabName)
    ? (requestedTab as TabName)
    : initialType && initialId
      ? "subgraph"
      : "stats";
  const validType =
    initialType &&
    NODE_TYPE_OPTIONS.includes(initialType as (typeof NODE_TYPE_OPTIONS)[number])
      ? initialType
      : undefined;

  const returnTo = safeInternalReturnPath(searchParams.get("from"));
  const [activeTab, setActiveTab] = useState<TabName>(validTab);
  const [domainId, setDomainId] = useState(searchParams.get("domain_id") ?? "");
  const initialAsOf = asLocalDateTime(searchParams.get("as_of"));
  const [historical, setHistorical] = useState(Boolean(initialAsOf));
  const [asOfLocal, setAsOfLocal] = useState(initialAsOf);

  const scope = useMemo(() => {
    let asOf: string | undefined;
    if (historical && asOfLocal) {
      const parsed = new Date(asOfLocal);
      if (!Number.isNaN(parsed.getTime())) asOf = parsed.toISOString();
    }
    return { domainId: domainId || undefined, asOf };
  }, [asOfLocal, domainId, historical]);

  function updateUrl(updates: Record<string, string | undefined>) {
    const next = new URLSearchParams(searchParams.toString());
    for (const [key, value] of Object.entries(updates)) {
      if (value) next.set(key, value);
      else next.delete(key);
    }
    const query = next.toString();
    router.replace(query ? `/graph-explorer?${query}` : "/graph-explorer", {
      scroll: false,
    });
  }

  function changeTab(value: string) {
    const next = value as TabName;
    setActiveTab(next);
    updateUrl({ tab: next });
  }

  return (
    <div className="space-y-6">
      <PageHeader
        title="Graph Explorer"
        description="Explore operational relationships, temporal topology, and agent-ready context."
        backHref={returnTo ?? undefined}
        backLabel={returnTo ? backLabelForPath(returnTo) : undefined}
      />

      <GraphQueryControls
        domainId={domainId}
        onDomainChange={(value) => {
          setDomainId(value);
          updateUrl({ domain_id: value || undefined });
        }}
        historical={historical}
        onHistoricalChange={(value) => {
          setHistorical(value);
          updateUrl({ as_of: value && scope.asOf ? scope.asOf : undefined });
        }}
        asOfLocal={asOfLocal}
        onAsOfLocalChange={(value) => {
          setAsOfLocal(value);
          const parsed = new Date(value);
          updateUrl({
            as_of: Number.isNaN(parsed.getTime()) ? undefined : parsed.toISOString(),
          });
        }}
      />

      <Tabs value={activeTab} onValueChange={changeTab} className="w-full">
        <TabsList variant="glass" className="max-w-full justify-start overflow-x-auto gap-1 border border-border/80 bg-muted/60 p-1 shadow-xs">
          <TabsTrigger value="stats" className="gap-2">
            <BarChart3 className="h-4 w-4" /> Statistics
          </TabsTrigger>
          <TabsTrigger value="subgraph" className="gap-2">
            <Network className="h-4 w-4" /> Subgraph
          </TabsTrigger>
          <TabsTrigger value="neighbors" className="gap-2">
            <GitBranch className="h-4 w-4" /> Neighbors
          </TabsTrigger>
          <TabsTrigger value="agent-context" className="gap-2">
            <BrainCircuit className="h-4 w-4" /> Agent Context
          </TabsTrigger>
          <TabsTrigger value="proposals" className="gap-2">
            <GitPullRequest className="h-4 w-4" /> Proposals
          </TabsTrigger>
        </TabsList>

        <TabsContent value="stats" className="mt-4 border-none p-0 outline-none">
          <GraphStats scope={scope} />
        </TabsContent>
        <TabsContent value="subgraph" className="mt-4 border-none p-0 outline-none">
          <GraphSubgraph
            scope={scope}
            initialType={validType}
            initialId={validType ? initialId : undefined}
            returnTo={returnTo ?? undefined}
          />
        </TabsContent>
        <TabsContent value="neighbors" className="mt-4 border-none p-0 outline-none">
          <GraphNeighbors scope={scope} />
        </TabsContent>
        <TabsContent value="agent-context" className="mt-4 border-none p-0 outline-none">
          <AgentContextPreview scope={scope} />
        </TabsContent>
        <TabsContent value="proposals" className="mt-4 border-none p-0 outline-none">
          <EdgeProposals />
        </TabsContent>
      </Tabs>
    </div>
  );
}

export default function GraphExplorerPage() {
  return (
    <Suspense>
      <GraphExplorerContent />
    </Suspense>
  );
}
