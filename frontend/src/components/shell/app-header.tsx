"use client";

import {
  Bell,
  Building2,
  CheckCheck,
  ChevronsLeft,
  ChevronsRight,
  LogOut,
  User,
} from "lucide-react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Button, buttonVariants } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { ThemeToggle } from "@/components/theme-toggle";
import { useAuthStore } from "@/lib/stores/auth-store";
import { logout } from "@/lib/auth";
import { api } from "@/lib/api";
import type { Notification, Tenant } from "@/lib/types";
import { BrandLockup } from "@/components/brand/brand";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
} from "@/components/ui/select";
import { isPlatformSuperAdmin } from "@/lib/roles";

function TenantSwitcher() {
  const qc = useQueryClient();
  const tenantId = useAuthStore((s) => s.tenantId);
  const setTenantContext = useAuthStore((s) => s.setTenantContext);
  const { data: tenants = [] } = useQuery({
    queryKey: ["tenants"],
    queryFn: () => api.get<Tenant[]>("/tenants"),
  });
  const selected = tenants.find((tenant) => tenant.id === tenantId);

  if (tenants.length === 0) return null;

  return (
    <div className="flex min-w-0 items-center gap-2">
      <Building2 className="hidden h-4 w-4 shrink-0 text-muted-foreground sm:block" />
      <Select
        value={tenantId ?? undefined}
        onValueChange={(value) => {
          if (!value || value === tenantId) return;
          setTenantContext(value);
          void qc.invalidateQueries();
        }}
      >
        <SelectTrigger
          size="sm"
          className="h-9 max-w-[220px]"
          aria-label="Switch tenant"
        >
          <span className="truncate">{selected?.name ?? "Select tenant"}</span>
        </SelectTrigger>
        <SelectContent align="end">
          {tenants.map((tenant) => (
            <SelectItem key={tenant.id} value={tenant.id}>
              {tenant.name}
            </SelectItem>
          ))}
        </SelectContent>
      </Select>
    </div>
  );
}

function NotificationBell() {
  const qc = useQueryClient();

  const { data: notifications = [] } = useQuery<Notification[]>({
    queryKey: ["notifications"],
    queryFn: () => api.get("/notifications", { unread_only: "true", limit: "20" }),
    refetchInterval: 60_000,
  });

  const unread = notifications.filter((n) => !n.is_read);

  const markRead = useMutation({
    mutationFn: (id: string) => api.patch(`/notifications/${id}/read`, { is_read: true }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["notifications"] }),
  });

  const markAllRead = async () => {
    for (const n of unread) {
      await markRead.mutateAsync(n.id);
    }
  };

  return (
    <DropdownMenu>
      <DropdownMenuTrigger
        aria-label="Open notifications"
        className={cn(buttonVariants({ variant: "ghost", size: "icon" }), "relative")}
      >
        <Bell className="h-4 w-4" />
        {unread.length > 0 && (
          <span className="absolute -right-0.5 -top-0.5 flex h-4 w-4 items-center justify-center rounded-full bg-destructive text-[10px] font-bold text-destructive-foreground">
            {unread.length > 9 ? "9+" : unread.length}
          </span>
        )}
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end" className="w-80">
        <div className="flex items-center justify-between px-2 py-1.5">
          <span className="text-sm font-medium">Notifications</span>
          {unread.length > 0 && (
            <Button variant="ghost" size="sm" className="h-6 px-2 text-xs" onClick={markAllRead}>
              <CheckCheck className="mr-1 h-3 w-3" />
              Mark all read
            </Button>
          )}
        </div>
        <DropdownMenuSeparator />
        {notifications.length === 0 ? (
          <div className="px-2 py-4 text-center text-xs text-muted-foreground">
            No notifications
          </div>
        ) : (
          <div className="max-h-72 overflow-y-auto">
            {notifications.map((n) => (
              <DropdownMenuItem
                key={n.id}
                className={cn("flex flex-col items-start gap-0.5 px-2 py-2", !n.is_read && "bg-muted/50")}
                onClick={() => !n.is_read && markRead.mutate(n.id)}
              >
                <div className="flex w-full items-center justify-between gap-2">
                  <span className="text-xs font-medium truncate">{n.title}</span>
                  {!n.is_read && (
                    <span className="h-1.5 w-1.5 shrink-0 rounded-full bg-primary" />
                  )}
                </div>
                <span className="text-xs text-muted-foreground line-clamp-2">{n.body}</span>
                <span className="text-[10px] text-muted-foreground">
                  {new Date(n.created_at).toLocaleString()}
                </span>
              </DropdownMenuItem>
            ))}
          </div>
        )}
      </DropdownMenuContent>
    </DropdownMenu>
  );
}

interface AppHeaderProps {
  sidebarCollapsed?: boolean;
  onToggleSidebar?: () => void;
}

export function AppHeader({
  sidebarCollapsed = false,
  onToggleSidebar,
}: AppHeaderProps) {
  const email = useAuthStore((s) => s.email);
  const roles = useAuthStore((s) => s.roles);
  const ToggleIcon = sidebarCollapsed ? ChevronsRight : ChevronsLeft;
  const showTenantSwitcher = isPlatformSuperAdmin(roles);

  return (
    <header className="sticky top-0 z-30 flex h-16 items-center justify-between border-b bg-card px-5 shadow-sm md:px-7">
      <div className="flex min-w-0 items-center gap-3">
        {onToggleSidebar && (
          <Button
            type="button"
            variant="outline"
            size="icon"
            aria-label={sidebarCollapsed ? "Expand sidebar" : "Collapse sidebar"}
            title={sidebarCollapsed ? "Expand sidebar" : "Collapse sidebar"}
            onClick={onToggleSidebar}
            className="hidden md:inline-flex"
          >
            <ToggleIcon className="h-4 w-4" />
          </Button>
        )}
        <div className="md:hidden">
          <BrandLockup variant="compact" />
        </div>
      </div>

      <div className="flex items-center gap-2">
        {showTenantSwitcher && <TenantSwitcher />}
        <ThemeToggle />
        <NotificationBell />

        <DropdownMenu>
          <DropdownMenuTrigger
            aria-label="Open account menu"
            className={cn(buttonVariants({ variant: "ghost", size: "icon" }))}
          >
            <User className="h-4 w-4" />
          </DropdownMenuTrigger>
          <DropdownMenuContent align="end" className="w-64">
            <div className="truncate px-2 py-1.5 text-sm text-muted-foreground" title={email ?? undefined}>
              {email}
            </div>
            <DropdownMenuSeparator />
            <DropdownMenuItem onClick={logout}>
              <LogOut className="mr-2 h-4 w-4" />
              Sign out
            </DropdownMenuItem>
          </DropdownMenuContent>
        </DropdownMenu>
      </div>
    </header>
  );
}
