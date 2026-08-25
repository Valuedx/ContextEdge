"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { PageHeader } from "@/components/common/page-header";
import { StatusBadge } from "@/components/common/status-badge";
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
import { Skeleton } from "@/components/ui/skeleton";
import { Textarea } from "@/components/ui/textarea";
import { api } from "@/lib/api";
import type { PoliciesOverview, PolicyType, TenantPolicyRecord } from "@/lib/types";
import { useAuthStore } from "@/lib/stores/auth-store";
import { isTenantAdmin } from "@/lib/roles";
import { ConfirmActionDialog } from "@/components/common/confirm-action-dialog";

const SECTIONS: {
  key: keyof PoliciesOverview;
  policyType: PolicyType;
  title: string;
  description: string;
}[] = [
  {
    key: "retention_policies",
    policyType: "retention",
    title: "Retention",
    description: "How long evidence and derived artifacts are kept before archive or deletion.",
  },
  {
    key: "classification_policies",
    policyType: "classification",
    title: "Classification",
    description: "Sensitivity labels, handling rules, and enforcement for retrieved context.",
  },
  {
    key: "access_policies",
    policyType: "access",
    title: "Access",
    description: "Who may query which domains, sources, and playbook tiers.",
  },
  {
    key: "approval_policies",
    policyType: "approval",
    title: "Approval",
    description: "Governance gates for playbook promotion and high-risk automation.",
  },
];

type PolicyFormState = {
  name: string;
  description: string;
  configText: string;
  is_active: boolean;
};

const emptyForm = (): PolicyFormState => ({
  name: "",
  description: "",
  configText: "{}",
  is_active: true,
});

function recordToForm(r: TenantPolicyRecord): PolicyFormState {
  return {
    name: r.name,
    description: r.description ?? "",
    configText: JSON.stringify(r.config ?? {}, null, 2),
    is_active: r.is_active,
  };
}

