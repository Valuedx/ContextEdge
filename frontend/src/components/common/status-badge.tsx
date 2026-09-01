import { Badge } from "@/components/ui/badge";

const statusColors: Record<string, string> = {
  active: "bg-green-100 text-green-800 dark:bg-green-500/15 dark:text-green-200",
  healthy: "bg-green-100 text-green-800 dark:bg-green-500/15 dark:text-green-200",
  approved: "bg-green-100 text-green-800 dark:bg-green-500/15 dark:text-green-200",
  connected: "bg-green-100 text-green-800 dark:bg-green-500/15 dark:text-green-200",
  running: "bg-sky-100 text-sky-800 dark:bg-sky-500/15 dark:text-sky-200",
  in_progress: "bg-sky-100 text-sky-800 dark:bg-sky-500/15 dark:text-sky-200",
  under_review: "bg-yellow-100 text-yellow-800 dark:bg-yellow-500/15 dark:text-yellow-200",
  candidate: "bg-yellow-100 text-yellow-800 dark:bg-yellow-500/15 dark:text-yellow-200",
  pending: "bg-yellow-100 text-yellow-800 dark:bg-yellow-500/15 dark:text-yellow-200",
  draft: "bg-gray-100 text-gray-800 dark:bg-white/10 dark:text-gray-200",
  inactive: "bg-gray-100 text-gray-800 dark:bg-white/10 dark:text-gray-200",
  failed: "bg-red-100 text-red-800 dark:bg-red-500/15 dark:text-red-200",
  error: "bg-red-100 text-red-800 dark:bg-red-500/15 dark:text-red-200",
  expired: "bg-red-100 text-red-800 dark:bg-red-500/15 dark:text-red-200",
  // Playbook quality states. `failed` was already here; `fail` was not, so a
  // failing quality verdict rendered grey — the one state that must not look
  // neutral. `inconclusive` is deliberately grey rather than amber: in the
  // current validator bundle most dimensions are undecided, and colouring
  // "we have not checked this yet" as a warning would put an alarm on every
  // playbook in the corpus and teach reviewers to ignore it.
  pass: "bg-green-100 text-green-800 dark:bg-green-500/15 dark:text-green-200",
  fail: "bg-red-100 text-red-800 dark:bg-red-500/15 dark:text-red-200",
  inconclusive: "bg-gray-100 text-gray-700 dark:bg-white/10 dark:text-gray-300",
  stale: "bg-amber-100 text-amber-800 dark:bg-amber-500/15 dark:text-amber-200",
  overridden: "bg-violet-100 text-violet-800 dark:bg-violet-500/15 dark:text-violet-200",
  major: "bg-orange-100 text-orange-900 dark:bg-orange-500/15 dark:text-orange-200",
  info: "bg-slate-100 text-slate-700 dark:bg-white/10 dark:text-slate-300",
  deprecated: "bg-amber-100 text-amber-800 dark:bg-amber-500/15 dark:text-amber-200",
  restricted: "bg-amber-100 text-amber-800 dark:bg-amber-500/15 dark:text-amber-200",
  retired: "bg-gray-100 text-gray-800 dark:bg-white/10 dark:text-gray-200",
  high: "bg-red-100 text-red-800 dark:bg-red-500/15 dark:text-red-200",
  critical: "bg-red-200 text-red-950 dark:bg-red-500/20 dark:text-red-100",
  medium: "bg-amber-100 text-amber-900 dark:bg-amber-500/15 dark:text-amber-200",
  low: "bg-slate-100 text-slate-800 dark:bg-white/10 dark:text-slate-200",
  minimal: "bg-slate-100 text-slate-700 dark:bg-white/10 dark:text-slate-300",
};

export function StatusBadge({ status }: { status: string }) {
  const key = status.toLowerCase();
  const colors =
    statusColors[key] ??
    "bg-gray-100 text-gray-800 dark:bg-gray-800 dark:text-gray-200";
  return (
    <Badge variant="outline" className={colors}>
      {status.replace(/_/g, " ")}
    </Badge>
  );
}
