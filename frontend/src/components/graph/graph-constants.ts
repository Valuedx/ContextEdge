export const NODE_TYPE_OPTIONS = [
  "pattern",
  "episode",
  "playbook",
  "evidence",
  "identity",
  "trigger",
  "entity",
  "error",
  "root_cause",
] as const;

export const nodeColors: Record<string, { bg: string; border: string; text: string; dot: string }> = {
  pattern:    { bg: "bg-indigo-900",  border: "border-indigo-500",  text: "text-white",       dot: "bg-indigo-500" },
  episode:    { bg: "bg-purple-900",  border: "border-purple-500",  text: "text-slate-100",   dot: "bg-purple-500" },
  playbook:   { bg: "bg-sky-900",     border: "border-sky-500",     text: "text-sky-100",     dot: "bg-sky-500" },
  identity:   { bg: "bg-teal-900",    border: "border-teal-500",    text: "text-teal-100",    dot: "bg-teal-500" },
  evidence:   { bg: "bg-slate-800",   border: "border-slate-500",   text: "text-slate-100",   dot: "bg-slate-500" },
  trigger:    { bg: "bg-amber-900",   border: "border-amber-500",   text: "text-amber-100",   dot: "bg-amber-500" },
  entity:     { bg: "bg-emerald-900", border: "border-emerald-500", text: "text-emerald-100", dot: "bg-emerald-500" },
  error:      { bg: "bg-rose-900",    border: "border-rose-500",    text: "text-rose-100",    dot: "bg-rose-500" },
  root_cause: { bg: "bg-orange-900",  border: "border-orange-500",  text: "text-orange-100",  dot: "bg-orange-500" },
};

export const edgeColors: Record<string, { stroke: string; dasharray?: string }> = {
  belongs_to:          { stroke: "#818cf8", dasharray: "5 5" },
  trigger_of:          { stroke: "#fbbf24" },
  involved_in:         { stroke: "#34d399" },
  discovered_in:       { stroke: "#fb7185" },
  causes:              { stroke: "#fb923c" },
  derived_from:        { stroke: "#38bdf8", dasharray: "5 5" },
  affects:             { stroke: "#a78bfa" },
  mentions_identity:   { stroke: "#2dd4bf", dasharray: "3 3" },
  references_identity: { stroke: "#2dd4bf" },
  contradicts:         { stroke: "#f43f5e", dasharray: "8 4" },
};

export function getNodeClassName(nodeType: string): string {
  const c = nodeColors[nodeType];
  if (!c) return "bg-slate-800 border-slate-600 text-slate-100";
  return `${c.bg} ${c.border} ${c.text}`;
}
