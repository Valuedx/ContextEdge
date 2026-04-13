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
} from "lucide-react";

const navItems = [
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
  { label: "Contradictions", href: "/contradictions", icon: AlertTriangle },
  { label: "Neg. Knowledge", href: "/negative-knowledge", icon: BrainCircuit },
  { label: "Identities", href: "/identities", icon: Fingerprint },
  { label: "Correlations", href: "/correlations", icon: Share2 },
  { label: "Drift", href: "/drift", icon: Activity },
  { label: "Policies", href: "/policies", icon: Shield },
  { label: "Audit Log", href: "/audit", icon: ClipboardList },
  { label: "Settings", href: "/settings", icon: Settings },
];

export function SidebarNav() {
  const pathname = usePathname();

  return (
    <nav className="flex flex-col gap-1 px-3 py-4">
      {navItems.map((item) => {
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
