/**
 * API base URL resolution and fetch utilities.
 *
 * - Production build: empty apiBase → same-origin `/v1` requests (put nginx in front, or set
 *   VITE_API_BASE_URL at build time).
 * - `npm run dev`: same-origin `/v1` (Vite proxy → FastAPI) when VITE_API_BASE_URL is unset.
 * - SaaS auth: HttpOnly session cookie with ``credentials: "include"`` — no JWT in headers or media URLs.
 */

import { directorAuthHeaders, hasSaasPersistedClientState } from "./directorAuthSession.js";

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

/** Media URLs rely on same-origin session cookies (no query-token JWTs). */
function appendMediaAuthQueryParams(_params) {
  void _params;
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
 * Timeline export MP4 (final_cut → fine_cut → rough_cut on disk). Same-origin session cookie for `<video>` / `<a download>`.
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
 * - Sends cookies on same-origin (session auth); optional ``Authorization`` only if callers add it in ``opts.headers``.
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
 * Same 401 → session-expired policy as `api()`.
 * @param {string} path — same as `api()` (e.g. `/v1/...`)
 * @param {Record<string, string>} mergedHeaders — headers actually sent on the request
 */
function _mergedAbortSignal(userSignal, timeoutMs) {
  if (timeoutMs == null || timeoutMs <= 0) return userSignal;
  if (typeof AbortSignal === "undefined") return userSignal;
  const t = typeof AbortSignal.timeout === "function" ? AbortSignal.timeout(timeoutMs) : null;
  if (!t) return userSignal;
  if (!userSignal) return t;
  if (typeof AbortSignal.any === "function") return AbortSignal.any([userSignal, t]);
  return t;
}

async function _applySessionPolicyOn401(path, mergedHeaders, response) {
  if (
    response.status !== 401 ||
    typeof window === "undefined" ||
    shouldIgnoreUnauthorizedForPath(path)
  ) {
    return response;
  }
  const sentBearer = _authorizationWasSent(mergedHeaders);
  if (!sentBearer && !hasSaasPersistedClientState()) {
    return response;
  }
  const code = await _detailCodeFrom401Response(response);
  if (code === "AUTH_REQUIRED") {
    return response;
  }
  // Only treat explicit session failures as "logged out". Proxies and edge cases
  // sometimes return 401 without our JSON shape; those must not clear SaaS session.
  if (code !== "UNAUTHORIZED") {
    return response;
  }
  window.dispatchEvent(new CustomEvent("director:session-expired"));
  return response;
}

export const api = (path, opts = {}) => {
  const { timeoutMs, ...rest } = opts;
  const method = String(rest.method || "GET").toUpperCase();
  const baseHeaders =
    method === "GET" || method === "HEAD" ? {} : { "Content-Type": "application/json" };
  const auth = directorAuthHeaders();
  const headers = {
    ...baseHeaders,
    ...auth,
    ...(rest.headers || {}),
  };
  const signal = _mergedAbortSignal(rest.signal, timeoutMs);
  return fetch(apiPath(path), {
    ...rest,
    credentials: rest.credentials ?? "include",
    headers,
    signal,
  }).then((response) => _applySessionPolicyOn401(path, headers, response));
};

/**
 * Fetch without forcing `Content-Type: application/json` (e.g. `FormData` uploads).
 * Same 401 session policy as `api()`.
 */
export function apiForm(path, opts = {}) {
  const auth = directorAuthHeaders();
  const headers = {
    ...auth,
    ...(opts.headers || {}),
  };
  return fetch(apiPath(path), {
    ...opts,
    credentials: opts.credentials ?? "include",
    headers,
  }).then((response) => _applySessionPolicyOn401(path, headers, response));
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
