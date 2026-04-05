"use client";

import { useEffect } from "react";
import { useRouter } from "next/navigation";
import { isAuthenticated } from "@/lib/auth";
import { SidebarNav } from "@/components/shell/sidebar-nav";
import { AppHeader } from "@/components/shell/app-header";
import { ScrollArea } from "@/components/ui/scroll-area";

export default function DashboardLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  const router = useRouter();

  useEffect(() => {
    if (!isAuthenticated()) {
      router.replace("/login");
    }
  }, [router]);

  return (
    <div className="relative flex h-screen overflow-hidden">
      <aside className="glass-sidebar hidden w-64 shrink-0 border-r md:block">
        <div className="flex h-14 items-center border-b border-white/10 px-5">
          <span className="bg-gradient-to-r from-violet-700 via-indigo-600 to-cyan-600 bg-clip-text text-sm font-semibold tracking-tight text-transparent dark:from-violet-200 dark:via-white dark:to-cyan-200">
            ContextEdge
          </span>
        </div>
        <ScrollArea className="h-[calc(100vh-3.5rem)]">
          <SidebarNav />
        </ScrollArea>
      </aside>

      <div className="relative flex min-w-0 flex-1 flex-col overflow-hidden">
        <AppHeader />
        <main className="relative flex-1 overflow-y-auto p-5 md:p-8">
          <div className="mx-auto flex w-full max-w-[1600px] flex-col gap-8">
            {children}
          </div>
        </main>
      </div>
    </div>
  );
}
