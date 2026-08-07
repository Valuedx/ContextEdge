"use client";

import {
  ColumnDef,
  flexRender,
  getCoreRowModel,
  useReactTable,
  SortingState,
  getSortedRowModel,
} from "@tanstack/react-table";
import { useState } from "react";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { Button } from "@/components/ui/button";
import { ChevronLeft, ChevronRight, ArrowUpDown, ArrowUp, ArrowDown } from "lucide-react";

interface DataTableProps<TData, TValue> {
  columns: ColumnDef<TData, TValue>[];
  data: TData[];
  pageSize?: number;
  totalCount?: number;
  page?: number;
  defaultSorting?: SortingState;
  onPageChange?: (page: number) => void;
  onSelectionChange?: (selectedIds: string[]) => void;
}

export function DataTable<TData, TValue>({
  columns,
  data,
  pageSize = 50,
  totalCount,
  page = 0,
  defaultSorting = [],
  onPageChange,
  onSelectionChange,
}: DataTableProps<TData, TValue>) {
  const [sorting, setSorting] = useState<SortingState>(defaultSorting);
  const [rowSelection, setRowSelection] = useState({});

  // eslint-disable-next-line react-hooks/incompatible-library
  const table = useReactTable({
    data,
    columns,
    getCoreRowModel: getCoreRowModel(),
    getSortedRowModel: getSortedRowModel(),
    onSortingChange: setSorting,
    onRowSelectionChange: (updater) => {
        const next = typeof updater === 'function' ? updater(rowSelection) : updater;
        setRowSelection(next);
        if (onSelectionChange) {
            // Get the IDs from the selected rows. 
            // This assumes TData has an 'id' property.
            const selectedRows = Object.keys(next).map(idx => (data[parseInt(idx)] as any)?.id).filter(Boolean);
            onSelectionChange(selectedRows);
        }
    },
    state: { sorting, rowSelection },
  });

  const totalPages = totalCount ? Math.ceil(totalCount / pageSize) : undefined;

  return (
    <div>
      <div className="overflow-hidden rounded-xl border border-black/10 bg-white/50 shadow-[inset_0_1px_0_rgba(255,255,255,0.9)] backdrop-blur-md dark:border-white/10 dark:bg-white/[0.04] dark:shadow-[inset_0_1px_0_rgba(255,255,255,0.06)]">
        <Table>
          <TableHeader>
            {table.getHeaderGroups().map((headerGroup) => (
              <TableRow key={headerGroup.id}>
                {headerGroup.headers.map((header) => {
                  const canSort =
                    header.column.getCanSort() &&
                    header.column.columnDef.enableSorting !== false &&
                    header.id !== "select" &&
                    header.id !== "actions";
                  const isSorted = header.column.getIsSorted();

                  return (
                    <TableHead key={header.id} className="select-none">
                      {header.isPlaceholder ? null : canSort ? (
                        <div
                          className="flex items-center gap-1.5 cursor-pointer hover:text-primary font-semibold transition-colors group py-1"
                          onClick={header.column.getToggleSortingHandler()}
                          title={`Sort by ${header.column.columnDef.header} (${
                            isSorted === "asc"
                              ? "Ascending -> click for Descending"
                              : isSorted === "desc"
                              ? "Descending -> click to clear"
                              : "Click to sort Ascending"
                          })`}
                        >
                          <span>
                            {flexRender(
                              header.column.columnDef.header,
                              header.getContext()
                            )}
                          </span>
                          <span className="shrink-0 text-muted-foreground group-hover:text-primary transition-colors">
                            {isSorted === "asc" ? (
                              <ArrowUp className="h-3.5 w-3.5 text-primary" />
                            ) : isSorted === "desc" ? (
                              <ArrowDown className="h-3.5 w-3.5 text-primary" />
                            ) : (
                              <ArrowUpDown className="h-3.5 w-3.5 opacity-40 group-hover:opacity-100" />
                            )}
                          </span>
                        </div>
                      ) : (
                        flexRender(
                          header.column.columnDef.header,
                          header.getContext()
                        )
                      )}
                    </TableHead>
                  );
                })}
              </TableRow>
            ))}
          </TableHeader>
          <TableBody>
            {table.getRowModel().rows?.length ? (
              table.getRowModel().rows.map((row) => (
                <TableRow key={row.id}>
                  {row.getVisibleCells().map((cell) => (
                    <TableCell key={cell.id}>
                      {flexRender(
                        cell.column.columnDef.cell,
                        cell.getContext()
                      )}
                    </TableCell>
                  ))}
                </TableRow>
              ))
            ) : (
              <TableRow>
                <TableCell
                  colSpan={columns.length}
                  className="h-24 text-center text-muted-foreground"
                >
                  No results.
                </TableCell>
              </TableRow>
            )}
          </TableBody>
        </Table>
      </div>

      {onPageChange && (
        <div className="flex items-center justify-between py-4">
          <div className="text-sm text-muted-foreground">
            {totalCount !== undefined && `${totalCount} total`}
          </div>
          <div className="flex items-center gap-2">
            <Button
              variant="outline"
              size="sm"
              onClick={() => onPageChange(page - 1)}
              disabled={page === 0}
            >
              <ChevronLeft className="h-4 w-4" />
            </Button>
            <span className="text-sm">
              Page {page + 1}
              {totalPages !== undefined && ` of ${totalPages}`}
            </span>
            <Button
              variant="outline"
              size="sm"
              onClick={() => onPageChange(page + 1)}
              disabled={totalPages !== undefined && page + 1 >= totalPages}
            >
              <ChevronRight className="h-4 w-4" />
            </Button>
          </div>
        </div>
      )}
    </div>
  );
}
