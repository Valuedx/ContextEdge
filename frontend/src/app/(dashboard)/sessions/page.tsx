"use client";

import Link from "next/link";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ColumnDef } from "@tanstack/react-table";
import { Plus, Loader2, CheckCircle, ExternalLink } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";

import { PageHeader } from "@/components/common/page-header";
import { DataTable } from "@/components/common/data-table";
import { DataTableSkeleton } from "@/components/common/data-table-skeleton";
import { StatusBadge } from "@/components/common/status-badge";
import { PaginationControls } from "@/components/common/pagination-controls";
import { usePagination } from "@/lib/hooks/use-pagination";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Textarea } from "@/components/ui/textarea";
import { Separator } from "@/components/ui/separator";
import { api } from "@/lib/api";
import { useAuthStore } from "@/lib/stores/auth-store";
import type { Decision } from "@/lib/types";

interface DecisionTraceEvent {
  id: string;
  event_type: string;
  reasoning: string | null;
  confidence: number | null;
  created_at: string;
}

interface ResolutionSession {
  id: string;
  tenant_id: string;
  domain_id: string | null;
  initiated_by: string | null;
  status: string;
  symptoms: string[];
  entities: string[];
  external_case_ids: string[];
  notes: string | null;
  closed_at: string | null;
  created_at: string;
  updated_at: string;
  trace_events: DecisionTraceEvent[];
}

const columns: ColumnDef<ResolutionSession>[] = [
  {
    accessorKey: "status",
    header: "Status",
    cell: ({ row }) => <StatusBadge status={row.getValue("status")} />,
  },
  {
    accessorKey: "symptoms",
    header: "Symptoms",
    cell: ({ row }) => {
      const s = row.getValue("symptoms") as string[];
      return s.length > 0 ? s.slice(0, 3).join(", ") + (s.length > 3 ? "…" : "") : "—";
    },
  },
  {
    accessorKey: "entities",
    header: "Entities",
    cell: ({ row }) => {
      const e = row.getValue("entities") as string[];
      return e.length > 0 ? e.slice(0, 3).join(", ") + (e.length > 3 ? "…" : "") : "—";
    },
  },
  {
    accessorKey: "trace_events",
    header: "Trace events",
    cell: ({ row }) => (row.getValue("trace_events") as unknown[]).length,
  },
  {
    accessorKey: "created_at",
    header: "Created",
    cell: ({ row }) => new Date(row.getValue("created_at") as string).toLocaleString(),
  },
  {
    accessorKey: "closed_at",
    header: "Closed",
    cell: ({ row }) => {
      const v = row.getValue("closed_at") as string | null;
      return v ? new Date(v).toLocaleString() : "—";
    },
  },
];

function NewSessionDialog({ onClose }: { onClose: () => void }) {
  const qc = useQueryClient();
  const [symptoms, setSymptoms] = useState("");
  const [entities, setEntities] = useState("");
  const [notes, setNotes] = useState("");
  const [caseIds, setCaseIds] = useState("");

  const mut = useMutation({
    mutationFn: () =>
      api.post<ResolutionSession>("/sessions", {
        symptoms: symptoms.split("\n").map((s) => s.trim()).filter(Boolean),
        entities: entities.split("\n").map((s) => s.trim()).filter(Boolean),
        external_case_ids: caseIds.split("\n").map((s) => s.trim()).filter(Boolean),
        notes: notes.trim() || null,
      }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["sessions"] });
      toast.success("Session created");
      onClose();
    },
    onError: (err: Error) => {
      toast.error(err.message || "Failed to create session");
    },
  });

  return (
    <DialogContent className="max-w-lg">
      <DialogHeader>
        <DialogTitle>New resolution session</DialogTitle>
      </DialogHeader>
      <div className="space-y-4">
        <div>
          <Label htmlFor="symptoms">Symptoms (one per line)</Label>
          <Textarea
            id="symptoms"
            className="mt-1 font-mono text-xs min-h-[80px]"
            placeholder="VPN drops after authentication&#10;MFA prompt not appearing"
            value={symptoms}
            onChange={(e) => setSymptoms(e.target.value)}
          />
        </div>
        <div>
          <Label htmlFor="entities">Entities (one per line)</Label>
          <Textarea
            id="entities"
            className="mt-1 font-mono text-xs min-h-[60px]"
            placeholder="Corporate VPN&#10;Windows 11"
            value={entities}
            onChange={(e) => setEntities(e.target.value)}
          />
        </div>
        <div>
          <Label htmlFor="case-ids">External case IDs (one per line, optional)</Label>
          <Input
            id="case-ids"
            className="mt-1 font-mono text-xs"
            placeholder="INC-1234"
            value={caseIds}
            onChange={(e) => setCaseIds(e.target.value)}
          />
        </div>
        <div>
          <Label htmlFor="notes">Notes (optional)</Label>
          <Textarea
            id="notes"
            className="mt-1"
            value={notes}
            onChange={(e) => setNotes(e.target.value)}
          />
        </div>
      </div>
      <DialogFooter>
        <Button variant="outline" onClick={onClose}>Cancel</Button>
        <Button disabled={mut.isPending} onClick={() => mut.mutate()}>
          {mut.isPending && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
          Create
        </Button>
      </DialogFooter>
      {mut.error && (
        <p className="text-sm text-destructive">{String((mut.error as Error).message)}</p>
      )}
    </DialogContent>
  );
}

