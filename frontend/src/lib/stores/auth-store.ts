import { create } from "zustand";
import { parseToken } from "../auth";

interface AuthState {
  isAuthenticated: boolean;
  userId: string | null;
  tenantId: string | null;
  email: string | null;
  roles: string[];
  hydrate: () => void;
  setAuthenticated: (token: string) => void;
  clearAuth: () => void;
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
      set({
        isAuthenticated: true,
        userId: payload.sub,
        tenantId: payload.tenant_id,
        email: payload.email,
        roles: payload.roles,
      });
    }
  },
  setAuthenticated: (token: string) => {
    localStorage.setItem("access_token", token);
    const payload = parseToken();
    if (payload) {
      set({
        isAuthenticated: true,
        userId: payload.sub,
        tenantId: payload.tenant_id,
        email: payload.email,
        roles: payload.roles,
      });
    }
  },
  clearAuth: () => {
    localStorage.removeItem("access_token");
    set({
      isAuthenticated: false,
      userId: null,
      tenantId: null,
      email: null,
      roles: [],
    });
  },
}));
