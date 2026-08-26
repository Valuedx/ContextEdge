import { fireEvent, render, screen } from "@testing-library/react";
import { useState } from "react";
import { describe, expect, it } from "vitest";

import { PlaybookStepsEditor } from "./playbook-steps-editor";
import { ensureStepIds, type EditableStep } from "@/lib/playbook-steps";

function Harness({ initial }: { initial: EditableStep[] }) {
  const [steps, setSteps] = useState(initial);
  return <PlaybookStepsEditor steps={steps} onChange={setSteps} />;
}

describe("PlaybookStepsEditor", () => {
  it("adds, deletes and reorders steps", () => {
    render(
      <Harness
        initial={ensureStepIds([
          { step_id: "a", text: "First", type: "diagnostic" },
          { step_id: "b", text: "Second", type: "remediation" },
        ])}
      />,
    );
    expect(screen.getByDisplayValue("First")).toBeInTheDocument();
    fireEvent.click(screen.getAllByLabelText("Add step after")[1]);
    expect(screen.getAllByLabelText("Delete step")).toHaveLength(3);
    fireEvent.click(screen.getAllByLabelText("Delete step")[2]);
    expect(screen.getAllByLabelText("Delete step")).toHaveLength(2);
    fireEvent.click(screen.getAllByLabelText("Move step down")[0]);
    const instructions = screen.getAllByLabelText("Instruction");
    expect((instructions[0] as HTMLTextAreaElement).value).toBe("Second");
  });
});
