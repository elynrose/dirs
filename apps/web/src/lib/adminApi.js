import { apiPath } from "./api.js";
import { directorAuthHeaders } from "./directorAuthSession.js";

const STORAGE_KEY = "director_admin_api_key";

export function getAdminKey() {
  try {
    return sessionStorage.getItem(STORAGE_KEY) || "";
  } catch {
    return "";
  }
}

export function setAdminKey(key) {
  try {
    if (key) sessionStorage.setItem(STORAGE_KEY, String(key).trim());
    else sessionStorage.removeItem(STORAGE_KEY);
  } catch {
    /* ignore */
  }
}

/** Admin API: optional ``X-Director-Admin-Key`` plus SaaS ``Authorization`` + ``X-Tenant-Id`` for workspace admins. */
export function adminFetch(path, opts = {}) {
  const k = getAdminKey().trim();
  const method = String(opts.method || "GET").toUpperCase();
  const baseHeaders =
    method === "GET" || method === "HEAD" ? {} : { "Content-Type": "application/json" };
  const auth = directorAuthHeaders();
  return fetch(apiPath(path), {
    ...opts,
    headers: {
      ...baseHeaders,
      ...(k ? { "X-Director-Admin-Key": k } : {}),
      ...auth,
      ...(opts.headers || {}),
    },
  });
}
