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
  Database,
  Gauge,
  Info,
  Layers,
  Timer,
  XCircle,
} from "lucide-react";

import { PageHeader } from "@/components/common/page-header";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { api } from "@/lib/api";
import { cn } from "@/lib/utils";

interface PipelineHealth {
  counts: {
    evidence: number;
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
  if (n >= 1_000) return `${(n / 1_000).toFixed(1)}k`;
  return n.toString();
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
          description="Queue depth, throughput and how far the graph has been built."
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

  return (
    <div className="space-y-6">
      <PageHeader
        title="Pipeline health"
        description="Queue depth, throughput and how far the graph has actually been built."
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

      {/* The run projection: what's left, how fast it's draining, when it
          lands, what it will cost. Rate is measured in THIS tab from poll
          deltas, so it reflects the pipeline as currently staffed. */}
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2 text-base">
            <Timer className="h-4 w-4" />
            Run projection
          </CardTitle>
        </CardHeader>
        <CardContent>
          {remaining !== undefined && remaining <= 10 ? (
            <p className="text-sm text-muted-foreground">
              No meaningful backlog — the pipeline is idle or serving steady-state
              trickle. Burn last hour: ${data.spend_last_hour_usd.toFixed(2)}.
            </p>
          ) : (
            <div className="grid grid-cols-2 gap-4 md:grid-cols-4">
              <div>
                <div className="text-2xl font-semibold tabular-nums">
                  {formatNumber(remaining ?? 0)}
                </div>
                <div className="text-xs text-muted-foreground">
                  tasks remaining (queued + in flight)
                </div>
              </div>
              <div>
                <div className="text-2xl font-semibold tabular-nums">
                  {!drain.warmedUp
                    ? "…"
                    : drain.perMinute !== null && drain.perMinute > 0
                      ? drain.perMinute.toFixed(1)
                      : "0"}
                </div>
                <div className="text-xs text-muted-foreground">
                  drain rate / min{" "}
                  {!drain.warmedUp && "(measuring — needs a minute of samples)"}
                  {drain.warmedUp &&
                    drain.perMinute !== null &&
                    drain.perMinute <= 0 &&
                    "(backlog holding or growing)"}
                </div>
              </div>
              <div>
                <div className="text-2xl font-semibold tabular-nums">
                  {drain.warmedUp && drain.perMinute !== null && drain.perMinute > 0
                    ? formatDuration(((remaining ?? 0) / drain.perMinute) * 60)
                    : "—"}
                </div>
                <div className="text-xs text-muted-foreground">
                  estimated time to complete
                </div>
              </div>
              <div>
                <div className="text-2xl font-semibold tabular-nums">
                  {drain.warmedUp && drain.perMinute !== null && drain.perMinute > 0
                    ? `$${(
                        data.spend_last_hour_usd *
                        ((remaining ?? 0) / drain.perMinute / 60)
                      ).toFixed(2)}`
                    : `$${data.spend_last_hour_usd.toFixed(2)}/hr`}
                </div>
                <div className="text-xs text-muted-foreground">
                  {drain.warmedUp && drain.perMinute !== null && drain.perMinute > 0
                    ? `projected cost to finish (at $${data.spend_last_hour_usd.toFixed(2)}/hr)`
                    : "burn rate, last hour"}
                </div>
              </div>
            </div>
          )}
        </CardContent>
      </Card>

      {/* The graph chain. Each stage feeds the next, so it is drawn as a flow
          rather than five cards — the point is which link is empty. */}
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2 text-base">
            <Layers className="h-4 w-4" />
            Graph chain
          </CardTitle>
        </CardHeader>
        <CardContent>
          <div className="flex flex-wrap items-center gap-1">
            {data.graph_chain.map((stage, i) => (
              <div key={stage.stage} className="flex items-center gap-1">
                <div
                  className={cn(
                    "min-w-[7.5rem] rounded-lg border px-3 py-2",
                    stage.count === 0
                      ? "border-amber-500/60 bg-amber-500/10"
                      : "border-border bg-muted/40",
                  )}
                >
                  <div className="text-xl font-semibold tabular-nums">
                    {formatNumber(stage.count)}
                  </div>
                  <div className="text-xs capitalize text-muted-foreground">
                    {stage.stage}
                  </div>
                </div>
                {i < data.graph_chain.length - 1 && (
                  <ArrowRight className="h-4 w-4 shrink-0 text-muted-foreground" />
                )}
              </div>
            ))}
          </div>
          {data.stalled_at && (
            <p className="mt-3 text-sm text-muted-foreground">
              Nothing downstream of{" "}
              <span className="font-medium text-foreground">{data.stalled_at}</span>{" "}
              can be produced until that stage does.
            </p>
          )}
        </CardContent>
      </Card>

      <div className="grid gap-4 md:grid-cols-2">
        {/* Queues — the view that was missing when the pipeline stalled. */}
        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2 text-base">
              <Database className="h-4 w-4" />
              Queue depth
            </CardTitle>
          </CardHeader>
          <CardContent>
            {Object.keys(queues).length === 0 ? (
              <p className="text-sm text-muted-foreground">
                Broker unreachable — queue depths unavailable.
              </p>
            ) : (
              <div className="space-y-2">
                {Object.entries(queues).map(([name, depth]) => (
                  <div
                    key={name}
                    className="flex items-center justify-between gap-3 text-sm"
                  >
                    <span className="font-mono text-xs text-muted-foreground">
                      {name}
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
            {/* In-flight is work DELIVERED to workers but unfinished —
                including every debounced reconstruction held to its ETA.
                During the reconstruction phase this is where ALL remaining
                work lives, and the queues above legitimately read zero. */}
            <div className="mt-3 flex items-center justify-between gap-3 border-t pt-3 text-sm">
              <span className="font-mono text-xs text-muted-foreground">
                in flight (held by workers)
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
              Tasks sharing a lane run in order, so a fast task queued behind a
              long backlog waits for all of it. Empty queues with work in
              flight means workers are holding debounced tasks, not idling.
            </p>
          </CardContent>
        </Card>

        {/* Throughput + latency, side by side: slow and starved look identical
            unless you can see both at once. */}
        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2 text-base">
              <Gauge className="h-4 w-4" />
              Throughput and latency
            </CardTitle>
          </CardHeader>
          <CardContent className="space-y-4">
            <div className="grid grid-cols-3 gap-3">
              <div>
                <div className="text-2xl font-semibold tabular-nums">
                  {data.throughput_per_10min}
                </div>
                <div className="text-xs text-muted-foreground">
                  evidence / 10 min
                </div>
              </div>
              <div>
                <div className="text-2xl font-semibold tabular-nums">
                  {data.episodes_per_10min}
                </div>
                <div className="text-xs text-muted-foreground">
                  episodes / 10 min
                </div>
              </div>
              <div>
                <div className="text-2xl font-semibold tabular-nums">
                  {latency.calls}
                </div>
                <div className="text-xs text-muted-foreground">
                  model calls / 10 min
                </div>
              </div>
            </div>
            <div className="grid grid-cols-3 gap-3 border-t pt-3">
              {[
                { label: "p50", value: latency.p50_ms },
                { label: "p95", value: latency.p95_ms },
                { label: "max", value: latency.max_ms },
              ].map((m) => (
                <div key={m.label}>
                  <div className="flex items-center gap-1 text-lg font-semibold tabular-nums">
                    <Timer className="h-3.5 w-3.5 text-muted-foreground" />
                    {formatMs(m.value)}
                  </div>
                  <div className="text-xs uppercase tracking-wide text-muted-foreground">
                    {m.label} latency
                  </div>
                </div>
              ))}
            </div>
          </CardContent>
        </Card>
      </div>

      <div className="grid gap-4 md:grid-cols-2">
        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2 text-base">
              <Activity className="h-4 w-4" />
              Latency by call (last hour)
            </CardTitle>
          </CardHeader>
          <CardContent>
            {data.by_call_60min.length === 0 ? (
              <p className="text-sm text-muted-foreground">
                No model calls in the last hour.
              </p>
            ) : (
              <div className="overflow-x-auto">
                <table className="w-full text-sm">
                  <thead>
                    <tr className="border-b text-left text-xs uppercase tracking-wide text-muted-foreground">
                      <th className="pb-2 font-medium">Call</th>
                      <th className="pb-2 text-right font-medium">Calls</th>
                      <th className="pb-2 text-right font-medium">p50</th>
                      <th className="pb-2 text-right font-medium">Tokens</th>
                    </tr>
                  </thead>
                  <tbody>
                    {data.by_call_60min.map((row) => (
                      <tr key={row.call} className="border-b last:border-0">
                        <td className="py-2 font-mono text-xs">{row.call}</td>
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

        <Card>
          <CardHeader>
            <CardTitle className="text-base">Corpus</CardTitle>
          </CardHeader>
          <CardContent>
            <div className="grid grid-cols-2 gap-4">
              {[
                { label: "Raw objects", value: counts.raw_objects },
                { label: "Evidence", value: counts.evidence },
                { label: "Embedded", value: counts.embedded },
                { label: "Active identities", value: counts.identities },
              ].map((m) => (
                <div key={m.label}>
                  <div className="text-xl font-semibold tabular-nums">
                    {formatNumber(m.value)}
                  </div>
                  <div className="text-xs text-muted-foreground">{m.label}</div>
                </div>
              ))}
            </div>
            <div className="mt-4 space-y-2 border-t pt-3 text-sm">
              <div className="flex items-center justify-between">
                <span className="text-xs text-muted-foreground">
                  chunks embedded
                </span>
                <span className="tabular-nums">
                  {formatNumber(counts.chunks_embedded)} /{" "}
                  {formatNumber(counts.chunks_total)}
                </span>
              </div>
              <div className="flex items-center justify-between">
                <span className="text-xs text-muted-foreground">
                  episodes pending review / approved
                </span>
                <span className="tabular-nums">
                  {formatNumber(counts.episodes_pending)} /{" "}
                  {formatNumber(counts.episodes_approved)}
                </span>
              </div>
            </div>
            {embedGap > 0 && (
              <Badge variant="outline" className="mt-4">
                {formatNumber(embedGap)} relevant items awaiting embedding
              </Badge>
            )}
          </CardContent>
        </Card>
      </div>
    </div>
  );
}
