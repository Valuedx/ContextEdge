const EXPLORER_TYPES = new Set([
  "pattern",
  "episode",
  "playbook",
  "evidence",
  "identity",
  "trigger",
  "entity",
  "error",
  "root_cause",
  "session",
  "execution_run",
  "approval_request",
  "user",
  "decision",
  "decision_option",
  "decision_outcome",
  "tenant_policy",
  "action_policy",
  "claim",
  "error_signature",
  "fix_pattern",
  "case_outcome",
  "entity_term",
]);

const TYPE_ALIASES: Record<string, string> = {
  trigger_condition: "trigger",
  policy: "tenant_policy",
};

export function safeInternalReturnPath(value: string | null | undefined): string | null {
  if (!value) return null;
  if (!value.startsWith("/") || value.startsWith("//") || value.includes("://")) {
    return null;
  }
  return value;
}

export function backLabelForPath(path: string): string {
  if (path.startsWith("/patterns/")) return "Back to pattern";
  if (path.startsWith("/episodes/")) return "Back to episode";
  if (path.startsWith("/playbooks/")) return "Back to playbook";
  if (path.startsWith("/evidence/")) return "Back to evidence";
  if (path.startsWith("/identities/")) return "Back to identity";
  if (path.startsWith("/decisions")) return "Back to decision";
  return "Back";
}

function explorerHref(type: string, id: string, from?: string): string {
  const params = new URLSearchParams({
    tab: "subgraph",
    node_type: type,
    node_id: id,
  });
  if (from) params.set("from", from);
  return `/graph-explorer?${params.toString()}`;
}

/** Working console URL for a graph node, or null when there is no record page. */
export function graphNodeRecordHref(
  type: string,
  rawId: string,
  options?: { from?: string | null },
): string | null {
  const id = rawId.trim();
  if (!id) return null;
  const mapped = TYPE_ALIASES[type] ?? type;
  const from = safeInternalReturnPath(options?.from ?? null) ?? undefined;

  switch (mapped) {
    case "pattern":
      return `/patterns/${id}`;
    case "episode":
      return `/episodes/${id}`;
    case "playbook":
      return `/playbooks/${id}`;
    case "evidence":
      return `/evidence/${id}`;
    case "identity":
      return `/identities/${id}`;
    case "decision":
      return `/decisions?id=${encodeURIComponent(id)}`;
    default:
      if (EXPLORER_TYPES.has(mapped)) {
        return explorerHref(mapped, id, from);
      }
      return null;
  }
}
