"use client";

/**
 * The questions the system needs answered before this playbook is right.
 *
 * The panel's job is to make three distinctions a reviewer must not have to
 * work out for themselves, and each is a design decision rather than styling:
 *
 * 1. **Mandatory and optional look different, and only optional can be
 *    skipped.** The Skip control simply does not exist on a mandatory
 *    question. Rendering one that the server will refuse teaches people the
 *    interface lies to them.
 *
 * 2. **An answer we found is not an answer a person gave.** A question
 *    prefilled from an approved article is shown, labelled, and editable —
 *    never hidden. Folding a retrieval silently into the playbook would let a
 *    wrong match enter as though somebody had approved it, which is the
 *    failure the whole quality plan exists to prevent.
 *
 * 3. **Questions about content that has moved are demoted, loudly.** If the
 *    playbook was edited after the round opened, every question below is about
 *    text nobody can see. That banner outranks the questions themselves.
 *
 * The panel also never claims the loop did more than it did: a round that
 * produced no questions says whether that is because there was nothing to ask
 * or because the generator failed, because those are opposite facts.
 */

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  AlertTriangle,
  BookOpen,
  CheckCircle2,
  CircleHelp,
  History,
  Loader2,
  RefreshCw,
  SearchX,
} from "lucide-react";
import { useState } from "react";

import { StatusBadge } from "@/components/common/status-badge";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Textarea } from "@/components/ui/textarea";
import { api } from "@/lib/api";
import type {
  ClarificationApplyResult,
  ClarificationQuestion,
  PlaybookClarification,
} from "@/lib/types";

// Mirrors MAX_QUESTION_REGENERATIONS in the service. Duplicated rather than
// fetched: it changes about never, and one extra round trip to learn a
// constant is not worth a loading state on a button.
const MAX_REGENERATIONS = 3;

const ANSWER_SOURCE_LABELS: Record<string, string> = {
  human: "answered by you",
  kb: "prefilled from approved documentation",
  context: "already in the playbook",
  carried: "carried forward from an earlier round",
};

const KB_STATUS_NOTES: Record<string, string> = {
  no_results:
    "No approved documentation matched this playbook, so none of these gaps could be answered from the knowledge base.",
  retrieval_failed:
    "The knowledge search failed for this round. These questions may have answers in documentation we could not reach — that is not the same as documentation not having them.",
};

const BLOCKED_REASON_LABELS: Record<string, string> = {
  mandatory_questions_outstanding: "mandatory questions are unanswered",
};

function blockedReasonText(reason: string): string {
  if (reason.startsWith("quality:")) {
    return `the quality gate would stop it (${reason.slice(8).replace(/_/g, " ")})`;
  }
  return BLOCKED_REASON_LABELS[reason] ?? reason.replace(/_/g, " ");
}

export function usePlaybookClarification(playbookId: string) {
  return useQuery({
    queryKey: ["playbook-clarification", playbookId],
    queryFn: () =>
      api.get<PlaybookClarification>(`/playbooks/${playbookId}/clarification`),
  });
}

function QuestionCard({
  question,
  value,
  onChange,
  onSkip,
  disabled,
}: {
  question: ClarificationQuestion;
  value: string;
  onChange: (next: string) => void;
  onSkip: () => void;
  disabled: boolean;
}) {
  const mandatory = question.obligation === "mandatory";
  const prefilled =
    question.answer_source === "kb" || question.answer_source === "context";
  const provenance = question.answer_provenance as
    | { title?: string; section_ref?: string; score?: number }
    | null;

  return (
    <div className="rounded-md border bg-background p-3 space-y-2">
      <div className="flex flex-wrap items-start justify-between gap-2">
        <p className="text-sm font-medium leading-snug">{question.question_text}</p>
        <span className="flex shrink-0 items-center gap-1.5">
          <StatusBadge status={mandatory ? "critical" : "info"} />
          <span className="text-[10px] uppercase tracking-wide text-muted-foreground">
            {mandatory ? "must answer" : "optional"}
          </span>
        </span>
      </div>

      {question.why_it_matters && (
        <p className="text-[11px] text-muted-foreground">{question.why_it_matters}</p>
      )}

      {question.answer_kind === "choice" && question.choices.length > 0 ? (
        <div className="flex flex-wrap gap-1.5">
          {question.choices.map((choice) => (
            <Button
              key={choice}
              type="button"
              size="sm"
              variant={value === choice ? "default" : "outline"}
              disabled={disabled}
              onClick={() => onChange(choice)}
            >
              {choice}
            </Button>
          ))}
        </div>
      ) : (
        <Textarea
          value={value}
          disabled={disabled}
          rows={2}
          placeholder={question.expected_format ?? "Your answer"}
          onChange={(event) => onChange(event.target.value)}
          aria-label={question.question_text}
        />
      )}

      {/* Prefilled is shown and editable, never silently applied: a wrong
          retrieval must not enter the playbook as though a person approved it. */}
      {prefilled && (
        <p className="flex items-start gap-1.5 text-[11px] text-muted-foreground">
          <BookOpen className="mt-0.5 h-3 w-3 shrink-0" aria-hidden />
          <span>
            {ANSWER_SOURCE_LABELS[question.answer_source ?? ""] ?? question.answer_source}
            {provenance?.title ? ` — ${provenance.title}` : ""}
            {provenance?.section_ref ? ` ${provenance.section_ref}` : ""}. Edit it if it
            is wrong; it is not applied until you do.
          </span>
        </p>
      )}

      {question.claim && (
        <p className="text-[11px] italic text-muted-foreground">
          About: “{question.claim.slice(0, 220)}”
        </p>
      )}

      <div className="flex items-center justify-between gap-2">
        <span className="text-[10px] text-muted-foreground">
          {question.gap_kind.replace(/_/g, " ")}
          {question.target_ref ? ` · ${question.target_kind} ${question.target_ref}` : ""}
        </span>
        {/* No Skip on a mandatory question. The server refuses it, and offering
            a control that will be rejected teaches people the UI lies. */}
        {!mandatory && (
          <Button
            type="button"
            size="sm"
            variant="ghost"
            disabled={disabled}
            onClick={onSkip}
          >
            Skip
          </Button>
        )}
      </div>
    </div>
  );
}

