/**
 * Client session for multi-tenant API auth (Bearer + X-Tenant-Id).
 * When the API reports auth disabled, tokens are cleared and the Studio runs in legacy mode.
 */

const TOKEN_KEY = "director_auth_token";
const TENANT_KEY = "director_auth_tenant_id";

export function getDirectorAuthToken() {
  try {
    return localStorage.getItem(TOKEN_KEY) || "";
  } catch {
    return "";
  }
}

export function getDirectorTenantId() {
  try {
    return localStorage.getItem(TENANT_KEY) || "";
  } catch {
    return "";
  }
}

export function setDirectorAuthSession({ accessToken, tenantId }) {
  try {
    if (accessToken) localStorage.setItem(TOKEN_KEY, accessToken);
    else localStorage.removeItem(TOKEN_KEY);
    if (tenantId) localStorage.setItem(TENANT_KEY, tenantId);
    else localStorage.removeItem(TENANT_KEY);
  } catch {
    /* ignore */
  }
}

export function clearDirectorAuthSession() {
  try {
    localStorage.removeItem(TOKEN_KEY);
    localStorage.removeItem(TENANT_KEY);
  } catch {
    /* ignore */
  }
}

/**
 * Drop a stored workspace id when there is no JWT. Otherwise many routes see Bearer missing and
 * return 401 "missing credentials" even though the real problem is a half-cleared session.
 */
export function normalizeDirectorAuthStorage() {
  try {
    const token = (localStorage.getItem(TOKEN_KEY) || "").trim();
    const tenant = (localStorage.getItem(TENANT_KEY) || "").trim();
    if (!token && tenant) localStorage.removeItem(TENANT_KEY);
  } catch {
    /* ignore */
  }
}

/**
 * After GET /v1/auth/me succeeds, persist a workspace id whenever local storage is empty or holds
 * a tenant the user no longer belongs to (stale tab, partial clear, or first load after OAuth).
 */
export function syncDirectorTenantFromMePayload(meData) {
  if (!meData || typeof meData !== "object") return false;
  const token = getDirectorAuthToken().trim();
  if (!token) return false;
  const tenants = Array.isArray(meData.tenants) ? meData.tenants : [];
  const tenantSet = new Set(tenants.map((t) => String(t?.id || "").trim()).filter(Boolean));
  const active = String(meData.active_tenant_id || meData.tenant_id || "").trim();
  const fallback = tenants.length ? String(tenants[0].id || "").trim() : "";
  const pick = active || fallback;
  if (!pick) return false;
  const cur = getDirectorTenantId().trim();
  if (!cur || !tenantSet.has(cur)) {
    setDirectorAuthSession({ accessToken: token, tenantId: pick });
    return true;
  }
  return false;
}

/**
 * Headers merged into `api()` when SaaS session values exist.
 * Send ``X-Tenant-Id`` only together with ``Authorization``: tenant-without-token produced 401
 * "missing credentials" on the API; token-without-tenant still allows /v1/auth/me until we sync.
 */
export function directorAuthHeaders() {
  const token = getDirectorAuthToken().trim();
  const tenant = getDirectorTenantId().trim();
  const out = {};
  if (token) out.Authorization = `Bearer ${token}`;
  if (token && tenant) out["X-Tenant-Id"] = tenant;
  return out;
}

/** Query suffix for EventSource (no custom headers). */
export function directorAuthQuerySuffix() {
  const token = getDirectorAuthToken().trim();
  const tenant = getDirectorTenantId().trim();
  if (!token || !tenant) return "";
  const p = new URLSearchParams();
  p.set("access_token", token);
  p.set("tenant_id", tenant);
  return `&${p.toString()}`;
}
