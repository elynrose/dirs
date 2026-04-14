/**
 * Shared fetch helpers for the web UI (JSON parse, idempotent POSTs, project readiness).
 */

export async function parseJson(response) {
  const status = response.status;
  const t = await response.text();
  try {
    const data = JSON.parse(t);
    if (
      !response.ok &&
      data !== null &&
      typeof data === "object" &&
      !Array.isArray(data)
    ) {
      return { ...data, _httpStatus: status };
    }
    return data;
  } catch {
    const out = { raw: t };
    if (!response.ok) out._httpStatus = status;
    return out;
  }
}

/**
 * Worker / job error strings often embed Python dict reprs and long bullet lists.
 * Strips inline `{...}` payloads and shortens noisy traces for UI display.
 */
export function humanizeErrorText(raw) {
  let s = String(raw ?? "").replace(/\r\n/g, "\n").trim();
  if (!s) return "";
  const tb = s.search(/\nTraceback \(most recent call last\)/i);
  if (tb >= 0) s = s.slice(0, tb).trim();
  let prev;
  let guard = 0;
  do {
    prev = s;
    s = s.replace(/:\s*\{[^{}]*\}/g, "");
    guard += 1;
  } while (s !== prev && guard < 12);
  s = s.replace(/[ \t]+\n/g, "\n").replace(/\n{3,}/g, "\n\n");
  s = s.replace(/^\s*[•\u2022]\s*([a-z0-9_]+)\s*:\s*$/gim, "• $1");
  s = s.replace(/\n\s*[•\u2022]\s*$/g, "");
  s = s.trim();
  if (s.length > 900) {
    s = `${s.slice(0, 880).trim()}…`;
  }
  return s || "Something went wrong.";
}

/** FastAPI validation: [{ loc, msg, type }, …] */
function _validationDetailSummary(detailArr) {
  if (!Array.isArray(detailArr) || !detailArr.length) return "";
  const parts = detailArr.slice(0, 5).map((e) => {
    if (!e || typeof e !== "object") return null;
    const loc = Array.isArray(e.loc) ? e.loc.filter(Boolean).join(".") : "";
    const msg = typeof e.msg === "string" ? e.msg : "";
    return loc ? `${loc}: ${msg}` : msg;
  });
  const s = parts.filter(Boolean).join("; ");
  const more = detailArr.length > 5 ? ` (+${detailArr.length - 5} more)` : "";
  return s ? `${s}${more}` : "";
}

/** Best-effort message from API error JSON (matches common FastAPI / app shapes). Avoids dumping raw JSON to users. */
export function apiErrorMessage(body) {
  if (body == null) return "Request failed.";
  if (typeof body === "string") return humanizeErrorText(body);
  if (typeof body !== "object") return String(body);
  const httpSt =
    typeof body._httpStatus === "number" && Number.isFinite(body._httpStatus) ? body._httpStatus : null;
  if (httpSt === 502 || httpSt === 503 || httpSt === 504) {
    return (
      `Cannot reach the Directely API (HTTP ${httpSt}). The Studio page loaded, but nginx has no working backend — ` +
      `usually Uvicorn is not running on 127.0.0.1:8000. On the server run: curl -sS http://127.0.0.1:8000/v1/health ` +
      `then start the API (see INSTALLATION.md).`
    );
  }
  const d = body.detail;
  const hint =
    d && typeof d === "object" && typeof d.hint === "string" && d.hint.trim()
      ? ` ${d.hint.trim()}`
      : "";
  if (typeof d === "string") {
    if (/^not found$/i.test(d.trim())) {
      return `Not found — restart the Directely API if you recently updated code (stale servers return HTTP 404 for new routes).${hint}`;
    }
    return `${d}${hint}`;
  }
  if (Array.isArray(d)) {
    const v = _validationDetailSummary(d);
    if (v) return `${v}${hint}`;
  }
  if (d && typeof d === "object") {
    if (typeof d.message === "string") return `${d.message}${hint}`;
    if (typeof d.msg === "string") return `${d.msg}${hint}`;
    if (typeof d.code === "string" && typeof d.message === "string") return `${d.code}: ${d.message}${hint}`;
  }
  const msg = body?.detail?.message;
  if (typeof msg === "string") return `${msg}${hint}`;
  if (body?.error?.message) return `${body.error.message}${hint}`;
  if (typeof body.message === "string") return `${body.message}${hint}`;
  if (typeof body.error === "string") return `${body.error}${hint}`;
  return "Request failed — check the network tab or server logs if this keeps happening.";
}

