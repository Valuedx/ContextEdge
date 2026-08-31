"use client";

/**
 * Pipeline health.
 *
 * The cost dashboard answers "what is this run spending". This one answers
 * "is it getting anywhere", which is a different question with a different
 * failure mode: a run can spend steadily, fail no task, and build nothing.
 *
 * That is not hypothetical. On the live Zoho backfill every visible number
 * said healthy — evidence climbing, tokens climbing, zero failures — while
 * `correlate_evidence` (a 0.25s task) sat behind 8,000 thirty-second
 * `normalize_evidence` tasks in one FIFO that grew by ~70 tasks a minute,
 * because thread hydration turns one ticket into ~41 more normalize tasks.
 * Episodes stayed at zero for hours and nothing anywhere said why.
 *
 * So the two things this page puts first are the two that were missing:
 * queue depth per lane, and the graph chain counted end to end. The first
 * zero in that chain is the diagnosis — everything after it is waiting.
 *
 * Refetches every 10s: this is watched live during an ingest, and a stalled
 * pipeline should be visible in seconds rather than at the next coffee break.
 */

import { useQuery } from "@tanstack/react-query";
import { useEffect, useMemo, useSyncExternalStore } from "react";
import {
  Activity,
  AlertTriangle,
  ArrowRight,
  BookOpen,
  CheckCircle2,
  Database,
  FileText,
  Gauge,
  Info,
  Layers,
  Loader,
  MessageSquare,
  MessagesSquare,
  Timer,
  XCircle,
} from "lucide-react";

import { PageHeader } from "@/components/common/page-header";
import { Badge } from "@/components/ui/badge";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { api } from "@/lib/api";
import { cn } from "@/lib/utils";

interface PipelineHealth {
  counts: {
    evidence: number;
    tickets?: number;
    kb_articles?: number;
    thread_messages?: number;
    threads?: number;
    threads_hydrated?: number;
    threads_pending?: number;
    threads_10min?: number;
    thread_messages_10min?: number;
    evidence_10min: number;
    embedded: number;
    embed_gap: number;
    raw_objects: number;
    identities: number;
    case_links: number;
    episodes: number;
    episodes_10min: number;
    episodes_pending: number;
    episodes_approved: number;
    chunks_total: number;
    chunks_embedded: number;
    patterns: number;
    playbooks: number;
  };
  throughput_per_10min: number;
  episodes_per_10min: number;
  in_flight: number;
  spend_last_hour_usd: number;
  queues: Record<string, number>;
  latency_10min: { calls: number; p50_ms: number; p95_ms: number; max_ms: number };
  by_call_60min: { call: string; calls: number; p50_ms: number; tokens: number }[];
  graph_chain: { stage: string; count: number }[];
  stalled_at: string | null;
  alerts: { level: string; message: string }[];
}

