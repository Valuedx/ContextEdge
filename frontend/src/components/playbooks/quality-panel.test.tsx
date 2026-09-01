import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import {
  render,
  screen,
  waitForElementToBeRemoved,
  within,
} from "@testing-library/react";
import type { ReactNode } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { QualityCell, QualityPanel, findingsForStep } from "./quality-panel";
import type {
  PlaybookQuality,
  PlaybookQualityFinding,
  PlaybookQualitySummary,
} from "@/lib/types";

vi.mock("@/lib/api", () => ({
  api: { get: vi.fn() },
}));

const { api } = await import("@/lib/api");

function summary(over: Partial<PlaybookQualitySummary> = {}): PlaybookQualitySummary {
  return {
    state: "fail",
    structure: "pass",
    groups: { subject: "inconclusive", steps: "fail", coherence: null },
    coverage: { decided: 3, undecided: 11, total: 14 },
    finding_counts: { critical: 1, major: 0, minor: 1, info: 4 },
    matches_current_content: true,
    assessed_at: "2026-09-01T00:00:00Z",
    stale_reason: null,
    evaluation_mode: "shadow",
    ...over,
  };
}

function finding(over: Partial<PlaybookQualityFinding> = {}): PlaybookQualityFinding {
  return {
    id: "f1",
    category: "empty_procedure",
    dimension: "structure",
    severity: "critical",
    target_kind: "playbook",
    target_ref: null,
    claim: null,
    explanation: "The playbook has no steps.",
    supporting_spans: [],
    contradicting_spans: [],
    validator: "structural",
    confidence: null,
    remediation_category: null,
    created_at: "2026-09-01T00:00:00Z",
    ...over,
  };
}

function quality(over: Partial<PlaybookQuality> = {}): PlaybookQuality {
  return {
    playbook_id: "p1",
    content_hash: "a".repeat(64),
    assessment_id: "a1",
    content_revision_id: "r1",
    assessed_content_hash: "a".repeat(64),
    validator_bundle_version: "qa-2026.09.01",
    dimension_states: {},
    summary: summary(),
    findings: [finding()],
    readiness: { ready: false, state: "fail", blocked_reason: "assessment_fail" },
    started_at: null,
    completed_at: null,
    stale_at: null,
    superseded_at: null,
    ...over,
  };
}

function wrapper({ children }: { children: ReactNode }) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return <QueryClientProvider client={client}>{children}</QueryClientProvider>;
}

async function renderPanel(payload: PlaybookQuality) {
  vi.mocked(api.get).mockResolvedValue(payload);
  render(<QualityPanel playbookId="p1" />, { wrapper });
  // Wait for the loading card to go, not for the title: the loading state
  // renders the same "Quality" heading, so awaiting that resolves while the
  // query is still in flight and any synchronous assertion after it races.
  await waitForElementToBeRemoved(() => screen.queryByText("Loading assessment…"));
}

afterEach(() => vi.mocked(api.get).mockReset());

