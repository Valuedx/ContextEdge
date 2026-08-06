"use client";
import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useForm, useWatch } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import * as z from "zod";
import { FolderOpen, FileText, Loader2 } from "lucide-react";
import { api } from "@/lib/api";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
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
import { toast } from "sonner";

const sourceSchema = z.object({
  display_name: z.string().min(1, "Display name is required").max(255),
  source_type: z.string().min(1, "Source type is required"),
  purpose: z.string().optional(),
  relevance_keywords: z.string().optional(),
  // Gmail specific fields
  gmail_auth_method: z.enum(["service_account", "personal"]).optional(),
  mailbox_email: z.string().email("Invalid email address").optional().or(z.literal("")),
  service_account_json: z.string().optional().or(z.literal("")),
  token_json: z.string().optional().or(z.literal("")),
  // What kind of content a local upload contains
  local_content_type: z.string().optional().or(z.literal("")),
  // ServiceNow specific fields
  servicenow_instance_url: z.string().optional().or(z.literal("")),
  servicenow_username: z.string().optional().or(z.literal("")),
  servicenow_password: z.string().optional().or(z.literal("")),
  servicenow_table_filters: z.string().optional().or(z.literal("")),
  servicenow_alert_severity_max: z.string().optional().or(z.literal("")),
  // Zoho Desk specific fields
  zoho_client_id: z.string().optional().or(z.literal("")),
  zoho_client_secret: z.string().optional().or(z.literal("")),
  zoho_refresh_token: z.string().optional().or(z.literal("")),
  zoho_org_id: z.string().optional().or(z.literal("")),
  zoho_data_center: z.string().optional().or(z.literal("")),
  zoho_modules: z.string().optional().or(z.literal("")),
  zoho_ticket_status: z.string().optional().or(z.literal("")),
  zoho_max_days: z.string().optional().or(z.literal("")),
  zoho_max_records: z.string().optional().or(z.literal("")),
  zoho_per_department: z.boolean().optional(),
  // SapphireIMS specific fields
  sapphire_base_url: z.string().optional().or(z.literal("")),
  sapphire_api_key: z.string().optional().or(z.literal("")),
  sapphire_auth_token: z.string().optional().or(z.literal("")),
  sapphire_projects: z.string().optional().or(z.literal("")),
  // ManageEngine ServiceDesk Plus specific fields
  manageengine_base_url: z.string().optional().or(z.literal("")),
  manageengine_api_key: z.string().optional().or(z.literal("")),
  manageengine_table_filters: z.string().optional().or(z.literal("")),
});

// Zoho pins each account to the data center it was created in; the
// accounts host that issues the token and the Desk host that accepts it
// must match, so a wrong choice fails authentication rather than
// degrading. Mirrors DATA_CENTERS in the connector.
const ZOHO_DATA_CENTERS = [
  { value: "com", label: "com — United States (desk.zoho.com)" },
  { value: "in", label: "in — India (desk.zoho.in)" },
  { value: "eu", label: "eu — Europe (desk.zoho.eu)" },
  { value: "au", label: "au — Australia (desk.zoho.com.au)" },
  { value: "jp", label: "jp — Japan (desk.zoho.jp)" },
  { value: "ca", label: "ca — Canada (desk.zohocloud.ca)" },
  { value: "sa", label: "sa — Saudi Arabia (desk.zoho.sa)" },
  { value: "uk", label: "uk — United Kingdom (desk.zoho.uk)" },
];

type SourceTypeOption = {
  source_type: string;
  label: string;
  connector_available: boolean;
  status: string;
  description?: string;
};

type SourceFormValues = z.infer<typeof sourceSchema>;

type SourceCreatePayload = {
  display_name: string;
  source_type: string;
  purpose?: string;
  auth_type: string;
  config: Record<string, unknown>;
  credentials: Record<string, unknown>;
};

interface AddSourceDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
}

