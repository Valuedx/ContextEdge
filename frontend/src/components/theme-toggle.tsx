"use client";

import { Check, Monitor, Moon, Sun } from "lucide-react";

import { buttonVariants } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { cn } from "@/lib/utils";
import { useTheme } from "@/components/theme-provider";
import type { ThemeMode } from "@/lib/theme-storage";

const options: { value: ThemeMode; label: string; icon: typeof Sun }[] = [
  { value: "light", label: "Light", icon: Sun },
  { value: "dark", label: "Dark", icon: Moon },
  { value: "system", label: "System", icon: Monitor },
];

export function ThemeToggle() {
  const { theme, setTheme, resolvedIsDark } = useTheme();

  return (
    <DropdownMenu>
      <DropdownMenuTrigger
        className={cn(
          buttonVariants({ variant: "ghost", size: "icon" }),
          "relative"
        )}
        aria-label="Theme"
      >
        <Sun
          className={cn(
            "h-4 w-4 transition-all",
            resolvedIsDark ? "scale-0 rotate-90 opacity-0" : "scale-100 opacity-100"
          )}
        />
        <Moon
          className={cn(
            "absolute h-4 w-4 transition-all",
            resolvedIsDark ? "scale-100 opacity-100" : "scale-0 -rotate-90 opacity-0"
          )}
        />
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end" className="min-w-[10rem]">
        {options.map(({ value, label, icon: Icon }) => (
          <DropdownMenuItem key={value} onClick={() => setTheme(value)}>
            <Icon className="mr-2 h-4 w-4" />
            {label}
            {theme === value && <Check className="ml-auto h-4 w-4" />}
          </DropdownMenuItem>
        ))}
      </DropdownMenuContent>
    </DropdownMenu>
  );
}
