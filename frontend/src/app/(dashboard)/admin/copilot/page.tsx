"use client";

/**
 * Admin Copilot usage dashboard.
 *
 * Date-range table of logins, chats, and tokens per engineer, with CSV
 * export and a drill-down into that user's saved conversations.
 */

import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { AlertTriangle, Download, MessageSquare } from "lucide-react";

import { PageHeader } from "@/components/common/page-header";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { api } from "@/lib/api";
import { cn } from "@/lib/utils";
import type {
  CopilotConversationDetail,
  CopilotConversationListItem,
  CopilotUsageSummary,
} from "@/lib/types";

function isoDate(daysAgo: number): string {
  const date = new Date();
  date.setUTCDate(date.getUTCDate() - daysAgo);
  return date.toISOString().slice(0, 10);
}

function formatNumber(n: number): string {
  return n.toLocaleString();
}

function csvEscape(value: string | number | null | undefined): string {
  const text = String(value ?? "");
  if (/[",\n]/.test(text)) return `"${text.replaceAll('"', '""')}"`;
  return text;
}

function citationLabels(citations: unknown): string[] {
  if (!Array.isArray(citations)) return [];
  return citations
    .map((item) => {
      if (typeof item === "string") return item;
      if (item && typeof item === "object" && "label" in item) {
        return String((item as { label?: string }).label || "");
      }
      return "";
    })
    .filter(Boolean);
}

export default function AdminCopilotPage() {
  const [fromDate, setFromDate] = useState(isoDate(30));
  const [toDate, setToDate] = useState(isoDate(0));
  const [ticketQuery, setTicketQuery] = useState("");
  const [groupBy, setGroupBy] = useState<"user" | "day">("user");
  const [conversationOffset, setConversationOffset] = useState(0);
  const [sortKey, setSortKey] = useState<"total_tokens" | "chat_count" | "login_count" | "username">(
    "total_tokens",
  );
  const [sortDir, setSortDir] = useState<"asc" | "desc">("desc");
  const [selectedUserId, setSelectedUserId] = useState<string | null>(null);
  const [selectedConversationId, setSelectedConversationId] = useState<string | null>(null);

  const fromIso = `${fromDate}T00:00:00Z`;
  const toIso = `${toDate}T23:59:59Z`;

  const { data, isLoading, error, refetch, isFetching } = useQuery<CopilotUsageSummary>({
    queryKey: ["admin-copilot-usage", fromIso, toIso, groupBy],
    queryFn: () =>
      api.get<CopilotUsageSummary>("/copilot/usage/summary", {
        from: fromIso,
        to: toIso,
        group_by: groupBy,
      }),
    retry: false,
  });

  const rows = useMemo(() => {
    const items = [...(data?.rows ?? [])];
    items.sort((a, b) => {
      const left = a[sortKey] ?? "";
      const right = b[sortKey] ?? "";
      if (typeof left === "number" && typeof right === "number") {
        return sortDir === "desc" ? right - left : left - right;
      }
      return sortDir === "desc"
        ? String(right).localeCompare(String(left))
        : String(left).localeCompare(String(right));
    });
    return items;
  }, [data?.rows, sortKey, sortDir]);

  const selectedUser = rows.find((row) => row.user_id === selectedUserId) ?? null;

  const { data: conversations = { items: [] }, isLoading: conversationsLoading } = useQuery<{
    items: CopilotConversationListItem[];
  }>({
    queryKey: ["admin-copilot-conversations", selectedUserId, ticketQuery, conversationOffset],
    queryFn: () => {
      const params: Record<string, string> = { limit: "100", offset: String(conversationOffset) };
      if (selectedUserId) params.user_id = selectedUserId;
      if (ticketQuery.trim()) params.q = ticketQuery.trim();
      return api.get("/copilot/conversations", params);
    },
    enabled: Boolean(selectedUserId || ticketQuery.trim()),
    retry: false,
  });

  const { data: conversation } = useQuery<CopilotConversationDetail>({
    queryKey: ["admin-copilot-conversation", selectedConversationId],
    queryFn: () => api.get(`/copilot/conversations/${selectedConversationId}`),
    enabled: Boolean(selectedConversationId),
    retry: false,
  });

  function toggleSort(key: typeof sortKey) {
    if (sortKey === key) {
      setSortDir((dir) => (dir === "desc" ? "asc" : "desc"));
      return;
    }
    setSortKey(key);
    setSortDir(key === "username" ? "asc" : "desc");
  }

  function exportCsv() {
    const header = ["username", "user_id", "login_count", "last_login", "chat_count", "total_tokens"];
    const body = rows.map((row) =>
      [
        csvEscape(row.username),
        csvEscape(row.user_id),
        row.login_count,
        csvEscape(row.last_login),
        row.chat_count,
        row.total_tokens,
      ].join(","),
    );
    const blob = new Blob([[header.join(","), ...body].join("\n")], { type: "text/csv;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = `copilot-usage-${fromDate}-to-${toDate}.csv`;
    link.click();
    URL.revokeObjectURL(url);
  }

  return (
    <div className="space-y-6">
      <PageHeader
        title="Copilot Usage"
        description="Extension logins and chat token spend per engineer. Drill into a user to read their conversations."
      />

      <div className="flex flex-wrap items-end gap-3">
        <label className="space-y-1.5 text-xs font-medium text-muted-foreground">
          From
          <Input type="date" value={fromDate} onChange={(e) => setFromDate(e.target.value)} className="w-[160px]" />
        </label>
        <label className="space-y-1.5 text-xs font-medium text-muted-foreground">
          To
          <Input type="date" value={toDate} onChange={(e) => setToDate(e.target.value)} className="w-[160px]" />
        </label>
        <label className="space-y-1.5 text-xs font-medium text-muted-foreground">
          Ticket lookup
          <Input
            value={ticketQuery}
            onChange={(e) => {
              setTicketQuery(e.target.value);
              setSelectedConversationId(null);
              setConversationOffset(0);
            }}
            placeholder="Ticket number or text"
            className="w-[220px]"
          />
        </label>
        <label className="space-y-1.5 text-xs font-medium text-muted-foreground">
          View
          <select
            className="flex h-9 w-[160px] rounded-md border border-input bg-background px-3 text-sm"
            value={groupBy}
            onChange={(e) => setGroupBy(e.target.value === "day" ? "day" : "user")}
          >
            <option value="user">By user</option>
            <option value="day">By day</option>
          </select>
        </label>
        <Button variant="outline" size="sm" disabled={isFetching} onClick={() => refetch()}>
          {isFetching ? "Refreshing…" : "Refresh"}
        </Button>
        <Button variant="outline" size="sm" disabled={!rows.length} onClick={exportCsv}>
          <Download className="mr-1.5 h-3.5 w-3.5" />
          CSV
        </Button>
      </div>

      {error && (
        <div className="rounded-md border border-destructive/40 bg-destructive/10 p-4 text-sm text-destructive flex items-start gap-2">
          <AlertTriangle className="h-4 w-4 mt-0.5 shrink-0" />
          <div>
            <div className="font-medium">Unable to load Copilot usage</div>
            <div className="text-xs mt-1 opacity-80">{error.message}</div>
            <div className="text-xs mt-2 opacity-70">
              This endpoint requires <code>tenant_admin</code> or <code>platform_super_admin</code>.
            </div>
          </div>
        </div>
      )}

      {!error && (
        <Card>
          <CardContent className="p-0">
            {isLoading ? (
              <div className="text-sm text-muted-foreground py-8 text-center">Loading usage…</div>
            ) : rows.length === 0 ? (
              <div className="text-sm text-muted-foreground py-8 text-center">
                No Copilot logins or chats in this range.
              </div>
            ) : (
              <table className="w-full text-sm">
                <thead className="bg-muted/40 text-[11px] uppercase tracking-wider text-muted-foreground">
                  <tr>
                    {(
                      (
                        groupBy === "day"
                          ? ([
                              ["day", "Day"],
                              ["login_count", "Logins"],
                              ["chat_count", "Chats"],
                              ["total_tokens", "Tokens"],
                            ] as const)
                          : ([
                              ["username", "User"],
                              ["login_count", "Logins"],
                              ["chat_count", "Chats"],
                              ["total_tokens", "Tokens"],
                            ] as const)
                      )
                    ).map(([key, label]) => (
                      <th key={key} className={cn("px-3 py-2 font-medium", key === "username" || key === "day" ? "text-left" : "text-right")}>
                        <button type="button" className="hover:text-foreground" onClick={() => {
                          if (key === "day") return;
                          toggleSort(key === "username" ? "username" : key);
                        }}>
                          {label}
                          {sortKey === key ? (sortDir === "desc" ? " ↓" : " ↑") : ""}
                        </button>
                      </th>
                    ))}
                    {groupBy === "user" && <th className="text-left px-3 py-2 font-medium">Last login</th>}
                  </tr>
                </thead>
                <tbody>
                  {rows.map((row) => (
                    <tr
                      key={row.user_id || row.day}
                      className={cn(
                        "border-t",
                        groupBy === "user" && "hover:bg-muted/30 cursor-pointer",
                        selectedUserId === row.user_id && "bg-muted/40",
                      )}
                      onClick={() => {
                        if (groupBy !== "user") return;
                        setSelectedUserId(row.user_id || null);
                        setSelectedConversationId(null);
                        setConversationOffset(0);
                      }}
                    >
                      <td className="px-3 py-2">
                        {groupBy === "day"
                          ? (row.day ? new Date(row.day).toLocaleDateString() : "—")
                          : (row.username || row.user_id)}
                      </td>
                      <td className="px-3 py-2 text-right font-mono text-xs">{formatNumber(row.login_count)}</td>
                      <td className="px-3 py-2 text-right font-mono text-xs">{formatNumber(row.chat_count)}</td>
                      <td className="px-3 py-2 text-right font-mono text-xs">{formatNumber(row.total_tokens)}</td>
                      {groupBy === "user" && (
                        <td className="px-3 py-2 text-xs text-muted-foreground">
                          {row.last_login ? new Date(row.last_login).toLocaleString() : "—"}
                        </td>
                      )}
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </CardContent>
        </Card>
      )}

      {(selectedUser || ticketQuery.trim()) && (
        <section className="grid gap-4 lg:grid-cols-2">
          <Card>
            <CardContent className="p-4 space-y-3">
              <div className="flex items-center gap-2">
                <MessageSquare className="h-4 w-4 text-muted-foreground" />
                <h2 className="text-sm font-semibold">
                  {selectedUser ? `Conversations · ${selectedUser.username || selectedUser.user_id}` : "Ticket conversations"}
                </h2>
              </div>
              {conversationsLoading ? (
                <div className="text-xs text-muted-foreground">Loading conversations…</div>
              ) : conversations.items.length === 0 ? (
                <div className="text-xs text-muted-foreground">No conversations for this filter.</div>
              ) : (
                <div className="space-y-1 max-h-[420px] overflow-y-auto">
                  {conversations.items.map((item) => (
                    <button
                      key={item.id}
                      type="button"
                      onClick={() => setSelectedConversationId(item.id)}
                      className={cn(
                        "w-full text-left rounded-md border px-3 py-2 hover:bg-muted/40",
                        selectedConversationId === item.id && "bg-muted/50",
                      )}
                    >
                      <div className="text-sm">
                        {item.ticket_number ? `#${item.ticket_number} · ` : item.mode === "general" ? "General · " : ""}
                        {item.title}
                      </div>
                      <div className="text-[11px] text-muted-foreground">
                        {item.message_count} messages · {formatNumber(item.total_tokens)} tokens
                        {item.last_message_at ? ` · ${new Date(item.last_message_at).toLocaleString()}` : ""}
                      </div>
                    </button>
                  ))}
                  {conversations.items.length >= 100 && (
                    <Button
                      variant="outline"
                      size="sm"
                      onClick={() => setConversationOffset((value) => value + 100)}
                    >
                      Next 100
                    </Button>
                  )}
                  {conversationOffset > 0 && (
                    <Button
                      variant="outline"
                      size="sm"
                      onClick={() => setConversationOffset((value) => Math.max(0, value - 100))}
                    >
                      Previous
                    </Button>
                  )}
                </div>
              )}
            </CardContent>
          </Card>

          <Card>
            <CardContent className="p-4 space-y-3">
              <h2 className="text-sm font-semibold">Transcript</h2>
              {!conversation ? (
                <div className="text-xs text-muted-foreground">Select a conversation to read it.</div>
              ) : (
                <div className="space-y-3 max-h-[420px] overflow-y-auto">
                  <div className="flex items-center gap-2">
                    <Badge variant="secondary" className="text-[10px]">
                      {conversation.mode}
                    </Badge>
                    {conversation.ticket_number && (
                      <Badge variant="outline" className="text-[10px] font-mono">
                        #{conversation.ticket_number}
                      </Badge>
                    )}
                  </div>
                  {conversation.messages.map((message) => (
                    <div key={message.seq} className="text-sm">
                      <div className="text-[11px] uppercase tracking-wide text-muted-foreground">
                        {message.role}
                      </div>
                      <div className="whitespace-pre-wrap">{message.content || "—"}</div>
                      {citationLabels(message.citations).length > 0 && (
                        <div className="mt-1 text-[11px] text-muted-foreground">
                          Sources: {citationLabels(message.citations).join(" · ")}
                        </div>
                      )}
                    </div>
                  ))}
                </div>
              )}
            </CardContent>
          </Card>
        </section>
      )}
    </div>
  );
}
