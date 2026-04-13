import { useState } from "react";

export interface PaginationState {
  page: number;
  pageSize: number;
  offset: number;
}

export interface PaginationActions {
  nextPage: () => void;
  prevPage: () => void;
  reset: () => void;
}

export function usePagination(pageSize = 50): PaginationState & PaginationActions & { params: Record<string, string> } {
  const [page, setPage] = useState(0);

  return {
    page,
    pageSize,
    offset: page * pageSize,
    params: { limit: String(pageSize), offset: String(page * pageSize) },
    nextPage: () => setPage((p) => p + 1),
    prevPage: () => setPage((p) => Math.max(0, p - 1)),
    reset: () => setPage(0),
  };
}
