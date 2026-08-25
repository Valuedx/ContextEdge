"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { isAuthenticated } from "@/lib/auth";
import { SidebarNav } from "@/components/shell/sidebar-nav";
import { AppHeader } from "@/components/shell/app-header";
import { ScrollArea } from "@/components/ui/scroll-area";
import { BrandLockup } from "@/components/brand/brand";
import { cn } from "@/lib/utils";

export default function DashboardLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  const router = useRouter();
  const [sidebarCollapsed, setSidebarCollapsed] = useState(false);

  useEffect(() => {
    if (!isAuthenticated()) {
      router.replace("/login");
    }
  }, [router]);

  return (
    <div className="relative flex h-screen overflow-hidden bg-background">
      <aside
        className={cn(
          "glass-sidebar hidden shrink-0 border-r text-sidebar-foreground transition-[width] duration-200 md:block",
          sidebarCollapsed ? "w-16" : "w-64"
        )}
      >
        <div
          className={cn(
            "flex h-16 items-center border-b border-sidebar-border",
            sidebarCollapsed ? "justify-center px-2" : "px-5"
          )}
        >
          <BrandLockup
            surface="dark"
            variant={sidebarCollapsed ? "mark" : "full"}
          />
        </div>
        <ScrollArea className="h-[calc(100vh-4rem)]">
          <SidebarNav collapsed={sidebarCollapsed} />
        </ScrollArea>
      </aside>

      <div className="relative flex min-w-0 flex-1 flex-col overflow-hidden">
        <AppHeader
          sidebarCollapsed={sidebarCollapsed}
          onToggleSidebar={() => setSidebarCollapsed((value) => !value)}
        />
        <main className="relative flex-1 overflow-y-auto px-4 py-4 md:px-5 md:py-4">
          <div className="flex w-full flex-col gap-4">
            {children}
          </div>
        </main>
      </div>
    </div>
  );
}
