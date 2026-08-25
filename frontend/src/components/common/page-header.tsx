import Link from "next/link";
import { ArrowLeft } from "lucide-react";
import { cn } from "@/lib/utils";

interface PageHeaderProps {
  title: string;
  description?: string;
  actions?: React.ReactNode;
  backHref?: string;
  backLabel?: string;
  className?: string;
}

export function PageHeader({
  title,
  description,
  actions,
  backHref,
  backLabel = "Back",
  className,
}: PageHeaderProps) {
  return (
    <div
      className={cn(
        "flex flex-col gap-3 rounded-lg border bg-card px-4 py-3 text-card-foreground shadow-sm",
        "lg:flex-row lg:items-center lg:justify-between lg:gap-4",
        className
      )}
    >
      <div className="min-w-0 flex-1 space-y-1">
        {backHref && (
          <Link
            href={backHref}
            className="inline-flex max-w-full items-center gap-1.5 text-sm font-medium leading-none text-muted-foreground transition-colors hover:text-foreground"
          >
            <ArrowLeft className="h-4 w-4 shrink-0" />
            <span className="truncate">{backLabel}</span>
          </Link>
        )}
        <h2 className="truncate text-xl font-semibold leading-tight tracking-tight text-foreground">
          {title}
        </h2>
        {description && (
          <p className="truncate text-sm leading-snug text-muted-foreground">
            {description}
          </p>
        )}
      </div>
      {actions && (
        <div className="flex min-w-0 shrink-0 items-center gap-2 overflow-x-auto [&>div]:flex [&>div]:flex-nowrap [&>div]:items-center">
          {actions}
        </div>
      )}
    </div>
  );
}
