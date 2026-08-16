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

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import {
  AlertTriangle,
  Brain,
  DollarSign,
  Gauge,
  Layers,
  Percent,
  ShieldCheck,
  TrendingUp,
} from "lucide-react";
import { toast } from "sonner";

import { PageHeader } from "@/components/common/page-header";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { api } from "@/lib/api";
import { cn } from "@/lib/utils";
import type {
  LlmUsageResponse,
  TenantBudget,
  TenantBudgetStatus,
  TenantBudgetUpsert,
  SyncRun,
} from "@/lib/types";

// Windows the dashboard offers — not free-form so we can cache consistently.
const WINDOW_OPTIONS = [
  { value: "1", label: "Last hour" },
  { value: "24", label: "Today (24h)" },
  { value: "168", label: "This week (7d)" },
  { value: "720", label: "This month (30d)" },
  // The API caps the rolling window at 30 days; "overall" is a different
  // question and asks the backend for no lower bound at all.
  { value: "all", label: "Overall to date" },
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
  reasoning = 0,
}: {
  prompt: number;
  cached: number;
  completion: number;
  /** Subset of `completion`, drawn as its own segment carved out of it. */
  reasoning?: number;
}) {
  const total = prompt + completion;
  if (total === 0) return null;
  const nonCachedPrompt = Math.max(prompt - cached, 0);
  // Reasoning is already inside completion, so the answer segment is what is
  // left after carving it out. Adding them would overstate the bar.
  const answer = Math.max(completion - reasoning, 0);
  const pctCached = (cached / total) * 100;
  const pctPrompt = (nonCachedPrompt / total) * 100;
  const pctAnswer = (answer / total) * 100;
  const pctReasoning = (reasoning / total) * 100;
  return (
    <div
      className="flex h-2 w-full rounded-sm overflow-hidden bg-muted"
      title={`Prompt (non-cached): ${formatNumber(
        nonCachedPrompt,
      )} • Cached: ${formatNumber(cached)} • Answer: ${formatNumber(
        answer,
      )} • Thinking: ${formatNumber(reasoning)}`}
    >
      <div className="bg-sky-500/70" style={{ width: `${pctPrompt}%` }} />
      <div className="bg-emerald-500/70" style={{ width: `${pctCached}%` }} />
      <div className="bg-violet-500/70" style={{ width: `${pctAnswer}%` }} />
      <div className="bg-fuchsia-500/70" style={{ width: `${pctReasoning}%` }} />
    </div>
  );
}

// ---------------------------------------------------------------------------
// Daily budget panel (GET/PUT /admin/tenant-budget + /status)
// ---------------------------------------------------------------------------

/**
 * Shows today's spend against the tenant's configured daily cap and
 * lets tenant_admin edit the cap inline. A null `budget` means "no
 * cap configured" — the tenant is uncapped.
 *
 * The "block" vs "warn" action is intentionally a dropdown rather
 * than a boolean: rolling out a new cap is safer when you can land
 * as `warn` first, watch the dashboard for a day, then flip to
 * `block` once the warnings are tuned out.
 */