function PolicySection({
  title,
  description,
  policyType,
  items,
}: {
  title: string;
  description: string;
  policyType: PolicyType;
  items: TenantPolicyRecord[];
}) {
  const qc = useQueryClient();
  const [createOpen, setCreateOpen] = useState(false);
  const [editRecord, setEditRecord] = useState<TenantPolicyRecord | null>(null);
  const [deleteRecord, setDeleteRecord] = useState<TenantPolicyRecord | null>(null);
  const [form, setForm] = useState<PolicyFormState>(emptyForm);
  const [formError, setFormError] = useState<string | null>(null);

  const invalidate = () => qc.invalidateQueries({ queryKey: ["policies"] });

  const createMut = useMutation({
    mutationFn: async () => {
      let config: Record<string, unknown> = {};
      try {
        const parsed = JSON.parse(form.configText || "{}");
        if (parsed === null || typeof parsed !== "object" || Array.isArray(parsed)) {
          throw new Error("Config must be a JSON object");
        }
        config = parsed as Record<string, unknown>;
      } catch {
        throw new Error("Invalid JSON in config");
      }
      return api.post<TenantPolicyRecord>("/policies", {
        policy_type: policyType,
        name: form.name.trim(),
        description: form.description.trim() || null,
        config,
        is_active: form.is_active,
      });
    },
    onSuccess: () => {
      invalidate();
      setCreateOpen(false);
      setForm(emptyForm());
      setFormError(null);
    },
    onError: (e: Error) => setFormError(e.message),
  });

  const updateMut = useMutation({
    mutationFn: async () => {
      if (!editRecord) throw new Error("No policy selected");
      let config: Record<string, unknown> | undefined;
      try {
        const parsed = JSON.parse(form.configText || "{}");
        if (parsed === null || typeof parsed !== "object" || Array.isArray(parsed)) {
          throw new Error("Config must be a JSON object");
        }
        config = parsed as Record<string, unknown>;
      } catch {
        throw new Error("Invalid JSON in config");
      }
      return api.patch<TenantPolicyRecord>(`/policies/${editRecord.id}`, {
        name: form.name.trim(),
        description: form.description.trim() || null,
        config,
        is_active: form.is_active,
      });
    },
    onSuccess: () => {
      invalidate();
      setEditRecord(null);
      setForm(emptyForm());
      setFormError(null);
    },
    onError: (e: Error) => setFormError(e.message),
  });

  const deleteMut = useMutation({
    mutationFn: (id: string) => api.delete(`/policies/${id}`),
    onSuccess: () => {
      invalidate();
      setDeleteRecord(null);
    },
  });

  const openCreate = () => {
    setForm(emptyForm());
    setFormError(null);
    setCreateOpen(true);
  };

  const openEdit = (r: TenantPolicyRecord) => {
    setForm(recordToForm(r));
    setFormError(null);
    setEditRecord(r);
  };

  const dialogBusy = createMut.isPending || updateMut.isPending;

  return (
    <Card>
      <CardHeader className="flex flex-row flex-wrap items-start justify-between gap-3 space-y-0">
        <div>
          <CardTitle className="text-base">{title}</CardTitle>
          <p className="mt-1 text-sm text-muted-foreground">{description}</p>
        </div>
        <Button size="sm" variant="outline" onClick={openCreate}>
          Add policy
        </Button>
      </CardHeader>
      <CardContent className="space-y-3">
        {items.length === 0 ? (
          <p className="text-sm text-muted-foreground">No policies in this bucket yet.</p>
        ) : (
          <ul className="space-y-3">
            {items.map((item) => (
              <li
                key={item.id}
                className="rounded-lg border bg-card p-3 shadow-sm"
              >
                <div className="flex flex-wrap items-start justify-between gap-2">
                  <div className="min-w-0">
                    <span className="font-medium">{item.name}</span>
                    {item.description && (
                      <p className="mt-1 text-sm text-muted-foreground">{item.description}</p>
                    )}
                    <div className="mt-2 flex flex-wrap gap-2">
                      <StatusBadge status={item.is_active ? "active" : "inactive"} />
                      <span className="font-mono text-xs text-muted-foreground">{item.id}</span>
                    </div>
                  </div>
                  <div className="flex shrink-0 gap-2">
                    <Button size="sm" variant="ghost" onClick={() => openEdit(item)}>
                      Edit
                    </Button>
                    <Button
                      size="sm"
                      variant="ghost"
                      className="text-destructive hover:text-destructive"
                      disabled={deleteMut.isPending}
                      onClick={() => setDeleteRecord(item)}
                    >
                      Delete
                    </Button>
                  </div>
                </div>
                <pre className="mt-3 max-h-40 overflow-auto rounded-md bg-muted p-2 text-xs">
                  {JSON.stringify(item.config, null, 2)}
                </pre>
              </li>
            ))}
          </ul>
        )}
      </CardContent>

      <ConfirmActionDialog
        open={!!deleteRecord}
        onOpenChange={(open) => {
          if (!open) setDeleteRecord(null);
        }}
        title="Delete policy?"
        description={
          deleteRecord
            ? `This will permanently delete "${deleteRecord.name}".`
            : "This policy will be permanently deleted."
        }
        confirmLabel="Delete policy"
        isPending={deleteMut.isPending}
        onConfirm={() => {
          if (deleteRecord) deleteMut.mutate(deleteRecord.id);
        }}
      />

      <Dialog open={createOpen} onOpenChange={setCreateOpen}>
        <DialogContent className="max-w-lg">
          <DialogHeader>
            <DialogTitle>Add {title.toLowerCase()} policy</DialogTitle>
          </DialogHeader>
          <div className="space-y-3">
            <div>
              <Label htmlFor={`name-${policyType}`}>Name</Label>
              <Input
                id={`name-${policyType}`}
                value={form.name}
                onChange={(e) => setForm((f) => ({ ...f, name: e.target.value }))}
              />
            </div>
            <div>
              <Label htmlFor={`desc-${policyType}`}>Description (optional)</Label>
              <Input
                id={`desc-${policyType}`}
                value={form.description}
                onChange={(e) => setForm((f) => ({ ...f, description: e.target.value }))}
              />
            </div>
            <div>
              <Label htmlFor={`cfg-${policyType}`}>Config (JSON object)</Label>
              <Textarea
                id={`cfg-${policyType}`}
                className="min-h-[120px] font-mono text-xs"
                value={form.configText}
                onChange={(e) => setForm((f) => ({ ...f, configText: e.target.value }))}
              />
            </div>
            <label className="flex items-center gap-2 text-sm">
              <input
                type="checkbox"
                checked={form.is_active}
                onChange={(e) => setForm((f) => ({ ...f, is_active: e.target.checked }))}
              />
              Active
            </label>
            {formError && <p className="text-sm text-destructive">{formError}</p>}
          </div>
          <DialogFooter>
            <Button
              disabled={!form.name.trim() || dialogBusy}
              onClick={() => {
                setFormError(null);
                createMut.mutate();
              }}
            >
              Create
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <Dialog open={!!editRecord} onOpenChange={(o) => !o && setEditRecord(null)}>
        <DialogContent className="max-w-lg">
          <DialogHeader>
            <DialogTitle>Edit policy</DialogTitle>
          </DialogHeader>
          <div className="space-y-3">
            <div>
              <Label htmlFor={`edit-name-${policyType}`}>Name</Label>
              <Input
                id={`edit-name-${policyType}`}
                value={form.name}
                onChange={(e) => setForm((f) => ({ ...f, name: e.target.value }))}
              />
            </div>
            <div>
              <Label htmlFor={`edit-desc-${policyType}`}>Description (optional)</Label>
              <Input
                id={`edit-desc-${policyType}`}
                value={form.description}
                onChange={(e) => setForm((f) => ({ ...f, description: e.target.value }))}
              />
            </div>
            <div>
              <Label htmlFor={`edit-cfg-${policyType}`}>Config (JSON object)</Label>
              <Textarea
                id={`edit-cfg-${policyType}`}
                className="min-h-[120px] font-mono text-xs"
                value={form.configText}
                onChange={(e) => setForm((f) => ({ ...f, configText: e.target.value }))}
              />
            </div>
            <label className="flex items-center gap-2 text-sm">
              <input
                type="checkbox"
                checked={form.is_active}
                onChange={(e) => setForm((f) => ({ ...f, is_active: e.target.checked }))}
              />
              Active
            </label>
            {formError && <p className="text-sm text-destructive">{formError}</p>}
          </div>
          <DialogFooter>
            <Button
              disabled={!form.name.trim() || dialogBusy}
              onClick={() => {
                setFormError(null);
                updateMut.mutate();
              }}
            >
              Save
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </Card>
  );
}

