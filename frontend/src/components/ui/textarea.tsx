import * as React from "react"

import { cn } from "@/lib/utils"

function Textarea({ className, ...props }: React.ComponentProps<"textarea">) {
  return (
    <textarea
      data-slot="textarea"
      className={cn(
        "flex field-sizing-content min-h-16 w-full rounded-lg border border-black/10 bg-white/80 px-2.5 py-2 text-base shadow-[inset_0_1px_0_rgba(255,255,255,0.9)] backdrop-blur-md transition-colors outline-none placeholder:text-muted-foreground focus-visible:border-violet-500/50 focus-visible:ring-3 focus-visible:ring-violet-500/20 disabled:cursor-not-allowed disabled:bg-black/[0.04] disabled:opacity-50 aria-invalid:border-destructive aria-invalid:ring-3 aria-invalid:ring-destructive/20 md:text-sm dark:border-input dark:bg-input/30 dark:shadow-none dark:focus-visible:border-ring dark:focus-visible:ring-ring/50 dark:disabled:bg-input/80 dark:aria-invalid:border-destructive/50 dark:aria-invalid:ring-destructive/40",
        className
      )}
      {...props}
    />
  )
}

export { Textarea }
