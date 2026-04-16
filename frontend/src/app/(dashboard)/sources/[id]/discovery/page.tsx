"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ColumnDef } from "@tanstack/react-table";
import Link from "next/link";
import { useParams } from "next/navigation";
import { PageHeader } from "@/components/common/page-header";
import { DataTable } from "@/components/common/data-table";
import { DataTableSkeleton } from "@/components/common/data-table-skeleton";
import { Button, buttonVariants } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import { api } from "@/lib/api";
import type { SourceObject } from "@/lib/types";
import { useAuthStore } from "@/lib/stores/auth-store";
import { isTenantAdmin } from "@/lib/roles";

export default function DiscoveryPage() {
  const params = useParams<{ id: string }>();
  const sourceId = params.id;
  const qc = useQueryClient();
  const roles = useAuthStore((s) => s.roles);
  const canApprove = isTenantAdmin(roles);

  const { data: objects = [], isLoading } = useQuery({
    queryKey: ["source-objects", sourceId],
    queryFn: () => api.get<SourceObject[]>(`/sources/${sourceId}/objects`),
    enabled: !!sourceId,
  });

  const toggleMut = useMutation({
    mutationFn: ({
      objectId,
      body,
    }: {
      objectId: string;
      body: { approved_for_sync?: boolean; approved_for_backfill?: boolean };
    }) => api.patch(`/sources/${sourceId}/objects/${objectId}`, body),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["source-objects", sourceId] }),
  });

  const columns: ColumnDef<SourceObject>[] = [
    { accessorKey: "object_type", header: "Type" },
    {
      accessorKey: "display_name",
      header: "Name",
      cell: ({ row }) => (
        <div>
          <div className="font-medium">{row.getValue("display_name")}</div>
          <div className="text-xs text-muted-foreground font-mono">{row.original.external_id}</div>
        </div>
      ),
    },
    {
      accessorKey: "approved_for_sync",
      header: "Sync",
      cell: ({ row }) => {
        const o = row.original;
        if (!canApprove) {
          return o.approved_for_sync ? "Yes" : "No";
        }
        return (
          <Button
            variant="outline"
            size="sm"
            disabled={toggleMut.isPending}
            onClick={() =>
              toggleMut.mutate({
                objectId: o.id,
                body: { approved_for_sync: !o.approved_for_sync },
              })
            }
          >
            {o.approved_for_sync ? "Revoke sync" : "Approve sync"}
          </Button>
        );
      },
    },
    {
      accessorKey: "approved_for_backfill",
      header: "Backfill",
      cell: ({ row }) => {
        const o = row.original;
        if (!canApprove) {
          return o.approved_for_backfill ? "Yes" : "No";
        }
        return (
          <Button
            variant="outline"
            size="sm"
            disabled={toggleMut.isPending}
            onClick={() =>
              toggleMut.mutate({
                objectId: o.id,
                body: { approved_for_backfill: !o.approved_for_backfill },
              })
            }
          >
            {o.approved_for_backfill ? "Revoke backfill" : "Approve backfill"}
          </Button>
        );
      },
    },
    {
      accessorKey: "last_successful_sync_at",
      header: "Last sync",
      cell: ({ row }) => {
        const v = row.getValue("last_successful_sync_at") as string | null;
        return v ? new Date(v).toLocaleString() : "—";
      },
    },
  ];

  if (!sourceId) return null;

  return (
    <div className="space-y-6">
      <PageHeader
        title="Discovery inventory"
        description="Discovered objects for this source. Approve channels or mailboxes for sync and backfill."
        actions={
          <Link
            href={`/sources/${sourceId}`}
            className={cn(buttonVariants({ variant: "outline" }))}
          >
            Back to source
          </Link>
        }
      />

      {!canApprove && (
        <p className="text-sm text-muted-foreground">
          Tenant admin role is required to change approval flags.
        </p>
      )}

      {isLoading ? (
        <DataTableSkeleton columns={5} />
      ) : objects.length === 0 ? (
        <p className="text-sm text-muted-foreground">
          No discovered objects yet. Run discovery from the source detail page.
        </p>
      ) : (
        <DataTable columns={columns} data={objects} />
      )}
    </div>
  );
}
