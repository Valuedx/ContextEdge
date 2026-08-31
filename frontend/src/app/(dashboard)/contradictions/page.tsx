"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ColumnDef } from "@tanstack/react-table";
import { useState } from "react";
import { toast } from "sonner";

import { PageHeader } from "@/components/common/page-header";
import { DataTable } from "@/components/common/data-table";
import { DataTableSkeleton } from "@/components/common/data-table-skeleton";
import { StatusBadge } from "@/components/common/status-badge";
import { PaginationControls } from "@/components/common/pagination-controls";
import { usePagination } from "@/lib/hooks/use-pagination";
import { Button } from "@/components/ui/button";
import {
  Dialog, DialogContent, DialogFooter, DialogHeader, DialogTitle,
} from "@/components/ui/dialog";
import { Label } from "@/components/ui/label";
import {
  Select, SelectContent, SelectItem, SelectTrigger, SelectValue,
} from "@/components/ui/select";
import { Textarea } from "@/components/ui/textarea";
import { api } from "@/lib/api";
import type { Contradiction } from "@/lib/types";

const STATUSES = ["open", "acknowledged", "resolved", "suppressed"];

function ResolveDialog({
  item,
  onClose,
}: {
  item: Contradiction;
  onClose: () => void;
}) {
  const qc = useQueryClient();
  const [status, setStatus] = useState(item.resolution_status);
  const [description, setDescription] = useState(item.description ?? "");

  const mut = useMutation({
    mutationFn: () =>
      api.patch(`/contradictions/${item.id}/status`, {
        resolution_status: status,
        description: description.trim() || null,
      }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["contradictions"] });
      toast.success("Contradiction status updated");
      onClose();
    },
    onError: (err: Error) => toast.error(err.message || "Update failed"),
  });

  return (
    <DialogContent>
      <DialogHeader>
        <DialogTitle>Update contradiction</DialogTitle>
      </DialogHeader>
      <div className="space-y-4 text-sm">
        <div>
          <p className="text-xs text-muted-foreground mb-1">Source A</p>
          <p className="font-mono text-xs break-all">{item.source_a_ref}</p>
        </div>
        <div>
          <p className="text-xs text-muted-foreground mb-1">Source B</p>
          <p className="font-mono text-xs break-all">{item.source_b_ref}</p>
        </div>
        <div>
          <Label>Resolution status</Label>
          <Select value={status} onValueChange={(v) => setStatus(v ?? status)}>
            <SelectTrigger className="mt-1">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {STATUSES.map((s) => (
                <SelectItem key={s} value={s}>{s}</SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
        <div>
          <Label htmlFor="con-desc">Notes (optional)</Label>
          <Textarea
            id="con-desc"
            className="mt-1"
            value={description}
            onChange={(e) => setDescription(e.target.value)}
          />
        </div>
      </div>
      <DialogFooter>
        <Button variant="outline" onClick={onClose}>Cancel</Button>
        <Button disabled={mut.isPending} onClick={() => mut.mutate()}>
          {mut.isPending ? "Saving…" : "Save"}
        </Button>
      </DialogFooter>
    </DialogContent>
  );
}

const columns: ColumnDef<Contradiction>[] = [
  {
    accessorKey: "contradiction_type",
    header: "Type",
    cell: ({ row }) => (
      <span className="rounded bg-muted px-2 py-0.5 text-xs">{row.getValue("contradiction_type")}</span>
    ),
  },
  {
    accessorKey: "source_a_ref",
    header: "Source A",
    cell: ({ row }) => (
      <span className="font-mono text-xs truncate max-w-[160px] block">{row.getValue("source_a_ref")}</span>
    ),
  },
  {
    accessorKey: "source_b_ref",
    header: "Source B",
    cell: ({ row }) => (
      <span className="font-mono text-xs truncate max-w-[160px] block">{row.getValue("source_b_ref")}</span>
    ),
  },
  {
    accessorKey: "resolution_status",
    header: "Status",
    cell: ({ row }) => <StatusBadge status={row.getValue("resolution_status")} />,
  },
  {
    accessorKey: "created_at",
    header: "Detected",
    cell: ({ row }) => new Date(row.getValue("created_at") as string).toLocaleDateString(),
  },
];

export default function ContradictionsPage() {
  const pg = usePagination(50);
  const [resolving, setResolving] = useState<Contradiction | null>(null);
  const [statusFilter, setStatusFilter] = useState("open");

  const { data = [], isLoading } = useQuery<Contradiction[]>({
    queryKey: ["contradictions", statusFilter, pg.page],
    queryFn: () => {
      const params: Record<string, string> = { ...pg.params };
      if (statusFilter !== "all") params.resolution_status = statusFilter;
      return api.get("/contradictions", params);
    },
  });

  return (
    <div className="space-y-6">
      <PageHeader
        title="Contradictions"
        description="Detected conflicts between approved playbooks and knowledge-base evidence."
      />

      <div className="flex items-center gap-3">
        <Label className="text-xs shrink-0">Show</Label>
        <Select value={statusFilter} onValueChange={(v) => { setStatusFilter(v ?? "open"); pg.reset(); }}>
          <SelectTrigger className="w-40">
            <SelectValue placeholder="All" />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="all">All</SelectItem>
            {STATUSES.map((s) => <SelectItem key={s} value={s}>{s}</SelectItem>)}
          </SelectContent>
        </Select>
      </div>

      {isLoading ? (
        <DataTableSkeleton columns={5} />
      ) : data.length === 0 ? (
        <div className="rounded-md border p-10 text-center text-sm text-muted-foreground">
          No contradictions match the current filter.
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
                  <Button variant="ghost" size="sm" onClick={() => setResolving(row.original)}>
                    Review
                  </Button>
                ),
              },
            ]}
            data={data}
          />
          <PaginationControls page={pg.page} pageSize={pg.pageSize} count={data.length} onPrev={pg.prevPage} onNext={pg.nextPage} />
        </>
      )}

      <Dialog open={!!resolving} onOpenChange={(o) => { if (!o) setResolving(null); }}>
        {resolving && <ResolveDialog item={resolving} onClose={() => setResolving(null)} />}
      </Dialog>
    </div>
  );
}
