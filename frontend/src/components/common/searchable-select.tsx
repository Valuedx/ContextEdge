"use client";

import { useMemo, useState } from "react";
import { Check, ChevronsUpDown } from "lucide-react";

import { buttonVariants } from "@/components/ui/button";
import {
  Command,
  CommandEmpty,
  CommandGroup,
  CommandInput,
  CommandItem,
  CommandList,
} from "@/components/ui/command";
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from "@/components/ui/popover";
import { cn } from "@/lib/utils";

export type SearchableSelectOption = {
  value: string;
  label: string;
  meta?: string;
  disabled?: boolean;
};

function matchesSearch(option: SearchableSelectOption, search: string): boolean {
  const query = search.trim().toLowerCase();
  if (!query) return true;
  return [option.label, option.meta, option.value]
    .filter(Boolean)
    .some((value) => value!.toLowerCase().includes(query));
}

export function SearchableSelect({
  className,
  disabled = false,
  emptyText = "No records found.",
  loading = false,
  loadingText = "Loading records...",
  onValueChange,
  options,
  placeholder = "Select an item",
  searchPlaceholder = "Search...",
  triggerId,
  value,
}: {
  className?: string;
  disabled?: boolean;
  emptyText?: string;
  loading?: boolean;
  loadingText?: string;
  onValueChange: (value: string) => void;
  options: SearchableSelectOption[];
  placeholder?: string;
  searchPlaceholder?: string;
  triggerId?: string;
  value: string;
}) {
  const [open, setOpen] = useState(false);
  const [search, setSearch] = useState("");
  const selected = options.find((option) => option.value === value);
  const visibleOptions = useMemo(
    () => options.filter((option) => matchesSearch(option, search)),
    [options, search],
  );

  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger
        id={triggerId}
        disabled={disabled || loading}
        className={cn(
          buttonVariants({ variant: "outline", size: "sm" }),
          "h-8 w-full justify-between gap-2 px-3 text-left font-normal",
          className,
        )}
      >
        <span className="min-w-0 flex-1 truncate">
          <span className={cn("truncate", !selected && "text-muted-foreground")}>
            {loading ? loadingText : selected?.label || placeholder}
          </span>
        </span>
        <ChevronsUpDown className="size-4 shrink-0 opacity-50" />
      </PopoverTrigger>
      <PopoverContent align="start" className="w-[min(34rem,calc(100vw-2rem))] p-0">
        <Command shouldFilter={false}>
          <CommandInput
            value={search}
            onValueChange={setSearch}
            placeholder={searchPlaceholder}
          />
          <CommandList>
            <CommandEmpty>{emptyText}</CommandEmpty>
            <CommandGroup>
              {visibleOptions.map((option) => (
                <CommandItem
                  key={option.value}
                  value={option.value}
                  disabled={option.disabled}
                  data-checked={option.value === value}
                  onSelect={() => {
                    if (option.disabled) return;
                    onValueChange(option.value);
                    setSearch("");
                    setOpen(false);
                  }}
                >
                  <div className="min-w-0 flex-1">
                    <span className="block truncate">{option.label}</span>
                    {option.meta && (
                      <span className="block truncate text-xs text-muted-foreground">
                        {option.meta}
                      </span>
                    )}
                  </div>
                  {option.value === value && <Check className="size-4 shrink-0" />}
                </CommandItem>
              ))}
            </CommandGroup>
          </CommandList>
        </Command>
      </PopoverContent>
    </Popover>
  );
}