function SessionDetail({
  session,
  onClose,
}: {
  session: ResolutionSession;
  onClose: () => void;
}) {
  const qc = useQueryClient();

  const closeMut = useMutation({
    mutationFn: () => api.patch(`/sessions/${session.id}/close`, {}),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["sessions"] });
      toast.success("Session closed");
      onClose();
    },
    onError: (err: Error) => toast.error(err.message || "Failed to close session"),
  });

  return (
    <Card>
      <CardHeader className="flex flex-row items-center justify-between pb-2">
        <CardTitle className="text-base font-mono text-xs">{session.id}</CardTitle>
        <div className="flex gap-2">
          {session.status === "open" && (
            <Button
              variant="outline"
              size="sm"
              disabled={closeMut.isPending}
              onClick={() => closeMut.mutate()}
            >
              {closeMut.isPending ? (
                <Loader2 className="mr-2 h-4 w-4 animate-spin" />
              ) : (
                <CheckCircle className="mr-2 h-4 w-4" />
              )}
              Close session
            </Button>
          )}
          <Button variant="ghost" size="sm" onClick={onClose}>
            Dismiss
          </Button>
        </div>
      </CardHeader>
      <CardContent className="space-y-4 text-sm">
        <div className="grid gap-2 md:grid-cols-2">
          <div>
            <p className="text-xs text-muted-foreground">Status</p>
            <StatusBadge status={session.status} />
          </div>
          <div>
            <p className="text-xs text-muted-foreground">Created</p>
            <p>{new Date(session.created_at).toLocaleString()}</p>
          </div>
          {session.symptoms.length > 0 && (
            <div className="col-span-2">
              <p className="text-xs text-muted-foreground mb-1">Symptoms</p>
              <ul className="list-disc list-inside space-y-0.5">
                {session.symptoms.map((s, i) => <li key={i}>{s}</li>)}
              </ul>
            </div>
          )}
          {session.entities.length > 0 && (
            <div className="col-span-2">
              <p className="text-xs text-muted-foreground mb-1">Entities</p>
              <ul className="list-disc list-inside space-y-0.5">
                {session.entities.map((e, i) => <li key={i}>{e}</li>)}
              </ul>
            </div>
          )}
          {session.notes && (
            <div className="col-span-2">
              <p className="text-xs text-muted-foreground mb-1">Notes</p>
              <p className="whitespace-pre-wrap">{session.notes}</p>
            </div>
          )}
        </div>

        <Separator />
        <SessionDecisionsAndTraces session={session} />
      </CardContent>
    </Card>
  );
}

