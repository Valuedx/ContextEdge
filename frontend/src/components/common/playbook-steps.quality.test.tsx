import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { PlaybookSteps } from "./playbook-steps";
import type { PlaybookQualityFinding } from "@/lib/types";

function finding(over: Partial<PlaybookQualityFinding> = {}): PlaybookQualityFinding {
  return {
    id: "f1",
    category: "stale_grounding",
    dimension: "evidence_grounding",
    severity: "major",
    target_kind: "step",
    target_ref: "s1",
    claim: null,
    explanation: "This step was edited but kept its original citations.",
    supporting_spans: [],
    contradicting_spans: [],
    validator: "grounding_integrity",
    confidence: null,
    remediation_category: null,
    created_at: "2026-09-01T00:00:00Z",
    ...over,
  };
}

const steps = [
  { step_id: "s1", order: 1, type: "remediation", text: "Restart the agent." },
  { step_id: "s2", order: 2, type: "verification", text: "Confirm it is running." },
];

describe("PlaybookSteps with quality findings", () => {
  it("shows a finding against the step it is about, not against the list", () => {
    // A finding three panels away from the step it describes is a finding the
    // reviewer scrolls past.
    render(<PlaybookSteps steps={steps} findings={[finding()]} />);
    const items = screen.getAllByRole("listitem");
    expect(within(items[0]).getByText(/kept its original citations/)).toBeInTheDocument();
    expect(within(items[1]).queryByText(/kept its original citations/)).toBeNull();
  });

  it("renders nothing extra when no findings are passed", () => {
    // Every existing call site passes no findings and must be unaffected.
    render(<PlaybookSteps steps={steps} />);
    expect(screen.queryByText(/stale grounding/)).toBeNull();
    expect(screen.getAllByRole("listitem")).toHaveLength(2);
  });

  it("ignores findings that are not about a step", () => {
    render(
      <PlaybookSteps
        steps={steps}
        findings={[finding({ target_kind: "playbook", target_ref: null })]}
      />,
    );
    expect(screen.queryByText(/kept its original citations/)).toBeNull();
  });

  it("matches a step by order when it has no step_id yet", () => {
    // Hand-authored and legacy steps have no step_id; the backend addresses
    // those by their 1-based order.
    render(
      <PlaybookSteps
        steps={[{ order: 1, type: "remediation", text: "Restart the agent." }]}
        findings={[finding({ target_ref: "1" })]}
      />,
    );
    expect(screen.getByText(/kept its original citations/)).toBeInTheDocument();
  });

  it("distinguishes a blocking finding from an advisory one", () => {
    render(
      <PlaybookSteps
        steps={steps}
        findings={[
          finding({ id: "a", severity: "critical", explanation: "Blocking problem." }),
          finding({ id: "b", severity: "info", explanation: "Advisory note." }),
        ]}
      />,
    );
    const blocking = screen.getByText(/Blocking problem/).closest("p");
    const advisory = screen.getByText(/Advisory note/).closest("p");
    expect(blocking?.className).toContain("destructive");
    expect(advisory?.className).not.toContain("destructive");
  });
});
