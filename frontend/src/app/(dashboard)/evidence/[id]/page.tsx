"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import Link from "next/link";
import { useParams } from "next/navigation";
import { useState } from "react";

import { PageHeader } from "@/components/common/page-header";
import {
  DetailCardGridSkeleton,
  DetailPageSkeleton,
  DetailWideCardSkeleton,
} from "@/components/common/detail-page-skeleton";
import { StatusBadge } from "@/components/common/status-badge";
import { Button, buttonVariants } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Separator } from "@/components/ui/separator";
import { api } from "@/lib/api";
import type { EvidenceItemDetail, PoliciesOverview, ThreadSummary } from "@/lib/types";
import { useAuthStore } from "@/lib/stores/auth-store";

const POLICY_NONE = "__none__";

function hasRole(roles: string[], role: string) {
  return roles.includes(role) || roles.includes("platform_super_admin");
}

function canListPoliciesForEvidence(roles: string[]) {
  return (
    hasRole(roles, "tenant_admin") ||
    hasRole(roles, "domain_admin") ||
    hasRole(roles, "knowledge_manager")
  );
}

function canEditEvidenceAccessPolicy(roles: string[]) {
  return hasRole(roles, "domain_admin") || hasRole(roles, "knowledge_manager");
}

export default function EvidenceDetailPage() {
  const params = useParams<{ id: string }>();
  const evidenceId = params.id;
  const qc = useQueryClient();
  const roles = useAuthStore((s) => s.roles);
  const policyListOk = canListPoliciesForEvidence(roles);
  const canEditPolicy = canEditEvidenceAccessPolicy(roles);

  const [draftAccess, setDraftAccess] = useState<string | null | undefined>(undefined);

  const { data: item, isLoading, error } = useQuery({
    queryKey: ["evidence", evidenceId],
    queryFn: () => api.get<EvidenceItemDetail>(`/evidence/${evidenceId}`),
    enabled: !!evidenceId,
  });

  const { data: thread } = useQuery({
    queryKey: ["thread", item?.thread_id],
    queryFn: () => api.get<ThreadSummary>(`/threads/${item!.thread_id}`),
    enabled: !!item?.thread_id,
  });

  const { data: policiesData } = useQuery({
    queryKey: ["policies"],
    queryFn: () => api.get<PoliciesOverview>("/policies"),
    enabled: !!evidenceId && policyListOk,
  });

  const srvAccess = item?.access_policy_id ?? null;
  const accessId = draftAccess !== undefined ? draftAccess : srvAccess;
  const accessDirty = !!item && accessId !== srvAccess;

  const patchAccessMut = useMutation({
    mutationFn: () =>
      api.patch<EvidenceItemDetail>(`/evidence/${evidenceId}/access-policy`, {
        access_policy_id: accessId,
      }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["evidence", evidenceId] });
      setDraftAccess(undefined);
    },
  });

  const showAccessCard = policyListOk || !!(item?.access_policy_id);

  if (!evidenceId) return null;

  if (isLoading) {
    return (
      <DetailPageSkeleton>
        <DetailCardGridSkeleton count={2} />
        <DetailWideCardSkeleton lines={3} />
        <DetailWideCardSkeleton lines={6} />
      </DetailPageSkeleton>
    );
  }

  if (error || !item) {
    return (
      <div className="space-y-4">
        <PageHeader title="Evidence" description="Not found." />
        <p className="text-sm text-destructive">{String((error as Error)?.message || "Missing")}</p>
        <Link href="/evidence" className={cn(buttonVariants({ variant: "outline" }))}>
          Back to evidence
        </Link>
      </div>
    );
  }

  return (
    <div className="space-y-6">
      <PageHeader
        title={item.title || "Untitled evidence"}
        description={`${item.evidence_type} · ingested ${new Date(item.ingested_at).toLocaleString()}`}
        actions={
          <Link href="/evidence" className={cn(buttonVariants({ variant: "outline" }))}>
            All evidence
          </Link>
        }
      />

      <div className="flex flex-wrap gap-2">
        <StatusBadge status={item.relevance_state} />
        {item.sensitivity_label && (
          <span className="rounded-md border px-2 py-0.5 text-xs">{item.sensitivity_label}</span>
        )}
      </div>

      {showAccessCard && (
        <Card>
          <CardHeader>
            <CardTitle className="text-base">Access policy</CardTitle>
            <p className="text-sm text-muted-foreground">
              Optional retrieval access rule for this evidence item. Manage policies under{" "}
              <Link href="/policies" className="text-primary underline-offset-4 hover:underline">
                Policies
              </Link>
              .
            </p>
          </CardHeader>
          <CardContent className="space-y-4">
            {!policyListOk ? (
              <div className="text-sm">
                <span className="text-muted-foreground">Policy id</span>{" "}
                <span className="font-mono text-xs">{item.access_policy_id ?? "—"}</span>
              </div>
            ) : (
              <>
                <div className="space-y-2">
                  <Label htmlFor="evidence-access-policy">Access policy</Label>
                  <Select
                    value={accessId ?? POLICY_NONE}
                    onValueChange={(v) =>
                      setDraftAccess(v === POLICY_NONE ? null : (v ?? null))
                    }
                    disabled={!canEditPolicy}
                  >
                    <SelectTrigger id="evidence-access-policy" className="w-full max-w-md">
                      <SelectValue placeholder="None" />
                    </SelectTrigger>
                    <SelectContent>
                      <SelectItem value={POLICY_NONE}>None</SelectItem>
                      {(policiesData?.access_policies ?? []).map((p) => (
                        <SelectItem key={p.id} value={p.id}>
                          {p.name}
                          {!p.is_active ? " (inactive)" : ""}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                </div>
                {!canEditPolicy && (
                  <p className="text-sm text-muted-foreground">
                    Domain admin or knowledge manager role is required to change this assignment.
                  </p>
                )}
                {canEditPolicy && (
                  <div className="flex flex-col gap-2 sm:flex-row sm:items-center">
                    <Button
                      type="button"
                      size="sm"
                      disabled={!accessDirty || patchAccessMut.isPending}
                      onClick={() => patchAccessMut.mutate()}
                    >
                      {patchAccessMut.isPending ? "Saving…" : "Save access policy"}
                    </Button>
                    {patchAccessMut.isError && (
                      <p className="text-sm text-destructive">
                        {(patchAccessMut.error as Error).message}
                      </p>
                    )}
                  </div>
                )}
              </>
            )}
          </CardContent>
        </Card>
      )}

      <div className="grid gap-4 md:grid-cols-2">
        <Card>
          <CardHeader>
            <CardTitle className="text-base">Provenance</CardTitle>
          </CardHeader>
          <CardContent className="space-y-2 text-sm">
            <div>
              <span className="text-muted-foreground">Source</span>{" "}
              <Link href={`/sources/${item.source_id}`} className="font-mono text-xs text-primary hover:underline">
                {item.source_id}
              </Link>
            </div>
            {item.source_object_id && (
              <div>
                <span className="text-muted-foreground">Source object</span>{" "}
                <span className="font-mono text-xs">{item.source_object_id}</span>
              </div>
            )}
            {item.thread_id && (
              <div>
                <span className="text-muted-foreground">Thread</span>{" "}
                <span className="font-mono text-xs">{item.thread_id}</span>
              </div>
            )}
            <div>
              <span className="text-muted-foreground">Domain</span>{" "}
              <span className="font-mono text-xs">{item.domain_id ?? "—"}</span>
            </div>
          </CardContent>
        </Card>

        {thread && (
          <Card>
            <CardHeader>
              <CardTitle className="text-base">Thread context</CardTitle>
            </CardHeader>
            <CardContent className="space-y-2 text-sm">
              <p className="font-medium">{thread.title || thread.external_thread_id}</p>
              <p className="text-muted-foreground">
                {thread.message_count} messages · {thread.participant_count} participants
              </p>
              <StatusBadge status={thread.hydration_status} />
            </CardContent>
          </Card>
        )}
      </div>

      {item.body_summary && (
        <Card>
          <CardHeader>
            <CardTitle className="text-base">Summary</CardTitle>
          </CardHeader>
          <CardContent>
            <p className="text-sm whitespace-pre-wrap">{item.body_summary}</p>
          </CardContent>
        </Card>
      )}

      <Card>
        <CardHeader>
          <CardTitle className="text-base">Body</CardTitle>
        </CardHeader>
        <CardContent>
          <p className="text-sm whitespace-pre-wrap text-muted-foreground">
            {item.body_text || "No body text stored."}
          </p>
        </CardContent>
      </Card>

      {item.canonical_entity_refs && Object.keys(item.canonical_entity_refs).length > 0 && (
        <>
          <Separator />
          <Card>
            <CardHeader>
              <CardTitle className="text-base">Entity refs</CardTitle>
            </CardHeader>
            <CardContent>
              <pre className="overflow-auto rounded-md bg-muted p-3 text-xs">
                {JSON.stringify(item.canonical_entity_refs, null, 2)}
              </pre>
            </CardContent>
          </Card>
        </>
      )}
    </div>
  );
}
