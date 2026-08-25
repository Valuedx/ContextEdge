"use client";

import { useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Loader2, KeyRound } from "lucide-react";
import { toast } from "sonner";

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
import { api } from "@/lib/api";
import type { Source } from "@/lib/types";

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

interface RotateCredentialsDialogProps {
  source: Source;
  open: boolean;
  onOpenChange: (open: boolean) => void;
}

export function RotateCredentialsDialog({
  source,
  open,
  onOpenChange,
}: RotateCredentialsDialogProps) {
  const queryClient = useQueryClient();

  // Zoho fields
  const [zohoClientId, setZohoClientId] = useState("");
  const [zohoClientSecret, setZohoClientSecret] = useState("");
  const [zohoRefreshToken, setZohoRefreshToken] = useState("");
  const [zohoOrgId, setZohoOrgId] = useState("");
  const [zohoDataCenter, setZohoDataCenter] = useState("in");

  // ServiceNow / Generic Basic Auth fields
  const [serviceUrl, setServiceUrl] = useState("");
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");

  // API Token / Generic JSON
  const [apiToken, setApiToken] = useState("");
  const [customJson, setCustomJson] = useState("");

  const rotateMut = useMutation({
    mutationFn: async () => {
      let credentials: Record<string, unknown> = {};

      if (source.source_type === "zoho_desk") {
        if (!zohoClientId.trim() || !zohoClientSecret.trim() || !zohoRefreshToken.trim()) {
          throw new Error("Client ID, Client Secret, and Refresh Token are required for Zoho Desk");
        }
        credentials = {
          client_id: zohoClientId.trim(),
          client_secret: zohoClientSecret.trim(),
          refresh_token: zohoRefreshToken.trim(),
          org_id: zohoOrgId.trim() || undefined,
          data_center: zohoDataCenter,
        };
      } else if (source.source_type === "servicenow") {
        credentials = {
          instance_url: serviceUrl.trim(),
          username: username.trim(),
          password: password,
        };
      } else if (source.source_type === "jira_sm") {
        credentials = {
          base_url: serviceUrl.trim(),
          email: username.trim(),
          api_token: apiToken.trim(),
        };
      } else if (source.source_type === "manageengine" || source.source_type === "sapphireims") {
        credentials = {
          base_url: serviceUrl.trim(),
          api_key: apiToken.trim(),
        };
      } else {
        try {
          credentials = customJson.trim() ? JSON.parse(customJson) : {};
        } catch {
          throw new Error("Credentials must be valid JSON");
        }
      }

      return api.post(`/sources/${source.id}/credentials/rotate`, {
        credentials,
        auth_type: source.auth_type,
      });
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["source", source.id] });
      queryClient.invalidateQueries({ queryKey: ["sources"] });
      onOpenChange(false);
      toast.success("Credentials successfully updated and encrypted!");
    },
    onError: (err: Error) => {
      toast.error(err.message || "Credential rotation failed");
    },
  });

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-[540px]">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            <KeyRound className="h-5 w-5 text-primary" />
            Rotate Credentials
          </DialogTitle>
          <DialogDescription>
            Enter new credentials for {source.display_name}. They will be securely encrypted with the server&apos;s current Fernet key.
          </DialogDescription>
        </DialogHeader>

        <div className="space-y-4 py-2">
          {source.source_type === "zoho_desk" && (
            <div className="space-y-3">
              <div className="grid gap-3 sm:grid-cols-2">
                <div className="space-y-1.5">
                  <Label htmlFor="rot_zoho_client_id">Client ID</Label>
                  <Input
                    id="rot_zoho_client_id"
                    placeholder="1000.XXXXX"
                    value={zohoClientId}
                    onChange={(e) => setZohoClientId(e.target.value)}
                  />
                </div>
                <div className="space-y-1.5">
                  <Label htmlFor="rot_zoho_client_secret">Client Secret</Label>
                  <Input
                    id="rot_zoho_client_secret"
                    type="password"
                    autoComplete="off"
                    value={zohoClientSecret}
                    onChange={(e) => setZohoClientSecret(e.target.value)}
                  />
                </div>
              </div>

              <div className="space-y-1.5">
                <Label htmlFor="rot_zoho_refresh_token">Refresh Token</Label>
                <Input
                  id="rot_zoho_refresh_token"
                  type="password"
                  autoComplete="off"
                  placeholder="1000.XXXXX.XXXXX"
                  value={zohoRefreshToken}
                  onChange={(e) => setZohoRefreshToken(e.target.value)}
                />
              </div>

              <div className="grid gap-3 sm:grid-cols-2">
                <div className="space-y-1.5">
                  <Label htmlFor="rot_zoho_org_id">Org ID</Label>
                  <Input
                    id="rot_zoho_org_id"
                    placeholder="60001911841"
                    value={zohoOrgId}
                    onChange={(e) => setZohoOrgId(e.target.value)}
                  />
                </div>
                <div className="space-y-1.5">
                  <Label htmlFor="rot_zoho_dc">Data Center</Label>
                  <Select
                    value={zohoDataCenter}
                    onValueChange={(value) => setZohoDataCenter(value ?? "com")}
                  >
                    <SelectTrigger id="rot_zoho_dc">
                      <SelectValue placeholder="Select Data Center" />
                    </SelectTrigger>
                    <SelectContent>
                      {ZOHO_DATA_CENTERS.map((dc) => (
                        <SelectItem key={dc.value} value={dc.value}>
                          {dc.label}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                </div>
              </div>
            </div>
          )}

          {source.source_type === "servicenow" && (
            <div className="space-y-3">
              <div className="space-y-1.5">
                <Label htmlFor="rot_sn_url">Instance URL</Label>
                <Input
                  id="rot_sn_url"
                  placeholder="https://instance.service-now.com"
                  value={serviceUrl}
                  onChange={(e) => setServiceUrl(e.target.value)}
                />
              </div>
              <div className="grid gap-3 sm:grid-cols-2">
                <div className="space-y-1.5">
                  <Label htmlFor="rot_sn_user">Username</Label>
                  <Input
                    id="rot_sn_user"
                    value={username}
                    onChange={(e) => setUsername(e.target.value)}
                  />
                </div>
                <div className="space-y-1.5">
                  <Label htmlFor="rot_sn_pass">Password</Label>
                  <Input
                    id="rot_sn_pass"
                    type="password"
                    value={password}
                    onChange={(e) => setPassword(e.target.value)}
                  />
                </div>
              </div>
            </div>
          )}

          {source.source_type !== "zoho_desk" && source.source_type !== "servicenow" && (
            <div className="space-y-1.5">
              <Label htmlFor="rot_custom_json">Credentials JSON</Label>
              <Textarea
                id="rot_custom_json"
                placeholder='{"api_key": "..."}'
                className="font-mono text-xs min-h-28"
                value={customJson}
                onChange={(e) => setCustomJson(e.target.value)}
              />
            </div>
          )}
        </div>

        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)}>
            Cancel
          </Button>
          <Button
            onClick={() => rotateMut.mutate()}
            disabled={rotateMut.isPending}
          >
            {rotateMut.isPending ? (
              <Loader2 className="mr-2 h-4 w-4 animate-spin" />
            ) : (
              <KeyRound className="mr-2 h-4 w-4" />
            )}
            Save &amp; Rotate
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
