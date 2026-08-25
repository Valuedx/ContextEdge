"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ColumnDef } from "@tanstack/react-table";
import { Plus, Loader2, ShieldCheck, Trash2 } from "lucide-react";
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
import type { Domain, RoleBinding, Tenant, User, Workspace } from "@/lib/types";
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
    onError: (err: Error) => toast.error(err.message || "Failed to create workspace"),
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
    onError: (err: Error) => toast.error(err.message || "Failed to create domain"),
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

const ASSIGNABLE_ROLES = [
  { value: "analyst", label: "Analyst" },
  { value: "knowledge_manager", label: "Knowledge manager" },
  { value: "playbook_reviewer", label: "Playbook reviewer" },
  { value: "domain_admin", label: "Domain administrator" },
  { value: "tenant_admin", label: "Tenant administrator" },
] as const;

function ManageUserRolesDialog({ user, onClose }: { user: User; onClose: () => void }) {
  const qc = useQueryClient();
  const [role, setRole] = useState("playbook_reviewer");
  const { data: bindings = [], isLoading } = useQuery<RoleBinding[]>({
    queryKey: ["user-roles", user.id],
    queryFn: () => api.get(`/users/${user.id}/roles`),
  });

  const assign = useMutation({
    mutationFn: () =>
      api.post<RoleBinding>(`/users/${user.id}/roles`, {
        user_id: user.id,
        role,
        scope_type: "tenant",
        scope_id: null,
      }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["user-roles", user.id] });
      toast.success("Role assigned. It takes effect at the user's next sign in.");
    },
    onError: (error: Error) => toast.error(error.message || "Failed to assign role"),
  });

  const remove = useMutation({
    mutationFn: (bindingId: string) =>
      api.delete(`/users/${user.id}/roles/${bindingId}`),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["user-roles", user.id] });
      toast.success("Role removed. The user's current session remains valid until next sign in.");
    },
    onError: (error: Error) => toast.error(error.message || "Failed to remove role"),
  });

  const assigned = new Set(bindings.map((binding) => binding.role));

  return (
    <DialogContent>
      <DialogHeader>
        <DialogTitle>Manage roles</DialogTitle>
      </DialogHeader>
      <div className="space-y-4">
        <div>
          <p className="font-medium">{user.display_name}</p>
          <p className="text-sm text-muted-foreground">{user.email}</p>
        </div>
        <div className="space-y-2">
          <Label>Assigned roles</Label>
          {isLoading ? (
            <Skeleton className="h-10 w-full" />
          ) : bindings.length === 0 ? (
            <p className="rounded-md border border-dashed px-3 py-4 text-sm text-muted-foreground">
              No roles assigned.
            </p>
          ) : (
            <div className="divide-y rounded-md border">
              {bindings.map((binding) => (
                <div key={binding.id} className="flex items-center justify-between gap-3 px-3 py-2">
                  <span className="text-sm capitalize">{binding.role.replaceAll("_", " ")}</span>
                  <Button
                    variant="ghost"
                    size="icon-sm"
                    title={`Remove ${binding.role.replaceAll("_", " ")}`}
                    disabled={remove.isPending}
                    onClick={() => remove.mutate(binding.id)}
                  >
                    <Trash2 className="h-4 w-4" />
                  </Button>
                </div>
              ))}
            </div>
          )}
        </div>
        <div className="space-y-2">
          <Label htmlFor="new-user-role">Assign role</Label>
          <div className="flex gap-2">
            <Select value={role} onValueChange={(value) => setRole(value ?? "playbook_reviewer")}>
              <SelectTrigger id="new-user-role" className="flex-1">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {ASSIGNABLE_ROLES.map((option) => (
                  <SelectItem key={option.value} value={option.value} disabled={assigned.has(option.value)}>
                    {option.label}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
            <Button disabled={assign.isPending || assigned.has(role)} onClick={() => assign.mutate()}>
              {assign.isPending ? (
                <Loader2 className="h-4 w-4 animate-spin" />
              ) : (
                <ShieldCheck className="h-4 w-4" />
              )}
              Assign
            </Button>
          </div>
          <p className="text-xs text-muted-foreground">
            Playbook approval requires the Playbook reviewer role. Role changes are included in a new token at the next sign in.
          </p>
        </div>
      </div>
      <DialogFooter>
        <Button variant="outline" onClick={onClose}>Close</Button>
      </DialogFooter>
    </DialogContent>
  );
}

function userColumns(onManageRoles: (user: User) => void): ColumnDef<User>[] {
  return [
    { accessorKey: "email", header: "Email" },
    { accessorKey: "display_name", header: "Name" },
    { accessorKey: "status", header: "Status" },
    {
      id: "actions",
      header: "Access",
      enableSorting: false,
      cell: ({ row }) => (
        <Button variant="outline" size="sm" onClick={() => onManageRoles(row.original)}>
          <ShieldCheck className="h-4 w-4" />
          Manage roles
        </Button>
      ),
    },
  ];
}

// ── Page ──────────────────────────────────────────────────────────────────

export default function SettingsPage() {
  const tenantId = useAuthStore((s) => s.tenantId);
  const roles = useAuthStore((s) => s.roles);
  const admin = isTenantAdmin(roles);

  const [wsOpen, setWsOpen] = useState(false);
  const [domOpen, setDomOpen] = useState(false);
  const [roleUser, setRoleUser] = useState<User | null>(null);

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
    <div className="space-y-4">
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
        <TabsContent value="general">
          <Card>
            <CardHeader><CardTitle>Tenant</CardTitle></CardHeader>
            <CardContent>
              {tenantLoading ? (
                <div className="grid gap-3 sm:grid-cols-2">
                  {Array.from({ length: 4 }).map((_, i) => (
                    <div key={i} className="space-y-1.5">
                      <Skeleton className="h-3 w-16" />
                      <Skeleton className="h-4 w-[min(100%,14rem)]" />
                    </div>
                  ))}
                </div>
              ) : tenant ? (
                <dl className="grid gap-x-6 gap-y-3 text-sm sm:grid-cols-2">
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
                  <div className="sm:col-span-2">
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
        <TabsContent value="workspaces" className="space-y-3">
          {admin && (
            <div className="flex items-center justify-between gap-2">
              <p className="text-sm font-medium text-foreground">Workspaces</p>
              <Button size="sm" onClick={() => setWsOpen(true)}>
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
        <TabsContent value="domains" className="space-y-3">
          {admin && (
            <div className="flex items-center justify-between gap-2">
              <p className="text-sm font-medium text-foreground">Domains</p>
              <Button size="sm" onClick={() => setDomOpen(true)}>
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
        <TabsContent value="users">
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
            <DataTable columns={userColumns(setRoleUser)} data={users} />
          )}
          <Dialog open={!!roleUser} onOpenChange={(open) => { if (!open) setRoleUser(null); }}>
            {roleUser && (
              <ManageUserRolesDialog user={roleUser} onClose={() => setRoleUser(null)} />
            )}
          </Dialog>
        </TabsContent>

        {/* ── Retention ── */}
        <TabsContent value="retention">
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
