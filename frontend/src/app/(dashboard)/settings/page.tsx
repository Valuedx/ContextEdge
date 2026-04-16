"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ColumnDef } from "@tanstack/react-table";
import { Plus, Loader2 } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";

import { PageHeader } from "@/components/common/page-header";
import { DataTable } from "@/components/common/data-table";
import { DataTableSkeleton } from "@/components/common/data-table-skeleton";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { api } from "@/lib/api";
import type { Domain, Tenant, User, Workspace } from "@/lib/types";
import { useAuthStore } from "@/lib/stores/auth-store";
import { isTenantAdmin } from "@/lib/roles";

// ── Workspace creation dialog ──────────────────────────────────────────────

function NewWorkspaceDialog({ onClose }: { onClose: () => void }) {
  const qc = useQueryClient();
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");

  const mut = useMutation({
    mutationFn: () =>
      api.post<Workspace>("/workspaces", {
        name: name.trim(),
        description: description.trim() || null,
      }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["workspaces"] });
      toast.success(`Workspace "${name.trim()}" created`);
      onClose();
    },
    onError: (err: any) => toast.error(err.message || "Failed to create workspace"),
  });

  return (
    <DialogContent>
      <DialogHeader>
        <DialogTitle>New workspace</DialogTitle>
      </DialogHeader>
      <div className="space-y-3">
        <div>
          <Label htmlFor="ws-name">Name</Label>
          <Input
            id="ws-name"
            className="mt-1"
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder="US Operations"
          />
        </div>
        <div>
          <Label htmlFor="ws-desc">Description (optional)</Label>
          <Input
            id="ws-desc"
            className="mt-1"
            value={description}
            onChange={(e) => setDescription(e.target.value)}
          />
        </div>
      </div>
      <DialogFooter>
        <Button variant="outline" onClick={onClose}>Cancel</Button>
        <Button disabled={!name.trim() || mut.isPending} onClick={() => mut.mutate()}>
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

// ── Domain creation dialog ─────────────────────────────────────────────────

function NewDomainDialog({
  workspaces,
  onClose,
}: {
  workspaces: Workspace[];
  onClose: () => void;
}) {
  const qc = useQueryClient();
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [workspaceId, setWorkspaceId] = useState<string>("");

  const mut = useMutation({
    mutationFn: () =>
      api.post<Domain>("/domains", {
        name: name.trim(),
        description: description.trim() || null,
        workspace_id: workspaceId || null,
      }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["domains"] });
      toast.success(`Domain "${name.trim()}" created`);
      onClose();
    },
    onError: (err: any) => toast.error(err.message || "Failed to create domain"),
  });

  return (
    <DialogContent>
      <DialogHeader>
        <DialogTitle>New domain</DialogTitle>
      </DialogHeader>
      <div className="space-y-3">
        <div>
          <Label htmlFor="dom-name">Name</Label>
          <Input
            id="dom-name"
            className="mt-1"
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder="VPN & Connectivity"
          />
        </div>
        <div>
          <Label htmlFor="dom-desc">Description (optional)</Label>
          <Input
            id="dom-desc"
            className="mt-1"
            value={description}
            onChange={(e) => setDescription(e.target.value)}
          />
        </div>
        {workspaces.length > 0 && (
          <div>
            <Label>Workspace (optional)</Label>
            <Select value={workspaceId} onValueChange={(v) => setWorkspaceId(v ?? "")}>
              <SelectTrigger className="mt-1">
                <SelectValue placeholder="No workspace" />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="">No workspace</SelectItem>
                {workspaces.map((w) => (
                  <SelectItem key={w.id} value={w.id}>
                    {w.name}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
        )}
      </div>
      <DialogFooter>
        <Button variant="outline" onClick={onClose}>Cancel</Button>
        <Button disabled={!name.trim() || mut.isPending} onClick={() => mut.mutate()}>
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

// ── Column definitions ─────────────────────────────────────────────────────

const workspaceColumns: ColumnDef<Workspace>[] = [
  { accessorKey: "name", header: "Name" },
  {
    accessorKey: "description",
    header: "Description",
    cell: ({ row }) => (row.getValue("description") as string | null) ?? "—",
  },
  {
    accessorKey: "is_active",
    header: "Active",
    cell: ({ row }) => ((row.getValue("is_active") as boolean) ? "Yes" : "No"),
  },
  {
    accessorKey: "created_at",
    header: "Created",
    cell: ({ row }) => new Date(row.getValue("created_at") as string).toLocaleDateString(),
  },
];

const domainColumns: ColumnDef<Domain>[] = [
  { accessorKey: "name", header: "Name" },
  {
    accessorKey: "description",
    header: "Description",
    cell: ({ row }) => (row.getValue("description") as string | null) ?? "—",
  },
  {
    accessorKey: "workspace_id",
    header: "Workspace",
    cell: ({ row }) => (
      <span className="font-mono text-xs">{(row.getValue("workspace_id") as string | null) ?? "—"}</span>
    ),
  },
  {
    accessorKey: "is_active",
    header: "Active",
    cell: ({ row }) => ((row.getValue("is_active") as boolean) ? "Yes" : "No"),
  },
];

const userColumns: ColumnDef<User>[] = [
  { accessorKey: "email", header: "Email" },
  { accessorKey: "display_name", header: "Name" },
  { accessorKey: "status", header: "Status" },
];

// ── Page ──────────────────────────────────────────────────────────────────

export default function SettingsPage() {
  const tenantId = useAuthStore((s) => s.tenantId);
  const roles = useAuthStore((s) => s.roles);
  const admin = isTenantAdmin(roles);

  const [wsOpen, setWsOpen] = useState(false);
  const [domOpen, setDomOpen] = useState(false);

  const { data: tenant, isLoading: tenantLoading } = useQuery({
    queryKey: ["tenant", tenantId],
    queryFn: () => api.get<Tenant>(`/tenants/${tenantId}`),
    enabled: !!tenantId,
  });

  const { data: workspaces = [], isLoading: wsLoading } = useQuery({
    queryKey: ["workspaces"],
    queryFn: () => api.get<Workspace[]>("/workspaces"),
    enabled: !!tenantId,
  });

  const { data: domains = [], isLoading: domLoading } = useQuery({
    queryKey: ["domains"],
    queryFn: () => api.get<Domain[]>("/domains"),
    enabled: !!tenantId,
  });

  const { data: users = [], isLoading: usersLoading, error: usersError } = useQuery({
    queryKey: ["users"],
    queryFn: () => api.get<User[]>("/users"),
    enabled: !!tenantId && admin,
  });

  return (
    <div className="space-y-6">
      <PageHeader
        title="Settings"
        description="Tenant, workspaces, domains, and users for your organization."
      />

      {!tenantId && (
        <p className="text-sm text-muted-foreground">Sign in to view tenant settings.</p>
      )}

      <Tabs defaultValue="general">
        <TabsList variant="glass">
          <TabsTrigger value="general">General</TabsTrigger>
          <TabsTrigger value="workspaces">Workspaces</TabsTrigger>
          <TabsTrigger value="domains">Domains</TabsTrigger>
          <TabsTrigger value="users">Users</TabsTrigger>
          <TabsTrigger value="retention">Retention</TabsTrigger>
        </TabsList>

        {/* ── General ── */}
        <TabsContent value="general" className="mt-4">
          <Card>
            <CardHeader><CardTitle>Tenant</CardTitle></CardHeader>
            <CardContent>
              {tenantLoading ? (
                <div className="space-y-4">
                  {Array.from({ length: 4 }).map((_, i) => (
                    <div key={i} className="space-y-1.5">
                      <Skeleton className="h-3 w-16" />
                      <Skeleton className="h-4 w-[min(100%,14rem)]" />
                    </div>
                  ))}
                  <Skeleton className="mt-2 h-32 w-full max-w-md rounded-md" />
                </div>
              ) : tenant ? (
                <dl className="space-y-2 text-sm">
                  <div>
                    <dt className="text-muted-foreground">Name</dt>
                    <dd className="font-medium">{tenant.name}</dd>
                  </div>
                  <div>
                    <dt className="text-muted-foreground">Slug</dt>
                    <dd className="font-mono text-xs">{tenant.slug}</dd>
                  </div>
                  <div>
                    <dt className="text-muted-foreground">Status</dt>
                    <dd>{tenant.is_active ? "Active" : "Inactive"}</dd>
                  </div>
                  <div>
                    <dt className="text-muted-foreground">Config</dt>
                    <dd>
                      <pre className="mt-1 max-h-48 overflow-auto rounded-md bg-muted p-2 text-xs">
                        {JSON.stringify(tenant.config ?? {}, null, 2)}
                      </pre>
                    </dd>
                  </div>
                </dl>
              ) : (
                <p className="text-sm text-muted-foreground">Could not load tenant.</p>
              )}
            </CardContent>
          </Card>
        </TabsContent>

        {/* ── Workspaces ── */}
        <TabsContent value="workspaces" className="mt-4 space-y-4">
          {admin && (
            <div className="flex justify-end">
              <Button onClick={() => setWsOpen(true)}>
                <Plus className="mr-2 h-4 w-4" />
                New Workspace
              </Button>
            </div>
          )}
          <Dialog open={wsOpen} onOpenChange={setWsOpen}>
            {wsOpen && <NewWorkspaceDialog onClose={() => setWsOpen(false)} />}
          </Dialog>
          {wsLoading ? (
            <DataTableSkeleton columns={4} />
          ) : workspaces.length === 0 ? (
            <p className="text-sm text-muted-foreground">No workspaces yet.</p>
          ) : (
            <DataTable columns={workspaceColumns} data={workspaces} />
          )}
        </TabsContent>

        {/* ── Domains ── */}
        <TabsContent value="domains" className="mt-4 space-y-4">
          {admin && (
            <div className="flex justify-end">
              <Button onClick={() => setDomOpen(true)}>
                <Plus className="mr-2 h-4 w-4" />
                New Domain
              </Button>
            </div>
          )}
          <Dialog open={domOpen} onOpenChange={setDomOpen}>
            {domOpen && (
              <NewDomainDialog
                workspaces={workspaces}
                onClose={() => setDomOpen(false)}
              />
            )}
          </Dialog>
          {domLoading ? (
            <DataTableSkeleton columns={4} />
          ) : domains.length === 0 ? (
            <p className="text-sm text-muted-foreground">No domains yet.</p>
          ) : (
            <DataTable columns={domainColumns} data={domains} />
          )}
        </TabsContent>

        {/* ── Users ── */}
        <TabsContent value="users" className="mt-4">
          {!admin ? (
            <p className="text-sm text-muted-foreground">
              Tenant admin role is required to list users.
            </p>
          ) : usersLoading ? (
            <DataTableSkeleton columns={3} />
          ) : usersError ? (
            <p className="text-sm text-destructive">Failed to load users.</p>
          ) : users.length === 0 ? (
            <p className="text-sm text-muted-foreground">No users returned.</p>
          ) : (
            <DataTable columns={userColumns} data={users} />
          )}
        </TabsContent>

        {/* ── Retention ── */}
        <TabsContent value="retention" className="mt-4">
          <Card>
            <CardHeader><CardTitle>Retention</CardTitle></CardHeader>
            <CardContent className="text-sm text-muted-foreground">
              Retention rules are managed via the policies API and tenant-level defaults in the backend.
              This tab is reserved for a future policy console.
            </CardContent>
          </Card>
        </TabsContent>
      </Tabs>
    </div>
  );
}
