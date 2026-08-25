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
        "rounded-lg border bg-card px-5 py-5 text-card-foreground shadow-sm sm:flex sm:items-start sm:justify-between sm:gap-5",
        className
      )}
    >
      <div className="min-w-0 flex-1 space-y-2">
        {backHref && (
          <Link
            href={backHref}
            className="inline-flex max-w-full items-center gap-1.5 text-sm font-medium leading-none text-muted-foreground transition-colors hover:text-foreground"
          >
            <ArrowLeft className="h-4 w-4 shrink-0" />
            <span className="truncate">{backLabel}</span>
          </Link>
        )}
        <h2 className="text-balance text-2xl font-semibold leading-tight tracking-tight text-foreground">
          {title}
        </h2>
        {description && (
          <p className="max-w-3xl text-pretty text-sm leading-relaxed text-muted-foreground">
            {description}
          </p>
        )}
      </div>
      {actions && (
        <div className="mt-4 flex w-full min-w-0 shrink-0 flex-wrap items-center justify-start gap-2 sm:mt-0 sm:w-auto sm:max-w-[72%] sm:justify-end">
          {actions}
        </div>
      )}
    </div>
  );
}
