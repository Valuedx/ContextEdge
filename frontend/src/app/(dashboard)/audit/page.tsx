"use client";

import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { ColumnDef } from "@tanstack/react-table";

import { PageHeader } from "@/components/common/page-header";
import { DataTable } from "@/components/common/data-table";
import { DataTableSkeleton } from "@/components/common/data-table-skeleton";
import { PaginationControls } from "@/components/common/pagination-controls";
import { usePagination } from "@/lib/hooks/use-pagination";
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
import { api } from "@/lib/api";
import type { AuditLog } from "@/lib/types";

const ACTION_TYPES = [
  "all",
  "source.created",
  "source.deleted",
  "source.local_ingest",
  "evidence.deleted",
  "evidence.access_policy_updated",
  "episode.approved",
  "playbook.transitioned",
  "policy.created",
  "policy.updated",
  "policy.deleted",
  "session.created",
  "session.closed",
];

const columns: ColumnDef<AuditLog>[] = [
  {
    accessorKey: "timestamp",
    header: "Time",
    cell: ({ row }) => new Date(row.getValue("timestamp")).toLocaleString(),
  },
  { accessorKey: "action", header: "Action" },
  {
    accessorKey: "actor_email",
    header: "Actor",
    cell: ({ row }) => row.getValue("actor_email") ?? "—",
  },
  { accessorKey: "resource_type", header: "Resource Type" },
  {
    accessorKey: "resource_id",
    header: "Resource ID",
    cell: ({ row }) => {
      const v = row.getValue("resource_id") as string | null;
      return v ? <span className="font-mono text-xs">{v.slice(0, 8)}…</span> : "—";
    },
  },
];

export default function AuditPage() {
  const pg = usePagination(50);

  // Filter state
  const [actionFilter, setActionFilter] = useState("all");
  const [sinceDate, setSinceDate] = useState("");
  const [untilDate, setUntilDate] = useState("");
  const [applied, setApplied] = useState<{
    action: string;
    since: string;
    until: string;
  }>({ action: "all", since: "", until: "" });

  const { data = [], isLoading } = useQuery<AuditLog[]>({
    queryKey: ["audit-logs", applied, pg.page],
    queryFn: () => {
      const params: Record<string, string> = { ...pg.params };
      if (applied.action && applied.action !== "all") params.action = applied.action;
      if (applied.since) params.since = new Date(applied.since).toISOString();
      if (applied.until) params.until = new Date(applied.until + "T23:59:59").toISOString();
      return api.get("/audit-logs", params);
    },
  });

  function applyFilters() {
    pg.reset();
    setApplied({ action: actionFilter, since: sinceDate, until: untilDate });
  }

  function clearFilters() {
    setActionFilter("all");
    setSinceDate("");
    setUntilDate("");
    pg.reset();
    setApplied({ action: "all", since: "", until: "" });
  }

  return (
    <div className="space-y-6">
      <PageHeader
        title="Audit Log"
        description="Track admin, reviewer, retrieval, and policy actions."
      />

      {/* Filter bar */}
      <div className="flex items-end gap-3 overflow-x-auto">
        <div className="space-y-1">
          <Label className="text-xs">Action type</Label>
          <Select value={actionFilter} onValueChange={(v) => setActionFilter(v ?? "all")}>
            <SelectTrigger className="w-56">
              <SelectValue placeholder="All actions" />
            </SelectTrigger>
            <SelectContent>
              {ACTION_TYPES.map((a) => (
                <SelectItem key={a} value={a}>
                  {a === "all" ? "All actions" : a}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>

        <div className="space-y-1">
          <Label className="text-xs">From date</Label>
          <Input
            type="date"
            className="w-40"
            value={sinceDate}
            onChange={(e) => setSinceDate(e.target.value)}
          />
        </div>

        <div className="space-y-1">
          <Label className="text-xs">To date</Label>
          <Input
            type="date"
            className="w-40"
            value={untilDate}
            onChange={(e) => setUntilDate(e.target.value)}
          />
        </div>

        <Button variant="secondary" onClick={applyFilters}>
          Apply
        </Button>
        <Button variant="outline" onClick={clearFilters}>
          Clear
        </Button>
      </div>

      {isLoading ? (
        <DataTableSkeleton columns={5} />
      ) : data.length === 0 ? (
        <div className="rounded-md border p-10 text-center text-sm text-muted-foreground">
          No audit log entries match the current filters.
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
    </div>
  );
}
