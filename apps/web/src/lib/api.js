/**
 * API base URL resolution and fetch utilities.
 *
 * - Production build: empty apiBase → same-origin `/v1` requests (put nginx in front, or set
 *   VITE_API_BASE_URL at build time).
 * - `npm run dev`: same-origin `/v1` (Vite proxy → FastAPI) when VITE_API_BASE_URL is unset.
 *   Set VITE_API_BASE_URL only if the UI must call the API on another origin.
 *
 * Media tags (<img>/<video>/<audio>) must use apiPath() too because they bypass the Vite
 * proxy in some configurations. When SaaS auth is on, media URLs must include access_token +
 * tenant_id query params (same as compiled video) — tags cannot send Bearer headers.
 */

import { directorAuthHeaders, getDirectorAuthToken, getDirectorTenantId } from "./directorAuthSession.js";

const _rawBase = String(import.meta.env.VITE_API_BASE_URL || "").trim().replace(/\/$/, "");
/** Raw `VITE_API_BASE_URL` (empty string if unset) — for UI hints vs resolved `apiBase`. */
export const viteApiBaseEnvRaw = _rawBase;
export const apiBase = _rawBase || "";

/** Resolve a v1 path to a fully-qualified URL (or same-origin relative path in prod). */
export const apiPath = (path) => `${apiBase}${path}`;

// ---------------------------------------------------------------------------
// Asset content URLs (always use these — never use storage_url / preview_url directly)
// ---------------------------------------------------------------------------

/** Strip BOM / zero-width chars so pasted UUIDs still match API path params. */
export function sanitizeStudioUuid(raw) {
  return String(raw ?? "")
    .replace(/[\uFEFF\u200B-\u200D]/g, "")
    .trim();
}

/** For media elements: API uses `settings_dep` which requires auth when enabled. */
function appendMediaAuthQueryParams(params) {
  const token = getDirectorAuthToken().trim();
  const tenant = getDirectorTenantId().trim();
  if (token) params.set("access_token", token);
  if (tenant) params.set("tenant_id", tenant);
}

/** Binary content URL for an asset (image or video). Cache-busted by `cacheBust`. */
export function apiAssetContentUrl(assetId, cacheBust) {
  const v = cacheBust != null && String(cacheBust).trim() !== "" ? String(cacheBust) : String(assetId);
  const params = new URLSearchParams();
  params.set("t", v);
  appendMediaAuthQueryParams(params);
  return apiPath(`/v1/assets/${assetId}/content?${params.toString()}`);
}

/**
 * Timeline export MP4 (final_cut → fine_cut → rough_cut on disk). Query params carry SaaS auth for `<video>` / `<a download>`.
 */
export function apiCompiledVideoUrl(projectId, timelineVersionId, { download = false, cacheBust } = {}) {
  const pid = encodeURIComponent(String(projectId));
  const tid = encodeURIComponent(String(timelineVersionId));
  const params = new URLSearchParams();
  appendMediaAuthQueryParams(params);
  if (download) params.set("download", "1");
  if (cacheBust != null && String(cacheBust).trim() !== "") {
    params.set("t", String(cacheBust));
  }
  const q = params.toString();
  return apiPath(`/v1/projects/${pid}/timeline-versions/${tid}/compiled-video${q ? `?${q}` : ""}`);
}

/** Chapter-level narration WAV URL. */
export function apiChapterNarrationContentUrl(chapterId, cacheBust) {
  const v = cacheBust != null && String(cacheBust).trim() !== "" ? String(cacheBust) : "0";
  const params = new URLSearchParams();
  params.set("t", v);
  appendMediaAuthQueryParams(params);
  return apiPath(`/v1/chapters/${chapterId}/narration/content?${params.toString()}`);
}

