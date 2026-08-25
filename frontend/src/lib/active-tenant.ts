export const ACTIVE_TENANT_KEY = "active_tenant_id";

function store(): Storage | null {
  if (typeof window === "undefined") return null;
  return window.sessionStorage;
}

export function readActiveTenantId(): string | null {
  const storage = store();
  if (!storage) return null;
  return storage.getItem(ACTIVE_TENANT_KEY);
}

export function writeActiveTenantId(tenantId: string | null): void {
  const storage = store();
  if (!storage) return;
  try {
    window.localStorage.removeItem(ACTIVE_TENANT_KEY);
  } catch {
    /* ignore quota / privacy mode */
  }
  if (tenantId) storage.setItem(ACTIVE_TENANT_KEY, tenantId);
  else storage.removeItem(ACTIVE_TENANT_KEY);
}
