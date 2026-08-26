import { readActiveTenantId } from "./active-tenant";
import { isPlatformSuperAdmin } from "./roles";

function getApiBase(): string {
  if (typeof window !== "undefined" && window.location.hostname) {
    const protocol = window.location.protocol || "http:";
    const host = window.location.hostname;
    // If NEXT_PUBLIC_API_URL is explicitly set and not localhost, use it
    if (process.env.NEXT_PUBLIC_API_URL && !process.env.NEXT_PUBLIC_API_URL.includes("localhost")) {
      return process.env.NEXT_PUBLIC_API_URL;
    }
    return `${protocol}//${host}:8001`;
  }
  return process.env.NEXT_PUBLIC_API_URL || "http://localhost:8001";
}

function _tokenRoles(): string[] {
  if (typeof window === "undefined") return [];
  const token = localStorage.getItem("access_token");
  if (!token) return [];
  try {
    const payload = JSON.parse(atob(token.split(".")[1] || "")) as { roles?: unknown };
    return Array.isArray(payload.roles) ? payload.roles.filter((r): r is string => typeof r === "string") : [];
  } catch {
    return [];
  }
}

export class ApiError extends Error {
  status: number;
  code?: string;
  detail?: unknown;
  currentRevision?: number;
  updatedAt?: string;

  constructor(message: string, status: number, detail?: unknown) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.detail = detail;
    if (detail && typeof detail === "object") {
      const d = detail as {
        code?: unknown;
        current_revision?: unknown;
        updated_at?: unknown;
      };
      if (typeof d.code === "string") this.code = d.code;
      if (typeof d.current_revision === "number") this.currentRevision = d.current_revision;
      if (typeof d.updated_at === "string") this.updatedAt = d.updated_at;
    }
  }
}

class ApiClient {
  private baseUrl?: string;

  constructor(baseUrl?: string) {
    this.baseUrl = baseUrl;
  }

  private getBaseUrl(): string {
    return this.baseUrl || getApiBase();
  }

  private getToken(): string | null {
    if (typeof window === "undefined") return null;
    return localStorage.getItem("access_token");
  }

  private async request<T>(
    path: string,
    options: RequestInit = {}
  ): Promise<T> {
    const token = this.getToken();
    const headers: Record<string, string> = {
      "Content-Type": "application/json",
      ...(options.headers as Record<string, string>),
    };
    if (token) {
      headers["Authorization"] = `Bearer ${token}`;
    }
    const activeTenant = readActiveTenantId();
    if (activeTenant && isPlatformSuperAdmin(_tokenRoles())) {
      headers["X-Tenant-Id"] = activeTenant;
    }
    if (typeof crypto !== "undefined" && "randomUUID" in crypto) {
      headers["X-Request-ID"] = crypto.randomUUID();
    }

    const res = await fetch(`${this.getBaseUrl()}${path}`, {
      ...options,
      headers,
    });

    if (res.status === 401) {
      if (typeof window !== "undefined") {
        localStorage.removeItem("access_token");
        window.location.href = "/login";
      }
      throw new Error("Unauthorized");
    }

    if (!res.ok) {
      const body = (await res.json().catch(() => ({}))) as {
        detail?: string | unknown;
      };
      const detail = body.detail;
      let message: string;
      if (typeof detail === "string") {
        message = detail;
      } else if (Array.isArray(detail)) {
        message = detail
          .map((d) =>
            typeof d === "object" && d !== null && "msg" in d
              ? String((d as { msg: string }).msg)
              : JSON.stringify(d),
          )
          .join("; ");
      } else if (detail != null && typeof detail === "object" && "message" in detail) {
        message = String((detail as { message: unknown }).message);
      } else if (detail != null) {
        message = JSON.stringify(detail);
      } else {
        message = `Request failed: ${res.status}`;
      }
      throw new ApiError(message, res.status, detail);
    }

    if (res.status === 204) return undefined as T;
    return res.json();
  }

  get<T>(path: string, params?: Record<string, string>) {
    const query = params
      ? "?" + new URLSearchParams(params).toString()
      : "";
    return this.request<T>(`/api/v1${path}${query}`);
  }

  post<T>(path: string, body?: unknown) {
    return this.request<T>(`/api/v1${path}`, {
      method: "POST",
      body: body ? JSON.stringify(body) : undefined,
    });
  }

  patch<T>(path: string, body: unknown) {
    return this.request<T>(`/api/v1${path}`, {
      method: "PATCH",
      body: JSON.stringify(body),
    });
  }

  put<T>(path: string, body: unknown) {
    return this.request<T>(`/api/v1${path}`, {
      method: "PUT",
      body: JSON.stringify(body),
    });
  }

  delete<T>(path: string) {
    return this.request<T>(`/api/v1${path}`, { method: "DELETE" });
  }
}

export const api = new ApiClient();
