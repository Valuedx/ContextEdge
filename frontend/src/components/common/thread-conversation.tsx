"use client";

/**
 * The conversation an evidence item belongs to.
 *
 * Every hydrated message is its own evidence row, so opening one showed a
 * single message with no indication that it was message 14 of a 32-message
 * thread — and no way to reach the other 31. The detail page already
 * fetched the thread and discarded it: both the query and the hydrate
 * mutation were dead code, referenced nowhere in the page's 438 lines.
 *
 * Ordering is by `created_at_source` — when the message was actually sent —
 * not by ingestion time. Hydration inserts a thread's messages in one pass
 * within the same second, so ingestion order carries no information about
 * the conversation, and re-hydration can reverse it outright.
 */

import Link from "next/link";
import { Loader2, MessageSquare, RefreshCw, Users } from "lucide-react";

import { StatusBadge } from "@/components/common/status-badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { cn } from "@/lib/utils";
import type { EvidenceItem, ThreadSummary } from "@/lib/types";

export interface ThreadConversationProps {
  thread: ThreadSummary | undefined;
  messages: EvidenceItem[];
  /** The evidence item currently open, highlighted in place. */
  currentEvidenceId: string;
  isLoading?: boolean;
  onHydrate?: () => void;
  isHydrating?: boolean;
}

/** Sent time first; ingestion time is a tiebreaker, not an ordering. */
export function sortMessagesByTime(messages: EvidenceItem[]): EvidenceItem[] {
  return [...messages].sort((a, b) => {
    const at = a.created_at_source ?? a.ingested_at;
    const bt = b.created_at_source ?? b.ingested_at;
    return new Date(at).getTime() - new Date(bt).getTime();
  });
}

function timeLabel(message: EvidenceItem): string {
  const raw = message.created_at_source ?? message.ingested_at;
  if (!raw) return "";
  const at = new Date(raw);
  return Number.isNaN(at.getTime()) ? "" : at.toLocaleString();
}

export function ThreadConversation({
  thread,
  messages,
  currentEvidenceId,
  isLoading = false,
  onHydrate,
  isHydrating = false,
}: ThreadConversationProps) {
  const ordered = sortMessagesByTime(messages);
  const hydrated = thread?.hydration_status === "complete";

  // The thread's own count comes from the connector; the list is what
  // survived normalization. Showing both when they disagree is the
  // point — a thread reporting 32 messages with 6 rows is mid-run or
  // has lost some, and collapsing that to one number hides it.
  const claimed = thread?.message_count ?? 0;
  const shown = ordered.length;

  return (
    <Card>
      <CardHeader className="flex flex-row items-center justify-between gap-3 border-b pb-3">
        <CardTitle className="flex items-center gap-2 text-base font-bold text-foreground">
          <MessageSquare className="h-4 w-4 text-primary" />
          Conversation
          {shown > 0 && (
            <span className="text-xs font-normal text-muted-foreground">
              {shown} message{shown === 1 ? "" : "s"}
              {claimed > shown && ` of ${claimed} · ${claimed - shown} not yet processed`}
            </span>
          )}
        </CardTitle>

        <div className="flex items-center gap-3">
          {typeof thread?.participant_count === "number" && thread.participant_count > 0 && (
            <span className="flex items-center gap-1 text-xs text-muted-foreground">
              <Users className="h-3.5 w-3.5" />
              {thread.participant_count}
            </span>
          )}
          {!hydrated && onHydrate && (
            <Button
              size="sm"
              variant="outline"
              onClick={onHydrate}
              disabled={isHydrating}
            >
              {isHydrating ? (
                <Loader2 className="h-3.5 w-3.5 animate-spin" />
              ) : (
                <RefreshCw className="h-3.5 w-3.5" />
              )}
              <span className="ml-1.5">Hydrate thread</span>
            </Button>
          )}
        </div>
      </CardHeader>

      <CardContent className="pt-4">
        {isLoading ? (
          <div className="flex items-center gap-2 text-sm text-muted-foreground">
            <Loader2 className="h-4 w-4 animate-spin" />
            Loading conversation…
          </div>
        ) : ordered.length === 0 ? (
          <p className="text-sm text-muted-foreground">
            {hydrated
              ? "This thread has no messages."
              : "This thread has not been hydrated yet, so its messages have not been fetched."}
          </p>
        ) : (
          <ol className="space-y-1.5">
            {ordered.map((message, index) => {
              const isCurrent = message.id === currentEvidenceId;
              const body = (
                <div
                  className={cn(
                    "flex items-start gap-3 rounded-lg border p-3 transition-colors",
                    isCurrent
                      ? "border-sky-300 bg-sky-50 dark:border-sky-500/50 dark:bg-sky-500/10"
                      : "bg-card hover:bg-muted/60",
                  )}
                >
                  <span className="mt-0.5 w-6 shrink-0 text-right font-mono text-xs text-muted-foreground">
                    {index + 1}
                  </span>
                  <div className="min-w-0 flex-1">
                    <p
                      className={cn(
                        "text-sm font-medium",
                        isCurrent ? "font-semibold text-primary" : "text-foreground",
                      )}
                    >
                      {message.title || "Untitled message"}
                    </p>
                    {(message.body_text || message.body_summary) && (
                      <p className="mt-1.5 line-clamp-4 rounded-md border bg-muted p-2.5 font-sans text-xs text-muted-foreground whitespace-pre-wrap">
                        {message.body_text || message.body_summary}
                      </p>
                    )}
                    <div className="mt-2 flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
                      <span>{timeLabel(message)}</span>
                      <StatusBadge status={message.relevance_state} />
                      {isCurrent && (
                        <span className="font-semibold text-primary">· currently viewing</span>
                      )}
                    </div>
                  </div>
                </div>
              );

              // The open message is not a link to itself — a row that
              // navigates nowhere reads as broken.
              return (
                <li key={message.id}>
                  {isCurrent ? (
                    body
                  ) : (
                    <Link href={`/evidence/${message.id}`} className="block">
                      {body}
                    </Link>
                  )}
                </li>
              );
            })}
          </ol>
        )}
      </CardContent>
    </Card>
  );
}