function BudgetPanel() {
  const qc = useQueryClient();
  const [isEditing, setIsEditing] = useState(false);

  const { data, isLoading, error } = useQuery<TenantBudgetStatus>({
    queryKey: ["admin-tenant-budget-status"],
    queryFn: () => api.get<TenantBudgetStatus>("/admin/tenant-budget/status"),
    refetchInterval: 60_000,
  });

  const upsert = useMutation({
    mutationFn: (body: TenantBudgetUpsert) =>
      api.put<TenantBudget>("/admin/tenant-budget", body),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["admin-tenant-budget-status"] });
      qc.invalidateQueries({ queryKey: ["admin-llm-usage"] });
      setIsEditing(false);
      toast.success("Budget updated");
    },
    onError: (e: Error) => {
      toast.error(e.message || "Failed to update budget");
    },
  });

  if (error) {
    return (
      <Card>
        <CardHeader>
          <CardTitle className="text-sm">Daily budget</CardTitle>
        </CardHeader>
        <CardContent className="text-xs text-muted-foreground">
          Unable to load budget status: {error.message}
        </CardContent>
      </Card>
    );
  }

  if (isLoading || !data) {
    return (
      <Card>
        <CardHeader>
          <CardTitle className="text-sm">Daily budget</CardTitle>
        </CardHeader>
        <CardContent className="text-xs text-muted-foreground py-4">
          Loading budget…
        </CardContent>
      </Card>
    );
  }

  const {
    budget,
    current_tokens,
    current_cost_usd,
    allowed,
    reason,
    effective_token_limit,
    effective_cost_cap_usd,
    limit_source,
  } = data;

  if (isEditing) {
    return (
      <BudgetEditForm
        initial={budget}
        onCancel={() => setIsEditing(false)}
        onSubmit={(body) => upsert.mutate(body)}
        submitting={upsert.isPending}
      />
    );
  }

  // Ratios come from the *effective* caps, not the row: a tenant with no row
  // is still capped by the deployment defaults, and drawing it as uncapped
  // would hide a limit that can actually block their calls.
  const tokenRatio =
    effective_token_limit && effective_token_limit > 0
      ? Math.min(current_tokens / effective_token_limit, 1)
      : null;
  const costRatio =
    effective_cost_cap_usd && effective_cost_cap_usd > 0
      ? Math.min(current_cost_usd / effective_cost_cap_usd, 1)
      : null;
  const effectiveAction = budget?.action_on_exceed ?? "block";

  return (
    <Card>
      <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-3">
        <div className="flex items-center gap-2">
          <ShieldCheck className="h-4 w-4 text-muted-foreground" />
          <CardTitle className="text-sm">Daily budget</CardTitle>
          {limit_source !== "none" && (
            <Badge
              variant="outline"
              className={cn(
                "text-[10px] font-mono",
                effectiveAction === "block"
                  ? "bg-rose-500/15 text-rose-300 border-rose-500/30"
                  : "bg-amber-500/15 text-amber-300 border-amber-500/30",
              )}
            >
              on-exceed: {effectiveAction}
            </Badge>
          )}
          {limit_source === "default" && (
            <Badge
              variant="outline"
              className="text-[10px] font-mono bg-sky-500/15 text-sky-300 border-sky-500/30"
              title="No budget row for this tenant, so the deployment-wide default caps apply. Set a budget to override them."
            >
              deployment default
            </Badge>
          )}
          {limit_source === "none" && (
            <Badge variant="secondary" className="text-[10px]">
              uncapped
            </Badge>
          )}
          {!allowed && (
            <Badge
              variant="outline"
              className="text-[10px] font-mono bg-rose-500/15 text-rose-300 border-rose-500/30"
            >
              {reason}
            </Badge>
          )}
        </div>
        <Button variant="outline" size="sm" onClick={() => setIsEditing(true)}>
          {budget ? "Edit budget" : "Set budget"}
        </Button>
      </CardHeader>
      <CardContent className="space-y-4">
        <BudgetAxis
          label="Tokens"
          current={current_tokens}
          limit={effective_token_limit}
          ratio={tokenRatio}
          formatValue={formatNumber}
        />
        <BudgetAxis
          label="Cost"
          current={current_cost_usd}
          limit={effective_cost_cap_usd}
          ratio={costRatio}
          formatValue={formatCurrency}
        />
        <div className="text-[11px] text-muted-foreground pt-1">
          Usage aggregates today&apos;s <code>llm.usage</code> events (UTC day).
          {limit_source === "default" &&
            " These are the deployment default caps, applied because this tenant has no budget row. Saving a budget here replaces them."}
          {effectiveAction === "warn" &&
            " Warning mode: calls still go through, but each one emits an llm.budget_warning event."}
          {effectiveAction === "block" &&
            " Blocking mode: llm_complete raises TenantBudgetExceeded once the cap is reached — callers are expected to degrade gracefully."}
        </div>
      </CardContent>
    </Card>
  );
}

function BudgetAxis({
  label,
  current,
  limit,
  ratio,
  formatValue,
}: {
  label: string;
  current: number;
  limit: number | null;
  ratio: number | null;
  formatValue: (n: number) => string;
}) {
  const percent = ratio !== null ? Math.round(ratio * 100) : null;
  const barTone =
    ratio === null
      ? "bg-muted-foreground/20"
      : ratio >= 1
        ? "bg-rose-500/70"
        : ratio >= 0.8
          ? "bg-amber-500/70"
          : "bg-emerald-500/70";
  return (
    <div className="space-y-1.5">
      <div className="flex items-baseline justify-between text-xs">
        <span className="text-muted-foreground">{label}</span>
        <span className="font-mono">
          {formatValue(current)}
          {limit !== null && limit > 0 ? (
            <>
              {" / "}
              <span className="text-muted-foreground">{formatValue(limit)}</span>
              {percent !== null && (
                <span className="ml-2 text-muted-foreground">({percent}%)</span>
              )}
            </>
          ) : (
            <span className="ml-2 text-muted-foreground">(no cap)</span>
          )}
        </span>
      </div>
      <div className="h-1.5 w-full rounded-sm overflow-hidden bg-muted">
        <div
          className={cn("h-full transition-all", barTone)}
          style={{ width: `${ratio !== null ? Math.max(ratio * 100, 1) : 0}%` }}
        />
      </div>
    </div>
  );
}

