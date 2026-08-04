import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { PlaybookSteps, sortSteps, type PlaybookStep } from "./playbook-steps";

const STEP: PlaybookStep = {
  text: "Access the AE server and verify the current AE license details.",
  type: "diagnostic",
  order: 2,
  status: "ok",
  on_failure: "If AE license details are not visible, refer to KB [kb-1].",
  expected_outcome: "AE license details are visible and accessible.",
  evidence_quality: "high",
  source_refs: [
    { id: "f0df3937", kind: "knowledge", label: "kb-1", title: "ae license details not visible" },
  ],
};

describe("sortSteps", () => {
  it("orders by the declared order, not array position", () => {
    // The two disagree in stored data, and a procedure executed out of
    // order is worse than no procedure.
    const sorted = sortSteps([
      { text: "third", order: 3 },
      { text: "first", order: 1 },
      { text: "second", order: 2 },
    ]);
    expect(sorted.map((s) => s.text)).toEqual(["first", "second", "third"]);
  });

  it("puts steps with no order last rather than first", () => {
    const sorted = sortSteps([{ text: "unordered" }, { text: "first", order: 1 }]);
    expect(sorted.map((s) => s.text)).toEqual(["first", "unordered"]);
  });

  it("does not mutate the array it was given", () => {
    const input = [{ text: "b", order: 2 }, { text: "a", order: 1 }];
    sortSteps(input);
    expect(input.map((s) => s.text)).toEqual(["b", "a"]);
  });
});

describe("PlaybookSteps", () => {
  it("renders the instruction, outcome and failure branch", () => {
    // The regression: all of this was inside a JSON.stringify, so a
    // generated playbook read as a summary with no steps.
    render(<PlaybookSteps steps={[STEP]} />);
    expect(screen.getByText(/verify the current AE license details/)).toBeInTheDocument();
    expect(screen.getByText(/AE license details are visible/)).toBeInTheDocument();
    expect(screen.getByText(/refer to KB/)).toBeInTheDocument();
    expect(screen.getByText("diagnostic")).toBeInTheDocument();
  });

  it("shows the citations a step was grounded in", () => {
    // A reviewer approving a procedure needs to see it came from real
    // incidents; a step citing nothing is the one to question.
    render(<PlaybookSteps steps={[STEP]} />);
    expect(screen.getByText("kb-1")).toBeInTheDocument();
    expect(screen.getByText(/ae license details not visible/)).toBeInTheDocument();
  });

  it("says a version has no steps rather than rendering an empty list", () => {
    // An empty playbook cannot enter review or execute, and silence
    // here reads as a rendering failure instead of the real state.
    render(<PlaybookSteps steps={[]} />);
    expect(screen.getByText(/no steps/i)).toBeInTheDocument();
  });

  it("survives a steps field that is not an array", () => {
    // The API types it `list`, and older rows have held objects.
    render(<PlaybookSteps steps={{ nope: true } as unknown} />);
    expect(screen.getByText(/no steps/i)).toBeInTheDocument();
  });

  it("renders a step missing every optional field", () => {
    render(<PlaybookSteps steps={[{ text: "Restart the broker." }]} />);
    expect(screen.getByText("Restart the broker.")).toBeInTheDocument();
  });

  it("does not silently drop a step with no instruction text", () => {
    // A blank step is a defect in generation. Rendering nothing would
    // hide it and make the step count disagree with what is shown.
    render(<PlaybookSteps steps={[{ order: 1, type: "remediation" }]} />);
    expect(screen.getByText(/no instruction text/i)).toBeInTheDocument();
  });

  it("renders every step it was given", () => {
    const steps = Array.from({ length: 8 }, (_, i) => ({
      text: `Step number ${i + 1}`,
      order: i + 1,
    }));
    render(<PlaybookSteps steps={steps} />);
    for (const step of steps) {
      expect(screen.getByText(step.text)).toBeInTheDocument();
    }
  });
});
