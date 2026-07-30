"use client";

import { useMutation, useQuery } from "@tanstack/react-query";
import Link from "next/link";
import { useState } from "react";

import { PageHeader } from "@/components/common/page-header";
import { StatusBadge } from "@/components/common/status-badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Textarea } from "@/components/ui/textarea";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Badge } from "@/components/ui/badge";
import { PaginationControls } from "@/components/common/pagination-controls";
import { usePagination } from "@/lib/hooks/use-pagination";
import { api } from "@/lib/api";
import type {
  Domain,
  RetrievalFeedback,
  ResolutionSessionResponse,
  RuntimeExplainResponse,
  RuntimeMatchResponse,
  RuntimePlaybookVersion,
} from "@/lib/types";

function linesToList(text: string): string[] {
  return text
    .split(/\r?\n/)
    .map((s) => s.trim())
    .filter(Boolean);
}

const UUID_RE =
  /^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;

const NO_SESSION = "__none__";
const NO_DOMAIN = "__all_domains__";

function domainMeta(domain: Domain): string {
  return domain.description || (domain.is_active ? "Active domain" : "Inactive domain");
}

function sessionDisplayName(session: ResolutionSessionResponse): string {
  const caseId = session.external_case_ids[0];
  const symptom = session.symptoms[0] ?? "No symptoms";
  return caseId ? `${caseId} - ${symptom}` : symptom;
}

function sessionMeta(session: ResolutionSessionResponse): string {
  return `${session.status} - ${new Date(session.created_at).toLocaleString()}`;
}

function FeedbackTab() {
  const pg = usePagination(50);

  const { data: feedbacks = [], isLoading } = useQuery<RetrievalFeedback[]>({
    queryKey: ["runtime-feedback", pg.page],
    queryFn: () => api.get("/runtime/feedback", pg.params),
  });

  return (
    <div className="space-y-4">
      {isLoading ? (
        <p className="text-sm text-muted-foreground">Loading feedback...</p>
      ) : feedbacks.length === 0 ? (
        <div className="rounded-md border p-10 text-center text-sm text-muted-foreground">
          No feedback submitted yet.
        </div>
      ) : (
        <div className="space-y-2">
          {feedbacks.map((fb) => (
            <div key={fb.id} className="rounded-md border px-4 py-3 text-sm space-y-1">
              <div className="flex flex-wrap items-center gap-2">
                <Badge variant="outline" className="text-xs">{fb.feedback_type}</Badge>
                {fb.playbook_id && (
                  <Link href={`/playbooks/${fb.playbook_id}`} className="font-mono text-xs text-primary hover:underline">
                    {fb.playbook_id}
                  </Link>
                )}
                {fb.match_id && (
                  <span className="font-mono text-xs text-muted-foreground">{fb.match_id}</span>
                )}
                <span className="ml-auto text-xs text-muted-foreground">
                  {new Date(fb.created_at).toLocaleString()}
                </span>
              </div>
              {fb.details && Object.keys(fb.details).length > 0 && (
                <pre className="rounded-md bg-muted p-2 text-xs overflow-auto max-h-24">
                  {JSON.stringify(fb.details, null, 2)}
                </pre>
              )}
            </div>
          ))}
        </div>
      )}
      <PaginationControls
        page={pg.page}
        pageSize={pg.pageSize}
        count={feedbacks.length}
        onPrev={pg.prevPage}
        onNext={pg.nextPage}
      />
    </div>
  );
}