export function ClarificationPanel({ playbookId }: { playbookId: string }) {
  const queryClient = useQueryClient();
  const { data, isLoading, isError, error } = usePlaybookClarification(playbookId);
  const [drafts, setDrafts] = useState<Record<string, string>>({});
  const [actionError, setActionError] = useState<string | null>(null);
  const [rewriting, setRewriting] = useState(false);
  const [rewriteNote, setRewriteNote] = useState("");

  const invalidate = () => {
    void queryClient.invalidateQueries({ queryKey: ["playbook-clarification", playbookId] });
    // The apply path writes a new version and re-assesses, so the quality
    // panel beside this one is stale the moment this succeeds.
    void queryClient.invalidateQueries({ queryKey: ["playbook-quality", playbookId] });
    void queryClient.invalidateQueries({ queryKey: ["playbook", playbookId] });
  };

  const openRound = useMutation({
    mutationFn: () =>
      api.post<PlaybookClarification>(`/playbooks/${playbookId}/clarification/rounds`),
    onSuccess: () => {
      setActionError(null);
      invalidate();
    },
    onError: (err: unknown) =>
      setActionError(err instanceof Error ? err.message : "Could not open a round"),
  });

  const saveAnswers = useMutation({
    mutationFn: (answers: { question_id: string; answer_text?: string; skip?: boolean }[]) =>
      api.post<PlaybookClarification>(`/playbooks/${playbookId}/clarification/answers`, {
        answers,
      }),
    onSuccess: () => {
      setActionError(null);
      setDrafts({});
      invalidate();
    },
    onError: (err: unknown) =>
      setActionError(err instanceof Error ? err.message : "Could not save answers"),
  });

  const regenerate = useMutation({
    mutationFn: (guidance: string | null) =>
      api.post<PlaybookClarification>(
        `/playbooks/${playbookId}/clarification/regenerate`,
        { guidance },
      ),
    onSuccess: () => {
      setActionError(null);
      setRewriteNote("");
      setRewriting(false);
      invalidate();
    },
    onError: (err: unknown) =>
      setActionError(
        err instanceof Error ? err.message : "Could not rewrite the questions",
      ),
  });

  const applyRound = useMutation({
    mutationFn: () =>
      api.post<ClarificationApplyResult>(`/playbooks/${playbookId}/clarification/apply`, {
        open_next: true,
      }),
    onSuccess: () => {
      setActionError(null);
      invalidate();
    },
    onError: (err: unknown) =>
      setActionError(err instanceof Error ? err.message : "Could not apply the answers"),
  });

  if (isLoading) {
    return (
      <Card>
        <CardHeader>
          <CardTitle>Clarification</CardTitle>
          <CardDescription className="text-xs">Loading questions…</CardDescription>
        </CardHeader>
      </Card>
    );
  }

  if (isError || !data) {
    return (
      <Card className="border-dashed shadow-none">
        <CardHeader>
          <CardTitle>Clarification</CardTitle>
          <CardDescription className="text-xs">
            Could not load the questions
            {error instanceof Error ? `: ${error.message}` : ""}. This says nothing about
            the playbook — only that the panel could not be read.
          </CardDescription>
        </CardHeader>
      </Card>
    );
  }

  const { round, questions, submission } = data;
  const busy =
    openRound.isPending ||
    saveAnswers.isPending ||
    applyRound.isPending ||
    regenerate.isPending;
  // Bounded server-side. The panel stops offering the button rather than
  // letting it 409 — an affordance that is always there and sometimes refuses
  // is worse than one that visibly runs out.
  const rewritesLeft = round
    ? Math.max(0, MAX_REGENERATIONS - round.regeneration_count)
    : 0;
  const answerable = questions.filter(
    (q) => q.status === "open" || q.status === "answered" || q.status === "resolved_from_kb",
  );

  const submitAnswers = () => {
    const payload = Object.entries(drafts)
      .filter(([, text]) => text.trim().length > 0)
      .map(([question_id, answer_text]) => ({ question_id, answer_text }));
    if (payload.length > 0) saveAnswers.mutate(payload);
  };

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex flex-wrap items-center justify-between gap-2">
          <span className="flex items-center gap-2">
            <CircleHelp className="h-4 w-4" aria-hidden />
            Clarification
          </span>
          {round && (
            <span className="text-[11px] font-normal text-muted-foreground">
              round {round.round_number} of at most {data.max_rounds} · {round.status}
            </span>
          )}
        </CardTitle>
        <CardDescription className="text-xs">
          {round ? (
            <>
              {round.gap_count} gap{round.gap_count === 1 ? "" : "s"} found;{" "}
              {round.resolved_from_kb_count + round.resolved_from_context_count} answered
              without asking you, {data.outstanding_mandatory} still need an answer.
            </>
          ) : (
            <>
              Nothing has been asked about this playbook yet. Opening a round looks for
              gaps, answers what it can from the playbook and the knowledge base, and asks
              you only about what is left.
            </>
          )}
        </CardDescription>
      </CardHeader>

      <CardContent className="space-y-3">
        {/* Loudest first: questions about text that is no longer on screen. */}
        {round && !data.matches_current_content && (
          <div className="flex items-start gap-2 rounded-md border border-amber-500/40 bg-amber-500/10 p-3">
            <History className="mt-0.5 h-4 w-4 shrink-0" aria-hidden />
            <p className="text-xs">
              <span className="font-medium">These questions are out of date.</span> The
              playbook has been edited since this round opened, so they are about an
              earlier version of the text. Abandon the round and open a new one to ask
              about what is there now.
            </p>
          </div>
        )}

        {round?.kb_status && KB_STATUS_NOTES[round.kb_status] && (
          <p className="flex items-start gap-1.5 text-[11px] text-muted-foreground">
            <SearchX className="mt-0.5 h-3 w-3 shrink-0" aria-hidden />
            {KB_STATUS_NOTES[round.kb_status]}
          </p>
        )}

        {/* An empty round has two causes and only one is good news. A playbook
            generated before the quality pipeline existed gives the detector
            nothing to read and produces exactly the same empty result as one we
            examined closely and found clean. The backend distinguishes them on
            the round's `notes`; showing them the same way would tell a reviewer
            their least-checked playbooks are the ones with nothing wrong. */}
        {round?.status === "satisfied" &&
          (round.notes ? (
            <div className="flex items-start gap-2 rounded-md border border-amber-500/40 bg-amber-500/10 p-3">
              <SearchX className="mt-0.5 h-4 w-4 shrink-0" aria-hidden />
              <p className="text-xs">
                <span className="font-medium">No questions, but nothing was checked.</span>{" "}
                {round.notes}
              </p>
            </div>
          ) : (
            <div className="flex items-start gap-2 rounded-md border border-green-500/40 bg-green-500/10 p-3">
              <CheckCircle2 className="mt-0.5 h-4 w-4 shrink-0" aria-hidden />
              <p className="text-xs">
                Nothing left to ask. The last pass found no gap that a person needs to
                fill.
              </p>
            </div>
          ))}

        {round?.status === "exhausted" && (
          <div className="flex items-start gap-2 rounded-md border border-destructive/40 bg-destructive/10 p-3">
            <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" aria-hidden />
            <p className="text-xs">
              <span className="font-medium">The loop stopped after {data.max_rounds} rounds</span>{" "}
              with {round.gap_count} gap{round.gap_count === 1 ? "" : "s"} still open. Asking
              again is unlikely to help — this needs a decision about the playbook itself.
            </p>
          </div>
        )}

        {/* An empty round says which kind of empty it is. "We could not compose
            the questions" and "there was nothing to ask" are opposite facts. */}
        {round && questions.length === 0 && round.generation_error && (
          <p className="text-xs text-muted-foreground">
            No questions were produced: {round.generation_error}
          </p>
        )}

        {round?.generation_error && questions.length > 0 && (
          <p className="text-[11px] text-muted-foreground">
            Note on this round: {round.generation_error}
          </p>
        )}

        {answerable.length > 0 && (
          <div className="space-y-2">
            {answerable.map((question) => (
              <QuestionCard
                key={question.id}
                question={question}
                disabled={busy}
                value={drafts[question.id] ?? question.answer_text ?? ""}
                onChange={(next) =>
                  setDrafts((current) => ({ ...current, [question.id]: next }))
                }
                onSkip={() =>
                  saveAnswers.mutate([{ question_id: question.id, skip: true }])
                }
              />
            ))}
          </div>
        )}

        {actionError && (
          <p className="text-xs text-destructive" role="alert">
            {actionError}
          </p>
        )}

        {rewriting && (
          <div className="space-y-2 rounded-md border bg-muted/30 p-3">
            <p className="text-xs font-medium">What is wrong with these questions?</p>
            <Textarea
              rows={2}
              value={rewriteNote}
              disabled={busy}
              placeholder="e.g. too vague — ask which service and in what order, not whether to restart"
              aria-label="What is wrong with these questions?"
              onChange={(event) => setRewriteNote(event.target.value)}
            />
            <p className="text-[11px] text-muted-foreground">
              Optional, but it is the difference between a rewrite and a re-roll: without
              it the same inputs tend to produce the same questions. Answers you have
              already given are kept. {rewritesLeft} rewrite
              {rewritesLeft === 1 ? "" : "s"} left for this round.
            </p>
            <div className="flex gap-2">
              <Button
                type="button"
                size="sm"
                disabled={busy}
                onClick={() => regenerate.mutate(rewriteNote.trim() || null)}
              >
                {regenerate.isPending && (
                  <Loader2 className="mr-1 h-3 w-3 animate-spin" />
                )}
                Ask again
              </Button>
              <Button
                type="button"
                size="sm"
                variant="ghost"
                disabled={busy}
                onClick={() => setRewriting(false)}
              >
                Cancel
              </Button>
            </div>
          </div>
        )}

        <div className="flex flex-wrap items-center gap-2">
          {!data.has_live_round && (
            <Button type="button" size="sm" disabled={busy} onClick={() => openRound.mutate()}>
              {openRound.isPending && <Loader2 className="mr-1 h-3 w-3 animate-spin" />}
              {round ? "Ask another round" : "Find what is missing"}
            </Button>
          )}
          {answerable.length > 0 && (
            <Button
              type="button"
              size="sm"
              variant="outline"
              disabled={busy || Object.keys(drafts).length === 0}
              onClick={submitAnswers}
            >
              {saveAnswers.isPending && <Loader2 className="mr-1 h-3 w-3 animate-spin" />}
              Save answers
            </Button>
          )}
          {/* Only while something is still unanswered: a rewrite leaves
              answered questions alone, so with none open it would do nothing. */}
          {data.has_live_round &&
            questions.some((q) => q.status === "open") &&
            rewritesLeft > 0 && (
              <Button
                type="button"
                size="sm"
                variant="ghost"
                disabled={busy}
                onClick={() => setRewriting((open) => !open)}
                title="Ask again for the wording of the unanswered questions"
              >
                <RefreshCw className="mr-1 h-3 w-3" aria-hidden />
                Rewrite questions
              </Button>
            )}
          {data.has_live_round && (
            <Button
              type="button"
              size="sm"
              variant="secondary"
              disabled={busy || data.outstanding_mandatory > 0}
              onClick={() => applyRound.mutate()}
              title={
                data.outstanding_mandatory > 0
                  ? `${data.outstanding_mandatory} mandatory question(s) still unanswered`
                  : "Rewrite the playbook using these answers"
              }
            >
              {applyRound.isPending && <Loader2 className="mr-1 h-3 w-3 animate-spin" />}
              Update the playbook
            </Button>
          )}
        </div>

        {/* Reports; never acts. The transition stays a separate, explicit human
            action — the loop does not move a playbook forward on its own. */}
        <div className="rounded-md border bg-muted/30 p-3">
          {submission.ready ? (
            <p className="flex items-center gap-1.5 text-xs">
              <CheckCircle2 className="h-3.5 w-3.5" aria-hidden />
              <span className="font-medium">Ready to submit.</span> Nothing is outstanding
              — use the lifecycle control to send it forward.
            </p>
          ) : (
            <p className="text-xs text-muted-foreground">
              <span className="font-medium">Not ready to submit</span> because{" "}
              {submission.blocked_reasons.map(blockedReasonText).join("; ") ||
                "the quality gate has not cleared it"}
              .
            </p>
          )}
        </div>
      </CardContent>
    </Card>
  );
}