/** Chapter narration WebVTT subtitle URL. */
export function apiChapterNarrationSubtitlesUrl(chapterId, cacheBust) {
  const v = cacheBust != null && String(cacheBust).trim() !== "" ? String(cacheBust) : "0";
  const params = new URLSearchParams();
  params.set("t", v);
  appendMediaAuthQueryParams(params);
  return apiPath(`/v1/chapters/${chapterId}/narration/subtitles.vtt?${params.toString()}`);
}

/** Per-scene TTS WAV URL. */
export function apiSceneNarrationContentUrl(sceneId, cacheBust) {
  const v = cacheBust != null && String(cacheBust).trim() !== "" ? String(cacheBust) : "0";
  const params = new URLSearchParams();
  params.set("t", v);
  appendMediaAuthQueryParams(params);
  return apiPath(`/v1/scenes/${encodeURIComponent(sceneId)}/narration/content?${params.toString()}`);
}

/** Per-scene narration WebVTT URL. */
export function apiSceneNarrationSubtitlesUrl(sceneId, cacheBust) {
  const v = cacheBust != null && String(cacheBust).trim() !== "" ? String(cacheBust) : "0";
  const params = new URLSearchParams();
  params.set("t", v);
  appendMediaAuthQueryParams(params);
  return apiPath(`/v1/scenes/${encodeURIComponent(sceneId)}/narration/subtitles.vtt?${params.toString()}`);
}

/** Chatterbox voice reference WAV URL (Settings → Voice reference). */
export function apiChatterboxVoiceRefContentUrl(cacheBust) {
  const v = cacheBust != null && String(cacheBust).trim() !== "" ? String(cacheBust) : "0";
  const params = new URLSearchParams();
  params.set("t", v);
  appendMediaAuthQueryParams(params);
  return apiPath(`/v1/settings/chatterbox-voice-ref/content?${params.toString()}`);
}

// ---------------------------------------------------------------------------
// Core fetch helper
// ---------------------------------------------------------------------------

/**
 * Thin fetch wrapper that:
 * - Defaults GET/HEAD to no Content-Type header.
 * - Passes through any extra options (method, headers, body, signal, …).
 * - Adds Bearer and/or X-Tenant-Id when stored (see directorAuthSession.js); both are required for API routes that use `auth_context_dep`.
 */
function shouldIgnoreUnauthorizedForPath(path) {
  const p = String(path || "");
  return (
    p.includes("/v1/auth/login") ||
    p.includes("/v1/auth/register") ||
    p.includes("/v1/auth/config") ||
    p.includes("/v1/auth/me") ||
    p.includes("/v1/auth/refresh")
  );
}

function _authorizationWasSent(mergedHeaders) {
  const a = mergedHeaders?.Authorization ?? mergedHeaders?.authorization;
  return typeof a === "string" && /^Bearer\s+\S/.test(a.trim());
}

// #region agent log
function _agentDbgApi(payload) {
  if (typeof window === "undefined") return;
  fetch("http://localhost:7813/ingest/697b30bc-3590-4d28-870a-7f8c016e2c27", {
    method: "POST",
    headers: { "Content-Type": "application/json", "X-Debug-Session-Id": "6de9e4" },
    body: JSON.stringify({ sessionId: "6de9e4", timestamp: Date.now(), ...payload }),
  }).catch(() => {});
}
// #endregion

/** Best-effort JSON detail.code from a 401 body (FastAPI HTTPException shape). */
async function _detailCodeFrom401Response(response) {
  try {
    const body = await response.clone().json();
    const d = body?.detail;
    if (d && typeof d === "object" && typeof d.code === "string") return d.code;
  } catch {
    /* non-JSON or empty */
  }
  return null;
}

/**
 * Same 401 → session-expired policy as `api()` (Bearer sent, not AUTH_REQUIRED, etc.).
 * @param {string} path — same as `api()` (e.g. `/v1/...`)
 * @param {Record<string, string>} mergedHeaders — headers actually sent on the request
 */
