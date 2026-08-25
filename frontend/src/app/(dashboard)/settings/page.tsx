"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ColumnDef } from "@tanstack/react-table";
import { Plus, Loader2, ShieldCheck, Trash2, Search } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { toast } from "sonner";

import { TabAccessEditor } from "@/components/settings/tab-access-editor";
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
import { Badge } from "@/components/ui/badge";
import { api } from "@/lib/api";
import type { Domain, RoleBinding, Tenant, User, Workspace } from "@/lib/types";
import { useAuthStore } from "@/lib/stores/auth-store";
import {
  SETTINGS_TABS,
  TENANT_ASSIGNABLE_ROLES,
  assignableRoles,
  canSeeNav,
  isPlatformSuperAdmin,
  isTenantAdmin,
  roleLabel,
} from "@/lib/roles";
import { sidebarTabsForRoles, tabsGrantedByRole, type NavAccessPayload } from "@/lib/nav";

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

function ManageUserRolesDialog({ user, onClose }: { user: User; onClose: () => void }) {
  const qc = useQueryClient();
  const actorRoles = useAuthStore((s) => s.roles);
  const roleOptions = assignableRoles(actorRoles);
  const [role, setRole] = useState(roleOptions[0]?.value ?? "analyst");
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
      qc.invalidateQueries({ queryKey: ["users"] });
      toast.success("Role assigned. It takes effect at the user's next sign in.");
    },
    onError: (error: Error) => toast.error(error.message || "Failed to assign role"),
  });

  const remove = useMutation({
    mutationFn: (bindingId: string) =>
      api.delete(`/users/${user.id}/roles/${bindingId}`),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["user-roles", user.id] });
      qc.invalidateQueries({ queryKey: ["users"] });
      toast.success("Role removed. The user's current session remains valid until next sign in.");
    },
    onError: (error: Error) => toast.error(error.message || "Failed to remove role"),
  });

  const { data: navAccess } = useQuery({
    queryKey: ["nav-access"],
    queryFn: () => api.get<NavAccessPayload>("/nav-access"),
  });
  const assigned = new Set(bindings.map((binding) => binding.role));
  const isPlatformAdmin = assigned.has("platform_super_admin");
  const isOrgAdmin = isPlatformAdmin || assigned.has("tenant_admin");

  return (
    <DialogContent>
      <DialogHeader>
        <DialogTitle>Manage roles</DialogTitle>
      </DialogHeader>
      <div className="space-y-4">
        <div>
          <p className="font-medium">{user.display_name}</p>
          <p className="text-sm text-muted-foreground">{user.username}</p>
          {user.tenant_name && (
            <p className="text-xs text-muted-foreground">Tenant: {user.tenant_name}</p>
          )}
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
                  <span className="text-sm">{roleLabel(binding.role)}</span>
                  <Button
                    variant="ghost"
                    size="icon-sm"
                    title={`Remove ${roleLabel(binding.role)}`}
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
        {isOrgAdmin && (
          <p className="rounded-md border bg-muted/40 px-3 py-2 text-xs text-muted-foreground">
            {isPlatformAdmin
              ? "Platform super admin sees every sidebar tab, including tenant administration."
              : "Tenant administrator sees every sidebar tab. Other users only see tabs granted by the roles you assign below."}
          </p>
        )}
        <div className="space-y-2">
          <Label>Tabs this user can see</Label>
          {isLoading ? (
            <Skeleton className="h-16 w-full" />
          ) : (
            <div className="flex flex-wrap gap-1 rounded-md border px-3 py-2">
              {sidebarTabsForRoles([...assigned], navAccess?.access).map((item) => (
                <Badge key={item.href} variant="outline">
                  {item.label}
                </Badge>
              ))}
              {sidebarTabsForRoles([...assigned], navAccess?.access).length === 0 && (
                <span className="text-xs text-muted-foreground">No tabs until a role is assigned.</span>
              )}
            </div>
          )}
        </div>
        <div className="space-y-2">
          <Label htmlFor="new-user-role">Assign role</Label>
          <div className="flex gap-2">
            <Select value={role} onValueChange={(value) => setRole(value ?? "analyst")}>
              <SelectTrigger id="new-user-role" className="flex-1">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {roleOptions.map((option) => (
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
            Assign extra roles to give this user more tabs. Changes apply the next time they sign in.
          </p>
        </div>
      </div>
      <DialogFooter>
        <Button variant="outline" onClick={onClose}>Close</Button>
      </DialogFooter>
    </DialogContent>
  );
}

function TabAccessGuide() {
  const { data: navAccess } = useQuery({
    queryKey: ["nav-access"],
    queryFn: () => api.get<NavAccessPayload>("/nav-access"),
  });
  const roles = [
    { value: "tenant_admin", label: roleLabel("tenant_admin") },
    ...TENANT_ASSIGNABLE_ROLES.filter((role) => role.value !== "tenant_admin"),
  ];
  return (
    <Card>
      <CardHeader className="pb-2">
        <CardTitle className="text-base">Tab access for other users</CardTitle>
      </CardHeader>
      <CardContent className="space-y-3 text-sm">
        <p className="text-muted-foreground">
          Platform super admin changes which tabs each role sees in Settings → Tab access.
          Then assign that role to a user under Manage roles.
        </p>
        <div className="space-y-3">
          {roles.map((role) => (
            <div key={role.value}>
              <p className="font-medium">{role.label}</p>
              <p className="text-xs text-muted-foreground">
                {tabsGrantedByRole(role.value, navAccess?.access).join(" · ")}
              </p>
            </div>
          ))}
        </div>
      </CardContent>
    </Card>
  );
}

function NewUserDialog({
  tenants,
  defaultTenantId,
  canChooseTenant,
  currentTenantName,
  onClose,
}: {
  tenants: Tenant[];
  defaultTenantId: string;
  canChooseTenant: boolean;
  currentTenantName?: string;
  onClose: () => void;
}) {
  const qc = useQueryClient();
  const actorRoles = useAuthStore((s) => s.roles);
  const { data: navAccess } = useQuery({
    queryKey: ["nav-access"],
    queryFn: () => api.get<NavAccessPayload>("/nav-access"),
  });
  const roleOptions = assignableRoles(actorRoles).filter((option) => option.value !== "platform_super_admin");
  const [username, setUsername] = useState("");
  const [displayName, setDisplayName] = useState("");
  const [password, setPassword] = useState("");
  const [role, setRole] = useState("analyst");
  const [tenantId, setTenantId] = useState(defaultTenantId);

  const mut = useMutation({
    mutationFn: () =>
      api.post<User>("/users", {
        username: username.trim(),
        display_name: displayName.trim(),
        password,
        role,
        tenant_id: canChooseTenant ? tenantId : undefined,
      }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["users"] });
      toast.success(`User ${username.trim()} created`);
      onClose();
    },
    onError: (err: Error) => toast.error(err.message || "Failed to create user"),
  });

  return (
    <DialogContent>
      <DialogHeader>
        <DialogTitle>New user</DialogTitle>
      </DialogHeader>
      <div className="space-y-3">
        <div>
          <Label htmlFor="user-tenant">Tenant</Label>
          {canChooseTenant ? (
            <Select value={tenantId} onValueChange={(value) => setTenantId(value ?? defaultTenantId)}>
              <SelectTrigger id="user-tenant" className="mt-1">
                <SelectValue placeholder="Select tenant" />
              </SelectTrigger>
              <SelectContent>
                {tenants.map((tenant) => (
                  <SelectItem key={tenant.id} value={tenant.id}>
                    {tenant.name}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          ) : (
            <Input
              id="user-tenant"
              className="mt-1"
              value={currentTenantName || "Current tenant"}
              disabled
            />
          )}
        </div>
        <div>
          <Label htmlFor="user-username">Username</Label>
          <Input
            id="user-username"
            className="mt-1"
            type="text"
            autoCapitalize="none"
            autoCorrect="off"
            value={username}
            onChange={(e) => setUsername(e.target.value)}
            placeholder="analyst-ae"
          />
        </div>
        <div>
          <Label htmlFor="user-name">Display name</Label>
          <Input
            id="user-name"
            className="mt-1"
            value={displayName}
            onChange={(e) => setDisplayName(e.target.value)}
            placeholder="Alex Analyst"
          />
        </div>
        <div>
          <Label htmlFor="user-password">Password</Label>
          <Input
            id="user-password"
            className="mt-1"
            type="password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            placeholder="At least 8 characters"
          />
        </div>
        <div>
          <Label>Role</Label>
          <Select value={role} onValueChange={(value) => setRole(value ?? "analyst")}>
            <SelectTrigger className="mt-1">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {roleOptions.map((option) => (
                <SelectItem key={option.value} value={option.value}>
                  {option.label}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
          {role && (
            <p className="mt-1 text-xs text-muted-foreground">
              Tabs: {tabsGrantedByRole(role, navAccess?.access).join(", ")}
            </p>
          )}
        </div>
      </div>
      <DialogFooter>
        <Button variant="outline" onClick={onClose}>Cancel</Button>
        <Button
          disabled={
            !username.trim() ||
            !displayName.trim() ||
            password.length < 8 ||
            (canChooseTenant && !tenantId) ||
            mut.isPending
          }
          onClick={() => mut.mutate()}
        >
          {mut.isPending && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
          Create
        </Button>
      </DialogFooter>
    </DialogContent>
  );
}

function NewTenantDialog({ onClose }: { onClose: () => void }) {
  const qc = useQueryClient();
  const [name, setName] = useState("");
  const [slug, setSlug] = useState("");
  const [adminUsername, setAdminUsername] = useState("");
  const [adminName, setAdminName] = useState("");
  const [adminPassword, setAdminPassword] = useState("");

  const mut = useMutation({
    mutationFn: () =>
      api.post<Tenant>("/tenants", {
        name: name.trim(),
        slug: slug.trim(),
        admin_username: adminUsername.trim() || undefined,
        admin_display_name: adminName.trim() || undefined,
        admin_password: adminPassword || undefined,
      }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["tenants"] });
      toast.success(`Tenant "${name.trim()}" created`);
      onClose();
    },
    onError: (err: Error) => toast.error(err.message || "Failed to create tenant"),
  });

  return (
    <DialogContent>
      <DialogHeader>
        <DialogTitle>New tenant</DialogTitle>
      </DialogHeader>
      <div className="space-y-3">
        <div>
          <Label htmlFor="tenant-name">Name</Label>
          <Input id="tenant-name" className="mt-1" value={name} onChange={(e) => setName(e.target.value)} placeholder="AutomationEdge" />
        </div>
        <div>
          <Label htmlFor="tenant-slug">Slug</Label>
          <Input
            id="tenant-slug"
            className="mt-1 font-mono"
            value={slug}
            onChange={(e) => setSlug(e.target.value.toLowerCase().replace(/[^a-z0-9-]/g, ""))}
            placeholder="automationedge"
          />
        </div>
        <div>
          <Label htmlFor="tenant-admin-username">First tenant admin username (optional)</Label>
          <Input
            id="tenant-admin-username"
            className="mt-1"
            type="text"
            autoCapitalize="none"
            autoCorrect="off"
            value={adminUsername}
            onChange={(e) => setAdminUsername(e.target.value)}
            placeholder="tenantadmin-ae"
          />
        </div>
        <div>
          <Label htmlFor="tenant-admin-name">Admin display name</Label>
          <Input id="tenant-admin-name" className="mt-1" value={adminName} onChange={(e) => setAdminName(e.target.value)} />
        </div>
        <div>
          <Label htmlFor="tenant-admin-password">Admin password</Label>
          <Input id="tenant-admin-password" className="mt-1" type="password" value={adminPassword} onChange={(e) => setAdminPassword(e.target.value)} />
        </div>
      </div>
      <DialogFooter>
        <Button variant="outline" onClick={onClose}>Cancel</Button>
        <Button
          disabled={!name.trim() || !slug.trim() || mut.isPending}
          onClick={() => mut.mutate()}
        >
          {mut.isPending && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
          Create
        </Button>
      </DialogFooter>
    </DialogContent>
  );
}

function userColumns(onManageRoles: (user: User) => void): ColumnDef<User>[] {
  return [
    { accessorKey: "username", header: "Username" },
    { accessorKey: "display_name", header: "Name" },
    {
      accessorKey: "tenant_name",
      header: "Tenant",
      cell: ({ row }) => row.original.tenant_name || "—",
    },
    {
      id: "roles",
      header: "Roles",
      enableSorting: false,
      cell: ({ row }) => {
        const roles = row.original.roles ?? [];
        if (roles.length === 0) return <span className="text-muted-foreground">None</span>;
        return (
          <div className="flex flex-wrap gap-1">
            {roles.map((role) => (
              <Badge key={role} variant="outline">{roleLabel(role)}</Badge>
            ))}
          </div>
        );
      },
    },
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
  const superAdmin = isPlatformSuperAdmin(roles);
  const showGeneral = canSeeNav(roles, SETTINGS_TABS.general);
  const showTenants = canSeeNav(roles, SETTINGS_TABS.tenants);
  const showWorkspaces = canSeeNav(roles, SETTINGS_TABS.workspaces);
  const showDomains = canSeeNav(roles, SETTINGS_TABS.domains);
  const showUsers = canSeeNav(roles, SETTINGS_TABS.users);
  const showRetention = canSeeNav(roles, SETTINGS_TABS.retention);
  const showTabAccess = canSeeNav(roles, SETTINGS_TABS.tabAccess);
  const defaultTab = showTenants ? "tenants" : showUsers ? "users" : "general";
  const qc = useQueryClient();

  const [wsOpen, setWsOpen] = useState(false);
  const [domOpen, setDomOpen] = useState(false);
  const [userOpen, setUserOpen] = useState(false);
  const [tenantOpen, setTenantOpen] = useState(false);
  const [roleUser, setRoleUser] = useState<User | null>(null);
  const [tenantName, setTenantName] = useState("");

  const { data: tenant, isLoading: tenantLoading } = useQuery({
    queryKey: ["tenant", tenantId],
    queryFn: () => api.get<Tenant>(`/tenants/${tenantId}`),
    enabled: !!tenantId,
  });

  const { data: tenants = [] } = useQuery({
    queryKey: ["tenants"],
    queryFn: () => api.get<Tenant[]>("/tenants"),
    enabled: superAdmin,
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

  const [userSearch, setUserSearch] = useState("");

  const { data: users = [], isLoading: usersLoading, error: usersError } = useQuery({
    queryKey: ["users", tenantId],
    queryFn: () => api.get<User[]>("/users", { limit: "200" }),
    enabled: !!tenantId && admin,
  });

  const filteredUsers = useMemo(() => {
    const q = userSearch.trim().toLowerCase();
    if (!q) return users;
    return users.filter((row) => {
      const haystack = [
        row.username,
        row.display_name,
        row.tenant_name ?? "",
        row.status,
        ...(row.roles ?? []),
      ]
        .join(" ")
        .toLowerCase();
      return haystack.includes(q);
    });
  }, [users, userSearch]);

  useEffect(() => {
    if (tenant?.name) setTenantName(tenant.name);
  }, [tenant?.name]);

  const saveTenant = useMutation({
    mutationFn: () => api.patch<Tenant>(`/tenants/${tenantId}`, { name: tenantName.trim() }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["tenant", tenantId] });
      toast.success("Tenant updated");
    },
    onError: (err: Error) => toast.error(err.message || "Failed to update tenant"),
  });

  return (
    <div className="space-y-4">
      <PageHeader
        title="Settings"
        description="Tenant, workspaces, domains, and users for your organization."
      />

      {!showGeneral && !showTenants && !showUsers && (
        <p className="text-sm text-muted-foreground">
          Settings is available to tenant administrators and the platform super admin.
        </p>
      )}

      {(showGeneral || showTenants || showWorkspaces || showDomains || showUsers || showRetention || showTabAccess) && (
      <Tabs defaultValue={defaultTab}>
        <TabsList variant="glass">
          {showGeneral && <TabsTrigger value="general">General</TabsTrigger>}
          {showTenants && <TabsTrigger value="tenants">Tenants</TabsTrigger>}
          {showWorkspaces && <TabsTrigger value="workspaces">Workspaces</TabsTrigger>}
          {showDomains && <TabsTrigger value="domains">Domains</TabsTrigger>}
          {showUsers && <TabsTrigger value="users">Users</TabsTrigger>}
          {showTabAccess && <TabsTrigger value="tab-access">Tab access</TabsTrigger>}
          {showRetention && <TabsTrigger value="retention">Retention</TabsTrigger>}
        </TabsList>

        {showGeneral && (
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
                <div className="space-y-4">
                  <dl className="grid gap-x-6 gap-y-3 text-sm sm:grid-cols-2">
                    <div className="sm:col-span-2">
                      <dt className="text-muted-foreground">Name</dt>
                      {admin ? (
                        <dd className="mt-1 flex max-w-md items-center gap-2">
                          <Input
                            value={tenantName}
                            onChange={(e) => setTenantName(e.target.value)}
                          />
                          <Button
                            size="sm"
                            disabled={!tenantName.trim() || tenantName.trim() === tenant.name || saveTenant.isPending}
                            onClick={() => saveTenant.mutate()}
                          >
                            Save
                          </Button>
                        </dd>
                      ) : (
                        <dd className="font-medium">{tenant.name}</dd>
                      )}
                    </div>
                    <div>
                      <dt className="text-muted-foreground">Slug</dt>
                      <dd className="font-mono text-xs">{tenant.slug}</dd>
                    </div>
                    <div>
                      <dt className="text-muted-foreground">Status</dt>
                      <dd>{tenant.is_active ? "Active" : "Inactive"}</dd>
                    </div>
                  </dl>
                </div>
              ) : (
                <p className="text-sm text-muted-foreground">Could not load tenant.</p>
              )}
            </CardContent>
          </Card>
        </TabsContent>
        )}

        {showTenants && (
          <TabsContent value="tenants" className="space-y-3">
            <div className="flex items-center justify-between gap-2">
              <p className="text-sm font-medium text-foreground">Tenants</p>
              <Button size="sm" onClick={() => setTenantOpen(true)}>
                <Plus className="mr-2 h-4 w-4" />
                New Tenant
              </Button>
            </div>
            <Dialog open={tenantOpen} onOpenChange={setTenantOpen}>
              {tenantOpen && <NewTenantDialog onClose={() => setTenantOpen(false)} />}
            </Dialog>
            {tenants.length === 0 ? (
              <p className="text-sm text-muted-foreground">No tenants yet.</p>
            ) : (
              <div className="overflow-hidden rounded-lg border">
                <table className="w-full text-sm">
                  <thead className="bg-muted/50 text-left">
                    <tr>
                      <th className="px-3 py-2 font-medium">Name</th>
                      <th className="px-3 py-2 font-medium">Slug</th>
                      <th className="px-3 py-2 font-medium">Status</th>
                    </tr>
                  </thead>
                  <tbody>
                    {tenants.map((row) => (
                      <tr key={row.id} className="border-t">
                        <td className="px-3 py-2">{row.name}</td>
                        <td className="px-3 py-2 font-mono text-xs">{row.slug}</td>
                        <td className="px-3 py-2">{row.is_active ? "Active" : "Inactive"}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </TabsContent>
        )}

        {showWorkspaces && (
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
        )}

        {showDomains && (
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
        )}

        {showUsers && (
        <TabsContent value="users" className="space-y-3">
          <TabAccessGuide />
          <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
            <p className="text-sm font-medium text-foreground">Users</p>
            <div className="flex flex-1 items-center gap-2 sm:max-w-md sm:justify-end">
              <div className="relative min-w-0 flex-1">
                <Search className="pointer-events-none absolute left-2.5 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
                <Input
                  className="pl-8"
                  value={userSearch}
                  onChange={(e) => setUserSearch(e.target.value)}
                  placeholder="Search username, name, or tenant"
                  aria-label="Search users"
                />
              </div>
              <Button size="sm" onClick={() => setUserOpen(true)}>
                <Plus className="mr-2 h-4 w-4" />
                New User
              </Button>
            </div>
          </div>
          <Dialog open={userOpen} onOpenChange={setUserOpen}>
            {userOpen && tenantId && (
              <NewUserDialog
                tenants={tenants}
                defaultTenantId={tenantId}
                canChooseTenant={superAdmin}
                currentTenantName={tenant?.name}
                onClose={() => setUserOpen(false)}
              />
            )}
          </Dialog>
          {usersLoading ? (
            <DataTableSkeleton columns={6} />
          ) : usersError ? (
            <p className="text-sm text-destructive">Failed to load users.</p>
          ) : filteredUsers.length === 0 ? (
            <p className="text-sm text-muted-foreground">
              {users.length === 0 ? "No users returned." : "No users match that search."}
            </p>
          ) : (
            <DataTable columns={userColumns(setRoleUser)} data={filteredUsers} />
          )}
          <Dialog open={!!roleUser} onOpenChange={(open) => { if (!open) setRoleUser(null); }}>
            {roleUser && (
              <ManageUserRolesDialog user={roleUser} onClose={() => setRoleUser(null)} />
            )}
          </Dialog>
        </TabsContent>
        )}

        {showTabAccess && (
        <TabsContent value="tab-access" className="space-y-3">
          <TabAccessEditor />
        </TabsContent>
        )}

        {showRetention && (
        <TabsContent value="retention">
          <Card>
            <CardHeader><CardTitle>Retention</CardTitle></CardHeader>
            <CardContent className="text-sm text-muted-foreground">
              Retention rules are managed via the policies API and tenant-level defaults in the backend.
              This tab is reserved for a future policy console.
            </CardContent>
          </Card>
        </TabsContent>
        )}
      </Tabs>
      )}
    </div>
  );
}