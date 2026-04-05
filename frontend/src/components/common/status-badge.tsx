import { Badge } from "@/components/ui/badge";

const statusColors: Record<string, string> = {
  active: "bg-green-100 text-green-800 dark:bg-green-900 dark:text-green-200",
  healthy: "bg-green-100 text-green-800 dark:bg-green-900 dark:text-green-200",
  approved: "bg-green-100 text-green-800 dark:bg-green-900 dark:text-green-200",
  connected: "bg-green-100 text-green-800 dark:bg-green-900 dark:text-green-200",
  running: "bg-blue-100 text-blue-800 dark:bg-blue-900 dark:text-blue-200",
  in_progress: "bg-blue-100 text-blue-800 dark:bg-blue-900 dark:text-blue-200",
  under_review: "bg-yellow-100 text-yellow-800 dark:bg-yellow-900 dark:text-yellow-200",
  candidate: "bg-yellow-100 text-yellow-800 dark:bg-yellow-900 dark:text-yellow-200",
  pending: "bg-yellow-100 text-yellow-800 dark:bg-yellow-900 dark:text-yellow-200",
  draft: "bg-gray-100 text-gray-800 dark:bg-gray-800 dark:text-gray-200",
  inactive: "bg-gray-100 text-gray-800 dark:bg-gray-800 dark:text-gray-200",
  failed: "bg-red-100 text-red-800 dark:bg-red-900 dark:text-red-200",
  error: "bg-red-100 text-red-800 dark:bg-red-900 dark:text-red-200",
  expired: "bg-red-100 text-red-800 dark:bg-red-900 dark:text-red-200",
  deprecated: "bg-orange-100 text-orange-800 dark:bg-orange-900 dark:text-orange-200",
  restricted: "bg-orange-100 text-orange-800 dark:bg-orange-900 dark:text-orange-200",
  retired: "bg-gray-100 text-gray-800 dark:bg-gray-800 dark:text-gray-200",
  high: "bg-red-100 text-red-800 dark:bg-red-900 dark:text-red-200",
  critical: "bg-red-200 text-red-950 dark:bg-red-950 dark:text-red-100",
  medium: "bg-amber-100 text-amber-900 dark:bg-amber-900 dark:text-amber-200",
  low: "bg-slate-100 text-slate-800 dark:bg-slate-800 dark:text-slate-200",
  minimal: "bg-slate-100 text-slate-700 dark:bg-slate-800 dark:text-slate-300",
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
