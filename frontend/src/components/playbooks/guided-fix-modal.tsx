"use client";

import { useMutation, useQueryClient } from "@tanstack/react-query";
import {
  AlertTriangle,
  ArrowLeft,
  ArrowRight,
  BookOpen,
  Check,
  CheckCircle2,
  ChevronDown,
  ChevronUp,
  HelpCircle,
  Info,
  Loader2,
  MessageSquarePlus,
  Pencil,
  RefreshCw,
  Sparkles,
  X,
} from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";

import { usePlaybookClarification } from "@/components/playbooks/clarification-panel";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { api } from "@/lib/api";
import type {
  ClarificationApplyResult,
  ClarificationQuestion,
  PlaybookClarification,
} from "@/lib/types";
import { cn } from "@/lib/utils";

const MAX_REGENERATIONS = 3;

const ANSWER_SOURCE_LABELS: Record<string, string> = {
  human: "Answered by reviewer",
  kb: "Prefilled from approved documentation",
  context: "Extracted from playbook context",
  carried: "Carried from previous round",
};

interface GuidedFixModalProps {
  playbookId: string;
  open: boolean;
  onOpenChange: (open: boolean) => void;
}

export function GuidedFixModal({
  playbookId,
  open,
  onOpenChange,
}: GuidedFixModalProps) {
  const queryClient = useQueryClient();
  const { data, isLoading } = usePlaybookClarification(playbookId);
  const [currentStep, setCurrentStep] = useState(0);
  const [drafts, setDrafts] = useState<Record<string, string>>({});
  const [customActive, setCustomActive] = useState<Record<string, boolean>>({});
  const [showContext, setShowContext] = useState<Record<string, boolean>>({});
  const [rewriting, setRewriting] = useState(false);
  const [rewriteNote, setRewriteNote] = useState("");
  const [actionError, setActionError] = useState<string | null>(null);

  const invalidate = () => {
    void queryClient.invalidateQueries({ queryKey: ["playbook-clarification", playbookId] });
    void queryClient.invalidateQueries({ queryKey: ["playbook-quality", playbookId] });
    void queryClient.invalidateQueries({ queryKey: ["playbook", playbookId] });
    void queryClient.invalidateQueries({ queryKey: ["playbook-versions", playbookId] });
  };

  const openRound = useMutation({
    mutationFn: () =>
      api.post<PlaybookClarification>(`/playbooks/${playbookId}/clarification/rounds`),
    onSuccess: () => {
      setActionError(null);
      invalidate();
      setCurrentStep(0);
    },
    onError: (err: unknown) =>
      setActionError(err instanceof Error ? err.message : "Could not open a clarification round"),
  });

  const saveAnswers = useMutation({
    mutationFn: (answers: { question_id: string; answer_text?: string; skip?: boolean }[]) =>
      api.post<PlaybookClarification>(`/playbooks/${playbookId}/clarification/answers`, {
        answers,
      }),
    onSuccess: () => {
      setActionError(null);
      invalidate();
    },
    onError: (err: unknown) =>
      setActionError(err instanceof Error ? err.message : "Could not save answers"),
  });

  const regenerate = useMutation({
    mutationFn: (guidance: string | null) =>
      api.post<PlaybookClarification>(`/playbooks/${playbookId}/clarification/regenerate`, {
        guidance,
      }),
    onSuccess: () => {
      setActionError(null);
      setRewriteNote("");
      setRewriting(false);
      invalidate();
      toast.success("Questions rephrased with your guidance");
    },
    onError: (err: unknown) =>
      setActionError(err instanceof Error ? err.message : "Could not rewrite questions"),
  });

  const applyRound = useMutation({
    mutationFn: () =>
      api.post<ClarificationApplyResult>(`/playbooks/${playbookId}/clarification/apply`, {
        open_next: true,
      }),
    onSuccess: (result) => {
      setActionError(null);
      invalidate();
      toast.success(
        `Playbook draft updated (v${result.new_semantic_version}) with your clarification fixes!`,
      );
      onOpenChange(false);
    },
    onError: (err: unknown) =>
      setActionError(err instanceof Error ? err.message : "Could not update playbook"),
  });

  const round = data?.round;
  const questions = data?.questions ?? [];
  const answerable = questions.filter(
    (q) => q.status === "open" || q.status === "answered" || q.status === "resolved_from_kb",
  );

  const busy =
    openRound.isPending ||
    saveAnswers.isPending ||
    applyRound.isPending ||
    regenerate.isPending;

  const currentQ: ClarificationQuestion | undefined = answerable[currentStep];
  const totalQuestions = answerable.length;

  const getEffectiveValue = (q: ClarificationQuestion): string => {
    if (drafts[q.id] !== undefined) return drafts[q.id];
    return q.answer_text ?? "";
  };

  const handleSelectChoice = (questionId: string, choice: string) => {
    setDrafts((prev) => ({ ...prev, [questionId]: choice }));
    // Auto-save this answer
    saveAnswers.mutate([{ question_id: questionId, answer_text: choice }]);
  };

  const handleCustomTextChange = (questionId: string, text: string) => {
    setDrafts((prev) => ({ ...prev, [questionId]: text }));
  };

  const handleBlurSave = (questionId: string) => {
    const val = drafts[questionId];
    if (val !== undefined && val.trim().length > 0) {
      saveAnswers.mutate([{ question_id: questionId, answer_text: val.trim() }]);
    }
  };

  const handleSkipQuestion = (questionId: string) => {
    saveAnswers.mutate([{ question_id: questionId, skip: true }]);
    if (currentStep < totalQuestions - 1) {
      setCurrentStep((prev) => prev + 1);
    }
  };

  const handleApplyAndFinish = async () => {
    // 1. Save any pending unsaved drafts
    const pending = Object.entries(drafts)
      .filter(([, text]) => text.trim().length > 0)
      .map(([question_id, answer_text]) => ({ question_id, answer_text }));

    if (pending.length > 0) {
      try {
        await saveAnswers.mutateAsync(pending);
      } catch {
        return;
      }
    }

    // 2. Apply round to playbook
    applyRound.mutate();
  };

  const rewritesLeft = round ? Math.max(0, MAX_REGENERATIONS - round.regeneration_count) : 0;

  // Calculate answered count
  const answeredCount = answerable.filter((q) => {
    const val = getEffectiveValue(q);
    return Boolean(val && val.trim().length > 0) || q.status === "answered";
  }).length;

  const mandatoryOutstanding = answerable.filter((q) => {
    const isMandatory = q.obligation === "mandatory";
    const val = getEffectiveValue(q);
    const hasAnswer = Boolean(val && val.trim().length > 0) || q.status === "answered";
    return isMandatory && !hasAnswer;
  }).length;

  const canApply = mandatoryOutstanding === 0 && (data?.has_live_round ?? false);

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-2xl max-h-[90vh] flex flex-col p-0 gap-0 overflow-hidden sm:rounded-xl">
        {/* Header */}
        <DialogHeader className="p-5 pb-4 border-b bg-gradient-to-r from-primary/10 via-primary/5 to-background">
          <div className="flex items-center justify-between gap-3">
            <div className="flex items-center gap-2.5">
              <div className="flex h-9 w-9 items-center justify-center rounded-lg bg-primary/15 text-primary">
                <Sparkles className="h-5 w-5" />
              </div>
              <div>
                <DialogTitle className="text-base font-semibold">
                  Fix Playbook with Guided Q&amp;A
                </DialogTitle>
                <DialogDescription className="text-xs">
                  {round ? (
                    <>
                      Round {round.round_number} of {data?.max_rounds} · {answeredCount} of{" "}
                      {totalQuestions} questions answered
                    </>
                  ) : (
                    "Turn quality gaps into clear, fast decisions."
                  )}
                </DialogDescription>
              </div>
            </div>

            {totalQuestions > 0 && (
              <div className="flex items-center gap-2">
                <span className="text-xs font-semibold text-muted-foreground">
                  {currentStep + 1} / {totalQuestions}
                </span>
              </div>
            )}
          </div>

          {/* Progress Bar */}
          {totalQuestions > 0 && (
            <div className="mt-3 h-1.5 w-full overflow-hidden rounded-full bg-muted">
              <div
                className="h-full bg-primary transition-all duration-300 ease-out rounded-full"
                style={{
                  width: `${((currentStep + 1) / totalQuestions) * 100}%`,
                }}
              />
            </div>
          )}
        </DialogHeader>

        {/* Content Body */}
        <div className="flex-1 overflow-y-auto p-5 space-y-4">
          {isLoading ? (
            <div className="flex flex-col items-center justify-center py-12 text-center gap-2">
              <Loader2 className="h-6 w-6 animate-spin text-primary" />
              <p className="text-xs text-muted-foreground">Loading clarification questions…</p>
            </div>
          ) : !round && !data?.has_live_round ? (
            <div className="py-6 text-center space-y-4 max-w-md mx-auto">
              <div className="mx-auto flex h-12 w-12 items-center justify-center rounded-full bg-primary/10 text-primary">
                <MessageSquarePlus className="h-6 w-6" />
              </div>
              <div className="space-y-1.5">
                <h3 className="text-sm font-semibold">Start a Guided Clarification Round</h3>
                <p className="text-xs text-muted-foreground leading-relaxed">
                  We scan quality findings, pull answers from the knowledge base where possible,
                  and ask you only about what is still missing.
                </p>
              </div>
              <Button
                type="button"
                className="w-full"
                disabled={busy}
                onClick={() => openRound.mutate()}
              >
                {openRound.isPending ? (
                  <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                ) : (
                  <ArrowRight className="mr-2 h-4 w-4" />
                )}
                Start Guided Fix
              </Button>
            </div>
          ) : totalQuestions === 0 ? (
            <div className="py-8 text-center space-y-3">
              <div className="mx-auto flex h-12 w-12 items-center justify-center rounded-full bg-emerald-500/10 text-emerald-600 dark:text-emerald-400">
                <CheckCircle2 className="h-6 w-6" />
              </div>
              <h3 className="text-sm font-semibold">All Questions Resolved</h3>
              <p className="text-xs text-muted-foreground max-w-sm mx-auto">
                Nothing left to clarify for this round. You can close this modal and review your
                playbook.
              </p>
            </div>
          ) : currentQ ? (
            <div className="space-y-4">
              {/* Question Top Card */}
              <div className="rounded-xl border bg-card p-4 space-y-3 shadow-xs">
                <div className="flex flex-wrap items-start justify-between gap-2">
                  <div className="flex items-center gap-2">
                    <span className="flex h-5 w-5 shrink-0 items-center justify-center rounded-full bg-primary/10 text-primary text-[11px] font-bold">
                      {currentStep + 1}
                    </span>
                    <span className="text-[11px] font-semibold uppercase tracking-wider text-muted-foreground">
                      {currentQ.gap_kind.replace(/_/g, " ")}
                    </span>
                  </div>

                  {currentQ.obligation === "mandatory" ? (
                    <Badge variant="destructive" className="gap-1 text-[10px] font-semibold uppercase tracking-wider">
                      <AlertTriangle className="h-3 w-3" />
                      Required Decision
                    </Badge>
                  ) : (
                    <Badge variant="secondary" className="text-[10px] font-semibold uppercase tracking-wider">
                      Optional
                    </Badge>
                  )}
                </div>

                <h3 className="text-base font-semibold leading-snug text-foreground">
                  {currentQ.question_text}
                </h3>

                {currentQ.why_it_matters && (
                  <div className="flex items-start gap-2 rounded-lg bg-muted/60 p-3 text-xs text-muted-foreground">
                    <Info className="mt-0.5 h-4 w-4 shrink-0 text-primary" />
                    <p className="leading-relaxed">
                      <strong className="text-foreground font-medium">Why this matters: </strong>
                      {currentQ.why_it_matters}
                    </p>
                  </div>
                )}

                {/* Pre-fill Provenance */}
                {(currentQ.answer_source === "kb" || currentQ.answer_source === "context") && (
                  <div className="flex items-start gap-2 rounded-lg border border-primary/20 bg-primary/5 p-3 text-xs text-primary">
                    <BookOpen className="mt-0.5 h-4 w-4 shrink-0" />
                    <div>
                      <p className="font-medium">
                        {ANSWER_SOURCE_LABELS[currentQ.answer_source ?? ""] ?? "Prefilled answer"}
                      </p>
                      <p className="text-[11px] text-muted-foreground mt-0.5">
                        {currentQ.answer_provenance &&
                        typeof currentQ.answer_provenance === "object" &&
                        "title" in currentQ.answer_provenance
                          ? String(currentQ.answer_provenance.title)
                          : "Derived from approved knowledge base"}
                        . You can accept this or choose another answer below.
                      </p>
                    </div>
                  </div>
                )}

                {/* Context claim toggle */}
                {currentQ.claim && (
                  <div className="border-t pt-2">
                    <button
                      type="button"
                      onClick={() =>
                        setShowContext((prev) => ({
                          ...prev,
                          [currentQ.id]: !prev[currentQ.id],
                        }))
                      }
                      className="flex items-center gap-1.5 text-[11px] text-muted-foreground hover:text-foreground transition-colors cursor-pointer"
                    >
                      <HelpCircle className="h-3.5 w-3.5" />
                      <span>{showContext[currentQ.id] ? "Hide" : "View"} source incident context</span>
                      {showContext[currentQ.id] ? (
                        <ChevronUp className="h-3 w-3" />
                      ) : (
                        <ChevronDown className="h-3 w-3" />
                      )}
                    </button>
                    {showContext[currentQ.id] && (
                      <p className="mt-2 rounded-md bg-muted/50 p-2.5 text-xs italic text-muted-foreground leading-relaxed">
                        “{currentQ.claim}”
                      </p>
                    )}
                  </div>
                )}
              </div>

              {/* Answering Area */}
              <div className="space-y-3">
                <Label className="text-xs font-semibold text-foreground block">
                  Select or provide your resolution:
                </Label>

                {currentQ.answer_kind === "choice" && currentQ.choices.length > 0 ? (
                  <div className="space-y-2">
                    <div className="grid gap-2 sm:grid-cols-1">
                      {currentQ.choices.map((choice) => {
                        const selected = getEffectiveValue(currentQ) === choice;
                        return (
                          <button
                            key={choice}
                            type="button"
                            disabled={busy}
                            onClick={() => {
                              handleSelectChoice(currentQ.id, choice);
                              setCustomActive((prev) => ({ ...prev, [currentQ.id]: false }));
                            }}
                            className={cn(
                              "flex items-center justify-between rounded-xl border p-3.5 text-left text-sm transition-all cursor-pointer",
                              selected
                                ? "border-primary bg-primary/10 font-semibold text-primary shadow-xs ring-1 ring-primary/30"
                                : "border-border/80 bg-background hover:border-primary/50 hover:bg-accent/40 text-foreground",
                            )}
                          >
                            <span className="flex-1 pr-2">{choice}</span>
                            <div
                              className={cn(
                                "flex h-5 w-5 shrink-0 items-center justify-center rounded-full border transition-colors",
                                selected
                                  ? "border-primary bg-primary text-primary-foreground"
                                  : "border-muted-foreground/40",
                              )}
                            >
                              {selected && <Check className="h-3 w-3" />}
                            </div>
                          </button>
                        );
                      })}
                    </div>

                    {/* Custom / Add Notes Option */}
                    <div className="pt-1">
                      {!customActive[currentQ.id] ? (
                        <Button
                          type="button"
                          variant="ghost"
                          size="sm"
                          className="h-8 text-xs text-muted-foreground hover:text-primary gap-1.5"
                          onClick={() => setCustomActive((prev) => ({ ...prev, [currentQ.id]: true }))}
                        >
                          <Pencil className="h-3.5 w-3.5" />
                          Write custom response / add clarification notes
                        </Button>
                      ) : (
                        <div className="rounded-xl border border-dashed p-3 space-y-2 bg-muted/20">
                          <div className="flex items-center justify-between">
                            <span className="text-xs font-medium text-foreground">
                              Custom Resolution / Additional Details:
                            </span>
                            <Button
                              type="button"
                              variant="ghost"
                              size="sm"
                              className="h-6 px-2 text-[11px]"
                              onClick={() => setCustomActive((prev) => ({ ...prev, [currentQ.id]: false }))}
                            >
                              <X className="h-3 w-3 mr-1" />
                              Cancel
                            </Button>
                          </div>
                          <Textarea
                            value={getEffectiveValue(currentQ)}
                            disabled={busy}
                            rows={3}
                            placeholder="Type your exact instructions or resolution here…"
                            onChange={(e) => handleCustomTextChange(currentQ.id, e.target.value)}
                            onBlur={() => handleBlurSave(currentQ.id)}
                            className="bg-background text-sm"
                          />
                        </div>
                      )}
                    </div>
                  </div>
                ) : (
                  <div className="space-y-2">
                    <Textarea
                      value={getEffectiveValue(currentQ)}
                      disabled={busy}
                      rows={3}
                      placeholder={currentQ.expected_format ?? "Type your answer here…"}
                      onChange={(e) => handleCustomTextChange(currentQ.id, e.target.value)}
                      onBlur={() => handleBlurSave(currentQ.id)}
                      className="bg-background text-sm"
                    />
                    {currentQ.expected_format && (
                      <p className="text-[11px] text-muted-foreground">
                        Suggested format: {currentQ.expected_format}
                      </p>
                    )}
                  </div>
                )}
              </div>

              {/* Action Error */}
              {actionError && (
                <p className="text-xs text-destructive rounded-md bg-destructive/10 p-2.5 font-medium">
                  {actionError}
                </p>
              )}

              {/* Rewrite Section */}
              {rewriting && (
                <div className="rounded-xl border bg-muted/40 p-3.5 space-y-2.5">
                  <div className="flex items-center justify-between">
                    <span className="text-xs font-semibold text-foreground">
                      How should AI rephrase this question?
                    </span>
                    <span className="text-[11px] text-muted-foreground">
                      {rewritesLeft} rewrite{rewritesLeft === 1 ? "" : "s"} left
                    </span>
                  </div>
                  <Textarea
                    rows={2}
                    value={rewriteNote}
                    disabled={busy}
                    placeholder="e.g. Too vague — ask specifically about the Azure AD URL configuration..."
                    onChange={(e) => setRewriteNote(e.target.value)}
                    className="text-xs bg-background"
                  />
                  <div className="flex gap-2">
                    <Button
                      type="button"
                      size="sm"
                      disabled={busy}
                      onClick={() => regenerate.mutate(rewriteNote.trim() || null)}
                    >
                      {regenerate.isPending && <Loader2 className="mr-1.5 h-3.5 w-3.5 animate-spin" />}
                      Rephrase Question
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
            </div>
          ) : null}
        </div>

        {/* Footer Navigation Bar */}
        <DialogFooter className="p-4 border-t bg-muted/20 flex flex-wrap items-center justify-between sm:justify-between gap-2">
          <div className="flex items-center gap-2">
            {currentStep > 0 && (
              <Button
                type="button"
                variant="outline"
                size="sm"
                disabled={busy}
                onClick={() => setCurrentStep((prev) => Math.max(0, prev - 1))}
              >
                <ArrowLeft className="mr-1 h-3.5 w-3.5" />
                Previous
              </Button>
            )}

            {currentQ && currentQ.obligation !== "mandatory" && (
              <Button
                type="button"
                variant="ghost"
                size="sm"
                disabled={busy}
                onClick={() => handleSkipQuestion(currentQ.id)}
              >
                Skip
              </Button>
            )}

            {currentQ && rewritesLeft > 0 && !rewriting && (
              <Button
                type="button"
                variant="ghost"
                size="sm"
                disabled={busy}
                onClick={() => setRewriting(true)}
                className="text-xs text-muted-foreground"
              >
                <RefreshCw className="mr-1 h-3 w-3" />
                Rephrase
              </Button>
            )}
          </div>

          <div className="flex items-center gap-2">
            {currentStep < totalQuestions - 1 ? (
              <Button
                type="button"
                size="sm"
                disabled={busy}
                onClick={() => {
                  // Save current question before advancing
                  if (currentQ) handleBlurSave(currentQ.id);
                  setCurrentStep((prev) => Math.min(totalQuestions - 1, prev + 1));
                }}
              >
                Next Question
                <ArrowRight className="ml-1 h-3.5 w-3.5" />
              </Button>
            ) : null}

            {/* Apply & Update Playbook (Primary Action) */}
            <Button
              type="button"
              size="sm"
              variant={canApply ? "default" : "secondary"}
              disabled={busy || !canApply}
              onClick={handleApplyAndFinish}
              className={cn(canApply && "bg-primary text-primary-foreground shadow-sm")}
            >
              {applyRound.isPending ? (
                <Loader2 className="mr-1.5 h-3.5 w-3.5 animate-spin" />
              ) : (
                <Sparkles className="mr-1.5 h-3.5 w-3.5" />
              )}
              Apply &amp; Update Playbook
            </Button>
          </div>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
