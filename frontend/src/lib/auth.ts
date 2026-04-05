import { api } from "./api";

interface TokenResponse {
  access_token: string;
  token_type: string;
  expires_in: number;
}

interface JwtPayload {
  sub: string;
  tenant_id: string;
  email: string;
  roles: string[];
  exp: number;
}

export async function login(
  email: string,
  password: string
): Promise<TokenResponse> {
  const res = await api.post<TokenResponse>("/auth/login", { email, password });
  if (typeof window !== "undefined") {
    localStorage.setItem("access_token", res.access_token);
  }
  return res;
}

export function logout() {
  if (typeof window !== "undefined") {
    localStorage.removeItem("access_token");
    window.location.href = "/login";
  }
}

export function getToken(): string | null {
  if (typeof window === "undefined") return null;
  return localStorage.getItem("access_token");
}

export function parseToken(): JwtPayload | null {
  const token = getToken();
  if (!token) return null;
  try {
    const parts = token.split(".");
    const payload = JSON.parse(atob(parts[1]));
    if (payload.exp * 1000 < Date.now()) {
      logout();
      return null;
    }
    return payload;
  } catch {
    return null;
  }
}

export function isAuthenticated(): boolean {
  return parseToken() !== null;
}

export function hasRole(role: string): boolean {
  const payload = parseToken();
  if (!payload) return false;
  return (
    payload.roles.includes(role) ||
    payload.roles.includes("platform_super_admin")
  );
}
