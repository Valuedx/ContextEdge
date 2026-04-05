import * as React from "react";

import { cn } from "@/lib/utils";

function Skeleton({
  className,
  ...props
}: React.ComponentProps<"div">) {
  return (
    <div
      data-slot="skeleton"
      className={cn(
        "relative overflow-hidden rounded-lg bg-muted/60 dark:bg-white/10",
        "after:pointer-events-none after:absolute after:inset-0 after:-translate-x-full after:animate-[shimmer_1.8s_ease-in-out_infinite] after:bg-gradient-to-r after:from-transparent after:via-foreground/20 after:to-transparent dark:after:via-white/25",
        className
      )}
      {...props}
    />
  );
}

export { Skeleton };
