"use client";
import { useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
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
  // ServiceNow specific fields
  servicenow_instance_url: z.string().optional().or(z.literal("")),
  servicenow_username: z.string().optional().or(z.literal("")),
  servicenow_password: z.string().optional().or(z.literal("")),
  servicenow_table_filters: z.string().optional().or(z.literal("")),
  servicenow_alert_severity_max: z.string().optional().or(z.literal("")),
});

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
      token_json: "",
      servicenow_instance_url: "",
      servicenow_username: "",
      servicenow_password: "",
      servicenow_table_filters: "",
      servicenow_alert_severity_max: "3",
    },
  });

  const sourceType = useWatch({ control, name: "source_type" });
  const gmailAuthMethod = useWatch({ control, name: "gmail_auth_method" });
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
        await api.post("/sources/local-ingest", {
          source_id: source.id,
          files: selectedFiles.map(f => ({
            filename: f.filename,
            content: f.content,
            metadata: { evidence_type: f.filename.includes("slack") ? "message" : "log" }
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
                  <SelectItem value="local_file">Local Directory / Files</SelectItem>
                  <SelectItem value="gmail">Gmail</SelectItem>
                  <SelectItem value="teams">MS Teams</SelectItem>
                  <SelectItem value="servicenow">ServiceNow</SelectItem>
                  <SelectItem value="jira_sm">Jira Service Management</SelectItem>
                  <SelectItem value="confluence">Confluence</SelectItem>
                  <SelectItem value="sharepoint">SharePoint</SelectItem>
                  <SelectItem value="exchange">Exchange</SelectItem>
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

            {sourceType === "local_file" && (
              <div className="space-y-3 rounded-lg border border-dashed p-4">
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
