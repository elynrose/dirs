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
 * - Adds Bearer + X-Tenant-Id when a SaaS session is stored (see directorAuthSession.js).
 */
function shouldIgnoreUnauthorizedForPath(path) {
  const p = String(path || "");
  return (
    p.includes("/v1/auth/login") ||
    p.includes("/v1/auth/register") ||
    p.includes("/v1/auth/config") ||
    p.includes("/v1/auth/me")
  );
}

/**
 * Only JWT / credential failures should clear the SaaS session. Other 401s (e.g. AUTH_REQUIRED
 * for a feature when the account state is odd) must not log the user out.
 */
function shouldInvalidateSessionOn401Body(body) {
  if (!body || typeof body !== "object") return false;
  const d = body.detail;
  if (d && typeof d === "object" && !Array.isArray(d) && d.code) {
    return String(d.code) === "UNAUTHORIZED";
  }
  if (typeof d === "string") {
    return /invalid or expired token|missing credentials|invalid token subject|user not found/i.test(d);
  }
  return false;
}

export const api = (path, opts = {}) => {
  const method = String(opts.method || "GET").toUpperCase();
  const baseHeaders =
    method === "GET" || method === "HEAD" ? {} : { "Content-Type": "application/json" };
  const auth = directorAuthHeaders();
  return fetch(apiPath(path), {
    ...opts,
    headers: {
      ...baseHeaders,
      ...auth,
      ...(opts.headers || {}),
    },
  }).then(async (response) => {
    if (
      response.status === 401 &&
      typeof window !== "undefined" &&
      !shouldIgnoreUnauthorizedForPath(path)
    ) {
      const ct = (response.headers.get("content-type") || "").toLowerCase();
      if (ct.includes("application/json")) {
        try {
          const body = await response.clone().json();
          if (shouldInvalidateSessionOn401Body(body)) {
            window.dispatchEvent(new CustomEvent("director:session-expired"));
          }
        } catch {
          /* ignore parse errors — do not force logout on ambiguous 401 */
        }
      }
    }
    return response;
  });
};
