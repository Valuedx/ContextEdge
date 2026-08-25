"use client";

import { ChevronDown, Clock3, Database } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { useDomains } from "@/lib/hooks/use-tenants";

interface GraphQueryControlsProps {
  domainId: string;
  onDomainChange: (value: string) => void;
  historical: boolean;
  onHistoricalChange: (value: boolean) => void;
  asOfLocal: string;
  onAsOfLocalChange: (value: string) => void;
}

export function GraphQueryControls({
  domainId,
  onDomainChange,
  historical,
  onHistoricalChange,
  asOfLocal,
  onAsOfLocalChange,
}: GraphQueryControlsProps) {
  const { data: domains = [], isLoading } = useDomains();

  return (
    <div className="flex flex-wrap items-end gap-3 rounded-lg border bg-card p-3 shadow-sm">
      <div className="flex min-w-48 sm:min-w-56 flex-col gap-1">
        <label className="flex items-center gap-1.5 text-xs font-medium text-muted-foreground">
          <Database className="size-3.5" />
          Domain
        </label>
        <div className="relative">
          <select
            value={domainId}
            onChange={(event) => onDomainChange(event.target.value)}
            disabled={isLoading}
            className="h-8 w-full appearance-none rounded-md border border-input bg-background pl-2.5 pr-8 text-xs font-medium outline-none transition-colors hover:border-slate-400 focus-visible:border-ring focus-visible:ring-2 focus-visible:ring-ring/20 disabled:cursor-not-allowed disabled:opacity-50"
          >
            <option value="">All visible domains</option>
            {domains
              .filter((domain) => domain.is_active)
              .map((domain) => (
                <option key={domain.id} value={domain.id}>
                  {domain.name}
                </option>
              ))}
          </select>
          <ChevronDown className="pointer-events-none absolute right-2.5 top-1/2 -translate-y-1/2 size-3.5 text-muted-foreground opacity-60" />
        </div>
      </div>

      <div className="flex flex-col gap-1">
        <span className="flex items-center gap-1.5 text-xs font-medium text-muted-foreground">
          <Clock3 className="size-3.5" />
          Time
        </span>
        <div className="inline-flex h-8 items-center rounded-lg border bg-muted/40 p-0.5">
          <Button
            type="button"
            size="sm"
            variant={historical ? "ghost" : "secondary"}
            className="h-7 rounded-md px-3 text-xs"
            onClick={() => onHistoricalChange(false)}
          >
            Current
          </Button>
          <Button
            type="button"
            size="sm"
            variant={historical ? "secondary" : "ghost"}
            className="h-7 rounded-md px-3 text-xs"
            onClick={() => onHistoricalChange(true)}
          >
            As of
          </Button>
        </div>
      </div>

      {historical && (
        <div className="flex min-w-56 flex-col gap-1">
          <label className="text-xs font-medium text-muted-foreground" htmlFor="graph-as-of">
            Date and time
          </label>
          <Input
            id="graph-as-of"
            type="datetime-local"
            className="h-8 text-xs"
            value={asOfLocal}
            max={new Date().toISOString().slice(0, 16)}
            onChange={(event) => onAsOfLocalChange(event.target.value)}
          />
        </div>
      )}

      {historical && asOfLocal && (
        <p className="self-center pt-3 text-xs text-muted-foreground sm:pt-0 sm:self-end sm:pb-1">
          Topology reflects that time. Node facts reflect their current state.
        </p>
      )}
    </div>
  );
}
