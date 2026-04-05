import { useQuery } from "@tanstack/react-query";
import { api } from "../api";
import type { Domain, Workspace } from "../types";

export function useWorkspaces() {
  return useQuery<Workspace[]>({
    queryKey: ["workspaces"],
    queryFn: () => api.get("/workspaces"),
  });
}

export function useDomains(workspaceId?: string) {
  return useQuery<Domain[]>({
    queryKey: ["domains", workspaceId],
    queryFn: () =>
      api.get("/domains", workspaceId ? { workspace_id: workspaceId } : {}),
  });
}