async function _applySessionPolicyOn401(path, mergedHeaders, response) {
  if (
    response.status !== 401 ||
    typeof window === "undefined" ||
    shouldIgnoreUnauthorizedForPath(path)
  ) {
    return response;
  }
  if (!_authorizationWasSent(mergedHeaders)) {
    return response;
  }
  const code = await _detailCodeFrom401Response(response);
  if (code === "AUTH_REQUIRED") {
    return response;
  }
  // Only treat explicit JWT/session failures as "logged out". Proxies and edge cases
  // sometimes return 401 without our JSON shape; those must not clear SaaS session.
  if (code !== "UNAUTHORIZED") {
    return response;
  }
  // #region agent log
  _agentDbgApi({
    hypothesisId: "C",
    location: "api.js:_applySessionPolicyOn401",
    message: "dispatch director:session-expired",
    data: { path: String(path).slice(0, 160), code },
  });
  // #endregion
  window.dispatchEvent(new CustomEvent("director:session-expired"));
  return response;
}

export const api = (path, opts = {}) => {
  const method = String(opts.method || "GET").toUpperCase();
  const baseHeaders =
    method === "GET" || method === "HEAD" ? {} : { "Content-Type": "application/json" };
  const auth = directorAuthHeaders();
  const headers = {
    ...baseHeaders,
    ...auth,
    ...(opts.headers || {}),
  };
  return fetch(apiPath(path), {
    ...opts,
    headers,
  }).then((response) => {
    // #region agent log
    const p = String(path || "");
    const logIt =
      response.status === 401 ||
      response.status === 403 ||
      p.startsWith("/v1/projects") ||
      p.startsWith("/v1/agent-runs") ||
      p.startsWith("/v1/auth/me");
    if (logIt) {
      const xt = headers["X-Tenant-Id"] || headers["x-tenant-id"] || "";
      _agentDbgApi({
        hypothesisId: response.status === 401 ? "A" : "E",
        location: "api.js:api",
        message: "fetch response",
        data: {
          method: String(opts.method || "GET"),
          path: p.slice(0, 180),
          status: response.status,
          hasAuthz: _authorizationWasSent(headers),
          hasXTenant: typeof xt === "string" && xt.length > 0,
        },
      });
    }
    // #endregion
    return _applySessionPolicyOn401(path, headers, response);
  });
};

/**
 * Fetch with Bearer + X-Tenant-Id but **without** forcing `Content-Type: application/json`
 * (use for `FormData` uploads and other non-JSON bodies). Same 401 session policy as `api()`.
 */
export function apiForm(path, opts = {}) {
  const auth = directorAuthHeaders();
  const headers = {
    ...auth,
    ...(opts.headers || {}),
  };
  return fetch(apiPath(path), {
    ...opts,
    headers,
  }).then((response) => {
    // #region agent log
    const p = String(path || "");
    if (response.status === 401 || response.status === 403 || p.startsWith("/v1/projects")) {
      const xt = headers["X-Tenant-Id"] || headers["x-tenant-id"] || "";
      _agentDbgApi({
        hypothesisId: "A",
        location: "api.js:apiForm",
        message: "fetch response",
        data: {
          method: String(opts.method || "GET"),
          path: p.slice(0, 180),
          status: response.status,
          hasAuthz: _authorizationWasSent(headers),
          hasXTenant: typeof xt === "string" && xt.length > 0,
        },
      });
    }
    // #endregion
    return _applySessionPolicyOn401(path, headers, response);
  });
}

/** Strip origin from an absolute API URL so it can be passed to `api()` / `apiForm()` (same as `apiPath` input). */
export function apiRelativePathFromFullUrl(fullUrl) {
  const s = String(fullUrl || "");
  if (/^https?:\/\//i.test(s)) {
    try {
      const u = new URL(s);
      return `${u.pathname}${u.search}`;
    } catch {
      return s;
    }
  }
  return s;
}
