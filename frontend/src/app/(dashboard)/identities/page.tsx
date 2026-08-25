"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ColumnDef } from "@tanstack/react-table";
import Link from "next/link";
import { useState } from "react";
import { toast } from "sonner";
import { Pencil, GitMerge } from "lucide-react";

import { PageHeader } from "@/components/common/page-header";
import { DataTable } from "@/components/common/data-table";
import { DataTableSkeleton } from "@/components/common/data-table-skeleton";
import { PaginationControls } from "@/components/common/pagination-controls";
import { usePagination } from "@/lib/hooks/use-pagination";
import { Button } from "@/components/ui/button";
import {
  Dialog, DialogContent, DialogFooter, DialogHeader, DialogTitle,
} from "@/components/ui/dialog";
import { Label } from "@/components/ui/label";
import { Input } from "@/components/ui/input";
import { Badge } from "@/components/ui/badge";
import { api } from "@/lib/api";
import type { CanonicalIdentity } from "@/lib/types";

// ── Edit Dialog ─────────────────────────────────────────────────────────────

function EditDialog({
  item,
  onClose,
}: {
  item: CanonicalIdentity;
  onClose: () => void;
}) {
  const qc = useQueryClient();
  const [canonicalName, setCanonicalName] = useState(item.canonical_name);
  const [entityType, setEntityType] = useState(item.entity_type);
  const [isActive, setIsActive] = useState(item.is_active);
  const [newAlias, setNewAlias] = useState("");

  const mut = useMutation({
    mutationFn: () =>
      api.patch(`/identities/${item.id}`, {
        canonical_name: canonicalName.trim() || undefined,
        entity_type: entityType.trim() || undefined,
        is_active: isActive,
        add_aliases: newAlias.trim() ? [newAlias.trim()] : [],
      }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["identities"] });
      toast.success("Identity updated");
      onClose();
    },
    onError: (err: Error) => toast.error(err.message || "Update failed"),
  });

  return (
    <DialogContent>
      <DialogHeader>
        <DialogTitle>Edit identity</DialogTitle>
      </DialogHeader>
      <div className="space-y-4 text-sm">
        <div>
          <Label htmlFor="id-name">Canonical name</Label>
          <Input
            id="id-name"
            className="mt-1"
            value={canonicalName}
            onChange={(e) => setCanonicalName(e.target.value)}
          />
        </div>
        <div>
          <Label htmlFor="id-type">Entity type</Label>
          <Input
            id="id-type"
            className="mt-1"
            value={entityType}
            onChange={(e) => setEntityType(e.target.value)}
            placeholder="e.g. person, service, device"
          />
        </div>
        <div className="flex items-center gap-3">
          <input
            id="id-active"
            type="checkbox"
            checked={isActive}
            onChange={(e) => setIsActive(e.target.checked)}
            className="h-4 w-4 rounded border-border"
          />
          <Label htmlFor="id-active">Active</Label>
        </div>
        <div>
          <Label htmlFor="id-alias">Add alias</Label>
          <Input
            id="id-alias"
            className="mt-1"
            value={newAlias}
            onChange={(e) => setNewAlias(e.target.value)}
            placeholder="Optional alias to add"
          />
          {item.aliases.length > 0 && (
            <div className="mt-2 flex flex-wrap gap-1">
              {item.aliases.map((a) => (
                <Badge key={a.id} variant="secondary" className="text-xs">{a.alias_text}</Badge>
              ))}
            </div>
          )}
        </div>
      </div>
      <DialogFooter>
        <Button variant="outline" onClick={onClose}>Cancel</Button>
        <Button disabled={mut.isPending} onClick={() => mut.mutate()}>
          {mut.isPending ? "Saving..." : "Save"}
        </Button>
      </DialogFooter>
    </DialogContent>
  );
}

// ── Merge Dialog ─────────────────────────────────────────────────────────────

function MergeDialog({
  primary,
  allIdentities,
  onClose,
}: {
  primary: CanonicalIdentity;
  allIdentities: CanonicalIdentity[];
  onClose: () => void;
}) {
  const qc = useQueryClient();
  const [duplicateId, setDuplicateId] = useState("");

  const mut = useMutation({
    mutationFn: () =>
      api.post("/identities/merge", {
        primary_identity_id: primary.id,
        duplicate_identity_id: duplicateId,
      }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["identities"] });
      toast.success("Identities merged");
      onClose();
    },
    onError: (err: Error) => toast.error(err.message || "Merge failed"),
  });

  const candidates = allIdentities.filter((i) => i.id !== primary.id);

  return (
    <DialogContent>
      <DialogHeader>
        <DialogTitle>Merge duplicate into primary</DialogTitle>
      </DialogHeader>
      <div className="space-y-4 text-sm">
        <div>
          <p className="text-xs text-muted-foreground mb-1">Primary (kept)</p>
          <p className="font-medium">{primary.canonical_name}</p>
          <p className="text-xs text-muted-foreground">{primary.entity_type}</p>
        </div>
        <div>
          <Label>Duplicate to merge in (will be deactivated)</Label>
          <select
            className="mt-1 w-full rounded-md border bg-background px-3 py-2 text-sm"
            value={duplicateId}
            onChange={(e) => setDuplicateId(e.target.value)}
          >
            <option value="">Select identity...</option>
            {candidates.map((c) => (
              <option key={c.id} value={c.id}>
                {c.canonical_name} ({c.entity_type})
              </option>
            ))}
          </select>
        </div>
      </div>
      <DialogFooter>
        <Button variant="outline" onClick={onClose}>Cancel</Button>
        <Button disabled={mut.isPending || !duplicateId} onClick={() => mut.mutate()}>
          {mut.isPending ? "Merging..." : "Merge"}
        </Button>
      </DialogFooter>
    </DialogContent>
  );
}

