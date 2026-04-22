"use client";

/**
 * Admin LLM cost dashboard.
 *
 * Shows per-tenant spend, cache-hit rate, and top model×task breakdown.
 * Gated to tenant_admin / platform_super_admin; non-admins are blocked
 * by the backend (403) and see a friendly empty-state message.
 *
 * The dashboard refetches every 60 seconds so admins watching a cost
 * spike can see it resolve in near-real-time after a deploy / config
 * change (e.g. switching on prompt caching or flipping the normalize
 * order should show cache_hit_rate climb and request_count per model
 * shift within a minute).
 */

import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import {
  AlertTriangle,
  DollarSign,
  Gauge,
  Layers,
  Percent,
  TrendingUp,
} from "lucide-react";

import { PageHeader } from "@/components/common/page-header";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { api } from "@/lib/api";
import { cn } from "@/lib/utils";
import type { LlmUsageResponse } from "@/lib/types";

// Windows the dashboard offers — not free-form so we can cache consistently.
const WINDOW_OPTIONS = [
  { value: "1", label: "Last hour" },
  { value: "24", label: "Last 24 hours" },
  { value: "168", label: "Last 7 days" },
  { value: "720", label: "Last 30 days" },
] as const;

function formatNumber(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(2)}M`;
  if (n >= 1_000) return `${(n / 1_000).toFixed(1)}k`;
  return n.toString();
}

function formatCurrency(n: number): string {
  if (n === 0) return "$0.00";
  if (n < 0.01) return `$${n.toFixed(4)}`;
  return `$${n.toFixed(2)}`;
}

function cacheRateTone(rate: number): { level: "red" | "amber" | "green"; className: string } {
  // Cache hit rate: < 20% is a problem (caching not configured or prompts
  // too dynamic to cache); 20-50% is fine; > 50% is the target after warm-up.
  if (rate >= 0.5) {
    return {
      level: "green",
      className: "bg-emerald-500/15 text-emerald-300 border-emerald-500/30",
    };
  }
  if (rate >= 0.2) {
    return {
      level: "amber",
      className: "bg-amber-500/15 text-amber-300 border-amber-500/30",
    };
  }
  return {
    level: "red",
    className: "bg-rose-500/15 text-rose-300 border-rose-500/30",
  };
}

// ---------------------------------------------------------------------------
// KPI card
// ---------------------------------------------------------------------------

function KpiCard({
  label,
  value,
  sub,
  icon: Icon,
  tone,
}: {
  label: string;
  value: string;
  sub?: string;
  icon: React.ElementType;
  tone?: string;
}) {
  return (
    <Card className="relative overflow-hidden">
      <CardHeader className="pb-2 flex flex-row items-center justify-between space-y-0">
        <CardTitle className="text-[11px] uppercase tracking-wider font-semibold text-muted-foreground">
          {label}
        </CardTitle>
        <Icon className="h-4 w-4 text-muted-foreground" />
      </CardHeader>
      <CardContent className="pt-0">
        <div className={cn("text-2xl font-semibold font-mono", tone)}>{value}</div>
        {sub && <div className="text-xs text-muted-foreground mt-1">{sub}</div>}
      </CardContent>
    </Card>
  );
}

// ---------------------------------------------------------------------------
// Breakdown bar — CSS-only horizontal stacked bar (prompt / cached / completion)
// ---------------------------------------------------------------------------

function BreakdownBar({
  prompt,
  cached,
  completion,
}: {
  prompt: number;
  cached: number;
  completion: number;
}) {
  const total = prompt + completion;
  if (total === 0) return null;
  const nonCachedPrompt = Math.max(prompt - cached, 0);
  const pctCached = (cached / total) * 100;
  const pctPrompt = (nonCachedPrompt / total) * 100;
  const pctCompletion = (completion / total) * 100;
  return (
    <div
      className="flex h-2 w-full rounded-sm overflow-hidden bg-muted"
      title={`Prompt (non-cached): ${formatNumber(
        nonCachedPrompt,
      )} • Cached: ${formatNumber(cached)} • Completion: ${formatNumber(completion)}`}
    >
      <div className="bg-sky-500/70" style={{ width: `${pctPrompt}%` }} />
      <div className="bg-emerald-500/70" style={{ width: `${pctCached}%` }} />
      <div className="bg-violet-500/70" style={{ width: `${pctCompletion}%` }} />
    </div>
  );
}

// ---------------------------------------------------------------------------
// Main page
// ---------------------------------------------------------------------------

export default function AdminCostPage() {
  const [windowHours, setWindowHours] = useState<string>("24");

  const { data, isLoading, error, refetch, isFetching } = useQuery<LlmUsageResponse>({
    queryKey: ["admin-llm-usage", windowHours],
    queryFn: () =>
      api.get<LlmUsageResponse>("/admin/llm-usage", {
        window_hours: windowHours,
        top_n_breakdown: "15",
      }),
    refetchInterval: 60_000,
  });

  const totals = data?.totals;
  const cacheRate = totals?.cache_hit_rate ?? 0;
  const cacheTone = cacheRateTone(cacheRate);

  return (
    <div className="space-y-6">
      <PageHeader
        title="LLM Cost & Usage"
        description="Per-tenant spend, cache-hit rate, and model/task breakdown over the selected window. Refreshes every 60 seconds."
      />

      <div className="flex flex-wrap items-end gap-3">
        <div className="space-y-1.5">
          <label className="text-xs font-medium text-muted-foreground">
            Window
          </label>
          <Select
            value={windowHours}
            onValueChange={(v) => {
              if (v) setWindowHours(v);
            }}
          >
            <SelectTrigger className="w-[180px]">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {WINDOW_OPTIONS.map((o) => (
                <SelectItem key={o.value} value={o.value}>
                  {o.label}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
        <Button
          variant="outline"
          size="sm"
          disabled={isFetching}
          onClick={() => refetch()}
        >
          {isFetching ? "Refreshing…" : "Refresh now"}
        </Button>
      </div>

      {error && (
        <div className="rounded-md border border-destructive/40 bg-destructive/10 p-4 text-sm text-destructive flex items-start gap-2">
          <AlertTriangle className="h-4 w-4 mt-0.5 shrink-0" />
          <div>
            <div className="font-medium">Unable to load LLM usage</div>
            <div className="text-xs mt-1 opacity-80">{error.message}</div>
            <div className="text-xs mt-2 opacity-70">
              This endpoint requires <code>tenant_admin</code> or{" "}
              <code>platform_super_admin</code>.
            </div>
          </div>
        </div>
      )}

      {!error && (
        <>
          <section className="grid gap-3 grid-cols-2 lg:grid-cols-4">
            <KpiCard
              label="Estimated cost"
              value={formatCurrency(totals?.estimated_cost_usd ?? 0)}
              sub={
                totals
                  ? `${totals.request_count.toLocaleString()} request${totals.request_count === 1 ? "" : "s"}`
                  : "Loading…"
              }
              icon={DollarSign}
            />
            <KpiCard
              label="Total tokens"
              value={formatNumber(totals?.total_tokens ?? 0)}
              sub={
                totals
                  ? `${formatNumber(totals.prompt_tokens)} prompt + ${formatNumber(totals.completion_tokens)} completion`
                  : "—"
              }
              icon={Layers}
            />
            <KpiCard
              label="Cache hit rate"
              value={
                totals && totals.prompt_tokens > 0
                  ? `${(cacheRate * 100).toFixed(1)}%`
                  : "—"
              }
              sub={
                totals && totals.prompt_tokens > 0
                  ? `${formatNumber(totals.cached_tokens)} of ${formatNumber(totals.prompt_tokens)} prompt tokens cached`
                  : "No prompt tokens in window"
              }
              icon={Percent}
              tone={
                totals && totals.prompt_tokens > 0
                  ? cacheTone.level === "green"
                    ? "text-emerald-300"
                    : cacheTone.level === "amber"
                      ? "text-amber-300"
                      : "text-rose-300"
                  : undefined
              }
            />
            <KpiCard
              label="Avg cost / request"
              value={
                totals && totals.request_count > 0
                  ? formatCurrency(
                      totals.estimated_cost_usd / totals.request_count,
                    )
                  : "—"
              }
              sub={
                totals && totals.request_count > 0
                  ? `avg ${Math.round(totals.total_tokens / totals.request_count)} tokens / request`
                  : "—"
              }
              icon={Gauge}
            />
          </section>

          {/* Cache-hit badge call-out — single line of ground truth */}
          {totals && totals.prompt_tokens > 0 && (
            <div className="flex items-center gap-2 text-xs">
              <Badge variant="outline" className={cn("font-mono", cacheTone.className)}>
                {cacheTone.level === "green"
                  ? "Caching healthy"
                  : cacheTone.level === "amber"
                    ? "Caching partial"
                    : "Caching not effective"}
              </Badge>
              <span className="text-muted-foreground">
                {cacheTone.level === "green"
                  ? "Prompt caching is cutting costs materially. Target ≥ 50%."
                  : cacheTone.level === "amber"
                    ? "Caching active but sub-optimal — check that system prompts are stable across calls."
                    : "Expected after a fresh deploy; should climb above 50% within the first hour. If it stays low, system prompts may be getting re-generated per call."}
              </span>
            </div>
          )}

          {/* Breakdown */}
          <section>
            <div className="flex items-end justify-between mb-3">
              <h2 className="text-sm font-semibold">
                <TrendingUp className="inline h-4 w-4 mr-1.5 -mt-0.5" />
                Top model × task spend
              </h2>
              <div className="text-[11px] text-muted-foreground flex items-center gap-3">
                <span className="flex items-center gap-1">
                  <span className="inline-block h-2 w-2 rounded-sm bg-sky-500/70" />
                  prompt (non-cached)
                </span>
                <span className="flex items-center gap-1">
                  <span className="inline-block h-2 w-2 rounded-sm bg-emerald-500/70" />
                  cached
                </span>
                <span className="flex items-center gap-1">
                  <span className="inline-block h-2 w-2 rounded-sm bg-violet-500/70" />
                  completion
                </span>
              </div>
            </div>

            {isLoading && !data ? (
              <div className="text-sm text-muted-foreground py-8 text-center border rounded-md">
                Loading usage data…
              </div>
            ) : !data || data.by_model_task.length === 0 ? (
              <div className="border rounded-md p-8 text-center text-sm text-muted-foreground">
                <div className="font-medium mb-1">No LLM calls in this window</div>
                <div className="text-xs">
                  LLM usage metrics appear here after the first inference
                  call — try ingesting an evidence item or opening a
                  reviewer session, then refresh.
                </div>
              </div>
            ) : (
              <Card>
                <CardContent className="p-0">
                  <table className="w-full text-sm">
                    <thead className="bg-muted/40 text-[11px] uppercase tracking-wider text-muted-foreground">
                      <tr>
                        <th className="text-left px-3 py-2 font-medium">Model</th>
                        <th className="text-left px-3 py-2 font-medium">Task</th>
                        <th className="text-right px-3 py-2 font-medium">Requests</th>
                        <th className="text-right px-3 py-2 font-medium">Tokens</th>
                        <th className="px-3 py-2 font-medium w-[35%]">Breakdown</th>
                        <th className="text-right px-3 py-2 font-medium">Cost</th>
                      </tr>
                    </thead>
                    <tbody>
                      {data.by_model_task.map((row) => (
                        <tr
                          key={`${row.model}:${row.task}`}
                          className="border-t hover:bg-muted/30 transition-colors"
                        >
                          <td className="px-3 py-2 font-mono text-xs">{row.model}</td>
                          <td className="px-3 py-2">
                            <Badge variant="secondary" className="text-[10px]">
                              {row.task}
                            </Badge>
                          </td>
                          <td className="px-3 py-2 text-right font-mono text-xs">
                            {row.request_count.toLocaleString()}
                          </td>
                          <td className="px-3 py-2 text-right font-mono text-xs">
                            {formatNumber(row.total_tokens)}
                          </td>
                          <td className="px-3 py-2">
                            <BreakdownBar
                              prompt={row.prompt_tokens}
                              cached={row.cached_tokens}
                              completion={row.completion_tokens}
                            />
                          </td>
                          <td className="px-3 py-2 text-right font-mono font-semibold">
                            {formatCurrency(row.estimated_cost_usd)}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </CardContent>
              </Card>
            )}
          </section>

          <div className="text-[11px] text-muted-foreground">
            Cost estimates derived from published per-million-token rates; use
            the LLM provider&apos;s billing dashboard for the authoritative
            invoice. Cache-hit rate reflects prompt tokens served from
            provider-side prompt caches (OpenAI automatic prefix cache or
            Anthropic ephemeral blocks).
          </div>
        </>
      )}
    </div>
  );
}
