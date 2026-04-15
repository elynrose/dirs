/**
 * SaaS session: HttpOnly cookie + Redis hold the API session and active workspace.
 * The Studio does **not** store or parse JWTs — only a mirrored workspace id for UI/helpers
 * and a tab flag for 401 handling (no localStorage for auth).
 */

let _activeTenantId = "";
let _saasClientActive = false;

/** True after successful SaaS bootstrap/login; cleared on logout (this tab only). */
export function setDirectorSaaSClientActive(active) {
  _saasClientActive = Boolean(active);
}

export function getDirectorSaaSClientActive() {
  return _saasClientActive;
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
 * ``accessToken`` / ``mediaAccessToken`` are ignored (kept for call-site compatibility with login JSON).
 */
export function setDirectorAuthSession(opts = {}) {
  const { tenantId } = opts;
  const touchTenant = "tenantId" in opts;
  if (touchTenant) {
    _activeTenantId = (tenantId ?? "").trim();
  }
}

export function clearDirectorAuthSession() {
  _migrateLegacyLocalStorageOnce();
  _activeTenantId = "";
  _saasClientActive = false;
}

export function normalizeDirectorAuthStorage() {
  _migrateLegacyLocalStorageOnce();
}

export function hasSaasPersistedClientState() {
  return Boolean(_saasClientActive || _activeTenantId.trim());
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

/** Headers for ``fetch``. Session auth uses HttpOnly cookie + ``credentials: "include"``. */
export function directorAuthHeaders() {
  return {};
}

/** Reserved for EventSource; cookie auth requires same-origin UI+API (empty suffix). */
export function directorAuthQuerySuffix() {
  return "";
}
