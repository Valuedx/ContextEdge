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
  { label: "Episodes", href: "/episodes", icon: GitBranch },
  { label: "Patterns", href: "/patterns", icon: Network },
  { label: "Playbooks", href: "/playbooks", icon: BookOpen },
  { label: "Sessions", href: "/sessions", icon: Layers },
  { label: "Evaluations", href: "/evaluations", icon: FlaskConical },
  { label: "Runtime", href: "/runtime", icon: Radio },
  { label: "Execution", href: "/execution", icon: PlayCircle },
  { label: "Decisions", href: "/decisions", icon: Scale },
  { label: "Contradictions", href: "/contradictions", icon: AlertTriangle },
  { label: "Neg. Knowledge", href: "/negative-knowledge", icon: BrainCircuit, requiredRoles: ["knowledge_manager", "domain_admin", "tenant_admin"] },
  { label: "Identities", href: "/identities", icon: Fingerprint, requiredRoles: ["knowledge_manager", "domain_admin", "tenant_admin"] },
  { label: "Correlations", href: "/correlations", icon: Share2, requiredRoles: ["knowledge_manager", "domain_admin", "tenant_admin"] },
  { label: "Graph Explorer", href: "/graph-explorer", icon: Waypoints },
  { label: "Drift", href: "/drift", icon: Activity },
  { label: "Policies", href: "/policies", icon: Shield, requiredRoles: ["tenant_admin"] },
  { label: "Audit Log", href: "/audit", icon: ClipboardList, requiredRoles: ["tenant_admin", "domain_admin"] },
  { label: "Settings", href: "/settings", icon: Settings },
];

export function SidebarNav() {
  const pathname = usePathname();
  const roles = useAuthStore((s) => s.roles);

  const visibleItems = navItems.filter((item) =>
    !item.requiredRoles || item.requiredRoles.some((r) => hasRole(roles, r))
  );

  return (
    <nav className="flex flex-col gap-1 px-3 py-4">
      {visibleItems.map((item) => {
        const isActive =
          pathname === item.href || pathname.startsWith(item.href + "/");
        const Icon = item.icon;
        return (
          <Link
            key={item.href}
            href={item.href}
            className={cn(
              "flex items-center gap-3 rounded-xl px-3 py-2.5 text-sm font-medium",
              isActive ? "glass-nav-item-active text-foreground" : "glass-nav-item rounded-xl"
            )}
          >
            <Icon className="h-4 w-4 shrink-0" />
            {item.label}
          </Link>
        );
      })}
    </nav>
  );
}