/** For `catch (e)` / `setError` from mixed API bodies, `Error` throws, and worker strings. */
export function formatUserFacingError(thing) {
  if (thing == null) return "Something went wrong.";
  if (thing instanceof Error) return humanizeErrorText(thing.message || String(thing));
  if (typeof thing === "object" && !Array.isArray(thing)) return apiErrorMessage(thing);
  const t = String(thing);
  if (/^Error:\s*/i.test(t)) return humanizeErrorText(t.replace(/^Error:\s*/i, ""));
  return humanizeErrorText(t);
}

export async function apiPostIdempotent(apiFetch, path, body, newIdempotencyKey) {
  const r = await apiFetch(path, {
    method: "POST",
    headers: { "Idempotency-Key": newIdempotencyKey() },
    body: JSON.stringify(body ?? {}),
  });
  const b = await parseJson(r);
  if (!r.ok) throw new Error(apiErrorMessage(b));
  return b;
}

/**
 * @param {object} [opts]
 * @param {string} [opts.timelineVersionId]
 * @param {"rough_cut"|"fine_cut"|"final_cut"} [opts.exportStage]
 * @param {boolean} [opts.allowUnapprovedMedia] Hands-off / unattended: relax approval gates in preflight.
 */
export async function fetchProjectPhase5Readiness(apiFetch, projectId, opts = {}) {
  const params = new URLSearchParams();
  if (opts.timelineVersionId) params.set("timeline_version_id", String(opts.timelineVersionId));
  if (opts.exportStage) params.set("export_stage", String(opts.exportStage));
  if (opts.allowUnapprovedMedia) params.set("allow_unapproved_media", "true");
  const q = params.toString();
  const path = `/v1/projects/${projectId}/phase5-readiness${q ? `?${q}` : ""}`;
  const r = await apiFetch(path);
  const b = await parseJson(r);
  return {
    ok: r.ok,
    body: b,
    data: r.ok ? (b?.data ?? null) : null,
  };
}

/**
 * Poll GET /v1/jobs/{id} until terminal status or timeout (for chained compile steps).
 *
 * @param {typeof fetch} apiFetch  Same `api` wrapper the app uses (base URL + auth).
 * @param {string} jobId
 * @param {{ intervalMs?: number, timeoutMs?: number }} [opts]
 * @returns {Promise<{ job: object, ok: boolean }>}  ok true iff status === "succeeded"
 */
export async function pollJobUntilTerminal(apiFetch, jobId, opts = {}) {
  const intervalMs = Math.max(400, Number(opts.intervalMs) || 1500);
  const timeoutMs = Math.max(5000, Number(opts.timeoutMs) || 45 * 60 * 1000);
  const start = Date.now();
  const path = `/v1/jobs/${encodeURIComponent(jobId)}`;
  while (Date.now() - start < timeoutMs) {
    const r = await apiFetch(path);
    const b = await parseJson(r);
    const job = b?.data ?? b;
    const st = job?.status;
    if (st === "succeeded" || st === "failed" || st === "cancelled") {
      return { job, ok: st === "succeeded" };
    }
    await new Promise((res) => setTimeout(res, intervalMs));
  }
  throw new Error("Timed out waiting for the job to finish.");
}
