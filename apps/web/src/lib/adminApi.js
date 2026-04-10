import { apiPath } from "./api.js";

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

/** Authenticated admin fetch (uses X-Director-Admin-Key). */
export function adminFetch(path, opts = {}) {
  const k = getAdminKey().trim();
  const method = String(opts.method || "GET").toUpperCase();
  const baseHeaders =
    method === "GET" || method === "HEAD" ? {} : { "Content-Type": "application/json" };
  return fetch(apiPath(path), {
    ...opts,
    headers: {
      ...baseHeaders,
      "X-Director-Admin-Key": k,
      ...(opts.headers || {}),
    },
  });
}
