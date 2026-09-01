import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import {
  fireEvent,
  render,
  screen,
  waitFor,
  waitForElementToBeRemoved,
} from "@testing-library/react";
import type { ReactNode } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { GuidedFixModal } from "./guided-fix-modal";
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
    question_count: 2,
    mandatory_count: 1,
    resolved_from_kb_count: 0,
    resolved_from_context_count: 0,
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
    answer_kind: "choice",
    choices: ["Portal Ingest Service", "Process Studio Sync"],
    expected_format: null,
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
    questions: [
      question(),
      question({
        id: "q2",
        gap_key: "k2",
        question_text: "Should optional logs be purged?",
        obligation: "optional",
        answer_kind: "text",
        choices: [],
      }),
    ],
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

async function renderModal(payload: PlaybookClarification, onOpenChange = vi.fn()) {
  vi.mocked(api.get).mockResolvedValue(payload);
  render(<GuidedFixModal playbookId="p1" open={true} onOpenChange={onOpenChange} />, { wrapper });
  await waitForElementToBeRemoved(() => screen.queryByText("Loading clarification questions…"));
}

afterEach(() => {
  vi.clearAllMocks();
});

describe("GuidedFixModal", () => {
  it("renders step 1 question and choice options", async () => {
    await renderModal(state());

    expect(screen.getByText("Fix Playbook with Guided Q&A")).toBeInTheDocument();
    expect(screen.getByText("Which service must be restarted, and in what order?")).toBeInTheDocument();
    expect(screen.getByText("Portal Ingest Service")).toBeInTheDocument();
    expect(screen.getByText("Process Studio Sync")).toBeInTheDocument();
    expect(screen.getByText("Required Decision")).toBeInTheDocument();
  });

  it("selects a choice card and auto-saves", async () => {
    await renderModal(state());
    vi.mocked(api.post).mockResolvedValue(state());

    fireEvent.click(screen.getByText("Portal Ingest Service"));

    await waitFor(() =>
      expect(api.post).toHaveBeenCalledWith("/playbooks/p1/clarification/answers", {
        answers: [{ question_id: "q1", answer_text: "Portal Ingest Service" }],
      }),
    );
  });

  it("navigates to next question and allows skipping optional question", async () => {
    await renderModal(state());
    vi.mocked(api.post).mockResolvedValue(state());

    // Advance to step 2
    fireEvent.click(screen.getByRole("button", { name: /Next Question/ }));

    expect(screen.getByText("Should optional logs be purged?")).toBeInTheDocument();
    expect(screen.getByText("Optional")).toBeInTheDocument();

    const skipBtn = screen.getByRole("button", { name: "Skip" });
    expect(skipBtn).toBeInTheDocument();
    fireEvent.click(skipBtn);

    await waitFor(() =>
      expect(api.post).toHaveBeenCalledWith("/playbooks/p1/clarification/answers", {
        answers: [{ question_id: "q2", skip: true }],
      }),
    );
  });

  it("toggles custom response textarea for choice questions", async () => {
    await renderModal(state());

    const customToggle = screen.getByRole("button", {
      name: /Write custom response \/ add clarification notes/,
    });
    expect(customToggle).toBeInTheDocument();
    fireEvent.click(customToggle);

    expect(
      screen.getByPlaceholderText("Type your exact instructions or resolution here…"),
    ).toBeInTheDocument();
  });
});