function BudgetEditForm({
  initial,
  onCancel,
  onSubmit,
  submitting,
}: {
  initial: TenantBudget | null;
  onCancel: () => void;
  onSubmit: (body: TenantBudgetUpsert) => void;
  submitting: boolean;
}) {
  // Strings so the inputs can be blank-ish. Blank → null (uncapped).
  const [tokenLimit, setTokenLimit] = useState<string>(
    initial?.daily_token_limit?.toString() ?? "",
  );
  const [costCap, setCostCap] = useState<string>(
    initial?.daily_cost_cap_usd?.toString() ?? "",
  );
  const [action, setAction] = useState<"block" | "warn">(
    initial?.action_on_exceed ?? "warn",
  );
  const [localError, setLocalError] = useState<string | null>(null);

  function parseOrNull(raw: string): number | null | "invalid" {
    const trimmed = raw.trim();
    if (trimmed === "") return null;
    const n = Number(trimmed);
    if (!Number.isFinite(n) || n < 0) return "invalid";
    return n;
  }

  function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    const t = parseOrNull(tokenLimit);
    const c = parseOrNull(costCap);
    if (t === "invalid") {
      setLocalError("Token limit must be a non-negative number or blank.");
      return;
    }
    if (c === "invalid") {
      setLocalError("Cost cap must be a non-negative number or blank.");
      return;
    }
    if (t === null && c === null) {
      setLocalError("Set at least one of token limit or cost cap, or delete the budget row entirely.");
      return;
    }
    onSubmit({
      daily_token_limit: t,
      daily_cost_cap_usd: c,
      action_on_exceed: action,
    });
  }

  return (
    <Card>
      <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-3">
        <div className="flex items-center gap-2">
          <ShieldCheck className="h-4 w-4 text-muted-foreground" />
          <CardTitle className="text-sm">
            {initial ? "Edit daily budget" : "Set daily budget"}
          </CardTitle>
        </div>
      </CardHeader>
      <CardContent>
        <form onSubmit={handleSubmit} className="space-y-4">
          <div className="grid gap-4 sm:grid-cols-2">
            <div className="space-y-1.5">
              <Label htmlFor="token-limit" className="text-xs">
                Daily token limit
              </Label>
              <Input
                id="token-limit"
                type="number"
                inputMode="numeric"
                min={0}
                step={1000}
                placeholder="blank = no cap"
                value={tokenLimit}
                onChange={(e) => {
                  setTokenLimit(e.target.value);
                  setLocalError(null);
                }}
              />
              <div className="text-[11px] text-muted-foreground">
                Counts prompt + completion. Cached tokens still count.
              </div>
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="cost-cap" className="text-xs">
                Daily cost cap (USD)
              </Label>
              <Input
                id="cost-cap"
                type="number"
                inputMode="decimal"
                min={0}
                step={0.01}
                placeholder="blank = no cap"
                value={costCap}
                onChange={(e) => {
                  setCostCap(e.target.value);
                  setLocalError(null);
                }}
              />
              <div className="text-[11px] text-muted-foreground">
                Estimated from provider per-million-token rates.
              </div>
            </div>
          </div>

          <div className="space-y-1.5">
            <Label className="text-xs">On exceed</Label>
            <Select
              value={action}
              onValueChange={(v) => {
                if (v === "block" || v === "warn") {
                  setAction(v);
                  setLocalError(null);
                }
              }}
            >
              <SelectTrigger className="w-[220px]">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="warn">Warn (allow, log event)</SelectItem>
                <SelectItem value="block">Block (raise exception)</SelectItem>
              </SelectContent>
            </Select>
            <div className="text-[11px] text-muted-foreground">
              Roll out new caps as <code>warn</code> first; flip to{" "}
              <code>block</code> once the warning volume is acceptable.
            </div>
          </div>

          {localError && (
            <div className="text-xs text-rose-300 bg-rose-500/10 border border-rose-500/30 rounded-md p-2">
              {localError}
            </div>
          )}

          <div className="flex items-center gap-2 pt-1">
            <Button type="submit" size="sm" disabled={submitting}>
              {submitting ? "Saving…" : "Save budget"}
            </Button>
            <Button
              type="button"
              size="sm"
              variant="outline"
              onClick={onCancel}
              disabled={submitting}
            >
              Cancel
            </Button>
          </div>
        </form>
      </CardContent>
    </Card>
  );
}

