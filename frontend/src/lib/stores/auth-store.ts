import { create } from "zustand";
import { parseToken } from "../auth";
import { readActiveTenantId, writeActiveTenantId } from "../active-tenant";

interface AuthState {
  isAuthenticated: boolean;
  userId: string | null;
  tenantId: string | null;
  email: string | null;
  roles: string[];
  hydrate: () => void;
  setAuthenticated: (token: string) => void;
  setTenantContext: (tenantId: string) => void;
  clearAuth: () => void;
}

function resolveTenantId(payload: { tenant_id: string; roles: string[] }): string {
  const stored = readActiveTenantId();
  if (stored && payload.roles.includes("platform_super_admin")) {
    return stored;
  }
  return payload.tenant_id;
}

export const useAuthStore = create<AuthState>((set) => ({
  isAuthenticated: false,
  userId: null,
  tenantId: null,
  email: null,
  roles: [],
  hydrate: () => {
    const payload = parseToken();
    if (payload) {
      const tenantId = resolveTenantId(payload);
      if (payload.roles.includes("platform_super_admin")) {
        writeActiveTenantId(tenantId);
      }
      set({
        isAuthenticated: true,
        userId: payload.sub,
        tenantId,
        email: payload.username || payload.email,
        roles: payload.roles,
      });
    }
  },
  setAuthenticated: (token: string) => {
    localStorage.setItem("access_token", token);
    const payload = parseToken();
    if (payload) {
      const tenantId = resolveTenantId(payload);
      if (payload.roles.includes("platform_super_admin")) {
        writeActiveTenantId(tenantId);
      } else {
        writeActiveTenantId(null);
      }
      set({
        isAuthenticated: true,
        userId: payload.sub,
        tenantId,
        email: payload.username || payload.email,
        roles: payload.roles,
      });
    }
  },
  setTenantContext: (tenantId: string) => {
    writeActiveTenantId(tenantId);
    set({ tenantId });
  },
  clearAuth: () => {
    localStorage.removeItem("access_token");
    writeActiveTenantId(null);
    set({
      isAuthenticated: false,
      userId: null,
      tenantId: null,
      email: null,
      roles: [],
    });
  },
}));
