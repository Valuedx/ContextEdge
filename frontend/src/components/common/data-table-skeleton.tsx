"use client";

import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { Skeleton } from "@/components/ui/skeleton";
import { cn } from "@/lib/utils";

type DataTableSkeletonProps = {
  columns?: number;
  rows?: number;
  className?: string;
};

export function DataTableSkeleton({
  columns = 6,
  rows = 8,
  className,
}: DataTableSkeletonProps) {
  return (
    <div
      className={cn(
        "overflow-hidden rounded-xl border border-black/10 bg-white/50 shadow-[inset_0_1px_0_rgba(255,255,255,0.9)] backdrop-blur-md dark:border-white/10 dark:bg-white/[0.04] dark:shadow-[inset_0_1px_0_rgba(255,255,255,0.06)]",
        className
      )}
    >
      <Table>
        <TableHeader>
          <TableRow className="border-b border-black/5 hover:bg-transparent dark:border-white/5">
            {Array.from({ length: columns }).map((_, i) => (
              <TableHead key={i}>
                <Skeleton className="h-3.5 w-16 max-w-full" />
              </TableHead>
            ))}
          </TableRow>
        </TableHeader>
        <TableBody>
          {Array.from({ length: rows }).map((_, ri) => (
            <TableRow
              key={ri}
              className="border-b border-black/5 hover:bg-transparent dark:border-white/5"
            >
              {Array.from({ length: columns }).map((_, ci) => (
                <TableCell key={ci}>
                  <Skeleton
                    className={cn(
                      "h-4 max-w-full",
                      ci === 0 ? "w-[min(100%,14rem)]" : "w-20"
                    )}
                  />
                </TableCell>
              ))}
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </div>
  );
}
