"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { cn } from "@/lib/utils";
import {
  LayoutDashboard,
  Database,
  RefreshCw,
  FileSearch,
  GitBranch,
  Network,
  BookOpen,
  FlaskConical,
  Radio,
  Activity,
  Shield,
  ClipboardList,
  Settings,
  Layers,
  AlertTriangle,
  BrainCircuit,
  Fingerprint,
  Share2,
  PlayCircle,
  Scale,
  Waypoints,
  CheckCircle2,
  DollarSign,
  Sparkles,
  Gauge,
} from "lucide-react";
import { useAuthStore } from "@/lib/stores/auth-store";
import { NAV_ITEMS, canSeeSidebarItem, type NavAccessPayload } from "@/lib/nav";
import { api } from "@/lib/api";
import { useQuery } from "@tanstack/react-query";

const ICONS: Record<string, React.ElementType> = {
  "/overview": LayoutDashboard,
  "/sources": Database,
  "/sync": RefreshCw,
  "/evidence": FileSearch,
  "/sessions": Layers,
  "/runtime": Radio,
  "/review": CheckCircle2,
  "/execution": PlayCircle,
  "/decisions": Scale,
  "/episodes": GitBranch,
  "/patterns": Network,
  "/playbooks": BookOpen,
  "/negative-knowledge": BrainCircuit,
  "/identities": Fingerprint,
  "/correlations": Share2,
  "/suggestions": Sparkles,
  "/graph-explorer": Waypoints,
  "/contradictions": AlertTriangle,
  "/drift": Activity,
  "/evaluations": FlaskConical,
  "/policies": Shield,
  "/audit": ClipboardList,
  "/admin/cost": DollarSign,
  "/admin/pipeline": Gauge,
  "/settings": Settings,
};

export function SidebarNav({ collapsed = false }: { collapsed?: boolean }) {
  const pathname = usePathname();
  const roles = useAuthStore((s) => s.roles);

  const { data: navAccess } = useQuery({
    queryKey: ["nav-access"],
    queryFn: () => api.get<NavAccessPayload>("/nav-access"),
  });
  const visibleItems = NAV_ITEMS.filter((item) =>
    canSeeSidebarItem(roles, item, navAccess?.access),
  );

  return (
    <nav className={cn("flex flex-col gap-1.5 py-4", collapsed ? "px-2" : "px-3")}>
      {visibleItems.map((item) => {
        const isActive =
          pathname === item.href || pathname.startsWith(item.href + "/");
        const Icon = ICONS[item.href] ?? LayoutDashboard;
        return (
          <Link
            key={item.href}
            href={item.href}
            title={collapsed ? item.label : undefined}
            aria-label={collapsed ? item.label : undefined}
            className={cn(
              "flex items-center rounded-md py-2.5 text-sm font-medium",
              collapsed ? "justify-center px-2" : "gap-3 px-3",
              isActive ? "glass-nav-item-active" : "glass-nav-item"
            )}
          >
            <Icon className="h-4 w-4 shrink-0" />
            <span className={cn("truncate", collapsed && "sr-only")}>
              {item.label}
            </span>
          </Link>
        );
      })}
    </nav>
  );
}
