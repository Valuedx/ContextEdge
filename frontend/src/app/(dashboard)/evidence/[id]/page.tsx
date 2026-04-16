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
import type { AttachmentArtifact, EvidenceItemDetail, PoliciesOverview, ThreadSummary } from "@/lib/types";
import { useAuthStore } from "@/lib/stores/auth-store";
import { canListPoliciesForEvidence, canEditEvidenceAccessPolicy } from "@/lib/roles";
import { Loader2, Paperclip, RefreshCw } from "lucide-react";
import { toast } from "sonner";

const POLICY_NONE = "__none__";


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

  const { data: attachments = [] } = useQuery<AttachmentArtifact[]>({
    queryKey: ["evidence-attachments", evidenceId],
    queryFn: () => api.get(`/evidence/${evidenceId}/attachments`),
    enabled: !!evidenceId,
  });

  const hydrateMut = useMutation({
    mutationFn: () => api.post(`/threads/${item!.thread_id}/hydrate`, {}),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["thread", item?.thread_id] });
      toast.success("Thread hydration queued");
    },
    onError: (err: any) => toast.error(err.message || "Hydration failed"),
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
            <CardContent className="space-y-3 text-sm">
              <p className="font-medium">{thread.title || thread.external_thread_id}</p>
              <p className="text-muted-foreground">
                {thread.message_count} messages · {thread.participant_count} participants
              </p>
              <StatusBadge status={thread.hydration_status} />
              {thread.hydration_status !== "hydrated" && (
                <Button
                  variant="outline"
                  size="sm"
                  disabled={hydrateMut.isPending}
                  onClick={() => hydrateMut.mutate()}
                >
                  {hydrateMut.isPending ? (
                    <Loader2 className="mr-1.5 h-3.5 w-3.5 animate-spin" />
                  ) : (
                    <RefreshCw className="mr-1.5 h-3.5 w-3.5" />
                  )}
                  Hydrate thread
                </Button>
              )}
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

      {attachments.length > 0 && (
        <>
          <Separator />
          <Card>
            <CardHeader>
              <CardTitle className="text-base flex items-center gap-2">
                <Paperclip className="h-4 w-4" />
                Attachments
              </CardTitle>
            </CardHeader>
            <CardContent>
              <ul className="space-y-2">
                {attachments.map((a) => (
                  <li key={a.id} className="flex flex-wrap items-center gap-3 rounded-md border px-3 py-2 text-sm">
                    <span className="font-medium truncate max-w-xs">{a.file_name ?? "Unnamed file"}</span>
                    <span className="text-xs text-muted-foreground">{a.mime_type ?? "—"}</span>
                    <StatusBadge status={a.extraction_status} />
                    {a.extracted_text && (
                      <details className="w-full mt-1">
                        <summary className="cursor-pointer text-xs text-primary">View extracted text</summary>
                        <pre className="mt-1 max-h-40 overflow-auto rounded-md bg-muted p-2 text-xs whitespace-pre-wrap">
                          {a.extracted_text}
                        </pre>
                      </details>
                    )}
                  </li>
                ))}
              </ul>
            </CardContent>
          </Card>
        </>
      )}
    </div>
  );
}