// ── Page ────────────────────────────────────────────────────────────────────

export default function IdentitiesPage() {
  const pg = usePagination(50);
  const [searchQuery, setSearchQuery] = useState("");
  const [submittedQuery, setSubmittedQuery] = useState("");
  const [editing, setEditing] = useState<CanonicalIdentity | null>(null);
  const [merging, setMerging] = useState<CanonicalIdentity | null>(null);

  const { data = [], error, isLoading } = useQuery<CanonicalIdentity[], Error>({
    queryKey: ["identities", submittedQuery, pg.page],
    queryFn: () => {
      const params: Record<string, string> = { ...pg.params };
      if (submittedQuery) params.query = submittedQuery;
      return api.get("/identities", params);
    },
  });

  const columns: ColumnDef<CanonicalIdentity>[] = [
    {
      accessorKey: "canonical_name",
      header: "Name",
      cell: ({ row }) => (
        <Link href={`/identities/${row.original.id}`} className="font-medium text-sm hover:underline">
          {row.getValue("canonical_name")}
        </Link>
      ),
    },
    {
      accessorKey: "entity_type",
      header: "Type",
      cell: ({ row }) => (
        <Badge variant="outline" className="text-xs">{row.getValue("entity_type")}</Badge>
      ),
    },
    {
      id: "aliases",
      header: "Aliases",
      cell: ({ row }) => {
        const aliases = row.original.aliases;
        if (!aliases.length) return <span className="text-muted-foreground text-xs">-</span>;
        return (
          <div className="flex flex-wrap gap-1">
            {aliases.slice(0, 3).map((a) => (
              <Badge key={a.id} variant="secondary" className="text-xs">{a.alias_text}</Badge>
            ))}
            {aliases.length > 3 && (
              <span className="text-xs text-muted-foreground">+{aliases.length - 3}</span>
            )}
          </div>
        );
      },
    },
    {
      accessorKey: "is_active",
      header: "Active",
      cell: ({ row }) => (
        <span className={row.getValue("is_active") ? "text-green-600 text-xs" : "text-muted-foreground text-xs"}>
          {row.getValue("is_active") ? "Yes" : "No"}
        </span>
      ),
    },
    {
      id: "actions",
      header: "",
      cell: ({ row }) => (
        <div className="flex items-center gap-1">
          <Button variant="ghost" size="icon" onClick={() => setEditing(row.original)}>
            <Pencil className="h-3.5 w-3.5" />
          </Button>
          <Button variant="ghost" size="icon" onClick={() => setMerging(row.original)}>
            <GitMerge className="h-3.5 w-3.5" />
          </Button>
        </div>
      ),
    },
  ];

  return (
    <div className="space-y-6">
      <PageHeader
        title="Identities"
        description="Names found in evidence are resolved into one trusted entity. Use this page to edit aliases and merge duplicates."
      />

      <div className="flex items-center gap-3">
        <Input
          placeholder="Search by name..."
          className="max-w-xs"
          value={searchQuery}
          onChange={(e) => setSearchQuery(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter") {
              setSubmittedQuery(searchQuery);
              pg.reset();
            }
          }}
        />
        <Button
          variant="outline"
          size="sm"
          onClick={() => { setSubmittedQuery(searchQuery); pg.reset(); }}
        >
          Search
        </Button>
        {submittedQuery && (
          <Button
            variant="ghost"
            size="sm"
            onClick={() => { setSearchQuery(""); setSubmittedQuery(""); pg.reset(); }}
          >
            Clear
          </Button>
        )}
      </div>

      {isLoading ? (
        <DataTableSkeleton columns={5} />
      ) : error ? (
        <div className="rounded-md border border-destructive/30 bg-destructive/5 p-6 text-sm">
          <p className="font-medium text-destructive">Identities could not be loaded.</p>
          <p className="mt-2 text-muted-foreground">
            {error.message || "Check that your user has the knowledge manager role."}
          </p>
        </div>
      ) : data.length === 0 ? (
        <div className="rounded-md border p-10 text-center text-sm">
          <p className="font-medium">No identities found yet.</p>
          <p className="mx-auto mt-2 max-w-2xl text-muted-foreground">
            Identities are created automatically after evidence is synced and processed.
            This page is for editing aliases and merging duplicates, so there is no manual
            create button here.
          </p>
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

      <Dialog open={!!editing} onOpenChange={(o) => { if (!o) setEditing(null); }}>
        {editing && <EditDialog item={editing} onClose={() => setEditing(null)} />}
      </Dialog>

      <Dialog open={!!merging} onOpenChange={(o) => { if (!o) setMerging(null); }}>
        {merging && (
          <MergeDialog
            primary={merging}
            allIdentities={data}
            onClose={() => setMerging(null)}
          />
        )}
      </Dialog>
    </div>
  );
}
