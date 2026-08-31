import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { SourceFacetsPanel } from "./source-facets";

describe("SourceFacetsPanel", () => {
  it("shows the ticket product version the playbook matcher reads", () => {
    render(
      <SourceFacetsPanel
        facets={{ version: "8.2.3", ticket_number: "245390", environment: "T3" }}
      />,
    );
    expect(screen.getByText("Product version")).toBeInTheDocument();
    expect(screen.getByText("8.2.3")).toBeInTheDocument();
    expect(screen.getByText("245390")).toBeInTheDocument();
    expect(screen.getByText("T3")).toBeInTheDocument();
  });

  it("does not treat a blank map as a recorded version", () => {
    // Empty is the normal state for unmapped sources and for tickets
    // whose custom fields were never filled. Claiming a version here
    // would invent one the matcher correctly refuses to guess.
    render(<SourceFacetsPanel facets={{}} />);
    expect(screen.getByText(/left those fields blank/i)).toBeInTheDocument();
    expect(screen.queryByText("Product version")).not.toBeInTheDocument();
  });
});
