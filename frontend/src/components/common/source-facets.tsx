/**
 * What the source system already recorded about a ticket or thread
 * message: product version, ticket number, environment, root cause.
 *
 * Distinct from applicability, which is extracted from knowledge prose.
 * A ticket does not have an applicability — it has an environment.
 * Empty means the source left those fields blank, not that nobody looked.
 */

import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { cn } from "@/lib/utils";

const LABELS: Record<string, string> = {
  version: "Product version",
  ticket_number: "Ticket number",
  environment: "Environment",
  root_cause: "Root cause",
  component: "Component",
  customer: "Customer",
  region: "Region",
  ticket_type: "Ticket type",
};

const REVIEW_KEYS = [
  "version",
  "ticket_number",
  "environment",
  "root_cause",
  "component",
  "customer",
  "region",
  "ticket_type",
] as const;

function statedEntries(facets: Record<string, string> | undefined): [string, string][] {
  if (!facets) return [];
  return REVIEW_KEYS.flatMap((key) => {
    const value = facets[key];
    return value ? [[LABELS[key], value] as [string, string]] : [];
  });
}

export function SourceFacetsPanel({
  facets,
  className,
}: {
  facets?: Record<string, string> | null;
  className?: string;
}) {
  const entries = statedEntries(facets ?? undefined);

  if (entries.length === 0) {
    return (
      <Card className={cn("border-dashed shadow-none", className)}>
        <CardHeader className="border-b pb-3">
          <CardTitle className="text-sm font-bold uppercase tracking-wider text-muted-foreground">
            What the source recorded
          </CardTitle>
        </CardHeader>
        <CardContent className="pt-4">
          <p className="text-xs leading-5 text-muted-foreground">
            No product version, ticket number, or environment on this row.
            The source left those fields blank — they were not inferred.
          </p>
        </CardContent>
      </Card>
    );
  }

  return (
    <Card className={className}>
      <CardHeader className="border-b pb-3">
        <CardTitle className="text-sm font-bold uppercase tracking-wider text-muted-foreground">
          What the source recorded
        </CardTitle>
        <CardDescription className="text-xs">
          Copied from the ticket at ingest, including onto related mail-thread
          messages. Blank fields stay blank rather than being guessed.
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-3.5 pt-4 text-sm">
        {entries.map(([label, value]) => (
          <div key={label} className="flex items-center justify-between gap-3">
            <span className="text-muted-foreground">{label}</span>
            <span className="text-right font-medium text-foreground">{value}</span>
          </div>
        ))}
      </CardContent>
    </Card>
  );
}
