"use client";

import { Bell, LogOut, User } from "lucide-react";
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

export function AppHeader() {
  const email = useAuthStore((s) => s.email);

  return (
    <header className="glass-header sticky top-0 z-30 flex h-14 items-center justify-between border-b px-5 md:px-8">
      <div className="flex items-center gap-4 md:hidden">
        <h1 className="bg-gradient-to-r from-violet-700 to-cyan-600 bg-clip-text text-lg font-semibold tracking-tight text-transparent dark:from-violet-200 dark:to-cyan-200">
          ContextEdge
        </h1>
      </div>
      <div className="hidden flex-1 md:block" aria-hidden />

      <div className="flex items-center gap-2">
        <ThemeToggle />
        <Button variant="ghost" size="icon" className="relative">
          <Bell className="h-4 w-4" />
        </Button>

        <DropdownMenu>
          <DropdownMenuTrigger
            className={cn(buttonVariants({ variant: "ghost", size: "icon" }))}
          >
            <User className="h-4 w-4" />
          </DropdownMenuTrigger>
          <DropdownMenuContent align="end">
            <div className="px-2 py-1.5 text-sm text-muted-foreground">
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
