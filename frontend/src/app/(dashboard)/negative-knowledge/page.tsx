"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ColumnDef } from "@tanstack/react-table";
import { useState } from "react";
import { toast } from "sonner";
import { Pencil, Trash2, Plus } from "lucide-react";

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
import { Input } from "@/components/ui/input";
import { api } from "@/lib/api";
import type { NegativeKnowledgeItem } from "@/lib/types";

const NK_STATUSES = ["ineffective", "conditionally_valid", "deprecated", "prohibited"];

// ── Create / Edit Dialog ────────────────────────────────────────────────────

function NKDialog({
  item,
  onClose,
}: {
  item: NegativeKnowledgeItem | null;
  onClose: () => void;
}) {
  const qc = useQueryClient();
  const isEdit = !!item;

  const [stepText, setStepText] = useState(item?.step_text ?? "");
  const [failureReason, setFailureReason] = useState(item?.failure_reason ?? "");
  const [status, setStatus] = useState(item?.status ?? "ineffective");

  const mut = useMutation({
    mutationFn: () =>
      isEdit
        ? api.patch(`/negative-knowledge/${item!.id}`, {
            step_text: stepText.trim() || undefined,
            failure_reason: failureReason.trim() || null,
            status,
          })
        : api.post("/negative-knowledge", {
            step_text: stepText.trim(),
            failure_reason: failureReason.trim() || null,
            status,
          }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["negative-knowledge"] });
      toast.success(isEdit ? "Item updated" : "Item created");
      onClose();
    },
    onError: (err: any) => toast.error(err.message || "Save failed"),
  });

  return (
    <DialogContent>
      <DialogHeader>
        <DialogTitle>{isEdit ? "Edit item" : "New negative-knowledge item"}</DialogTitle>
      </DialogHeader>
      <div className="space-y-4 text-sm">
        <div>
          <Label htmlFor="nk-step">Step text *</Label>
          <Textarea
            id="nk-step"
            className="mt-1"
            rows={3}
            value={stepText}
            onChange={(e) => setStepText(e.target.value)}
            placeholder="Describe the step that was found to be ineffective…"
          />
        </div>
        <div>
          <Label htmlFor="nk-reason">Failure reason</Label>
          <Input
            id="nk-reason"
            className="mt-1"
            value={failureReason}
            onChange={(e) => setFailureReason(e.target.value)}
            placeholder="Optional reason for failure"
          />
        </div>
        <div>
          <Label>Status</Label>
          <Select value={status} onValueChange={(v) => setStatus(v ?? status)}>
            <SelectTrigger className="mt-1">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {NK_STATUSES.map((s) => (
                <SelectItem key={s} value={s}>{s}</SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
      </div>
      <DialogFooter>
        <Button variant="outline" onClick={onClose}>Cancel</Button>
        <Button
          disabled={mut.isPending || !stepText.trim()}
          onClick={() => mut.mutate()}
        >
          {mut.isPending ? "Saving…" : "Save"}
        </Button>
      </DialogFooter>
    </DialogContent>
  );
}

// ── Delete Confirm Dialog ───────────────────────────────────────────────────

function DeleteDialog({
  item,
  onClose,
}: {
  item: NegativeKnowledgeItem;
  onClose: () => void;
}) {
  const qc = useQueryClient();

  const del = useMutation({
    mutationFn: () => api.delete(`/negative-knowledge/${item.id}`),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["negative-knowledge"] });
      toast.success("Item deleted");
      onClose();
    },
    onError: (err: any) => toast.error(err.message || "Delete failed"),
  });

  return (
    <DialogContent>
      <DialogHeader>
        <DialogTitle>Delete item?</DialogTitle>
      </DialogHeader>
      <p className="text-sm text-muted-foreground">
        This will permanently remove the negative-knowledge entry. This action cannot be undone.
      </p>
      <p className="mt-2 rounded bg-muted px-3 py-2 font-mono text-xs break-all">{item.step_text}</p>
      <DialogFooter>
        <Button variant="outline" onClick={onClose}>Cancel</Button>
        <Button variant="destructive" disabled={del.isPending} onClick={() => del.mutate()}>
          {del.isPending ? "Deleting…" : "Delete"}
        </Button>
      </DialogFooter>
    </DialogContent>
  );
}

