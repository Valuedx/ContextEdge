import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import {
  fireEvent,
  render,
  screen,
  waitFor,
  waitForElementToBeRemoved,
  within,
} from "@testing-library/react";
import type { ReactNode } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ClarificationPanel } from "./clarification-panel";
import type {
  ClarificationQuestion,
  ClarificationRound,
  PlaybookClarification,
} from "@/lib/types";

vi.mock("@/lib/api", () => ({
  api: { get: vi.fn(), post: vi.fn() },
}));

const { api } = await import("@/lib/api");

function round(over: Partial<ClarificationRound> = {}): ClarificationRound {
  return {
    id: "r1",
    round_number: 1,
    status: "open",
    content_hash: "a".repeat(64),
    assessment_id: "as1",
    gap_count: 5,
    question_count: 3,
    mandatory_count: 1,
    resolved_from_kb_count: 1,
    resolved_from_context_count: 1,
    kb_status: "ok",
    regeneration_count: 0,
    prompt_name: "clarification_questions",
    prompt_version: "v1",
    generation_error: null,
    applied_version_id: null,
    opened_at: "2026-09-01T00:00:00Z",
    closed_at: null,
    notes: null,
    ...over,
  };
}

function question(over: Partial<ClarificationQuestion> = {}): ClarificationQuestion {
  return {
    id: "q1",
    gap_key: "k1",
    gap_kind: "missing_required_action",
    gap_origin: "finding",
    target_kind: "playbook",
    target_ref: null,
    claim: "Restart the ingest service after the patch",
    severity: "major",
    question_text: "Which service must be restarted, and in what order?",
    why_it_matters: "The procedure cannot be followed without the service name.",
    obligation: "mandatory",
    answer_kind: "text",
    choices: [],
    expected_format: "A service name",
    status: "open",
    answer_text: null,
    answer_source: null,
    answer_provenance: null,
    answered_at: null,
    ...over,
  };
}

function state(over: Partial<PlaybookClarification> = {}): PlaybookClarification {
  return {
    playbook_id: "p1",
    content_hash: "a".repeat(64),
    round: round(),
    questions: [question()],
    matches_current_content: true,
    has_live_round: true,
    outstanding_mandatory: 1,
    max_rounds: 5,
    submission: {
      ready: false,
      blocked_reasons: ["mandatory_questions_outstanding"],
      outstanding_mandatory: 1,
      open_round_id: "r1",
      open_round_status: "open",
      quality: { ready: false, blocked_reason: "assessment_inconclusive" },
    },
    ...over,
  };
}

function wrapper({ children }: { children: ReactNode }) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return <QueryClientProvider client={client}>{children}</QueryClientProvider>;
}

async function renderPanel(payload: PlaybookClarification) {
  vi.mocked(api.get).mockResolvedValue(payload);
  render(<ClarificationPanel playbookId="p1" />, { wrapper });
  await waitForElementToBeRemoved(() => screen.queryByText("Loading questions…"));
}

afterEach(() => {
  vi.clearAllMocks();
});

