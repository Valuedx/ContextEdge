import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { GraphQueryControls } from "./graph-query-controls";

vi.mock("@/lib/hooks/use-tenants", () => ({
  useDomains: () => ({
    data: [
      { id: "active-domain", name: "Payments", is_active: true },
      { id: "inactive-domain", name: "Retired", is_active: false },
    ],
    isLoading: false,
  }),
}));

describe("GraphQueryControls", () => {
  it("shows active domains and emits scope changes", () => {
    const onDomainChange = vi.fn();
    const onHistoricalChange = vi.fn();

    render(
      <GraphQueryControls
        domainId=""
        onDomainChange={onDomainChange}
        historical={false}
        onHistoricalChange={onHistoricalChange}
        asOfLocal=""
        onAsOfLocalChange={vi.fn()}
      />,
    );

    expect(screen.getByRole("option", { name: "Payments" })).toBeInTheDocument();
    expect(
      screen.queryByRole("option", { name: "Retired" }),
    ).not.toBeInTheDocument();

    fireEvent.change(screen.getByRole("combobox"), {
      target: { value: "active-domain" },
    });
    fireEvent.click(screen.getByRole("button", { name: "As of" }));

    expect(onDomainChange).toHaveBeenCalledWith("active-domain");
    expect(onHistoricalChange).toHaveBeenCalledWith(true);
  });

  it("labels historical topology semantics", () => {
    render(
      <GraphQueryControls
        domainId=""
        onDomainChange={vi.fn()}
        historical
        onHistoricalChange={vi.fn()}
        asOfLocal="2026-07-28T10:00"
        onAsOfLocalChange={vi.fn()}
      />,
    );

    expect(
      screen.getByText(/Topology reflects that time/),
    ).toBeInTheDocument();
    expect(screen.getByLabelText("Date and time")).toHaveValue(
      "2026-07-28T10:00",
    );
  });
});
