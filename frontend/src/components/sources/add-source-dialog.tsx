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

const sourceSchema = z.z.object({
  display_name: z.string().min(1, "Display name is required").max(255),
  source_type: z.string().min(1, "Source type is required"),
  purpose: z.string().optional(),
});

type SourceFormValues = z.infer<typeof sourceSchema>;

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
    },
  });

  const sourceType = useWatch({ control, name: "source_type" });
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
    mutationFn: (values: SourceFormValues) => api.post("/sources", values),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["sources"] });
      onOpenChange(false);
      reset();
    },
    onError: (error: Error) => {
      console.error(`Failed to create source: ${error.message}`);
    },
  });

  const onSubmit = async (values: SourceFormValues) => {
    try {
      const source = await mutation.mutateAsync(values) as { id: string };
      
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
    } catch {
      // Error handled by mutation or manually
    }
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-[425px]">
        <DialogHeader>
          <DialogTitle>Add Source</DialogTitle>
          <DialogDescription>
            Configure a new data source to ingest evidence into ContextEdge.
          </DialogDescription>
        </DialogHeader>
        <form onSubmit={handleSubmit(onSubmit)} className="space-y-4 py-4">
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
              defaultValue="local_file"
              onValueChange={(value) => setValue("source_type", value ?? "")}
            >
              <SelectTrigger id="source_type" className="w-full">
                <SelectValue placeholder="Select a source type" />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="local_file">Local Directory / Files</SelectItem>
                <SelectItem value="gmail">Gmail (Cloud - Disabled)</SelectItem>
                <SelectItem value="teams">MS Teams (Cloud - Disabled)</SelectItem>
                <SelectItem value="servicenow">ServiceNow (Cloud - Disabled)</SelectItem>
                <SelectItem value="jira_sm">Jira Service Management (Cloud - Disabled)</SelectItem>
              </SelectContent>
            </Select>
            {errors.source_type && (
              <p className="text-sm text-destructive">{errors.source_type.message}</p>
            )}
          </div>

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
          <div className="space-y-2">
            <Label htmlFor="purpose">Purpose (Optional)</Label>
            <Textarea
              id="purpose"
              placeholder="Describe what this source is for..."
              {...register("purpose")}
            />
          </div>
          <DialogFooter className="pt-4">
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