export default function RuntimePage() {
  const [symptomsText, setSymptomsText] = useState("VPN drops\npacket loss");
  const [entitiesText, setEntitiesText] = useState("Acme Corp\ngateway-01");
  const [context, setContext] = useState("");
  const [domainId, setDomainId] = useState("");
  const [topK, setTopK] = useState("5");
  const [environmentText, setEnvironmentText] = useState("{}");
  const [sessionId, setSessionId] = useState("");
  const [formError, setFormError] = useState<string | null>(null);

  const [match, setMatch] = useState<RuntimeMatchResponse | null>(null);
  const [explain, setExplain] = useState<RuntimeExplainResponse | null>(null);

  const [stableKey, setStableKey] = useState("");
  const [pbDomainId, setPbDomainId] = useState("");
  const [pbError, setPbError] = useState<string | null>(null);
  const [playbookVersion, setPlaybookVersion] = useState<RuntimePlaybookVersion | null>(null);

  const { data: sessions = [], isLoading: sessionsLoading } = useQuery<
    ResolutionSessionResponse[]
  >({
    queryKey: ["sessions", "runtime-selector"],
    queryFn: () => api.get("/sessions", { limit: "50" }),
  });

  const { data: domains = [], isLoading: domainsLoading } = useQuery<Domain[]>({
    queryKey: ["domains", "runtime-selector"],
    queryFn: () => api.get("/domains"),
  });

  const selectedSession = sessions.find((session) => session.id === sessionId);
  const activeDomains = domains.filter((domain) => domain.is_active);
  const selectedDomain = domains.find((domain) => domain.id === domainId);
  const selectedPlaybookDomain = domains.find((domain) => domain.id === pbDomainId);

  const matchMut = useMutation({
    mutationFn: async () => {
      setFormError(null);
      let environment: Record<string, unknown> = {};
      if (environmentText.trim()) {
        try {
          const parsed = JSON.parse(environmentText) as unknown;
          if (parsed === null || typeof parsed !== "object" || Array.isArray(parsed)) {
            throw new Error("Environment must be a JSON object");
          }
          environment = parsed as Record<string, unknown>;
        } catch {
          throw new Error("Invalid JSON in environment");
        }
      }
      const k = Number.parseInt(topK, 10);
      if (Number.isNaN(k) || k < 1 || k > 20) {
        throw new Error("top_k must be between 1 and 20");
      }
      const did = domainId.trim();
      if (did && !UUID_RE.test(did)) {
        throw new Error("domain_id must be a valid UUID or empty");
      }
      const sid = sessionId.trim();
      if (sid && !UUID_RE.test(sid)) {
        throw new Error("session_id must be a valid UUID or empty");
      }
      const body: Record<string, unknown> = {
        symptoms: linesToList(symptomsText),
        entities: linesToList(entitiesText),
        environment,
        context: context.trim() || null,
        top_k: k,
      };
      if (did) body.domain_id = did;
      if (sid) body.session_id = sid;
      return api.post<RuntimeMatchResponse>("/runtime/match", body);
    },
    onSuccess: (data) => {
      setMatch(data);
      setExplain(null);
    },
    onError: (e: Error) => setFormError(e.message),
  });

  const explainMut = useMutation({
    mutationFn: (matchId: string) =>
      api.get<RuntimeExplainResponse>(`/runtime/explain/${matchId}`),
    onSuccess: (data) => setExplain(data),
    onError: (e: Error) => setFormError(e.message),
  });

  const fetchPlaybookMut = useMutation({
    mutationFn: async () => {
      setPbError(null);
      const key = stableKey.trim();
      if (!key) throw new Error("stable_key is required");
      const did = pbDomainId.trim();
      if (did && !UUID_RE.test(did)) {
        throw new Error("Playbook domain id must be a valid UUID or empty");
      }
      const params: Record<string, string> = did ? { domain_id: did } : {};
      return api.get<RuntimePlaybookVersion>(
        `/runtime/playbooks/${encodeURIComponent(key)}`,
        Object.keys(params).length ? params : undefined
      );
    },
    onSuccess: (data) => setPlaybookVersion(data),
    onError: (e: Error) => setPbError(e.message),
  });

  return (
    <div className="space-y-6">
      <PageHeader
        title="Runtime"
        description="Call the live retrieval ranker with your JWT. Domain scope and risk caps match production behavior for your role. Explain requires Redis to have cached the match."
      />

      <Tabs defaultValue="sandbox">
        <TabsList>
          <TabsTrigger value="sandbox">Sandbox</TabsTrigger>
          <TabsTrigger value="feedback">Feedback</TabsTrigger>
        </TabsList>

        <TabsContent value="feedback" className="mt-4">
          <FeedbackTab />
        </TabsContent>

        <TabsContent value="sandbox" className="mt-4 space-y-6">

      <Card>
        <CardHeader>
          <CardTitle className="text-base">Match request</CardTitle>
          <p className="text-sm text-muted-foreground">
            One symptom or entity per line. Optional domain scopes ranking like{" "}
            <code className="text-xs">POST /runtime/match</code>.
          </p>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="grid gap-4 md:grid-cols-2">
            <div className="space-y-2">
              <Label htmlFor="symptoms">Symptoms</Label>
              <Textarea
                id="symptoms"
                className="min-h-[100px] font-mono text-xs"
                value={symptomsText}
                onChange={(e) => setSymptomsText(e.target.value)}
              />
            </div>
            <div className="space-y-2">
              <Label htmlFor="entities">Entities</Label>
              <Textarea
                id="entities"
                className="min-h-[100px] font-mono text-xs"
                value={entitiesText}
                onChange={(e) => setEntitiesText(e.target.value)}
              />
            </div>
          </div>
          <div className="space-y-2">
            <Label htmlFor="context">Context (optional)</Label>
            <Textarea
              id="context"
              className="min-h-[72px] text-sm"
              value={context}
              onChange={(e) => setContext(e.target.value)}
            />
          </div>
            <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
            <div className="space-y-2">
              <Label htmlFor="domain">Domain / area (optional)</Label>
              <Select
                value={domainId || NO_DOMAIN}
                onValueChange={(value) => setDomainId(value === NO_DOMAIN ? "" : value)}
                disabled={domainsLoading}
              >
                <SelectTrigger id="domain" className="w-full">
                  <SelectValue
                    placeholder={
                      domainsLoading ? "Loading domains..." : "Select business area"
                    }
                  />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value={NO_DOMAIN}>All domains</SelectItem>
                  {activeDomains.map((domain) => (
                    <SelectItem key={domain.id} value={domain.id}>
                      <div className="flex min-w-0 flex-col">
                        <span className="truncate">{domain.name}</span>
                        <span className="text-xs text-muted-foreground">
                          {domainMeta(domain)}
                        </span>
                      </div>
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
              <p className="text-xs text-muted-foreground">
                {selectedDomain
                  ? `Runtime searches inside ${selectedDomain.name}.`
                  : "All domains means Runtime searches broadly."}
              </p>
            </div>
            <div className="space-y-2">
              <Label htmlFor="session">Resolution session (optional)</Label>
              <Select
                value={sessionId || NO_SESSION}
                onValueChange={(value) => {
                  const nextSessionId = value === NO_SESSION ? "" : value;
                  setSessionId(nextSessionId);
                  const session = sessions.find((s) => s.id === nextSessionId);
                  if (session) {
                    setSymptomsText(session.symptoms.join("\n"));
                    setEntitiesText(session.entities.join("\n"));
                    setContext(session.notes ?? "");
                    setDomainId(session.domain_id ?? "");
                  }
                }}
              >
                <SelectTrigger id="session" className="w-full">
                  <SelectValue
                    placeholder={
                      sessionsLoading ? "Loading sessions..." : "Select session by issue"
                    }
                  />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value={NO_SESSION}>No session</SelectItem>
                  {sessions.map((session) => (
                    <SelectItem key={session.id} value={session.id}>
                      <div className="flex min-w-0 flex-col">
                        <span className="truncate">{sessionDisplayName(session)}</span>
                        <span className="text-xs text-muted-foreground">
                          {sessionMeta(session)}
                        </span>
                      </div>
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
              {selectedSession && (
                <p className="text-xs text-muted-foreground">
                  Linked to selected session. Runtime will store trace events on this case.
                </p>
              )}
            </div>
            <div className="space-y-2">
              <Label htmlFor="topk">top_k</Label>
              <Input
                id="topk"
                type="number"
                min={1}
                max={20}
                value={topK}
                onChange={(e) => setTopK(e.target.value)}
              />
            </div>
          </div>
          <div className="space-y-2">
            <Label htmlFor="env">Environment JSON object</Label>
            <Textarea
              id="env"
              className="min-h-[72px] font-mono text-xs"
              value={environmentText}
              onChange={(e) => setEnvironmentText(e.target.value)}
            />
          </div>
          {formError && <p className="text-sm text-destructive">{formError}</p>}
          <Button type="button" disabled={matchMut.isPending} onClick={() => matchMut.mutate()}>
            {matchMut.isPending ? "Running..." : "Run match"}
          </Button>
        </CardContent>
      </Card>

      {match && (
        <Card>
          <CardHeader className="flex flex-row flex-wrap items-center justify-between gap-2 space-y-0">
            <CardTitle className="text-base">Results</CardTitle>
            <div className="flex flex-wrap gap-2">
              <Button
                type="button"
                variant="outline"
                size="sm"
                disabled={!match.results[0]}
                onClick={() => {
                  const sk = match.results[0]?.stable_key;
                  if (sk) setStableKey(sk);
                  const mid = domainId.trim();
                  if (mid && UUID_RE.test(mid)) setPbDomainId(mid);
                }}
              >
                Fill stable_key from top hit
              </Button>
              <Button
                type="button"
                variant="outline"
                size="sm"
                disabled={explainMut.isPending}
                onClick={() => explainMut.mutate(match.match_id)}
              >
                {explainMut.isPending ? "Loading explain..." : "Load explain"}
              </Button>
            </div>
          </CardHeader>
          <CardContent className="space-y-4">
            <div>
              <p className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
                match_id
              </p>
              <p className="font-mono text-sm">{match.match_id}</p>
              {match.session_id ? (
                <p className="mt-1 font-mono text-xs text-muted-foreground">
                  session_id {match.session_id}
                </p>
              ) : null}
            </div>
            <div>
              <p className="mb-2 text-xs font-medium uppercase tracking-wide text-muted-foreground">
                filters_applied
              </p>
              <pre className="max-h-40 overflow-auto rounded-md bg-muted p-3 text-xs">
                {JSON.stringify(match.filters_applied, null, 2)}
              </pre>
            </div>
            {match.fallback_guidance && (
              <p className="rounded-lg border border-amber-500/30 bg-amber-500/5 px-3 py-2 text-sm text-amber-900 dark:text-amber-100/90">
                {match.fallback_guidance}
              </p>
            )}
            {match.results.length === 0 ? (
              <p className="text-sm text-muted-foreground">No playbooks in scope after filters.</p>
            ) : (
              <ul className="space-y-2">
                {match.results.map((r) => (
                  <li
                    key={r.playbook_id}
                    className="rounded-xl border border-black/10 bg-white/40 px-4 py-3 dark:border-white/10 dark:bg-white/[0.04]"
                  >
                    <div className="flex flex-wrap items-start justify-between gap-2">
                      <div>
                        <Link
                          href={`/playbooks/${r.playbook_id}`}
                          className="font-medium text-primary hover:underline"
                        >
                          {r.playbook_title}
                        </Link>
                        <p className="mt-1 font-mono text-xs text-muted-foreground">
                          {r.stable_key}
                        </p>
                      </div>
                      <StatusBadge status={r.risk_tier} />
                    </div>
                    <div className="mt-2 flex flex-wrap gap-3 text-xs text-muted-foreground">
                      <span>score {r.match_score}</span>
                      <span>confidence {r.confidence}</span>
                      {r.retrieval_score != null ? (
                        <span>retrieval {r.retrieval_score}</span>
                      ) : null}
                      {r.playbook_confidence != null ? (
                        <span>playbook conf. {r.playbook_confidence}</span>
                      ) : null}
                      <span>{r.freshness_status}</span>
                      <span>{r.automation_mode}</span>
                    </div>
                  </li>
                ))}
              </ul>
            )}
          </CardContent>
        </Card>
      )}

      {explain && (
        <Card>
          <CardHeader>
            <CardTitle className="text-base">Explain payload</CardTitle>
            <p className="text-sm text-muted-foreground">
              Cached breakdown for this match_id. If this is empty or load fails, Redis may be
              unavailable or the cache expired.
            </p>
          </CardHeader>
          <CardContent className="space-y-3">
            <pre className="max-h-[28rem] overflow-auto rounded-md bg-muted p-3 text-xs">
              {JSON.stringify(explain, null, 2)}
            </pre>
          </CardContent>
        </Card>
      )}

      <Card>
        <CardHeader>
          <CardTitle className="text-base">Fetch published playbook</CardTitle>
          <p className="text-sm text-muted-foreground">
            <code className="text-xs">GET /runtime/playbooks/{"{stable_key}"}</code> with optional{" "}
            <code className="text-xs">domain_id</code>.             Same risk-tier and domain checks as the API. After a match, use{" "}
            <span className="font-medium">Fill stable_key from top hit</span> to copy the leading
            result and, when the match form has a valid domain id, copy it here for scope checks.
          </p>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="space-y-2">
            <Label htmlFor="stable-key">stable_key</Label>
            <Input
              id="stable-key"
              className="font-mono text-sm"
              placeholder="e.g. playbook.vpn_disconnect_v1"
              value={stableKey}
              onChange={(e) => setStableKey(e.target.value)}
            />
          </div>
          <div className="space-y-2">
            <Label htmlFor="pb-domain">Domain / area for scope check (optional)</Label>
            <Select
              value={pbDomainId || NO_DOMAIN}
              onValueChange={(value) => setPbDomainId(value === NO_DOMAIN ? "" : value)}
              disabled={domainsLoading}
            >
              <SelectTrigger id="pb-domain" className="w-full">
                <SelectValue
                  placeholder={
                    domainsLoading ? "Loading domains..." : "Select business area"
                  }
                />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value={NO_DOMAIN}>All domains</SelectItem>
                {activeDomains.map((domain) => (
                  <SelectItem key={domain.id} value={domain.id}>
                    <div className="flex min-w-0 flex-col">
                      <span className="truncate">{domain.name}</span>
                      <span className="text-xs text-muted-foreground">
                        {domainMeta(domain)}
                      </span>
                    </div>
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
            <p className="text-xs text-muted-foreground">
              {selectedPlaybookDomain
                ? `Playbook lookup is checked against ${selectedPlaybookDomain.name}.`
                : "All domains means tenant-wide playbook lookup."}
            </p>
          </div>
          {pbError && <p className="text-sm text-destructive">{pbError}</p>}
          <div className="flex flex-wrap gap-2">
            <Button
              type="button"
              variant="outline"
              disabled={fetchPlaybookMut.isPending}
              onClick={() => fetchPlaybookMut.mutate()}
            >
              {fetchPlaybookMut.isPending ? "Loading..." : "Fetch playbook"}
            </Button>
          </div>
          {playbookVersion && (
            <div className="space-y-2">
              <div className="flex flex-wrap items-center gap-2 text-sm">
                <span className="text-muted-foreground">Playbook</span>
                <Link
                  href={`/playbooks/${playbookVersion.playbook_id}`}
                  className="font-mono text-xs text-primary hover:underline"
                >
                  {playbookVersion.playbook_id}
                </Link>
                <span className="text-muted-foreground">-</span>
                <span>v{playbookVersion.semantic_version}</span>
              </div>
              <pre className="max-h-[28rem] overflow-auto rounded-md bg-muted p-3 text-xs">
                {JSON.stringify(playbookVersion, null, 2)}
              </pre>
            </div>
          )}
        </CardContent>
      </Card>

        </TabsContent>
      </Tabs>
    </div>
  );
}
