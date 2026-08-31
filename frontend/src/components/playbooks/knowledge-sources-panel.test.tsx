import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { KnowledgeSourcesPanel } from "./knowledge-sources-panel";
import type { PlaybookVersion } from "@/lib/types";

function version(over: Partial<PlaybookVersion> = {}): PlaybookVersion {
  return {
    id: "v1",
    playbook_id: "p1",
    semantic_version: "1.0.0",
    trigger_conditions: {},
    branching_logic: {},
    inputs: [],
    outputs: [],
    steps: [],
    rollback_notes: null,
    evidence_refs: null,
    conflicts: null,
    playbook_confidence: 0.8,
    execution_confidence_guidance: null,
    published_at: null,
    created_at: "2026-08-31T00:00:00Z",
    ...over,
  } as PlaybookVersion;
}

describe("KnowledgeSourcesPanel", () => {
  it("names the ticket version used at generation and a KB mismatch", () => {
    // The generator stores ticket_version and version_mismatch on
    // evidence_refs. If this panel only showed titles, a reviewer would
    // approve steps grounded in a different-release article with no
    // warning on the page they actually read.
    render(
      <KnowledgeSourcesPanel
        version={version({
          evidence_refs: {
            knowledge_ids: ["kb-1"],
            ticket_version: "8.2.3",
            knowledge: [
              {
                evidence_id: "kb-1",
                title: "License not visible",
                evidence_type: "kb_article",
                applicability_verdict: "mismatch",
                product_version: "7*",
                version_mismatch: ["7*", "8.2.3"],
              },
            ],
          },
        })}
      />,
    );
    expect(screen.getByText(/ticket product version used at generation/i)).toBeInTheDocument();
    expect(screen.getByText(/AutomationEdge 8\.2\.3/)).toBeInTheDocument();
    expect(
      screen.getByText(/Based on KB for AutomationEdge 7\* \(this ticket is 8\.2\.3\)/),
    ).toBeInTheDocument();
  });

  it("labels a matching KB rather than inventing a conflict", () => {
    render(
      <KnowledgeSourcesPanel
        version={version({
          evidence_refs: {
            knowledge_ids: ["kb-1"],
            ticket_version: "8.2.3",
            knowledge: [
              {
                evidence_id: "kb-1",
                title: "AE 8 license",
                product_version: "8*",
                version_mismatch: null,
              },
            ],
          },
        })}
      />,
    );
    expect(screen.getByText(/KB for AutomationEdge 8\*/)).toBeInTheDocument();
    expect(screen.getByText(/matches ticket 8\.2\.3/)).toBeInTheDocument();
    expect(screen.queryByText(/this ticket is/)).not.toBeInTheDocument();
  });

  it("treats an unversioned article as version-agnostic, not as a mismatch", () => {
    render(
      <KnowledgeSourcesPanel
        version={version({
          evidence_refs: {
            knowledge_ids: ["kb-1"],
            ticket_version: "7*",
            knowledge: [
              {
                evidence_id: "kb-1",
                title: "Generic SOP",
                product_version: null,
                version_mismatch: null,
              },
            ],
          },
        })}
      />,
    );
    expect(screen.getByText(/treated as version-agnostic/i)).toBeInTheDocument();
    expect(screen.queryByText(/Based on KB for/)).not.toBeInTheDocument();
  });
});
