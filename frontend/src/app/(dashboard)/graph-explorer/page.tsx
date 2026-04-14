"use client";

import { useState } from "react";
import { PageHeader } from "@/components/common/page-header";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { BarChart3, Network, GitBranch } from "lucide-react";
import { GraphStats } from "@/components/graph/graph-stats";
import { GraphSubgraph } from "@/components/graph/graph-subgraph";
import { GraphNeighbors } from "@/components/graph/graph-neighbors";

export default function GraphExplorerPage() {
  const [activeTab, setActiveTab] = useState("stats");

  return (
    <div className="space-y-6">
      <PageHeader
        title="Graph Explorer"
        description="Explore the operational context graph — entity relationships, traversal, and aggregate statistics."
      />

      <Tabs value={activeTab} onValueChange={setActiveTab} className="w-full">
        <div className="flex justify-start mb-4">
          <TabsList>
            <TabsTrigger value="stats" className="flex items-center gap-2">
              <BarChart3 className="h-4 w-4" /> Statistics
            </TabsTrigger>
            <TabsTrigger value="subgraph" className="flex items-center gap-2">
              <Network className="h-4 w-4" /> Subgraph
            </TabsTrigger>
            <TabsTrigger value="neighbors" className="flex items-center gap-2">
              <GitBranch className="h-4 w-4" /> Neighbors
            </TabsTrigger>
          </TabsList>
        </div>

        <TabsContent value="stats" className="border-none p-0 outline-none">
          <GraphStats />
        </TabsContent>

        <TabsContent value="subgraph" className="border-none p-0 outline-none">
          <GraphSubgraph />
        </TabsContent>

        <TabsContent value="neighbors" className="border-none p-0 outline-none">
          <GraphNeighbors />
        </TabsContent>
      </Tabs>
    </div>
  );
}
