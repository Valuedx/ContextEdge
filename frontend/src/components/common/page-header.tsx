import { cn } from "@/lib/utils";

interface PageHeaderProps {
  title: string;
  description?: string;
  actions?: React.ReactNode;
  className?: string;
}

export function PageHeader({ title, description, actions, className }: PageHeaderProps) {
  return (
    <div
      className={cn(
        "glass-panel flex flex-col gap-4 p-5 sm:flex-row sm:items-start sm:justify-between",
        className
      )}
    >
      <div className="min-w-0 space-y-1">
        <h2 className="text-balance bg-gradient-to-br from-slate-900 via-indigo-800 to-violet-700 bg-clip-text text-2xl font-semibold tracking-tight text-transparent dark:from-white dark:via-white dark:to-white/70">
          {title}
        </h2>
        {description && (
          <p className="text-sm text-muted-foreground text-pretty leading-relaxed">
            {description}
          </p>
        )}
      </div>
      {actions && (
        <div className="flex shrink-0 flex-wrap items-center gap-2">{actions}</div>
      )}
    </div>
  );
}
