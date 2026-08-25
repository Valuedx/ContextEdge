"use client";

import { useQuery } from "@tanstack/react-query";
import Link from "next/link";
import { useParams } from "next/navigation";
import { Network } from "lucide-react";

import { PageHeader } from "@/components/common/page-header";
import {
  DetailPageSkeleton,
  DetailWideCardSkeleton,
} from "@/components/common/detail-page-skeleton";
import { Badge } from "@/components/ui/badge";
import { buttonVariants } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { api } from "@/lib/api";
import { cn } from "@/lib/utils";
import type { CanonicalIdentity } from "@/lib/types";

export default function IdentityDetailPage() {
  const params = useParams<{ id: string }>();
  const identityId = params.id;

  const { data: identity, isLoading, error } = useQuery({
    queryKey: ["identity", identityId],
    queryFn: () => api.get<CanonicalIdentity>(`/identities/${identityId}`),
    enabled: !!identityId,
  });

  if (!identityId) return null;

  if (isLoading) {
    return (
      <DetailPageSkeleton>
        <DetailWideCardSkeleton lines={6} />
      </DetailPageSkeleton>
    );
  }

  if (error || !identity) {
    return (
      <div className="space-y-4">
        <PageHeader
          title="Identity"
          description="Not found."
          backHref="/identities"
          backLabel="Identities"
        />
        <p className="text-sm text-destructive">
          {String((error as Error)?.message || "This identity record does not exist or you do not have access.")}
        </p>
      </div>
    );
  }

  return (
    <div className="space-y-6">
      <PageHeader
        title={identity.canonical_name}
        description={`${identity.entity_type} · ${identity.resolution_state ?? "resolved"}`}
        backHref="/identities"
        backLabel="Identities"
        actions={
          <Link
            href={`/graph-explorer?tab=subgraph&node_type=identity&node_id=${identity.id}&from=${encodeURIComponent(`/identities/${identity.id}`)}`}
            className={cn(buttonVariants({ size: "sm" }))}
          >
            <Network className="mr-1.5 h-3.5 w-3.5" />
            Open in graph
          </Link>
        }
      />

      <div className="grid gap-4 md:grid-cols-2">
        <Card>
          <CardHeader>
            <CardTitle className="text-base">Record</CardTitle>
          </CardHeader>
          <CardContent className="space-y-3 text-sm">
            <div>
              <p className="text-xs font-medium uppercase tracking-wide text-muted-foreground">Type</p>
              <Badge variant="outline" className="mt-1">{identity.entity_type}</Badge>
            </div>
            <div>
              <p className="text-xs font-medium uppercase tracking-wide text-muted-foreground">Status</p>
              <p className="mt-1">
                {identity.is_active ? "Active" : "Inactive"}
                {identity.resolution_state ? ` · ${identity.resolution_state}` : ""}
              </p>
            </div>
            {identity.resolution_method && (
              <div>
                <p className="text-xs font-medium uppercase tracking-wide text-muted-foreground">Resolution method</p>
                <p className="mt-1 text-muted-foreground">{identity.resolution_method}</p>
              </div>
            )}
            <div>
              <p className="text-xs font-medium uppercase tracking-wide text-muted-foreground">ID</p>
              <p className="mt-1 font-mono text-xs text-muted-foreground">{identity.id}</p>
            </div>
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle className="text-base">Aliases</CardTitle>
          </CardHeader>
          <CardContent>
            {identity.aliases.length === 0 ? (
              <p className="text-sm text-muted-foreground">No aliases recorded.</p>
            ) : (
              <div className="flex flex-wrap gap-2">
                {identity.aliases.map((alias) => (
                  <Badge key={alias.id} variant="secondary">
                    {alias.alias_text}
                  </Badge>
                ))}
              </div>
            )}
          </CardContent>
        </Card>
      </div>

      {identity.metadata_extra && Object.keys(identity.metadata_extra).length > 0 && (
        <Card>
          <CardHeader>
            <CardTitle className="text-base">Metadata</CardTitle>
          </CardHeader>
          <CardContent>
            <pre className="overflow-x-auto rounded-md border bg-muted p-3 text-xs">
              {JSON.stringify(identity.metadata_extra, null, 2)}
            </pre>
          </CardContent>
        </Card>
      )}
    </div>
  );
}
