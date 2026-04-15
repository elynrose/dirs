/**
 * SaaS session: HttpOnly cookie + Redis hold the API session and active workspace.
 * This module keeps only **in-memory** hints for the SPA (no localStorage):
 * - optional media JWT when the UI is built against a **cross-origin** API (see api.js)
 * - mirrored workspace id for components that run before React profile state hydrates
 * - a flag so fetch 401 handling knows the user had established a SaaS session this tab
 */

let _mediaQueryJwt = "";
let _activeTenantId = "";
let _saasClientActive = false;

/** True after successful SaaS bootstrap/login; cleared on logout (this tab only). */
export function setDirectorSaaSClientActive(active) {
  _saasClientActive = Boolean(active);
}

export function getDirectorSaaSClientActive() {
  return _saasClientActive;
}

/** JWT used only for ``access_token`` / ``tenant_id`` query params when API is cross-origin. */
export function getDirectorAuthToken() {
  return _mediaQueryJwt;
}

/** Mirrored active workspace (server is canonical via session); prefer ``accountProfile.active_tenant_id`` in UI. */
export function getDirectorTenantId() {
  return _activeTenantId;
}

function _migrateLegacyLocalStorageOnce() {
  if (typeof window === "undefined") return;
  try {
    window.localStorage?.removeItem?.("director_auth_token");
    window.localStorage?.removeItem?.("director_media_jwt");
    window.localStorage?.removeItem?.("director_auth_tenant_id");
  } catch {
    /* ignore */
  }
}

/**
 * @param {{ accessToken?: string, mediaAccessToken?: string, tenantId?: string }} opts
 * ``accessToken`` is accepted as an alias for ``mediaAccessToken`` (login JSON field name).
 * Only keys present on ``opts`` are applied.
 */
export function setDirectorAuthSession(opts = {}) {
  const { accessToken, mediaAccessToken, tenantId } = opts;
  const mediaPayload = mediaAccessToken ?? accessToken;
  const touchMedia = "mediaAccessToken" in opts || "accessToken" in opts;
  const touchTenant = "tenantId" in opts;
  if (touchMedia) {
    _mediaQueryJwt = (mediaPayload ?? "").trim();
  }
  if (touchTenant) {
    _activeTenantId = (tenantId ?? "").trim();
  }
}

export function clearDirectorAuthSession() {
  _migrateLegacyLocalStorageOnce();
  _mediaQueryJwt = "";
  _activeTenantId = "";
  _saasClientActive = false;
}

export function normalizeDirectorAuthStorage() {
  _migrateLegacyLocalStorageOnce();
}

/** @deprecated use ``getDirectorSaaSClientActive()`` or ``getDirectorAuthToken()`` */
export function hasSaasPersistedClientState() {
  return Boolean(_saasClientActive || _mediaQueryJwt.trim() || _activeTenantId.trim());
}

/**
 * After GET /v1/auth/me succeeds, mirror workspace id from the server payload when empty or stale.
 */
export function syncDirectorTenantFromMePayload(meData) {
  if (!meData || typeof meData !== "object") return false;
  const tenants = Array.isArray(meData.tenants) ? meData.tenants : [];
  const tenantSet = new Set(tenants.map((t) => String(t?.id || "").trim()).filter(Boolean));
  const active = String(meData.active_tenant_id || meData.tenant_id || "").trim();
  const fallback = tenants.length ? String(tenants[0].id || "").trim() : "";
  const pick = active || fallback;
  if (!pick) return false;
  const cur = _activeTenantId.trim();
  if (!cur || !tenantSet.has(cur)) {
    setDirectorAuthSession({ tenantId: pick });
    return true;
  }
  return false;
}

/**
 * Headers for ``fetch``. Workspace + browser session auth live on the server (HttpOnly cookie).
 */
export function directorAuthHeaders() {
  return {};
}

/** Query suffix for EventSource when the API is on another origin (cookie not sent). */
export function directorAuthQuerySuffix() {
  if (!String(import.meta.env.VITE_API_BASE_URL || "").trim()) return "";
  const token = _mediaQueryJwt.trim();
  const tenant = _activeTenantId.trim();
  if (!token) return "";
  const p = new URLSearchParams();
  p.set("access_token", token);
  if (tenant) p.set("tenant_id", tenant);
  return `&${p.toString()}`;
}
