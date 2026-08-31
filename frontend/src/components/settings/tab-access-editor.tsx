"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useMemo, useState } from "react";
import { Loader2 } from "lucide-react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";
import { api } from "@/lib/api";
import type { NavAccessPayload, RoleTabAccess } from "@/lib/nav";
import { NAV_ITEMS } from "@/lib/nav";
import { ROLE_LABELS, roleLabel } from "@/lib/roles";

export function TabAccessEditor() {
  const qc = useQueryClient();
  const { data, isLoading, error } = useQuery({
    queryKey: ["nav-access"],
    queryFn: () => api.get<NavAccessPayload>("/nav-access"),
  });
  const [role, setRole] = useState("analyst");
  const [draft, setDraft] = useState<RoleTabAccess>({});

  useEffect(() => {
    if (data?.access) setDraft(data.access);
  }, [data]);

  const selected = useMemo(() => new Set(draft[role] ?? []), [draft, role]);

  const save = useMutation({
    mutationFn: () => api.put<NavAccessPayload>("/nav-access", { access: draft }),
    onSuccess: (payload) => {
      qc.setQueryData(["nav-access"], payload);
      toast.success("Tab access saved. Users see the new tabs after they refresh or sign in again.");
    },
    onError: (err: Error) => toast.error(err.message || "Failed to save tab access"),
  });

  function toggle(href: string, checked: boolean) {
    setDraft((current) => {
      const next = new Set(current[role] ?? []);
      if (checked) next.add(href);
      else if (href !== "/overview") next.delete(href);
      return { ...current, [role]: [...next] };
    });
  }

  if (isLoading) {
    return (
      <Card>
        <CardHeader>
          <CardTitle>Tab access</CardTitle>
        </CardHeader>
        <CardContent className="space-y-2">
          <Skeleton className="h-10 w-64" />
          <Skeleton className="h-48 w-full" />
        </CardContent>
      </Card>
    );
  }

  if (error || !data) {
    return <p className="text-sm text-destructive">Failed to load tab access.</p>;
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle>Tab access by role</CardTitle>
      </CardHeader>
      <CardContent className="space-y-4">
        <p className="text-sm text-muted-foreground">
          Choose a role, tick the sidebar tabs that role should see, then save.
          Platform super admin always sees every tab. Overview cannot be removed.
        </p>
        <div>
          <Label htmlFor="tab-access-role">Role</Label>
          <Select value={role} onValueChange={(value) => setRole(value ?? "analyst")}>
            <SelectTrigger id="tab-access-role" className="mt-1 w-full max-w-sm">
              <SelectValue>
                {(value: string | null) => ROLE_LABELS[value ?? ""] ?? roleLabel(value ?? "")}
              </SelectValue>
            </SelectTrigger>
            <SelectContent>
              {data.roles.map((value) => (
                <SelectItem key={value} value={value}>
                  {ROLE_LABELS[value] ?? roleLabel(value)}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
        <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-3">
          {NAV_ITEMS.map((item) => (
            <label
              key={item.href}
              className="flex items-center gap-2 rounded-md border px-3 py-2 text-sm"
            >
              <input
                type="checkbox"
                className="h-4 w-4 accent-current"
                checked={selected.has(item.href)}
                disabled={item.href === "/overview"}
                onChange={(event) => toggle(item.href, event.target.checked)}
              />
              <span>{item.label}</span>
            </label>
          ))}
        </div>
        <Button disabled={save.isPending} onClick={() => save.mutate()}>
          {save.isPending && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
          Save tab access
        </Button>
      </CardContent>
    </Card>
  );
}
