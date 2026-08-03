import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import {
  ApplicabilityBadge,
  ApplicabilityPanel,
  KNOWLEDGE_EVIDENCE_TYPES,
} from "./applicability";

describe("ApplicabilityPanel", () => {
  it("distinguishes 'nobody looked' from 'stated no constraints'", () => {
    // These are different facts and must read differently. Collapsing
    // them would claim a check was performed and passed.
    const { unmount } = render(<ApplicabilityPanel applicability={null} />);
    expect(screen.getByText(/not extracted/i)).toBeInTheDocument();
    unmount();

    render(<ApplicabilityPanel applicability={{}} />);
    expect(screen.getByText(/broadly applicable/i)).toBeInTheDocument();
    expect(screen.queryByText(/not extracted/i)).not.toBeInTheDocument();
  });

  it("never renders an empty facet as 'does not apply'", () => {
    // Two thirds of real articles state no version. Silence means it
    // applies broadly, not that it applies nowhere.
    render(<ApplicabilityPanel applicability={{ components: ["activemq"] }} />);
    expect(screen.queryByText(/version/i)).not.toBeInTheDocument();
    expect(screen.getByText("activemq")).toBeInTheDocument();
  });

  it("renders a stated range as a range, not a point version", () => {
    // "8.0 and later" collapsed to "8.0" is how a point reading invents
    // a conflict against an article that covers the reader's release.
    render(
      <ApplicabilityPanel
        applicability={{ version_floor: { jira: "8.0" } }}
      />,
    );
    expect(screen.getByText("jira 8.0 and later")).toBeInTheDocument();
  });

  it("does not also show a point version for a product given a range", () => {
    render(
      <ApplicabilityPanel
        applicability={{
          product_versions: { jira: "8.0" },
          version_floor: { jira: "8.0" },
        }}
      />,
    );
    expect(screen.getByText("jira 8.0 and later")).toBeInTheDocument();
    expect(screen.queryByText("jira 8.0")).not.toBeInTheDocument();
  });

  it("renders a ceiling", () => {
    render(
      <ApplicabilityPanel applicability={{ version_ceiling: { jira: "8.9" } }} />,
    );
    expect(screen.getByText("jira up to 8.9")).toBeInTheDocument();
  });

  it("labels the deployment model in words a reviewer can act on", () => {
    render(<ApplicabilityPanel applicability={{ deployment: "onprem" }} />);
    expect(screen.getByText(/on-premise/i)).toBeInTheDocument();
  });

  it("treats an unconstrained deployment as no constraint", () => {
    // "both" and "unknown" are stored identically for a reason — showing
    // either as a constraint would imply a check that did not narrow
    // anything.
    for (const deployment of ["unknown", "both"]) {
      const { unmount } = render(<ApplicabilityPanel applicability={{ deployment }} />);
      expect(screen.getByText(/broadly applicable/i)).toBeInTheDocument();
      unmount();
    }
  });

  it("warns when facets came from the lexical fallback", () => {
    // The fallback measurably misreads licence versions and IP addresses
    // as product versions, so a reviewer should weigh it less.
    render(
      <ApplicabilityPanel
        applicability={{ components: ["jira"], extracted_by: "rules" }}
      />,
    );
    expect(screen.getByText(/pattern matching/i)).toBeInTheDocument();
  });

  it("does not warn when a model read the article", () => {
    render(
      <ApplicabilityPanel
        applicability={{ components: ["jira"], extracted_by: "llm" }}
      />,
    );
    expect(screen.queryByText(/pattern matching/i)).not.toBeInTheDocument();
  });
});

describe("ApplicabilityBadge", () => {
  it("shows a mismatch as something to check, not as a rejection", () => {
    // A mismatch demotes; it never withholds. The label has to invite a
    // look rather than read as "ignore this".
    render(<ApplicabilityBadge verdict="mismatch" />);
    expect(screen.getByText(/check applicability/i)).toBeInTheDocument();
  });

  it("falls back to 'not stated' for a missing or unrecognised verdict", () => {
    const { unmount } = render(<ApplicabilityBadge />);
    expect(screen.getByText(/not stated/i)).toBeInTheDocument();
    unmount();

    render(<ApplicabilityBadge verdict="banana" />);
    expect(screen.getByText(/not stated/i)).toBeInTheDocument();
  });

  it("shows a positive match", () => {
    render(<ApplicabilityBadge verdict="applies" />);
    expect(screen.getByText(/applies here/i)).toBeInTheDocument();
  });
});

describe("KNOWLEDGE_EVIDENCE_TYPES", () => {
  it("mirrors the backend set — a ticket has an environment, not an applicability", () => {
    expect(KNOWLEDGE_EVIDENCE_TYPES.has("kb_article")).toBe(true);
    expect(KNOWLEDGE_EVIDENCE_TYPES.has("sop")).toBe(true);
    expect(KNOWLEDGE_EVIDENCE_TYPES.has("documentation")).toBe(true);
    expect(KNOWLEDGE_EVIDENCE_TYPES.has("incident")).toBe(false);
    expect(KNOWLEDGE_EVIDENCE_TYPES.has("ticket")).toBe(false);
  });
});