// ── Page ────────────────────────────────────────────────────────────────────

export default function NegativeKnowledgePage() {
  const pg = usePagination(50);
  const [statusFilter, setStatusFilter] = useState("all");
  const [editing, setEditing] = useState<NegativeKnowledgeItem | null | "new">(null);
  const [deleting, setDeleting] = useState<NegativeKnowledgeItem | null>(null);

  const { data = [], isLoading } = useQuery<NegativeKnowledgeItem[]>({
    queryKey: ["negative-knowledge", statusFilter, pg.page],
    queryFn: () => {
      const params: Record<string, string> = { ...pg.params };
      if (statusFilter !== "all") params.status = statusFilter;
      return api.get("/negative-knowledge", params);
    },
  });

  const columns: ColumnDef<NegativeKnowledgeItem>[] = [
    {
      accessorKey: "step_text",
      header: "Step",
      cell: ({ row }) => (
        <span className="line-clamp-2 max-w-sm text-sm">{row.getValue("step_text")}</span>
      ),
    },
    {
      accessorKey: "failure_reason",
      header: "Reason",
      cell: ({ row }) => (
        <span className="text-xs text-muted-foreground">{row.getValue("failure_reason") ?? "—"}</span>
      ),
    },
    {
      accessorKey: "status",
      header: "Status",
      cell: ({ row }) => <StatusBadge status={row.getValue("status")} />,
    },
    {
      accessorKey: "created_at",
      header: "Created",
      cell: ({ row }) => new Date(row.getValue("created_at") as string).toLocaleDateString(),
    },
    {
      id: "actions",
      header: "",
      cell: ({ row }) => (
        <div className="flex items-center gap-1">
          <Button variant="ghost" size="icon" onClick={() => setEditing(row.original)}>
            <Pencil className="h-3.5 w-3.5" />
          </Button>
          <Button variant="ghost" size="icon" onClick={() => setDeleting(row.original)}>
            <Trash2 className="h-3.5 w-3.5 text-destructive" />
          </Button>
        </div>
      ),
    },
  ];

  return (
    <div className="space-y-6">
      <PageHeader
        title="Negative Knowledge"
        description="Steps and approaches documented as ineffective, conditional, deprecated, or prohibited."
        actions={
          <Button size="sm" onClick={() => setEditing("new")}>
            <Plus className="mr-1.5 h-4 w-4" />
            Add item
          </Button>
        }
      />

      <div className="flex items-center gap-3">
        <Label className="text-xs shrink-0">Filter by status</Label>
        <Select value={statusFilter} onValueChange={(v) => { setStatusFilter(v ?? "all"); pg.reset(); }}>
          <SelectTrigger className="w-48">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="all">All</SelectItem>
            {NK_STATUSES.map((s) => <SelectItem key={s} value={s}>{s}</SelectItem>)}
          </SelectContent>
        </Select>
      </div>

      {isLoading ? (
        <DataTableSkeleton columns={5} />
      ) : data.length === 0 ? (
        <div className="rounded-md border p-10 text-center text-sm text-muted-foreground">
          No items match the current filter.
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

      {/* Create / Edit dialog */}
      <Dialog
        open={editing !== null}
        onOpenChange={(o) => { if (!o) setEditing(null); }}
      >
        {editing !== null && (
          <NKDialog
            item={editing === "new" ? null : editing}
            onClose={() => setEditing(null)}
          />
        )}
      </Dialog>

      {/* Delete confirm dialog */}
      <Dialog open={!!deleting} onOpenChange={(o) => { if (!o) setDeleting(null); }}>
        {deleting && (
          <DeleteDialog item={deleting} onClose={() => setDeleting(null)} />
        )}
      </Dialog>
    </div>
  );
}
