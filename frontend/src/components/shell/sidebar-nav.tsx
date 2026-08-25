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
import { hasRole } from "@/lib/roles";

interface NavItem {
  label: string;
  href: string;
  icon: React.ElementType;
  /** Item is shown only if the user holds at least one of these roles (or platform_super_admin). Omit to show to everyone. */
  requiredRoles?: string[];
}

const navItems: NavItem[] = [
  { label: "Overview", href: "/overview", icon: LayoutDashboard },
  { label: "Sources", href: "/sources", icon: Database },
  { label: "Sync Operations", href: "/sync", icon: RefreshCw },
  { label: "Evidence", href: "/evidence", icon: FileSearch },
  { label: "Sessions", href: "/sessions", icon: Layers },
  { label: "Runtime", href: "/runtime", icon: Radio },
  { label: "Reviewer Console", href: "/review", icon: CheckCircle2 },
  { label: "Execution", href: "/execution", icon: PlayCircle },
  { label: "Decisions", href: "/decisions", icon: Scale },
  { label: "Episodes", href: "/episodes", icon: GitBranch },
  { label: "Patterns", href: "/patterns", icon: Network },
  { label: "Playbooks", href: "/playbooks", icon: BookOpen },
  { label: "Neg. Knowledge", href: "/negative-knowledge", icon: BrainCircuit, requiredRoles: ["knowledge_manager", "domain_admin", "tenant_admin"] },
  { label: "Identities", href: "/identities", icon: Fingerprint, requiredRoles: ["knowledge_manager", "domain_admin", "tenant_admin"] },
  { label: "Correlations", href: "/correlations", icon: Share2, requiredRoles: ["knowledge_manager", "domain_admin", "tenant_admin"] },
  { label: "Suggestions", href: "/suggestions", icon: Sparkles, requiredRoles: ["knowledge_manager", "domain_admin", "tenant_admin"] },
  { label: "Graph Explorer", href: "/graph-explorer", icon: Waypoints },
  { label: "Contradictions", href: "/contradictions", icon: AlertTriangle },
  { label: "Drift", href: "/drift", icon: Activity },
  { label: "Evaluations", href: "/evaluations", icon: FlaskConical },
  { label: "Policies", href: "/policies", icon: Shield, requiredRoles: ["tenant_admin"] },
  { label: "Audit Log", href: "/audit", icon: ClipboardList, requiredRoles: ["tenant_admin", "domain_admin"] },
  { label: "LLM Cost", href: "/admin/cost", icon: DollarSign, requiredRoles: ["tenant_admin"] },
  { label: "Pipeline Health", href: "/admin/pipeline", icon: Gauge, requiredRoles: ["tenant_admin"] },
  { label: "Settings", href: "/settings", icon: Settings },
];

export function SidebarNav({ collapsed = false }: { collapsed?: boolean }) {
  const pathname = usePathname();
  const roles = useAuthStore((s) => s.roles);

  const visibleItems = navItems.filter((item) =>
    !item.requiredRoles || item.requiredRoles.some((r) => hasRole(roles, r))
  );

  return (
    <nav className={cn("flex flex-col gap-1.5 py-4", collapsed ? "px-2" : "px-3")}>
      {visibleItems.map((item) => {
        const isActive =
          pathname === item.href || pathname.startsWith(item.href + "/");
        const Icon = item.icon;
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