export function AddSourceDialog({ open, onOpenChange }: AddSourceDialogProps) {
  const queryClient = useQueryClient();
  const {
    register,
    handleSubmit,
    reset,
    setValue,
    control,
    getValues,
    formState: { errors },
  } = useForm<SourceFormValues>({
    resolver: zodResolver(sourceSchema),
    defaultValues: {
      display_name: "",
      source_type: "local_file",
      purpose: "",
      mailbox_email: "",
      relevance_keywords: "error, issue, ticket, bug, support, failed, incident",
      service_account_json: "",
      gmail_auth_method: "service_account",
      local_content_type: "document",
      token_json: "",
      servicenow_instance_url: "",
      servicenow_username: "",
      servicenow_password: "",
      servicenow_table_filters: "",
      servicenow_alert_severity_max: "3",
      zoho_client_id: "",
      zoho_client_secret: "",
      zoho_refresh_token: "",
      zoho_org_id: "",
      zoho_data_center: "com",
      zoho_modules: "",
      zoho_ticket_status: "",
      zoho_max_days: "",
      zoho_max_records: "",
      zoho_per_department: false,
      sapphire_base_url: "",
      sapphire_api_key: "",
      sapphire_auth_token: "",
      sapphire_projects: "",
      manageengine_base_url: "",
      manageengine_api_key: "",
      manageengine_table_filters: "",
    },
  });

  // The selectable types come from the backend connector registry, not a
  // hardcoded list. The two had drifted in both directions: the picker
  // offered Confluence, SharePoint, and Exchange (no connector — the
  // source created fine, then died at sync with "Unknown source type"),
  // and omitted SapphireIMS and Zoho Desk, which worked but could not be
  // selected. A client-side list makes that invisible until a user hits
  // it; deriving it means a newly registered connector shows up here on
  // its own.
  const { data: sourceTypes, isLoading: typesLoading } = useQuery<SourceTypeOption[]>({
    queryKey: ["source-types"],
    // api.get prepends /api/v1 — passing it here would double the prefix.
    queryFn: () => api.get<SourceTypeOption[]>("/sources/types"),
    staleTime: 5 * 60 * 1000,
  });

  const sourceType = useWatch({ control, name: "source_type" });
  const localContentType = useWatch({ control, name: "local_content_type" });
  const gmailAuthMethod = useWatch({ control, name: "gmail_auth_method" });
  const zohoDataCenter = useWatch({ control, name: "zoho_data_center" });
  const [selectedFiles, setSelectedFiles] = useState<{ filename: string; content: string }[]>([]);
  const [isReading, setIsReading] = useState(false);

  const handleBrowseFolder = async () => {
    try {
      if (!("showDirectoryPicker" in window)) {
        toast.error("Your browser does not support the Folder Picker API. Please use Chrome or Edge.");
        return;
      }

      setIsReading(true);
      // @ts-expect-error - showDirectoryPicker is modern API not yet in lib.dom.d.ts
      const dirHandle = await window.showDirectoryPicker();
      const files: { filename: string; content: string }[] = [];

      for await (const entry of dirHandle.values()) {
        if (entry.kind === "file") {
          const file = await entry.getFile();
          if (file.name.endsWith(".log") || file.name.endsWith(".txt") || file.name.endsWith(".json") || file.name.endsWith(".md")) {
            const content = await file.text();
            files.push({ filename: file.name, content });
          }
        }
      }

      setSelectedFiles(files);
      if (files.length > 0 && !getValues("display_name")) {
          setValue("display_name", dirHandle.name);
      }
      toast.success(`Identified ${files.length} valid log/text files in "${dirHandle.name}"`);
    } catch (err) {
      if ((err as Error).name !== "AbortError") {
        console.error(err);
        toast.error("Failed to read directory");
      }
    } finally {
      setIsReading(false);
    }
  };

  const mutation = useMutation({
    mutationFn: (payload: SourceCreatePayload) => api.post<{ id: string }>("/sources", payload),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["sources"] });
      onOpenChange(false);
      reset();
      toast.success("Source created successfully");
    },
    onError: (error: Error) => {
      console.error(`Failed to create source: ${error.message}`);
      toast.error(`Could not create source: ${error.message}. Please check your connection or JSON format.`);
    },
  });

  const onSubmit = async (values: SourceFormValues) => {
    try {
      // Prepare the payload for the backend
      const payload: SourceCreatePayload = {
        display_name: values.display_name,
        source_type: values.source_type,
        purpose: values.purpose,
        auth_type: values.source_type === "gmail" ? "service_account" : "oauth2",
        config: {},
        credentials: {},
      };

      if (values.source_type === "servicenow") {
        const instanceUrl = values.servicenow_instance_url?.trim().replace(/\/+$/, "");
        const username = values.servicenow_username?.trim();
        const password = values.servicenow_password;
        const severityText = values.servicenow_alert_severity_max?.trim() || "3";
        const alertSeverityMax = Number(severityText);

        if (!instanceUrl || !username || !password) {
          toast.error("ServiceNow URL, username, and password are required.");
          return;
        }

        if (!/^https?:\/\//i.test(instanceUrl)) {
          toast.error("ServiceNow URL must start with http:// or https://.");
          return;
        }

        if (!Number.isInteger(alertSeverityMax) || alertSeverityMax < 1 || alertSeverityMax > 5) {
          toast.error("Alert severity must be a whole number from 1 to 5.");
          return;
        }

        let tableFilters: Record<string, string> = {};
        const tableFilterText = values.servicenow_table_filters?.trim();
        if (tableFilterText) {
          try {
            const parsed: unknown = JSON.parse(tableFilterText);
            if (
              !parsed ||
              typeof parsed !== "object" ||
              Array.isArray(parsed) ||
              !Object.values(parsed).every((value) => typeof value === "string")
            ) {
              toast.error("ServiceNow table filters must be a JSON object with string values.");
              return;
            }
            tableFilters = parsed as Record<string, string>;
          } catch {
            toast.error("ServiceNow table filters must be valid JSON.");
            return;
          }
        }

        payload.auth_type = "basic";
        payload.config = {
          instance_url: instanceUrl,
          alert_severity_max: alertSeverityMax,
          table_filters: tableFilters,
        };
        payload.credentials = {
          instance_url: instanceUrl,
          username,
          password,
        };
      }

      if (values.source_type === "zoho_desk") {
        const clientId = values.zoho_client_id?.trim();
        const clientSecret = values.zoho_client_secret?.trim();
        const refreshToken = values.zoho_refresh_token?.trim();
        const orgId = values.zoho_org_id?.trim();
        const dataCenter = (values.zoho_data_center || "com").trim();

        if (!clientId || !clientSecret || !refreshToken || !orgId) {
          toast.error(
            "Zoho client ID, client secret, refresh token, and org ID are all required.",
          );
          return;
        }
        if (!ZOHO_DATA_CENTERS.some((dc) => dc.value === dataCenter)) {
          toast.error("Select a valid Zoho data center.");
          return;
        }

        // Empty means "sync every module the token can read", which is
        // what discovery already does — so an empty list is omitted
        // rather than sent as [], which would sync nothing.
        const modules = (values.zoho_modules || "")
          .split(",")
          .map((m) => m.trim())
          .filter(Boolean);
        const ticketStatus = values.zoho_ticket_status?.trim();
        const maxDays = values.zoho_max_days?.trim() ? Number.parseInt(values.zoho_max_days.trim(), 10) : undefined;
        const maxRecords = values.zoho_max_records?.trim() ? Number.parseInt(values.zoho_max_records.trim(), 10) : undefined;

        payload.auth_type = "oauth2";
        payload.config = {
          ...(modules.length > 0 ? { modules } : {}),
          ...(values.zoho_per_department ? { per_department: true } : {}),
          ...(maxDays && !Number.isNaN(maxDays) ? { max_days: maxDays } : {}),
          ...(maxRecords && !Number.isNaN(maxRecords) ? { max_records: maxRecords } : {}),
          ...(ticketStatus
            ? {
                module_filters: {
                  tickets: {
                    status: ticketStatus,
                  },
                },
              }
            : {}),
        };
        payload.credentials = {
          client_id: clientId,
          client_secret: clientSecret,
          refresh_token: refreshToken,
          org_id: orgId,
          data_center: dataCenter,
        };
      }

      if (values.source_type === "sapphireims") {
        const baseUrl = values.sapphire_base_url?.trim().replace(/\/+$/, "");
        const apiKey = values.sapphire_api_key?.trim();
        const authToken = values.sapphire_auth_token?.trim();

        if (!baseUrl || !apiKey || !authToken) {
          toast.error("SapphireIMS base URL, API key, and auth token are required.");
          return;
        }
        if (!/^https?:\/\//i.test(baseUrl)) {
          toast.error("SapphireIMS base URL must start with http:// or https://.");
          return;
        }

        // SapphireIMS has no public projects-list endpoint, so discovery
        // enumerates exactly what is declared here — an empty list
        // discovers nothing, which is worth saying out loud.
        const projects = (values.sapphire_projects || "")
          .split(",")
          .map((p) => p.trim())
          .filter(Boolean);
        if (projects.length === 0) {
          toast.error(
            "List at least one SapphireIMS project — discovery has no way to enumerate them.",
          );
          return;
        }

        payload.auth_type = "api_key";
        payload.config = { projects };
        payload.credentials = {
          base_url: baseUrl,
          api_key: apiKey,
          auth_token: authToken,
        };
      }

      if (values.source_type === "manageengine") {
        const baseUrl = values.manageengine_base_url?.trim().replace(/\/+$/, "");
        const apiKey = values.manageengine_api_key?.trim();

        if (!baseUrl || !apiKey) {
          toast.error("ManageEngine base URL and API key are required.");
          return;
        }
        if (!/^https?:\/\//i.test(baseUrl)) {
          toast.error("ManageEngine base URL must start with http:// or https://.");
          return;
        }

        let tableFilters: Record<string, string> = {};
        const tableFilterText = values.manageengine_table_filters?.trim();
        if (tableFilterText) {
          try {
            const parsed: unknown = JSON.parse(tableFilterText);
            if (
              !parsed ||
              typeof parsed !== "object" ||
              Array.isArray(parsed) ||
              !Object.values(parsed).every((value) => typeof value === "string")
            ) {
              toast.error("ManageEngine table filters must be a JSON object with string values.");
              return;
            }
            tableFilters = parsed as Record<string, string>;
          } catch {
            toast.error("ManageEngine table filters must be valid JSON.");
            return;
          }
        }

        payload.auth_type = "api_key";
        payload.config = { table_filters: tableFilters };
        payload.credentials = {
          base_url: baseUrl,
          api_key: apiKey,
        };
      }

      if (values.source_type === "gmail") {
        const authMethod = values.gmail_auth_method || "service_account";
        payload.auth_type = authMethod;
        payload.config = { 
            mailbox_email: values.mailbox_email,
            relevance_keywords: values.relevance_keywords 
        };

        if (authMethod === "service_account") {
          if (!values.mailbox_email || !values.service_account_json) {
            toast.error("Mailbox Email and Service Account JSON are required for Service Account auth.");
            return;
          }
          try {
            const creds = JSON.parse(values.service_account_json);
            payload.credentials = { service_account_json: creds };
          } catch {
            toast.error("Invalid Service Account JSON.");
            return;
          }
        } else {
          // Personal Account
          if (!values.token_json) {
            toast.error("Authorized User JSON (token.json) is required for Personal Account auth.");
            return;
          }
          try {
            const token = JSON.parse(values.token_json);
            payload.credentials = { user_oauth2_info: token };
          } catch {
            toast.error("Invalid Authorized User JSON (token.json).");
            return;
          }
        }
      }

      const source = await mutation.mutateAsync(payload);
      
      if (values.source_type === "local_file" && selectedFiles.length > 0) {
        toast.info(`Ingesting ${selectedFiles.length} files...`);
        // The content kind is declared, not guessed. This previously
        // inferred it from the filename ("slack" → message, everything
        // else → log), so an uploaded SOP was typed as a log — which
        // costs it knowledge authority in the reranker and keeps it out
        // of long-term memory. The uploader is present at ingest time
        // and knows what these files are; ask them.
        await api.post("/sources/local-ingest", {
          source_id: source.id,
          evidence_type: values.local_content_type || "document",
          files: selectedFiles.map(f => ({
            filename: f.filename,
            content: f.content,
            metadata: {},
          }))
        });
        toast.success("Local ingestion started successfully");
      }
    } catch (err: unknown) {
      // Re-throw or handle if not a mutation error
      if (!(err instanceof Error) || !err.message.includes("Failed to fetch")) {
        console.error("Submission error:", err);
      }
    }
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-[550px] p-0 overflow-hidden flex flex-col max-h-[90vh]">
        <DialogHeader className="p-6 pb-2">
          <DialogTitle>Add Source</DialogTitle>
          <DialogDescription>
            Configure a new data source to ingest evidence into ContextEdge.
          </DialogDescription>
        </DialogHeader>
        
        <form onSubmit={handleSubmit(onSubmit)} className="flex flex-col min-h-0">
          <div className="flex-1 overflow-y-auto px-6 py-4 space-y-4 max-h-[60vh]">
            <div className="space-y-2">
              <Label htmlFor="display_name">Display Name</Label>
              <Input
                id="display_name"
                placeholder="e.g. Local Operations Logs"
                {...register("display_name")}
                aria-invalid={!!errors.display_name}
              />
              {errors.display_name && (
                <p className="text-sm text-destructive">{errors.display_name.message}</p>
              )}
            </div>
            <div className="space-y-2">
              <Label htmlFor="source_type">Source Type</Label>
              <Select
                value={sourceType}
                onValueChange={(value) => setValue("source_type", value ?? "")}
              >
                <SelectTrigger id="source_type" className="w-full">
                  <SelectValue placeholder="Select a source type" />
                </SelectTrigger>
                <SelectContent>
                  {typesLoading && (
                    <SelectItem value="local_file" disabled>
                      Loading source types…
                    </SelectItem>
                  )}
                  {(sourceTypes ?? []).map((option) => {
                    // local_file has no connector by design — it is an
                    // upload path, so it stays selectable. Everything
                    // else without a connector is disabled rather than
                    // hidden: hiding it loses the roadmap signal, but
                    // leaving it selectable creates a source that fails
                    // later, in a worker log, in front of nobody.
                    const selectable =
                      option.connector_available || option.status === "manual";
                    return (
                      <SelectItem
                        key={option.source_type}
                        value={option.source_type}
                        disabled={!selectable}
                      >
                        {option.label}
                        {!selectable ? " — coming soon" : ""}
                      </SelectItem>
                    );
                  })}
                </SelectContent>
              </Select>
              {errors.source_type && (
                <p className="text-sm text-destructive">{errors.source_type.message}</p>
              )}
            </div>

            {sourceType === "gmail" && (
              <div className="space-y-4 p-4 border rounded-lg bg-slate-900/50">
                <div className="space-y-2">
                  <Label>Authentication Method</Label>
                  <Select
                    value={gmailAuthMethod}
                    onValueChange={(v) => {
                      if (v === "service_account" || v === "personal") {
                        setValue("gmail_auth_method", v);
                      }
                    }}
                  >
                    <SelectTrigger className="w-full bg-slate-950/30">
                      <SelectValue placeholder="Select auth method" />
                    </SelectTrigger>
                    <SelectContent>
                      <SelectItem value="service_account">Service Account (DWD)</SelectItem>
                      <SelectItem value="personal">Personal Account (OAuth2 Token)</SelectItem>
                    </SelectContent>
                  </Select>
                </div>

                <div className="space-y-2">
                  <Label htmlFor="mailbox_email">Mailbox Email</Label>
                  <Input
                    id="mailbox_email"
                    placeholder="e.g. your-email@gmail.com"
                    {...register("mailbox_email")}
                    aria-invalid={!!errors.mailbox_email}
                  />
                  {errors.mailbox_email && (
                    <p className="text-sm text-destructive">{errors.mailbox_email.message}</p>
                  )}
                </div>

                <div className="space-y-2">
                  <Label htmlFor="relevance_keywords">Relevance Keywords (Filter)</Label>
                  <Input
                    id="relevance_keywords"
                    placeholder="error, issue, ticket..."
                    {...register("relevance_keywords")}
                  />
                  <p className="text-[10px] text-muted-foreground">
                    Only emails containing these words in the subject or body will be ingested. Separate with commas.
                  </p>
                </div>

                {gmailAuthMethod === "service_account" ? (
                  <div className="space-y-2">
                    <Label htmlFor="service_account_json">Service Account JSON</Label>
                    <Textarea
                      id="service_account_json"
                      className="font-mono text-[10px] min-h-[180px] bg-slate-950/50"
                      placeholder='Paste the content of your Google Service Account JSON key here...'
                      {...register("service_account_json")}
                    />
                    <p className="text-[10px] text-muted-foreground">
                      Standard format: {"{ \"type\": \"service_account\", ... }"}
                    </p>
                  </div>
                ) : (
                  <div className="space-y-2">
                    <Label htmlFor="token_json">Authorized User JSON (token.json)</Label>
                    <Textarea
                      id="token_json"
                      className="font-mono text-[10px] min-h-[220px] bg-slate-950/50"
                      placeholder='Paste the content of your token.json here...'
                      {...register("token_json")}
                    />
                    <p className="text-[10px] text-muted-foreground">
                      This JSON should contain <code>client_id</code>, <code>client_secret</code>, and <code>refresh_token</code>.
                    </p>
                  </div>
                )}
              </div>
            )}

            {sourceType === "servicenow" && (
              <div className="space-y-4 p-4 border rounded-lg bg-slate-900/50">
                <div className="space-y-2">
                  <Label htmlFor="servicenow_instance_url">Instance URL</Label>
                  <Input
                    id="servicenow_instance_url"
                    placeholder="https://example.service-now.com"
                    {...register("servicenow_instance_url")}
                  />
                </div>

                <div className="grid gap-4 sm:grid-cols-2">
                  <div className="space-y-2">
                    <Label htmlFor="servicenow_username">Username</Label>
                    <Input
                      id="servicenow_username"
                      placeholder="integration.user"
                      autoComplete="username"
                      {...register("servicenow_username")}
                    />
                  </div>

                  <div className="space-y-2">
                    <Label htmlFor="servicenow_password">Password</Label>
                    <Input
                      id="servicenow_password"
                      type="password"
                      autoComplete="current-password"
                      {...register("servicenow_password")}
                    />
                  </div>
                </div>

                <div className="space-y-2">
                  <Label htmlFor="servicenow_alert_severity_max">Alert Severity Max</Label>
                  <Input
                    id="servicenow_alert_severity_max"
                    type="number"
                    min={1}
                    max={5}
                    step={1}
                    {...register("servicenow_alert_severity_max")}
                  />
                </div>

                <div className="space-y-2">
                  <Label htmlFor="servicenow_table_filters">Table Filters JSON</Label>
                  <Textarea
                    id="servicenow_table_filters"
                    className="font-mono text-xs min-h-28 bg-slate-950/50"
                    placeholder='{"incident":"priority<=2","change_request":"state=3"}'
                    {...register("servicenow_table_filters")}
                  />
                </div>
              </div>
            )}

            {sourceType === "zoho_desk" && (
              <div className="space-y-4 p-4 border rounded-lg bg-slate-900/50">
                <p className="text-xs text-muted-foreground">
                  Scopes are fixed when the refresh token is issued and cannot be
                  added later. Grant <code>Desk.tickets.READ</code> for tickets and{" "}
                  <code>Desk.articles.READ</code> for the knowledge base — a partial
                  grant syncs what it can and reports the rest.
                </p>

                <div className="grid gap-4 sm:grid-cols-2">
                  <div className="space-y-2">
                    <Label htmlFor="zoho_client_id">Client ID</Label>
                    <Input
                      id="zoho_client_id"
                      placeholder="1000.XXXXXXXXXXXXXXXXXXXX"
                      {...register("zoho_client_id")}
                    />
                  </div>

                  <div className="space-y-2">
                    <Label htmlFor="zoho_client_secret">Client Secret</Label>
                    <Input
                      id="zoho_client_secret"
                      type="password"
                      autoComplete="off"
                      {...register("zoho_client_secret")}
                    />
                  </div>
                </div>

                <div className="space-y-2">
                  <Label htmlFor="zoho_refresh_token">Refresh Token</Label>
                  <Input
                    id="zoho_refresh_token"
                    type="password"
                    autoComplete="off"
                    {...register("zoho_refresh_token")}
                  />
                </div>

                <div className="grid gap-4 sm:grid-cols-2">
                  <div className="space-y-2">
                    <Label htmlFor="zoho_org_id">Org ID</Label>
                    <Input
                      id="zoho_org_id"
                      placeholder="60001911841"
                      {...register("zoho_org_id")}
                    />
                  </div>

                  <div className="space-y-2">
                    <Label htmlFor="zoho_data_center">Data Center</Label>
                    <Select
                      value={zohoDataCenter || "com"}
                      onValueChange={(value) =>
                        setValue("zoho_data_center", value ?? "com")
                      }
                    >
                      <SelectTrigger id="zoho_data_center" className="w-full">
                        <SelectValue placeholder="Select a data center" />
                      </SelectTrigger>
                      <SelectContent>
                        {ZOHO_DATA_CENTERS.map((dc) => (
                          <SelectItem key={dc.value} value={dc.value}>
                            {dc.label}
                          </SelectItem>
                        ))}
                      </SelectContent>
                    </Select>
                    <p className="text-xs text-muted-foreground">
                      Must match your portal&apos;s domain — a cross-region call
                      fails authentication.
                    </p>
                  </div>
                </div>

                <div className="space-y-2">
                  <Label htmlFor="zoho_modules">Modules (optional)</Label>
                  <Input
                    id="zoho_modules"
                    placeholder="tickets, articles"
                    {...register("zoho_modules")}
                  />
                  <p className="text-xs text-muted-foreground">
                    Leave empty to sync every module the token can read.
                  </p>
                </div>

                <div className="space-y-2">
                  <Label htmlFor="zoho_ticket_status">Ticket Status Filter (optional)</Label>
                  <Input
                    id="zoho_ticket_status"
                    placeholder="e.g. Closed"
                    {...register("zoho_ticket_status")}
                  />
                  <p className="text-xs text-muted-foreground">
                    Filter tickets by status (e.g., <code>Closed</code>). Leave empty to sync all ticket statuses.
                  </p>
                </div>

                <div className="grid gap-4 sm:grid-cols-2">
                  <div className="space-y-2">
                    <Label htmlFor="zoho_max_days">Max Age in Days (optional)</Label>
                    <Input
                      id="zoho_max_days"
                      type="number"
                      placeholder="e.g. 30"
                      {...register("zoho_max_days")}
                    />
                    <p className="text-xs text-muted-foreground">
                      Only sync tickets modified within the last X days.
                    </p>
                  </div>

                  <div className="space-y-2">
                    <Label htmlFor="zoho_max_records">Max Record Count (optional)</Label>
                    <Input
                      id="zoho_max_records"
                      type="number"
                      placeholder="e.g. 500"
                      {...register("zoho_max_records")}
                    />
                    <p className="text-xs text-muted-foreground">
                      Limit total count of tickets to fetch per sync.
                    </p>
                  </div>
                </div>

                <div className="flex items-start gap-2">
                  <input
                    id="zoho_per_department"
                    type="checkbox"
                    className="mt-1"
                    {...register("zoho_per_department")}
                  />
                  <Label
                    htmlFor="zoho_per_department"
                    className="text-sm font-normal leading-snug"
                  >
                    Sync tickets per department
                    <span className="block text-xs text-muted-foreground">
                      Offers one approvable object per department instead of one
                      for all tickets. Needs <code>Desk.settings.READ</code>.
                    </span>
                  </Label>
                </div>
              </div>
            )}

            {sourceType === "sapphireims" && (
              <div className="space-y-4 p-4 border rounded-lg bg-slate-900/50">
                <p className="text-xs text-muted-foreground">
                  SapphireIMS endpoint paths and payload field names are
                  instance-specific. The defaults are a starting point — verify
                  them against your instance&apos;s API guide, then use
                  Probe Config on the source to confirm the mapping before the
                  first sync.
                </p>

                <div className="space-y-2">
                  <Label htmlFor="sapphire_base_url">Base URL</Label>
                  <Input
                    id="sapphire_base_url"
                    placeholder="https://itsm.example.com"
                    {...register("sapphire_base_url")}
                  />
                </div>

                <div className="grid gap-4 sm:grid-cols-2">
                  <div className="space-y-2">
                    <Label htmlFor="sapphire_api_key">API Key</Label>
                    <Input
                      id="sapphire_api_key"
                      type="password"
                      autoComplete="off"
                      {...register("sapphire_api_key")}
                    />
                  </div>

                  <div className="space-y-2">
                    <Label htmlFor="sapphire_auth_token">Auth Token</Label>
                    <Input
                      id="sapphire_auth_token"
                      type="password"
                      autoComplete="off"
                      {...register("sapphire_auth_token")}
                    />
                  </div>
                </div>

                <div className="space-y-2">
                  <Label htmlFor="sapphire_projects">Projects</Label>
                  <Input
                    id="sapphire_projects"
                    placeholder="ACME-IT, ACME-OPS"
                    {...register("sapphire_projects")}
                  />
                  <p className="text-xs text-muted-foreground">
                    Comma-separated. Required — there is no public endpoint to
                    enumerate projects, so discovery only sees what you list.
                  </p>
                </div>
              </div>
            )}

            {sourceType === "manageengine" && (
              <div className="space-y-4 p-4 border rounded-lg bg-slate-900/50">
                <p className="text-xs text-muted-foreground">
                  ManageEngine ServiceDesk Plus V3 API connector. Ingests service desk tickets, requests, and KB articles.
                </p>

                <div className="space-y-2">
                  <Label htmlFor="manageengine_base_url">ServiceDesk Plus Base URL</Label>
                  <Input
                    id="manageengine_base_url"
                    placeholder="https://servicedesk.example.com"
                    {...register("manageengine_base_url")}
                  />
                  <p className="text-xs text-muted-foreground">
                    The full URL to your ServiceDesk Plus instance (e.g., https://sdp.acme.com)
                  </p>
                </div>

                <div className="space-y-2">
                  <Label htmlFor="manageengine_api_key">API Key (TECHNICIAN_KEY)</Label>
                  <Input
                    id="manageengine_api_key"
                    type="password"
                    autoComplete="off"
                    placeholder="Your TECHNICIAN_KEY"
                    {...register("manageengine_api_key")}
                  />
                  <p className="text-xs text-muted-foreground">
                    Create an API key for a technician account in ServiceDesk Plus
                  </p>
                </div>

                <div className="space-y-2">
                  <Label htmlFor="manageengine_table_filters">Table Filters (optional)</Label>
                  <Textarea
                    id="manageengine_table_filters"
                    className="font-mono text-xs min-h-24 bg-slate-950/50"
                    placeholder='{"requests":"priority<=2"}'
                    {...register("manageengine_table_filters")}
                  />
                  <p className="text-xs text-muted-foreground">
                    Optional JSON filters to scope syncs by table (e.g., priority, status). Server-side filtering saves tokens and storage.
                  </p>
                </div>
              </div>
            )}

            {sourceType === "local_file" && (
              <div className="space-y-3 rounded-lg border border-dashed p-4">
                <div className="space-y-2">
                  <Label htmlFor="local_content_type">Content Type</Label>
                  <Select
                    value={localContentType || "document"}
                    onValueChange={(value) =>
                      setValue("local_content_type", value ?? "document")
                    }
                  >
                    <SelectTrigger id="local_content_type" className="w-full">
                      <SelectValue placeholder="What kind of files are these?" />
                    </SelectTrigger>
                    <SelectContent>
                      <SelectItem value="kb_article">Knowledge Base Articles</SelectItem>
                      <SelectItem value="sop">SOPs / Standard Procedures</SelectItem>
                      <SelectItem value="runbook">Runbooks</SelectItem>
                      <SelectItem value="documentation">Product Documentation</SelectItem>
                      <SelectItem value="postmortem">Post-mortems</SelectItem>
                      <SelectItem value="transcript">Transcripts</SelectItem>
                      <SelectItem value="document">Other Documents</SelectItem>
                      <SelectItem value="message">Chat / Message Logs</SelectItem>
                    </SelectContent>
                  </Select>
                  <p className="text-xs text-muted-foreground">
                    Knowledge types (KB articles, SOPs, documentation) are held as
                    long-term memory and carry knowledge authority rather than
                    ticket authority when ranked.
                  </p>
                </div>

                <div className="flex items-center justify-between">
                  <div className="space-y-0.5">
                    <Label className="text-sm font-medium">Reconstruction Data</Label>
                    <p className="text-xs text-muted-foreground">
                      Select a local folder containing your evidence logs.
                    </p>
                  </div>
                  <Button 
                    type="button" 
                    variant="secondary" 
                    size="sm" 
                    onClick={handleBrowseFolder}
                    disabled={isReading}
                  >
                    {isReading ? (
                      <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                    ) : (
                      <FolderOpen className="mr-2 h-4 w-4" />
                    )}
                    Browse
                  </Button>
                </div>
                
                {selectedFiles.length > 0 && (
                  <div className="flex items-center gap-2 text-sm text-primary">
                    <FileText className="h-4 w-4" />
                    <span>{selectedFiles.length} files ready for ingestion</span>
                  </div>
                )}
              </div>
            )}
            <div className="space-y-2 pb-2">
              <Label htmlFor="purpose">Purpose (Optional)</Label>
              <Textarea
                id="purpose"
                placeholder="Describe what this source is for..."
                {...register("purpose")}
              />
            </div>
          </div>
          
          <DialogFooter className="p-6 border-t bg-slate-900/10">
            <Button
              type="button"
              variant="outline"
              onClick={() => onOpenChange(false)}
            >
              Cancel
            </Button>
            <Button type="submit" disabled={mutation.isPending || isReading}>
              {mutation.isPending ? "Creating..." : sourceType === "local_file" ? "Create & Ingest" : "Create Source"}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}
