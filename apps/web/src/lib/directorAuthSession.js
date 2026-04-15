/**
 * Client session for multi-tenant SaaS: HttpOnly cookie carries API auth; we persist
 * workspace id + a media-only JWT for query strings (<img>, EventSource) that cannot send cookies reliably.
 */

const LEGACY_TOKEN_KEY = "director_auth_token";
const MEDIA_JWT_KEY = "director_media_jwt";
const TENANT_KEY = "director_auth_tenant_id";

/** JWT used only for ``access_token`` / ``tenant_id`` query params (not ``Authorization``). */
export function getDirectorAuthToken() {
  try {
    return localStorage.getItem(MEDIA_JWT_KEY) || "";
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

/**
 * @param {{ accessToken?: string, mediaAccessToken?: string, tenantId?: string }} opts
 * ``accessToken`` is accepted as an alias for ``mediaAccessToken`` (login JSON field name).
 */
export function setDirectorAuthSession({ accessToken, mediaAccessToken, tenantId }) {
  const media = (mediaAccessToken ?? accessToken ?? "").trim();
  try {
    try {
      localStorage.removeItem(LEGACY_TOKEN_KEY);
    } catch {
      /* ignore */
    }
    if (media) localStorage.setItem(MEDIA_JWT_KEY, media);
    else localStorage.removeItem(MEDIA_JWT_KEY);
    if (tenantId) localStorage.setItem(TENANT_KEY, tenantId);
    else localStorage.removeItem(TENANT_KEY);
  } catch {
    /* ignore */
  }
}

export function clearDirectorAuthSession() {
  try {
    localStorage.removeItem(LEGACY_TOKEN_KEY);
    localStorage.removeItem(MEDIA_JWT_KEY);
    localStorage.removeItem(TENANT_KEY);
  } catch {
    /* ignore */
  }
}

/**
 * Remove legacy primary JWT storage; keep tenant even if media JWT is absent (cookie may still be valid).
 */
export function normalizeDirectorAuthStorage() {
  try {
    localStorage.removeItem(LEGACY_TOKEN_KEY);
  } catch {
    /* ignore */
  }
}

/** True when local hints suggest a SaaS browser session may exist (tenant and/or media JWT). */
export function hasSaasPersistedClientState() {
  return Boolean(getDirectorAuthToken().trim() || getDirectorTenantId().trim());
}

/**
 * After GET /v1/auth/me succeeds, persist workspace id when empty or stale.
 */
export function syncDirectorTenantFromMePayload(meData) {
  if (!meData || typeof meData !== "object") return false;
  const tenants = Array.isArray(meData.tenants) ? meData.tenants : [];
  const tenantSet = new Set(tenants.map((t) => String(t?.id || "").trim()).filter(Boolean));
  const active = String(meData.active_tenant_id || meData.tenant_id || "").trim();
  const fallback = tenants.length ? String(tenants[0].id || "").trim() : "";
  const pick = active || fallback;
  if (!pick) return false;
  const cur = getDirectorTenantId().trim();
  const media = getDirectorAuthToken().trim();
  if (!cur || !tenantSet.has(cur)) {
    setDirectorAuthSession({ mediaAccessToken: media || undefined, tenantId: pick });
    return true;
  }
  return false;
}

/**
 * Headers for ``fetch``: ``X-Tenant-Id`` when set. API auth uses HttpOnly ``director_session`` cookie
 * (``credentials: "include"``). Optional legacy ``director_auth_token`` Bearer is still honored if present.
 */
export function directorAuthHeaders() {
  const tenant = getDirectorTenantId().trim();
  let legacyBearer = "";
  try {
    legacyBearer = (localStorage.getItem(LEGACY_TOKEN_KEY) || "").trim();
  } catch {
    /* ignore */
  }
  const out = {};
  if (legacyBearer) out.Authorization = `Bearer ${legacyBearer}`;
  if (tenant) out["X-Tenant-Id"] = tenant;
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
