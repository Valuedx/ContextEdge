/**
 * What the source system already recorded about a ticket or thread
 * message: product version, ticket number, environment, root cause.
 *
 * Distinct from applicability, which is extracted from knowledge prose.
 * A ticket does not have an applicability — it has an environment.
 * Empty means the source left those fields blank, not that nobody looked.
 */

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
  const container = className ?? "rounded-lg border p-4";

  if (entries.length === 0) {
    return (
      <div className={className ?? "rounded-lg border border-dashed p-4"}>
        <h3 className="text-sm font-semibold">What the source recorded</h3>
        <p className="mt-1 text-xs text-muted-foreground">
          No product version, ticket number, or environment on this row.
          The source left those fields blank — they were not inferred.
        </p>
      </div>
    );
  }

  return (
    <div className={`${container} space-y-3`}>
      <div>
        <h3 className="text-sm font-semibold">What the source recorded</h3>
        <p className="mt-1 text-xs text-muted-foreground">
          Copied from the ticket at ingest, including onto related mail-thread
          messages. Blank fields stay blank rather than being guessed.
        </p>
      </div>
      <dl className="grid gap-2 text-sm sm:grid-cols-2">
        {entries.map(([label, value]) => (
          <div key={label} className="flex items-baseline justify-between gap-2">
            <dt className="text-xs text-muted-foreground">{label}</dt>
            <dd className="font-medium">{value}</dd>
          </div>
        ))}
      </dl>
    </div>
  );
}