function formatNumber(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(2)}M`;
  if (n >= 10_000) return `${(n / 1_000).toFixed(1)}k`;
  return n.toLocaleString();
}

function formatCount(n: number): string {
  return n.toLocaleString();
}

function formatMs(ms: number): string {
  if (!ms) return "—";
  if (ms >= 1000) return `${(ms / 1000).toFixed(1)}s`;
  return `${ms}ms`;
}

function formatDuration(seconds: number): string {
  if (seconds < 90) return "under 2 min";
  if (seconds < 3600) return `~${Math.round(seconds / 60)} min`;
  const hours = Math.floor(seconds / 3600);
  const minutes = Math.round((seconds % 3600) / 60);
  return `~${hours}h ${minutes}m`;
}

const STAGE_LABELS: Record<string, { title: string; hint: string }> = {
  evidence: { title: "Evidence", hint: "Tickets and articles ingested" },
  threads: { title: "Threads", hint: "Mail/chat on tickets only — not KB articles" },
  messages: { title: "Messages", hint: "Replies stored on those threads" },
  correlations: { title: "Links", hint: "Related evidence tied together" },
  episodes: { title: "Incidents", hint: "Timelines built from those links" },
  patterns: { title: "Patterns", hint: "Issues that keep coming back" },
  playbooks: { title: "Playbooks", hint: "Guided procedures" },
};

const QUEUE_LABELS: Record<string, string> = {
  extraction: "Reading tickets",
  correlation: "Linking related tickets",
  embedding: "Making searchable",
  hydration: "Fetching threads",
  pattern: "Finding recurrences",
  evaluation: "Quality checks",
  sync: "Source sync",
  default: "Other jobs",
};

const CALL_LABELS: Record<string, string> = {
  relevance: "Ticket relevance",
  episode: "Incident reconstruction",
  episode_review: "Incident review",
  playbook: "Playbook writing",
  pattern: "Pattern clustering",
  identity: "Identity matching",
  identity_adjudication: "Identity review",
  identity_reconciliation: "Identity merge",
  decision: "Decision tracing",
  contradiction: "Contradiction check",
  knowledge_applicability: "KB applicability",
  message_function: "Message role",
  issue_signature: "Issue signature",
};

function friendlyName(map: Record<string, string>, key: string): string {
  if (map[key]) return map[key];
  return key.replace(/_/g, " ").replace(/\b\w/g, (char) => char.toUpperCase());
}

function Stat({
  value,
  label,
  hint,
  exact,
}: {
  value: string | number;
  label: string;
  hint?: string;
  exact?: boolean;
}) {
  return (
    <div className="min-w-0">
      <div className="text-2xl font-semibold tabular-nums tracking-tight">
        {typeof value === "number"
          ? exact
            ? formatCount(value)
            : formatNumber(value)
          : value}
      </div>
      <div className="mt-0.5 text-xs font-medium text-foreground">{label}</div>
      {hint ? (
        <div className="mt-0.5 text-[11px] leading-4 text-muted-foreground">{hint}</div>
      ) : null}
    </div>
  );
}

function StepNum({ n }: { n: number }) {
  return (
    <span className="flex h-5 w-5 shrink-0 items-center justify-center rounded-full bg-primary/15 text-[10px] font-semibold text-primary">
      {n}
    </span>
  );
}

/**
 * Live drain-rate estimate from consecutive poll samples.
 *
 * The server is deliberately stateless, so the rate comes from watching
 * `remaining` (queued + in-flight) move between polls in THIS browser tab.
 * A two-minute sliding window smooths worker burstiness; until the window
 * has ≥60s of samples the ETA reads "estimating". A non-positive rate is
 * reported as such (backlog growing), never as a fake ETA.
 */
type DrainRate = {
  perMinute: number | null;
  warmedUp: boolean;
};

type DrainSample = { t: number; remaining: number };

const COLD_DRAIN_RATE: DrainRate = { perMinute: null, warmedUp: false };

function calculateDrainRate(samples: DrainSample[]): DrainRate {
  if (samples.length === 0) return COLD_DRAIN_RATE;
  const first = samples[0];
  const last = samples[samples.length - 1];
  const spanSeconds = (last.t - first.t) / 1000;
  if (spanSeconds < 60) return COLD_DRAIN_RATE;
  const drained = first.remaining - last.remaining;
  return { perMinute: (drained / spanSeconds) * 60, warmedUp: true };
}

function createDrainRateStore() {
  let samples: DrainSample[] = [];
  let snapshot = COLD_DRAIN_RATE;
  const listeners = new Set<() => void>();

  return {
    addSample(remaining: number) {
      const now = Date.now();
      samples = [...samples, { t: now, remaining }].filter((s) => now - s.t <= 120_000);
      snapshot = calculateDrainRate(samples);
      listeners.forEach((listener) => listener());
    },
    getSnapshot() {
      return snapshot;
    },
    subscribe(listener: () => void) {
      listeners.add(listener);
      return () => listeners.delete(listener);
    },
  };
}

function useDrainRate(remaining: number | undefined): DrainRate {
  const store = useMemo(() => createDrainRateStore(), []);
  const rate = useSyncExternalStore(store.subscribe, store.getSnapshot, store.getSnapshot);

  useEffect(() => {
    if (remaining === undefined) return;
    store.addSample(remaining);
  }, [remaining, store]);

  return rate;
}

const ALERT_STYLES: Record<string, string> = {
  critical: "border-destructive/50 bg-destructive/10 text-destructive",
  warning: "border-amber-500/50 bg-amber-500/10 text-amber-700 dark:text-amber-400",
  info: "border-sky-500/50 bg-sky-500/10 text-sky-700 dark:text-sky-400",
};

export default function PipelineHealthPage() {
  const { data, isLoading, isError } = useQuery<PipelineHealth>({
    queryKey: ["pipeline-health"],
    queryFn: () => api.get<PipelineHealth>("/admin/pipeline-health"),
    refetchInterval: 5_000,
  });

  const remaining =
    data === undefined
      ? undefined
      : Object.values(data.queues).reduce((a, b) => a + b, 0) + data.in_flight;
  const drain = useDrainRate(remaining);

  if (isLoading) {
    return (
      <div className="space-y-6">
        <PageHeader title="Pipeline health" description="Loading…" />
      </div>
    );
  }

  if (isError || !data) {
    return (
      <div className="space-y-6">
        <PageHeader
          title="Pipeline health"
          description="How ingest is progressing — waiting work, speed, and how far tickets have become playbooks."
        />
        <Card>
          <CardContent className="py-10 text-center text-sm text-muted-foreground">
            Pipeline health is available to tenant administrators.
          </CardContent>
        </Card>
      </div>
    );
  }

  const { counts, queues, latency_10min: latency } = data;
  // Only relevant items count as "awaiting": not_relevant rows skip
  // embedding by design (the relevance gate's cost short-circuit).
  const embedGap = counts.embed_gap;
  const evidenceCount = counts.evidence;
  const ticketCount = counts.tickets ?? 0;
  const kbCount = counts.kb_articles ?? 0;
  const threadCount = counts.threads ?? 0;
  const threadMessages = counts.thread_messages ?? 0;
  const threadsHydrated = counts.threads_hydrated ?? 0;
  const threadsPending = counts.threads_pending ?? 0;
  const threads10min = counts.threads_10min ?? 0;
  const threadMessages10min = counts.thread_messages_10min ?? 0;
  const hydrationQueue = queues.hydration ?? 0;
  const hydrationPct = threadCount > 0 ? Math.min(100, (threadsHydrated / threadCount) * 100) : 0;
  const avgMessages =
    threadCount > 0 ? Math.round((threadMessages / threadCount) * 10) / 10 : 0;
  const ticketsWithThreadPct =
    ticketCount > 0 ? Math.round((threadCount / ticketCount) * 100) : 0;
  const otherEvidence = Math.max(0, evidenceCount - ticketCount - kbCount);

  let statusLabel = "Caught up";
  let statusHint = "No meaningful backlog. The pipeline is idle or handling a slow trickle.";
  if (data.stalled_at && evidenceCount > 0) {
    statusLabel = `Waiting on ${STAGE_LABELS[data.stalled_at]?.title ?? data.stalled_at}`;
    statusHint = `Later steps cannot run until ${STAGE_LABELS[data.stalled_at]?.title ?? data.stalled_at} produces work.`;
  } else if (remaining !== undefined && remaining > 10) {
    statusLabel = "Processing";
    statusHint = `${formatNumber(remaining)} jobs still in line.`;
  }

  return (
    <div className="space-y-6">
      <PageHeader
        title="Pipeline health"
        description="Follow one path: Evidence lands, its Thread is fetched, Messages are stored on that thread, then incidents and playbooks are built."
      />

      {data.alerts.length > 0 && (
        <div className="space-y-2">
          {data.alerts.map((alert, i) => (
            <div
              key={i}
              className={cn(
                "flex items-start gap-2 rounded-lg border px-4 py-3 text-sm",
                ALERT_STYLES[alert.level] ?? ALERT_STYLES.info,
              )}
            >
              {alert.level === "critical" ? (
                <XCircle className="mt-0.5 h-4 w-4 shrink-0" />
              ) : alert.level === "warning" ? (
                <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" />
              ) : (
                <Info className="mt-0.5 h-4 w-4 shrink-0" />
              )}
              <span>{alert.message}</span>
            </div>
          ))}
        </div>
      )}

      <div className="flex flex-wrap items-center gap-3 rounded-lg border bg-card px-4 py-3 shadow-sm">
        <Badge
          variant="outline"
          className={cn(
            "text-xs",
            statusLabel === "Caught up" && "border-emerald-500/40 bg-emerald-500/10 text-emerald-700 dark:text-emerald-300",
            statusLabel === "Processing" && "border-sky-500/40 bg-sky-500/10 text-sky-700 dark:text-sky-300",
            statusLabel.startsWith("Waiting") && "border-amber-500/40 bg-amber-500/10 text-amber-800 dark:text-amber-300",
          )}
        >
          {statusLabel}
        </Badge>
        <p className="text-sm text-muted-foreground">{statusHint}</p>
      </div>

      <Card>
        <CardHeader className="border-b pb-3">
          <CardTitle className="flex items-center gap-2">
            <Timer className="h-4 w-4 text-primary" />
            What's left
          </CardTitle>
          <CardDescription className="text-xs">
            Jobs still waiting, how fast they finish, and the time and cost that implies.
          </CardDescription>
        </CardHeader>
        <CardContent className="pt-4">
          {remaining !== undefined && remaining <= 10 ? (
            <p className="text-sm text-muted-foreground">
              Nothing waiting. Spent ${data.spend_last_hour_usd.toFixed(2)} in the last hour.
            </p>
          ) : (
            <div className="grid grid-cols-2 gap-x-6 gap-y-4 md:grid-cols-4">
              <Stat value={remaining ?? 0} label="Jobs still waiting" hint="Queued plus being processed" />
              <Stat
                value={
                  !drain.warmedUp
                    ? "…"
                    : drain.perMinute !== null && drain.perMinute > 0
                      ? drain.perMinute.toFixed(1)
                      : "0"
                }
                label="Jobs finished / min"
                hint={
                  !drain.warmedUp
                    ? "Measuring — needs about a minute"
                    : drain.perMinute !== null && drain.perMinute <= 0
                      ? "Backlog is not shrinking"
                      : undefined
                }
              />
              <Stat
                value={
                  drain.warmedUp && drain.perMinute !== null && drain.perMinute > 0
                    ? formatDuration(((remaining ?? 0) / drain.perMinute) * 60)
                    : "—"
                }
                label="Time left"
              />
              <Stat
                value={
                  drain.warmedUp && drain.perMinute !== null && drain.perMinute > 0
                    ? `$${(
                        data.spend_last_hour_usd *
                        ((remaining ?? 0) / drain.perMinute / 60)
                      ).toFixed(2)}`
                    : `$${data.spend_last_hour_usd.toFixed(2)}/hr`
                }
                label={
                  drain.warmedUp && drain.perMinute !== null && drain.perMinute > 0
                    ? "Cost to finish"
                    : "Spent last hour"
                }
                hint={
                  drain.warmedUp && drain.perMinute !== null && drain.perMinute > 0
                    ? `At $${data.spend_last_hour_usd.toFixed(2)}/hr`
                    : undefined
                }
              />
            </div>
          )}
        </CardContent>
      </Card>

      <Card>
        <CardHeader className="border-b pb-3">
          <CardTitle className="flex items-center gap-2">
            <MessagesSquare className="h-4 w-4 text-primary" />
            Step 1 — Evidence, threads, and messages
          </CardTitle>
          <CardDescription className="text-xs">
            Three layers on a Zoho ticket: the ticket itself (Evidence), the email
            thread on it (Thread), and each reply in that thread (Messages). KB
            articles are Evidence too, but they do not get a Thread.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-5 pt-4">
          <div className="rounded-lg border border-dashed bg-muted/20 px-4 py-3 text-xs leading-5 text-muted-foreground">
            <p className="font-medium text-foreground">How to read these numbers</p>
            <ul className="mt-2 list-inside list-disc space-y-1">
              <li>
                <span className="font-medium text-foreground">Evidence</span> — parent
                records only (tickets, KB articles, and any other ingested types). Use the
                breakdown row below for live counts. Replies are not included.
              </li>
              <li>
                <span className="font-medium text-foreground">Threads</span> — mail or
                chat on tickets only. Compare to the ticket count, not total Evidence.
                KB articles do not get a Thread.
              </li>
              <li>
                <span className="font-medium text-foreground">Messages</span> — each
                reply stored inside a Thread. Also not Evidence.
              </li>
            </ul>
          </div>
          <div className="flex flex-col gap-3 lg:flex-row lg:items-stretch">
            <div className="flex min-w-0 flex-1 items-start gap-3 rounded-lg border bg-muted/30 p-3">
              <StepNum n={1} />
              <FileText className="mt-0.5 h-4 w-4 shrink-0 text-primary" />
              <Stat
                value={evidenceCount}
                label="Evidence"
                exact
                hint={
                  typeof counts.tickets === "number" || typeof counts.kb_articles === "number"
                    ? "Live split in the row below — same list as the Evidence tab"
                    : "Same list as the Evidence tab"
                }
              />
            </div>
            <ArrowRight className="hidden h-4 w-4 shrink-0 self-center text-muted-foreground lg:block" />
            <div className="flex min-w-0 flex-1 items-start gap-3 rounded-lg border border-primary/25 bg-primary/5 p-3">
              <StepNum n={2} />
              <MessagesSquare className="mt-0.5 h-4 w-4 shrink-0 text-primary" />
              <Stat
                value={threadCount}
                label="Threads"
                exact
                hint={
                  ticketCount > 0
                    ? threadCount > ticketCount
                      ? `${formatCount(ticketCount)} tickets in Evidence · ${formatCount(threadCount)} Threads (can exceed tickets after re-sync)`
                      : `${ticketsWithThreadPct}% of tickets have a Thread · KB articles are not included`
                    : "Mail or chat conversation on a ticket — not on KB articles"
                }
              />
            </div>
            <ArrowRight className="hidden h-4 w-4 shrink-0 self-center text-muted-foreground lg:block" />
            <div className="flex min-w-0 flex-1 items-start gap-3 rounded-lg border bg-muted/30 p-3">
              <StepNum n={3} />
              <MessageSquare className="mt-0.5 h-4 w-4 shrink-0 text-primary" />
              <Stat
                value={threadMessages}
                label="Messages"
                exact
                hint={
                  avgMessages
                    ? `${avgMessages} Messages on a typical Thread — not counted as Evidence`
                    : "Replies stored on those Threads, not as Evidence"
                }
              />
            </div>
          </div>

          <div className="grid gap-3 text-sm sm:grid-cols-2 lg:grid-cols-4">
            <div className="flex items-center justify-between gap-2 rounded-md border bg-muted/20 px-3 py-2">
              <span className="flex items-center gap-1.5 text-xs text-muted-foreground">
                <FileText className="h-3.5 w-3.5" />
                Tickets
              </span>
              <span className="tabular-nums font-medium">
                {typeof counts.tickets === "number" ? formatCount(ticketCount) : "—"}
              </span>
            </div>
            <div className="flex items-center justify-between gap-2 rounded-md border bg-muted/20 px-3 py-2">
              <span className="flex items-center gap-1.5 text-xs text-muted-foreground">
                <BookOpen className="h-3.5 w-3.5" />
                KB articles
              </span>
              <span className="tabular-nums font-medium">
                {typeof counts.kb_articles === "number" ? formatCount(kbCount) : "—"}
              </span>
            </div>
            {otherEvidence > 0 ? (
              <div className="flex items-center justify-between gap-2 rounded-md border bg-muted/20 px-3 py-2">
                <span className="text-xs text-muted-foreground">Other evidence</span>
                <span className="tabular-nums font-medium">{formatCount(otherEvidence)}</span>
              </div>
            ) : null}
            <div className="flex items-center justify-between gap-2 rounded-md border bg-muted/20 px-3 py-2">
              <span className="text-xs text-muted-foreground">Evidence total</span>
              <span className="tabular-nums font-medium">{formatCount(evidenceCount)}</span>
            </div>
          </div>

          <div className="space-y-2">
            <div className="flex flex-wrap items-center justify-between gap-2 text-sm">
              <span className="flex items-center gap-1.5 text-muted-foreground">
                {threadsPending === 0 ? (
                  <CheckCircle2 className="h-3.5 w-3.5 text-emerald-600" />
                ) : (
                  <Loader className="h-3.5 w-3.5 text-sky-600" />
                )}
                Threads fully fetched
              </span>
              <span className="tabular-nums font-medium">
                {formatCount(threadsHydrated)} of {formatCount(threadCount)}
                {threadCount > 0 ? ` (${Math.round(hydrationPct)}%)` : ""}
                {threadsPending > 0 ? ` · ${formatCount(threadsPending)} still loading` : ""}
              </span>
            </div>
            <div className="h-2 overflow-hidden rounded-full bg-muted">
              <div
                className="h-full rounded-full bg-primary"
                style={{ width: `${hydrationPct}%` }}
              />
            </div>
            {(threadsPending > 0 || hydrationQueue > 0) && (
              <p className="text-[11px] leading-4 text-muted-foreground">
                Fetching threads is the hydration waiting line
                {hydrationQueue > 0
                  ? ` — ${formatCount(hydrationQueue)} jobs in that line now.`
                  : "."}{" "}
                Each finished job fills one Thread with its Messages.
              </p>
            )}
          </div>

          <div className="grid gap-3 text-sm sm:grid-cols-3">
            <div className="flex items-center justify-between gap-2 rounded-md border px-3 py-2">
              <span className="text-xs text-muted-foreground">New Evidence / 10 min</span>
              <span className="tabular-nums font-medium">{formatCount(data.throughput_per_10min)}</span>
            </div>
            <div className="flex items-center justify-between gap-2 rounded-md border px-3 py-2">
              <span className="text-xs text-muted-foreground">New Threads / 10 min</span>
              <span className="tabular-nums font-medium">{formatCount(threads10min)}</span>
            </div>
            <div className="flex items-center justify-between gap-2 rounded-md border px-3 py-2">
              <span className="text-xs text-muted-foreground">New Messages / 10 min</span>
              <span className="tabular-nums font-medium">{formatCount(threadMessages10min)}</span>
            </div>
          </div>
        </CardContent>
      </Card>

      <Card>
        <CardHeader className="border-b pb-3">
          <CardTitle className="flex items-center gap-2">
            <Layers className="h-4 w-4 text-primary" />
            Step 2 — What Evidence becomes
          </CardTitle>
          <CardDescription className="text-xs">
            After Threads and Messages are fetched from Evidence, related items are
            linked, then incidents, patterns, and playbooks are built. The first empty
            box is where work has stopped.
          </CardDescription>
        </CardHeader>
        <CardContent className="pt-4">
          <div className="flex flex-wrap items-stretch gap-1">
            {[
              ...data.graph_chain.slice(0, 1),
              { stage: "threads", count: threadCount },
              { stage: "messages", count: threadMessages },
              ...data.graph_chain.slice(1),
            ].map((stage, i, chain) => (
              <div key={stage.stage} className="flex items-center gap-1">
                <div
                  className={cn(
                    "min-w-[9rem] rounded-lg border px-3 py-2.5",
                    stage.count === 0
                      ? "border-amber-500/60 bg-amber-500/10"
                      : stage.stage === "threads" || stage.stage === "messages"
                        ? "border-primary/25 bg-primary/5"
                        : "border-border bg-muted/40",
                  )}
                >
                  <div className="text-xl font-semibold tabular-nums">
                    {formatCount(stage.count)}
                  </div>
                  <div className="text-xs font-medium text-foreground">
                    {STAGE_LABELS[stage.stage]?.title ?? stage.stage}
                  </div>
                  <div className="text-[11px] leading-4 text-muted-foreground">
                    {STAGE_LABELS[stage.stage]?.hint ?? ""}
                  </div>
                </div>
                {i < chain.length - 1 && (
                  <ArrowRight className="h-4 w-4 shrink-0 text-muted-foreground" />
                )}
              </div>
            ))}
          </div>
          {data.stalled_at && (
            <p className="mt-3 text-sm text-muted-foreground">
              Nothing after{" "}
              <span className="font-medium text-foreground">
                {STAGE_LABELS[data.stalled_at]?.title ?? data.stalled_at}
              </span>{" "}
              can be built until that step produces work.
            </p>
          )}
        </CardContent>
      </Card>

      <div className="grid items-stretch gap-4 md:grid-cols-2">
        <Card className="h-full">
          <CardHeader className="border-b pb-3">
            <CardTitle className="flex items-center gap-2">
              <Database className="h-4 w-4 text-primary" />
              Waiting lines
            </CardTitle>
            <CardDescription className="text-xs">
              Jobs in the same line wait their turn. “Fetching threads” fills
              Threads and Messages above.
            </CardDescription>
          </CardHeader>
          <CardContent className="pt-4">
            {Object.keys(queues).length === 0 ? (
              <p className="text-sm text-muted-foreground">
                Could not read waiting lines. The job broker may be down.
              </p>
            ) : (
              <div className="space-y-2.5">
                {Object.entries(queues).map(([name, depth]) => (
                  <div
                    key={name}
                    className="flex items-center justify-between gap-3 text-sm"
                  >
                    <span className="w-44 shrink-0 text-xs text-muted-foreground">
                      {QUEUE_LABELS[name] ?? friendlyName({}, name)}
                      {name === "hydration" && threadsPending > 0 ? (
                        <span className="mt-0.5 block text-[10px] leading-3">
                          {formatCount(threadsPending)} threads still loading
                        </span>
                      ) : null}
                    </span>
                    <div className="flex flex-1 items-center gap-2">
                      <div className="h-1.5 flex-1 overflow-hidden rounded-full bg-muted">
                        <div
                          className={cn(
                            "h-full rounded-full",
                            depth > 500 ? "bg-amber-500" : "bg-primary",
                          )}
                          style={{
                            width: `${Math.min(100, (depth / 1000) * 100)}%`,
                          }}
                        />
                      </div>
                      <span className="w-16 text-right font-medium tabular-nums">
                        {formatNumber(depth)}
                      </span>
                    </div>
                  </div>
                ))}
              </div>
            )}
            <div className="mt-3 flex items-center justify-between gap-3 border-t pt-3 text-sm">
              <span className="text-xs text-muted-foreground">
                Being processed now
              </span>
              <span
                className={cn(
                  "font-medium tabular-nums",
                  data.in_flight > 50 && "text-sky-500",
                )}
              >
                {formatNumber(data.in_flight)}
              </span>
            </div>
            <p className="mt-3 text-xs text-muted-foreground">
              An empty line with work still being processed means workers are
              finishing held jobs, not sitting idle.
            </p>
          </CardContent>
        </Card>

        <Card className="h-full">
          <CardHeader className="border-b pb-3">
            <CardTitle className="flex items-center gap-2">
              <Gauge className="h-4 w-4 text-primary" />
              Speed (last 10 minutes)
            </CardTitle>
            <CardDescription className="text-xs">
              How much landed recently, and how long AI calls took.
            </CardDescription>
          </CardHeader>
          <CardContent className="space-y-4 pt-4">
            <div className="grid grid-cols-2 gap-x-6 gap-y-4 sm:grid-cols-4">
              <Stat value={data.throughput_per_10min} label="Evidence" hint="Tickets & articles" exact />
              <Stat value={threads10min} label="Threads" hint="Conversations started" exact />
              <Stat value={threadMessages10min} label="Thread messages" hint="Replies fetched" exact />
              <Stat value={data.episodes_per_10min} label="Incidents" hint="Timelines built" exact />
            </div>
            <div className="grid grid-cols-2 gap-3 border-t pt-3 sm:grid-cols-4">
              {[
                { label: "AI calls", value: String(latency.calls) },
                { label: "Typical AI time", value: formatMs(latency.p50_ms) },
                { label: "Slow cases", value: formatMs(latency.p95_ms) },
                { label: "Longest", value: formatMs(latency.max_ms) },
              ].map((m) => (
                <div key={m.label}>
                  <div className="text-lg font-semibold tabular-nums">{m.value}</div>
                  <div className="text-xs text-muted-foreground">{m.label}</div>
                </div>
              ))}
            </div>
          </CardContent>
        </Card>
      </div>

      <div className="grid items-stretch gap-4 md:grid-cols-2">
        <Card className="h-full">
          <CardHeader className="border-b pb-3">
            <CardTitle className="flex items-center gap-2">
              <Activity className="h-4 w-4 text-primary" />
              AI work (last hour)
            </CardTitle>
            <CardDescription className="text-xs">
              Which steps the model ran, how often, and how long they typically took.
            </CardDescription>
          </CardHeader>
          <CardContent className="pt-4">
            {data.by_call_60min.length === 0 ? (
              <p className="text-sm text-muted-foreground">
                No AI calls in the last hour.
              </p>
            ) : (
              <div className="overflow-x-auto">
                <table className="w-full text-sm">
                  <thead>
                    <tr className="border-b text-left text-xs text-muted-foreground">
                      <th className="pb-2 font-medium">Step</th>
                      <th className="pb-2 text-right font-medium">Calls</th>
                      <th className="pb-2 text-right font-medium">Typical time</th>
                      <th className="pb-2 text-right font-medium">Tokens</th>
                    </tr>
                  </thead>
                  <tbody>
                    {data.by_call_60min.map((row) => (
                      <tr key={row.call} className="border-b last:border-0">
                        <td className="py-2 text-xs">
                          {CALL_LABELS[row.call] ?? friendlyName({}, row.call)}
                        </td>
                        <td className="py-2 text-right tabular-nums">
                          {row.calls}
                        </td>
                        <td className="py-2 text-right tabular-nums">
                          {formatMs(row.p50_ms)}
                        </td>
                        <td className="py-2 text-right tabular-nums">
                          {formatNumber(row.tokens)}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </CardContent>
        </Card>

        <Card className="h-full">
          <CardHeader className="border-b pb-3">
            <CardTitle className="text-base">Also stored</CardTitle>
            <CardDescription className="text-xs">
              Search index, people and systems, and incident review. Thread replies
              are not mixed into evidence.
            </CardDescription>
          </CardHeader>
          <CardContent className="space-y-3.5 pt-4 text-sm">
            <div className="flex items-center justify-between gap-3">
              <span className="text-xs text-muted-foreground">Tickets</span>
              <span className="tabular-nums font-medium">
                {typeof counts.tickets === "number" ? formatCount(ticketCount) : "—"}
              </span>
            </div>
            <div className="flex items-center justify-between gap-3">
              <span className="text-xs text-muted-foreground">KB articles</span>
              <span className="tabular-nums font-medium">
                {typeof counts.kb_articles === "number" ? formatCount(kbCount) : "—"}
              </span>
            </div>
            <div className="flex items-center justify-between gap-3 border-b pb-3">
              <span className="text-xs text-muted-foreground">Evidence total</span>
              <span className="tabular-nums font-medium">{formatCount(evidenceCount)}</span>
            </div>
            <div className="flex items-center justify-between gap-3">
              <span className="text-xs text-muted-foreground">Threads</span>
              <span className="tabular-nums font-medium">{formatCount(threadCount)}</span>
            </div>
            <div className="flex items-center justify-between gap-3 border-b pb-3">
              <span className="text-xs text-muted-foreground">Messages</span>
              <span className="tabular-nums font-medium">{formatCount(threadMessages)}</span>
            </div>
            <div className="flex items-center justify-between gap-3">
              <span className="text-xs text-muted-foreground">Raw objects fetched</span>
              <span className="tabular-nums font-medium">{formatNumber(counts.raw_objects)}</span>
            </div>
            <div className="flex items-center justify-between gap-3">
              <span className="text-xs text-muted-foreground">People & systems</span>
              <span className="tabular-nums font-medium">{formatNumber(counts.identities)}</span>
            </div>
            <div className="flex items-center justify-between gap-3">
              <span className="text-xs text-muted-foreground">Searchable (indexed)</span>
              <span className="tabular-nums font-medium">{formatNumber(counts.embedded)}</span>
            </div>
            <div className="flex items-center justify-between gap-3">
              <span className="text-xs text-muted-foreground">Text chunks indexed</span>
              <span className="tabular-nums font-medium">
                {formatNumber(counts.chunks_embedded)} / {formatNumber(counts.chunks_total)}
              </span>
            </div>
            <div className="flex items-center justify-between gap-3">
              <span className="text-xs text-muted-foreground">Incidents awaiting review / approved</span>
              <span className="tabular-nums font-medium">
                {formatNumber(counts.episodes_pending)} / {formatNumber(counts.episodes_approved)}
              </span>
            </div>
            {embedGap > 0 && (
              <Badge variant="outline" className="mt-1">
                {formatNumber(embedGap)} relevant items still waiting to be searchable
              </Badge>
            )}
          </CardContent>
        </Card>
      </div>
    </div>
  );
}
