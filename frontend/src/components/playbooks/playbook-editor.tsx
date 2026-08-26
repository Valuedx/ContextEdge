"use client";

import { useEffect, useMemo, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";

import { PlaybookMetaEditor, type PlaybookMetaDraft } from "@/components/playbooks/playbook-meta-editor";
import { PlaybookSaveBar } from "@/components/playbooks/playbook-save-bar";
import { PlaybookStepsEditor } from "@/components/playbooks/playbook-steps-editor";
import { api, ApiError } from "@/lib/api";
import {
  changeCount,
  diffSummary,
  ensureStepIds,
  rebaseEdits,
  toPatchPayload,
  type EditableStep,
} from "@/lib/playbook-steps";
import type { Playbook, PlaybookVersion } from "@/lib/types";

function metaFrom(playbook: Playbook, version: PlaybookVersion): PlaybookMetaDraft {
  return {
    title: playbook.title,
    description: playbook.description ?? "",
    risk_tier: playbook.risk_tier,
    rollback_notes: version.rollback_notes ?? "",
    execution_confidence_guidance: version.execution_confidence_guidance ?? "",
    trigger_conditions:
      version.trigger_conditions && typeof version.trigger_conditions === "object"
        ? { ...version.trigger_conditions }
        : {},
  };
}

export function PlaybookEditor({
  playbook,
  version,
  onCancel,
  onSaved,
}: {
  playbook: Playbook;
  version: PlaybookVersion;
  onCancel: () => void;
  onSaved: (next: PlaybookVersion) => void;
}) {
  const qc = useQueryClient();
  const [originalSteps, setOriginalSteps] = useState(() => ensureStepIds(version.steps));
  const [steps, setSteps] = useState(() => ensureStepIds(version.steps));
  const [originalMeta, setOriginalMeta] = useState(() => metaFrom(playbook, version));
  const [meta, setMeta] = useState(() => metaFrom(playbook, version));
  const [editNote, setEditNote] = useState("");
  const [revision, setRevision] = useState(version.revision ?? 1);
  const [saving, setSaving] = useState(false);
  const [conflict, setConflict] = useState(false);

  const stepChanges = useMemo(() => changeCount(originalSteps, steps), [originalSteps, steps]);
  const metaDirty =
    meta.title !== originalMeta.title ||
    meta.description !== originalMeta.description ||
    meta.risk_tier !== originalMeta.risk_tier ||
    meta.rollback_notes !== originalMeta.rollback_notes ||
    meta.execution_confidence_guidance !== originalMeta.execution_confidence_guidance ||
    JSON.stringify(meta.trigger_conditions) !== JSON.stringify(originalMeta.trigger_conditions);
  const dirty = stepChanges > 0 || metaDirty || editNote.trim().length > 0;
  const unsavedCount = stepChanges + (metaDirty ? 1 : 0) + (editNote.trim() ? 1 : 0);

  useEffect(() => {
    if (!dirty) return;
    const onUnload = (event: BeforeUnloadEvent) => {
      event.preventDefault();
      event.returnValue = "";
    };
    window.addEventListener("beforeunload", onUnload);
    return () => window.removeEventListener("beforeunload", onUnload);
  }, [dirty]);

  const refreshQueries = async () => {
    await Promise.all([
      qc.invalidateQueries({ queryKey: ["playbook", playbook.id] }),
      qc.invalidateQueries({ queryKey: ["playbook-versions", playbook.id] }),
      qc.invalidateQueries({ queryKey: ["playbooks"] }),
    ]);
  };

  const latestVersion = async (): Promise<PlaybookVersion | null> => {
    const versions = await api.get<PlaybookVersion[]>(`/playbooks/${playbook.id}/versions`);
    return versions.find((item) => item.id === version.id) ?? versions[0] ?? null;
  };

  const save = async (stepsToSave: EditableStep[], metaToSave: PlaybookMetaDraft) => {
    if (!metaToSave.title.trim()) {
      toast.error("Title cannot be empty");
      return;
    }
    const emptyInstruction = stepsToSave.some((step) => {
      const text = [step.text, step.title, step.description].find(
        (v) => typeof v === "string" && v.trim(),
      );
      return !text;
    });
    if (emptyInstruction) {
      toast.error("Every step needs instruction text");
      return;
    }
    setSaving(true);
    try {
      const playbookPatch: Record<string, unknown> = {};
      if (metaToSave.title !== originalMeta.title) playbookPatch.title = metaToSave.title;
      if (metaToSave.description !== originalMeta.description) {
        playbookPatch.description = metaToSave.description || null;
      }
      if (metaToSave.risk_tier !== originalMeta.risk_tier) {
        playbookPatch.risk_tier = metaToSave.risk_tier;
      }
      if (Object.keys(playbookPatch).length > 0) {
        await api.patch(`/playbooks/${playbook.id}`, playbookPatch);
      }

      const versionBody: Record<string, unknown> = {
        expected_revision: revision,
        edit_note: editNote.trim() || undefined,
      };
      const stepDiff = diffSummary(originalSteps, stepsToSave);
      if (
        stepDiff.added.length ||
        stepDiff.removed.length ||
        stepDiff.modified.length ||
        stepDiff.reordered
      ) {
        versionBody.steps = toPatchPayload(originalSteps, stepsToSave);
      }
      if (metaToSave.rollback_notes !== originalMeta.rollback_notes) {
        versionBody.rollback_notes = metaToSave.rollback_notes || null;
      }
      if (metaToSave.execution_confidence_guidance !== originalMeta.execution_confidence_guidance) {
        versionBody.execution_confidence_guidance =
          metaToSave.execution_confidence_guidance || null;
      }
      if (
        JSON.stringify(metaToSave.trigger_conditions) !==
        JSON.stringify(originalMeta.trigger_conditions)
      ) {
        versionBody.trigger_conditions = metaToSave.trigger_conditions;
      }

      const shouldPatchVersion = Object.keys(versionBody).some(
        (key) => key !== "expected_revision" && key !== "edit_note",
      ) || Boolean(editNote.trim());

      let saved = version;
      if (shouldPatchVersion) {
        saved = await api.patch<PlaybookVersion>(
          `/playbooks/${playbook.id}/versions/${version.id}`,
          versionBody,
        );
      }
      await refreshQueries();
      toast.success("Draft saved");
      onSaved(saved);
    } catch (err) {
      if (err instanceof ApiError && err.code === "revision_conflict") {
        setConflict(true);
        if (typeof err.currentRevision === "number") setRevision(err.currentRevision);
        toast.error(err.message);
      } else {
        toast.error(err instanceof Error ? err.message : "Save failed");
      }
    } finally {
      setSaving(false);
    }
  };

  const reloadLatest = async () => {
    const latest = await latestVersion();
    if (!latest) return;
    const nextSteps = ensureStepIds(latest.steps);
    setOriginalSteps(nextSteps);
    setSteps(nextSteps);
    setOriginalMeta(metaFrom(playbook, latest));
    setMeta(metaFrom(playbook, latest));
    setRevision(latest.revision ?? revision);
    setConflict(false);
    setEditNote("");
  };

  const overwrite = async () => {
    const latest = await latestVersion();
    if (!latest) return;
    const newBase = ensureStepIds(latest.steps);
    const rebased = rebaseEdits(originalSteps, steps, newBase);
    setOriginalSteps(newBase);
    setSteps(rebased);
    setRevision(latest.revision ?? revision);
    setConflict(false);
    await save(rebased, meta);
  };

  return (
    <div className="space-y-6 pb-4">
      <PlaybookMetaEditor value={meta} onChange={setMeta} />
      <PlaybookStepsEditor steps={steps} onChange={setSteps} />
      <PlaybookSaveBar
        changeCount={unsavedCount}
        editNote={editNote}
        onEditNoteChange={setEditNote}
        onSave={() => void save(steps, meta)}
        onDiscard={onCancel}
        saving={saving}
        conflict={conflict}
        onReload={() => void reloadLatest()}
        onOverwrite={() => void overwrite()}
      />
    </div>
  );
}
