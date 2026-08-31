/**
 * "Does this article apply to the system in front of me?"
 *
 * Semantic search answers "same subject". It does not answer "same
 * system", and the gap between those is where a confidently wrong
 * citation comes from — an article for a vendor's cloud edition reads
 * perfectly against a self-hosted incident right up to the step that
 * cannot be performed there.
 *
 * Two rules this UI has to keep, because they are what make the feature
 * safe rather than merely clever:
 *
 * 1. A mismatch is DEMOTED, never hidden. An article a release behind is
 *    often the only guidance that exists, so it is shown with its
 *    caveat. Rendering a warning is the entire point; suppressing the
 *    row would leave a reviewer with nothing and no idea anything was
 *    withheld.
 *
 * 2. Silence is not inapplicability. An empty facet means the article
 *    stated nothing on that axis, which usually means it applies
 *    broadly. It must never render as "does not apply".
 */

import type { EvidenceApplicability } from "@/lib/types";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { cn } from "@/lib/utils";

/**
 * Evidence types that carry an applicability. Mirrors
 * `services.evidence_typing.KNOWLEDGE_EVIDENCE_TYPES` — a ticket does
 * not have an applicability, it has an environment.
 */
export const KNOWLEDGE_EVIDENCE_TYPES = new Set([
  "kb_article",
  "sop",
  "documentation",
]);

const DEPLOYMENT_LABELS: Record<string, string> = {
  cloud: "Cloud / SaaS",
  onprem: "On-premise / self-hosted",
};

const VERDICT_STYLES: Record<string, string> = {
  applies: "border-emerald-500/40 bg-emerald-500/10 text-emerald-700 dark:text-emerald-300",
  mismatch: "border-amber-500/50 bg-amber-500/10 text-amber-700 dark:text-amber-300",
  unknown: "border-border bg-muted text-muted-foreground",
};

const VERDICT_LABELS: Record<string, string> = {
  applies: "Applies here",
  mismatch: "Check applicability",
  unknown: "Not stated",
};

/** Compact verdict pill for lists and cited-source rows. */
export function ApplicabilityBadge({ verdict }: { verdict?: string }) {
  const key = verdict && verdict in VERDICT_STYLES ? verdict : "unknown";
  return (
    <span
      className={`rounded-full border px-2 py-0.5 text-[10px] font-medium ${VERDICT_STYLES[key]}`}
    >
      {VERDICT_LABELS[key]}
    </span>
  );
}

function FacetRow({ label, values }: { label: string; values: string[] }) {
  if (values.length === 0) return null;
  return (
    <div className="flex items-start justify-between gap-3">
      <span className="pt-0.5 text-muted-foreground">{label}</span>
      <div className="flex flex-wrap justify-end gap-1">
        {values.map((value) => (
          <span
            key={value}
            className="rounded border bg-muted/50 px-1.5 py-0.5 text-[11px] font-medium"
          >
            {value}
          </span>
        ))}
      </div>
    </div>
  );
}

function versionFacet(app: EvidenceApplicability): string[] {
  const out: string[] = [];
  // Ranges first and rendered AS ranges. "8.0 and later" collapsed to
  // "8.0" is how a point-version reading invents a conflict against an
  // article that explicitly covers the reader's release.
  for (const [product, version] of Object.entries(app.version_floor ?? {})) {
    out.push(`${product} ${version} and later`);
  }
  for (const [product, version] of Object.entries(app.version_ceiling ?? {})) {
    out.push(`${product} up to ${version}`);
  }
  for (const [product, version] of Object.entries(app.product_versions ?? {})) {
    // A product already described by a range is not also a point.
    if (app.version_floor?.[product] || app.version_ceiling?.[product]) continue;
    out.push(`${product} ${version}`);
  }
  return out;
}

/**
 * Full panel for a knowledge document's own applicability.
 *
 * `applicability === null | undefined` and an empty object mean
 * different things and must read differently: nobody looked, versus it
 * was read and stated no constraints. Collapsing the two would claim a
 * check was performed.
 */
export function ApplicabilityPanel({
  applicability,
  className,
}: {
  applicability?: EvidenceApplicability | null;
  /** Extra classes on the card, so a page with its own palette can theme it. */
  className?: string;
}) {
  if (applicability === null || applicability === undefined) {
    return (
      <Card className={cn("border-dashed shadow-none", className)}>
        <CardHeader className="border-b pb-3">
          <CardTitle className="text-sm font-bold uppercase tracking-wider text-muted-foreground">
            Where this applies
          </CardTitle>
        </CardHeader>
        <CardContent className="pt-4">
          <p className="text-xs leading-5 text-muted-foreground">
            Not extracted — this document was ingested before applicability
            was read, or it is not knowledge content.
          </p>
        </CardContent>
      </Card>
    );
  }

  const components = applicability.components ?? [];
  const environments = applicability.environments ?? [];
  const platforms = applicability.platforms ?? [];
  const versions = versionFacet(applicability);
  const deployment =
    applicability.deployment && applicability.deployment in DEPLOYMENT_LABELS
      ? DEPLOYMENT_LABELS[applicability.deployment]
      : null;

  const stated =
    components.length + environments.length + platforms.length + versions.length >
      0 || deployment !== null;

  return (
    <Card className={className}>
      <CardHeader className="border-b pb-3">
        <CardTitle className="text-sm font-bold uppercase tracking-wider text-muted-foreground">
          Where this applies
        </CardTitle>
        <CardDescription className="text-xs">
          {stated
            ? "Used to rank this document against an incident's environment. A mismatch demotes it and shows why; it is never hidden."
            : "This document states no component, deployment or version constraints — treated as broadly applicable rather than as applying nowhere."}
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-3.5 pt-4 text-sm">
        {stated && (
          <>
            <FacetRow label="Component" values={components} />
            {deployment && <FacetRow label="Deployment" values={[deployment]} />}
            <FacetRow label="Version" values={versions} />
            <FacetRow label="Environment" values={environments} />
            <FacetRow label="Platform" values={platforms} />
          </>
        )}
        {applicability.extracted_by === "rules" && (
          // Worth surfacing: the lexical fallback measurably misreads
          // licence versions and IP addresses as product versions, so a
          // reviewer should weigh these facets less than a model's reading.
          <p className="text-[11px] leading-5 text-muted-foreground">
            Read by pattern matching rather than by a model — less reliable
            on version and deployment.
          </p>
        )}
      </CardContent>
    </Card>
  );
}