function SessionDecisionsAndTraces({ session }: { session: ResolutionSession }) {
  const [tab, setTab] = useState("traces");

  const { data: decisions = [], isLoading } = useQuery<Decision[]>({
    queryKey: ["session-decisions", session.id],
    queryFn: () =>
      api.get("/decisions", { session_id: session.id, limit: "50" }),
    enabled: tab === "decisions",
  });

  return (
    <Tabs value={tab} onValueChange={setTab}>
      <TabsList>
        <TabsTrigger value="traces">
          Trace events ({session.trace_events.length})
        </TabsTrigger>
        <TabsTrigger value="decisions">Decisions</TabsTrigger>
      </TabsList>

      <TabsContent value="traces" className="mt-3">
        {session.trace_events.length === 0 ? (
          <p className="text-xs text-muted-foreground">No trace events yet.</p>
        ) : (
          <div className="space-y-2">
            {session.trace_events.map((ev) => (
              <div key={ev.id} className="rounded-md border p-3 space-y-1">
                <div className="flex items-center justify-between">
                  <span className="font-mono text-xs font-medium">
                    {ev.event_type}
                  </span>
                  <span className="text-xs text-muted-foreground">
                    {new Date(ev.created_at).toLocaleString()}
                  </span>
                </div>
                {ev.confidence != null && (
                  <p className="text-xs text-muted-foreground">
                    Confidence: {(ev.confidence * 100).toFixed(0)}%
                  </p>
                )}
                {ev.reasoning && (
                  <p className="text-xs whitespace-pre-wrap">{ev.reasoning}</p>
                )}
              </div>
            ))}
          </div>
        )}
      </TabsContent>

      <TabsContent value="decisions" className="mt-3">
        {isLoading ? (
          <p className="text-xs text-muted-foreground">Loading decisions…</p>
        ) : decisions.length === 0 ? (
          <p className="text-xs text-muted-foreground">
            No first-class decisions linked to this session.
          </p>
        ) : (
          <div className="space-y-2">
            {decisions.map((d) => (
              <div
                key={d.id}
                className="rounded-md border p-3 space-y-1 hover:border-amber-500/30 transition-colors"
              >
                <div className="flex items-center gap-2 flex-wrap">
                  <Badge variant="outline" className="text-[10px]">
                    {d.decision_type}
                  </Badge>
                  <Badge variant="secondary" className="text-[10px]">
                    {d.agent_step}
                  </Badge>
                  <StatusBadge status={d.status} />
                  {d.confidence != null && (
                    <span className="text-[10px] font-mono text-muted-foreground">
                      {Math.round(d.confidence * 100)}%
                    </span>
                  )}
                  <span className="ml-auto text-[10px] text-muted-foreground">
                    {new Date(d.created_at).toLocaleString()}
                  </span>
                </div>
                {d.compact_trace && (
                  <p className="text-xs">{d.compact_trace}</p>
                )}
                {d.options.length > 0 && (
                  <p className="text-[10px] text-muted-foreground">
                    {d.options.length} option{d.options.length !== 1 ? "s" : ""}
                    {d.options.find((o) => o.selected) &&
                      ` · selected: ${d.options.find((o) => o.selected)!.action}`}
                  </p>
                )}
                {d.outcomes.length > 0 && (
                  <StatusBadge
                    status={d.outcomes[d.outcomes.length - 1].execution_result}
                  />
                )}
                <Link
                  href={`/decisions?id=${d.id}`}
                  className="text-[10px] text-primary hover:underline inline-flex items-center gap-1 mt-1"
                >
                  View full decision
                  <ExternalLink className="h-2.5 w-2.5" />
                </Link>
              </div>
            ))}
          </div>
        )}
      </TabsContent>
    </Tabs>
  );
}

export default function SessionsPage() {
  const roles = useAuthStore((s) => s.roles);
  const canCreate = roles.length > 0; // any authenticated user may open a session
  const [newOpen, setNewOpen] = useState(false);
  const [selected, setSelected] = useState<ResolutionSession | null>(null);
  const pg = usePagination(50);

  const { data = [], isLoading } = useQuery<ResolutionSession[]>({
    queryKey: ["sessions", pg.page],
    queryFn: () => api.get("/sessions", pg.params),
  });

  return (
    <div className="space-y-6">
      <PageHeader
        title="Resolution Sessions"
        description="Governed investigation sessions with full decision traces for audit."
        actions={
          canCreate ? (
            <Button onClick={() => setNewOpen(true)}>
              <Plus className="mr-2 h-4 w-4" />
              New Session
            </Button>
          ) : undefined
        }
      />

      <Dialog open={newOpen} onOpenChange={setNewOpen}>
        {newOpen && <NewSessionDialog onClose={() => setNewOpen(false)} />}
      </Dialog>

      {isLoading ? (
        <DataTableSkeleton columns={6} />
      ) : data.length === 0 ? (
        <div className="rounded-md border p-12 text-center text-muted-foreground">
          No sessions yet. Create a session from the Runtime page or start one here.
        </div>
      ) : (
        <>
          <DataTable
            columns={[
              ...columns,
              {
                id: "actions",
                header: "",
                cell: ({ row }) => (
                  <Button
                    variant="ghost"
                    size="sm"
                    onClick={() =>
                      setSelected(selected?.id === row.original.id ? null : row.original)
                    }
                  >
                    {selected?.id === row.original.id ? "Hide" : "View"}
                  </Button>
                ),
              },
            ]}
            data={data}
          />
          <PaginationControls
            page={pg.page}
            pageSize={pg.pageSize}
            count={data.length}
            onPrev={pg.prevPage}
            onNext={pg.nextPage}
          />
        </>
      )}

      {selected && (
        <SessionDetail session={selected} onClose={() => setSelected(null)} />
      )}
    </div>
  );
}
