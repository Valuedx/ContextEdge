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
import {
  ApplicabilityPanel,
  KNOWLEDGE_EVIDENCE_TYPES,
} from "@/components/common/applicability";
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
import { ThreadConversation } from "@/components/common/thread-conversation";
import type {
  AttachmentArtifact,
  EvidenceItem,
  EvidenceItemDetail,
  PoliciesOverview,
  ThreadSummary,
} from "@/lib/types";
import { useAuthStore } from "@/lib/stores/auth-store";
import { canListPoliciesForEvidence, canEditEvidenceAccessPolicy } from "@/lib/roles";
import {
  Loader2,
  Paperclip,
  RefreshCw,
  FileText,
  Database,
  ExternalLink,
  ShieldCheck,
  Tag,
  Calendar,
  Layers,
  ArrowRight,
  Sparkles,
} from "lucide-react";
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

  // 1. Fetch Evidence item detail
  const { data: item, isLoading, error } = useQuery({
    queryKey: ["evidence", evidenceId],
    queryFn: () => api.get<EvidenceItemDetail>(`/evidence/${evidenceId}`),
    enabled: !!evidenceId,
  });

  // 2. Fetch Thread context if present
  const { data: thread } = useQuery({
    queryKey: ["thread", item?.thread_id],
    queryFn: () => api.get<ThreadSummary>(`/threads/${item!.thread_id}`),
    enabled: !!item?.thread_id,
  });

  // 2b. The other messages in that thread. Every hydrated message is its
  // own evidence row, so without this the page shows one message of a
  // conversation with no way to reach the rest of it.
  const { data: threadMessages = [], isLoading: threadMessagesLoading } = useQuery<
    EvidenceItem[]
  >({
    queryKey: ["thread-evidence", item?.thread_id],
    queryFn: () => api.get(`/threads/${item!.thread_id}/evidence`),
    enabled: !!item?.thread_id,
  });

  // 3. Fetch Attachments
  const { data: attachments = [] } = useQuery<AttachmentArtifact[]>({
    queryKey: ["evidence-attachments", evidenceId],
    queryFn: () => api.get(`/evidence/${evidenceId}/attachments`),
    enabled: !!evidenceId,
  });

  // 4. Hydrate thread mutation
  const hydrateMut = useMutation({
    mutationFn: () => api.post(`/threads/${item!.thread_id}/hydrate`, {}),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["thread", item?.thread_id] });
      // The messages are what hydration produces, so the list has to be
      // refetched too — invalidating only the summary leaves the count
      // updating while the conversation below it stays stale.
      qc.invalidateQueries({ queryKey: ["thread-evidence", item?.thread_id] });
      toast.success("Thread hydration queued");
    },
    onError: (err: Error) => toast.error(err.message || "Hydration failed"),
  });

  // 5. Fetch Policies list for governance
  const { data: policiesData } = useQuery({
    queryKey: ["policies"],
    queryFn: () => api.get<PoliciesOverview>("/policies"),
    enabled: !!evidenceId && policyListOk,
  });

  // 6. Fetch resolved Source/Domain names & Linked Episodes/Patterns context
  const { data: contextData } = useQuery<{
    source_name?: string;
    domain_name?: string;
    episodes: { id: string; title: string; case_ref?: string; status: string }[];
    patterns: { id: string; title: string; confidence: number }[];
    playbooks: { id: string; title: string; risk_tier: string }[];
  }>({
    queryKey: ["evidence-context", evidenceId],
    queryFn: () => api.get(`/evidence/${evidenceId}/context`),
    enabled: !!evidenceId,
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
      toast.success("Access policy updated successfully");
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
        <PageHeader title="Evidence Item" description="Not found." />
        <p className="text-sm text-destructive">{String((error as Error)?.message || "Missing")}</p>
        <Link href="/evidence" className={cn(buttonVariants({ variant: "outline" }))}>
          Back to evidence
        </Link>
      </div>
    );
  }

  return (
    <div className="space-y-6 max-w-6xl mx-auto pb-12">
      {/* Top Header */}
      <PageHeader
        title={item.title || "Untitled Evidence Ticket"}
        description={`Type: ${item.evidence_type} · Ingested ${new Date(item.ingested_at).toLocaleString()}`}
        actions={
          <Link href="/evidence" className={cn(buttonVariants({ variant: "outline", size: "sm" }))}>
            All Evidence
          </Link>
        }
      />

      {/* Primary Grid: Ticket Provenance & Linked Knowledge Context */}
      <div className="grid gap-6 md:grid-cols-2">
        {/* Card 1: Ticket Details & Provenance */}
        <Card className="bg-slate-900/80 border-slate-800 backdrop-blur-sm shadow-xl">
          <CardHeader className="pb-3 border-b border-slate-800/80">
            <CardTitle className="text-sm font-bold uppercase tracking-wider text-slate-300 flex items-center gap-2">
              <FileText className="h-4 w-4 text-indigo-400" />
              Ticket Metadata & Provenance
            </CardTitle>
          </CardHeader>
          <CardContent className="pt-4 space-y-3.5 text-sm">
            {/* The number a human can actually act on. Without it the
                only identifier on this page was the internal UUID, which
                cannot be searched for in the source system or quoted to
                anyone. */}
            {item.source_reference?.display_id && (
              <div className="flex items-center justify-between">
                <span className="text-slate-400">Source Record:</span>
                {item.source_reference.url ? (
                  <a
                    href={item.source_reference.url}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="font-mono font-medium text-indigo-400 hover:underline flex items-center gap-1"
                  >
                    #{item.source_reference.display_id}
                    <ExternalLink className="h-3.5 w-3.5" />
                  </a>
                ) : (
                  <span className="font-mono font-medium text-slate-200">
                    #{item.source_reference.display_id}
                  </span>
                )}
              </div>
            )}

            <div className="flex items-center justify-between">
              <span className="text-slate-400">Ingested Source:</span>
              <Link
                href={`/sources/${item.source_id}`}
                className="font-medium text-indigo-400 hover:underline flex items-center gap-1"
              >
                <Database className="h-3.5 w-3.5" />
                {contextData?.source_name || "SupportFlo Enterprise Connector"}
              </Link>
            </div>

            <div className="flex items-center justify-between">
              <span className="text-slate-400">Target Domain:</span>
              <span className="font-medium text-slate-200">
                {contextData?.domain_name || "General IT Operations"}
              </span>
            </div>

            <div className="flex items-center justify-between">
              <span className="text-slate-400">Evidence Type:</span>
              <span className="px-2 py-0.5 rounded text-xs font-mono bg-slate-800 text-slate-300 border border-slate-700">
                {item.evidence_type}
              </span>
            </div>

            <div className="flex items-center justify-between">
              <span className="text-slate-400">Relevance State:</span>
              <StatusBadge status={item.relevance_state} />
            </div>

            {item.sensitivity_label && (
              <div className="flex items-center justify-between">
                <span className="text-slate-400">Sensitivity Label:</span>
                <span className="rounded-md border border-amber-500/30 bg-amber-500/10 px-2 py-0.5 text-xs text-amber-400">
                  {item.sensitivity_label}
                </span>
              </div>
            )}
          </CardContent>
        </Card>

        {/* Card 2: Linked Knowledge Graph Context (Episode & Pattern) */}
        <Card className="bg-indigo-950/20 border-indigo-500/30 backdrop-blur-sm shadow-xl flex flex-col justify-between">
          <CardHeader className="pb-3 border-b border-indigo-500/20">
            <CardTitle className="text-sm font-bold uppercase tracking-wider text-indigo-300 flex items-center gap-2">
              <Sparkles className="h-4 w-4 text-indigo-400" />
              Linked Knowledge Graph Context
            </CardTitle>
          </CardHeader>
          <CardContent className="pt-4 space-y-3">
            {contextData && contextData.episodes.length > 0 ? (
              contextData.episodes.map((ep) => (
                <Link
                  key={ep.id}
                  href={`/episodes/${ep.id}`}
                  className="p-3 rounded-xl border border-emerald-500/30 bg-emerald-950/30 hover:border-emerald-500 transition-all block group"
                >
                  <div className="flex items-center justify-between mb-1">
                    <span className="text-[10px] font-bold uppercase tracking-wider text-emerald-400 flex items-center gap-1">
                      🟢 Connected Episode
                    </span>
                    <ArrowRight className="h-3.5 w-3.5 text-emerald-400 group-hover:translate-x-1 transition-transform" />
                  </div>
                  <p className="text-sm font-semibold text-slate-100 line-clamp-1">{ep.title}</p>
                </Link>
              ))
            ) : (
              <div className="p-3 rounded-xl border border-slate-800 bg-slate-900/50 text-xs text-slate-400">
                Awaiting episode extraction cluster...
              </div>
            )}

            {contextData && contextData.patterns.length > 0 ? (
              contextData.patterns.map((pat) => (
                <Link
                  key={pat.id}
                  href={`/patterns/${pat.id}`}
                  className="p-3 rounded-xl border border-indigo-500/30 bg-indigo-950/30 hover:border-indigo-500 transition-all block group"
                >
                  <div className="flex items-center justify-between mb-1">
                    <span className="text-[10px] font-bold uppercase tracking-wider text-indigo-400 flex items-center gap-1">
                      🟣 Connected Pattern
                    </span>
                    <ArrowRight className="h-3.5 w-3.5 text-indigo-400 group-hover:translate-x-1 transition-transform" />
                  </div>
                  <p className="text-sm font-semibold text-slate-100 line-clamp-1">{pat.title}</p>
                </Link>
              ))
            ) : (
              <div className="p-3 rounded-xl border border-slate-800 bg-slate-900/50 text-xs text-slate-400">
                No recurring pattern linked yet.
              </div>
            )}
          </CardContent>
        </Card>
      </div>

      {/* Where this article applies. Knowledge only — a ticket does not
          have an applicability, it has an environment. */}
      {KNOWLEDGE_EVIDENCE_TYPES.has(item.evidence_type) && (
        <ApplicabilityPanel
          applicability={item.applicability}
          className="rounded-xl border border-slate-800 bg-slate-900/80 p-4 text-slate-200"
        />
      )}

      {/* Main Ticket Body / Raw Payload Container */}
      <Card className="bg-slate-900/90 border-slate-800 shadow-2xl">
        <CardHeader className="border-b border-slate-800/80 pb-3 flex flex-row items-center justify-between">
          <CardTitle className="text-base font-bold text-slate-100 flex items-center gap-2">
            <FileText className="h-4 w-4 text-indigo-400" />
            {/* Naming this "Ticket Content" on a hydrated message is how a
                single reply reads as the whole ticket. Every message in a
                thread is its own evidence row, and `evidence_type` cannot
                tell them apart — normalization types them all as "ticket"
                — so the thread is the signal. */}
            {item.thread_id ? "Message Content" : "Raw Evidence Ticket Content"}
          </CardTitle>
          {/* Lead with the ticket number; the UUID stays for support
              but is not what a reviewer is looking for. */}
          {/* Truncated because a connector with no record number falls
              back to its external id, which for file sources is a long
              slug rather than a ticket number. */}
          <span
            className="text-xs text-slate-400 font-mono truncate max-w-[28rem]"
            title={item.id}
          >
            {item.source_reference?.display_id
              ? `#${item.source_reference.display_id} · ${item.id}`
              : `ID: ${item.id}`}
          </span>
        </CardHeader>
        <CardContent className="pt-4">
          {item.body_summary && (
            <div className="mb-4 p-3.5 rounded-xl border border-indigo-500/20 bg-indigo-950/20">
              <span className="text-xs font-bold uppercase tracking-wider text-indigo-400 block mb-1">
                Ticket Summary
              </span>
              <p className="text-sm text-slate-200 leading-relaxed">{item.body_summary}</p>
            </div>
          )}

          <div className="p-4 rounded-xl border border-slate-800 bg-[#020617] font-mono text-xs text-slate-300 whitespace-pre-wrap leading-relaxed">
            {item.body_text || "No body text stored for this evidence item."}
          </div>
        </CardContent>
      </Card>

      {/* The rest of the conversation this message belongs to. */}
      {item.thread_id && (
        <ThreadConversation
          thread={thread}
          messages={threadMessages}
          currentEvidenceId={item.id}
          isLoading={threadMessagesLoading}
          onHydrate={() => hydrateMut.mutate()}
          isHydrating={hydrateMut.isPending}
        />
      )}

      {/* Attachments Section */}
      {attachments.length > 0 && (
        <Card className="bg-slate-900/80 border-slate-800">
          <CardHeader>
            <CardTitle className="text-base flex items-center gap-2 text-slate-200">
              <Paperclip className="h-4 w-4 text-indigo-400" />
              Attachments ({attachments.length})
            </CardTitle>
          </CardHeader>
          <CardContent>
            <ul className="space-y-2">
              {attachments.map((a) => (
                <li key={a.id} className="flex flex-wrap items-center gap-3 rounded-lg border border-slate-800 bg-slate-900 p-3 text-sm">
                  <span className="font-medium text-slate-200 truncate max-w-xs">{a.file_name ?? "Unnamed file"}</span>
                  <span className="text-xs text-slate-400">{a.mime_type ?? "—"}</span>
                  <StatusBadge status={a.extraction_status} />
                  {a.extracted_text && (
                    <details className="w-full mt-2">
                      <summary className="cursor-pointer text-xs text-indigo-400 hover:underline">
                        View extracted text
                      </summary>
                      <pre className="mt-2 max-h-40 overflow-auto rounded-lg bg-[#020617] p-3 text-xs text-slate-300 font-mono whitespace-pre-wrap">
                        {a.extracted_text}
                      </pre>
                    </details>
                  )}
                </li>
              ))}
            </ul>
          </CardContent>
        </Card>
      )}

      {/* Bottom Accordion: Access Policy Governance (Moved to Secondary Control) */}
      {showAccessCard && (
        <Card className="bg-slate-900/50 border-slate-800">
          <CardHeader className="pb-3">
            <CardTitle className="text-sm font-semibold text-slate-400 flex items-center gap-2">
              <ShieldCheck className="h-4 w-4 text-slate-400" />
              Access Control & Retrieval Policy (Governance)
            </CardTitle>
          </CardHeader>
          <CardContent className="space-y-4 pt-1">
            {!policyListOk ? (
              <div className="text-sm text-slate-400">
                <span>Assigned Policy ID:</span>{" "}
                <span className="font-mono text-xs text-slate-300">{item.access_policy_id ?? "None"}</span>
              </div>
            ) : (
              <div className="flex flex-col sm:flex-row sm:items-center gap-4">
                <div className="space-y-1 flex-1 max-w-md">
                  <Label htmlFor="evidence-access-policy" className="text-xs text-slate-300">
                    Select Access Retrieval Policy
                  </Label>
                  <Select
                    value={accessId ?? POLICY_NONE}
                    onValueChange={(v) => setDraftAccess(v === POLICY_NONE ? null : (v ?? null))}
                    disabled={!canEditPolicy}
                  >
                    <SelectTrigger id="evidence-access-policy" className="bg-slate-900 border-slate-700">
                      <SelectValue placeholder="None" />
                    </SelectTrigger>
                    <SelectContent>
                      <SelectItem value={POLICY_NONE}>None (Unrestricted)</SelectItem>
                      {(policiesData?.access_policies ?? []).map((p) => (
                        <SelectItem key={p.id} value={p.id}>
                          {p.name} {!p.is_active ? "(inactive)" : ""}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                </div>
                {canEditPolicy && (
                  <Button
                    type="button"
                    size="sm"
                    className="sm:mt-5"
                    disabled={!accessDirty || patchAccessMut.isPending}
                    onClick={() => patchAccessMut.mutate()}
                  >
                    {patchAccessMut.isPending ? "Saving…" : "Save Policy"}
                  </Button>
                )}
              </div>
            )}
          </CardContent>
        </Card>
      )}
    </div>
  );
}