// ---------------------------------------------------------------------------
// Main page
// ---------------------------------------------------------------------------

export default function AdminCostPage() {
  const [windowHours, setWindowHours] = useState<string>("24");

  // "This sync" is bounded by a run's own start and end rather than by the
  // clock, so the number answers "what did THAT cost" instead of "what
  // happened in a window that happens to contain it". A run still going has
  // no end yet, which is what makes the meter live.
  const { data: runs = [] } = useQuery<SyncRun[]>({
    queryKey: ["admin-cost-sync-runs"],
    queryFn: () => api.get<SyncRun[]>("/sync-runs", { limit: "20" }),
    retry: false,
  });
  const [syncRunId, setSyncRunId] = useState<string>("");
  const activeRun = runs.find((r) => r.status === "running") ?? null;

  const { data, isLoading, error, refetch, isFetching } = useQuery<LlmUsageResponse>({
    queryKey: ["admin-llm-usage", windowHours, syncRunId],
    queryFn: () =>
      api.get<LlmUsageResponse>("/admin/llm-usage", {
        ...(syncRunId
          ? { sync_run_id: syncRunId }
          : windowHours === "all"
            ? { all_time: "true" }
            : { window_hours: windowHours }),
        top_n_breakdown: "15",
      }),
    // A live sync moves the number every few seconds; a quiet system does
    // not need to be asked every five.
    refetchInterval: syncRunId || activeRun ? 5_000 : 60_000,
  });

  const totals = data?.totals;
  const cacheRate = totals?.cache_hit_rate ?? 0;
  const cacheTone = cacheRateTone(cacheRate);

  return (
    <div className="space-y-6">
      <PageHeader
        title="LLM Cost & Usage"
        description="Per-tenant spend, cache-hit rate, and model/task breakdown. Pick a window, or scope to a single sync run — a run still going updates every 5 seconds."
      />

      <div className="flex flex-wrap items-end gap-3">
        <div className="space-y-1.5">
          <label className="text-xs font-medium text-muted-foreground">
            Window
          </label>
          <Select
            value={windowHours}
            onValueChange={(v) => {
              if (v) {
                setWindowHours(v);
                // A window and a run are two different questions; picking one
                // clears the other rather than silently combining them.
                setSyncRunId("");
              }
            }}
            disabled={!!syncRunId}
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

        <div className="space-y-1.5">
          <label className="text-xs font-medium text-muted-foreground">
            This sync
          </label>
          <Select
            value={syncRunId || "none"}
            onValueChange={(v) => setSyncRunId(!v || v === "none" ? "" : v)}
          >
            <SelectTrigger className="w-[280px]">
              <SelectValue placeholder="Not scoped to a sync" />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="none">Not scoped to a sync</SelectItem>
              {runs.map((r) => (
                <SelectItem key={r.id} value={r.id}>
                  {r.status === "running" ? "▶ " : ""}
                  {r.run_type} · {r.items_processed} items ·{" "}
                  {r.started_at ? new Date(r.started_at).toLocaleString() : "—"}
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
          <section className="grid gap-3 grid-cols-2 lg:grid-cols-5">
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
              label="Thinking tokens"
              value={
                totals && totals.completion_tokens > 0
                  ? formatNumber(totals.reasoning_tokens ?? 0)
                  : "—"
              }
              sub={
                totals && totals.completion_tokens > 0
                  ? `${((totals.reasoning_share ?? 0) * 100).toFixed(0)}% of generated output`
                  : "No output tokens in window"
              }
              icon={Brain}
              tone={
                (totals?.reasoning_share ?? 0) >= 0.5 ? "text-fuchsia-300" : undefined
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

          {/* Daily budget panel — configure + live usage vs cap. */}
          <BudgetPanel />

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
                  answer
                </span>
                <span className="flex items-center gap-1">
                  <span className="inline-block h-2 w-2 rounded-sm bg-fuchsia-500/70" />
                  thinking
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
                              reasoning={row.reasoning_tokens}
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
            Anthropic ephemeral blocks). Thinking tokens are a subset of
            generated output, not an extra charge on top — they are billed at
            the output rate and are already inside the cost above.
          </div>
        </>
      )}
    </div>
  );
}
