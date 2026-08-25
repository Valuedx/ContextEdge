import { cn } from "@/lib/utils";

type PageToolbarProps = {
  children: React.ReactNode;
  actions?: React.ReactNode;
  className?: string;
};

export function PageToolbar({ children, actions, className }: PageToolbarProps) {
  return (
    <div
      className={cn(
        "flex items-center gap-2 overflow-x-auto rounded-lg border bg-card px-3 py-2 shadow-sm",
        className
      )}
    >
      {children}
      {actions ? (
        <div className="flex shrink-0 items-center gap-2">
          {actions}
        </div>
      ) : null}
    </div>
  );
}
