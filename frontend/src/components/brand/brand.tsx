import Image from "next/image";
import { cn } from "@/lib/utils";

type BrandLockupProps = {
  className?: string;
  variant?: "full" | "compact" | "mark";
  surface?: "default" | "light" | "dark";
};

const textBySurface = {
  default: "text-foreground",
  light: "text-slate-950",
  dark: "text-white",
};

const mutedBySurface = {
  default: "text-muted-foreground",
  light: "text-slate-500",
  dark: "text-slate-300",
};

export function BrandLockup({
  className,
  variant = "full",
  surface = "default",
}: BrandLockupProps) {
  const isMarkOnly = variant === "mark";
  const isCompact = variant === "compact";

  return (
    <div className={cn("flex min-w-0 items-center gap-3", className)}>
      <span
        className={cn(
          "flex shrink-0 items-center justify-center rounded-md border border-black/10 bg-white shadow-sm",
          isMarkOnly ? "h-9 w-11" : "h-9 w-12"
        )}
      >
        <Image
          src="/ae-mark.png"
          alt={isMarkOnly ? "ContextEdge" : ""}
          width={44}
          height={28}
          className="h-7 w-11 object-contain"
        />
      </span>

      {!isMarkOnly && (
        <span className="min-w-0 leading-none">
          {!isCompact && (
            <span
              className={cn(
                "block text-[10px] font-semibold uppercase tracking-[0.18em]",
                mutedBySurface[surface]
              )}
            >
              AutomationEdge
            </span>
          )}
          <span
            className={cn(
              "block truncate font-semibold tracking-tight",
              isCompact ? "text-sm" : "mt-1 text-base",
              textBySurface[surface]
            )}
          >
            ContextEdge
          </span>
        </span>
      )}
    </div>
  );
}
