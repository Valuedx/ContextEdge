"use client";

import { useQuery } from "@tanstack/react-query";
import { ColumnDef } from "@tanstack/react-table";

import { PageHeader } from "@/components/common/page-header";
import { DataTable } from "@/components/common/data-table";
import { DataTableSkeleton } from "@/components/common/data-table-skeleton";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { api } from "@/lib/api";
import type { Domain, Tenant, User, Workspace } from "@/lib/types";
import { useAuthStore } from "@/lib/stores/auth-store";

function isTenantAdmin(roles: string[]) {
  return roles.includes("tenant_admin") || roles.includes("platform_super_admin");
}

const workspaceColumns: ColumnDef<Workspace>[] = [
  { accessorKey: "name", header: "Name" },
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
  {
    accessorKey: "status",
    header: "Status",
  },
];

export default function SettingsPage() {
  const tenantId = useAuthStore((s) => s.tenantId);
  const roles = useAuthStore((s) => s.roles);
  const admin = isTenantAdmin(roles);

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
      <PageHeader title="Settings" description="Tenant, workspaces, domains, and users for your organization." />

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

        <TabsContent value="general" className="mt-4">
          <Card>
            <CardHeader>
              <CardTitle>Tenant</CardTitle>
            </CardHeader>
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

        <TabsContent value="workspaces" className="mt-4">
          {wsLoading ? (
            <DataTableSkeleton columns={3} />
          ) : workspaces.length === 0 ? (
            <p className="text-sm text-muted-foreground">No workspaces yet.</p>
          ) : (
            <DataTable columns={workspaceColumns} data={workspaces} />
          )}
        </TabsContent>

        <TabsContent value="domains" className="mt-4">
          {domLoading ? (
            <DataTableSkeleton columns={3} />
          ) : domains.length === 0 ? (
            <p className="text-sm text-muted-foreground">No domains yet.</p>
          ) : (
            <DataTable columns={domainColumns} data={domains} />
          )}
        </TabsContent>

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

        <TabsContent value="retention" className="mt-4">
          <Card>
            <CardHeader>
              <CardTitle>Retention</CardTitle>
            </CardHeader>
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
