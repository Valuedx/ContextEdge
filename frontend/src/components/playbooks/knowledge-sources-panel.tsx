"use client";

/**
 * The approved knowledge a playbook version was generated from, including
 * the ticket AE version used at generation and any KB/ticket mismatch.
 *
 * A reviewer asking "which SOP does this implement" needs more than a
 * list of titles: an article flagged as written for a release this
 * estate does not run still informed the playbook, and that caveat was
 * computed and shown to the model. Dropping it here would leave the
 * reviewer approving steps grounded in a document nobody told them was
 * out of scope.
 */

import Link from "next/link";

import { ApplicabilityBadge } from "@/components/common/applicability";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import type { PlaybookKnowledgeRef, PlaybookVersion } from "@/lib/types";

function versionMismatchLine(doc: PlaybookKnowledgeRef): string | null {
  const pair = doc.version_mismatch;
  if (!pair || pair.length < 2) return null;
  return `Based on KB for AutomationEdge ${pair[0]} (this ticket is ${pair[1]})`;
}

export function KnowledgeSourcesPanel({ version }: { version: PlaybookVersion }) {
  const refs = version.evidence_refs ?? null;
  const knowledge = refs?.knowledge;
  const ticketVersion = refs?.ticket_version ?? null;

  // Versions generated before applicability was recorded carry
  // knowledge_ids but no verdicts. Say so rather than rendering a
  // verdict-less list that looks like everything checked out.
  if (!knowledge || knowledge.length === 0) {
    const count = refs?.knowledge_ids?.length ?? 0;
    if (count === 0) {
      return (
        <Card className="border-dashed shadow-none">
          <CardHeader>
            <CardTitle>Approved knowledge used</CardTitle>
            <CardDescription className="text-xs">
              None. This playbook reflects observed practice only — no KB
              article or SOP was matched to the pattern.
            </CardDescription>
          </CardHeader>
        </Card>
      );
    }
    return (
      <Card>
        <CardHeader>
          <CardTitle>Approved knowledge used</CardTitle>
          <CardDescription className="text-xs">
            {count} document{count === 1 ? "" : "s"}. Applicability was not
            recorded for this version, so whether they match this
            environment is unknown here.
          </CardDescription>
        </CardHeader>
      </Card>
    );
  }

  const flagged = knowledge.filter(
    (doc) => doc.applicability_verdict === "mismatch" || Boolean(doc.version_mismatch),
  ).length;

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex flex-wrap items-center gap-2">
          Approved knowledge used
          {flagged > 0 && (
            <span className="rounded-full bg-amber-500/20 px-2 py-0.5 text-[10px] font-medium">
              {flagged} flagged
            </span>
          )}
        </CardTitle>
        <CardDescription className="text-xs">
          What the organisation says should be done, as matched to this
          pattern. A flagged document still informed the playbook — it was
          ranked lower, not withheld.
        </CardDescription>
        {ticketVersion ? (
          <p className="text-xs text-foreground">
            Ticket product version used at generation:{" "}
            <span className="font-medium">AutomationEdge {ticketVersion}</span>
          </p>
        ) : (
          <p className="text-xs text-muted-foreground">
            Ticket product version was not recorded for this generation.
          </p>
        )}
      </CardHeader>

      <CardContent className="space-y-2">
        {knowledge.map((doc) => {
          const mismatch = versionMismatchLine(doc);
          return (
            <div
              key={doc.evidence_id}
              className="rounded-md border bg-background p-3 space-y-1"
            >
              <div className="flex items-start justify-between gap-2">
                <Link
                  href={`/evidence/${doc.evidence_id}`}
                  className="text-sm font-medium hover:underline"
                >
                  {doc.title || doc.evidence_id}
                </Link>
                <ApplicabilityBadge verdict={doc.applicability_verdict} />
              </div>
              {doc.evidence_type && (
                <p className="text-[11px] text-muted-foreground">
                  {doc.evidence_type}
                </p>
              )}
              {mismatch ? (
                <p className="text-xs text-amber-700 dark:text-amber-300">
                  {mismatch}
                </p>
              ) : doc.product_version ? (
                <p className="text-xs text-muted-foreground">
                  KB for AutomationEdge {doc.product_version}
                  {ticketVersion ? ` (matches ticket ${ticketVersion})` : ""}
                </p>
              ) : ticketVersion ? (
                <p className="text-xs text-muted-foreground">
                  Version not stated on this article (ticket is{" "}
                  {ticketVersion}; treated as version-agnostic)
                </p>
              ) : null}
              {doc.applicability_notes && doc.applicability_notes.length > 0 && (
                <ul className="mt-1 space-y-0.5">
                  {doc.applicability_notes.map((note, index) => (
                    <li key={index} className="text-xs text-muted-foreground">
                      • {note}
                    </li>
                  ))}
                </ul>
              )}
            </div>
          );
        })}
      </CardContent>
    </Card>
  );
}