describe("ClarificationPanel", () => {
  it("shows the question, why it matters, and that it must be answered", async () => {
    await renderPanel(state());

    expect(
      screen.getByText("Which service must be restarted, and in what order?"),
    ).toBeInTheDocument();
    expect(
      screen.getByText("The procedure cannot be followed without the service name."),
    ).toBeInTheDocument();
    expect(screen.getByText("must answer")).toBeInTheDocument();
  });

  it("offers no Skip on a mandatory question", async () => {
    // The server refuses the skip. Rendering a control that will be rejected
    // teaches people the interface lies to them.
    await renderPanel(state());
    expect(screen.queryByRole("button", { name: "Skip" })).not.toBeInTheDocument();
  });

  it("offers Skip on an optional question", async () => {
    await renderPanel(
      state({
        questions: [question({ obligation: "optional" })],
        outstanding_mandatory: 0,
      }),
    );
    expect(screen.getByRole("button", { name: "Skip" })).toBeInTheDocument();
  });

  it("labels a KB-prefilled answer as prefilled and leaves it editable", async () => {
    // Folding a retrieval in silently would let a wrong match enter the
    // playbook as though somebody had approved it.
    await renderPanel(
      state({
        questions: [
          question({
            obligation: "optional",
            status: "resolved_from_kb",
            answer_text: "Restart the ingest service, then the scheduler.",
            answer_source: "kb",
            answer_provenance: { title: "Patch runbook", section_ref: "§4.2", score: 0.71 },
          }),
        ],
        outstanding_mandatory: 0,
      }),
    );

    expect(
      screen.getByText(/prefilled from approved documentation/),
    ).toBeInTheDocument();
    expect(screen.getByText(/Patch runbook/)).toBeInTheDocument();
    const field = screen.getByLabelText(
      "Which service must be restarted, and in what order?",
    ) as HTMLTextAreaElement;
    expect(field.value).toBe("Restart the ingest service, then the scheduler.");
    expect(field).not.toBeDisabled();
  });

  it("says the questions are out of date when the playbook has moved on", async () => {
    await renderPanel(state({ matches_current_content: false }));
    expect(screen.getByText(/These questions are out of date/)).toBeInTheDocument();
  });

  it("blocks Update while a mandatory question is unanswered", async () => {
    await renderPanel(state());
    expect(
      screen.getByRole("button", { name: /Update the playbook/ }),
    ).toBeDisabled();
  });

  it("allows Update once nothing mandatory is outstanding", async () => {
    await renderPanel(
      state({
        outstanding_mandatory: 0,
        questions: [
          question({ status: "answered", answer_text: "Ingest service", answer_source: "human" }),
        ],
      }),
    );
    expect(
      screen.getByRole("button", { name: /Update the playbook/ }),
    ).not.toBeDisabled();
  });

  it("distinguishes a round that had nothing to ask from one that failed", async () => {
    await renderPanel(
      state({
        round: round({ status: "satisfied", gap_count: 0, question_count: 0 }),
        questions: [],
        has_live_round: false,
        outstanding_mandatory: 0,
      }),
    );
    expect(screen.getByText(/Nothing left to ask/)).toBeInTheDocument();

    vi.clearAllMocks();
    await renderPanel(
      state({
        round: round({
          question_count: 0,
          generation_error: "generation error: RuntimeError: boom",
        }),
        questions: [],
        outstanding_mandatory: 0,
      }),
    );
    expect(screen.getByText(/No questions were produced/)).toBeInTheDocument();
  });

  it("does not report an unassessed old playbook as having nothing to ask", async () => {
    // A playbook generated before the quality pipeline existed gives the gap
    // detector nothing to read, and produces exactly the same empty round as
    // one examined closely and found clean. Showing them identically would
    // tell a reviewer their least-checked playbooks are the healthiest.
    await renderPanel(
      state({
        round: round({
          status: "satisfied",
          gap_count: 0,
          question_count: 0,
          assessment_id: null,
          notes:
            "Nothing to derive questions from: this playbook has no quality assessment of its current content and no stored quality contract.",
        }),
        questions: [],
        has_live_round: false,
        outstanding_mandatory: 0,
      }),
    );
    expect(screen.getByText(/nothing was checked/i)).toBeInTheDocument();
    expect(screen.queryByText(/Nothing left to ask/)).not.toBeInTheDocument();
  });

  it("says a retrieval failure is not an absence of documentation", async () => {
    await renderPanel(state({ round: round({ kb_status: "retrieval_failed" }) }));
    expect(screen.getByText(/knowledge search failed/)).toBeInTheDocument();
    expect(
      screen.getByText(/not the same as documentation not having them/),
    ).toBeInTheDocument();
  });

  it("reports readiness without offering to submit", async () => {
    // The loop reports; a person presses Submit through the lifecycle control.
    await renderPanel(
      state({
        round: round({ status: "satisfied" }),
        questions: [],
        has_live_round: false,
        outstanding_mandatory: 0,
        submission: {
          ready: true,
          blocked_reasons: [],
          outstanding_mandatory: 0,
          open_round_id: null,
          open_round_status: null,
          quality: { ready: true, blocked_reason: null },
        },
      }),
    );
    expect(screen.getByText(/Ready to submit/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /^Submit/ })).not.toBeInTheDocument();
  });

  it("explains why it is not ready in words rather than slugs", async () => {
    await renderPanel(
      state({
        submission: {
          ready: false,
          blocked_reasons: ["mandatory_questions_outstanding", "quality:assessment_fail"],
          outstanding_mandatory: 1,
          open_round_id: "r1",
          open_round_status: "open",
          quality: { ready: false, blocked_reason: "assessment_fail" },
        },
      }),
    );
    const banner = screen.getByText(/Not ready to submit/).parentElement as HTMLElement;
    expect(within(banner).getByText(/mandatory questions are unanswered/)).toBeInTheDocument();
    expect(within(banner).getByText(/quality gate would stop it/)).toBeInTheDocument();
  });

  it("says the loop has stopped when the round limit is reached", async () => {
    await renderPanel(
      state({
        round: round({ status: "exhausted", round_number: 6, gap_count: 2 }),
        questions: [],
        has_live_round: false,
        outstanding_mandatory: 0,
      }),
    );
    expect(screen.getByText(/The loop stopped after 5 rounds/)).toBeInTheDocument();
  });

  it("sends typed answers to the answers endpoint", async () => {
    // fireEvent rather than user-event: the project does not depend on
    // @testing-library/user-event, and a UI test is not worth a new dependency.
    await renderPanel(state());
    vi.mocked(api.post).mockResolvedValue(state());

    fireEvent.change(
      screen.getByLabelText("Which service must be restarted, and in what order?"),
      { target: { value: "Ingest service" } },
    );
    fireEvent.click(screen.getByRole("button", { name: /Save answers/ }));

    await waitFor(() =>
      expect(api.post).toHaveBeenCalledWith("/playbooks/p1/clarification/answers", {
        answers: [{ question_id: "q1", answer_text: "Ingest service" }],
      }),
    );
  });

  it("does not offer Save until something has been typed", async () => {
    await renderPanel(state());
    expect(screen.getByRole("button", { name: /Save answers/ })).toBeDisabled();
  });

  it("offers a rewrite while questions are still unanswered", async () => {
    await renderPanel(state());
    expect(
      screen.getByRole("button", { name: /Rewrite questions/ }),
    ).toBeInTheDocument();
  });

  it("stops offering a rewrite once the round has used its allowance", async () => {
    // Bounded server-side. An affordance that is always there and sometimes
    // refuses is worse than one that visibly runs out.
    await renderPanel(state({ round: round({ regeneration_count: 3 }) }));
    expect(
      screen.queryByRole("button", { name: /Rewrite questions/ }),
    ).not.toBeInTheDocument();
  });

  it("does not offer a rewrite when nothing is open to rewrite", async () => {
    await renderPanel(
      state({
        outstanding_mandatory: 0,
        questions: [
          question({ status: "answered", answer_text: "Ingest", answer_source: "human" }),
        ],
      }),
    );
    expect(
      screen.queryByRole("button", { name: /Rewrite questions/ }),
    ).not.toBeInTheDocument();
  });

  it("sends the reviewer's note with the rewrite", async () => {
    await renderPanel(state());
    vi.mocked(api.post).mockResolvedValue(state());

    fireEvent.click(screen.getByRole("button", { name: /Rewrite questions/ }));
    fireEvent.change(
      screen.getByLabelText("What is wrong with these questions?"),
      { target: { value: "Too vague — ask about ordering." } },
    );
    fireEvent.click(screen.getByRole("button", { name: /Ask again/ }));

    await waitFor(() =>
      expect(api.post).toHaveBeenCalledWith("/playbooks/p1/clarification/regenerate", {
        guidance: "Too vague — ask about ordering.",
      }),
    );
  });

  it("sends null rather than an empty note when the reviewer types nothing", async () => {
    await renderPanel(state());
    vi.mocked(api.post).mockResolvedValue(state());

    fireEvent.click(screen.getByRole("button", { name: /Rewrite questions/ }));
    fireEvent.click(screen.getByRole("button", { name: /Ask again/ }));

    await waitFor(() =>
      expect(api.post).toHaveBeenCalledWith("/playbooks/p1/clarification/regenerate", {
        guidance: null,
      }),
    );
  });

  it("says how many rewrites are left", async () => {
    await renderPanel(state({ round: round({ regeneration_count: 2 }) }));
    fireEvent.click(screen.getByRole("button", { name: /Rewrite questions/ }));
    expect(screen.getByText(/1 rewrite left for this round/)).toBeInTheDocument();
  });

  it("offers to open a round when none is live and none has ever run", async () => {
    await renderPanel(
      state({ round: null, questions: [], has_live_round: false, outstanding_mandatory: 0 }),
    );
    expect(
      screen.getByRole("button", { name: /Find what is missing/ }),
    ).toBeInTheDocument();
  });
});
