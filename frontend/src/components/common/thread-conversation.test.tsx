import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { ThreadConversation, sortMessagesByTime } from "./thread-conversation";
import type { EvidenceItem, ThreadSummary } from "@/lib/types";

function message(over: Partial<EvidenceItem> & { id: string }): EvidenceItem {
  return {
    tenant_id: "t",
    source_id: "s",
    source_type: "zoho_desk",
    evidence_type: "ticket",
    title: "A message",
    body_summary: null,
    relevance_state: "operational",
    relevance_score: 0.9,
    created_at_source: "2026-08-01T10:00:00Z",
    ingested_at: "2026-08-04T21:00:00Z",
    ...over,
  } as EvidenceItem;
}

function thread(over: Partial<ThreadSummary> = {}): ThreadSummary {
  return {
    id: "th",
    tenant_id: "t",
    source_id: "s",
    external_thread_id: "zoho_ticket:1",
    title: "Unable to register ps-t4",
    participant_count: 3,
    message_count: 3,
    first_message_at: null,
    last_message_at: null,
    hydration_status: "complete",
    relevance_state: "operational",
    created_at: "2026-08-04T21:00:00Z",
    ...over,
  } as ThreadSummary;
}

describe("sortMessagesByTime", () => {
  it("orders by when the message was sent, not when it was ingested", () => {
    // Hydration writes a whole thread in one pass within the same second,
    // so ingestion order carries no information about the conversation --
    // and re-hydration can reverse it outright.
    const ordered = sortMessagesByTime([
      message({ id: "c", created_at_source: "2026-08-03T09:00:00Z", ingested_at: "2026-08-04T21:00:01Z" }),
      message({ id: "a", created_at_source: "2026-08-01T09:00:00Z", ingested_at: "2026-08-04T21:00:03Z" }),
      message({ id: "b", created_at_source: "2026-08-02T09:00:00Z", ingested_at: "2026-08-04T21:00:02Z" }),
    ]);
    expect(ordered.map((m) => m.id)).toEqual(["a", "b", "c"]);
  });

  it("falls back to ingestion time when the source timestamp is missing", () => {
    const ordered = sortMessagesByTime([
      message({ id: "second", created_at_source: null, ingested_at: "2026-08-04T21:00:05Z" }),
      message({ id: "first", created_at_source: null, ingested_at: "2026-08-04T21:00:01Z" }),
    ]);
    expect(ordered.map((m) => m.id)).toEqual(["first", "second"]);
  });

  it("does not mutate the array it was given", () => {
    // React Query hands back its cached array; sorting in place would
    // reorder the cache under every other consumer.
    const input = [
      message({ id: "b", created_at_source: "2026-08-02T09:00:00Z" }),
      message({ id: "a", created_at_source: "2026-08-01T09:00:00Z" }),
    ];
    sortMessagesByTime(input);
    expect(input.map((m) => m.id)).toEqual(["b", "a"]);
  });
});

describe("ThreadConversation", () => {
  it("shows every message in the thread, not just the open one", () => {
    // The regression this exists for: opening message 14 of a 32-message
    // ticket showed one message with no sign the other 31 existed.
    render(
      <ThreadConversation
        thread={thread()}
        messages={[
          message({ id: "m1", title: "First reply" }),
          message({ id: "m2", title: "Second reply", created_at_source: "2026-08-02T10:00:00Z" }),
          message({ id: "m3", title: "Third reply", created_at_source: "2026-08-03T10:00:00Z" }),
        ]}
        currentEvidenceId="m2"
      />,
    );
    expect(screen.getByText("First reply")).toBeInTheDocument();
    expect(screen.getByText("Second reply")).toBeInTheDocument();
    expect(screen.getByText("Third reply")).toBeInTheDocument();
    expect(screen.getByText(/3 messages/)).toBeInTheDocument();
  });

  it("links the other messages but not the one being viewed", () => {
    // A row that navigates to the page you are already on reads as broken.
    render(
      <ThreadConversation
        thread={thread()}
        messages={[
          message({ id: "m1", title: "Other message" }),
          message({ id: "m2", title: "Open message", created_at_source: "2026-08-02T10:00:00Z" }),
        ]}
        currentEvidenceId="m2"
      />,
    );
    expect(screen.getByRole("link", { name: /Other message/ })).toHaveAttribute(
      "href",
      "/evidence/m1",
    );
    expect(screen.queryByRole("link", { name: /Open message/ })).not.toBeInTheDocument();
    expect(screen.getByText(/viewing/)).toBeInTheDocument();
  });

  it("reports the gap when fewer messages are stored than the thread claims", () => {
    // A thread reporting 32 messages with 6 rows is mid-run or has lost
    // some. Showing only one of those numbers hides which.
    render(
      <ThreadConversation
        thread={thread({ message_count: 32 })}
        messages={[message({ id: "m1" })]}
        currentEvidenceId="m1"
      />,
    );
    expect(screen.getByText(/1 message of 32/)).toBeInTheDocument();
    expect(screen.getByText(/31 not yet processed/)).toBeInTheDocument();
  });

  it("does not claim a gap when the counts agree", () => {
    render(
      <ThreadConversation
        thread={thread({ message_count: 1 })}
        messages={[message({ id: "m1" })]}
        currentEvidenceId="m1"
      />,
    );
    expect(screen.queryByText(/not yet processed/)).not.toBeInTheDocument();
  });

  it("offers hydration only while the thread is not hydrated", () => {
    const onHydrate = vi.fn();
    const { unmount } = render(
      <ThreadConversation
        thread={thread({ hydration_status: "pending" })}
        messages={[]}
        currentEvidenceId="x"
        onHydrate={onHydrate}
      />,
    );
    expect(screen.getByRole("button", { name: /hydrate thread/i })).toBeInTheDocument();
    unmount();

    render(
      <ThreadConversation
        thread={thread({ hydration_status: "complete" })}
        messages={[message({ id: "m1" })]}
        currentEvidenceId="m1"
        onHydrate={onHydrate}
      />,
    );
    expect(screen.queryByRole("button", { name: /hydrate thread/i })).not.toBeInTheDocument();
  });

  it("distinguishes an un-hydrated thread from an empty one", () => {
    // Both show zero messages. Only one of them means the ticket had no
    // conversation -- the other means nobody has fetched it yet, and
    // telling the user "no messages" there is simply wrong.
    const { unmount } = render(
      <ThreadConversation
        thread={thread({ hydration_status: "pending", message_count: 0 })}
        messages={[]}
        currentEvidenceId="x"
      />,
    );
    expect(screen.getByText(/has not been hydrated/i)).toBeInTheDocument();
    unmount();

    render(
      <ThreadConversation
        thread={thread({ hydration_status: "complete", message_count: 0 })}
        messages={[]}
        currentEvidenceId="x"
      />,
    );
    expect(screen.getByText(/no messages/i)).toBeInTheDocument();
  });

  it("survives a thread summary that has not loaded yet", () => {
    // The summary and the message list are separate requests; the list
    // can arrive first.
    render(
      <ThreadConversation
        thread={undefined}
        messages={[message({ id: "m1", title: "Arrived first" })]}
        currentEvidenceId="m1"
      />,
    );
    expect(screen.getByText("Arrived first")).toBeInTheDocument();
  });
});
