"use client";

import { Clock3, Database } from "lucide-react";

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
    <div className="flex flex-wrap items-end gap-3 border-y bg-background/35 px-1 py-3">
      <div className="min-w-52 space-y-1">
        <label className="flex items-center gap-1.5 text-xs text-muted-foreground">
          <Database className="size-3.5" />
          Domain
        </label>
        <select
          value={domainId}
          onChange={(event) => onDomainChange(event.target.value)}
          disabled={isLoading}
          className="h-8 w-full rounded-lg border border-white/15 bg-white/[0.06] px-2.5 text-sm outline-none"
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
      </div>

      <div className="space-y-1">
        <span className="flex items-center gap-1.5 text-xs text-muted-foreground">
          <Clock3 className="size-3.5" />
          Time
        </span>
        <div className="inline-flex rounded-lg border bg-muted/35 p-0.5">
          <Button
            type="button"
            size="sm"
            variant={historical ? "ghost" : "secondary"}
            className="h-7 rounded-md px-3"
            onClick={() => onHistoricalChange(false)}
          >
            Current
          </Button>
          <Button
            type="button"
            size="sm"
            variant={historical ? "secondary" : "ghost"}
            className="h-7 rounded-md px-3"
            onClick={() => onHistoricalChange(true)}
          >
            As of
          </Button>
        </div>
      </div>

      {historical && (
        <div className="min-w-56 space-y-1">
          <label className="text-xs text-muted-foreground" htmlFor="graph-as-of">
            Date and time
          </label>
          <Input
            id="graph-as-of"
            type="datetime-local"
            value={asOfLocal}
            max={new Date().toISOString().slice(0, 16)}
            onChange={(event) => onAsOfLocalChange(event.target.value)}
          />
        </div>
      )}

      {historical && asOfLocal && (
        <p className="max-w-md text-xs text-muted-foreground">
          Topology reflects that time. Node facts reflect their current state.
        </p>
      )}
    </div>
  );
}
