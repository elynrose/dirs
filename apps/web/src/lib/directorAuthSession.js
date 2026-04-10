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

/** Headers merged into `api()` when a token and tenant are stored. */
export function directorAuthHeaders() {
  const token = getDirectorAuthToken().trim();
  const tenant = getDirectorTenantId().trim();
  if (!token || !tenant) return {};
  return {
    Authorization: `Bearer ${token}`,
    "X-Tenant-Id": tenant,
  };
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
