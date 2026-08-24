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
      } else if (detail != null) {
        message = JSON.stringify(detail);
      } else {
        message = `Request failed: ${res.status}`;
      }
      throw new Error(message);
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