export default function PoliciesPage() {
  const roles = useAuthStore((s) => s.roles);
  const admin = isTenantAdmin(roles);

  const { data, isLoading, error } = useQuery({
    queryKey: ["policies"],
    queryFn: () => api.get<PoliciesOverview>("/policies"),
    enabled: admin,
  });

  return (
    <div className="space-y-6">
      <PageHeader
        title="Policies"
        description="Retention, classification, access rules, and approval gates for governed retrieval. Policies are stored per tenant and can be attached to sources and evidence as those flows mature."
      />

      {!admin && (
        <p className="text-sm text-muted-foreground">
          Tenant admin role is required to view organization policies.
        </p>
      )}

      {admin && error && (
        <p className="text-sm text-destructive">
          {String((error as Error).message || "Failed to load policies")}
        </p>
      )}

      {admin && isLoading && (
        <div className="grid gap-4 lg:grid-cols-2">
          {Array.from({ length: 4 }).map((_, i) => (
            <Card key={i}>
              <CardHeader>
                <Skeleton className="h-5 w-32" />
                <Skeleton className="mt-2 h-10 w-full" />
              </CardHeader>
              <CardContent>
                <Skeleton className="h-16 w-full" />
              </CardContent>
            </Card>
          ))}
        </div>
      )}

      {admin && data && !isLoading && (
        <div className="grid gap-4 lg:grid-cols-2">
          {SECTIONS.map(({ key, policyType, title, description }) => (
            <PolicySection
              key={key}
              title={title}
              description={description}
              policyType={policyType}
              items={data[key]}
            />
          ))}
        </div>
      )}
    </div>
  );
}
