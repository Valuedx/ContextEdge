"use client";

import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { usePagination } from "@/lib/hooks/use-pagination";
import { PaginationControls } from "@/components/common/pagination-controls";
import { ColumnDef } from "@tanstack/react-table";
import { CheckCircle2, Loader2, Plus, Search, Send, X } from "lucide-react";

import { PageHeader } from "@/components/common/page-header";
import { DataTable } from "@/components/common/data-table";
import { DataTableSkeleton } from "@/components/common/data-table-skeleton";
import { StatusBadge } from "@/components/common/status-badge";
import {
  bulkTransitionTarget,
  lifecycleStateLabel,
  PlaybookLifecycleActions,
} from "@/components/common/playbook-lifecycle-actions";
import { Input } from "@/components/ui/input";
import { Button, buttonVariants } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import { api } from "@/lib/api";
import type { Playbook } from "@/lib/types";
import Link from "next/link";
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { toast } from "sonner";
import { canTransitionPlaybook } from "@/lib/roles";
import { useAuthStore } from "@/lib/stores/auth-store";

type BulkTransitionResult = {
  transitioned_count: number;
  new_state: string;
  ids: string[];
};

const LIFECYCLE_TABS = [
  { value: "all", label: "All" },
  { value: "candidate", label: "Candidates" },
  { value: "under_review", label: "Under review" },
  { value: "approved", label: "Approved" },
  { value: "restricted", label: "Restricted" },
] as const;

const columns: ColumnDef<Playbook>[] = [
  {
    id: "select",
    header: ({ table }) => (
      <input
        type="checkbox"
        className="h-4 w-4 rounded border-black/20 bg-white/50 text-primary focus:ring-primary dark:border-white/20 dark:bg-white/5"
        checked={table.getIsAllPageRowsSelected()}
        onChange={(event) => table.toggleAllPageRowsSelected(event.target.checked)}
        aria-label="Select all playbooks on this page"
      />
    ),
    cell: ({ row }) => (
      <input
        type="checkbox"
        className="h-4 w-4 rounded border-black/20 bg-white/50 text-primary focus:ring-primary dark:border-white/20 dark:bg-white/5"
        checked={row.getIsSelected()}
        onChange={(event) => row.toggleSelected(event.target.checked)}
        aria-label={`Select ${row.original.title}`}
      />
    ),
    enableSorting: false,
    enableHiding: false,
  },
  {
    accessorKey: "title",
    header: "Title",
    cell: ({ row }) => (
      <Link href={`/playbooks/${row.original.id}`} className="font-medium text-primary hover:underline">
        {row.getValue("title")}
      </Link>
    ),
  },
  {
    accessorKey: "lifecycle_state",
    header: "State",
    cell: ({ row }) => <StatusBadge status={row.getValue("lifecycle_state")} />,
  },
  { accessorKey: "risk_tier", header: "Risk" },
  { accessorKey: "automation_mode", header: "Automation" },
  {
    accessorKey: "confidence",
    header: "Score",
    cell: ({ row }) => {
      const val = row.original.confidence;
      return val !== null && val !== undefined ? (val * 100).toFixed(0) + "%" : "—";
    },
  },
  {
    accessorKey: "last_validated_at",
    header: "Validated",
    cell: ({ row }) => {
      const val = row.getValue("last_validated_at");
      return val ? new Date(val as string).toLocaleString() : "Never";
    },
  },
  {
    accessorKey: "updated_at",
    header: "Updated",
    cell: ({ row }) => new Date(row.getValue("updated_at")).toLocaleString(),
  },
  {
    id: "actions",
    header: "Review action",
    enableSorting: false,
    cell: ({ row }) => <PlaybookLifecycleActions playbook={row.original} />,
  },
];