describe("QualityPanel", () => {
  it("leads with coverage, so the verdict is read in proportion to it", async () => {
    // With 11 of 14 validators unbuilt, the state is mostly a statement about
    // our own coverage. A reviewer who sees only a red badge will conclude
    // something about the playbook that the data does not support.
    await renderPanel(quality());
    expect(await screen.findByText("3 of 14 checks run")).toBeInTheDocument();
    expect(
      screen.getByText(/11 of 14 checks are not built yet/),
    ).toBeInTheDocument();
  });

  it("says an absent finding is not evidence of quality", async () => {
    await renderPanel(quality({ findings: [], summary: summary({ finding_counts: {} }) }));
    expect(
      await screen.findByText(/nothing the current checks can see/),
    ).toBeInTheDocument();
  });

  it("distinguishes never-assessed from assessed-and-clean", async () => {
    // An empty panel reads as approval, which is the one thing it must not do.
    await renderPanel(quality({ assessment_id: null }));
    expect(
      await screen.findByText(/never been assessed/),
    ).toBeInTheDocument();
    expect(screen.getByText(/not the same as/)).toBeInTheDocument();
  });

  it("warns first when the content moved after assessment", async () => {
    // The findings below are about text no longer on screen. A healthy-looking
    // assessment about invisible content is worse than none.
    await renderPanel(
      quality({ summary: summary({ matches_current_content: false }) }),
    );
    expect(
      await screen.findByText(/This assessment is out of date/),
    ).toBeInTheDocument();
  });

  it("explains a stale reason in words rather than a slug", async () => {
    await renderPanel(
      quality({ summary: summary({ state: "stale", stale_reason: "source_changed" }) }),
    );
    expect(await screen.findByText(/a cited source changed/)).toBeInTheDocument();
  });

  it("renders structure as a banner, not as a fourth group", async () => {
    // A precondition: while it fails, the three verdicts are about a
    // procedure that cannot be followed as written.
    await renderPanel(quality({ summary: summary({ structure: "fail" }) }));
    expect(
      await screen.findByText(/The artifact itself is malformed/),
    ).toBeInTheDocument();
    // exactly the three peer groups, and structure is not one of them
    expect(screen.getByText("Subject & title")).toBeInTheDocument();
    expect(screen.getByText("Steps")).toBeInTheDocument();
    expect(screen.getByText("Coherence")).toBeInTheDocument();
    expect(screen.queryByText("Structure")).not.toBeInTheDocument();
  });

  it("hides the structure banner when structure passes", async () => {
    await renderPanel(quality());
    expect(
      screen.queryByText(/The artifact itself is malformed/),
    ).not.toBeInTheDocument();
  });

  it("shows an unevaluated group as 'not checked', never as clean", async () => {
    await renderPanel(quality());
    expect(await screen.findByText("not checked")).toBeInTheDocument();
  });

  it("keeps the three verdicts separate rather than rolling them into one", async () => {
    await renderPanel(quality());
    // Steps failed while subject is merely unchecked and coherence was not
    // evaluated at all. A single rolled-up number loses exactly this, which
    // is why the assertion is per-tile rather than per-page.
    const tile = (label: string) =>
      within(screen.getByText(label).parentElement as HTMLElement);
    expect(tile("Steps").getByText("fail")).toBeInTheDocument();
    expect(tile("Subject & title").getByText("inconclusive")).toBeInTheDocument();
    expect(tile("Coherence").getByText("not checked")).toBeInTheDocument();
  });

  it("reports a load failure as a failure to check, not a bad playbook", async () => {
    vi.mocked(api.get).mockRejectedValue(new Error("boom"));
    render(<QualityPanel playbookId="p1" />, { wrapper });
    expect(
      await screen.findByText(/says\s+nothing about the playbook/),
    ).toBeInTheDocument();
  });

  it("names the shadow mode so nobody thinks approval was blocked", async () => {
    await renderPanel(quality());
    expect(await screen.findByText(/does not block approval/)).toBeInTheDocument();
  });
});

describe("QualityCell", () => {
  it("renders a dash when the row carries no assessment", () => {
    render(<QualityCell summary={null} />);
    expect(screen.getByText("—")).toBeInTheDocument();
  });

  it("flags a row whose verdict predates the latest edit", () => {
    render(<QualityCell summary={summary({ matches_current_content: false })} />);
    expect(screen.getByText("out of date")).toBeInTheDocument();
  });

  it("counts only findings that would fail a check", () => {
    render(
      <QualityCell
        summary={summary({ finding_counts: { critical: 2, major: 1, minor: 9, info: 9 } })}
      />,
    );
    expect(screen.getByText("3 blocking")).toBeInTheDocument();
  });

  it("names the unit rather than showing a bare number", () => {
    // It shipped as a bare integer. In a column of red badges "fail 5" reads
    // as a version, an ID, or nothing — the reviewer has to guess the unit.
    render(
      <QualityCell summary={summary({ finding_counts: { critical: 0, major: 1 } })} />,
    );
    expect(screen.queryByText("1")).not.toBeInTheDocument();
    const count = screen.getByText("1 blocking");
    expect(count).toHaveAttribute(
      "title",
      expect.stringContaining("would fail a check"),
    );
  });

  it("shows no count when nothing would fail a check", () => {
    render(
      <QualityCell
        summary={summary({
          state: "inconclusive",
          finding_counts: { critical: 0, major: 0, minor: 4, info: 9 },
        })}
      />,
    );
    expect(screen.queryByText(/blocking/)).not.toBeInTheDocument();
  });
});

describe("findingsForStep", () => {
  it("matches on step_id and orders worst-first", () => {
    const findings = [
      finding({ id: "a", target_kind: "step", target_ref: "s1", severity: "info" }),
      finding({ id: "b", target_kind: "step", target_ref: "s1", severity: "critical" }),
      finding({ id: "c", target_kind: "step", target_ref: "s2", severity: "major" }),
      finding({ id: "d", target_kind: "playbook", target_ref: null }),
    ];
    expect(findingsForStep(findings, "s1").map((f) => f.id)).toEqual(["b", "a"]);
  });

  it("is empty for a step with no id, rather than matching everything", () => {
    const findings = [finding({ target_kind: "step", target_ref: "s1" })];
    expect(findingsForStep(findings, undefined)).toEqual([]);
  });
});
