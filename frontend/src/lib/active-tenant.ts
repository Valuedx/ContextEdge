export const ACTIVE_TENANT_KEY = "active_tenant_id";

export function readActiveTenantId(): string | null {
  if (typeof window === "undefined") return null;
  return localStorage.getItem(ACTIVE_TENANT_KEY);
}

export function writeActiveTenantId(tenantId: string | null): void {
  if (typeof window === "undefined") return;
  if (tenantId) localStorage.setItem(ACTIVE_TENANT_KEY, tenantId);
  else localStorage.removeItem(ACTIVE_TENANT_KEY);
}
