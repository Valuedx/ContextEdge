"use client";

import type { ReactNode } from "react";

import { Skeleton } from "@/components/ui/skeleton";
import { cn } from "@/lib/utils";

type DetailPageSkeletonProps = {
  /** Skeleton buttons in the header actions area */
  actionSlots?: 1 | 2;
  className?: string;
  children?: ReactNode;
};

export function DetailPageSkeleton({
  actionSlots = 1,
  className,
  children,
}: DetailPageSkeletonProps) {
  return (
    <div className={cn("space-y-6", className)}>
      <div className="flex flex-col gap-3 rounded-lg border bg-card px-4 py-3 lg:flex-row lg:items-center lg:justify-between">
        <div className="min-w-0 flex-1 space-y-1">
          <Skeleton className="h-6 w-[min(100%,16rem)]" />
          <Skeleton className="h-4 w-[min(100%,24rem)]" />
        </div>
        {actionSlots === 2 ? (
          <div className="flex shrink-0 gap-2">
            <Skeleton className="h-8 w-24 rounded-md" />
            <Skeleton className="h-8 w-32 rounded-md" />
          </div>
        ) : (
          <Skeleton className="h-8 w-28 shrink-0 rounded-md" />
        )}
      </div>
      <div className="flex flex-wrap gap-2">
        <Skeleton className="h-6 w-20 rounded-full" />
        <Skeleton className="h-6 w-24 rounded-full" />
      </div>
      {children}
    </div>
  );
}

export function DetailCardGridSkeleton({
  count = 2,
  columns = "md:grid-cols-2",
}: {
  count?: number;
  columns?: string;
}) {
  return (
    <div className={cn("grid gap-4", columns)}>
      {Array.from({ length: count }).map((_, i) => (
        <div
          key={i}
          className="rounded-lg border bg-card p-4 shadow-sm"
        >
          <Skeleton className="h-5 w-28" />
          <div className="mt-3 space-y-2">
            <Skeleton className="h-3 w-full" />
            <Skeleton className="h-3 w-[92%] max-w-full" />
            <Skeleton className="h-3 w-[66%] max-w-full" />
          </div>
        </div>
      ))}
    </div>
  );
}

export function DetailStatCardsSkeleton({ count = 3 }: { count?: number }) {
  return (
    <div className="grid gap-4 md:grid-cols-3">
      {Array.from({ length: count }).map((_, i) => (
        <div
          key={i}
          className="rounded-lg border bg-card p-4 shadow-sm"
        >
          <Skeleton className="mb-4 h-3 w-24" />
          <Skeleton className="h-8 w-14" />
        </div>
      ))}
    </div>
  );
}

export function DetailWideCardSkeleton({ lines = 4 }: { lines?: number }) {
  return (
    <div className="rounded-lg border bg-card p-4 shadow-sm">
      <Skeleton className="h-5 w-36" />
      <div className="mt-3 space-y-2">
        {Array.from({ length: lines }).map((_, i) => (
          <Skeleton
            key={i}
            className={cn("h-3", i === lines - 1 ? "w-2/3" : "w-full")}
          />
        ))}
      </div>
    </div>
  );
}
