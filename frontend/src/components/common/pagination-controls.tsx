"use client";

import { ChevronLeft, ChevronRight } from "lucide-react";
import { Button } from "@/components/ui/button";

interface PaginationControlsProps {
  page: number;
  pageSize: number;
  count: number; // number of items returned in the current page
  onPrev: () => void;
  onNext: () => void;
}

export function PaginationControls({
  page,
  pageSize,
  count,
  onPrev,
  onNext,
}: PaginationControlsProps) {
  const isFirst = page === 0;
  const isLast = count < pageSize;

  if (isFirst && isLast) return null; // only one page — no controls needed

  const from = page * pageSize + 1;
  const to = page * pageSize + count;

  return (
    <div className="flex items-center justify-between pt-2">
      <span className="text-xs text-muted-foreground">
        Showing {from}–{to}
      </span>
      <div className="flex gap-2">
        <Button variant="outline" size="sm" disabled={isFirst} onClick={onPrev}>
          <ChevronLeft className="h-4 w-4" />
          Prev
        </Button>
        <Button variant="outline" size="sm" disabled={isLast} onClick={onNext}>
          Next
          <ChevronRight className="h-4 w-4" />
        </Button>
      </div>
    </div>
  );
}