export default function PlaybooksPage() {
  const pg = usePagination(50);
  const queryClient = useQueryClient();
  const roles = useAuthStore((state) => state.roles);
  const [searchQuery, setSearchQuery] = useState("");
  const [lifecycleState, setLifecycleState] = useState("all");
  const [selectedIds, setSelectedIds] = useState<string[]>([]);
  const [selectionVersion, setSelectionVersion] = useState(0);

  const params: Record<string, string> = { ...pg.params };
  if (searchQuery.trim()) {
    params.q = searchQuery.trim();
  }
  if (lifecycleState !== "all") {
    params.lifecycle_state = lifecycleState;
  }

  const { data = [], isLoading } = useQuery<Playbook[]>({
    queryKey: ["playbooks", pg.page, searchQuery, lifecycleState],
    queryFn: () => api.get("/playbooks", params),
  });

  const selectedPlaybooks = data.filter((playbook) => selectedIds.includes(playbook.id));
  const bulkTarget = bulkTransitionTarget(selectedPlaybooks);
  const canBulkTransition = canTransitionPlaybook(roles);

  const bulkTransition = useMutation({
    mutationFn: ({ ids, target }: { ids: string[]; target: string }) =>
      api.post<BulkTransitionResult>("/playbooks/bulk-transition", {
        ids,
        new_state: target,
        comments: "Bulk review action from the Playbooks queue",
      }),
    onSuccess: (result) => {
      queryClient.invalidateQueries({ queryKey: ["playbooks"] });
      toast.success(
        `${result.transitioned_count} playbooks moved to ${lifecycleStateLabel(result.new_state)}`,
      );
      setSelectedIds([]);
      setSelectionVersion((version) => version + 1);
    },
    onError: (error: Error) => toast.error(error.message || "Bulk playbook action failed"),
  });

  const resetSelection = () => {
    setSelectedIds([]);
    setSelectionVersion((version) => version + 1);
  };

  return (
    <div className="space-y-6">
      <PageHeader
        title="Playbooks"
        description="Governed living playbooks and candidate review queue."
        actions={
          <div className="flex flex-wrap items-center justify-end gap-2">
            {selectedIds.length > 0 && (
              <Button
                type="button"
                disabled={!canBulkTransition || !bulkTarget || bulkTransition.isPending}
                title={
                  !canBulkTransition
                    ? "The playbook_reviewer role is required"
                    : !bulkTarget
                      ? "Select only Candidates or only Under review playbooks"
                      : undefined
                }
                onClick={() => {
                  if (bulkTarget) {
                    bulkTransition.mutate({ ids: selectedIds, target: bulkTarget });
                  }
                }}
              >
                {bulkTransition.isPending ? (
                  <Loader2 className="h-4 w-4 animate-spin" />
                ) : bulkTarget === "approved" ? (
                  <CheckCircle2 className="h-4 w-4" />
                ) : (
                  <Send className="h-4 w-4" />
                )}
                {bulkTarget === "approved"
                  ? `Approve selected (${selectedIds.length})`
                  : bulkTarget === "under_review"
                    ? `Submit selected (${selectedIds.length})`
                    : `Select one review state (${selectedIds.length})`}
              </Button>
            )}
            <Link href="/patterns" className={cn(buttonVariants(), "")}>
              <Plus className="mr-2 h-4 w-4" />
              Generate from Pattern
            </Link>
          </div>
        }
      />

      {/* Advanced Search Bar */}
      <div className="flex flex-col gap-3">
        <Tabs
          value={lifecycleState}
          onValueChange={(value) => {
            setLifecycleState(value);
            pg.reset();
            resetSelection();
          }}
        >
          <TabsList variant="glass" className="h-auto flex-wrap justify-start">
            {LIFECYCLE_TABS.map((tab) => (
              <TabsTrigger key={tab.value} value={tab.value}>
                {tab.label}
              </TabsTrigger>
            ))}
          </TabsList>
        </Tabs>
        <div className="relative flex-1">
          <Search className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
          <Input
            placeholder="Search playbooks by issue, description, title, or ticket # (e.g. 408801)..."
            value={searchQuery}
            onChange={(e) => {
              setSearchQuery(e.target.value);
              resetSelection();
            }}
            className="pl-9 pr-9"
          />
          {searchQuery && (
            <button
              onClick={() => {
                setSearchQuery("");
                resetSelection();
              }}
              className="absolute right-3 top-1/2 -translate-y-1/2 text-muted-foreground hover:text-foreground"
            >
              <X className="h-4 w-4" />
            </button>
          )}
        </div>
      </div>

      {isLoading ? (
        <DataTableSkeleton columns={8} />
      ) : (
        <>
          <DataTable
            key={`${lifecycleState}-${selectionVersion}`}
            columns={columns}
            data={data}
            onSelectionChange={setSelectedIds}
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
    </div>
  );
}
