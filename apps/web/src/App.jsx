import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { EditorLayoutProvider, EDITOR_CENTER_SCENE_TAB_IDS } from "./editor/EditorLayoutContext.jsx";
import { EditorCardColumn } from "./editor/EditorCard.jsx";
import { InfoTip } from "./components/InfoTip.jsx";
import { InspectorPipelinePanel } from "./editor/InspectorPipelinePanel.jsx";
import { CompiledVideoPreview } from "./editor/CompiledVideoPreview.jsx";
import {
  parseJson,
  apiPostIdempotent,
  fetchProjectPhase5Readiness,
  pollJobUntilTerminal,
  apiErrorMessage,
  formatUserFacingError,
  humanizeErrorText,
  summarizeAgentRunFailure,
} from "./lib/apiHelpers.js";

// Extracted lib / hooks / components
import {
  api,
  apiForm,
  apiBase,
  viteApiBaseEnvRaw,
  apiPath,
  sanitizeStudioUuid,
  apiAssetContentUrl,
  apiChapterNarrationContentUrl,
  apiChapterNarrationSubtitlesUrl,
  apiSceneNarrationContentUrl,
  apiSceneNarrationSubtitlesUrl,
  apiChatterboxVoiceRefContentUrl,
} from "./lib/api.js";
import {
  DIRECTOR_UI_SESSION_KEY,
  FAL_CATALOG_MIN_REFRESH_MS,
  STUDIO_MEDIA_JOB_TYPES,
  EXPORT_COMPILE_JOB_TYPES,
  DEFAULT_NARRATION_PRESET_ID,
  OPENAI_TTS_VOICE_OPTIONS,
  GEMINI_TTS_VOICE_FALLBACK,
  KOKORO_VOICE_OPTIONS,
  KOKORO_LANG_OPTIONS,
  VISUAL_STYLE_PRESET_FALLBACK,
  RUN_STEP_LABEL,
  AGENT_PROGRESS_ORDER,
  RESTART_AUTOMATION_STEPS,
  AGENT_STEP_TO_PIPELINE_STEP_ID,
  PIPELINE_STEP_TO_RERUN_FROM,
  PIPELINE_RERUN_NEEDS_FULL_VIDEO,
  PIPELINE_STEP_ID_TO_AGENT_EFF_KEY,
  PHASE5_TIMELINE_UUID_RE,
  EVENT_META_LABELS,
  agentRunAutoGenerateSceneVideos,
  agentRunAutoGenerateSceneImages,
  agentRunMinSceneImages,
  agentRunMinSceneVideos,
  sceneAutomationMediaPipelineOptions,
  briefPreferredMediaProvidersFromAppConfig,
} from "./lib/constants.js";
import { usePollJob } from "./hooks/usePollJob.js";
import { useToast } from "./hooks/useToast.js";
import { useKeyboardShortcuts } from "./hooks/useKeyboardShortcuts.js";
import { useProjectEvents } from "./hooks/useProjectEvents.js";
import { ToastContainer } from "./components/Toast.jsx";
import { ShortcutHelp } from "./components/ShortcutHelp.jsx";
import {
  SkeletonSceneList,
  SkeletonAssetGrid,
  SkeletonMediaCanvas,
} from "./components/LoadingSkeleton.jsx";
import { StudioAuthPanel } from "./components/StudioAuthPanel.jsx";
import { StudioUpgradeModal } from "./components/StudioUpgradeModal.jsx";
import { StudioPanelErrorBoundary } from "./components/StudioPanelErrorBoundary.jsx";
import { ChatStudioPage } from "./components/ChatStudioPage.jsx";
import { StudioAccountPage } from "./components/StudioAccountPage.jsx";
import { StudioAdminPage } from "./components/StudioAdminPage.jsx";
import { StudioLegalPage } from "./components/StudioLegalPage.jsx";
import {
  clearDirectorAuthSession,
  getDirectorTenantId,
  normalizeDirectorAuthStorage,
  setDirectorAuthSession,
  setDirectorSaaSClientActive,
  syncDirectorTenantFromMePayload,
} from "./lib/directorAuthSession.js";

/** Primary areas — vertical rail with sideways labels (Blender-style). Order matches former top tabs. */
const STUDIO_PAGE_RAILS = [
  { id: "editor", label: "Editor" },
  { id: "chat", label: "Chat" },
  { id: "research_chapters", label: "Research & scripts" },
  { id: "characters", label: "Characters" },
  { id: "usage", label: "Usage" },
  { id: "prompts", label: "Prompts" },
  { id: "settings", label: "Settings" },
  { id: "account", label: "Account" },
  { id: "admin", label: "Admin" },
];

const STUDIO_PAGE_IDS = new Set(STUDIO_PAGE_RAILS.map((r) => r.id));

/** In-app legal views (not shown in the primary rail). */
const LEGAL_PAGE_IDS = new Set(["terms", "privacy", "copyright"]);

function normalizeDirectorActivePage(v) {
  const id = typeof v === "string" ? v.trim() : "";
  if (LEGAL_PAGE_IDS.has(id)) return id;
  return STUDIO_PAGE_IDS.has(id) ? id : "editor";
}

/**
 * Fal catalog video row: { endpoint_id, display_name, category? }.
 * Platform category: "text-to-video" | "image-to-video".
 */
function falVideoEndpointKind(m) {
  if (!m || typeof m !== "object") return "t2v";
  const c = String(m.category || "").toLowerCase();
  if (c === "image-to-video") return "i2v";
  if (c === "text-to-video") return "t2v";
  const id = String(m.endpoint_id || "");
  if (/image-to-video/i.test(id) || /\/i2v\//i.test(id) || /reference-to-video/i.test(id)) return "i2v";
  return "t2v";
}

function partitionFalVideoModels(models) {
  const t2v = [];
  const i2v = [];
  for (const m of models || []) {
    (falVideoEndpointKind(m) === "i2v" ? i2v : t2v).push(m);
  }
  return { t2v, i2v };
}

/** Compare GET /v1/projects payloads so silent polls skip setState when nothing changed (avoids full-app re-renders). */
function projectsPollSnapshotFromRows(rows) {
  if (!Array.isArray(rows)) return "[]";
  return JSON.stringify(
    [...rows]
      .map((p) => ({
        id: String(p?.id ?? ""),
        title: String(p?.title ?? ""),
        status: String(p?.status ?? ""),
        workflow_phase: String(p?.workflow_phase ?? ""),
      }))
      .sort((a, b) => a.id.localeCompare(b.id)),
  );
}

/** When the field is typed manually and not in catalog, infer from endpoint id. */
function falVideoKindFromEndpointId(endpointId) {
  const s = String(endpointId || "").trim();
  if (!s) return null;
  if (/image-to-video/i.test(s) || /\/i2v\//i.test(s) || /reference-to-video/i.test(s)) return "i2v";
  return "t2v";
}

/**
 * Map saved `active_speech_provider` (API `speech_route`) to the Settings → Speech provider `<select>` value.
 * Prefix forms like `openai:nova`, `kokoro:af_heart`, `chatterbox_mtl:es` collapse to their base engine.
 */
function speechProviderSettingSelectValue(raw) {
  const s = String(raw ?? "openai").trim().toLowerCase();
  if (!s) return "openai";
  if (OPENAI_TTS_VOICE_OPTIONS.includes(s)) return "openai";
  if (s.startsWith("openai:") || s === "openai" || s === "openai_tts") return "openai";
  if (s.startsWith("elevenlabs:") || s === "elevenlabs" || s === "11labs" || s === "eleven") return "elevenlabs";
  if (s.startsWith("gemini:") || s === "gemini" || s === "google" || s === "google_tts") return "gemini";
  if (s.startsWith("kokoro:") || s === "kokoro" || s === "local_kokoro") return "kokoro";
  if (
    s.startsWith("chatterbox_turbo:") ||
    s === "chatterbox" ||
    s === "chatterbox_turbo" ||
    s === "resemble_turbo"
  ) {
    return "chatterbox_turbo";
  }
  if (
    s.startsWith("chatterbox_mtl:") ||
    s === "chatterbox_mtl" ||
    s === "chatterbox_multilingual" ||
    s === "resemble_mtl"
  ) {
    return "chatterbox_mtl";
  }
  return "openai";
}

/** Non-nested `[inner]` segments in narration (aligned with API `narration_bracket_visual`). */
function extractBracketPhrasesFromNarration(narrationText) {
  const out = [];
  if (!narrationText || typeof narrationText !== "string") return out;
  const re = /\[([^\[\]]+)\]/g;
  let m;
  while ((m = re.exec(narrationText)) !== null) {
    const inner = String(m[1] || "").trim();
    if (inner) out.push(inner);
  }
  return out;
}

/** Matches worker `_scene_still_prompt_for_comfy` / `base_image_prompt_from_scene_fields` (bracket hints override package image_prompt). */
function baseImagePromptFromScene(scene) {
  if (!scene || typeof scene !== "object") return "";
  const narr = String(scene.narration_text || "");
  const phrases = extractBracketPhrasesFromNarration(narr);
  if (phrases.length) {
    const joined = phrases.slice(0, 16).join("; ");
    return (
      `A single photoreal documentary still — abstract tableau: ${joined}. ` +
      "One cohesive composition; clear focal subject and setting implied by the hints."
    ).slice(0, 4000);
  }
  const pp = scene.prompt_package_json;
  const pkg = pp && typeof pp === "object" ? pp : {};
  const im = pkg.image_prompt;
  if (typeof im === "string" && im.trim()) return im.trim();
  return narr.slice(0, 1200);
}

/** Matches worker `video_text_prompt_from_scene_fields` (bracket hints before raw VO when no `video_prompt`). */
function baseVideoPromptFromScene(scene) {
  if (!scene || typeof scene !== "object") return "";
  const pp = scene.prompt_package_json;
  const pkg = pp && typeof pp === "object" ? pp : {};
  const vp = pkg.video_prompt;
  if (typeof vp === "string" && vp.trim()) return vp.trim();
  const narr = String(scene.narration_text || "").trim();
  const phrases = extractBracketPhrasesFromNarration(narr);
  if (phrases.length) {
    const joined = phrases.slice(0, 16).join("; ");
    return (
      `Cinematic documentary shot: ${joined}. ` + "Subtle natural motion or slow camera move; one coherent beat."
    ).slice(0, 3000);
  }
  if (narr) return narr.slice(0, 3000);
  const p = String(scene.purpose || scene.visual_type || "").trim();
  return p ? p.slice(0, 3000) : "";
}

function narrationWordCount(text) {
  if (!text || typeof text !== "string") return 0;
  const t = text.trim();
  if (!t) return 0;
  return t.split(/\s+/).filter(Boolean).length;
}

/** Timeline cover from succeeded assets: explicit planned_duration_sec or one clip per image/video. */
function estAssetCoverSec(asset, clipSec) {
  const pj = asset?.params_json;
  if (pj && typeof pj === "object") {
    const d = Number(pj.planned_duration_sec);
    if (Number.isFinite(d) && d > 0) return d;
  }
  const t = String(asset?.asset_type || "").toLowerCase();
  if (t === "video" || t === "image") return clipSec;
  return 0;
}

/**
 * Per-scene VO budget: split chapter MP3 duration by narration word share; else use planned_duration_sec.
 * @returns {Map<string, { narrationSec: number; clipHint: number; source: "narration_audio" | "planned" }>}
 */
function buildSceneNarrationGuide(sortedScenes, chapterDurationSec, clipSec) {
  const clip = clipSec === 5 ? 5 : 10;
  const rows = [...(sortedScenes || [])].sort((a, b) => (Number(a.order_index) || 0) - (Number(b.order_index) || 0));
  const map = new Map();
  const dur = Number(chapterDurationSec);
  const hasAudio = Number.isFinite(dur) && dur > 0;
  const weights = rows.map((s) => ({
    id: String(s.id),
    w: Math.max(1, narrationWordCount(s.narration_text || "")),
  }));
  const totalW = weights.reduce((a, x) => a + x.w, 0);

  if (!rows.length) return map;

  if (!hasAudio || totalW <= 0) {
    rows.forEach((s) => {
      const planned = Math.max(1, Number(s.planned_duration_sec) || clip);
      map.set(String(s.id), {
        narrationSec: planned,
        clipHint: Math.max(1, Math.ceil(planned / clip)),
        source: "planned",
      });
    });
    return map;
  }

  let allocated = 0;
  weights.forEach((x, i) => {
    const isLast = i === weights.length - 1;
    const sec = isLast
      ? Math.max(1, Math.round(dur - allocated))
      : Math.max(1, Math.round((dur * x.w) / totalW));
    allocated += sec;
    map.set(x.id, {
      narrationSec: sec,
      clipHint: Math.max(1, Math.ceil(sec / clip)),
      source: "narration_audio",
    });
  });
  return map;
}

/** Chapters sorted by `order_index`. LLMs sometimes emit 0-based or 1-based indices — do not use `order_index + 1` for display. */
function chaptersSorted(list) {
  return [...(list || [])].sort((a, b) => (Number(a?.order_index) || 0) - (Number(b?.order_index) || 0));
}

/** 1-based chapter number for UI labels (first chapter in sort order is always 1). */
function chapterHumanNumber(list, chapterIdOrRow) {
  const id = typeof chapterIdOrRow === "string" ? chapterIdOrRow : chapterIdOrRow?.id;
  if (!id) return null;
  const i = chaptersSorted(list).findIndex((c) => String(c.id) === String(id));
  return i >= 0 ? i + 1 : null;
}

/** First succeeded image/video in timeline order — for scene list thumbnails. */
function bestSceneListThumbAsset(rows) {
  if (!Array.isArray(rows) || !rows.length) return null;
  const ordered = rows.filter((a) => a.status !== "rejected");
  ordered.sort((a, b) => {
    const as = a.status === "succeeded" ? 1 : 0;
    const bs = b.status === "succeeded" ? 1 : 0;
    if (bs !== as) return bs - as;
    const seq = Number(a.timeline_sequence ?? 0) - Number(b.timeline_sequence ?? 0);
    if (seq !== 0) return seq;
    return new Date(a.created_at || 0).getTime() - new Date(b.created_at || 0).getTime();
  });
  return (
    ordered.find((r) => {
      if (r.status !== "succeeded") return false;
      const t = String(r.asset_type || "").toLowerCase();
      return t === "image" || t === "video";
    }) || null
  );
}

/** When no thumbnail: choose video vs image placeholder from assets or scene heuristics. */
function sceneListFallbackThumbKind(scene, rows) {
  const hint = (scene?.visual_type || "").toLowerCase();
  if (/\bvideo\b|motion|footage|clip|b_roll|b roll/.test(hint)) return "video";
  if (/\bimage\b|photo|still/.test(hint)) return "image";
  if (rows?.some((r) => String(r.asset_type || "").toLowerCase() === "video")) return "video";
  return "image";
}

function friendlyPipelineStep(step) {
  if (!step) return "—";
  return RUN_STEP_LABEL[step] || String(step).replace(/_/g, " ");
}

function friendlyRunStatus(status) {
  if (!status) return "Idle";
  const m = {
    running: "Running",
    succeeded: "Finished",
    failed: "Failed",
    cancelled: "Stopped",
    queued: "Waiting",
    paused: "Paused",
    blocked: "Needs attention",
    idle: "Idle",
    skipped: "Skipped",
  };
  return m[status] || String(status).replace(/_/g, " ");
}

/** True after user clicks Stop until the worker sets status to cancelled (run may still be ``running``). */
function pipelineStopRequested(ctrl) {
  return Boolean(ctrl && typeof ctrl === "object" && ctrl.stop_requested);
}

function friendlyAgentRunStatus(run) {
  if (!run) return friendlyRunStatus(null);
  if (run.status === "running" && pipelineStopRequested(run.pipeline_control_json)) return "Stopping";
  return friendlyRunStatus(run.status);
}

/** While true, disable Re-run row / Restart… so users do not stack agent runs on an active one. */
function agentRunLocksPipelineControls(run) {
  if (!run) return false;
  const st = run.status;
  if (st === "running" || st === "queued" || st === "paused") return true;
  return false;
}

function friendlyPipelineStepStatus(status) {
  const m = {
    done: "Done",
    pending: "Waiting",
    running: "In progress",
    blocked: "Needs attention",
  };
  return m[status] || String(status || "").replace(/_/g, " ");
}

function friendlyBlockReason(code) {
  if (code === "CRITIC_GATE") return "Chapter review gate";
  return String(code || "unknown").replace(/_/g, " ");
}

/**
 * Worker preflight uses the same timeline id string. Using rough_cut avoids final-cut-only issues
 * (e.g. missing prior MP4) while debugging a first render.
 */
function buildPhase5ReadinessFetchOpts(pipelineMode, timelineVersionIdRaw, exportStage = "rough_cut") {
  const allowUnapprovedMedia = pipelineMode === "unattended";
  const raw = String(timelineVersionIdRaw || "").trim();
  const tv = PHASE5_TIMELINE_UUID_RE.test(raw) ? raw : null;
  return {
    allowUnapprovedMedia,
    ...(tv ? { timelineVersionId: tv, exportStage } : {}),
  };
}

function friendlyIssueCodesList(codes) {
  if (!Array.isArray(codes) || !codes.length) return "—";
  return codes.map((c) => String(c).replace(/_/g, " ")).join(", ");
}

/** Surfaces API `export_attention_timeline_assets` in the export gate modal and timeline panel. */
function ExportAttentionTimelineAssetsBlock({ rows, busy, onOpenScene, onReconcile, reconcileDisabled }) {
  if (!Array.isArray(rows) || rows.length === 0) return null;
  return (
    <div className="export-attention-timeline-block" style={{ marginTop: 12 }}>
      <p style={{ margin: "0 0 8px", fontSize: "0.85rem", fontWeight: 600 }}>
        Timeline media to fix
      </p>
      <p className="subtle" style={{ margin: "0 0 10px", fontSize: "0.75rem", lineHeight: 1.5 }}>
        Export checks each clip: <strong>image or video</strong>, <strong>approved</strong> (unless hands-off), <strong>file on disk</strong>, and not
        <strong> rejected/failed</strong> in the DB — <strong>succeeded</strong> status is not required if the file is already there.{" "}
        <strong>Reconcile timeline clips</strong> re-points bad clips (same scene, then other scenes / project fallbacks). Each row shows{" "}
        <strong>type</strong> and <strong>status</strong> from the server when available.
      </p>
      {typeof onReconcile === "function" ? (
        <div className="action-row" style={{ marginBottom: 12, flexWrap: "wrap", gap: 8 }}>
          <button
            type="button"
            className="secondary"
            disabled={Boolean(reconcileDisabled) || busy}
            onClick={() => void onReconcile()}
            title="Relink timeline clips to viable scene media, sync storyboard order, and related fixes"
          >
            Reconcile timeline clips
          </button>
        </div>
      ) : null}
      <ul style={{ margin: 0, paddingLeft: 18, fontSize: "0.8rem", lineHeight: 1.45 }}>
        {rows.map((row) => {
          const aid = String(row?.asset_id || "");
          const short = aid.length > 14 ? `${aid.slice(0, 8)}…${aid.slice(-4)}` : aid || "—";
          const sid = row?.scene_id ? String(row.scene_id) : "";
          return (
            <li key={aid || short} style={{ marginBottom: 10 }}>
              <span className="mono">{short}</span>
              {" — "}
              <span className="subtle">{friendlyIssueCodesList(row?.issue_codes)}</span>
              {row?.asset_type || row?.status ? (
                <div className="subtle" style={{ marginTop: 4, fontSize: "0.72rem" }}>
                  DB: {row?.asset_type ? String(row.asset_type) : "—"} · status {row?.status ? String(row.status) : "—"}
                </div>
              ) : null}
              <div className="action-row" style={{ marginTop: 6, flexWrap: "wrap", gap: 6 }}>
                <button
                  type="button"
                  className="secondary"
                  style={{ padding: "2px 8px", fontSize: "0.72rem" }}
                  disabled={!aid}
                  onClick={() => {
                    if (aid) void navigator.clipboard?.writeText(aid);
                  }}
                >
                  Copy asset id
                </button>
                {sid ? (
                  <button
                    type="button"
                    className="secondary"
                    style={{ padding: "2px 8px", fontSize: "0.72rem" }}
                    disabled={busy}
                    onClick={() => void onOpenScene?.(aid)}
                  >
                    Open scene
                  </button>
                ) : (
                  <span className="subtle">
                    Export still flagged this clip — Reconcile timeline clips, or replace the asset reference.
                  </span>
                )}
              </div>
            </li>
          );
        })}
      </ul>
    </div>
  );
}

/** User-facing text for export-readiness rows (hides raw API codes). */
function friendlyReadinessIssue(iss) {
  if (!iss || typeof iss !== "object") return "Something still needs attention before export.";
  if (iss.code === "CHAPTER_GATE") {
    return "This chapter’s review gate isn’t cleared yet. Run a chapter review, or ask an admin to waive the gate if your workflow allows.";
  }
  if (iss.code === "SCENE_CRITIC") {
    return "This scene hasn’t passed its review yet (or needs a waiver).";
  }
  if (iss.code === "missing_scene_narration") {
    return "One or more scenes have script (VO) text but no synthesized scene audio yet — run scene VO or Automate (per-scene narration).";
  }
  if (iss.code === "scene_narration_audio_missing_on_disk") {
    return "A scene narration file is missing on disk — regenerate scene VO for that scene.";
  }
  if (iss.code === "missing_approved_scene_image") {
    return "Every scene needs at least one approved, succeeded image or video (not only audio). Approve the row you use on the timeline — highlighted scenes still need that.";
  }
  if (iss.code === "missing_succeeded_scene_image") {
    return "One or more scenes have no succeeded image or video yet — generate or fix media on those scenes before export.";
  }
  if (iss.code === "timeline_asset_not_approved") {
    return "The timeline uses media that isn’t approved yet — approve those assets or pick approved clips.";
  }
  if (iss.code === "timeline_asset_not_in_project") {
    return "The timeline references an unknown asset ID or one from another tenant — fix the clip, or ensure that asset exists in this workspace.";
  }
  if (iss.code === "timeline_clip_not_visual_asset") {
    return "This timeline clip points at a row that isn’t an image or video (wrong asset type). Reconcile tries to swap in scene media; otherwise fix the clip or regenerate.";
  }
  if (iss.code === "timeline_asset_rejected_or_failed") {
    return "The timeline still points at this exact asset row (check the asset id). Approving a different video in the gallery does not change the clip — click Approve on this row, or use Reconcile timeline clips to swap in current scene media.";
  }
  if (iss.code === "timeline_asset_not_succeeded") {
    return "Legacy checklist code — refresh readiness; if you still see this, tell the team.";
  }
  if (iss.code === "timeline_asset_file_missing") {
    return "A timeline media file is missing on disk — regenerate that asset or fix storage.";
  }
  const raw = (iss.message || "").trim();
  if (raw.toLowerCase().includes("post ") || raw.includes("POST ")) {
    return "This item still needs a review or approval before you can export.";
  }
  return raw || "Something still needs attention before export.";
}

/** Rough/final/export job errors: if set, show approval gate dialog instead of raw log text. */
function parsePhase5GateModalPayload(errorMessage) {
  const t = String(errorMessage || "");
  if (!/\bPHASE5_NOT_READY\b/.test(t) && !/\bAUTO_ROUGH_NOT_READY\b/.test(t)) {
    return null;
  }
  const codes = new Set();
  const re = /[•\u2022\-]\s*([a-z0-9_]+)\s*:/gi;
  let m;
  while ((m = re.exec(t)) !== null) {
    codes.add(m[1]);
  }
  const knownCodes = [
    "missing_approved_scene_image",
    "missing_succeeded_scene_image",
    "timeline_asset_not_approved",
    "timeline_asset_not_in_project",
    "timeline_clip_not_visual_asset",
    "timeline_asset_rejected_or_failed",
    "timeline_asset_not_succeeded",
    "timeline_asset_file_missing",
    "timeline_empty_clips",
    "invalid_timeline_json",
  ];
  for (const c of knownCodes) {
    if (t.includes(c)) codes.add(c);
  }
  const approvalRelated = ["missing_approved_scene_image", "timeline_asset_not_approved"];
  const offerBulkApprove = approvalRelated.some((c) => codes.has(c));
  const bulletOrder = knownCodes;
  const summaryBullets = [];
  for (const code of bulletOrder) {
    if (codes.has(code)) summaryBullets.push(friendlyReadinessIssue({ code }));
  }
  for (const c of codes) {
    if (bulletOrder.includes(c)) continue;
    if (c === "export_preflight_missing_context") continue;
    summaryBullets.push(friendlyReadinessIssue({ code: c, message: c.replace(/_/g, " ") }));
  }
  return {
    offerBulkApprove,
    summaryBullets: summaryBullets.slice(0, 12),
  };
}

function humanizeMetaKey(key) {
  if (EVENT_META_LABELS[key]) return EVENT_META_LABELS[key];
  return String(key || "").replace(/_/g, " ");
}

function friendlyEventMeta(ev) {
  if (!ev || typeof ev !== "object") return "";
  const skip = new Set(["at", "step", "status"]);
  const entries = Object.entries(ev).filter(([k, v]) => !skip.has(k) && v !== null && v !== undefined && String(v) !== "");
  if (!entries.length) return "";
  return entries
    .slice(0, 8)
    .map(([k, v]) => {
      const label = humanizeMetaKey(k);
      let val = String(v);
      if (v !== null && typeof v === "object") {
        val = Array.isArray(v) ? `${v.length} entries` : "see details";
      }
      return `${label}: ${val}`;
    })
    .join(" · ");
}

function agentThroughFromRun(run, autoThroughFallback) {
  const raw = run?.pipeline_options_json;
  const t = raw && typeof raw === "object" ? raw.through : null;
  if (t === "full_video" || t === "critique" || t === "chapters") return t;
  if (
    autoThroughFallback === "full_video" ||
    autoThroughFallback === "critique" ||
    autoThroughFallback === "chapters"
  ) {
    return autoThroughFallback;
  }
  return "full_video";
}

/** User-facing "Now …" line for the top pipeline alert. */
function agentStageHeadline(stepKey) {
  const m = {
    queued: "Waiting for the automation worker to start…",
    working: "Working on the pipeline…",
    director: "Now preparing the director brief…",
    research: "Now gathering research and sources…",
    outline: "Now outlining chapters…",
    chapters: "Now writing chapter scripts…",
    scenes: "Now planning scenes and visuals…",
    story_research_review: "Now reviewing the story against research…",
    auto_characters: "Now building the character bible…",
    auto_images: "Now generating scene images…",
    auto_videos: "Now generating scene videos…",
    auto_narration: "Now synthesizing narration audio…",
    auto_timeline: "Now building the edit timeline…",
    auto_rough_cut: "Now rendering the rough cut…",
    auto_final_cut: "Now mixing the final cut…",
    pipeline: "Updating the pipeline…",
    rerun: "Re-running from a chosen phase…",
  };
  if (m[stepKey]) return m[stepKey];
  if (!stepKey) return m.working;
  return `Now working on ${friendlyPipelineStep(stepKey).toLowerCase()}…`;
}

function lastAgentEventWithStatus(stepsJson, status) {
  const evs = Array.isArray(stepsJson) ? stepsJson : [];
  for (let i = evs.length - 1; i >= 0; i--) {
    const e = evs[i];
    if (e && e.status === status && e.step) return e;
  }
  return null;
}

/** Latest per-chapter progress while the worker is in the ``scenes`` step (``steps_json`` row). */
function lastScenesProgressEvent(stepsJson) {
  const evs = Array.isArray(stepsJson) ? stepsJson : [];
  for (let i = evs.length - 1; i >= 0; i--) {
    const e = evs[i];
    if (e && e.step === "scenes" && e.status === "progress") return e;
  }
  return null;
}

/** Font Awesome classes per stage (`fa-solid` + animation). Paused uses a static icon. */
function agentPipelineActivityIconClass(effKey, runStatus) {
  if (runStatus === "paused") return "fa-solid fa-circle-pause fa-fw pipeline-fa-icon pipeline-fa-icon--paused";
  if (runStatus === "cancelled")
    return "fa-solid fa-circle-stop fa-fw pipeline-fa-icon pipeline-fa-icon--cancelled";
  const table = {
    queued: "fa-solid fa-hourglass-start fa-beat-fade fa-fw pipeline-fa-icon",
    working: "fa-solid fa-spinner fa-spin fa-fw pipeline-fa-icon",
    director: "fa-solid fa-wand-magic-sparkles fa-beat-fade fa-fw pipeline-fa-icon",
    research: "fa-solid fa-magnifying-glass fa-bounce fa-fw pipeline-fa-icon",
    outline: "fa-solid fa-list-check fa-fade fa-fw pipeline-fa-icon",
    chapters: "fa-solid fa-file-lines fa-beat fa-fw pipeline-fa-icon",
    scenes: "fa-solid fa-photo-film fa-beat-fade fa-fw pipeline-fa-icon",
    story_research_review: "fa-solid fa-scale-balanced fa-shake fa-fw pipeline-fa-icon",
    auto_characters: "fa-solid fa-users fa-beat-fade fa-fw pipeline-fa-icon",
    auto_images: "fa-solid fa-image fa-beat-fade fa-fw pipeline-fa-icon",
    auto_videos: "fa-solid fa-clapperboard fa-beat-fade fa-fw pipeline-fa-icon",
    auto_narration: "fa-solid fa-microphone-lines fa-beat fa-fw pipeline-fa-icon",
    auto_timeline: "fa-solid fa-timeline fa-bounce fa-fw pipeline-fa-icon",
    auto_rough_cut: "fa-solid fa-scissors fa-beat-fade fa-fw pipeline-fa-icon",
    auto_final_cut: "fa-solid fa-circle-play fa-beat-fade fa-fw pipeline-fa-icon",
    pipeline: "fa-solid fa-gears fa-spin fa-fw pipeline-fa-icon",
    rerun: "fa-solid fa-rotate-right fa-spin fa-fw pipeline-fa-icon",
  };
  return table[effKey] || "fa-solid fa-spinner fa-spin fa-fw pipeline-fa-icon";
}

/**
 * Single source of truth: ``Job.type`` → agent macro-step key (banner / pipeline icons).
 * Order is the priority when several job types are active at once.
 */
const JOB_TYPE_MACRO_STEP_RULES = [
  { macro: "chapters", types: new Set(["script_chapters", "script_chapter_regenerate"]) },
  { macro: "outline", types: new Set(["script_outline"]) },
  { macro: "research", types: new Set(["research_run"]) },
  { macro: "auto_characters", types: new Set(["characters_generate"]) },
  { macro: "auto_images", types: new Set(["scene_generate_image", "scene_generate", "scene_extend"]) },
  { macro: "auto_videos", types: new Set(["scene_generate_video"]) },
  { macro: "auto_narration", types: new Set(["narration_generate", "narration_generate_scene"]) },
  { macro: "auto_rough_cut", types: new Set(["rough_cut"]) },
  { macro: "auto_final_cut", types: new Set(["fine_cut", "final_cut", "export", "subtitles_generate"]) },
  { macro: "story_research_review", types: new Set(["chapter_critique", "scene_critique", "scene_critic_revision"]) },
];

function jobTypeToMacroStepKey(jobType) {
  const t = String(jobType || "");
  for (const { macro, types } of JOB_TYPE_MACRO_STEP_RULES) {
    if (types.has(t)) return macro;
  }
  return null;
}

/**
 * When the agent run has no `current_step` and no `running` row in `steps_json` yet, infer the macro-step
 * from queued/running Studio jobs (image/video/TTS/cuts, etc.) so the banner and inspector stay animated.
 */
function inferAgentStepKeyFromActiveJobs(jobs) {
  if (!Array.isArray(jobs) || !jobs.length) return null;
  const active = jobs.filter((j) => j && (j.status === "running" || j.status === "queued"));
  if (!active.length) return null;
  const types = new Set(active.map((j) => String(j.type || "")));
  for (const { macro, types: ruleTypes } of JOB_TYPE_MACRO_STEP_RULES) {
    for (const rt of ruleTypes) {
      if (types.has(rt)) return macro;
    }
  }
  return null;
}

/** Map one ``Job.type`` to the same macro-step keys as ``inferAgentStepKeyFromActiveJobs``. */
function inferMacroStepKeyFromJobType(jobType) {
  return jobTypeToMacroStepKey(jobType) ?? "pipeline";
}

function studioJobKindHeadline(jobType) {
  const t = String(jobType || "");
  const m = {
    scene_generate: "Scene planning",
    scene_extend: "Extend scene",
    scene_generate_image: "Image generation",
    scene_generate_video: "Video generation",
    scene_critique: "Scene critic",
    chapter_critique: "Chapter critic",
    scene_critic_revision: "Scene revision",
    narration_generate: "Narration",
    narration_generate_scene: "Scene VO",
    subtitles_generate: "Subtitles",
    rough_cut: "Rough cut",
    fine_cut: "Fine cut",
    final_cut: "Final cut",
    export: "Export",
    research_run: "Research",
    script_outline: "Outline",
    script_chapters: "Chapter scripts",
    script_chapter_regenerate: "Chapter script (regenerate)",
    characters_generate: "Character bible",
  };
  return m[t] || "Background job";
}

/** Resolve macro-step for progress UI (handles full_video tail where `current_step` is null). */
function resolveEffectiveAgentStepKey(run, opts = {}) {
  if (!run) return "queued";
  if (run.status === "cancelled") {
    const evs = Array.isArray(run.steps_json) ? run.steps_json : [];
    const pipe = [...evs].reverse().find((e) => e && e.step === "pipeline" && e.status === "cancelled");
    if (pipe) return "pipeline";
    return "working";
  }
  if (run.status === "queued") return "queued";
  if (run.status === "running" && pipelineStopRequested(run.pipeline_control_json)) return "pipeline";
  const evs = Array.isArray(run.steps_json) ? run.steps_json : [];
  /** Prefer live `steps_json` over `current_step` — the worker may still hold a pre-tail latch (e.g. story_research_review) while the full-video tail runs auto_characters / auto_images / … */
  const running = lastAgentEventWithStatus(evs, "running");
  if (run.status === "running" && running?.step) return running.step;
  /**
   * Queued/running Celery jobs are often ahead of `current_step` during the full-video tail
   * (e.g. scene image jobs are in flight while the DB still says auto_characters).
   */
  if (run.status === "running") {
    const fromJobs = inferAgentStepKeyFromActiveJobs(opts.activeProjectJobs);
    if (fromJobs) return fromJobs;
  }
  if (run.current_step) return run.current_step;
  if (running) return running.step;
  const retry = lastAgentEventWithStatus(evs, "retry");
  if (retry) return retry.step;
  const last = evs[evs.length - 1];
  if (last && last.step && (last.status === "succeeded" || last.status === "skipped")) {
    const through = agentThroughFromRun(run, "full_video");
    const order = AGENT_PROGRESS_ORDER[through] || AGENT_PROGRESS_ORDER.full_video;
    const idx = order.indexOf(last.step);
    if (idx >= 0 && idx < order.length - 1) return order[idx + 1];
  }
  const tailFromJobs = inferAgentStepKeyFromActiveJobs(opts.activeProjectJobs);
  if (tailFromJobs) return tailFromJobs;
  return "working";
}

function pipelineStepActivityIconClass(stepId, runStatus) {
  const effKey = PIPELINE_STEP_ID_TO_AGENT_EFF_KEY[stepId];
  if (!effKey) return "fa-solid fa-spinner fa-spin fa-fw pipeline-step-fa-icon";
  return agentPipelineActivityIconClass(effKey, runStatus);
}

/**
 * While the agent is running, `workflow_phase` can lag (e.g. chapter scripts stay "pending" until the
 * batched LLM returns). Reflect `current_step` / last running event / active jobs so each phase shows in progress.
 */
function mergePipelineStepsWithAgentActivity(pipelineStatus, run, activeProjectJobs) {
  if (!pipelineStatus || !Array.isArray(pipelineStatus.steps)) return pipelineStatus;

  const agentSt = run?.status;
  // Do not keep forcing a row to "running" after the agent run was stopped (Studio jobs may still be active).
  if (agentSt === "cancelled") return pipelineStatus;
  if (agentSt === "running" && pipelineStopRequested(run?.pipeline_control_json)) return pipelineStatus;

  let effKey =
    run && ["running", "queued"].includes(agentSt)
      ? resolveEffectiveAgentStepKey(run, { activeProjectJobs })
      : inferAgentStepKeyFromActiveJobs(activeProjectJobs || []);
  if (
    agentSt === "running" &&
    (!effKey || effKey === "working" || !AGENT_STEP_TO_PIPELINE_STEP_ID[effKey])
  ) {
    const jk = inferAgentStepKeyFromActiveJobs(activeProjectJobs || []);
    if (jk && AGENT_STEP_TO_PIPELINE_STEP_ID[jk]) effKey = jk;
  }
  if (!effKey) return pipelineStatus;

  const targetId = AGENT_STEP_TO_PIPELINE_STEP_ID[effKey];
  if (!targetId) return pipelineStatus;

  const steps = pipelineStatus.steps.map((s) => {
    if (s.id !== targetId) return s;
    if (s.status === "done" || s.status === "blocked") return s;
    const next = { ...s, status: "running" };
    if (effKey === "chapters" && !next.detail) {
      next.detail =
        "Batched model call for all chapters — often several minutes with no intermediate database updates.";
    }
    return next;
  });
  return { ...pipelineStatus, steps };
}

function safeIsoMs(iso) {
  if (!iso || typeof iso !== "string") return 0;
  const t = Date.parse(iso);
  return Number.isFinite(t) ? t : 0;
}

function activeStudioJobsMatchEffKey(activeProjectJobs, effKey) {
  if (!Array.isArray(activeProjectJobs) || !effKey) return false;
  for (const rule of JOB_TYPE_MACRO_STEP_RULES) {
    if (rule.macro !== effKey) continue;
    for (const j of activeProjectJobs) {
      if (!j || (j.status !== "running" && j.status !== "queued")) continue;
      if (rule.types.has(String(j.type || ""))) return true;
    }
  }
  return false;
}

function stallThresholdMs(effKey, runStatus) {
  if (runStatus === "queued") return 120_000;
  if (effKey === "chapters" || effKey === "scenes") return 600_000;
  return 180_000;
}

function scenesProgressHeartbeatMs(stepsJson) {
  const p = lastScenesProgressEvent(stepsJson);
  if (!p?.at) return 0;
  return safeIsoMs(p.at);
}

/** Popup copy when the agent run shows no heartbeat — likely external API / network (client-side heuristic). */
const AGENT_STEP_STALL_COPY = {
  __default__: {
    title: "This step looks stalled",
    body: "The worker has not updated the automation run for a while. That usually means a call to an external API (text LLM, search, image, video, or speech) is taking a long time, failing slowly, or cannot be reached from the machine running the Celery worker. Verify Settings → Integrations, confirm the worker host can reach your API base URL (same network / firewall), and check worker logs for timeouts.",
  },
  queued: {
    title: "Run not picked up yet",
    body: "The run is still queued — the Celery worker may be busy, not running, or unable to connect to Redis. Check that workers are up and the broker is healthy.",
  },
  director: {
    title: "Director pack (text model)",
    body: "This step calls your configured text provider (OpenAI, LM Studio, OpenRouter, xAI, Gemini, etc.) to build the director pack. A wrong base URL, offline server, or long model load can block until the HTTP client times out.",
  },
  research: {
    title: "Research (search + text model)",
    body: "This step uses web search (e.g. Tavily when configured) and may call a text model to structure the dossier. Missing keys, rate limits, or an unreachable LLM endpoint can stall progress.",
  },
  outline: {
    title: "Outline (text model)",
    body: "Chapter outline generation uses your workspace text provider. Check connectivity and model availability on the worker host.",
  },
  chapters: {
    title: "Chapter scripts (long batched call)",
    body: "All chapter scripts are often produced in one large model call — several minutes without a database update can be normal. If it exceeds ~10 minutes with no run update, treat it like other API stalls: verify the text endpoint and worker logs.",
  },
  scenes: {
    title: "Scene planning (text model)",
    body: "Scene breakdown runs per chapter in sequence. Long scripts or many chapters take time. If per-chapter progress in the list is not advancing and the run timestamp is old, the text API may be hanging or unreachable.",
  },
  story_research_review: {
    title: "Story vs research (text model)",
    body: "This automated check compares the script to the research dossier via your text provider. Failures here are usually LLM timeouts or connectivity.",
  },
  auto_characters: {
    title: "Character bible (text model)",
    body: "Character inference uses your text provider. The same connectivity and timeout rules apply as other LLM steps.",
  },
  auto_images: {
    title: "Scene images (image providers)",
    body: "Images are requested from your configured image backend (e.g. Fal, Comfy, placeholders). Slow or failing provider APIs can stall this phase. Also confirm Studio background jobs are not blocked.",
  },
  auto_videos: {
    title: "Scene videos (video providers)",
    body: "Video generation depends on your video provider and queue. Long encodes or unreachable services look like a stalled step until a job completes or errors.",
  },
  auto_narration: {
    title: "Narration (speech APIs)",
    body: "TTS calls your configured speech provider. API keys, quotas, or unreachable endpoints cause long waits.",
  },
  auto_timeline: {
    title: "Timeline build",
    body: "Timeline assembly is mostly server-side; if it stalls for many minutes with no run update, check worker logs for exceptions or database issues.",
  },
  auto_rough_cut: {
    title: "Rough cut (render / ffmpeg)",
    body: "Rendering can take a long time for long programs. Very long stalls may indicate a stuck encoder or disk issue — see worker logs.",
  },
  auto_final_cut: {
    title: "Final cut / mux",
    body: "Final mux combines narration, music, and mix. External tools or I/O problems can delay completion.",
  },
  working: {
    title: "Worker between checkpoints",
    body: "The run is between named steps. If this persists, the worker may be blocked on a provider call that has not yet updated the database.",
  },
  pipeline: {
    title: "Pipeline control",
    body: "The worker is updating pipeline state. If this lasts unusually long, inspect worker logs.",
  },
};

function formatStallDuration(sec) {
  if (sec < 60) return `${sec}s`;
  const m = Math.floor(sec / 60);
  const s = sec % 60;
  return s > 0 ? `${m}m ${s}s` : `${m}m`;
}

/**
 * Client-side stall signal: no recent `updated_at` on the agent run (and no scenes progress heartbeat when relevant),
 * while not explained by active Studio jobs for the same macro-step.
 */
function computeAgentRunStallInfo(run, activeProjectJobs, nowMs) {
  const empty = { stalled: false };
  if (!run) return empty;
  const st = run.status;
  if (st !== "running" && st !== "queued") return empty;
  if (st === "paused") return empty;
  if (st === "running" && pipelineStopRequested(run.pipeline_control_json)) return empty;

  const effKey = resolveEffectiveAgentStepKey(run, { activeProjectJobs });
  if (activeStudioJobsMatchEffKey(activeProjectJobs, effKey)) return empty;

  const thr = stallThresholdMs(effKey, st);
  let hb = safeIsoMs(run.updated_at);
  if (effKey === "scenes") {
    hb = Math.max(hb, scenesProgressHeartbeatMs(run.steps_json));
  }

  if (hb <= 0) return empty;
  const age = nowMs - hb;
  if (age < thr) return empty;

  const stallSeconds = Math.floor(age / 1000);
  const pipelineStepId = AGENT_STEP_TO_PIPELINE_STEP_ID[effKey] ?? null;
  const copy = AGENT_STEP_STALL_COPY[effKey] || AGENT_STEP_STALL_COPY.__default__;
  return {
    stalled: true,
    effKey,
    pipelineStepId,
    stallSeconds,
    stallLabel: formatStallDuration(stallSeconds),
    title: copy.title,
    body: copy.body,
  };
}

/** Restore Editor vs Settings + last project/chapter/run after refresh. */
function readDirectorUiSession() {
  try {
    const raw = localStorage.getItem(DIRECTOR_UI_SESSION_KEY);
    if (!raw) return null;
    const o = JSON.parse(raw);
    if (!o || typeof o !== "object") return null;
    const ap = normalizeDirectorActivePage(o.activePage);
    return {
      activePage: ap,
      projectId: typeof o.projectId === "string" ? o.projectId.trim() : "",
      chapterId: typeof o.chapterId === "string" ? o.chapterId.trim() : "",
      agentRunId: typeof o.agentRunId === "string" ? o.agentRunId.trim() : "",
      expandedScene:
        o.expandedScene && typeof o.expandedScene === "string" ? o.expandedScene.trim() || null : null,
      timelineVersionId: typeof o.timelineVersionId === "string" ? o.timelineVersionId.trim() : "",
      mediaJobId: typeof o.mediaJobId === "string" ? o.mediaJobId.trim() : "",
      charactersJobId: typeof o.charactersJobId === "string" ? o.charactersJobId.trim() : "",
    };
  } catch {
    return null;
  }
}

/** One-time boot values from localStorage so progress + polling survive a full page refresh. */
function initialBootFromDirectorSession() {
  const s = readDirectorUiSession();
  if (!s) {
    return {
      projectId: "",
      agentRunId: "",
      chapterId: "",
      expandedScene: null,
      timelineVersionId: "",
      mediaJobId: "",
      charactersJobId: "",
      mediaPoll: false,
    };
  }
  const aid = typeof s.agentRunId === "string" ? s.agentRunId.trim() : "";
  const mid = typeof s.mediaJobId === "string" ? s.mediaJobId.trim() : "";
  return {
    projectId: typeof s.projectId === "string" ? s.projectId.trim() : "",
    agentRunId: aid.length >= 32 ? aid : "",
    chapterId: typeof s.chapterId === "string" ? s.chapterId.trim() : "",
    expandedScene: s.expandedScene || null,
    timelineVersionId: typeof s.timelineVersionId === "string" ? s.timelineVersionId.trim() : "",
    mediaJobId: mid,
    charactersJobId: typeof s.charactersJobId === "string" ? s.charactersJobId.trim() : "",
    mediaPoll: Boolean(mid),
  };
}

const UI_BOOT = initialBootFromDirectorSession();

export default function App() {
  const [activePage, setActivePage] = useState(() => readDirectorUiSession()?.activePage ?? "editor");
  const [projects, setProjects] = useState([]);
  const lastProjectsPollSnapshotRef = useRef("");
  const [appConfig, setAppConfig] = useState({});
  /** Optional secret keys filled from platform workspace (GET /v1/settings); UI explains instead of showing values. */
  const [platformCredentialKeysInherited, setPlatformCredentialKeysInherited] = useState([]);
  const credKeyInherited = (key) => platformCredentialKeysInherited.includes(key);
  const [credentialKeysPresent, setCredentialKeysPresent] = useState({});
  const credKeyStoredOnServer = (key) =>
    Boolean(credentialKeysPresent && typeof credentialKeysPresent === "object" && credentialKeysPresent[key]);
  const credKeyHidden = (key) => credKeyInherited(key) || credKeyStoredOnServer(key);
  const credKeyNote = (key) => {
    const inh = credKeyInherited(key);
    const st = credKeyStoredOnServer(key);
    if (!inh && !st) return null;
    const text = inh
      ? "Using your administrator's key (not displayed). Leave blank to keep it, or paste your own to override."
      : "A key is saved for this workspace (not displayed). Leave blank to keep it, or paste a new key to replace.";
    return (
      <p className="subtle" style={{ marginTop: -4 }}>
        {text}
      </p>
    );
  };
  const credKeyNoteXaiGrok = () => {
    if (!credKeyHidden("grok_api_key") && !credKeyHidden("xai_api_key")) return null;
    const inh = credKeyInherited("grok_api_key") || credKeyInherited("xai_api_key");
    const text = inh
      ? "Using your administrator's key for xAI / Grok (not displayed). Leave blank to keep it, or paste your own to override."
      : "A workspace API key is saved (not displayed). Leave blank to keep it, or paste a new key to replace.";
    return (
      <p className="subtle" style={{ marginTop: -4 }}>
        {text}
      </p>
    );
  };
  const [settingsBusy, setSettingsBusy] = useState(false);
  /** LLM system prompts (per user / workspace) */
  const [llmPrompts, setLlmPrompts] = useState([]);
  const [llmPromptsBusy, setLlmPromptsBusy] = useState(false);
  const [llmPromptsErr, setLlmPromptsErr] = useState("");
  const [llmPromptDrafts, setLlmPromptDrafts] = useState(() => ({}));
  /** Settings sub-page: sidebar tab id */
  const [settingsTab, setSettingsTab] = useState("generation");
  /** Generation panel: engines | narration_styles | visual */
  const [generationSettingsTab, setGenerationSettingsTab] = useState("engines");
  const [narrationStylesLib, setNarrationStylesLib] = useState([]);
  const [narrationStylesLibBusy, setNarrationStylesLibBusy] = useState(false);
  const [narrationStylesLibErr, setNarrationStylesLibErr] = useState("");
  const [narFormTitle, setNarFormTitle] = useState("");
  const [narFormPrompt, setNarFormPrompt] = useState("");
  /** `user:<uuid>` while editing a custom style, else null */
  const [narEditingRef, setNarEditingRef] = useState(null);
  const [title, setTitle] = useState("Agent run — test");
  /** Locked to the open project after load; drives new agent-run brief before a project exists. */
  const [frameAspectRatio, setFrameAspectRatio] = useState("16:9");
  const [topic, setTopic] = useState(
    "Urban community gardens and neighborhood food security in one mid-size city.",
  );
  const [runtime, setRuntime] = useState(15);
  const [projectId, setProjectId] = useState(() => UI_BOOT.projectId);
  const [agentRunId, setAgentRunId] = useState(() => UI_BOOT.agentRunId);
  const [run, setRun] = useState(null);
  const [chapters, setChapters] = useState([]);
  /** Research & scripts tab: dossier JSON textarea + per-chapter script fields */
  const [researchJsonDraft, setResearchJsonDraft] = useState("");
  const [researchMeta, setResearchMeta] = useState(null);
  const [researchPageBusy, setResearchPageBusy] = useState(false);
  const [researchPageErr, setResearchPageErr] = useState("");
  const [researchPipelineBusy, setResearchPipelineBusy] = useState(false);
  const [chapterRegenerateId, setChapterRegenerateId] = useState("");
  const [chapterScriptsDraft, setChapterScriptsDraft] = useState({});
  const [chapterId, setChapterId] = useState(() => UI_BOOT.chapterId);
  const [scenes, setScenes] = useState([]);
  const [expandedScene, setExpandedScene] = useState(() => UI_BOOT.expandedScene);
  /** Matches visible gallery: expanded scene, or first scene when none expanded yet. */
  const sceneIdForAssetGalleryRefresh = useCallback(
    () => String(expandedScene || scenes[0]?.id || "").trim(),
    [expandedScene, scenes],
  );
  const [sceneAssets, setSceneAssets] = useState({});
  const [retryPrompt, setRetryPrompt] = useState("");
  /** When this changes, we pre-fill retry prompt from the scene (avoid clobbering edits on scenes refresh). */
  const retryPromptSceneRef = useRef(null);
  const [retryVideoPrompt, setRetryVideoPrompt] = useState("");
  const retryVideoPromptSceneRef = useRef(null);
  /** Inline scene narration editor: sync draft when switching scene vs. when scenes[] reloads from API. */
  const scriptEditSceneIdRef = useRef("");
  const [sceneNarrationDraft, setSceneNarrationDraft] = useState("");
  const [sceneNarrationDirty, setSceneNarrationDirty] = useState(false);
  const [sceneNarrationSaving, setSceneNarrationSaving] = useState(false);
  const [promptEnhanceImageBusy, setPromptEnhanceImageBusy] = useState(false);
  const [promptEnhanceVoBusy, setPromptEnhanceVoBusy] = useState(false);
  const [promptExpandVoBusy, setPromptExpandVoBusy] = useState(false);
  /** Scene script expand: approximate sentence count + optional user hints for the LLM. */
  const [sceneVoExpandSentenceTarget, setSceneVoExpandSentenceTarget] = useState(6);
  const [sceneVoExpandContext, setSceneVoExpandContext] = useState("");
  const [mediaJobId, setMediaJobId] = useState(() => UI_BOOT.mediaJobId);
  const [mediaPoll, setMediaPoll] = useState(() => UI_BOOT.mediaPoll);
  const [lastHandledMediaJobId, setLastHandledMediaJobId] = useState("");
  /** After image/video jobs, pin canvas to this asset until user picks another scene */
  const [pinnedPreviewAssetId, setPinnedPreviewAssetId] = useState(null);
  const [previewMediaError, setPreviewMediaError] = useState(false);
  /** Media preview card: scene asset vs timeline compiled MP4. */
  const [mediaPreviewTab, setMediaPreviewTab] = useState("scene");
  const [phase3Summary, setPhase3Summary] = useState(null);
  const [criticReport, setCriticReport] = useState(null);
  /** Summary rows from `GET /v1/projects/{id}/critic-reports` (newest first). */
  const [projectCriticReports, setProjectCriticReports] = useState([]);
  const [criticListError, setCriticListError] = useState("");
  const [phase5Ready, setPhase5Ready] = useState(null);
  /** Set when rough/final/export fails with PHASE5 — dialog: review scenes vs approve all. */
  const [phase5ExportGateModal, setPhase5ExportGateModal] = useState(null);
  const [approveAllMediaBusy, setApproveAllMediaBusy] = useState(false);
  const [timelineVersionId, setTimelineVersionId] = useState(() => UI_BOOT.timelineVersionId);
  const [musicBeds, setMusicBeds] = useState([]);
  const [mixMusicVol, setMixMusicVol] = useState(0.28);
  const [mixNarrVol, setMixNarrVol] = useState(1);
  const [narrMixMode, setNarrMixMode] = useState("scene_timeline");
  const [musicBedPick, setMusicBedPick] = useState("");
  /** Rough-cut: dissolve between consecutive stills (timeline ``clip_crossfade_sec``). */
  const [clipCrossfadeSec, setClipCrossfadeSec] = useState(0);
  /** When true, POST /final-cut sends ``burn_subtitles_into_video`` (requires ``subtitles.vtt`` on disk). */
  const [burnSubtitlesOnFinalCut, setBurnSubtitlesOnFinalCut] = useState(false);
  const [musicUploadLicense, setMusicUploadLicense] = useState("");
  const musicFileInputRef = useRef(null);
  const sceneClipFileInputRef = useRef(null);
  const [sceneClipUploadKind, setSceneClipUploadKind] = useState("auto");
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");
  const [authBootstrap, setAuthBootstrap] = useState({
    done: false,
    mode: "legacy",
    needLogin: false,
    allowRegistration: true,
  });
  /** SaaS: no API/project streaming until bootstrap finished and user is logged in (avoids 401 → "missing credentials"). */
  const studioReady = useMemo(
    () => authBootstrap.done && !(authBootstrap.mode === "saas" && authBootstrap.needLogin),
    [authBootstrap.done, authBootstrap.mode, authBootstrap.needLogin],
  );
  const gatedProjectId = studioReady ? projectId : "";
  const [saasTenants, setSaasTenants] = useState([]);
  /** From GET /v1/auth/me (entitlements + billing) for gating and Account page */
  const [accountProfile, setAccountProfile] = useState(null);
  const [eventAuthKey, setEventAuthKey] = useState(0);
  const [upgradeModalOpen, setUpgradeModalOpen] = useState(false);
  const authModeRef = useRef("legacy");
  const [showShortcutHelp, setShowShortcutHelp] = useState(false);
  /** Slide-out primary nav on narrow viewports (see `index.css` @media). */
  const [mobileNavOpen, setMobileNavOpen] = useState(false);
  const [isMobileLayout, setIsMobileLayout] = useState(
    () => typeof window !== "undefined" && window.matchMedia("(max-width: 900px)").matches,
  );
  const [celeryStatus, setCeleryStatus] = useState("unknown");
  const [celeryWorkers, setCeleryWorkers] = useState([]);
  /** Tooltip when API reports *online* via DB fallback (solo pool busy). */
  const [celeryStatusDetail, setCeleryStatusDetail] = useState("");
  const [celeryRestarting, setCeleryRestarting] = useState(false);
  /** Workspace settings fetch/save problems — shown on Settings only, not over the editor. */
  const [settingsLoadError, setSettingsLoadError] = useState("");
  const [usageSummary, setUsageSummary] = useState(null);
  const [usageErr, setUsageErr] = useState("");
  const [usageLoading, setUsageLoading] = useState(false);
  const [usageDays, setUsageDays] = useState(30);
  /** Project character bible (editable); used in image/video prompts. */
  const [projectCharacters, setProjectCharacters] = useState([]);
  const [charactersJobId, setCharactersJobId] = useState(() => UI_BOOT.charactersJobId);
  /** Settings → Integrations: `POST /v1/jobs` adapter_smoke poll target. */
  const [adapterSmokeJobId, setAdapterSmokeJobId] = useState(null);
  const [adapterSmokePollActive, setAdapterSmokePollActive] = useState(false);
  const [telegramTestLoading, setTelegramTestLoading] = useState(false);
  const adapterSmokeLabelRef = useRef("");
  /** Queued + running `Job` rows for the open project (GET /v1/projects/{id}/jobs/active). */
  const [activeProjectJobs, setActiveProjectJobs] = useState([]);
  const [activeJobsLoadErr, setActiveJobsLoadErr] = useState("");
  /** Prior snapshot to detect jobs that left the active list (completed/cancelled) without UI refresh. */
  const activeJobsPrevRef = useRef([]);
  /** Latest ``loadActiveProjectJobs`` for SSE ``onConnected`` (handler is stable; ref avoids stale projectId). */
  const loadActiveProjectJobsRef = useRef(() => Promise.resolve());
  /** `loadPipelineStatus` runs on an interval; bundle full phase5-readiness at most this often (ms). */
  const lastPhase5BundledRefreshRef = useRef(0);
  const phase5BundledPidRef = useRef("");
  /** Previous `pipeline-status.latest_timeline_version_id` for this project — used to follow a new latest timeline without clobbering manual picks. */
  const lastPolledLatestTimelineRef = useRef(null);
  const jobPollIntervalMs = Math.max(
    500,
    Math.min(120_000, Number(appConfig.studio_job_poll_interval_ms) || 800),
  );
  /** Background refresh for the project list (Project & story → Projects). */
  const projectsListPollMs = useMemo(
    () => Math.min(15_000, Math.max(2_500, jobPollIntervalMs * 3)),
    [jobPollIntervalMs],
  );
  const { job: charactersJob } = usePollJob(
    charactersJobId,
    Boolean(charactersJobId) && studioReady,
    jobPollIntervalMs,
  );

  // ---------------------------------------------------------------------------
  // Toast notifications
  // ---------------------------------------------------------------------------
  const { toasts, toast: showToast, dismissToast } = useToast({ durationMs: 5000 });

  /** Do not gate on ``studioReady`` — bootstrap can lag after POST /v1/jobs; polling must run or the test appears stuck. */
  const { job: adapterSmokeJob, err: adapterSmokePollErr } = usePollJob(
    adapterSmokeJobId,
    adapterSmokePollActive && Boolean(adapterSmokeJobId),
    jobPollIntervalMs,
  );

  /** SaaS: Studio Admin only for workspace membership role **admin** (not owner/member); legacy mode keeps Admin for operators. */
  const canAccessStudioAdmin = useMemo(() => {
    if (authBootstrap.mode !== "saas") return true;
    if (!accountProfile?.active_tenant_id || !Array.isArray(accountProfile.tenants)) return false;
    const row = accountProfile.tenants.find((x) => x.id === accountProfile.active_tenant_id);
    const r = String(row?.role || "").toLowerCase();
    return r === "admin";
  }, [authBootstrap.mode, accountProfile]);

  const studioPageRails = useMemo(() => {
    const ent = accountProfile?.entitlements;
    let rows = !ent || ent.chat_enabled !== false ? STUDIO_PAGE_RAILS : STUDIO_PAGE_RAILS.filter((t) => t.id !== "chat");
    if (!canAccessStudioAdmin) rows = rows.filter((t) => t.id !== "admin");
    return rows;
  }, [accountProfile, canAccessStudioAdmin]);

  useEffect(() => {
    if (!canAccessStudioAdmin && activePage === "admin") setActivePage("editor");
  }, [canAccessStudioAdmin, activePage]);

  useEffect(() => {
    if (!mobileNavOpen) return;
    const onKey = (e) => {
      if (e.key === "Escape") setMobileNavOpen(false);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [mobileNavOpen]);

  useEffect(() => {
    if (mobileNavOpen) document.body.style.overflow = "hidden";
    else document.body.style.overflow = "";
    return () => {
      document.body.style.overflow = "";
    };
  }, [mobileNavOpen]);

  useEffect(() => {
    const mq = window.matchMedia("(max-width: 900px)");
    const sync = () => {
      const narrow = mq.matches;
      setIsMobileLayout(narrow);
      if (!narrow) setMobileNavOpen(false);
    };
    sync();
    mq.addEventListener("change", sync);
    return () => mq.removeEventListener("change", sync);
  }, []);

  const telegramPlanLocked = useMemo(
    () => authBootstrap.mode === "saas" && accountProfile?.entitlements?.telegram_enabled === false,
    [authBootstrap.mode, accountProfile],
  );

  /** When the Studio is served over HTTPS on a real host, API webhooks use the same origin + `/v1/...` (nginx → API). */
  const telegramWebhookPublicUrl = useMemo(() => {
    const path = apiPath("/v1/integrations/telegram/webhook");
    if (path.startsWith("http://") || path.startsWith("https://")) return path;
    if (typeof window === "undefined") return "";
    const { protocol, hostname } = window.location;
    if (protocol !== "https:") return "";
    if (hostname === "localhost" || hostname === "127.0.0.1") return "";
    return `${window.location.origin}${path}`;
  }, []);

  /** Same-origin `https://host` for copy-paste in `telegram-set-webhook.sh` (no path). */
  const telegramWebhookPublicOrigin = useMemo(() => {
    if (typeof window === "undefined") return "";
    const { protocol, hostname } = window.location;
    if (protocol !== "https:") return "";
    if (hostname === "localhost" || hostname === "127.0.0.1") return "";
    return window.location.origin;
  }, []);

  /** Slug of the workspace plan when subscription is active or trialing (Upgrade modal hides re-subscribe for this plan). */
  const billingActivePlanSlug = useMemo(() => {
    const b = accountProfile?.billing;
    if (!b) return null;
    const st = String(b.status || "").toLowerCase();
    if (st !== "active" && st !== "trialing") return null;
    const s = String(b.plan_slug || "").trim();
    return s || null;
  }, [accountProfile?.billing]);

  /** Admin → Tools budget pipeline: target workspace matches the Studio session cookie. */
  const adminToolsWorkspaceTenantId = useMemo(() => {
    const fromProfile = (accountProfile?.active_tenant_id || accountProfile?.tenant_id || "").trim();
    if (fromProfile) return fromProfile;
    return getDirectorTenantId().trim();
  }, [accountProfile?.active_tenant_id, accountProfile?.tenant_id, eventAuthKey]);

  const refreshAccountProfile = useCallback(async () => {
    try {
      normalizeDirectorAuthStorage();
      const r = await api("/v1/auth/config");
      const cfg = await parseJson(r);
      if (!cfg.data?.auth_enabled) {
        setAccountProfile({
          auth_enabled: false,
          entitlements: {
            chat_enabled: true,
            telegram_enabled: true,
            max_projects: null,
            full_through_automation_enabled: true,
            hands_off_unattended_enabled: true,
            subtitles_enabled: true,
          },
          billing: { status: "none", plan_slug: null, plan_display_name: null },
        });
        return;
      }
      const r2 = await api("/v1/auth/me");
      const me = await parseJson(r2);
      if (r2.ok && me.data) {
        syncDirectorTenantFromMePayload(me.data);
        setDirectorSaaSClientActive(true);
        setAccountProfile(me.data);
        try {
          const rr = await api("/v1/auth/refresh", { method: "POST", body: "{}" });
          const bb = await parseJson(rr);
          if (rr.ok) {
            const tid = String(bb?.data?.tenant_id || "").trim() || getDirectorTenantId();
            if (tid) setDirectorAuthSession({ tenantId: tid });
          }
        } catch {
          /* ignore */
        }
      } else {
        setAccountProfile(null);
      }
    } catch {
      setAccountProfile(null);
    }
  }, []);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        normalizeDirectorAuthStorage();
        const r = await api("/v1/auth/config");
        const body = await parseJson(r);
        if (cancelled) return;
        const authEnabled = Boolean(body.data?.auth_enabled);
        const allowRegistration = body.data?.allow_registration !== false;
        if (!authEnabled) {
          clearDirectorAuthSession();
          authModeRef.current = "legacy";
          setAuthBootstrap({
            done: true,
            mode: "legacy",
            needLogin: false,
            allowRegistration: true,
          });
          setSaasTenants([]);
          if (!cancelled) {
            setAccountProfile({
              auth_enabled: false,
              entitlements: {
                chat_enabled: true,
                telegram_enabled: true,
                max_projects: null,
                full_through_automation_enabled: true,
                hands_off_unattended_enabled: true,
                subtitles_enabled: true,
              },
              billing: { status: "none" },
            });
          }
          return;
        }
        const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
        const transientAuthMeStatus = (st) =>
          st === 0 || st === 429 || st === 408 || (st >= 500 && st < 600);

        let r2 = await api("/v1/auth/me");
        let me = await parseJson(r2);
        if (cancelled) return;
        // Race with Firebase redirect: first /auth/me may 401 before the cookie is visible; retry once.
        if (!r2.ok && r2.status === 401) {
          const r3 = await api("/v1/auth/me");
          const me3 = await parseJson(r3);
          if (r3.ok && me3.data?.email) {
            r2 = r3;
            me = me3;
          }
        }
        // API blips (502/503/429) must not wipe a valid browser session — only 401 means "signed out".
        for (let attempt = 0; !cancelled && !r2.ok && transientAuthMeStatus(r2.status) && attempt < 4; attempt++) {
          await sleep(400 * (attempt + 1));
          if (cancelled) return;
          r2 = await api("/v1/auth/me");
          me = await parseJson(r2);
        }
        if (r2.ok && me.data?.email) {
          syncDirectorTenantFromMePayload(me.data);
          setDirectorSaaSClientActive(true);
          authModeRef.current = "saas";
          setSaasTenants(Array.isArray(me.data.tenants) ? me.data.tenants : []);
          setAccountProfile(me.data);
          setAuthBootstrap({
            done: true,
            mode: "saas",
            needLogin: false,
            allowRegistration,
          });
          void (async () => {
            try {
              const rr = await api("/v1/auth/refresh", { method: "POST", body: "{}" });
              const bb = await parseJson(rr);
              if (rr.ok) {
                const tid = String(bb?.data?.tenant_id || "").trim() || getDirectorTenantId();
                if (tid) setDirectorAuthSession({ tenantId: tid });
              }
            } catch {
              /* ignore */
            }
          })();
          return;
        }
        if (r2.status === 401) {
          clearDirectorAuthSession();
          authModeRef.current = "saas";
          setSaasTenants([]);
          setAccountProfile(null);
          setError("");
          setAuthBootstrap({
            done: true,
            mode: "saas",
            needLogin: true,
            allowRegistration,
          });
          return;
        }
        authModeRef.current = "saas";
        setSaasTenants([]);
        setAccountProfile(null);
        setAuthBootstrap({
          done: true,
          mode: "saas",
          needLogin: false,
          allowRegistration,
        });
        setError(
          (prev) =>
            prev ||
            `Could not reach the account service (HTTP ${r2.status}). Your session was kept — try refreshing the page.`,
        );
      } catch {
        if (!cancelled) {
          authModeRef.current = "legacy";
          setAuthBootstrap({
            done: true,
            mode: "legacy",
            needLogin: false,
            allowRegistration: true,
          });
          setAccountProfile({
            auth_enabled: false,
            entitlements: {
              chat_enabled: true,
              telegram_enabled: true,
              max_projects: null,
              full_through_automation_enabled: true,
              hands_off_unattended_enabled: true,
              subtitles_enabled: true,
            },
            billing: { status: "none" },
          });
        }
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    if (!authBootstrap.done || authBootstrap.needLogin) return;
    void refreshAccountProfile();
  }, [authBootstrap.done, authBootstrap.needLogin, eventAuthKey, refreshAccountProfile]);

  /** Keep in-memory tenant mirror aligned with server (auth/me) for hooks and cross-origin media hints. */
  useEffect(() => {
    if (authBootstrap.mode !== "saas" || authBootstrap.needLogin || !authBootstrap.done) return;
    const tid = String(accountProfile?.active_tenant_id || accountProfile?.tenant_id || "").trim();
    if (tid) setDirectorAuthSession({ tenantId: tid });
  }, [
    authBootstrap.done,
    authBootstrap.needLogin,
    authBootstrap.mode,
    accountProfile?.active_tenant_id,
    accountProfile?.tenant_id,
  ]);

  useEffect(() => {
    try {
      const u = new URL(window.location.href);
      if (u.searchParams.get("billing") === "success") {
        void refreshAccountProfile().then(() => {
          showToast("Subscription updated — refreshing your access.", { type: "success", durationMs: 6000 });
        });
        u.searchParams.delete("billing");
        window.history.replaceState({}, "", `${u.pathname}${u.search}${u.hash}`);
      }
    } catch {
      /* ignore */
    }
  }, [refreshAccountProfile, showToast]);

  /** Deep link from Telegram “Open Studio” (and bookmarks): ``?agentRun=<uuid>`` */
  useEffect(() => {
    if (!authBootstrap.done || authBootstrap.needLogin) return;
    try {
      const u = new URL(window.location.href);
      const ar = u.searchParams.get("agentRun") || u.searchParams.get("run");
      const t = ar && String(ar).trim();
      if (t && /^[0-9a-f-]{36}$/i.test(t)) {
        setAgentRunId(t);
        u.searchParams.delete("agentRun");
        u.searchParams.delete("run");
        window.history.replaceState({}, "", `${u.pathname}${u.search}${u.hash}`);
      }
    } catch {
      /* ignore */
    }
  }, [authBootstrap.done, authBootstrap.needLogin]);

  useEffect(() => {
    const ent = accountProfile?.entitlements;
    if (!ent || ent.chat_enabled !== false) return;
    if (activePage === "chat") {
      setActivePage("editor");
    }
  }, [accountProfile, activePage]);

  /** Clear editor + ``director_ui_session`` so a new login never inherits another user's open project / scenes. */
  const resetWorkspaceForTenantBoundary = useCallback(() => {
    try {
      localStorage.removeItem(DIRECTOR_UI_SESSION_KEY);
    } catch {
      /* ignore */
    }
    setError("");
    setMessage("");
    setActivePage("editor");
    setProjectId("");
    setAgentRunId("");
    setRun(null);
    setChapters([]);
    setChapterId("");
    setScenes([]);
    setExpandedScene(null);
    setPinnedPreviewAssetId(null);
    setSceneAssets({});
    setTrimByScene({});
    setTimelineVersionId("");
    setMediaJobId("");
    setMediaPoll(false);
    setCharactersJobId("");
    setLastHandledMediaJobId("");
    setRetryPrompt("");
    setRetryVideoPrompt("");
    retryPromptSceneRef.current = null;
    retryVideoPromptSceneRef.current = null;
    setPhase3Summary(null);
    setCriticReport(null);
    setProjectCriticReports([]);
    setCriticListError("");
    setPhase5Ready(null);
    setPipelineStatus(null);
    setActiveProjectJobs([]);
    setActiveJobsLoadErr("");
    setProjectCharacters([]);
    setResearchJsonDraft("");
    setResearchMeta(null);
    setResearchPageErr("");
    setResearchPipelineBusy(false);
    setTitle("New documentary");
    setTopic("Describe your topic, audience, and the story you want to tell.");
    setRuntime(15);
  }, []);

  const onSaaSLoggedIn = useCallback(
    (payload) => {
      resetWorkspaceForTenantBoundary();
      setDirectorSaaSClientActive(true);
      setDirectorAuthSession({ tenantId: payload.tenant_id });
      setSaasTenants(Array.isArray(payload.tenants) ? payload.tenants : []);
      setAuthBootstrap((s) => ({ ...s, needLogin: false }));
      setEventAuthKey((k) => k + 1);
      queueMicrotask(() => {
        void refreshAccountProfile();
      });
    },
    [refreshAccountProfile, resetWorkspaceForTenantBoundary],
  );

  const signOutSaas = useCallback(() => {
    void api("/v1/auth/logout", { method: "POST", body: "{}" }).catch(() => {});
    clearDirectorAuthSession();
    resetWorkspaceForTenantBoundary();
    setSaasTenants([]);
    setAccountProfile(null);
    setAuthBootstrap((s) => ({ ...s, needLogin: true }));
    setEventAuthKey((k) => k + 1);
  }, [resetWorkspaceForTenantBoundary]);

  /** SaaS login screen: drop any stale ``director_ui_session`` / in-memory project from a prior browser user. */
  useEffect(() => {
    if (!authBootstrap.done || authBootstrap.mode !== "saas" || !authBootstrap.needLogin) return;
    resetWorkspaceForTenantBoundary();
  }, [authBootstrap.done, authBootstrap.mode, authBootstrap.needLogin, resetWorkspaceForTenantBoundary]);

  useEffect(() => {
    const onSessionExpired = () => {
      if (authModeRef.current !== "saas") return;
      clearDirectorAuthSession();
      resetWorkspaceForTenantBoundary();
      setSaasTenants([]);
      setAccountProfile(null);
      setAuthBootstrap((s) => ({
        ...s,
        done: true,
        mode: "saas",
        needLogin: true,
      }));
      setEventAuthKey((k) => k + 1);
    };
    window.addEventListener("director:session-expired", onSessionExpired);
    return () => window.removeEventListener("director:session-expired", onSessionExpired);
  }, [resetWorkspaceForTenantBoundary]);

  /** Touch server session (sliding TTL) and mirror workspace id from refresh response. */
  const tryRefreshSaaSAccessToken = useCallback(async () => {
    if (authBootstrap.mode !== "saas" || authBootstrap.needLogin) return;
    const tenant = getDirectorTenantId().trim();
    if (!tenant) return;
    try {
      const r = await api("/v1/auth/refresh", { method: "POST", body: "{}" });
      const b = await parseJson(r);
      if (r.ok) {
        setDirectorSaaSClientActive(true);
        const tid = String(b?.data?.tenant_id || tenant).trim();
        if (tid) setDirectorAuthSession({ tenantId: tid });
        setEventAuthKey((k) => k + 1);
      }
    } catch {
      /* ignore */
    }
  }, [authBootstrap.mode, authBootstrap.needLogin]);

  useEffect(() => {
    if (!authBootstrap.done || authBootstrap.needLogin || authBootstrap.mode !== "saas") return;
    const id = setInterval(() => void tryRefreshSaaSAccessToken(), 5 * 60 * 1000);
    void tryRefreshSaaSAccessToken();
    return () => clearInterval(id);
  }, [authBootstrap.done, authBootstrap.needLogin, authBootstrap.mode, tryRefreshSaaSAccessToken]);

  /** Long runs often background the tab; timers are throttled so session refresh may be delayed. */
  useEffect(() => {
    if (!authBootstrap.done || authBootstrap.needLogin || authBootstrap.mode !== "saas") return;
    const onVisible = () => {
      if (document.visibilityState === "visible") void tryRefreshSaaSAccessToken();
    };
    document.addEventListener("visibilitychange", onVisible);
    return () => document.removeEventListener("visibilitychange", onVisible);
  }, [authBootstrap.done, authBootstrap.needLogin, authBootstrap.mode, tryRefreshSaaSAccessToken]);

  useEffect(() => {
    if (!adapterSmokePollActive || !adapterSmokeJobId) return;
    if (adapterSmokePollErr) {
      showToast(`Provider connection test failed: ${adapterSmokePollErr}`, {
        type: "error",
        durationMs: 8000,
      });
      setAdapterSmokePollActive(false);
      setAdapterSmokeJobId(null);
      return;
    }
    const j = adapterSmokeJob;
    if (!j || String(j.id) !== String(adapterSmokeJobId)) return;
    const st = String(j.status || "");
    if (st === "succeeded") {
      const label = adapterSmokeLabelRef.current || j.provider || "provider";
      showToast(`Connection OK — ${label}`, { type: "success" });
      setAdapterSmokePollActive(false);
      setAdapterSmokeJobId(null);
    } else if (st === "failed" || st === "cancelled") {
      const detail = j.error_message || st;
      showToast(`Connection failed — ${adapterSmokeLabelRef.current || j.provider}: ${detail}`, {
        type: "error",
        durationMs: 8000,
      });
      setAdapterSmokePollActive(false);
      setAdapterSmokeJobId(null);
    }
  }, [
    adapterSmokePollActive,
    adapterSmokeJobId,
    adapterSmokeJob,
    adapterSmokePollErr,
    showToast,
  ]);

  // ---------------------------------------------------------------------------
  // SSE real-time event stream — replaces active-job + Celery polling loops.
  // `sseConnected` is true while the EventSource connection is live so polling
  // effects can lengthen or skip their intervals to avoid duplicate work.
  // ---------------------------------------------------------------------------
  const { sseConnected } = useProjectEvents(gatedProjectId || null, {
    onConnected: useCallback(() => {
      // Trigger an immediate active-jobs load on first connect so the UI is
      // never stale on project open, even before the first SSE event arrives.
      void loadActiveProjectJobsRef.current();
    }, []),
    onJobsUpdate: useCallback((jobs) => {
      setActiveProjectJobs(jobs);
    }, []),
    onAgentRunUpdate: useCallback((updatedRun) => {
      if (!updatedRun) return;
      // Merge partial update — SSE only sends {id, status, current_step, updated_at}.
      // Preserve the full run object (steps_json, etc.) loaded by polling.
      setRun((prev) => {
        if (!prev || prev.id === updatedRun.id) return { ...(prev || {}), ...updatedRun };
        return prev;
      });
    }, []),
    onAssetReady: useCallback((asset) => {
      if (!asset?.scene_id) return;
      // Merge new asset into the scene's asset list without a full reload.
      setSceneAssets((prev) => {
        const existing = prev[asset.scene_id] ?? [];
        if (existing.some((a) => a.id === asset.id)) return prev;
        return { ...prev, [asset.scene_id]: [...existing, asset] };
      });
      showToast(
        `${asset.asset_type === "video" ? "Video" : "Image"} ready`,
        { type: "success" },
      );
    }, [showToast]),
    onCeleryStatus: useCallback((online) => {
      setCeleryStatus(online ? "online" : "offline");
    }, []),
  }, eventAuthKey);
  const [panelSizes, setPanelSizes] = useState({ left: 300, right: 360, bottom: 240 });
  const [dragState, setDragState] = useState(null);
  const [trimByScene, setTrimByScene] = useState({});
  const workspaceRef = useRef(null);
  /** FAL model lists from `data/media_models_catalog.json` via GET /v1/settings/fal-models; sync with POST /v1/fal/models/sync. */
  const [falImageModels, setFalImageModels] = useState([]);
  const [falVideoModels, setFalVideoModels] = useState([]);
  const falVideoByKind = useMemo(() => partitionFalVideoModels(falVideoModels), [falVideoModels]);
  const selectedFalVideoKind = useMemo(() => {
    const cur = String(appConfig.fal_video_model || "").trim();
    if (!cur) return null;
    const hit = falVideoModels.find((m) => m.endpoint_id === cur);
    if (hit) return falVideoEndpointKind(hit);
    return falVideoKindFromEndpointId(cur);
  }, [appConfig.fal_video_model, falVideoModels]);
  const [falCatalogNote, setFalCatalogNote] = useState("");
  /** From GET /v1/settings/gemini-tts-voices (or GEMINI_TTS_VOICE_FALLBACK). */
  const [geminiTtsVoices, setGeminiTtsVoices] = useState([]);
  /** From GET /v1/settings/elevenlabs-voices (account voices). */
  const [elevenlabsVoices, setElevenlabsVoices] = useState([]);
  const [elevenlabsVoicesNote, setElevenlabsVoicesNote] = useState("");
  const [chapterNarration, setChapterNarration] = useState(null);
  /** Latest ``GET /v1/scenes/:id/narration`` for the focused scene (drives preview vs chapter MP3). */
  const [sceneNarrationMeta, setSceneNarrationMeta] = useState(null);
  /** From `GET /v1/settings/style-presets` (labels + ids for narration / visual defaults). */
  const [stylePresets, setStylePresets] = useState({
    narration_presets: [],
    visual_presets: [],
    defaults: {},
  });
  /** GET /v1/settings/chatterbox-voice-ref */
  const [chatterboxVoiceRef, setChatterboxVoiceRef] = useState(null);
  const [chatterboxVoiceRefBusy, setChatterboxVoiceRefBusy] = useState(false);
  const [chatterboxVoiceRefErr, setChatterboxVoiceRefErr] = useState("");
  const [chatterboxRecording, setChatterboxRecording] = useState(false);
  const chatterboxMediaStreamRef = useRef(null);
  const chatterboxMediaRecRef = useRef(null);
  const chatterboxMediaChunksRef = useRef([]);
  /** When true, ``MediaRecorder`` ``onstop`` discards the clip (e.g. user left the settings tab). */
  const chatterboxDiscardRecordingRef = useRef(false);
  /** `manual` | `auto` | `unattended` — unattended = full pipeline hands-off (full_video + relaxed research gate). */
  const [pipelineMode, setPipelineMode] = useState(() => {
    try {
      const v = localStorage.getItem("director_pipeline_mode");
      if (v === "auto") return "auto";
      if (v === "unattended") return "unattended";
      return "manual";
    } catch {
      return "manual";
    }
  });
  /** When true, storyboard sync / reconcile / auto timeline add one clip per approved export-ready visual per scene. */
  const [useAllApprovedSceneMedia, setUseAllApprovedSceneMedia] = useState(false);
  /** Agent `through` when in auto mode: critics only vs full render tail. */
  const [autoThrough, setAutoThrough] = useState(() => {
    try {
      const v = localStorage.getItem("director_auto_through");
      return v === "critique" || v === "full_video" ? v : "full_video";
    } catch {
      return "full_video";
    }
  });

  useEffect(() => {
    const ent = accountProfile?.entitlements;
    if (!ent) return;
    if (ent.full_through_automation_enabled === false && autoThrough === "full_video") {
      setAutoThrough("critique");
    }
    if (ent.hands_off_unattended_enabled === false && pipelineMode === "unattended") {
      setPipelineMode("manual");
    }
  }, [accountProfile, autoThrough, pipelineMode]);

  /** When true, Automate sends force_replan_scenes (agent overwrites existing scene plans). */
  const [forceReplanScenesOnContinue, setForceReplanScenesOnContinue] = useState(() => {
    try {
      return localStorage.getItem("director_force_replan_scenes_continue") === "true";
    } catch {
      return false;
    }
  });
  const [restartAutomationOpen, setRestartAutomationOpen] = useState(false);
  const [restartAutomationForce, setRestartAutomationForce] = useState(() =>
    Object.fromEntries(RESTART_AUTOMATION_STEPS.map((s) => [s.key, true])),
  );
  const [restartAutomationThrough, setRestartAutomationThrough] = useState("full_video");
  /** When false, worker skips web research if a dossier already exists (Restart automation modal). */
  const [restartRerunWebResearch, setRestartRerunWebResearch] = useState(false);
  const [pipelineStatus, setPipelineStatus] = useState(null);
  /** After first session restore attempt; avoids clobbering localStorage before hydrate. */
  const [uiSessionReady, setUiSessionReady] = useState(false);
  const sessionRestoreStartedRef = useRef(false);
  const openProjectRef = useRef(async () => {});
  const falCatalogLastLoadAtRef = useRef(0);
  const batchImagesCancelRef = useRef(false);
  /** Bumps on each scene narration meta fetch so slow responses cannot overwrite a newer selection. */
  const sceneNarrationMetaFetchGenRef = useRef(0);
  /** `{ total, done, label }` while a chapter batch image run is active. */
  const [batchImagesProgress, setBatchImagesProgress] = useState(null);
  /** Optional 1-based story-order bounds for "All images (chapter)"; empty = full chapter. */
  const [batchImageRangeFrom, setBatchImageRangeFrom] = useState("");
  const [batchImageRangeTo, setBatchImageRangeTo] = useState("");
  /** When narration has ``[bracket]`` hints, optionally run the text API to merge them into one still prompt (image jobs only). */
  const [refineBracketImageWithLlm, setRefineBracketImageWithLlm] = useState(false);

  useEffect(() => {
    setBatchImageRangeFrom("");
    setBatchImageRangeTo("");
  }, [chapterId]);

  const phase5ReadinessFetchOpts = useMemo(
    () => buildPhase5ReadinessFetchOpts(pipelineMode, timelineVersionId, "rough_cut"),
    [pipelineMode, timelineVersionId],
  );

  /** Scene rows / timeline clips to highlight for export blockers (includes timeline asset home scenes). */
  const exportAttentionSceneIdSet = useMemo(() => {
    if (pipelineMode === "unattended") return new Set();
    const issues = phase5Ready?.issues || [];
    const wants = issues.some((i) =>
      [
        "missing_approved_scene_image",
        "missing_succeeded_scene_image",
        "timeline_asset_not_approved",
        "timeline_clip_not_visual_asset",
        "timeline_asset_rejected_or_failed",
        "timeline_asset_not_succeeded",
        "timeline_asset_file_missing",
      ].includes(i.code),
    );
    if (!wants) return new Set();
    const ids = new Set();
    for (const id of phase5Ready?.export_attention_scene_ids || []) ids.add(String(id));
    for (const row of phase5Ready?.export_attention_timeline_assets || []) {
      if (row?.scene_id) ids.add(String(row.scene_id));
    }
    return ids;
  }, [pipelineMode, phase5Ready]);

  /** Scene asset thumbnails tied to a timeline export problem (approve / succeeded / file on disk). */
  const exportAttentionAssetIdSet = useMemo(() => {
    if (pipelineMode === "unattended") return new Set();
    const issues = phase5Ready?.issues || [];
    const wants = issues.some((i) =>
      [
        "timeline_asset_not_approved",
        "timeline_asset_not_in_project",
        "timeline_clip_not_visual_asset",
        "timeline_asset_rejected_or_failed",
        "timeline_asset_not_succeeded",
        "timeline_asset_file_missing",
      ].includes(i.code),
    );
    if (!wants) return new Set();
    const s = new Set();
    for (const row of phase5Ready?.export_attention_timeline_assets || []) {
      if (row?.asset_id) s.add(String(row.asset_id));
    }
    return s;
  }, [pipelineMode, phase5Ready]);

  const idem = useCallback(() => crypto.randomUUID(), []);

  const runAdapterSmokeTest = useCallback(
    async (provider, label) => {
      if (adapterSmokePollActive) {
        showToast("A connection test is already running.", { type: "info" });
        return;
      }
      adapterSmokeLabelRef.current = label || provider;
      try {
        const b = await apiPostIdempotent(api, "/v1/jobs", { type: "adapter_smoke", provider }, idem);
        const jid = b.job?.id;
        if (!jid) {
          showToast("Server did not return a job id.", { type: "error" });
          return;
        }
        setAdapterSmokeJobId(String(jid));
        setAdapterSmokePollActive(true);
      } catch (e) {
        showToast(formatUserFacingError(e), { type: "error", durationMs: 8000 });
      }
    },
    [adapterSmokePollActive, idem, showToast],
  );

  const runTelegramConnectionTest = useCallback(async () => {
    if (telegramTestLoading) return;
    setTelegramTestLoading(true);
    try {
      const r = await api("/v1/settings/telegram/test", {
        method: "POST",
        body: JSON.stringify({ send_test_message: true }),
      });
      const body = await parseJson(r);
      if (!r.ok) {
        showToast(apiErrorMessage(body) || "Telegram test failed", { type: "error", durationMs: 8000 });
        return;
      }
      const d = body.data || {};
      const bits = [`@${d.bot_username || "bot"}`];
      if (d.test_message_sent) bits.push("test message sent");
      else if (!d.chat_id_configured) bits.push("add chat id to receive a test message");
      if (d.webhook_action_required || d.webhook_registered_with_telegram === false) {
        bits.push("run setWebhook (curl below) — Telegram is not posting to Directely yet");
      }
      showToast(`Telegram: ${bits.join(" · ")}`, {
        type: d.webhook_action_required ? "warning" : "success",
        durationMs: d.webhook_action_required ? 12000 : 7000,
      });
    } catch (e) {
      showToast(formatUserFacingError(e), { type: "error", durationMs: 8000 });
    } finally {
      setTelegramTestLoading(false);
    }
  }, [telegramTestLoading, showToast]);

  const refreshPhase5Readiness = useCallback(
    async ({ pid = projectId, reportError = false, timelineVersionIdHint } = {}) => {
      if (!pid) return;
      const tvSource =
        timelineVersionIdHint !== undefined && timelineVersionIdHint !== null
          ? timelineVersionIdHint
          : timelineVersionId;
      const opts = buildPhase5ReadinessFetchOpts(pipelineMode, tvSource, "rough_cut");
      const { ok, body, data } = await fetchProjectPhase5Readiness(api, pid, opts);
      if (ok) setPhase5Ready(data);
      else if (reportError) setError(apiErrorMessage(body));
    },
    [projectId, pipelineMode, timelineVersionId],
  );

  const loadActiveProjectJobs = useCallback(async () => {
    if (!gatedProjectId) {
      setActiveProjectJobs([]);
      return;
    }
    try {
      let r = await api(`/v1/projects/${encodeURIComponent(gatedProjectId)}/jobs/active`);
      let body = await parseJson(r);
      // Older APIs without this route, or some proxies, return 404 "Not Found"; list endpoint still works.
      if (r.status === 404) {
        r = await api(
          `/v1/jobs?project_id=${encodeURIComponent(gatedProjectId)}&status=queued,running&limit=80`,
        );
        body = await parseJson(r);
      }
      if (!r.ok) {
        setActiveJobsLoadErr(apiErrorMessage(body));
        return;
      }
      setActiveJobsLoadErr("");
      setActiveProjectJobs(body.data?.jobs || []);
    } catch (e) {
      setActiveJobsLoadErr(formatUserFacingError(e));
    }
  }, [gatedProjectId]);
  loadActiveProjectJobsRef.current = loadActiveProjectJobs;

  const saveUseAllApprovedSceneMedia = useCallback(
    async (nextBool) => {
      if (!projectId) return;
      setBusy(true);
      setError("");
      try {
        const r = await api(`/v1/projects/${encodeURIComponent(projectId)}`, {
          method: "PATCH",
          body: JSON.stringify({ use_all_approved_scene_media: nextBool }),
        });
        const b = await parseJson(r);
        if (!r.ok) throw new Error(apiErrorMessage(b));
        setUseAllApprovedSceneMedia(Boolean(b.data?.use_all_approved_scene_media));
        setMessage(
          nextBool
            ? "Use all approved scene media is on — Reconcile / export auto-heal use every approved still or video per scene (gallery order)."
            : "Use all approved scene media is off — one primary clip per scene when syncing the timeline.",
        );
      } catch (e) {
        setError(formatUserFacingError(e));
      } finally {
        setBusy(false);
      }
    },
    [projectId],
  );

  const cancelBackgroundJob = useCallback(
    async (jobId) => {
      if (!jobId) return;
      setError("");
      try {
        const r = await api(`/v1/jobs/${encodeURIComponent(jobId)}/cancel`, { method: "POST" });
        const b = await parseJson(r);
        if (!r.ok) throw new Error(apiErrorMessage(b));
        setMessage(`Job cancelled (${String(jobId).slice(0, 8)}…).`);
        void loadActiveProjectJobs();
      } catch (e) {
        setError(formatUserFacingError(e));
      }
    },
    [loadActiveProjectJobs],
  );

  const clearTaskBacklog = useCallback(async () => {
    setError("");
    try {
      const r = await api("/v1/jobs/clear-backlog", { method: "POST" });
      const b = await parseJson(r);
      if (!r.ok) throw new Error(apiErrorMessage(b));
      const d = b.data || {};
      setMessage(
        `Backlog cleared: ${d.cancelled_jobs ?? 0} queued job(s), ${d.cancelled_agent_runs ?? 0} queued agent run(s); ` +
          `${d.purged_broker_messages ?? 0} broker message(s) dropped.`,
      );
      void loadActiveProjectJobs();
    } catch (e) {
      setError(formatUserFacingError(e));
    }
  }, [loadActiveProjectJobs]);

  useEffect(() => {
    if (!gatedProjectId) {
      setActiveProjectJobs([]);
      return undefined;
    }
    // Always do one immediate load so the list is populated on project open (any Studio page —
    // top banner + pipeline progress need jobs even when Settings / Chat is active).
    void loadActiveProjectJobs();
    // While SSE is live it pushes jobs_update events — polling would be redundant.
    // Keep a long-interval fallback (30 s) for environments where SSE is blocked
    // (e.g. nginx without X-Accel-Buffering, corporate proxies that buffer SSE).
    const bgMs = sseConnected
      ? 30_000
      : Math.min(10_000, Math.max(1500, jobPollIntervalMs * 2));
    const id = window.setInterval(() => void loadActiveProjectJobs(), bgMs);
    return () => window.clearInterval(id);
  }, [gatedProjectId, loadActiveProjectJobs, jobPollIntervalMs, sseConnected]);

  const queueMediaJob = useCallback(
    async (path, body, successMessage) => {
      setBusy(true);
      setError("");
      try {
        const b = await apiPostIdempotent(api, path, body, idem);
        const jid = b.job?.id;
        if (jid) {
          setMediaJobId(jid);
          setMediaPoll(true);
        }
        if (successMessage) setMessage(successMessage);
        void loadActiveProjectJobs();
      } catch (e) {
        setError(formatUserFacingError(e));
      } finally {
        setBusy(false);
      }
    },
    [idem, loadActiveProjectJobs],
  );

  const loadMusicBeds = useCallback(async () => {
    if (!gatedProjectId) {
      setMusicBeds([]);
      return;
    }
    try {
      const r = await api(`/v1/projects/${encodeURIComponent(gatedProjectId)}/music-beds`);
      const b = await parseJson(r);
      if (r.ok) setMusicBeds(Array.isArray(b.data) ? b.data : []);
    } catch {
      setMusicBeds([]);
    }
  }, [gatedProjectId]);

  const loadTimelineMixFields = useCallback(async () => {
    const tid = String(timelineVersionId || "").trim();
    if (!tid) return;
    try {
      const r = await api(`/v1/timeline-versions/${encodeURIComponent(tid)}`);
      const b = await parseJson(r);
      if (!r.ok) return;
      const tj = b.data?.timeline_json;
      if (!tj || typeof tj !== "object") return;
      setMixMusicVol(
        typeof tj.mix_music_volume === "number" ? tj.mix_music_volume : 0.28,
      );
      setMixNarrVol(
        typeof tj.mix_narration_volume === "number" ? tj.mix_narration_volume : 1,
      );
      setNarrMixMode("scene_timeline");
      setMusicBedPick(tj.music_bed_id ? String(tj.music_bed_id) : "");
      setClipCrossfadeSec(
        typeof tj.clip_crossfade_sec === "number" && Number.isFinite(tj.clip_crossfade_sec)
          ? Math.max(0, Math.min(2, tj.clip_crossfade_sec))
          : 0,
      );
    } catch {
      /* ignore */
    }
  }, [timelineVersionId]);

  /** Persists music bed + mix fields from current UI state to ``timeline_json`` (no busy / toast). */
  const patchTimelineMixToServer = useCallback(
    async (opts = {}) => {
      const tid = String(timelineVersionId || "").trim();
      if (!projectId || !tid) {
        return { ok: false, error: "Set a timeline version ID first." };
      }
      const bedId =
        opts.musicBedIdOverride !== undefined
          ? opts.musicBedIdOverride
          : musicBedPick.trim()
            ? musicBedPick.trim()
            : null;
      try {
        const gr = await api(`/v1/timeline-versions/${encodeURIComponent(tid)}`);
        const gb = await parseJson(gr);
        if (!gr.ok) throw new Error(apiErrorMessage(gb));
        const prev = gb.data?.timeline_json;
        if (!prev || typeof prev !== "object") throw new Error("timeline_json missing");
        const next = {
          ...prev,
          mix_music_volume: Math.max(0, Math.min(1, Number(mixMusicVol) || 0)),
          mix_narration_volume: Math.max(0, Math.min(4, Number(mixNarrVol) || 1)),
          final_cut_narration_mode: narrMixMode,
          music_bed_id: bedId && String(bedId).trim() ? String(bedId).trim() : null,
          clip_crossfade_sec: Math.max(0, Math.min(2, Number(clipCrossfadeSec) || 0)),
        };
        const pr = await api(`/v1/timeline-versions/${encodeURIComponent(tid)}`, {
          method: "PATCH",
          body: JSON.stringify({ timeline_json: next }),
        });
        const pb = await parseJson(pr);
        if (!pr.ok) throw new Error(apiErrorMessage(pb));
        return { ok: true };
      } catch (e) {
        return { ok: false, error: formatUserFacingError(e) };
      }
    },
    [projectId, timelineVersionId, mixMusicVol, mixNarrVol, narrMixMode, musicBedPick, clipCrossfadeSec],
  );

  /** Same sequence as ``scripts/run_rough_cut.py`` + ``run_final_cut.py``: rough-cut → wait → save mix → final-cut → wait. */
  const queueRoughThenFinalCompile = useCallback(async () => {
    if (!projectId || !timelineVersionId) return;
    const tv = sanitizeStudioUuid(timelineVersionId);
    if (!tv || !PHASE5_TIMELINE_UUID_RE.test(tv)) {
      setError("Enter a valid timeline version UUID in the field above.");
      return;
    }
    const bodyBase = {
      timeline_version_id: tv,
      allow_unapproved_media: pipelineMode === "unattended",
    };
    const finalBody = {
      ...bodyBase,
      burn_subtitles_into_video: burnSubtitlesOnFinalCut,
    };
    const roughPath = `/v1/projects/${projectId}/rough-cut`;
    const finalPath = `/v1/projects/${projectId}/final-cut`;
    const pollOpts = {
      intervalMs: jobPollIntervalMs,
      timeoutMs: 120 * 60 * 1000,
    };
    setBusy(true);
    setError("");
    try {
      setMessage("Rough cut running (step 1 of 2)…");
      const rb = await apiPostIdempotent(api, roughPath, bodyBase, idem);
      const rid = rb.job?.id;
      if (!rid) throw new Error("Rough cut did not return a job id.");
      setMediaJobId(rid);
      setMediaPoll(true);
      const r1 = await pollJobUntilTerminal(api, rid, pollOpts);
      void loadActiveProjectJobs();
      if (!r1.ok) {
        throw new Error(r1.job?.error_message || "Rough cut failed.");
      }
      const sync = await patchTimelineMixToServer();
      if (!sync.ok) {
        throw new Error(sync.error ? humanizeErrorText(sync.error) : "Could not save mix to timeline before final cut.");
      }
      setMessage("Final cut running (step 2 of 2)…");
      const fb = await apiPostIdempotent(api, finalPath, finalBody, idem);
      const fid = fb.job?.id;
      if (!fid) throw new Error("Final cut did not return a job id.");
      setMediaJobId(fid);
      const r2 = await pollJobUntilTerminal(api, fid, pollOpts);
      void loadActiveProjectJobs();
      if (!r2.ok) {
        throw new Error(r2.job?.error_message || "Final cut failed.");
      }
      setMediaPoll(false);
      setMessage("Rough cut and final cut finished (full compile).");
    } catch (e) {
      setError(formatUserFacingError(e));
    } finally {
      setBusy(false);
    }
  }, [
    projectId,
    timelineVersionId,
    pipelineMode,
    idem,
    jobPollIntervalMs,
    patchTimelineMixToServer,
    loadActiveProjectJobs,
    burnSubtitlesOnFinalCut,
  ]);

  const saveTimelineMixToServer = useCallback(async () => {
    if (!projectId || !String(timelineVersionId || "").trim()) {
      setError("Set a timeline version ID first.");
      return;
    }
    setBusy(true);
    setError("");
    try {
      const r = await patchTimelineMixToServer();
      if (!r.ok) throw new Error(r.error);
      setMessage("Timeline mix and transition settings saved.");
    } catch (e) {
      setError(formatUserFacingError(e));
    } finally {
      setBusy(false);
    }
  }, [projectId, timelineVersionId, patchTimelineMixToServer]);

  const uploadMusicBedFile = useCallback(async () => {
    if (!projectId) return;
    const inp = musicFileInputRef.current;
    const f = inp?.files?.[0];
    if (!f) {
      setError("Choose an audio file first.");
      return;
    }
    const lic = musicUploadLicense.trim();
    if (lic.length < 2) {
      setError("Enter a license / source note for the music upload.");
      return;
    }
    setBusy(true);
    setError("");
    try {
      const fd = new FormData();
      fd.append("file", f);
      fd.append("title", f.name || "Uploaded music");
      fd.append("license_or_source_ref", lic);
      const r = await apiForm(`/v1/projects/${encodeURIComponent(projectId)}/music-beds/upload`, {
        method: "POST",
        body: fd,
      });
      const b = await parseJson(r);
      if (!r.ok) throw new Error(apiErrorMessage(b));
      const row = b.data;
      if (row?.id) setMusicBedPick(String(row.id));
      if (inp) inp.value = "";
      void loadMusicBeds();
      const tid = String(timelineVersionId || "").trim();
      if (tid && row?.id) {
        const sync = await patchTimelineMixToServer({ musicBedIdOverride: String(row.id) });
        if (sync.ok) {
          setMessage("Music uploaded and mix saved to timeline.");
        } else {
          setMessage("Music uploaded. Save mix to timeline failed — click Save mix.");
          setError(sync.error ? humanizeErrorText(sync.error) : "");
        }
      } else if (!tid) {
        setMessage("Music uploaded. Paste timeline version ID, then Save mix to timeline before final cut.");
      } else {
        setMessage("Music uploaded.");
      }
    } catch (e) {
      setError(formatUserFacingError(e));
    } finally {
      setBusy(false);
    }
  }, [projectId, musicUploadLicense, loadMusicBeds, timelineVersionId, patchTimelineMixToServer]);

  useEffect(() => {
    void loadMusicBeds();
  }, [loadMusicBeds]);

  useEffect(() => {
    void loadTimelineMixFields();
  }, [loadTimelineMixFields]);

  const revertSceneNarrationDraft = useCallback(() => {
    const sid = expandedScene || scenes[0]?.id || "";
    if (!sid) {
      setSceneNarrationDraft("");
      setSceneNarrationDirty(false);
      return;
    }
    const sc = scenes.find((s) => String(s.id) === String(sid));
    setSceneNarrationDraft(sc?.narration_text ?? "");
    setSceneNarrationDirty(false);
  }, [expandedScene, scenes]);

  const saveSceneNarrationDraft = useCallback(async () => {
    const sid = expandedScene || scenes[0]?.id || "";
    if (!sid || !sceneNarrationDirty) return;
    setSceneNarrationSaving(true);
    setError("");
    try {
      const trimmed = sceneNarrationDraft.trim();
      const r = await api(`/v1/scenes/${encodeURIComponent(sid)}`, {
        method: "PATCH",
        body: JSON.stringify({ narration_text: trimmed ? trimmed : null }),
      });
      const b = await parseJson(r);
      if (!r.ok) throw new Error(apiErrorMessage(b));
      const row = b.data;
      setScenes((prev) =>
        prev.map((s) => {
          if (String(s.id) !== String(sid)) return s;
          return { ...s, ...row, asset_count: row.asset_count ?? s.asset_count };
        }),
      );
      setSceneNarrationDirty(false);
      setSceneNarrationDraft(trimmed ? trimmed : "");
      setMessage("Scene narration saved.");
    } catch (e) {
      setError(formatUserFacingError(e));
    } finally {
      setSceneNarrationSaving(false);
    }
  }, [expandedScene, scenes, sceneNarrationDraft, sceneNarrationDirty]);

  const loadChapters = useCallback(async (pid) => {
    if (!pid) return;
    const r = await api(`/v1/projects/${pid}/chapters`);
    const body = await parseJson(r);
    if (r.ok) setChapters(body.data?.chapters || []);
  }, []);

  const loadResearchChaptersEditor = useCallback(async (pid) => {
    if (!pid) return;
    setResearchPageBusy(true);
    setResearchPageErr("");
    try {
      const [rr, cr] = await Promise.all([api(`/v1/projects/${pid}/research`), api(`/v1/projects/${pid}/chapters`)]);
      const rb = await parseJson(rr);
      const cb = await parseJson(cr);
      if (!rr.ok) throw new Error(apiErrorMessage(rb) || `Research HTTP ${rr.status}`);
      if (!cr.ok) throw new Error(apiErrorMessage(cb) || `Chapters HTTP ${cr.status}`);
      const data = rb.data || {};
      const dossier = data.dossier;
      const body = dossier?.body;
      setResearchJsonDraft(
        body !== undefined && body !== null ? JSON.stringify(body, null, 2) : "{}",
      );
      setResearchMeta({
        script_gate_open: Boolean(data.script_gate_open),
        dossier,
        sourceCount: Array.isArray(data.sources) ? data.sources.length : 0,
        claimCount: Array.isArray(data.claims) ? data.claims.length : 0,
      });
      const list = cb.data?.chapters || [];
      setChapters(list);
      const drafts = {};
      for (const ch of list) {
        drafts[ch.id] = {
          title: ch.title ?? "",
          summary: ch.summary ?? "",
          target_duration_sec: ch.target_duration_sec != null ? String(ch.target_duration_sec) : "",
          script_text: ch.script_text ?? "",
        };
      }
      setChapterScriptsDraft(drafts);
    } catch (e) {
      setResearchPageErr(formatUserFacingError(e));
      setResearchJsonDraft("");
      setResearchMeta(null);
      setChapterScriptsDraft({});
    } finally {
      setResearchPageBusy(false);
    }
  }, []);

  const loadProjectCharacters = useCallback(async (pid) => {
    if (!pid) {
      setProjectCharacters([]);
      return;
    }
    const r = await api(`/v1/projects/${pid}/characters`);
    const body = await parseJson(r);
    if (r.ok) setProjectCharacters(body.data?.characters || []);
  }, []);

  const loadProjects = useCallback(async (opts) => {
    const silent = Boolean(opts && typeof opts === "object" && opts.silent);
    try {
      const r = await api("/v1/projects?limit=100");
      const body = await parseJson(r);
      if (r.ok) {
        const next = body.data?.projects || [];
        const snap = projectsPollSnapshotFromRows(next);
        if (silent && snap === lastProjectsPollSnapshotRef.current) {
          return;
        }
        lastProjectsPollSnapshotRef.current = snap;
        setProjects(next);
        return;
      }
      if (!silent) {
        setError(apiErrorMessage(body) || `Could not load projects (HTTP ${r.status}).`);
        lastProjectsPollSnapshotRef.current = projectsPollSnapshotFromRows([]);
        setProjects([]);
      }
    } catch (e) {
      if (!silent) {
        const net =
          e instanceof TypeError || String(e).toLowerCase().includes("fetch") || String(e).includes("NetworkError");
        const hint = String(e?.message || e || "").trim();
        setError(
          net
            ? [
                "Could not reach the API to load projects (network error before any HTTP response).",
                hint ? `Details: ${hint}` : null,
                "Check: (1) API is running on the host you expect. (2) If you set VITE_API_BASE_URL=http://127.0.0.1:8000 and open the Studio from another device or https://, the browser cannot reach that URL—leave VITE_API_BASE_URL unset and use the Vite/nginx same-origin /v1 proxy, or point it at a reachable API and set CORS_EXTRA_ORIGINS.",
                "Electron: wait until Docker + API finish starting.",
              ]
                .filter(Boolean)
                .join(" ")
            : formatUserFacingError(e),
        );
        lastProjectsPollSnapshotRef.current = projectsPollSnapshotFromRows([]);
        setProjects([]);
      }
    }
  }, []);

  const [scenesLoading, setScenesLoading] = useState(false);

  const loadScenes = useCallback(async (cid) => {
    if (!cid) return;
    setScenesLoading(true);
    try {
      const r = await api(`/v1/chapters/${cid}/scenes`);
      const body = await parseJson(r);
      if (r.ok) setScenes(body.data?.scenes || []);
    } finally {
      setScenesLoading(false);
    }
  }, []);

  const loadPhase3Summary = useCallback(async (cid) => {
    if (!cid) {
      setPhase3Summary(null);
      return;
    }
    const r = await api(`/v1/chapters/${cid}/phase3-summary`);
    const body = await parseJson(r);
    if (r.ok) setPhase3Summary(body.data ?? null);
  }, []);

  const loadSceneAssets = useCallback(async (sid) => {
    const r = await api(`/v1/scenes/${sid}/assets`);
    const body = await parseJson(r);
    if (r.ok) {
      setSceneAssets((prev) => ({ ...prev, [sid]: body.data?.assets || [] }));
    }
  }, []);

  /** POST /v1/assets/{id}/approve — same as CLI/curl; ``quiet`` skips banner (bulk actions). */
  const approveAsset = useCallback(
    async (assetId, opts = {}) => {
      const quiet = Boolean(opts.quiet);
      const id = sanitizeStudioUuid(assetId);
      if (!id) {
        setError("Invalid asset id.");
        return false;
      }
      if (!quiet) setError("");
      try {
        const r = await api(`/v1/assets/${encodeURIComponent(id)}/approve`, {
          method: "POST",
          body: JSON.stringify({}),
        });
        const body = await parseJson(r);
        if (!r.ok) {
          setError(apiErrorMessage(body) || "approve failed");
          return false;
        }
        const sid = sceneIdForAssetGalleryRefresh();
        if (sid) void loadSceneAssets(sid);
        if (chapterId) void loadPhase3Summary(chapterId);
        if (projectId) void refreshPhase5Readiness({ reportError: false });
        if (!quiet) setMessage("Approval saved.");
        return true;
      } catch (e) {
        setError(formatUserFacingError(e));
        return false;
      }
    },
    [
      sceneIdForAssetGalleryRefresh,
      loadSceneAssets,
      chapterId,
      loadPhase3Summary,
      projectId,
      refreshPhase5Readiness,
    ],
  );

  const rejectAsset = useCallback(
    async (assetId, opts = {}) => {
      const quiet = Boolean(opts.quiet);
      const id = sanitizeStudioUuid(assetId);
      if (!id) {
        setError("Invalid asset id.");
        return false;
      }
      if (!quiet) setError("");
      try {
        const r = await api(`/v1/assets/${encodeURIComponent(id)}/reject`, {
          method: "POST",
          body: JSON.stringify({ reason: "Rejected from studio UI" }),
        });
        const body = await parseJson(r);
        if (!r.ok) {
          setError(apiErrorMessage(body) || "reject failed");
          return false;
        }
        const sid = sceneIdForAssetGalleryRefresh();
        if (sid) void loadSceneAssets(sid);
        if (chapterId) void loadPhase3Summary(chapterId);
        if (projectId) void refreshPhase5Readiness({ reportError: false });
        if (!quiet) setMessage("Asset rejected.");
        return true;
      } catch (e) {
        setError(formatUserFacingError(e));
        return false;
      }
    },
    [
      sceneIdForAssetGalleryRefresh,
      loadSceneAssets,
      chapterId,
      loadPhase3Summary,
      projectId,
      refreshPhase5Readiness,
    ],
  );

  const dismissPhase5ExportGateModal = useCallback(() => {
    setPhase5ExportGateModal(null);
  }, []);

  const reviewScenesForExportGate = useCallback(async () => {
    setPhase5ExportGateModal(null);
    setActivePage("editor");
    if (!projectId) return;
    const { ok, data } = await fetchProjectPhase5Readiness(api, projectId, phase5ReadinessFetchOpts);
    if (ok && data) {
      setPhase5Ready(data);
      const fromTimeline = data.export_attention_timeline_assets?.find((r) => r?.scene_id)?.scene_id;
      const first = data.export_attention_scene_ids?.[0] || fromTimeline;
      if (first) {
        const sid = String(first);
        setExpandedScene(sid);
        setPinnedPreviewAssetId(null);
        await loadSceneAssets(sid);
      }
      setMessage(
        first
          ? "Open the highlighted scene rows (or timeline clips) to fix the listed media, then re-run export."
          : "Use the Scenes list, timeline checklist, and pipeline panel to fix media, then check readiness and re-run export.",
      );
    }
  }, [projectId, phase5ReadinessFetchOpts, loadSceneAssets]);

  const approveAllSucceededMediaForExport = useCallback(async () => {
    if (!projectId) return;
    setApproveAllMediaBusy(true);
    setError("");
    try {
      const r = await api(`/v1/projects/${encodeURIComponent(projectId)}/assets/approve-all-succeeded`, {
        method: "POST",
      });
      const b = await parseJson(r);
      if (!r.ok) throw new Error(apiErrorMessage(b));
      const n = Number(b.data?.approved_count ?? 0);
      setPhase5ExportGateModal(null);
      setMessage(
        n > 0
          ? `Approved ${n} image(s) and video(s). When the export checklist is clear, queue Rough cut or Final cut again.`
          : "No unapproved succeeded images or videos were left to approve. Fix remaining timeline or generation issues, then retry export.",
      );
      await refreshPhase5Readiness({});
      if (chapterId) {
        loadScenes(chapterId);
        loadPhase3Summary(chapterId);
      }
      const sid = sceneIdForAssetGalleryRefresh();
      if (sid) void loadSceneAssets(sid);
    } catch (e) {
      setError(formatUserFacingError(e));
    } finally {
      setApproveAllMediaBusy(false);
    }
  }, [
    projectId,
    refreshPhase5Readiness,
    chapterId,
    loadScenes,
    loadPhase3Summary,
    sceneIdForAssetGalleryRefresh,
    loadSceneAssets,
  ]);

  const loadCriticReport = useCallback(async (rid) => {
    if (!rid) return;
    const r = await api(`/v1/critic-reports/${rid}`);
    const body = await parseJson(r);
    if (r.ok) setCriticReport(body.data ?? null);
  }, []);

  const loadProjectCriticReports = useCallback(async (pid) => {
    if (!pid) {
      setProjectCriticReports([]);
      setCriticListError("");
      return;
    }
    setCriticListError("");
    try {
      const r = await api(`/v1/projects/${pid}/critic-reports?limit=40`);
      const body = await parseJson(r);
      if (!r.ok) {
        setCriticListError(
          apiErrorMessage(body) || `HTTP ${r.status} (is the API restarted with the critic-reports list route?)`,
        );
        setProjectCriticReports([]);
        return;
      }
      setProjectCriticReports(body.data?.reports || []);
    } catch (e) {
      setCriticListError(formatUserFacingError(e));
      setProjectCriticReports([]);
    }
  }, []);

  const loadAppSettings = useCallback(async () => {
    try {
      const r = await api("/v1/settings");
      const body = await parseJson(r);
      if (!r.ok) {
        setSettingsLoadError(
          apiErrorMessage(body) ||
            `Could not load workspace settings (server returned HTTP ${r.status}). Is the API running?`,
        );
        return;
      }
      setSettingsLoadError("");
      setAppConfig(body.data?.config || {});
      setPlatformCredentialKeysInherited(
        Array.isArray(body.data?.platform_credential_keys_inherited)
          ? body.data.platform_credential_keys_inherited
          : [],
      );
      setCredentialKeysPresent(
        body.data?.credential_keys_present && typeof body.data.credential_keys_present === "object"
          ? body.data.credential_keys_present
          : {},
      );
    } catch (e) {
      const net =
        e instanceof TypeError || String(e).toLowerCase().includes("fetch") || String(e).includes("NetworkError");
      setSettingsLoadError(
        net
          ? "Could not reach the API. Check that the server is running and the app is pointed at the right address."
          : formatUserFacingError(e),
      );
    }
  }, []);

  const loadLlmPrompts = useCallback(async () => {
    setLlmPromptsBusy(true);
    setLlmPromptsErr("");
    try {
      let r = await api("/v1/settings/prompts");
      if (r.status === 404) {
        r = await api("/v1/prompts");
      }
      const body = await parseJson(r);
      if (!r.ok) {
        let msg = apiErrorMessage(body) || `Could not load prompts (HTTP ${r.status}).`;
        if (r.status === 404) {
          msg =
            "Prompts API not found on the server. Stop the Directely API (close its PowerShell window from Launch, or run scripts\\stop-director.ps1), then run Launch.cmd again so the process loads the latest code.";
        }
        setLlmPromptsErr(msg);
        setLlmPrompts([]);
        return;
      }
      const list = body.data?.prompts || [];
      setLlmPrompts(list);
      const d = {};
      for (const row of list) {
        d[row.prompt_key] = row.effective_content ?? "";
      }
      setLlmPromptDrafts(d);
    } catch (e) {
      setLlmPromptsErr(formatUserFacingError(e));
      setLlmPrompts([]);
    } finally {
      setLlmPromptsBusy(false);
    }
  }, []);

  const saveLlmPrompt = useCallback(
    async (promptKey) => {
      const content = (llmPromptDrafts[promptKey] ?? "").trim();
      if (!content) {
        setLlmPromptsErr("Prompt text cannot be empty.");
        return;
      }
      setLlmPromptsBusy(true);
      setLlmPromptsErr("");
      try {
        let r = await api(`/v1/settings/prompts/${encodeURIComponent(promptKey)}`, {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ content }),
        });
        if (r.status === 404) {
          r = await api(`/v1/prompts/${encodeURIComponent(promptKey)}`, {
            method: "PUT",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ content }),
          });
        }
        const body = await parseJson(r);
        if (!r.ok) {
          setLlmPromptsErr(apiErrorMessage(body) || `Save failed (${r.status}).`);
          return;
        }
        const pr = body.data?.prompt;
        if (pr) {
          setLlmPrompts((prev) =>
            prev.map((x) => (x.prompt_key === pr.prompt_key ? { ...x, ...pr } : x)),
          );
          setLlmPromptDrafts((d) => ({ ...d, [pr.prompt_key]: pr.effective_content }));
        }
      } catch (e) {
        setLlmPromptsErr(formatUserFacingError(e));
      } finally {
        setLlmPromptsBusy(false);
      }
    },
    [llmPromptDrafts],
  );

  const resetLlmPrompt = useCallback(async (promptKey) => {
    setLlmPromptsBusy(true);
    setLlmPromptsErr("");
    try {
      let r = await api(`/v1/settings/prompts/${encodeURIComponent(promptKey)}/override`, {
        method: "DELETE",
      });
      if (r.status === 404) {
        r = await api(`/v1/prompts/${encodeURIComponent(promptKey)}/override`, {
          method: "DELETE",
        });
      }
      const body = await parseJson(r);
      if (!r.ok) {
        setLlmPromptsErr(apiErrorMessage(body) || `Reset failed (${r.status}).`);
        return;
      }
      const pr = body.data?.prompt;
      if (pr) {
        setLlmPrompts((prev) =>
          prev.map((x) => (x.prompt_key === pr.prompt_key ? { ...x, ...pr } : x)),
        );
        setLlmPromptDrafts((d) => ({ ...d, [pr.prompt_key]: pr.effective_content }));
      }
    } catch (e) {
      setLlmPromptsErr(formatUserFacingError(e));
    } finally {
      setLlmPromptsBusy(false);
    }
  }, []);

  const loadChatterboxVoiceRef = useCallback(async () => {
    setChatterboxVoiceRefErr("");
    try {
      const r = await api("/v1/settings/chatterbox-voice-ref");
      const body = await parseJson(r);
      if (!r.ok) {
        setChatterboxVoiceRefErr(apiErrorMessage(body));
        setChatterboxVoiceRef(null);
        return;
      }
      setChatterboxVoiceRef(body.data || null);
    } catch (e) {
      setChatterboxVoiceRefErr(formatUserFacingError(e));
      setChatterboxVoiceRef(null);
    }
  }, []);

  const uploadChatterboxFile = useCallback(
    async (file) => {
      if (!file) return;
      setChatterboxVoiceRefBusy(true);
      setChatterboxVoiceRefErr("");
      try {
        const fd = new FormData();
        fd.append("file", file, file.name || "upload");
        const r = await apiForm("/v1/settings/chatterbox-voice-ref", {
          method: "POST",
          body: fd,
        });
        const body = await parseJson(r);
        if (!r.ok) {
          setChatterboxVoiceRefErr(apiErrorMessage(body));
          return;
        }
        setChatterboxVoiceRef(body.data || null);
        await loadAppSettings();
      } catch (e) {
        setChatterboxVoiceRefErr(formatUserFacingError(e));
      } finally {
        setChatterboxVoiceRefBusy(false);
      }
    },
    [loadAppSettings],
  );

  const deleteChatterboxVoiceRef = useCallback(async () => {
    setChatterboxVoiceRefBusy(true);
    setChatterboxVoiceRefErr("");
    try {
      const r = await api("/v1/settings/chatterbox-voice-ref", { method: "DELETE" });
      const body = await parseJson(r);
      if (!r.ok) {
        setChatterboxVoiceRefErr(apiErrorMessage(body));
        return;
      }
      setChatterboxVoiceRef(body.data || null);
      await loadAppSettings();
    } catch (e) {
      setChatterboxVoiceRefErr(formatUserFacingError(e));
    } finally {
      setChatterboxVoiceRefBusy(false);
    }
  }, [loadAppSettings]);

  const stopChatterboxMedia = useCallback(() => {
    chatterboxDiscardRecordingRef.current = true;
    const rec = chatterboxMediaRecRef.current;
    if (rec && rec.state !== "inactive") {
      try {
        rec.stop();
      } catch {
        /* ignore */
      }
    } else {
      chatterboxDiscardRecordingRef.current = false;
    }
    const stream = chatterboxMediaStreamRef.current;
    if (stream) {
      stream.getTracks().forEach((t) => t.stop());
      chatterboxMediaStreamRef.current = null;
    }
    chatterboxMediaRecRef.current = null;
    chatterboxMediaChunksRef.current = [];
    setChatterboxRecording(false);
    chatterboxDiscardRecordingRef.current = false;
  }, []);

  const startChatterboxRecording = useCallback(async () => {
    setChatterboxVoiceRefErr("");
    chatterboxDiscardRecordingRef.current = false;
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      chatterboxMediaStreamRef.current = stream;
      chatterboxMediaChunksRef.current = [];
      const mime = MediaRecorder.isTypeSupported("audio/webm;codecs=opus")
        ? "audio/webm;codecs=opus"
        : MediaRecorder.isTypeSupported("audio/webm")
          ? "audio/webm"
          : "";
      const rec = new MediaRecorder(stream, mime ? { mimeType: mime } : undefined);
      chatterboxMediaRecRef.current = rec;
      rec.ondataavailable = (ev) => {
        if (ev.data.size) chatterboxMediaChunksRef.current.push(ev.data);
      };
      rec.onstop = () => {
        const streamIn = chatterboxMediaStreamRef.current;
        if (streamIn) {
          streamIn.getTracks().forEach((t) => t.stop());
          chatterboxMediaStreamRef.current = null;
        }
        chatterboxMediaRecRef.current = null;
        setChatterboxRecording(false);
        const discard = chatterboxDiscardRecordingRef.current;
        chatterboxDiscardRecordingRef.current = false;
        const chunks = [...chatterboxMediaChunksRef.current];
        chatterboxMediaChunksRef.current = [];
        if (discard || !chunks.length) return;
        const blob = new Blob(chunks, { type: rec.mimeType || "audio/webm" });
        void uploadChatterboxFile(
          new File([blob], "recording.webm", { type: blob.type || "audio/webm" }),
        );
      };
      rec.start();
      setChatterboxRecording(true);
    } catch (e) {
      setChatterboxVoiceRefErr(
        e instanceof Error ? e.message : String(e) || "Microphone access failed (permission denied?)",
      );
    }
  }, [uploadChatterboxFile]);

  const finishChatterboxRecording = useCallback(() => {
    const rec = chatterboxMediaRecRef.current;
    if (rec && rec.state !== "inactive") rec.stop();
  }, []);

  useEffect(() => {
    if (settingsTab !== "voice_ref") {
      stopChatterboxMedia();
      return undefined;
    }
    void loadChatterboxVoiceRef();
    return undefined;
  }, [settingsTab, loadChatterboxVoiceRef, stopChatterboxMedia]);

  const loadNarrationStylesLibrary = useCallback(async () => {
    setNarrationStylesLibBusy(true);
    setNarrationStylesLibErr("");
    try {
      const r = await api("/v1/narration-styles");
      const body = await parseJson(r);
      if (!r.ok) {
        setNarrationStylesLibErr(apiErrorMessage(body) || `Could not load narration styles (HTTP ${r.status}).`);
        setNarrationStylesLib([]);
        return;
      }
      setNarrationStylesLib(Array.isArray(body.data?.styles) ? body.data.styles : []);
    } catch (e) {
      setNarrationStylesLibErr(formatUserFacingError(e));
      setNarrationStylesLib([]);
    } finally {
      setNarrationStylesLibBusy(false);
    }
  }, []);

  useEffect(() => {
    if (activePage !== "settings" || settingsTab !== "generation") {
      return undefined;
    }
    void loadNarrationStylesLibrary();
    return undefined;
  }, [activePage, settingsTab, loadNarrationStylesLibrary]);

  const createOrUpdateNarrationStyle = useCallback(async () => {
    const title = narFormTitle.trim();
    const prompt_text = narFormPrompt.trim();
    if (!title || prompt_text.length < 10) {
      setNarrationStylesLibErr("Title and a prompt of at least 10 characters are required.");
      return;
    }
    setNarrationStylesLibBusy(true);
    setNarrationStylesLibErr("");
    try {
      if (narEditingRef && narEditingRef.startsWith("user:")) {
        const id = narEditingRef.slice(5);
        const r = await api(`/v1/narration-styles/${encodeURIComponent(id)}`, {
          method: "PATCH",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ title, prompt_text }),
        });
        const body = await parseJson(r);
        if (!r.ok) throw new Error(apiErrorMessage(body) || `HTTP ${r.status}`);
      } else {
        const r = await api("/v1/narration-styles", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ title, prompt_text }),
        });
        const body = await parseJson(r);
        if (!r.ok) throw new Error(apiErrorMessage(body) || `HTTP ${r.status}`);
      }
      setNarFormTitle("");
      setNarFormPrompt("");
      setNarEditingRef(null);
      await loadNarrationStylesLibrary();
    } catch (e) {
      setNarrationStylesLibErr(formatUserFacingError(e));
    } finally {
      setNarrationStylesLibBusy(false);
    }
  }, [narFormTitle, narFormPrompt, narEditingRef, loadNarrationStylesLibrary]);

  const deleteNarrationStyleByRef = useCallback(
    async (ref) => {
      if (!ref.startsWith("user:")) return;
      const id = ref.slice(5);
      if (
        !window.confirm(
          "Delete this narration style? Projects that reference it will fall back to workspace defaults.",
        )
      )
        return;
      setNarrationStylesLibBusy(true);
      setNarrationStylesLibErr("");
      try {
        const r = await api(`/v1/narration-styles/${encodeURIComponent(id)}`, { method: "DELETE" });
        const body = await parseJson(r);
        if (!r.ok) throw new Error(apiErrorMessage(body) || `HTTP ${r.status}`);
        setAppConfig((p) =>
          String(p.default_narration_style_ref || "").trim() === ref ? { ...p, default_narration_style_ref: null } : p,
        );
        await loadNarrationStylesLibrary();
      } catch (e) {
        setNarrationStylesLibErr(formatUserFacingError(e));
      } finally {
        setNarrationStylesLibBusy(false);
      }
    },
    [loadNarrationStylesLibrary],
  );

  const loadUsageSummary = useCallback(async (days) => {
    setUsageLoading(true);
    setUsageErr("");
    try {
      const d = Math.min(366, Math.max(1, Number(days) || 30));
      const r = await api(`/v1/settings/usage-summary?days=${d}`);
      const body = await parseJson(r);
      if (!r.ok) {
        const msg =
          body?.detail?.message ||
          body?.error?.message ||
          `Could not load usage (HTTP ${r.status}).`;
        setUsageErr(msg);
        setUsageSummary(null);
        return;
      }
      setUsageSummary(body.data || null);
    } catch (e) {
      setUsageErr(formatUserFacingError(e));
      setUsageSummary(null);
    } finally {
      setUsageLoading(false);
    }
  }, []);

  const loadStylePresets = useCallback(async () => {
    try {
      const r = await api("/v1/settings/style-presets");
      const body = await parseJson(r);
      if (r.ok && body.data) setStylePresets(body.data);
    } catch {
      /* keep empty; dropdowns use inline fallback ids */
    }
  }, []);

  const loadGeminiTtsVoices = useCallback(async () => {
    try {
      const r = await api("/v1/settings/gemini-tts-voices");
      const body = await parseJson(r);
      if (r.ok && Array.isArray(body.data?.voices)) setGeminiTtsVoices(body.data.voices);
      else setGeminiTtsVoices([]);
    } catch {
      setGeminiTtsVoices([]);
    }
  }, []);

  const loadElevenlabsVoices = useCallback(async () => {
    setElevenlabsVoicesNote("");
    try {
      const r = await api("/v1/settings/elevenlabs-voices");
      const body = await parseJson(r);
      if (r.ok && Array.isArray(body.data?.voices)) {
        setElevenlabsVoices(body.data.voices);
        if (body.data?.error === "no_api_key") {
          setElevenlabsVoicesNote("Save your ElevenLabs API key and workspace settings, then refresh voices.");
        } else if (body.data?.error && body.data.voices.length === 0) {
          setElevenlabsVoicesNote(
            `Could not load voices (${body.data.error}). Check the key or try again.`,
          );
        }
      } else {
        setElevenlabsVoices([]);
      }
    } catch {
      setElevenlabsVoices([]);
      setElevenlabsVoicesNote("Could not load ElevenLabs voices.");
    }
  }, []);

  const loadFalCatalog = useCallback(async (opts = {}) => {
    const force = Boolean(opts?.force);
    const sync = Boolean(opts?.sync);
    const now = Date.now();
    const since = now - Number(falCatalogLastLoadAtRef.current || 0);
    if (!force && !sync && falCatalogLastLoadAtRef.current && since < FAL_CATALOG_MIN_REFRESH_MS) {
      const waitSec = Math.ceil((FAL_CATALOG_MIN_REFRESH_MS - since) / 1000);
      setFalCatalogNote(`catalog reload limited to once per minute; try again in ~${waitSec}s`);
      return;
    }
    if (sync || force) falCatalogLastLoadAtRef.current = now;
    setFalCatalogNote("");
    try {
      let syncSummary = null;
      if (sync) {
        const rs = await api("/v1/fal/models/sync", { method: "POST" });
        const bs = await parseJson(rs);
        if (!rs.ok) {
          setFalCatalogNote(
            bs?.detail?.message || bs?.error?.message || `sync HTTP ${rs.status}`,
          );
          return;
        }
        syncSummary = bs?.data ?? null;
      }
      const fetchMedia = async (media) => {
        const cb = `_cb=${Date.now()}_${media}`;
        let r = await api(`/v1/settings/fal-models?media=${encodeURIComponent(media)}&${cb}`);
        if (r.status === 404) r = await api(`/v1/fal/models?media=${encodeURIComponent(media)}&${cb}`);
        return r;
      };
      const [ri, rv] = await Promise.all([fetchMedia("image"), fetchMedia("video")]);
      const bi = await parseJson(ri);
      const bv = await parseJson(rv);
      const errs = [];
      if (ri.ok && Array.isArray(bi.data?.models)) setFalImageModels(bi.data.models);
      else errs.push(`image catalog: ${bi?.detail?.message || bi?.error?.message || `HTTP ${ri.status}`}`);
      if (rv.ok && Array.isArray(bv.data?.models)) setFalVideoModels(bv.data.models);
      else errs.push(`video catalog: ${bv?.detail?.message || bv?.error?.message || `HTTP ${rv.status}`}`);
      const hints = [];
      if (bi?.data?.needs_sync) hints.push("image list empty — use Sync from fal API");
      if (bv?.data?.needs_sync) hints.push("video list empty — use Sync from fal API");
      if (bi?.data?.catalog_updated_at) hints.push(`catalog updated ${bi.data.catalog_updated_at}`);
      const hintMsg = hints.filter(Boolean).join(" · ");
      if (errs.length) setFalCatalogNote(errs.join(" · "));
      else if (sync && syncSummary?.updated_at) setFalCatalogNote(`Synced · ${syncSummary.updated_at}`);
      else if (hintMsg) setFalCatalogNote(hintMsg);
    } catch (e) {
      setFalCatalogNote(String(e));
    }
  }, []);

  const loadChapterNarration = useCallback(async (cid) => {
    if (!cid) {
      setChapterNarration(null);
      return;
    }
    try {
      const r = await api(`/v1/chapters/${cid}/narration`);
      const b = await parseJson(r);
      if (r.ok) setChapterNarration(b.data || { has_audio: false });
      else setChapterNarration({ has_audio: false });
    } catch {
      setChapterNarration({ has_audio: false });
    }
  }, []);

  const loadSceneNarrationMeta = useCallback(async (sid) => {
    if (!sid) {
      sceneNarrationMetaFetchGenRef.current += 1;
      setSceneNarrationMeta(null);
      return;
    }
    const s = String(sid);
    const gen = ++sceneNarrationMetaFetchGenRef.current;
    try {
      const r = await api(`/v1/scenes/${encodeURIComponent(s)}/narration`);
      const b = await parseJson(r);
      if (gen !== sceneNarrationMetaFetchGenRef.current) return;
      if (r.ok) setSceneNarrationMeta(b.data || { has_audio: false, scene_id: s });
      else setSceneNarrationMeta({ has_audio: false, scene_id: s });
    } catch {
      if (gen !== sceneNarrationMetaFetchGenRef.current) return;
      setSceneNarrationMeta({ has_audio: false, scene_id: s });
    }
  }, []);

  const loadPipelineStatus = useCallback(
    async (pid) => {
      if (!pid) {
        setPipelineStatus(null);
        return;
      }
      try {
        const r = await api(`/v1/projects/${pid}/pipeline-status`);
        const body = await parseJson(r);
        if (r.ok && body.data) {
          setPipelineStatus(body.data);
          const lid = body.data.latest_timeline_version_id;
          if (lid) {
            const prevLatest = lastPolledLatestTimelineRef.current;
            lastPolledLatestTimelineRef.current = lid;
            setTimelineVersionId((cur) => {
              const curTrim = cur && String(cur).trim() ? String(cur).trim() : "";
              if (!curTrim) return lid;
              if (prevLatest != null && lid !== prevLatest && curTrim === prevLatest) {
                return lid;
              }
              return curTrim;
            });
          }
          // Full GET phase5-readiness (includes export_attention_timeline_assets). Throttle: same handler runs on a 2.5s poll.
          const ps = String(pid);
          if (ps !== phase5BundledPidRef.current) {
            phase5BundledPidRef.current = ps;
            lastPhase5BundledRefreshRef.current = 0;
          }
          const now = Date.now();
          if (now - lastPhase5BundledRefreshRef.current >= 12_000) {
            lastPhase5BundledRefreshRef.current = now;
            await refreshPhase5Readiness({ pid, reportError: false, timelineVersionIdHint: lid || undefined });
          }
        }
      } catch {
        /* ignore */
      }
    },
    [refreshPhase5Readiness],
  );

  useEffect(() => {
    lastPolledLatestTimelineRef.current = null;
  }, [projectId]);

  const refreshRun = useCallback(async () => {
    if (!studioReady || !agentRunId) return;
    const r = await api(`/v1/agent-runs/${agentRunId}`);
    const body = await parseJson(r);
    if (!r.ok) return;
    const data = body.data;
    setRun(data);
    const runProjectId = data?.project_id != null ? String(data.project_id) : "";
    if (runProjectId && !String(projectId || "").trim()) {
      setProjectId(runProjectId);
    }
    const pid = (runProjectId || String(projectId || "").trim()) || "";
    if (pid) loadProjectCriticReports(pid);

    const st = data?.status;
    const runHot = st === "running" || st === "queued";

    /* While the worker is active, keep chapters/scenes/assets/narration in sync (no refresh needed). */
    if (runHot && pid) {
      loadChapters(pid);
      if (chapterId) {
        loadScenes(chapterId);
        loadPhase3Summary(chapterId);
        loadChapterNarration(chapterId);
      }
      const sid = expandedScene ? String(expandedScene) : "";
      if (sid) {
        loadSceneAssets(sid);
        void loadSceneNarrationMeta(sid);
      }
    }

    const terminal = ["succeeded", "cancelled", "failed", "blocked"].includes(st);
    if (terminal && pid) {
      loadPipelineStatus(pid);
      loadChapters(pid);
      if (chapterId) {
        loadScenes(chapterId);
        loadPhase3Summary(chapterId);
        loadChapterNarration(chapterId);
      }
      if (expandedScene) {
        const es = String(expandedScene);
        loadSceneAssets(es);
        void loadSceneNarrationMeta(es);
      }
    }
  }, [
    studioReady,
    agentRunId,
    projectId,
    chapterId,
    expandedScene,
    loadChapters,
    loadPipelineStatus,
    loadProjectCriticReports,
    loadScenes,
    loadPhase3Summary,
    loadChapterNarration,
    loadSceneAssets,
    loadSceneNarrationMeta,
  ]);

  const agentRunFailedToastKeyRef = useRef("");
  useEffect(() => {
    if (!studioReady || !agentRunId || !run || run.status !== "failed") return;
    const key = `${agentRunId}:${run.error_message || ""}`;
    if (agentRunFailedToastKeyRef.current === key) return;
    agentRunFailedToastKeyRef.current = key;
    const msg = summarizeAgentRunFailure(run.error_message || "");
    showToast(`Automation failed — ${msg}`, { type: "error", durationMs: 14000 });
  }, [studioReady, agentRunId, run?.status, run?.error_message, showToast]);

  useEffect(() => {
    if (!studioReady) return;
    void loadAppSettings();
    loadProjects().catch((e) => setError(formatUserFacingError(e)));
    loadFalCatalog();
    loadStylePresets();
    void loadGeminiTtsVoices();
    void loadElevenlabsVoices();
  }, [
    studioReady,
    loadAppSettings,
    loadProjects,
    loadFalCatalog,
    loadStylePresets,
    loadGeminiTtsVoices,
    loadElevenlabsVoices,
  ]);

  /** Live-updates project titles/status/phase in Project & story (and Chat rail list) without manual reload. */
  useEffect(() => {
    if (!studioReady) return undefined;
    const tick = () => {
      if (typeof document !== "undefined" && document.visibilityState !== "visible") return;
      void loadProjects({ silent: true });
    };
    const id = window.setInterval(tick, projectsListPollMs);
    const onVis = () => {
      if (document.visibilityState === "visible") void loadProjects({ silent: true });
    };
    document.addEventListener("visibilitychange", onVis);
    return () => {
      window.clearInterval(id);
      document.removeEventListener("visibilitychange", onVis);
    };
  }, [studioReady, loadProjects, projectsListPollMs]);

  useEffect(() => {
    if (activePage !== "usage") return;
    void loadUsageSummary(usageDays);
  }, [activePage, usageDays, loadUsageSummary]);

  useEffect(() => {
    if (activePage !== "prompts") return;
    void loadLlmPrompts();
  }, [activePage, loadLlmPrompts]);

  useEffect(() => {
    try {
      localStorage.setItem("director_pipeline_mode", pipelineMode);
    } catch {
      /* ignore */
    }
  }, [pipelineMode]);

  useEffect(() => {
    try {
      localStorage.setItem("director_auto_through", autoThrough);
    } catch {
      /* ignore */
    }
  }, [autoThrough]);

  useEffect(() => {
    try {
      localStorage.setItem(
        "director_force_replan_scenes_continue",
        forceReplanScenesOnContinue ? "true" : "false",
      );
    } catch {
      /* ignore */
    }
  }, [forceReplanScenesOnContinue]);

  useEffect(() => {
    if (!gatedProjectId) {
      setPipelineStatus(null);
      return undefined;
    }
    loadPipelineStatus(gatedProjectId);
    const tick = setInterval(() => loadPipelineStatus(gatedProjectId), 2500);
    return () => clearInterval(tick);
  }, [gatedProjectId, loadPipelineStatus]);

  useEffect(() => {
    if (!studioReady) return undefined;
    let cancelled = false;
    const check = async () => {
      try {
        const r = await api("/v1/celery/status");
        const b = await parseJson(r);
        if (cancelled) return;
        const d = b?.data || {};
        setCeleryStatus(d.status || "offline");
        setCeleryWorkers(d.workers || []);
        setCeleryStatusDetail(d.liveness === "inferred_busy" && d.note ? String(d.note) : "");
      } catch {
        if (!cancelled) {
          setCeleryStatus("offline");
          setCeleryStatusDetail("");
        }
      }
    };
    // One check after auth bootstrap (same as loadProjects) — avoids 401 before session + mirrored tenant.
    check();
    // While SSE is live it sends celery_status events every 30 s — fall back
    // to a 60 s HTTP poll as a safety net only (e.g. SSE blocked by a proxy).
    const intervalMs = sseConnected ? 60_000 : 15_000;
    const id = setInterval(check, intervalMs);
    return () => {
      cancelled = true;
      clearInterval(id);
    };
  }, [sseConnected, studioReady]);

  const restartCelery = useCallback(async () => {
    setCeleryRestarting(true);
    setCeleryStatus("restarting");
    setCeleryStatusDetail("");
    try {
      const r = await api("/v1/celery/restart", { method: "POST" });
      const b = await parseJson(r);
      if (!r.ok) throw new Error(apiErrorMessage(b));
      setMessage("Celery restart triggered — worker should be online in a few seconds.");
    } catch (err) {
      setError(`Celery restart failed: ${err.message || err}`);
    }
    setTimeout(async () => {
      for (let i = 0; i < 6; i++) {
        await new Promise((ok) => setTimeout(ok, 3000));
        try {
          const r = await api("/v1/celery/status");
          const b = await parseJson(r);
          const d = b?.data || {};
          setCeleryStatus(d.status || "offline");
          setCeleryWorkers(d.workers || []);
          setCeleryStatusDetail(d.liveness === "inferred_busy" && d.note ? String(d.note) : "");
          if (d.status === "online") break;
        } catch { /* retry */ }
      }
      setCeleryRestarting(false);
    }, 2000);
  }, []);

  useEffect(() => {
    loadProjectCriticReports(gatedProjectId);
  }, [gatedProjectId, loadProjectCriticReports]);

  /** When chapter critic blocks, refresh phase-5 readiness for concrete scene/chapter issue rows. */
  useEffect(() => {
    if (!gatedProjectId || run?.status !== "blocked" || run?.block_code !== "CRITIC_GATE") return undefined;
    let cancelled = false;
    void (async () => {
      const { ok, data } = await fetchProjectPhase5Readiness(api, gatedProjectId, phase5ReadinessFetchOpts);
      if (!cancelled && ok) setPhase5Ready(data);
    })();
    return () => {
      cancelled = true;
    };
  }, [gatedProjectId, run?.status, run?.block_code, phase5ReadinessFetchOpts]);

  useEffect(() => {
    if (!studioReady) return;
    loadChapterNarration(chapterId);
  }, [studioReady, chapterId, loadChapterNarration]);

  useEffect(() => {
    if (!studioReady) return;
    loadPhase3Summary(chapterId);
  }, [studioReady, chapterId, loadPhase3Summary]);

  useEffect(() => {
    if (!studioReady || !chapterId) return;
    loadScenes(chapterId);
  }, [studioReady, chapterId, loadScenes]);

  const sceneIdsPrefetchKey = useMemo(() => scenes.map((s) => String(s.id)).join(","), [scenes]);

  /** Load assets for every scene in the list so thumbnails can render (same API as gallery). */
  useEffect(() => {
    if (!studioReady || !sceneIdsPrefetchKey) return;
    for (const id of sceneIdsPrefetchKey.split(",")) {
      if (id) void loadSceneAssets(id);
    }
  }, [studioReady, sceneIdsPrefetchKey, loadSceneAssets]);

  /** After scenes load for the current chapter, focus the first scene if nothing in this chapter is selected yet. */
  useEffect(() => {
    if (!chapterId || scenes.length === 0) return;
    const cur = expandedScene != null && String(expandedScene).trim() !== "" ? String(expandedScene) : "";
    const inChapter = cur && scenes.some((s) => String(s.id) === cur);
    if (inChapter) return;
    const first = scenes[0]?.id;
    if (!first) return;
    setExpandedScene(first);
    void loadSceneAssets(String(first));
  }, [chapterId, scenes, expandedScene, loadSceneAssets]);

  useEffect(() => {
    const sid = expandedScene || scenes[0]?.id || null;
    if (!sid) {
      setRetryPrompt("");
      setRetryVideoPrompt("");
      retryPromptSceneRef.current = null;
      retryVideoPromptSceneRef.current = null;
      return;
    }
    const sc = scenes.find((s) => String(s.id) === String(sid));
    if (!sc) return;
    if (retryPromptSceneRef.current !== sid) {
      retryPromptSceneRef.current = sid;
      setRetryPrompt(baseImagePromptFromScene(sc));
    }
    if (retryVideoPromptSceneRef.current !== sid) {
      retryVideoPromptSceneRef.current = sid;
      setRetryVideoPrompt(baseVideoPromptFromScene(sc));
    }
  }, [expandedScene, scenes]);

  useEffect(() => {
    const sid = expandedScene || scenes[0]?.id || "";
    if (!sid) {
      setSceneNarrationDraft("");
      setSceneNarrationDirty(false);
      scriptEditSceneIdRef.current = "";
      return;
    }
    const switched = String(scriptEditSceneIdRef.current) !== String(sid);
    scriptEditSceneIdRef.current = String(sid);
    const sc = scenes.find((s) => String(s.id) === String(sid));
    const next = sc?.narration_text ?? "";
    if (switched) {
      setSceneNarrationDraft(next);
      setSceneNarrationDirty(false);
      return;
    }
    if (!sceneNarrationDirty) {
      setSceneNarrationDraft(next);
    }
  }, [expandedScene, scenes, sceneNarrationDirty]);

  useEffect(() => {
    const sid = expandedScene || scenes[0]?.id || "";
    void loadSceneNarrationMeta(sid ? String(sid) : null);
  }, [expandedScene, scenes, loadSceneNarrationMeta]);

  const saveAppSettings = async () => {
    setSettingsBusy(true);
    setError("");
    try {
      const r = await api("/v1/settings", {
        method: "PATCH",
        body: JSON.stringify({ config: appConfig }),
      });
      const body = await parseJson(r);
      if (!r.ok) throw new Error(apiErrorMessage(body));
      setAppConfig(body.data?.config || {});
      setPlatformCredentialKeysInherited(
        Array.isArray(body.data?.platform_credential_keys_inherited)
          ? body.data.platform_credential_keys_inherited
          : [],
      );
      setCredentialKeysPresent(
        body.data?.credential_keys_present && typeof body.data.credential_keys_present === "object"
          ? body.data.credential_keys_present
          : {},
      );
      setSettingsLoadError("");
      setMessage("Settings saved.");
    } catch (e) {
      setError(formatUserFacingError(e));
    } finally {
      setSettingsBusy(false);
    }
  };

  /** Merge keys into workspace config and PATCH (used from inspector for auto-pipeline toggles). */
  const patchWorkspaceConfig = async (partial) => {
    const next = { ...appConfig, ...(partial && typeof partial === "object" ? partial : {}) };
    setSettingsBusy(true);
    setError("");
    try {
      const r = await api("/v1/settings", {
        method: "PATCH",
        body: JSON.stringify({ config: next }),
      });
      const body = await parseJson(r);
      if (!r.ok) throw new Error(apiErrorMessage(body));
      setAppConfig(body.data?.config || next);
      setPlatformCredentialKeysInherited(
        Array.isArray(body.data?.platform_credential_keys_inherited)
          ? body.data.platform_credential_keys_inherited
          : [],
      );
      setCredentialKeysPresent(
        body.data?.credential_keys_present && typeof body.data.credential_keys_present === "object"
          ? body.data.credential_keys_present
          : {},
      );
      setSettingsLoadError("");
    } catch (e) {
      setError(formatUserFacingError(e));
    } finally {
      setSettingsBusy(false);
    }
  };

  useEffect(() => {
    setBurnSubtitlesOnFinalCut(Boolean(appConfig.burn_subtitles_in_final_cut_default));
  }, [appConfig.burn_subtitles_in_final_cut_default]);

  useEffect(() => {
    if (!studioReady || !agentRunId) return undefined;
    refreshRun();
    const id = setInterval(refreshRun, 1500);
    return () => clearInterval(id);
  }, [studioReady, agentRunId, refreshRun]);

  const { job: mediaJob } = usePollJob(mediaJobId, mediaPoll && studioReady, jobPollIntervalMs);

  useEffect(() => {
    if (!mediaJob) return;
    if (mediaJob.status === "succeeded" || mediaJob.status === "failed") {
      if (lastHandledMediaJobId === mediaJob.id) return;
      setLastHandledMediaJobId(mediaJob.id);
      setMediaPoll(false);
      const mediaSceneId =
        mediaJob?.payload?.scene_id || mediaJob?.result?.scene_id || null;
      if (mediaSceneId) {
        setExpandedScene(String(mediaSceneId));
        loadSceneAssets(String(mediaSceneId));
        if (mediaJob.type === "scene_generate_image" || mediaJob.type === "scene_generate_video") {
          window.setTimeout(() => loadSceneAssets(String(mediaSceneId)), 450);
        }
      }
      const aid = mediaJob?.result?.asset_id;
      if (
        mediaJob.status === "succeeded" &&
        aid &&
        (mediaJob.type === "scene_generate_image" || mediaJob.type === "scene_generate_video")
      ) {
        setPinnedPreviewAssetId(String(aid));
      }
      const jobLabel =
        mediaJob.type === "scene_generate"
          ? "Scene planning"
          : mediaJob.type === "scene_extend"
            ? "Extend scene"
            : mediaJob.type === "scene_generate_image"
              ? "Image"
              : mediaJob.type === "scene_generate_video"
                ? "Video"
                : mediaJob.type === "rough_cut"
                  ? "Rough cut"
                  : mediaJob.type === "fine_cut"
                    ? "Fine cut"
                    : mediaJob.type === "final_cut"
                      ? "Final cut"
                      : mediaJob.type === "export"
                        ? "Export bundle"
                        : "Job";
      const gatePayload =
        mediaJob.status === "failed" && mediaJob.error_message
          ? parsePhase5GateModalPayload(mediaJob.error_message)
          : null;
      const showExportGateModal = Boolean(
        gatePayload && EXPORT_COMPILE_JOB_TYPES.has(mediaJob.type) && mediaJob.status === "failed",
      );
      const detail = mediaJob.error_message ? ` — ${humanizeErrorText(mediaJob.error_message)}` : "";
      if (showExportGateModal) {
        setMessage(`${jobLabel} was blocked by export checks — use the dialog below.`);
      } else {
        setMessage(`${jobLabel} (${mediaJobId.slice(0, 8)}…) ${mediaJob.status}${detail}`);
      }
      if (mediaJob.status === "failed" && mediaJob.error_message) {
        if (showExportGateModal) {
          const p = mediaJob.payload && typeof mediaJob.payload === "object" ? mediaJob.payload : {};
          const tvHint = p.timeline_version_id;
          void (async () => {
            if (projectId) {
              await refreshPhase5Readiness({
                pid: projectId,
                timelineVersionIdHint: tvHint,
                reportError: false,
              });
            }
            setPhase5ExportGateModal({ jobLabel, ...gatePayload });
            setError("");
          })();
        } else {
          setError(`${jobLabel} failed: ${humanizeErrorText(mediaJob.error_message)}`);
        }
      }
      if (expandedScene) loadSceneAssets(expandedScene);
      if (chapterId) {
        loadScenes(chapterId);
        loadPhase3Summary(chapterId);
      }
      if (projectId && mediaJob.type === "chapter_critique") {
        loadChapters(projectId);
      }
      const rid = mediaJob.result?.critic_report_id;
      if (
        rid &&
        (mediaJob.type === "scene_critique" || mediaJob.type === "chapter_critique")
      ) {
        loadCriticReport(rid);
      }
      if (projectId && mediaJob?.status === "succeeded") {
        void (async () => {
          const { ok, data } = await fetchProjectPhase5Readiness(api, projectId, phase5ReadinessFetchOpts);
          if (ok) setPhase5Ready(data);
        })();
        if (["rough_cut", "fine_cut", "final_cut", "export", "subtitles_generate"].includes(mediaJob.type)) {
          loadPipelineStatus(projectId);
        }
        if (["script_outline", "script_chapters", "research_run", "script_chapter_regenerate"].includes(mediaJob.type)) {
          loadChapters(projectId);
          if (activePage === "research_chapters") {
            void loadResearchChaptersEditor(projectId);
          }
          if (chapterId) {
            loadScenes(chapterId);
            loadPhase3Summary(chapterId);
          }
        }
      }
      if (chapterId && mediaJob.type === "narration_generate" && mediaJob.status === "succeeded") {
        loadChapterNarration(chapterId);
      }
      if (mediaJob.type === "narration_generate_scene" && mediaJob.status === "succeeded") {
        const mp = mediaJob.payload && typeof mediaJob.payload === "object" ? mediaJob.payload : {};
        const nsid = mp.scene_id ?? mediaJob.result?.scene_id;
        if (nsid) void loadSceneNarrationMeta(String(nsid));
      }
      if (projectId && mediaJob.type === "scene_generate" && mediaJob.status === "succeeded" && chapterId) {
        loadScenes(chapterId);
        loadPhase3Summary(chapterId);
      }
    }
  }, [
    mediaJob,
    lastHandledMediaJobId,
    mediaJobId,
    loadSceneAssets,
    loadScenes,
    loadPhase3Summary,
    loadChapters,
    loadResearchChaptersEditor,
    loadCriticReport,
    loadChapterNarration,
    loadSceneNarrationMeta,
    loadPipelineStatus,
    refreshPhase5Readiness,
    activePage,
    chapterId,
    projectId,
    expandedScene,
    phase5ReadinessFetchOpts,
  ]);

  /** Jobs that left the active list while still queued/running = finished; refresh UI (batch images, background jobs). */
  useEffect(() => {
    if (!projectId) {
      activeJobsPrevRef.current = [];
      return;
    }
    const prev = activeJobsPrevRef.current;
    const now = activeProjectJobs || [];
    const nowIds = new Set(now.map((j) => j.id));

    for (const pj of prev) {
      if (!pj?.id) continue;
      const wasActive = pj.status === "running" || pj.status === "queued";
      if (!wasActive || nowIds.has(pj.id)) continue;
      const p = pj.payload && typeof pj.payload === "object" ? pj.payload : {};
      const sid = p.scene_id ?? pj.result?.scene_id;
      if (sid) loadSceneAssets(String(sid));
      if (chapterId) {
        loadScenes(chapterId);
        loadPhase3Summary(chapterId);
      }
      if (pj.type === "narration_generate" && chapterId) loadChapterNarration(chapterId);
      if (pj.type === "narration_generate_scene") {
        const nsid = p.scene_id ?? pj.result?.scene_id;
        if (nsid) void loadSceneNarrationMeta(String(nsid));
      }
      if (["rough_cut", "fine_cut", "final_cut", "export", "subtitles_generate"].includes(pj.type)) {
        loadPipelineStatus(projectId);
      }
      if (["script_outline", "script_chapters", "research_run", "script_chapter_regenerate"].includes(pj.type)) {
        loadChapters(projectId);
        if (activePage === "research_chapters") {
          void loadResearchChaptersEditor(projectId);
        }
      }
      if (
        chapterId &&
        ["scene_generate", "scene_generate_image", "scene_generate_video", "scene_extend"].includes(pj.type)
      ) {
        loadScenes(chapterId);
        loadPhase3Summary(chapterId);
      }
      if (["scene_generate_image", "scene_generate_video"].includes(pj.type) && sid) {
        window.setTimeout(() => loadSceneAssets(String(sid)), 450);
      }
      if (["scene_critique", "chapter_critique", "scene_critic_revision"].includes(pj.type)) {
        loadProjectCriticReports(projectId);
        if (chapterId) loadScenes(chapterId);
      }
      void (async () => {
        const { ok, data } = await fetchProjectPhase5Readiness(api, projectId, phase5ReadinessFetchOpts);
        if (ok) setPhase5Ready(data);
      })();
    }

    activeJobsPrevRef.current = now.map((j) => ({
      id: j.id,
      status: j.status,
      type: j.type,
      payload: j.payload,
      result: j.result,
    }));
  }, [
    activeProjectJobs,
    activePage,
    projectId,
    chapterId,
    loadSceneAssets,
    loadScenes,
    loadPhase3Summary,
    loadChapterNarration,
    loadSceneNarrationMeta,
    loadPipelineStatus,
    loadChapters,
    loadResearchChaptersEditor,
    loadProjectCriticReports,
    phase5ReadinessFetchOpts,
  ]);

  useEffect(() => {
    if (!researchPipelineBusy || !Array.isArray(activeProjectJobs)) return;
    const busy = activeProjectJobs.some(
      (j) => j && j.type === "research_run" && (j.status === "queued" || j.status === "running"),
    );
    if (!busy) setResearchPipelineBusy(false);
  }, [activeProjectJobs, researchPipelineBusy]);

  useEffect(() => {
    if (!chapterRegenerateId || !Array.isArray(activeProjectJobs)) return;
    const busy = activeProjectJobs.some(
      (j) =>
        j &&
        j.type === "script_chapter_regenerate" &&
        (j.status === "queued" || j.status === "running"),
    );
    if (!busy) setChapterRegenerateId("");
  }, [activeProjectJobs, chapterRegenerateId]);

  const startAgentRun = async () => {
    setBusy(true);
    setError("");
    setMessage("");
    setRun(null);
    setChapters([]);
    setScenes([]);
    setChapterId("");
    try {
      const narRefRaw = String(appConfig.default_narration_style_ref || "").trim();
      const narPresetFallback = String(
        appConfig.narration_style_preset || stylePresets.defaults?.narration_style_preset || DEFAULT_NARRATION_PRESET_ID,
      ).trim();
      const narration_style =
        narRefRaw && (narRefRaw.startsWith("preset:") || narRefRaw.startsWith("user:"))
          ? narRefRaw
          : `preset:${narPresetFallback || DEFAULT_NARRATION_PRESET_ID}`;
      const visId = String(
        appConfig.visual_style_preset || stylePresets.defaults?.visual_style_preset || "cinematic_documentary",
      ).trim();
      const pipeline_options =
        pipelineMode === "manual"
          ? { through: "chapters" }
          : pipelineMode === "unattended"
            ? {
                through: "full_video",
                unattended: true,
                narration_granularity: "scene",
                ...sceneAutomationMediaPipelineOptions(appConfig),
                ...(forceReplanScenesOnContinue ? { force_replan_scenes: true } : {}),
              }
            : {
                // Auto (new project): same scene-media + replan prefs as Automate on an existing project,
                // so workspace toggles apply even when Auto target is "critique" first (stored on the run for later tail).
                through: autoThrough,
                narration_granularity: "scene",
                ...sceneAutomationMediaPipelineOptions(appConfig),
                ...(forceReplanScenesOnContinue ? { force_replan_scenes: true } : {}),
              };
      const r = await api("/v1/agent-runs", {
        method: "POST",
        body: JSON.stringify({
          brief: {
            title,
            topic,
            target_runtime_minutes: Number(runtime),
            audience: "general",
            tone: "documentary",
            narration_style,
            visual_style: `preset:${visId || "cinematic_documentary"}`,
            frame_aspect_ratio: frameAspectRatio === "9:16" ? "9:16" : "16:9",
            ...briefPreferredMediaProvidersFromAppConfig(appConfig),
          },
          pipeline_options,
        }),
      });
      const body = await parseJson(r);
      if (!r.ok) throw new Error(apiErrorMessage(body));
      const ar = body.data?.agent_run;
      const proj = body.data?.project;
      if (proj?.id) setProjectId(proj.id);
      loadProjects();
      if (ar?.id) {
        setAgentRunId(ar.id);
        setRun(ar);
      }
      setMessage(
        pipelineMode === "manual"
          ? "Agent run queued — stops after chapter scripts are written; reload chapters when it finishes."
          : pipelineMode === "unattended"
            ? "Hands-off run queued — worker will attempt research through final video (relaxed research gate; check logs if sources are thin)."
            : autoThrough === "critique"
              ? "Agent run queued — by default Auto stops after the one-time story vs research check (no media tail). Switch Auto target to “Through final video” for one-pass character bible → images → narration → cuts, or Automate again later."
              : "Agent run queued — runs through final video after scenes and story check; polling status…",
      );
    } catch (e) {
      setError(formatUserFacingError(e));
    } finally {
      setBusy(false);
    }
  };

  /** Resume autonomous pipeline on the open project (skips work already satisfied). */
  const continuePipelineAuto = async () => {
    if (!projectId) return;
    setBusy(true);
    setError("");
    setMessage("");
    try {
      const r = await api("/v1/agent-runs", {
        method: "POST",
        body: JSON.stringify({
          project_id: projectId,
          pipeline_options: {
            continue_from_existing: true,
            through: pipelineMode === "unattended" ? "full_video" : autoThrough,
            rerun_web_research: false,
            ...(pipelineMode === "unattended" ? { unattended: true } : {}),
            ...(forceReplanScenesOnContinue ? { force_replan_scenes: true } : {}),
            ...(pipelineMode === "unattended" || (pipelineMode === "auto" && autoThrough === "full_video")
              ? {
                  ...sceneAutomationMediaPipelineOptions(appConfig),
                  narration_granularity: "scene",
                }
              : {}),
          },
        }),
      });
      const body = await parseJson(r);
      if (!r.ok) throw new Error(apiErrorMessage(body));
      const ar = body.data?.agent_run;
      if (ar?.id) {
        setAgentRunId(ar.id);
        setRun(ar);
      }
      loadPipelineStatus(projectId);
      setMessage(
        pipelineMode === "auto" && autoThrough === "critique"
          ? "Auto pipeline resumed (critique target — completes after story vs research unless you switch Auto target to final video). See Run activity for stages."
          : "Auto pipeline resumed. See Control / Inspector → Run activity for exact stage actions.",
      );
    } catch (e) {
      setError(formatUserFacingError(e));
    } finally {
      setBusy(false);
    }
  };

  const openRestartAutomationModal = useCallback(() => {
    setRestartAutomationForce(Object.fromEntries(RESTART_AUTOMATION_STEPS.map((s) => [s.key, true])));
    setRestartAutomationThrough(
      pipelineMode === "unattended" || (pipelineMode === "auto" && autoThrough === "full_video")
        ? "full_video"
        : "critique",
    );
    setRestartRerunWebResearch(false);
    setRestartAutomationOpen(true);
  }, [pipelineMode, autoThrough]);

  const submitRestartAutomation = useCallback(async () => {
    if (!projectId) return;
    const force_pipeline_steps = RESTART_AUTOMATION_STEPS.filter((s) => restartAutomationForce[s.key]).map(
      (s) => s.key,
    );
    if (force_pipeline_steps.length === 0) {
      setError("Select at least one step to re-run.");
      return;
    }
    let through = restartAutomationThrough;
    if (
      force_pipeline_steps.some((k) =>
        ["auto_characters", "auto_images", "auto_videos", "auto_narration"].includes(k),
      )
    ) {
      through = "full_video";
    }
    setBusy(true);
    setError("");
    setMessage("");
    setRestartAutomationOpen(false);
    try {
      const r = await api("/v1/agent-runs", {
        method: "POST",
        body: JSON.stringify({
          project_id: projectId,
          pipeline_options: {
            continue_from_existing: true,
            through,
            force_pipeline_steps,
            rerun_web_research: restartRerunWebResearch,
            ...(pipelineMode === "unattended" ? { unattended: true } : {}),
            ...(through === "full_video" || pipelineMode === "unattended"
              ? {
                  ...sceneAutomationMediaPipelineOptions(appConfig),
                  narration_granularity: "scene",
                }
              : {}),
          },
        }),
      });
      const body = await parseJson(r);
      if (!r.ok) throw new Error(apiErrorMessage(body));
      const ar = body.data?.agent_run;
      if (ar?.id) {
        setAgentRunId(ar.id);
        setRun(ar);
      }
      void loadPipelineStatus(projectId);
      setMessage(
        "Restart automation queued — checked steps re-run even when already complete; unchecked steps stay as fast-skip when satisfied.",
      );
    } catch (e) {
      setError(formatUserFacingError(e));
    } finally {
      setBusy(false);
    }
  }, [
    projectId,
    restartAutomationForce,
    restartAutomationThrough,
    restartRerunWebResearch,
    pipelineMode,
    appConfig,
    loadPipelineStatus,
  ]);

  /**
   * Start a new agent run that skips earlier phases when safe, but re-executes from the chosen row.
   * Project progress stays data-driven; this only replaces the active agent run for live status.
   */
  const rerunPipelineFromStep = useCallback(
    async (pipelineStepId) => {
      if (!projectId) return;
      const rerunFrom = PIPELINE_STEP_TO_RERUN_FROM[pipelineStepId];
      if (!rerunFrom) return;
      const needsTail = PIPELINE_RERUN_NEEDS_FULL_VIDEO.has(pipelineStepId);
      const through =
        needsTail || pipelineMode === "unattended"
          ? "full_video"
          : pipelineMode === "auto"
            ? autoThrough
            : "critique";
      if (needsTail && pipelineMode === "auto" && autoThrough !== "full_video") {
        setMessage(
          "Using full video for this re-run (character bible → scene images → narration, timeline, cuts). Switch Auto target to full video to match next time.",
        );
      }
      const rerun_web_research =
        rerunFrom === "research"
          ? true
          : window.confirm(
              "Re-run web research (sources and claims) for this project?\n\n" +
                "OK = re-run research. Cancel = skip and continue with the existing dossier when available.",
            );
      setBusy(true);
      setError("");
      try {
        const r = await api("/v1/agent-runs", {
          method: "POST",
          body: JSON.stringify({
            project_id: projectId,
            pipeline_options: {
              continue_from_existing: true,
              through,
              rerun_from_step: rerunFrom,
              rerun_web_research,
              ...(pipelineMode === "unattended" ? { unattended: true } : {}),
              ...(through === "full_video" || pipelineMode === "unattended"
                ? {
                    ...sceneAutomationMediaPipelineOptions(appConfig),
                    narration_granularity: "scene",
                  }
                : {}),
            },
          }),
        });
        const body = await parseJson(r);
        if (!r.ok) throw new Error(apiErrorMessage(body));
        const ar = body.data?.agent_run;
        if (ar?.id) {
          setAgentRunId(ar.id);
          setRun(ar);
        }
        void loadPipelineStatus(projectId);
        setMessage(`Re-run queued from “${rerunFrom.replace(/_/g, " ")}” — new run id in Run activity.`);
      } catch (e) {
        setError(formatUserFacingError(e));
      } finally {
        setBusy(false);
      }
    },
    [
      projectId,
      pipelineMode,
      autoThrough,
      appConfig,
      loadPipelineStatus,
    ],
  );

  const pipelineControl = async (action) => {
    if (!agentRunId) return;
    setBusy(true);
    setError("");
    try {
      const r = await api(`/v1/agent-runs/${agentRunId}/control`, {
        method: "POST",
        body: JSON.stringify({ action }),
      });
      const body = await parseJson(r);
      if (!r.ok) throw new Error(apiErrorMessage(body));
      if (body.data) setRun(body.data);
      if (projectId) loadPipelineStatus(projectId);
      setMessage(`Pipeline: ${action}`);
    } catch (e) {
      setError(formatUserFacingError(e));
    } finally {
      setBusy(false);
    }
  };

  const postScenesGenerate = async () => {
    if (!chapterId) return;
    const n = scenes.length;
    const body = {};
    if (n > 0) {
      const ok = window.confirm(
        `Replace all ${n} scene(s) in this chapter? Existing scenes and their assets will be removed from the plan. ` +
          `To add one more beat without deleting anything, use "Extend scene" instead.`,
      );
      if (!ok) return;
      body.replace_existing_scenes = true;
    }
    await queueMediaJob(`/v1/chapters/${chapterId}/scenes/generate`, body, "Scene planning job queued…");
  };

  const postScenesExtend = async () => {
    if (!chapterId || scenes.length === 0) return;
    await queueMediaJob(
      `/v1/chapters/${chapterId}/scenes/extend`,
      {},
      "Extend scene job queued — appends one beat after your current scenes…",
    );
  };

  const postImage = async (sceneId, path, bodyObj = {}) => {
    setBusy(true);
    setError("");
    setExpandedScene(sceneId);
    try {
      const extra = { ...bodyObj };
      if (path === "generate-image" || path === "retry") {
        const m = String(appConfig.fal_smoke_model || "").trim();
        if (m) extra.fal_image_model = m;
        const p = String(appConfig.active_image_provider || "fal").trim().toLowerCase();
        if (p) extra.image_provider = p;
        if (refineBracketImageWithLlm) extra.refine_bracket_visual_with_llm = true;
      }
      if (path === "generate-video") {
        const m = String(appConfig.fal_video_model || "").trim();
        if (m) extra.fal_video_model = m;
        const vp = String(appConfig.active_video_provider || "fal").trim().toLowerCase();
        if (vp) extra.video_provider = vp;
      }
      const body = await apiPostIdempotent(api, `/v1/scenes/${sceneId}/${path}`, extra, idem);
      const jid = body.job?.id;
      if (jid) {
        setMediaJobId(jid);
        setMediaPoll(true);
      }
      loadSceneAssets(sceneId);
      setMessage(`${path} queued…`);
      void loadActiveProjectJobs();
    } catch (e) {
      setError(formatUserFacingError(e));
    } finally {
      setBusy(false);
    }
  };

  const stopBatchChapterImages = () => {
    batchImagesCancelRef.current = true;
  };

  /** Sleep up to `ms`, but wake every 500ms so Stop batch can interrupt long waits. */
  const sleepBatchWait = (ms) =>
    new Promise((resolve) => {
      const start = Date.now();
      const tick = () => {
        if (batchImagesCancelRef.current) {
          resolve();
          return;
        }
        if (Date.now() - start >= ms) {
          resolve();
          return;
        }
        window.setTimeout(tick, Math.min(500, ms - (Date.now() - start)));
      };
      tick();
    });

  /** Enqueue one image job per scene in the current chapter, spacing by Settings → Studio (default 15s, not provider generation time). */
  const startBatchChapterImages = async () => {
    if (!chapterId || scenes.length === 0) return;
    batchImagesCancelRef.current = false;
    const intervalSec = Math.max(
      2,
      Math.min(3600, Number(appConfig.studio_batch_image_interval_sec) || 5),
    );
    const ordered = [...scenes].sort((a, b) => (a.order_index ?? 0) - (b.order_index ?? 0));
    const n = ordered.length;
    const rawFrom = String(batchImageRangeFrom ?? "").trim();
    const rawTo = String(batchImageRangeTo ?? "").trim();
    let fromIdx = 1;
    let toIdx = n;
    if (rawFrom !== "") {
      const v = parseInt(rawFrom, 10);
      if (!Number.isFinite(v) || v < 1 || v > n) {
        setError(`Batch range: "From" must be between 1 and ${n} (story order in this chapter).`);
        return;
      }
      fromIdx = v;
    }
    if (rawTo !== "") {
      const v = parseInt(rawTo, 10);
      if (!Number.isFinite(v) || v < 1 || v > n) {
        setError(`Batch range: "To" must be between 1 and ${n} (story order in this chapter).`);
        return;
      }
      toIdx = v;
    }
    if (fromIdx > toIdx) {
      setError('Batch range: "From" must be less than or equal to "To".');
      return;
    }
    const toSlice = ordered.slice(fromIdx - 1, toIdx);
    if (toSlice.length === 0) {
      setError("Batch range: no scenes in range (check From / To).");
      return;
    }
    setError("");
    setBatchImagesProgress({ total: toSlice.length, done: 0, label: "Starting…" });
    try {
      for (let i = 0; i < toSlice.length; i++) {
        if (batchImagesCancelRef.current) {
          setMessage(`Batch images stopped after ${i} / ${toSlice.length}.`);
          break;
        }
        if (i > 0) {
          await sleepBatchWait(intervalSec * 1000);
        }
        if (batchImagesCancelRef.current) break;
        const scene = toSlice[i];
        const sid = scene.id;
        const globalNum = fromIdx + i;
        setExpandedScene(sid);
        setBatchImagesProgress({
          total: toSlice.length,
          done: i,
          label: `Scene ${globalNum} of ${n} (batch ${i + 1}/${toSlice.length}) · S${(scene.order_index ?? globalNum - 1) + 1}`,
        });
        const extra = {};
        const m = String(appConfig.fal_smoke_model || "").trim();
        if (m) extra.fal_image_model = m;
        const p = String(appConfig.active_image_provider || "fal").trim().toLowerCase();
        if (p) extra.image_provider = p;
        if (refineBracketImageWithLlm) extra.refine_bracket_visual_with_llm = true;
        const body = await apiPostIdempotent(api, `/v1/scenes/${sid}/generate-image`, extra, idem);
        const jid = body.job?.id;
        if (jid) {
          setMediaJobId(jid);
          setMediaPoll(true);
        }
        loadSceneAssets(sid);
        void loadActiveProjectJobs();
        setMessage(
          `Batch images: queued ${i + 1} / ${toSlice.length} (chapter scene ${globalNum}/${n}; spacing ${intervalSec}s between jobs).`,
        );
        setBatchImagesProgress({
          total: toSlice.length,
          done: i + 1,
          label: `Queued ${i + 1} / ${toSlice.length}`,
        });
      }
      if (!batchImagesCancelRef.current && toSlice.length > 0) {
        setMessage(
          `Batch images: finished queueing ${toSlice.length} job(s) (scenes ${fromIdx}–${toIdx} of ${n}). Poll interval uses Settings → Studio.`,
        );
      }
    } catch (e) {
      setError(formatUserFacingError(e));
    } finally {
      setBatchImagesProgress(null);
    }
  };

  const postSceneCritique = async (sceneId) => {
    setBusy(true);
    setError("");
    setExpandedScene(sceneId);
    try {
      const body = await apiPostIdempotent(api, `/v1/scenes/${sceneId}/critique`, {}, idem);
      const jid = body.job?.id;
      if (jid) {
        setMediaJobId(jid);
        setMediaPoll(true);
      }
      setMessage("Scene critique queued…");
    } catch (e) {
      setError(formatUserFacingError(e));
    } finally {
      setBusy(false);
    }
  };

  /** Open chapter in the UI and optionally focus a scene (loads scenes list). */
  const goToChapterScene = useCallback(
    async (chId, sceneIdToFocus) => {
      if (!chId) return;
      setChapterId(chId);
      const r = await api(`/v1/chapters/${chId}/scenes`);
      const body = await parseJson(r);
      if (r.ok) {
        const next = body.data?.scenes || [];
        setScenes(next);
        if (sceneIdToFocus && next.some((s) => s.id === sceneIdToFocus)) {
          setExpandedScene(sceneIdToFocus);
          loadSceneAssets(sceneIdToFocus);
        } else if (next[0]?.id) {
          setExpandedScene(next[0].id);
          loadSceneAssets(next[0].id);
        }
      }
    },
    [loadSceneAssets],
  );

  const openSceneForCriticReport = useCallback(
    async (sceneId) => {
      if (!sceneId) return;
      setError("");
      const r = await api(`/v1/scenes/${sceneId}`);
      const body = await parseJson(r);
      if (!r.ok) {
        setError(apiErrorMessage(body) || "Could not load that scene. Try refreshing the chapter list.");
        return;
      }
      const chId = body.data?.chapter_id;
      if (!chId) {
        setError("Could not determine which chapter this scene belongs to.");
        return;
      }
      await goToChapterScene(String(chId), String(sceneId));
    },
    [goToChapterScene],
  );

  /** Jump to the scene that owns a timeline-flagged asset and pin that asset in the preview when possible. */
  const openSceneForTimelineAttentionAsset = useCallback(
    async (assetId) => {
      if (!assetId) return;
      const list = phase5Ready?.export_attention_timeline_assets;
      const row = Array.isArray(list) ? list.find((x) => String(x?.asset_id) === String(assetId)) : null;
      if (!row?.scene_id) return;
      setPinnedPreviewAssetId(null);
      await openSceneForCriticReport(String(row.scene_id));
      setPinnedPreviewAssetId(String(assetId));
    },
    [phase5Ready, openSceneForCriticReport],
  );

  /** `targetChapterId` optional — defaults to current chapter selector. */
  const postChapterCritique = async (targetChapterId) => {
    const cid = targetChapterId || chapterId;
    if (!cid) return;
    setBusy(true);
    setError("");
    try {
      const body = await apiPostIdempotent(api, `/v1/chapters/${cid}/critique`, {}, idem);
      const jid = body.job?.id;
      if (jid) {
        setMediaJobId(jid);
        setMediaPoll(true);
      }
      setMessage("Chapter critique queued…");
    } catch (e) {
      setError(formatUserFacingError(e));
    } finally {
      setBusy(false);
    }
  };

  /** Rough-cut repair: reject flagged timeline images and queue scene_generate_image per affected scene. */
  const rejectAndRegenerateRoughCutImages = async () => {
    const pid = sanitizeStudioUuid(projectId);
    const tv = sanitizeStudioUuid(timelineVersionId);
    if (!pid || !tv) return;
    setBusy(true);
    setError("");
    try {
      const q = pipelineMode === "unattended" ? "?allow_unapproved_media=true" : "";
      const b = await apiPostIdempotent(
        api,
        `/v1/projects/${encodeURIComponent(pid)}/timeline-versions/${encodeURIComponent(tv)}/reject-and-regenerate-rough-cut-images${q}`,
        {},
        idem,
      );
      const d = b.data || {};
      const n = (d.rejected_asset_ids || []).length;
      const m = (d.scene_ids_queued || []).length;
      setMessage(
        n || m
          ? `Rejected ${n} timeline image(s), queued ${m} scene image job(s). When jobs finish, use Reconcile timeline clips, then Check readiness.`
          : String(d.note || "Nothing matched rough-cut image repair rules."),
      );
      await refreshPhase5Readiness({ pid, timelineVersionIdHint: tv, reportError: false });
      void loadActiveProjectJobs();
      if (chapterId) loadScenes(chapterId);
      if (expandedScene) loadSceneAssets(expandedScene);
    } catch (e) {
      setError(formatUserFacingError(e));
    } finally {
      setBusy(false);
    }
  };

  // ---------------------------------------------------------------------------
  // Keyboard shortcuts
  // ---------------------------------------------------------------------------
  const sortedScenes = useMemo(
    () => [...scenes].sort((a, b) => (Number(a.order_index) || 0) - (Number(b.order_index) || 0)),
    [scenes],
  );

  useKeyboardShortcuts(
    {
      onNextScene: useCallback(() => {
        if (!sortedScenes.length) return;
        const idx = sortedScenes.findIndex((s) => s.id === expandedScene);
        const next = sortedScenes[Math.min(idx + 1, sortedScenes.length - 1)];
        if (next) setExpandedScene(next.id);
      }, [sortedScenes, expandedScene]),

      onPrevScene: useCallback(() => {
        if (!sortedScenes.length) return;
        const idx = sortedScenes.findIndex((s) => s.id === expandedScene);
        const prev = sortedScenes[Math.max(idx - 1, 0)];
        if (prev) setExpandedScene(prev.id);
      }, [sortedScenes, expandedScene]),

      onApproveAsset: useCallback(() => {
        const sid = sceneIdForAssetGalleryRefresh();
        if (!sid) return;
        const assets = sceneAssets[sid] ?? [];
        const candidate = assets.find((a) => a.status === "succeeded" && !a.approved_at);
        if (candidate) void approveAsset(String(candidate.id));
      }, [sceneIdForAssetGalleryRefresh, sceneAssets, approveAsset]),

      onGenerateImage: useCallback(() => {
        if (!expandedScene || !projectId || busy) return;
        void api(`/v1/scenes/${encodeURIComponent(expandedScene)}/generate-image`, {
          method: "POST",
          body: JSON.stringify({ project_id: projectId }),
        }).then(async (r) => {
          const body = await parseJson(r);
          if (!r.ok) setError(apiErrorMessage(body) || "image generation failed");
          else void loadActiveProjectJobs();
        });
      }, [expandedScene, projectId, busy, loadActiveProjectJobs]),

      onSaveNarration: useCallback(() => {
        if (sceneNarrationDirty) void saveSceneNarrationDraft();
      }, [sceneNarrationDirty, saveSceneNarrationDraft]),

      onToggleHelp: useCallback(() => {
        setShowShortcutHelp((prev) => !prev);
      }, []),
    },
    { enabled: activePage === "editor" },
  );

  /** Point timeline clips at a valid scene still when the current ref is rejected, missing, or not approved. */
  const reconcileTimelineClipImages = async () => {
    const pid = sanitizeStudioUuid(projectId);
    const tv = sanitizeStudioUuid(timelineVersionId);
    if (!pid || !tv) return;
    setBusy(true);
    setError("");
    try {
      const q = pipelineMode === "unattended" ? "?allow_unapproved_media=true" : "";
      const b = await apiPostIdempotent(
        api,
        `/v1/projects/${encodeURIComponent(pid)}/timeline-versions/${encodeURIComponent(tv)}/reconcile-clip-images${q}`,
        {},
        idem,
      );
      const d = b.data || {};
      const rel = Number(d.relinked_assets) || 0;
      const appr = Number(d.approved_scene_stills) || 0;
      const sync = Number(d.storyboard_synced_clips) || 0;
      const reb = Number(d.rebound_clips) || 0;
      const up = Number(d.updated_clips) || 0;
      const un = Number(d.unchanged_clips) || 0;
      const bits = [
        rel ? `${rel} asset row(s) relinked from storage paths` : null,
        appr ? `${appr} scene still(s) auto-approved on disk` : null,
        sync ? `${sync} timeline clip(s) aligned to storyboard order` : null,
        reb ? `${reb} orphan clip(s) rebound to scene media` : null,
        `${up} clip(s) reconciled to viable scene media`,
        `${un} unchanged`,
      ].filter(Boolean);
      setMessage(`${bits.join(" · ")}. Run Check readiness, then try export again.`);
      await refreshPhase5Readiness({ pid, timelineVersionIdHint: tv, reportError: true });
    } catch (e) {
      setError(formatUserFacingError(e));
    } finally {
      setBusy(false);
    }
  };

  const reorderSceneAssets = async (sceneId, orderedIds) => {
    if (!sceneId || !orderedIds?.length) return;
    setError("");
    try {
      const r = await api(`/v1/scenes/${sceneId}/assets/sequence`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ asset_ids: orderedIds }),
      });
      const body = await parseJson(r);
      if (!r.ok) {
        setError(apiErrorMessage(body) || "reorder failed");
        return;
      }
      setSceneAssets((prev) => ({ ...prev, [sceneId]: body.data?.assets || [] }));
      if (chapterId) loadPhase3Summary(chapterId);
    } catch (e) {
      setError(formatUserFacingError(e));
    }
  };

  const events = Array.isArray(run?.steps_json) ? run.steps_json : [];

  const runStepGuidance = useMemo(() => {
    const throughLabel = autoThrough === "full_video" ? "full video" : "critique (scenes + one-time story check)";
    return {
      queued: "Waiting for worker pickup.",
      director: "Creating or enriching the director pack from your brief (tone, structure, and constraints).",
      research: "Gathering sources and evidence from your brief.",
      outline: "Outlining chapters and target lengths.",
      scripts: "Writing narration scripts from the outline.",
      chapters:
        "Batch-writing full scripts for every chapter in one model call — several minutes with no intermediate saves is normal.",
      scenes:
        "Breaking each chapter script into scenes (visuals + timing). One LLM pass per chapter that needs planning — long scripts or many chapters can take several minutes with no other UI updates unless progress is shown below.",
      story_research_review:
        "One-time LLM check comparing the scripted story to the research dossier (runs automatically once per project after scenes, then never repeats).",
      auto_characters:
        "Inferring recurring figures from your scripts and research into a character bible — image and video prompts use this for consistent looks.",
      scene_critique: "Legacy: per-scene quality review (no longer run by the agent).",
      scene_critic_repair: "Legacy: scene narration fixes after critic.",
      chapter_critique: "Legacy: chapter-level critic gate (no longer run by the agent).",
      chapter_critic_repair: "Legacy: batch fixes after chapter critic.",
      auto_images: "Generating/approving missing images so each scene has usable media.",
      auto_narration: "Synthesizing per-scene narration audio tracks.",
      auto_videos: "Generating optional per-scene videos for scenes missing succeeded video assets.",
      auto_timeline: "Building the edit timeline from approved media.",
      auto_rough_cut: "Rendering the first full-length video (rough cut) from the timeline.",
      auto_final_cut:
        "Muxing narration (chapter or per-scene VO), optional background music, and mix levels into the final video.",
      rough_cut: "Rendering a first full-length video cut.",
      subtitles: "Creating subtitles from scene scripts (chapter fallback if needed).",
      final_cut: "Mixing final video with narration, optional music bed, and saved mix levels.",
      export: "Packaging the final deliverables.",
      full_video:
        "Building the character bible (when needed), then scene media, narration, timeline, and exports after scene planning (includes a one-time story vs research check when needed).",
      working: "The worker is between named checkpoints or committing progress.",
      done:
        pipelineMode === "manual"
          ? "Run finished — chapter scripts are ready. Plan scenes in the editor, or switch to Auto and use Automate."
          : pipelineMode === "unattended"
            ? "Hands-off run finished — check timeline/exports and worker logs if anything stopped early."
            : `Run finished through ${throughLabel}.`,
    };
  }, [autoThrough, pipelineMode]);

  const runStoppingUi = run?.status === "running" && pipelineStopRequested(run?.pipeline_control_json);
  const runStepNow = useMemo(() => {
    if (runStoppingUi) {
      return "Stop requested — waiting for the worker to finish the current step; Studio jobs may still complete.";
    }
    if (run?.status === "cancelled") {
      return "Automation was stopped — progress above reflects project state, not an active run.";
    }
    if (run?.status === "running" || run?.status === "queued") {
      const effKey = resolveEffectiveAgentStepKey(run, { activeProjectJobs });
      let base =
        runStepGuidance[effKey] ||
        (run?.current_step ? runStepGuidance[run.current_step] : null) ||
        "Working on the current step.";
      const prog = lastScenesProgressEvent(run?.steps_json);
      if (
        effKey === "scenes" &&
        prog &&
        typeof prog.chapters_total === "number" &&
        prog.chapters_total > 0 &&
        typeof prog.chapter_index === "number"
      ) {
        const title =
          typeof prog.chapter_title === "string" && prog.chapter_title.trim()
            ? ` — “${prog.chapter_title.trim()}”`
            : "";
        base = `${base} Now planning chapter ${prog.chapter_index}/${prog.chapters_total}${title}.`;
      }
      return base;
    }
    if (run?.current_step) {
      return runStepGuidance[run.current_step] || "Working on the current step.";
    }
    return null;
  }, [runStoppingUi, run, activeProjectJobs, runStepGuidance]);

  const [agentRunStallTick, setAgentRunStallTick] = useState(0);
  useEffect(() => {
    if (!run || !["running", "queued"].includes(run.status)) return undefined;
    const id = window.setInterval(() => setAgentRunStallTick((x) => x + 1), 10_000);
    return () => window.clearInterval(id);
  }, [run?.id, run?.status]);

  const agentRunStallInfo = useMemo(
    () => computeAgentRunStallInfo(run, activeProjectJobs, Date.now()),
    [run, activeProjectJobs, agentRunStallTick],
  );

  /** Top alert: agent run, Studio-only jobs (image/video/…), and last completion (does not vanish when work finishes). */
  const headerProgressBanner = useMemo(() => {
    const studioActive = (activeProjectJobs || []).filter(
      (j) => j && STUDIO_MEDIA_JOB_TYPES.has(j.type) && ["queued", "running"].includes(j.status),
    );

    const runStoppingBanner =
      agentRunId &&
      run &&
      run.status === "running" &&
      pipelineStopRequested(run.pipeline_control_json);

    if (runStoppingBanner) {
      const through = agentThroughFromRun(run, autoThrough);
      const order = AGENT_PROGRESS_ORDER[through] || AGENT_PROGRESS_ORDER.full_video;
      const evs = Array.isArray(run.steps_json) ? run.steps_json : [];
      let effKey = run.current_step || "working";
      for (let i = evs.length - 1; i >= 0; i--) {
        const e = evs[i];
        if (e?.step && (e.status === "succeeded" || e.status === "skipped")) {
          effKey = e.step;
          break;
        }
      }
      const idx = order.indexOf(effKey);
      const stepTotal = order.length;
      const stepIndexDisplay = idx >= 0 ? idx + 1 : stepTotal;
      const pct = Math.min(96, Math.max(10, Math.round((stepIndexDisplay / Math.max(1, stepTotal)) * 92)));
      const throughLabel =
        through === "full_video" ? "Full video" : through === "chapters" ? "Through chapter scripts" : "Through story review";
      return {
        headline: "Stopping automation…",
        detail:
          "Stop was requested; the worker will exit after the current step or an in-flight provider call. Background Studio jobs may still run until you cancel them.",
        pct,
        stepIndexDisplay,
        stepTotal,
        effKey: "pipeline",
        throughLabel,
        statusLabel: "Stopping",
        stepShort: "Stopping",
        iconClassName: "fa-solid fa-circle-stop fa-fw pipeline-fa-icon pipeline-fa-icon--cancelled",
        trackActive: false,
      };
    }

    if (agentRunId && run && ["queued", "running", "paused"].includes(run.status)) {
      const st = run.status;
      const through = agentThroughFromRun(run, autoThrough);
      const order = AGENT_PROGRESS_ORDER[through] || AGENT_PROGRESS_ORDER.full_video;
      const effKey = resolveEffectiveAgentStepKey(run, { activeProjectJobs });
      let idx = order.indexOf(effKey);
      if (idx < 0) idx = effKey === "queued" ? -1 : 0;
      const stepTotal = order.length;
      const stepIndexDisplay = idx < 0 ? 0 : idx + 1;
      const pct =
        idx < 0 ? 4 : Math.min(100, Math.round(((idx + 0.4) / Math.max(1, stepTotal)) * 100));
      const headline = agentStageHeadline(effKey);
      const guidance = runStepGuidance[effKey] || runStepGuidance.working || "";
      const evs = Array.isArray(run.steps_json) ? run.steps_json : [];
      const liveEv = lastAgentEventWithStatus(evs, "running") || lastAgentEventWithStatus(evs, "retry");
      const meta = liveEv ? friendlyEventMeta(liveEv) : "";
      let detail = [guidance, meta].filter(Boolean).join(" ");
      if (st === "paused") {
        detail = detail
          ? `${detail} — Paused; use Resume in the inspector when ready.`
          : "Automation is paused — use Resume in the inspector when ready.";
      }
      const stallActive =
        Boolean(agentRunStallInfo?.stalled) && (st === "running" || st === "queued");
      if (stallActive) {
        const prefix = `No run heartbeat for ${agentRunStallInfo.stallLabel} — likely slow or unreachable APIs. `;
        detail = detail ? prefix + detail : prefix.trim();
      }
      const throughLabel =
        through === "full_video" ? "Full video" : through === "chapters" ? "Through chapter scripts" : "Through story review";
      return {
        headline,
        detail,
        pct,
        stepIndexDisplay,
        stepTotal,
        effKey,
        throughLabel,
        statusLabel: friendlyRunStatus(st),
        stepShort: friendlyPipelineStep(effKey),
        iconClassName: stallActive
          ? "fa-solid fa-triangle-exclamation fa-fw pipeline-fa-icon pipeline-fa-icon--stall"
          : agentPipelineActivityIconClass(effKey, st),
        trackActive: st === "running" || st === "queued",
        stallAlert: stallActive,
      };
    }

    if (projectId && studioActive.length > 0) {
      const j = studioActive[0];
      const effKey = inferMacroStepKeyFromJobType(j.type);
      const headline0 = studioJobKindHeadline(j.type);
      const mult = studioActive.length > 1 ? ` (+${studioActive.length - 1} more)` : "";
      const anyRun = studioActive.some((x) => x.status === "running");
      return {
        headline: `${headline0}${mult}`,
        detail:
          studioActive.length > 1
            ? `${studioActive.length} Studio jobs in progress. Open Background jobs in the editor for IDs and cancel.`
            : `${headline0} — job ${String(j.id).slice(0, 8)}…`,
        pct: Math.min(92, 24 + studioActive.length * 14),
        stepIndexDisplay: 1,
        stepTotal: 1,
        effKey,
        throughLabel: "Studio",
        statusLabel: friendlyRunStatus(anyRun ? "running" : "queued"),
        stepShort: friendlyPipelineStep(effKey),
        iconClassName: agentPipelineActivityIconClass(effKey, anyRun ? "running" : "queued"),
        trackActive: true,
      };
    }

    if (mediaJobId && mediaJob && ["queued", "running"].includes(mediaJob.status)) {
      const effKey = inferMacroStepKeyFromJobType(mediaJob.type);
      const headline = studioJobKindHeadline(mediaJob.type);
      return {
        headline,
        detail: `${headline} — job ${String(mediaJob.id).slice(0, 8)}…`,
        pct: 55,
        stepIndexDisplay: 1,
        stepTotal: 1,
        effKey,
        throughLabel: "Studio",
        statusLabel: friendlyRunStatus(mediaJob.status),
        stepShort: friendlyPipelineStep(effKey),
        iconClassName: agentPipelineActivityIconClass(effKey, mediaJob.status),
        trackActive: true,
      };
    }

    if (agentRunId && run && ["succeeded", "failed", "blocked", "cancelled"].includes(run.status)) {
      const st = run.status;
      const through = agentThroughFromRun(run, autoThrough);
      const order = AGENT_PROGRESS_ORDER[through] || AGENT_PROGRESS_ORDER.full_video;
      const evs = Array.isArray(run.steps_json) ? run.steps_json : [];
      let effKey = run.current_step || "working";
      for (let i = evs.length - 1; i >= 0; i--) {
        const e = evs[i];
        if (e?.step && (e.status === "succeeded" || e.status === "skipped")) {
          effKey = e.step;
          break;
        }
      }
      const idx = order.indexOf(effKey);
      const stepTotal = order.length;
      const stepIndexDisplay = idx >= 0 ? idx + 1 : stepTotal;
      const headline =
        st === "succeeded"
          ? "Automation finished"
          : st === "failed"
            ? "Automation failed"
            : st === "cancelled"
              ? "Automation stopped"
              : "Automation blocked";
      const tail =
        st === "cancelled"
          ? "You stopped this run. Background Studio jobs may still finish; the pipeline list shows project state."
          : runStepGuidance.done;
      let detail = tail;
      if (st === "blocked" && run.block_code) {
        detail = `${tail} (${run.block_code})`.trim();
      } else if (st === "failed" && run.error_message) {
        detail = `${tail} — ${summarizeAgentRunFailure(run.error_message)}`.slice(0, 520);
      } else if (st === "cancelled" && run.error_message) {
        detail = `${tail} ${humanizeErrorText(run.error_message)}`.trim().slice(0, 360);
      }
      const throughLabel =
        through === "full_video" ? "Full video" : through === "chapters" ? "Through chapter scripts" : "Through story review";
      const pct =
        st === "cancelled"
          ? Math.min(96, Math.max(10, Math.round((stepIndexDisplay / Math.max(1, stepTotal)) * 92)))
          : 100;
      return {
        headline,
        detail,
        pct,
        stepIndexDisplay,
        stepTotal,
        effKey,
        throughLabel,
        statusLabel: friendlyRunStatus(st),
        stepShort: friendlyPipelineStep(effKey),
        iconClassName:
          st === "succeeded"
            ? "fa-solid fa-circle-check fa-fw pipeline-fa-icon"
            : st === "cancelled"
              ? "fa-solid fa-circle-stop fa-fw pipeline-fa-icon pipeline-fa-icon--cancelled"
              : st === "failed"
                ? "fa-solid fa-circle-xmark fa-fw pipeline-fa-icon pipeline-fa-icon--failed"
                : "fa-solid fa-triangle-exclamation fa-fw pipeline-fa-icon",
        trackActive: false,
      };
    }

    if (mediaJobId && mediaJob && ["succeeded", "failed"].includes(mediaJob.status)) {
      const st = mediaJob.status;
      const effKey = inferMacroStepKeyFromJobType(mediaJob.type);
      const base = studioJobKindHeadline(mediaJob.type);
      const headline = `${base} — ${friendlyRunStatus(st)}`;
      const detail = mediaJob.error_message
        ? humanizeErrorText(mediaJob.error_message)
        : st === "succeeded"
          ? "Generation finished. You can run again from the editor anytime."
          : "";
      return {
        headline,
        detail,
        pct: 100,
        stepIndexDisplay: 1,
        stepTotal: 1,
        effKey,
        throughLabel: "Studio",
        statusLabel: friendlyRunStatus(st),
        stepShort: friendlyPipelineStep(effKey),
        iconClassName:
          st === "succeeded"
            ? "fa-solid fa-circle-check fa-fw pipeline-fa-icon"
            : "fa-solid fa-triangle-exclamation fa-fw pipeline-fa-icon",
        trackActive: false,
      };
    }

    return null;
  }, [
    agentRunId,
    run,
    autoThrough,
    runStepGuidance,
    activeProjectJobs,
    agentRunStallInfo,
    projectId,
    mediaJobId,
    mediaJob,
  ]);

  const pipelineActivityRunStatus = useMemo(() => {
    if (run && run.status === "running" && pipelineStopRequested(run.pipeline_control_json)) return "cancelled";
    if (run && ["cancelled", "failed", "succeeded", "blocked"].includes(run.status)) return run.status;
    if (run && ["running", "queued", "paused"].includes(run.status)) return run.status;
    const hasStudio =
      (activeProjectJobs || []).some(
        (j) => j && STUDIO_MEDIA_JOB_TYPES.has(j.type) && ["queued", "running"].includes(j.status),
      ) ||
      (mediaJobId && mediaJob && ["queued", "running"].includes(mediaJob.status));
    if (hasStudio) return "running";
    return run?.status;
  }, [run, activeProjectJobs, mediaJobId, mediaJob]);

  const pipelineStatusWithActivity = useMemo(
    () => mergePipelineStepsWithAgentActivity(pipelineStatus, run, activeProjectJobs),
    [pipelineStatus, run, activeProjectJobs],
  );

  const blocked = run?.status === "blocked";
  const blockedChapterReportHints = useMemo(() => {
    const ids = run?.block_detail_json?.chapter_ids;
    if (!Array.isArray(ids) || !ids.length || !projectCriticReports.length) return [];
    const idset = new Set(ids.map(String));
    return projectCriticReports.filter(
      (rep) => rep.target_type === "chapter" && idset.has(String(rep.target_id)),
    );
  }, [run?.block_detail_json?.chapter_ids, projectCriticReports]);

  const criticGateChapterIds = useMemo(() => {
    const raw = run?.block_detail_json?.chapter_ids;
    const fromGates = (run?.block_detail_json?.failing_gates || []).map((g) => g.chapter_id);
    const ids = [...(Array.isArray(raw) ? raw : []), ...fromGates].filter(Boolean);
    return [...new Set(ids.map(String))];
  }, [run?.block_detail_json]);

  const failedReadinessIssues = useMemo(() => {
    if (run?.status !== "failed") return [];
    const arr = Array.isArray(phase5Ready?.issues) ? phase5Ready.issues : [];
    return arr.filter((x) => x && typeof x === "object");
  }, [run?.status, phase5Ready?.issues]);

  const chapterTitleForId = (cid) => {
    const ch = chapters.find((c) => String(c.id) === String(cid));
    if (!ch) return `Chapter id ${String(cid).slice(0, 8)}…`;
    const n = chapterHumanNumber(chapters, ch);
    return `Chapter ${n ?? "?"}: ${ch.title || "(untitled)"}`;
  };

  const criticReportTargetLabel = (rep) => {
    const tt = rep?.target_type;
    const tid = rep?.target_id;
    if (!tid) return tt || "—";
    if (tt === "project") return "Whole project (story vs research)";
    if (tt === "chapter") return chapterTitleForId(tid);
    if (tt === "scene") {
      const sc = scenes.find((s) => String(s.id) === String(tid));
      if (sc) {
        const ch = chapters.find((c) => String(c.id) === String(sc.chapter_id));
        const chNum = ch ? chapterHumanNumber(chapters, ch) ?? "?" : "?";
        return `Scene ${sc.order_index + 1} · chapter ${chNum}`;
      }
      return `Scene ${String(tid).slice(0, 8)}…`;
    }
    return `${tt} ${String(tid).slice(0, 8)}…`;
  };

  const sceneLabelForId = (sid, chapterIdHint = null) => {
    const sidS = String(sid || "");
    const local = scenes.find((x) => String(x.id) === sidS);
    if (local) return `Scene ${local.order_index + 1}`;
    if (chapterIdHint) return `Scene id ${sidS.slice(0, 8)}… (${chapterTitleForId(chapterIdHint)})`;
    return `Scene id ${sidS.slice(0, 8)}…`;
  };


  const selectedSceneId = expandedScene || scenes[0]?.id || "";
  const selectedScene = scenes.find((s) => String(s.id) === String(selectedSceneId)) || null;

  const uploadSceneClipFile = useCallback(async () => {
    if (!selectedSceneId) return;
    const inp = sceneClipFileInputRef.current;
    const f = inp?.files?.[0];
    if (!f) {
      setError("Choose an image, video, or audio file first.");
      return;
    }
    setBusy(true);
    setError("");
    try {
      const fd = new FormData();
      fd.append("file", f, f.name || "upload");
      fd.append("clip_kind", sceneClipUploadKind);
      const r = await apiForm(`/v1/scenes/${encodeURIComponent(selectedSceneId)}/upload-clip`, {
        method: "POST",
        body: fd,
      });
      const b = await parseJson(r);
      if (!r.ok) throw new Error(apiErrorMessage(b));
      if (inp) inp.value = "";
      void loadSceneAssets(selectedSceneId);
      if (chapterId) void loadPhase3Summary(chapterId);
      if (projectId) void refreshPhase5Readiness({ reportError: false });
      setMessage("Clip uploaded to this scene.");
    } catch (e) {
      setError(formatUserFacingError(e));
    } finally {
      setBusy(false);
    }
  }, [
    selectedSceneId,
    sceneClipUploadKind,
    loadSceneAssets,
    chapterId,
    loadPhase3Summary,
    projectId,
    refreshPhase5Readiness,
  ]);

  const enhanceRetryImagePrompt = useCallback(async () => {
    const sid = String(selectedSceneId || "").trim();
    if (!sid) return;
    const current = String(retryPrompt || "").trim();
    if (!current.length) {
      setError("Add some text to the image prompt first, then use Improve prompt.");
      return;
    }
    setPromptEnhanceImageBusy(true);
    setError("");
    try {
      const r = await api(`/v1/scenes/${encodeURIComponent(sid)}/prompt-enhance-image`, {
        method: "POST",
        body: JSON.stringify({ current_prompt: current }),
      });
      const b = await parseJson(r);
      if (!r.ok) throw new Error(apiErrorMessage(b));
      const text = b.data?.text;
      if (typeof text !== "string" || !String(text).trim()) throw new Error("No improved text returned.");
      setRetryPrompt(String(text).trim());
      setMessage("Image prompt improved with previous scene + character context.");
    } catch (e) {
      setError(formatUserFacingError(e));
    } finally {
      setPromptEnhanceImageBusy(false);
    }
  }, [selectedSceneId, retryPrompt]);

  const enhanceSceneVoFromStyle = useCallback(async () => {
    const sid = String(selectedSceneId || "").trim();
    if (!sid) return;
    const current = String(sceneNarrationDraft || "").trim();
    if (!current.length) {
      setError("Add narration text first, then use Improve VO.");
      return;
    }
    setPromptEnhanceVoBusy(true);
    setError("");
    try {
      const r = await api(`/v1/scenes/${encodeURIComponent(sid)}/prompt-enhance-vo`, {
        method: "POST",
        body: JSON.stringify({ current_script: current }),
      });
      const b = await parseJson(r);
      if (!r.ok) throw new Error(apiErrorMessage(b));
      const text = b.data?.text;
      if (typeof text !== "string" || !String(text).trim()) throw new Error("No improved text returned.");
      setSceneNarrationDraft(String(text).trim());
      setSceneNarrationDirty(true);
      setMessage("Narration rewritten to match project narration style.");
    } catch (e) {
      setError(formatUserFacingError(e));
    } finally {
      setPromptEnhanceVoBusy(false);
    }
  }, [selectedSceneId, sceneNarrationDraft]);

  const expandSceneVoScript = useCallback(async () => {
    const sid = String(selectedSceneId || "").trim();
    if (!sid) return;
    const current = String(sceneNarrationDraft || "").trim();
    if (!current.length) {
      setError("Add narration text first, then use Expand script.");
      return;
    }
    const n = Math.min(40, Math.max(1, Number(sceneVoExpandSentenceTarget) || 6));
    setPromptExpandVoBusy(true);
    setError("");
    try {
      const payload = {
        current_script: current,
        target_sentence_count: n,
      };
      const ctx = String(sceneVoExpandContext || "").trim();
      if (ctx) payload.expansion_context = ctx;
      const r = await api(`/v1/scenes/${encodeURIComponent(sid)}/prompt-expand-vo`, {
        method: "POST",
        body: JSON.stringify(payload),
      });
      const b = await parseJson(r);
      if (!r.ok) throw new Error(apiErrorMessage(b));
      const text = b.data?.text;
      if (typeof text !== "string" || !String(text).trim()) throw new Error("No expanded text returned.");
      setSceneNarrationDraft(String(text).trim());
      setSceneNarrationDirty(true);
      setMessage("Narration expanded. Review and save if it reads well.");
    } catch (e) {
      setError(formatUserFacingError(e));
    } finally {
      setPromptExpandVoBusy(false);
    }
  }, [selectedSceneId, sceneNarrationDraft, sceneVoExpandSentenceTarget, sceneVoExpandContext]);

  const selectedSceneAssetRows = selectedSceneId ? sceneAssets[selectedSceneId] || [] : [];
  const gallerySceneAssets = useMemo(() => {
    const rows = (selectedSceneAssetRows || []).filter((a) => a.status !== "rejected");
    return [...rows].sort((a, b) => {
      const sa = Number(a.timeline_sequence ?? 0);
      const sb = Number(b.timeline_sequence ?? 0);
      if (sa !== sb) return sa - sb;
      return new Date(a.created_at || 0) - new Date(b.created_at || 0);
    });
  }, [selectedSceneAssetRows]);
  const [selectedAssetIds, setSelectedAssetIds] = useState(() => new Set());
  // Reset selection when the scene changes
  const toggleAssetSelected = useCallback((id) => {
    const key = String(id);
    setSelectedAssetIds((prev) => {
      const next = new Set(Array.from(prev, String));
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });
  }, []);
  const selectAllAssets = useCallback(() => {
    setSelectedAssetIds(new Set(gallerySceneAssets.map((a) => String(a.id))));
  }, [gallerySceneAssets]);
  const rejectAllAssets = useCallback(async () => {
    const ids = gallerySceneAssets.map((a) => String(a.id));
    if (!ids.length) return;
    const ok = window.confirm(
      `Reject all ${ids.length} asset(s) for this scene? They will be hidden from the gallery (same as Reject on each card).`,
    );
    if (!ok) return;
    setSelectedAssetIds(new Set());
    setError("");
    let n = 0;
    for (const id of ids) {
      if (await rejectAsset(id, { quiet: true })) n += 1;
    }
    if (n > 0) setMessage(`Rejected ${n} asset(s).`);
  }, [gallerySceneAssets, rejectAsset]);
  const clearAssetSelection = useCallback(() => setSelectedAssetIds(new Set()), []);
  const bulkApproveAssets = useCallback(async () => {
    const ids = Array.from(selectedAssetIds, String);
    if (!ids.length) return;
    // Clear selection immediately so any new selections made during the async loop are not lost.
    setSelectedAssetIds(new Set());
    setError("");
    let n = 0;
    for (const id of ids) {
      if (await approveAsset(id, { quiet: true })) n += 1;
    }
    if (n > 0) setMessage(`Approved ${n} asset(s).`);
  }, [selectedAssetIds, approveAsset]);
  const bulkRejectAssets = useCallback(async () => {
    const ids = Array.from(selectedAssetIds, String);
    if (!ids.length) return;
    // Clear selection immediately so any new selections made during the async loop are not lost.
    setSelectedAssetIds(new Set());
    setError("");
    let n = 0;
    for (const id of ids) {
      if (await rejectAsset(id, { quiet: true })) n += 1;
    }
    if (n > 0) setMessage(`Rejected ${n} asset(s).`);
  }, [selectedAssetIds, rejectAsset]);

  // Reset bulk selection when scene changes
  useEffect(() => { setSelectedAssetIds(new Set()); }, [selectedSceneId]);

  useEffect(() => {
    setSceneVoExpandContext("");
  }, [selectedSceneId]);

  const timelineTotalSec = scenes.reduce((acc, s) => acc + Number(s.planned_duration_sec || 0), 0);
  const sceneClipSec = Number(appConfig.scene_clip_duration_sec) === 5 ? 5 : 10;
  const scenesOrdered = useMemo(
    () => [...(scenes || [])].sort((a, b) => (Number(a.order_index) || 0) - (Number(b.order_index) || 0)),
    [scenes],
  );
  const sceneNarrationGuideMap = useMemo(() => {
    const d =
      chapterNarration?.has_audio && chapterNarration.duration_sec != null
        ? Number(chapterNarration.duration_sec)
        : null;
    return buildSceneNarrationGuide(scenesOrdered, d, sceneClipSec);
  }, [scenesOrdered, chapterNarration, sceneClipSec]);
  const chapterNarrClipHintTotal = useMemo(() => {
    if (!chapterNarration?.has_audio || chapterNarration.duration_sec == null) return null;
    const d = Number(chapterNarration.duration_sec);
    if (!Number.isFinite(d) || d <= 0) return null;
    return Math.max(1, Math.ceil(d / sceneClipSec));
  }, [chapterNarration?.has_audio, chapterNarration?.duration_sec, sceneClipSec]);
  const selectedNarrGuide = selectedSceneId ? sceneNarrationGuideMap.get(String(selectedSceneId)) : null;
  const selectedCoveredSec = useMemo(() => {
    if (!selectedSceneId) return 0;
    const rows = (sceneAssets[selectedSceneId] || []).filter(
      (a) => a.status === "succeeded" || a.status === "approved",
    );
    return rows.reduce((acc, a) => acc + estAssetCoverSec(a, sceneClipSec), 0);
  }, [selectedSceneId, sceneAssets, sceneClipSec]);
  const selectedNarrProgressPct =
    selectedNarrGuide && selectedNarrGuide.narrationSec > 0
      ? Math.min(100, (selectedCoveredSec / selectedNarrGuide.narrationSec) * 100)
      : 0;
  const bestPreviewAsset = useMemo(() => {
    const rows = (selectedSceneAssetRows || []).filter((a) => a.status !== "rejected");
    if (pinnedPreviewAssetId) {
      const pinned = rows.find((r) => String(r.id) === String(pinnedPreviewAssetId));
      if (pinned && pinned.status === "succeeded") {
        return pinned;
      }
    }
    rows.sort((a, b) => {
      const as = a.status === "succeeded" ? 1 : 0;
      const bs = b.status === "succeeded" ? 1 : 0;
      if (bs !== as) return bs - as;
      const seq = Number(a.timeline_sequence ?? 0) - Number(b.timeline_sequence ?? 0);
      if (seq !== 0) return seq;
      const ta = new Date(a.created_at || 0).getTime();
      const tb = new Date(b.created_at || 0).getTime();
      return ta - tb;
    });
    // Only succeeded assets have on-disk bytes; requesting content for running/failed rows 404s the preview.
    const firstSucceeded = rows.find((r) => r.status === "succeeded");
    return firstSucceeded || null;
  }, [selectedSceneAssetRows, pinnedPreviewAssetId]);

  const moveSceneAssetInSequence = (index, delta) => {
    if (!selectedSceneId) return;
    const next = [...gallerySceneAssets];
    const j = index + delta;
    if (j < 0 || j >= next.length) return;
    [next[index], next[j]] = [next[j], next[index]];
    void reorderSceneAssets(selectedSceneId, next.map((a) => a.id));
  };
  const previewUrl = useMemo(() => {
    if (!bestPreviewAsset?.id) return "";
    const v = bestPreviewAsset.updated_at || bestPreviewAsset.created_at || bestPreviewAsset.id;
    return apiAssetContentUrl(bestPreviewAsset.id, v);
  }, [bestPreviewAsset]);

  useEffect(() => {
    setPreviewMediaError(false);
  }, [previewUrl]);

  const previewKind = (bestPreviewAsset?.asset_type || "").toLowerCase();

  const narrationPreviewSrc = useMemo(() => {
    const sid = String(selectedSceneId || "");
    const sm = sceneNarrationMeta;
    if (sid && sm && sm.has_audio && (!sm.scene_id || String(sm.scene_id) === sid)) {
      const t = sm.created_at || sm.track_id || "";
      return apiSceneNarrationContentUrl(sid, t);
    }
    return "";
  }, [selectedSceneId, sceneNarrationMeta]);

  const narrationPreviewIsSceneTrack = useMemo(() => {
    const sid = String(selectedSceneId || "");
    const sm = sceneNarrationMeta;
    return Boolean(
      sid && sm && sm.has_audio && (!sm.scene_id || String(sm.scene_id) === sid),
    );
  }, [selectedSceneId, sceneNarrationMeta]);

  useEffect(() => {
    if (!dragState) return undefined;
    const onMove = (ev) => {
      if (!workspaceRef.current) return;
      const rect = workspaceRef.current.getBoundingClientRect();
      if (dragState.type === "left") {
        const left = Math.max(240, Math.min(520, ev.clientX - rect.left));
        setPanelSizes((p) => ({ ...p, left }));
      } else if (dragState.type === "right") {
        const right = Math.max(280, Math.min(560, rect.right - ev.clientX));
        setPanelSizes((p) => ({ ...p, right }));
      } else if (dragState.type === "bottom") {
        const y = Math.max(150, Math.min(400, rect.bottom - ev.clientY));
        setPanelSizes((p) => ({ ...p, bottom: y }));
      }
    };
    const onUp = () => setDragState(null);
    window.addEventListener("mousemove", onMove);
    window.addEventListener("mouseup", onUp);
    return () => {
      window.removeEventListener("mousemove", onMove);
      window.removeEventListener("mouseup", onUp);
    };
  }, [dragState]);

  const reorderScenes = (fromId, toId) => {
    if (!fromId || !toId || fromId === toId) return;
    setScenes((prev) => {
      const from = prev.findIndex((s) => s.id === fromId);
      const to = prev.findIndex((s) => s.id === toId);
      if (from < 0 || to < 0) return prev;
      const next = [...prev];
      const [m] = next.splice(from, 1);
      next.splice(to, 0, m);
      return next.map((s, i) => ({ ...s, order_index: i }));
    });
  };

  const openProject = async (pid, restore = null) => {
    const id = sanitizeStudioUuid(pid);
    if (!id) return;
    setBusy(true);
    setError("");
    setProjectId(id);
    const keepAgent =
      Boolean(restore?.agentRunId && String(restore.agentRunId).length >= 32);
    if (!keepAgent) {
      setAgentRunId("");
      setRun(null);
    }
    setChapterId("");
    setScenes([]);
    setExpandedScene(null);
    setPinnedPreviewAssetId(null);
    setMediaPreviewTab("scene");
    setSceneAssets({});
    setTrimByScene({});
    setTimelineVersionId(
      restore?.timelineVersionId && String(restore.timelineVersionId).trim()
        ? restore.timelineVersionId
        : "",
    );
    setMediaJobId("");
    setMediaPoll(false);
    setCharactersJobId("");
    setLastHandledMediaJobId("");
    setRetryPrompt("");
    setRetryVideoPrompt("");
    retryPromptSceneRef.current = null;
    retryVideoPromptSceneRef.current = null;
    setPhase3Summary(null);
    setCriticReport(null);
    try {
      const pe = encodeURIComponent(id);
      const pr = await api(`/v1/projects/${pe}`);
      const pb = await parseJson(pr);
      if (!pr.ok) {
        const msg = apiErrorMessage(pb);
        const hint404 =
          pr.status === 404
            ? " Wrong workspace? Open the tenant where this project was created (workspace / account switcher), then choose the project again."
            : "";
        throw new Error(`${msg}${hint404}`);
      }
      const p = pb.data || {};
      setTitle(p.title || "");
      setTopic(p.topic || "");
      setRuntime(Number(p.target_runtime_minutes || 15));
      setFrameAspectRatio(p.frame_aspect_ratio === "9:16" ? "9:16" : "16:9");
      setUseAllApprovedSceneMedia(Boolean(p.use_all_approved_scene_media));

      const cr = await api(`/v1/projects/${pe}/chapters`);
      const cb = await parseJson(cr);
      if (!cr.ok) {
        throw new Error(apiErrorMessage(cb) || `Chapters request failed (HTTP ${cr.status})`);
      }
      const nextChapters = cb.data?.chapters || [];
      setChapters(nextChapters);

      const savedCid =
        restore?.chapterId && nextChapters.some((c) => c.id === restore.chapterId)
          ? restore.chapterId
          : "";
      const pickChapter = savedCid || nextChapters[0]?.id || "";
      setChapterId(pickChapter);
      if (pickChapter) {
        const sr = await api(`/v1/chapters/${pickChapter}/scenes`);
        const sb = await parseJson(sr);
        if (sr.ok) {
          const nextScenes = sb.data?.scenes || [];
          setScenes(nextScenes);
          const savedSid =
            restore?.expandedScene && nextScenes.some((s) => s.id === restore.expandedScene)
              ? restore.expandedScene
              : null;
          const pickScene = savedSid || nextScenes[0]?.id || null;
          if (pickScene) {
            setExpandedScene(pickScene);
            loadSceneAssets(pickScene);
          }
        }
      }

      await refreshPhase5Readiness({ pid: id });

      if (keepAgent) {
        setAgentRunId(restore.agentRunId);
        /* Do not clear run — it drops the progress banner until the next poll tick after refresh. */
      }

      try {
        const jr = await api(`/v1/projects/${pe}/jobs/active`);
        const jb = await parseJson(jr);
        if (jr.ok) {
          const jobs = jb.data?.jobs || [];
          const rid = restore && typeof restore === "object" ? restore : {};
          const charJobs = jobs.filter((j) => j.type === "characters_generate");
          const prefChar =
            rid.charactersJobId && charJobs.some((j) => j.id === rid.charactersJobId)
              ? rid.charactersJobId
              : charJobs[0]?.id;
          if (prefChar) setCharactersJobId(prefChar);
          const mediaJobs = jobs.filter((j) => STUDIO_MEDIA_JOB_TYPES.has(j.type));
          const prefMedia =
            rid.mediaJobId && mediaJobs.some((j) => j.id === rid.mediaJobId)
              ? rid.mediaJobId
              : mediaJobs[0]?.id;
          if (prefMedia) {
            setMediaJobId(prefMedia);
            setMediaPoll(true);
          }
        }
      } catch {
        /* ignore — non-fatal */
      }
    } catch (e) {
      const em = formatUserFacingError(e);
      setError(em);
      setProjectId("");
      setChapters([]);
      setChapterId("");
      setScenes([]);
      setExpandedScene(null);
      setTitle("");
      setTopic("");
    } finally {
      setBusy(false);
    }
  };

  openProjectRef.current = openProject;

  /** Keep main Studio ``projectId`` in sync when the user picks a production on the Chat rail. */
  const onChatStudioProjectOpen = useCallback((id) => {
    const clean = sanitizeStudioUuid(id);
    if (!clean || clean === String(projectId || "").trim()) return;
    void openProjectRef.current(clean);
  }, [projectId]);

  /** Restore open project from `director_ui_session` only after auth bootstrap (avoids 401 / "missing credentials" vs project load). */
  useEffect(() => {
    if (!authBootstrap.done) return;
    if (sessionRestoreStartedRef.current) return;
    sessionRestoreStartedRef.current = true;

    if (authBootstrap.mode === "saas" && authBootstrap.needLogin) {
      setUiSessionReady(true);
      return;
    }

    const session = readDirectorUiSession();
    const pid = session?.projectId?.trim();
    const rid = session?.agentRunId?.trim();
    const runIdOk = Boolean(rid && rid.length >= 32);

    if (!pid && !runIdOk) {
      setUiSessionReady(true);
      return;
    }

    void (async () => {
      try {
        let effective = session;
        if (!pid && runIdOk && rid) {
          const rr = await api(`/v1/agent-runs/${rid}`);
          const rb = await parseJson(rr);
          if (!rr.ok || !rb?.data?.project_id) {
            try {
              localStorage.removeItem(DIRECTOR_UI_SESSION_KEY);
            } catch {
              /* ignore */
            }
            return;
          }
          effective = {
            ...session,
            projectId: String(rb.data.project_id),
          };
          setProjectId(String(rb.data.project_id));
        }

        const projectIdToOpen = effective?.projectId?.trim();
        if (!projectIdToOpen) {
          return;
        }

        const r = await api(`/v1/projects/${projectIdToOpen}`);
        if (!r.ok) {
          try {
            localStorage.removeItem(DIRECTOR_UI_SESSION_KEY);
          } catch {
            /* ignore */
          }
          return;
        }
        await openProjectRef.current(projectIdToOpen, effective);
      } catch {
        /* ignore */
      } finally {
        setUiSessionReady(true);
      }
    })();
  }, [authBootstrap.done, authBootstrap.mode, authBootstrap.needLogin]);

  useEffect(() => {
    if (!uiSessionReady) return;
    try {
      localStorage.setItem(
        DIRECTOR_UI_SESSION_KEY,
        JSON.stringify({
          activePage,
          projectId,
          chapterId,
          agentRunId,
          expandedScene: expandedScene || "",
          timelineVersionId,
          mediaJobId: mediaJobId || "",
          charactersJobId: charactersJobId || "",
        }),
      );
    } catch {
      /* ignore */
    }
  }, [
    uiSessionReady,
    activePage,
    projectId,
    chapterId,
    agentRunId,
    expandedScene,
    timelineVersionId,
    mediaJobId,
    charactersJobId,
  ]);

  useEffect(() => {
    if (activePage !== "characters" || !projectId) return;
    void loadProjectCharacters(projectId);
  }, [activePage, projectId, loadProjectCharacters]);

  useEffect(() => {
    if (activePage !== "research_chapters" || !projectId) return;
    void loadResearchChaptersEditor(projectId);
  }, [activePage, projectId, loadResearchChaptersEditor]);

  useEffect(() => {
    if (!charactersJobId || !charactersJob) return;
    const st = charactersJob.status;
    if (st !== "succeeded" && st !== "failed") return;
    setCharactersJobId("");
    if (st === "succeeded" && projectId) {
      void loadProjectCharacters(projectId);
      setMessage("Character bible updated from story.");
    } else if (st === "failed") {
      setError(humanizeErrorText(charactersJob.error_message || "Character generation failed."));
    }
  }, [charactersJob, charactersJobId, projectId, loadProjectCharacters]);

  const deleteProject = async (pid) => {
    if (!pid) return;
    const ok = window.confirm(
      "Delete this project? This removes it from the studio and deletes generated media on disk (scene assets, narrations). Files under the exports folder for this project are kept. This cannot be undone.",
    );
    if (!ok) return;
    setBusy(true);
    setError("");
    try {
      const r = await api(`/v1/projects/${pid}`, { method: "DELETE" });
      const body = await parseJson(r);
      if (!r.ok) {
        throw new Error(apiErrorMessage(body) || "delete failed");
      }
      if (projectId === pid) {
        setProjectId("");
        setAgentRunId("");
        setRun(null);
        setChapters([]);
        setChapterId("");
        setScenes([]);
        setExpandedScene(null);
        setPinnedPreviewAssetId(null);
        setSceneAssets({});
        setPhase3Summary(null);
        setCriticReport(null);
        setPhase5Ready(null);
        setTimelineVersionId("");
        setProjectCharacters([]);
      }
      await loadProjects();
      setMessage("Project deleted.");
    } catch (e) {
      setError(formatUserFacingError(e));
    } finally {
      setBusy(false);
    }
  };

  /** Clear the open project and jump to Pipeline → Project brief to start another documentary. */
  const startNewProjectDraft = useCallback(() => {
    setError("");
    setMessage("");
    setActivePage("editor");
    setProjectId("");
    setAgentRunId("");
    setRun(null);
    setChapterId("");
    setScenes([]);
    setExpandedScene(null);
    setPinnedPreviewAssetId(null);
    setSceneAssets({});
    setTrimByScene({});
    setTimelineVersionId("");
    setMediaJobId("");
    setMediaPoll(false);
    setCharactersJobId("");
    setLastHandledMediaJobId("");
    setRetryPrompt("");
    setRetryVideoPrompt("");
    retryPromptSceneRef.current = null;
    retryVideoPromptSceneRef.current = null;
    setPhase3Summary(null);
    setCriticReport(null);
    setProjectCriticReports([]);
    setCriticListError("");
    setPhase5Ready(null);
    setPipelineStatus(null);
    setActiveProjectJobs([]);
    setActiveJobsLoadErr("");
    setProjectCharacters([]);
    setTitle("New documentary");
    setTopic("Describe your topic, audience, and the story you want to tell.");
    setRuntime(15);
    setFrameAspectRatio("16:9");
    queueMicrotask(() => {
      document.getElementById("studio-pipeline-panel")?.scrollIntoView({ behavior: "smooth", block: "start" });
    });
  }, []);

  if (!authBootstrap.done) {
    return (
      <div className="app-shell" data-testid="director-app-root">
        <p className="subtle" style={{ padding: 24 }}>
          Loading…
        </p>
      </div>
    );
  }

  if (authBootstrap.needLogin) {
    return (
      <StudioAuthPanel
        allowRegistration={authBootstrap.allowRegistration}
        onLoggedIn={onSaaSLoggedIn}
      />
    );
  }

  return (
    <EditorLayoutProvider>
    <div className="app-shell" data-testid="director-app-root">
      <header className="topbar topbar--compact panel">
        <div className="topbar-leading">
          <button
            type="button"
            className="secondary mobile-nav-toggle"
            aria-label={mobileNavOpen ? "Close menu" : "Open menu"}
            aria-expanded={mobileNavOpen}
            aria-controls="studio-primary-nav"
            onClick={() => setMobileNavOpen((o) => !o)}
          >
            <i className="fa-solid fa-bars" aria-hidden="true" />
          </button>
          <div className="topbar-brand">
            <div className="studio-brand studio-brand--topbar">
              <img
                src="/images/directely-logo.png"
                alt=""
                width={40}
                height={40}
                className="studio-brand__mark"
                decoding="async"
              />
              <h1 className="studio-brand__heading">
                <span className="studio-brand__wordmark">Directely</span>
                <span className="studio-brand__suffix"> Studio</span>
              </h1>
            </div>
          {authBootstrap.mode === "saas" && saasTenants.length > 1 ? (
            <label className="subtle topbar-saas-workspace">
              Workspace
              <select
                value={String(
                  accountProfile?.active_tenant_id || accountProfile?.tenant_id || getDirectorTenantId() || "",
                ).trim()}
                onChange={(e) => {
                  const v = e.target.value;
                  void (async () => {
                    try {
                      const r = await api("/v1/auth/session-tenant", {
                        method: "POST",
                        body: JSON.stringify({ tenant_id: v }),
                      });
                      const b = await parseJson(r);
                      setDirectorAuthSession({ tenantId: v });
                    } catch {
                      setDirectorAuthSession({ tenantId: v });
                    }
                    setEventAuthKey((k) => k + 1);
                    window.location.reload();
                  })();
                }}
              >
                {saasTenants.map((t) => (
                  <option key={t.id} value={t.id}>
                    {t.name || t.id}
                  </option>
                ))}
              </select>
            </label>
          ) : null}
          </div>
        </div>
        {authBootstrap.mode === "saas" ? (
          <div className="topbar-actions">
            <button type="button" className="secondary" onClick={() => setUpgradeModalOpen(true)}>
              Upgrade
            </button>
            <button
              type="button"
              className="secondary"
              onClick={() => {
                setActivePage("account");
              }}
            >
              Account
            </button>
            <button type="button" className="secondary" onClick={() => signOutSaas()}>
              Sign out
            </button>
          </div>
        ) : null}
      </header>

      <StudioUpgradeModal
        open={upgradeModalOpen}
        onClose={() => setUpgradeModalOpen(false)}
        showToast={showToast}
        activePlanSlug={billingActivePlanSlug}
      />

      {headerProgressBanner ? (
        <div
          className={`pipeline-progress-alert panel${headerProgressBanner.stallAlert ? " pipeline-progress-alert--stall" : ""}`}
          role="status"
          aria-live="polite"
          aria-atomic="true"
        >
          <div className="pipeline-progress-alert__top">
            <div className="pipeline-progress-alert__icon-wrap" aria-hidden="true">
              <i className={headerProgressBanner.iconClassName} />
            </div>
            <div className="pipeline-progress-alert__body">
              <div className="pipeline-progress-alert__row">
                <div className="pipeline-progress-alert__titles">
                  <strong className="pipeline-progress-alert__headline">{headerProgressBanner.headline}</strong>
                  <span className="pipeline-progress-alert__through subtle">{headerProgressBanner.throughLabel}</span>
                </div>
                <div className="pipeline-progress-alert__badges">
                  <span className="chip chip--with-fa">
                    <i className="fa-solid fa-bolt fa-fade fa-fw" style={{ marginRight: 5 }} aria-hidden="true" />
                    {headerProgressBanner.statusLabel}
                  </span>
                  <span className="chip mono chip--with-fa">
                    <i className="fa-solid fa-flag-checkered fa-beat-fade fa-fw" style={{ marginRight: 5 }} aria-hidden="true" />
                    Step {headerProgressBanner.stepIndexDisplay} / {headerProgressBanner.stepTotal}
                  </span>
                </div>
              </div>
            </div>
          </div>
          {headerProgressBanner.detail ? (
            <p className="pipeline-progress-alert__detail subtle">{headerProgressBanner.detail}</p>
          ) : null}
          <div
            className={
              headerProgressBanner.trackActive
                ? "pipeline-progress-track pipeline-progress-track--active"
                : "pipeline-progress-track"
            }
            aria-hidden="true"
          >
            <div className="pipeline-progress-fill" style={{ width: `${headerProgressBanner.pct}%` }} />
          </div>
        </div>
      ) : null}

      {message ? <p className="app-toast app-toast--ok">{message}</p> : null}
      {error ? <p className="err">{error}</p> : null}

      {phase5ExportGateModal ? (
        <div
          className="restart-automation-modal-backdrop"
          role="presentation"
          onClick={dismissPhase5ExportGateModal}
        >
          <div
            className="panel phase5-export-gate-modal"
            role="dialog"
            aria-modal="true"
            aria-labelledby="phase5-export-gate-title"
            onClick={(e) => e.stopPropagation()}
            onKeyDown={(e) => {
              if (e.key === "Escape") dismissPhase5ExportGateModal();
            }}
          >
            <h3 id="phase5-export-gate-title">{phase5ExportGateModal.jobLabel} blocked</h3>
            <p className="subtle" style={{ marginTop: 8 }}>
              Export can&apos;t run until the checklist passes. Review highlighted scenes and approve media there, or use{" "}
              <strong>Approve all</strong> for every succeeded image/video that still lacks approval. Run <strong>Check readiness</strong> again.
              If the problem is <em>which asset the timeline points at</em> (not only approval), use{" "}
              <strong>Reconcile timeline clips</strong> — it does not replace bulk approve. Use{" "}
              <strong>Reject &amp; regen flagged stills</strong> only when those stills are wrong and need new scene images.
            </p>
            {phase5ExportGateModal.summaryBullets?.length ? (
              <ul>
                {phase5ExportGateModal.summaryBullets.map((line, idx) => (
                  <li key={idx}>{line}</li>
                ))}
              </ul>
            ) : null}
            <ExportAttentionTimelineAssetsBlock
              rows={phase5Ready?.export_attention_timeline_assets}
              busy={busy}
              onOpenScene={openSceneForTimelineAttentionAsset}
              onReconcile={reconcileTimelineClipImages}
              reconcileDisabled={!projectId || !String(timelineVersionId || "").trim()}
            />
            <div className="restart-automation-modal-actions" style={{ marginTop: 14, flexWrap: "wrap", gap: 8 }}>
              <button type="button" className="secondary" onClick={dismissPhase5ExportGateModal}>
                Close
              </button>
              <button type="button" className="secondary" onClick={() => void reviewScenesForExportGate()}>
                Review scenes
              </button>
              {phase5ExportGateModal.offerBulkApprove ? (
                <button
                  type="button"
                  disabled={approveAllMediaBusy || !projectId}
                  onClick={() => void approveAllSucceededMediaForExport()}
                  title="Mark every succeeded image and video in this project as approved"
                >
                  {approveAllMediaBusy ? "Approving…" : "Approve all"}
                </button>
              ) : null}
              <button
                type="button"
                className="secondary"
                disabled={busy || !projectId || !String(timelineVersionId || "").trim()}
                onClick={() => void reconcileTimelineClipImages()}
                title="Relink timeline clips to viable scene media, sync storyboard order, and related fixes"
              >
                Reconcile timeline clips
              </button>
              <button
                type="button"
                className="secondary"
                disabled={busy || !projectId || !String(timelineVersionId || "").trim()}
                onClick={() => void rejectAndRegenerateRoughCutImages()}
                title="Destructive: reject flagged rough-cut stills and queue new scene image jobs per scene"
              >
                Reject &amp; regen flagged stills
              </button>
            </div>
          </div>
        </div>
      ) : null}

      <div className={`app-shell__body${mobileNavOpen ? " app-shell__body--mobile-nav-open" : ""}`}>
        <div
          className={`mobile-nav-backdrop${mobileNavOpen ? " mobile-nav-backdrop--visible" : ""}`}
          aria-hidden="true"
          onClick={() => setMobileNavOpen(false)}
        />
        <nav
          id="studio-primary-nav"
          className={`studio-page-rail studio-page-rail--drawer panel${mobileNavOpen ? " studio-page-rail--open" : ""}`}
          aria-label="Primary pages"
          aria-hidden={isMobileLayout ? !mobileNavOpen : undefined}
        >
          <div className="studio-page-rail__drawer-head">
            <span className="studio-page-rail__drawer-title">Menu</span>
            <button
              type="button"
              className="secondary studio-page-rail__drawer-close"
              aria-label="Close menu"
              onClick={() => setMobileNavOpen(false)}
            >
              <i className="fa-solid fa-xmark" aria-hidden="true" />
            </button>
          </div>
          {studioPageRails.map((tab) => (
            <button
              key={tab.id}
              type="button"
              className={`studio-page-rail__tab${activePage === tab.id ? " studio-page-rail__tab--active" : ""}`}
              onClick={() => {
                setActivePage(tab.id);
                setMobileNavOpen(false);
              }}
              aria-current={activePage === tab.id ? "page" : undefined}
            >
              <span className="studio-page-rail__tab-label">{tab.label}</span>
            </button>
          ))}
        </nav>
        <div className="app-shell__main">
      <StudioPanelErrorBoundary resetKey={activePage}>
      {activePage === "terms" || activePage === "privacy" || activePage === "copyright" ? (
        <StudioLegalPage docId={activePage} setActivePage={setActivePage} />
      ) : activePage === "chat" ? (
        <ChatStudioPage
          appConfig={appConfig}
          stylePresets={stylePresets}
          projects={projects}
          onReloadProjects={() => void loadProjects()}
          studioProjectId={projectId}
          onStudioProjectOpen={onChatStudioProjectOpen}
        />
      ) : activePage === "account" ? (
        <StudioAccountPage
          authMode={authBootstrap.mode}
          accountProfile={accountProfile}
          onRefreshProfile={refreshAccountProfile}
          onSignOut={signOutSaas}
          showToast={showToast}
        />
      ) : activePage === "admin" ? (
        <StudioAdminPage showToast={showToast} workspaceTenantId={adminToolsWorkspaceTenantId} />
      ) : activePage === "usage" ? (
        <section className="panel usage-page">
          <header className="usage-page-header">
            <div>
              <h2>Usage</h2>
              <p className="subtle">
                LLM token totals by model for this workspace (tenant). Costs are rough estimates from built-in price hints — verify against
                your provider invoices.
              </p>
              {usageErr ? <p className="err usage-page-error">{usageErr}</p> : null}
            </div>
            <div className="usage-page-toolbar">
              <label htmlFor="usage-range" className="usage-range-label">
                Period
              </label>
              <select
                id="usage-range"
                value={String(usageDays)}
                disabled={usageLoading}
                onChange={(e) => setUsageDays(Number(e.target.value))}
              >
                <option value="7">Last 7 days</option>
                <option value="30">Last 30 days</option>
                <option value="90">Last 90 days</option>
              </select>
              <button type="button" className="secondary" disabled={usageLoading} onClick={() => loadUsageSummary(usageDays)}>
                Refresh
              </button>
            </div>
          </header>

          {usageSummary ? (
            <>
              <div className="usage-totals">
                <div className="usage-total-card">
                  <span className="usage-total-label">Total tokens</span>
                  <strong>{(usageSummary.totals?.total_tokens ?? 0).toLocaleString()}</strong>
                  <span className="subtle">
                    in {(usageSummary.totals?.prompt_tokens ?? 0).toLocaleString()} · out{" "}
                    {(usageSummary.totals?.completion_tokens ?? 0).toLocaleString()}
                  </span>
                </div>
                <div className="usage-total-card">
                  <span className="usage-total-label">Est. cost (USD)</span>
                  <strong>
                    {new Intl.NumberFormat("en-US", {
                      style: "currency",
                      currency: "USD",
                      minimumFractionDigits: 2,
                      maximumFractionDigits: 4,
                    }).format(Number(usageSummary.totals?.estimated_cost_usd ?? 0))}
                  </strong>
                  <span className="subtle">{usageSummary.totals?.llm_calls ?? 0} LLM calls recorded</span>
                </div>
                <div className="usage-total-card">
                  <span className="usage-total-label">Directely credits</span>
                  <strong>{Number(usageSummary.totals?.director_credits ?? 0).toLocaleString(undefined, { maximumFractionDigits: 2 })}</strong>
                  <span className="subtle">
                    LLM {Number(usageSummary.totals?.llm_credits ?? 0).toLocaleString(undefined, { maximumFractionDigits: 1 })} · all modalities in range
                  </span>
                </div>
              </div>

              {usageSummary.models?.length ? (
                <div className="usage-chart-section">
                  <h3 className="usage-section-title">Tokens by model</h3>
                  <div className="usage-bar-chart" aria-label="Token usage by model">
                    {(() => {
                      const rows = usageSummary.models;
                      const maxT = Math.max(...rows.map((m) => m.total_tokens || 0), 1);
                      return rows.map((m) => (
                        <div key={`${m.provider}:${m.model}`} className="usage-bar-row">
                          <div className="usage-bar-meta">
                            <span className="usage-bar-model">{m.model}</span>
                            <span className="usage-bar-provider">{m.provider}</span>
                          </div>
                          <div className="usage-bar-track">
                            <div
                              className="usage-bar-fill"
                              style={{ width: `${(100 * (m.total_tokens || 0)) / maxT}%` }}
                              title={`${(m.total_tokens || 0).toLocaleString()} tokens`}
                            />
                          </div>
                          <div className="usage-bar-count">{(m.total_tokens || 0).toLocaleString()}</div>
                        </div>
                      ));
                    })()}
                  </div>
                </div>
              ) : (
                <p className="subtle usage-empty">No LLM token records in this period yet. Run scripts, scene planning, or critics to populate usage.</p>
              )}

              {usageSummary.models?.length ? (
                <div className="usage-table-wrap">
                  <h3 className="usage-section-title">Cost breakdown</h3>
                  <table className="usage-table">
                    <thead>
                      <tr>
                        <th>Model</th>
                        <th>Provider</th>
                        <th>Input tok</th>
                        <th>Output tok</th>
                        <th>Calls</th>
                        <th>Est. USD</th>
                        <th>Credits</th>
                      </tr>
                    </thead>
                    <tbody>
                      {usageSummary.models.map((m) => (
                        <tr key={`${m.provider}:${m.model}:row`}>
                          <td>{m.model}</td>
                          <td>{m.provider}</td>
                          <td>{(m.prompt_tokens ?? 0).toLocaleString()}</td>
                          <td>{(m.completion_tokens ?? 0).toLocaleString()}</td>
                          <td>{m.llm_calls ?? 0}</td>
                          <td>
                            {new Intl.NumberFormat("en-US", {
                              style: "currency",
                              currency: "USD",
                              minimumFractionDigits: 2,
                              maximumFractionDigits: 4,
                            }).format(Number(m.estimated_cost_usd ?? 0))}
                          </td>
                          <td>{Number(m.credits ?? 0).toLocaleString(undefined, { maximumFractionDigits: 2 })}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              ) : null}
            </>
          ) : !usageErr && usageLoading ? (
            <p className="subtle">Loading usage…</p>
          ) : null}
        </section>
      ) : activePage === "prompts" ? (
        <section className="panel settings-page prompts-page">
          <header className="settings-page-header">
            <div>
              <h2>Prompts</h2>
              <p className="subtle">
                System instructions for documentary text models. Workspace defaults come from the catalog; your edits are
                stored per signed-in user (and scoped to the current workspace when using multi-tenant auth). Scene-plan prompts
                should mention that writers may use <code>[square brackets]</code> in <code>narration_text</code> for optional visual
                emphasis — generation code uses those hints before raw VO when building image/video prompts (see also{" "}
                <strong>Settings → Visual styles</strong>).
              </p>
              {llmPromptsErr ? <p className="err settings-page-error">{llmPromptsErr}</p> : null}
            </div>
            <div className="settings-page-toolbar">
              <button type="button" className="secondary" disabled={llmPromptsBusy} onClick={() => void loadLlmPrompts()}>
                Reload from server
              </button>
            </div>
          </header>
          <div className="prompts-list">
            {llmPromptsBusy && llmPrompts.length === 0 ? <p className="subtle">Loading prompts…</p> : null}
            {llmPrompts.map((p) => (
              <details key={p.prompt_key} className="settings-section prompts-editor-card">
                <summary className="settings-section-summary">
                  <span className="settings-section-heading">
                    {p.title}
                    {p.is_custom ? <span className="subtle"> — custom</span> : null}
                  </span>
                </summary>
                {p.description ? <p className="subtle prompts-editor-desc">{p.description}</p> : null}
                <div className="prompts-editor-meta subtle">{p.prompt_key}</div>
                <textarea
                  className="prompts-editor-textarea"
                  rows={14}
                  value={llmPromptDrafts[p.prompt_key] ?? ""}
                  onChange={(e) =>
                    setLlmPromptDrafts((d) => ({ ...d, [p.prompt_key]: e.target.value }))
                  }
                  disabled={llmPromptsBusy}
                  spellCheck={false}
                />
                <div className="prompts-editor-actions">
                  <button type="button" disabled={llmPromptsBusy} onClick={() => void saveLlmPrompt(p.prompt_key)}>
                    Save
                  </button>
                  <button
                    type="button"
                    className="secondary"
                    disabled={llmPromptsBusy || !p.is_custom}
                    onClick={() => void resetLlmPrompt(p.prompt_key)}
                  >
                    Revert to default
                  </button>
                </div>
              </details>
            ))}
          </div>
        </section>
      ) : activePage === "research_chapters" ? (
        <section className="panel usage-page research-scripts-page">
          <header className="usage-page-header">
            <div>
              <h2>Research dossier &amp; chapters</h2>
              <p className="subtle">
                Edit the structured research JSON and chapter scripts before <strong>Plan scenes</strong>. Use this in <strong>manual</strong> mode to
                fix facts, tighten summaries, or rewrite VO. Saving the dossier checks it against the server schema; chapter saves update titles,
                summaries, target duration, and full script text.
              </p>
              {researchPageErr ? <p className="err usage-page-error">{researchPageErr}</p> : null}
            </div>
            <div className="usage-page-toolbar">
              <button
                type="button"
                disabled={
                  researchPageBusy ||
                  researchPipelineBusy ||
                  !projectId
                }
                onClick={async () => {
                  if (!projectId) return;
                  setResearchPipelineBusy(true);
                  setResearchPageErr("");
                  try {
                    await apiPostIdempotent(api, `/v1/projects/${projectId}/research/run`, {}, idem);
                    setMessage("Research rerun queued — worker will refresh the dossier. Reload this tab when the job finishes.");
                  } catch (e) {
                    setResearchPipelineBusy(false);
                    setResearchPageErr(formatUserFacingError(e));
                  }
                }}
              >
                {researchPipelineBusy ? "Research running…" : "Rerun research"}
              </button>
              <button
                type="button"
                className="secondary"
                disabled={researchPageBusy || researchPipelineBusy || !projectId}
                onClick={() => projectId && void loadResearchChaptersEditor(projectId)}
              >
                Reload from server
              </button>
            </div>
          </header>
          {!projectId ? (
            <p className="subtle">Open a project from the Editor tab first.</p>
          ) : (
            <>
              {researchPageBusy ? <p className="subtle">Loading or saving…</p> : null}
              <div className="research-scripts-meta subtle" style={{ marginBottom: 16 }}>
                {researchMeta?.dossier ? (
                  <>
                    Dossier v{researchMeta.dossier.version ?? "—"} · status <code>{researchMeta.dossier.status}</code>
                    {researchMeta.script_gate_open ? (
                      <span> · script gate open</span>
                    ) : (
                      <span> · script gate closed (approve or override research in Pipeline if scripts are blocked)</span>
                    )}
                    {researchMeta.sourceCount != null ? (
                      <span>
                        {" "}
                        · {researchMeta.sourceCount} source(s), {researchMeta.claimCount} claim(s) (edit sources/claims via API only)
                      </span>
                    ) : null}
                  </>
                ) : (
                  <span>No dossier yet — run research from the Pipeline panel, then reload.</span>
                )}
              </div>
              <details className="settings-section" open>
                <summary className="settings-section-summary">
                  <span className="settings-section-heading">Research dossier (JSON)</span>
                </summary>
                <div className="settings-section-body">
                  <p className="subtle" style={{ marginTop: 0 }}>
                    Must stay valid for <code>research-dossier.schema.json</code>. Invalid JSON or schema errors return 422 from the server.
                    Use <strong>Rerun research</strong> above to fetch a fresh dossier from the web (same as Pipeline).
                  </p>
                  <textarea
                    className="research-dossier-json"
                    rows={16}
                    spellCheck={false}
                    value={researchJsonDraft}
                    onChange={(e) => setResearchJsonDraft(e.target.value)}
                    disabled={
                      researchPageBusy || researchPipelineBusy || !researchMeta?.dossier
                    }
                  />
                  <div className="action-row" style={{ marginTop: 10 }}>
                    <button
                      type="button"
                      disabled={
                        researchPageBusy ||
                        researchPipelineBusy ||
                        !projectId ||
                        !researchMeta?.dossier
                      }
                      onClick={async () => {
                        if (!projectId || !researchMeta?.dossier) return;
                        let parsed;
                        try {
                          parsed = JSON.parse(researchJsonDraft || "{}");
                        } catch (e) {
                          setResearchPageErr(`Invalid JSON: ${e instanceof Error ? e.message : String(e)}`);
                          return;
                        }
                        if (parsed === null || typeof parsed !== "object" || Array.isArray(parsed)) {
                          setResearchPageErr("Dossier body must be a JSON object.");
                          return;
                        }
                        setResearchPageBusy(true);
                        setResearchPageErr("");
                        try {
                          const r = await api(`/v1/projects/${projectId}/research/body`, {
                            method: "PATCH",
                            headers: { "Content-Type": "application/json" },
                            body: JSON.stringify({ body: parsed }),
                          });
                          const b = await parseJson(r);
                          if (!r.ok) throw new Error(apiErrorMessage(b) || `HTTP ${r.status}`);
                          setMessage("Research dossier saved.");
                          await loadResearchChaptersEditor(projectId);
                        } catch (e) {
                          setResearchPageErr(formatUserFacingError(e));
                        } finally {
                          setResearchPageBusy(false);
                        }
                      }}
                    >
                      Save dossier
                    </button>
                  </div>
                </div>
              </details>
              <h3 className="usage-section-title" style={{ marginTop: 24 }}>
                Chapters
              </h3>
              {chapters.length === 0 ? (
                <p className="subtle">No chapters yet — generate outline and scripts from the Pipeline panel, then reload.</p>
              ) : (
                chapters.map((ch) => {
                  const d = chapterScriptsDraft[ch.id] || {
                    title: ch.title ?? "",
                    summary: ch.summary ?? "",
                    target_duration_sec: ch.target_duration_sec != null ? String(ch.target_duration_sec) : "",
                    script_text: ch.script_text ?? "",
                  };
                  const setD = (patch) =>
                    setChapterScriptsDraft((prev) => ({
                      ...prev,
                      [ch.id]: { ...d, ...patch },
                    }));
                  return (
                    <details key={ch.id} className="settings-section" style={{ marginBottom: 12 }}>
                      <summary className="settings-section-summary">
                        <span className="settings-section-heading">
                          {ch.order_index + 1}. {d.title || ch.title || "Chapter"}
                        </span>
                      </summary>
                      <div className="settings-section-body">
                        <label className="subtle" htmlFor={`rch-title-${ch.id}`}>
                          Title
                        </label>
                        <input
                          id={`rch-title-${ch.id}`}
                          value={d.title}
                          onChange={(e) => setD({ title: e.target.value })}
                          disabled={researchPageBusy}
                        />
                        <label className="subtle" htmlFor={`rch-sum-${ch.id}`} style={{ display: "block", marginTop: 10 }}>
                          Summary (also used as <strong>regeneration notes</strong> — describe what to improve in the script)
                        </label>
                        <textarea
                          id={`rch-sum-${ch.id}`}
                          rows={3}
                          value={d.summary}
                          onChange={(e) => setD({ summary: e.target.value })}
                          disabled={researchPageBusy || Boolean(chapterRegenerateId)}
                        />
                        <label className="subtle" htmlFor={`rch-sec-${ch.id}`} style={{ display: "block", marginTop: 10 }}>
                          Target duration (seconds, optional)
                        </label>
                        <input
                          id={`rch-sec-${ch.id}`}
                          type="number"
                          min={30}
                          max={7200}
                          value={d.target_duration_sec}
                          onChange={(e) => setD({ target_duration_sec: e.target.value })}
                          disabled={researchPageBusy}
                          style={{ maxWidth: 120 }}
                        />
                        <label className="subtle" htmlFor={`rch-script-${ch.id}`} style={{ display: "block", marginTop: 10 }}>
                          Script / VO (used for scene planning)
                        </label>
                        <textarea
                          id={`rch-script-${ch.id}`}
                          className="research-chapter-script"
                          rows={12}
                          value={d.script_text}
                          onChange={(e) => setD({ script_text: e.target.value })}
                          disabled={researchPageBusy}
                        />
                        <div className="action-row" style={{ marginTop: 10, flexWrap: "wrap", gap: 8 }}>
                          <button
                            type="button"
                            className="secondary"
                            disabled={
                              researchPageBusy || researchPipelineBusy || chapterRegenerateId !== ""
                            }
                            title="Queues an LLM job: passes the summary above as enhancement_notes to rewrite the script."
                            onClick={async () => {
                              const notes = (d.summary || "").trim();
                              if (notes.length < 8) {
                                setResearchPageErr(
                                  "Chapter summary must be at least 8 characters to regenerate (use it as editorial direction).",
                                );
                                return;
                              }
                              setResearchPageErr("");
                              setChapterRegenerateId(ch.id);
                              try {
                                await apiPostIdempotent(
                                  api,
                                  `/v1/chapters/${ch.id}/script/regenerate`,
                                  { enhancement_notes: notes },
                                  idem,
                                );
                                setMessage(
                                  `Chapter “${(d.title || ch.title || "").trim() || ch.id}” script regeneration queued.`,
                                );
                              } catch (e) {
                                setChapterRegenerateId("");
                                setResearchPageErr(formatUserFacingError(e));
                              }
                            }}
                          >
                            {chapterRegenerateId === ch.id ? "Regenerating…" : "Regenerate script from summary"}
                          </button>
                          <button
                            type="button"
                            disabled={
                              researchPageBusy ||
                              researchPipelineBusy ||
                              Boolean(chapterRegenerateId)
                            }
                            onClick={async () => {
                              const title = (d.title || "").trim();
                              if (!title) {
                                setResearchPageErr("Chapter title cannot be empty.");
                                return;
                              }
                              let target_duration_sec = null;
                              const ts = (d.target_duration_sec || "").trim();
                              if (ts !== "") {
                                const n = Number(ts);
                                if (!Number.isFinite(n) || n < 30 || n > 7200) {
                                  setResearchPageErr("Target duration must be between 30 and 7200 seconds, or leave blank.");
                                  return;
                                }
                                target_duration_sec = Math.round(n);
                              }
                              setResearchPageBusy(true);
                              setResearchPageErr("");
                              try {
                                const r = await api(`/v1/chapters/${ch.id}`, {
                                  method: "PATCH",
                                  headers: { "Content-Type": "application/json" },
                                  body: JSON.stringify({
                                    title,
                                    summary: d.summary,
                                    target_duration_sec,
                                    script_text: d.script_text,
                                  }),
                                });
                                const b = await parseJson(r);
                                if (!r.ok) throw new Error(apiErrorMessage(b) || `HTTP ${r.status}`);
                                const row = b.data;
                                setMessage(`Chapter “${title}” saved.`);
                                if (row?.id) {
                                  setChapters((prev) =>
                                    prev.map((c) => (c.id === row.id ? { ...c, ...row } : c)),
                                  );
                                }
                              } catch (e) {
                                setResearchPageErr(formatUserFacingError(e));
                              } finally {
                                setResearchPageBusy(false);
                              }
                            }}
                          >
                            Save chapter
                          </button>
                        </div>
                        {chapterRegenerateId === ch.id ? (
                          <p className="subtle" style={{ marginTop: 8 }}>
                            Worker is rewriting this chapter’s script from your summary. This tab will reload when the job finishes (or use{" "}
                            <strong>Reload from server</strong>).
                          </p>
                        ) : null}
                      </div>
                    </details>
                  );
                })
              )}
            </>
          )}
        </section>
      ) : activePage === "settings" ? (
        <section className="panel settings-page">
          <header className="settings-page-header">
            <div>
              <h2>Settings</h2>
              <p className="subtle">
                Workspace defaults (stored on the server). Open a project to override some options per production.
              </p>
              {settingsLoadError ? <p className="err settings-page-error">{settingsLoadError}</p> : null}
              {platformCredentialKeysInherited.length > 0 ? (
                <p className="subtle" style={{ marginTop: 8, maxWidth: 640 }}>
                  Some API keys are supplied by your administrator and are not shown (
                  {platformCredentialKeysInherited.join(", ")}). Leave those fields empty to keep using them, or enter your own to override for this workspace.
                </p>
              ) : null}
            </div>
            <div className="settings-page-toolbar">
              <button type="button" className="secondary" disabled={settingsBusy} onClick={loadAppSettings}>
                Reload from server
              </button>
              <button type="button" disabled={settingsBusy} onClick={saveAppSettings}>
                Save changes
              </button>
            </div>
          </header>

          <div className="settings-layout">
            <nav className="settings-nav" aria-label="Settings sections">
              <button
                type="button"
                className={settingsTab === "generation" ? "is-active" : ""}
                onClick={() => setSettingsTab("generation")}
              >
                Generation
              </button>
              <button
                type="button"
                className={settingsTab === "automation" ? "is-active" : ""}
                onClick={() => setSettingsTab("automation")}
              >
                Automation
              </button>
              <button
                type="button"
                className={settingsTab === "studio" ? "is-active" : ""}
                onClick={() => setSettingsTab("studio")}
              >
                Studio
              </button>
              <button
                type="button"
                className={settingsTab === "integrations" ? "is-active" : ""}
                onClick={() => setSettingsTab("integrations")}
              >
                API keys
              </button>
              <button
                type="button"
                className={settingsTab === "voice_ref" ? "is-active" : ""}
                onClick={() => setSettingsTab("voice_ref")}
              >
                Voice reference
              </button>
            </nav>
            <div className="settings-tab-panel">
              {settingsTab === "generation" && (
                <>
                  <nav className="settings-subnav" aria-label="Generation sections">
                    <button
                      type="button"
                      className={generationSettingsTab === "engines" ? "is-active" : ""}
                      onClick={() => setGenerationSettingsTab("engines")}
                    >
                      Engines &amp; timing
                    </button>
                    <button
                      type="button"
                      className={generationSettingsTab === "narration_styles" ? "is-active" : ""}
                      onClick={() => setGenerationSettingsTab("narration_styles")}
                    >
                      Narration styles
                    </button>
                    <button
                      type="button"
                      className={generationSettingsTab === "visual" ? "is-active" : ""}
                      onClick={() => setGenerationSettingsTab("visual")}
                    >
                      Visual styles
                    </button>
                  </nav>
                  {generationSettingsTab === "engines" && (
                  <div className="settings-section">
                    <h3 className="settings-section-heading settings-section-heading-static">Engines &amp; timing</h3>
                    <div className="settings-section-body">
                    <p className="subtle">Which engines to use and how long scene clips should run.</p>
            <label htmlFor="cfg-active-text">Text provider</label>
            <select
              id="cfg-active-text"
              value={appConfig.active_text_provider || "openai"}
              onChange={(e) => setAppConfig((p) => ({ ...p, active_text_provider: e.target.value }))}
            >
              <option value="openai">openai (text)</option>
              <option value="lm_studio">lm_studio (text, local)</option>
              <option value="openrouter">openrouter (text)</option>
              <option value="xai">grok/xai (text)</option>
              <option value="gemini">gemini (text)</option>
            </select>
            <p className="subtle" style={{ marginTop: -6 }}>
              <strong>lm_studio</strong> uses the LM Studio block under <strong>API keys</strong> (base URL + model). The OpenAI/LM routing dropdown there applies only when this is set to <strong>openai</strong>.
            </p>
            <label htmlFor="cfg-active-image">Image provider</label>
            <select
              id="cfg-active-image"
              value={appConfig.active_image_provider || "fal"}
              onChange={(e) => setAppConfig((p) => ({ ...p, active_image_provider: e.target.value }))}
            >
              <option value="fal">fal (image)</option>
              <option value="comfyui">ComfyUI (stills)</option>
            </select>
            <p className="subtle" style={{ marginTop: -6 }}>
              Studio scene images use <strong>fal</strong> or <strong>ComfyUI</strong> only. Save settings to persist; unsaved changes still apply for this browser session. Configure ComfyUI under <strong>ComfyUI</strong> below.
            </p>
            <label htmlFor="cfg-active-video">Video provider</label>
            <select
              id="cfg-active-video"
              value={appConfig.active_video_provider || "fal"}
              onChange={(e) => setAppConfig((p) => ({ ...p, active_video_provider: e.target.value }))}
            >
              <option value="fal">fal (generative video)</option>
              <option value="comfyui_wan">ComfyUI (video workflow)</option>
              <option value="local_ffmpeg">local_ffmpeg (still→MP4 encode)</option>
            </select>
            <p className="subtle" style={{ marginTop: -6 }}>
              Generative clips use <strong>fal</strong> or <strong>comfyui_wan</strong>. <strong>local_ffmpeg</strong> turns existing scene images into MP4s without an external API. ComfyUI video workflow path is under <strong>ComfyUI</strong> below.
            </p>
            <label htmlFor="cfg-active-speech">Speech provider (scene narration TTS)</label>
            <p className="subtle" style={{ marginTop: -4 }}>
              Default TTS for <strong>Generate narration</strong>. Cloud engines use keys under <strong>API keys</strong>. Local{" "}
              <strong>Chatterbox</strong> needs a reference clip from Settings → <strong>Voice reference</strong>; Kokoro and Chatterbox need the
              optional API/worker Python extras and a GPU/CPU where applicable.
            </p>
            <select
              id="cfg-active-speech"
              value={speechProviderSettingSelectValue(appConfig.active_speech_provider)}
              onChange={(e) => setAppConfig((p) => ({ ...p, active_speech_provider: e.target.value }))}
            >
              <optgroup label="Cloud narration">
                <option value="openai">OpenAI (tts-1 / gpt-4o-mini-tts)</option>
                <option value="elevenlabs">ElevenLabs</option>
                <option value="gemini">Gemini (preview TTS)</option>
              </optgroup>
              <optgroup label="Local narration">
                <option value="kokoro">Kokoro (local TTS)</option>
                <option value="chatterbox_turbo">Chatterbox turbo (voice clone — use Voice reference)</option>
                <option value="chatterbox_mtl">Chatterbox multilingual (voice clone — use Voice reference)</option>
              </optgroup>
            </select>
            {speechProviderSettingSelectValue(appConfig.active_speech_provider) === "kokoro" ? (
              <>
                <label htmlFor="cfg-kokoro-voice" style={{ marginTop: 14 }}>
                  Kokoro voice
                </label>
                <p className="subtle" style={{ marginTop: -4 }}>
                  Voice tensors download from the Hugging Face repo on first synthesis. Use a language below that matches your script; American vs
                  British English use different G2P pipelines.
                </p>
                {(() => {
                  const rawSp = String(appConfig.active_speech_provider || "").trim();
                  const fromPrefix =
                    rawSp.toLowerCase().startsWith("kokoro:") ? rawSp.slice("kokoro:".length).trim() : "";
                  const curVoice =
                    fromPrefix ||
                    String(appConfig.kokoro_voice || "af_bella").trim() ||
                    "af_bella";
                  const voiceInList = KOKORO_VOICE_OPTIONS.some((x) => x.id === curVoice);
                  return (
                    <select
                      id="cfg-kokoro-voice"
                      value={curVoice}
                      onChange={(e) =>
                        setAppConfig((p) => ({
                          ...p,
                          kokoro_voice: e.target.value,
                          active_speech_provider: "kokoro",
                        }))
                      }
                    >
                      {!voiceInList && curVoice ? (
                        <option value={curVoice}>
                          {curVoice} (saved)
                        </option>
                      ) : null}
                      {KOKORO_VOICE_OPTIONS.map((v) => (
                        <option key={v.id} value={v.id}>
                          {v.label}
                        </option>
                      ))}
                    </select>
                  );
                })()}
                <label htmlFor="cfg-kokoro-lang" style={{ marginTop: 12 }}>
                  Kokoro language (G2P)
                </label>
                {(() => {
                  const curLang = String(appConfig.kokoro_lang_code || "a").trim().toLowerCase() || "a";
                  const langInList = KOKORO_LANG_OPTIONS.some((x) => x.id === curLang);
                  return (
                    <select
                      id="cfg-kokoro-lang"
                      value={curLang}
                      onChange={(e) => setAppConfig((p) => ({ ...p, kokoro_lang_code: e.target.value }))}
                    >
                      {!langInList ? (
                        <option value={curLang}>
                          {curLang} (saved)
                        </option>
                      ) : null}
                      {KOKORO_LANG_OPTIONS.map((o) => (
                        <option key={o.id} value={o.id}>
                          {o.label}
                        </option>
                      ))}
                    </select>
                  );
                })()}
                <label htmlFor="cfg-kokoro-speed" style={{ marginTop: 12 }}>
                  Kokoro speed
                </label>
                <p className="subtle" style={{ marginTop: -4 }}>
                  1.0 = default. Requires worker Python extra <code>kokoro</code> + model weights.
                </p>
                <input
                  id="cfg-kokoro-speed"
                  type="number"
                  min={0.5}
                  max={2}
                  step={0.05}
                  value={Number(appConfig.kokoro_speed ?? 1)}
                  onChange={(e) => {
                    const v = Math.min(2, Math.max(0.5, Number.parseFloat(e.target.value) || 1));
                    setAppConfig((p) => ({ ...p, kokoro_speed: v }));
                  }}
                />
              </>
            ) : null}
            <p className="subtle" style={{ marginTop: 6 }}>
              Multilingual default language is <code>chatterbox_mtl_language_id</code> in saved settings (or override with{" "}
              <code>chatterbox_mtl:&lt;lang&gt;</code> in advanced config). Saving this dropdown sets the base engine only (prefix overrides are
              replaced when you pick a new option).
            </p>
            <label htmlFor="cfg-scene-clip-sec" style={{ marginTop: 14 }}>
              Scene clip length (seconds)
            </label>
            <p className="subtle" style={{ marginTop: -4 }}>
              Length for generated scene clips and for still-image beats when a scene doesn’t specify its own duration. Save, then run the next
              generation to apply.
            </p>
            <select
              id="cfg-scene-clip-sec"
              value={Number(appConfig.scene_clip_duration_sec) === 5 ? "5" : "10"}
              onChange={(e) =>
                setAppConfig((p) => ({ ...p, scene_clip_duration_sec: Number(e.target.value) }))
              }
            >
              <option value="5">5 seconds</option>
              <option value="10">10 seconds</option>
            </select>
            <label htmlFor="cfg-scene-plan-target" style={{ marginTop: 14 }}>
              Target scenes per chapter (scripts + scene plan)
            </label>
            <p className="subtle" style={{ marginTop: -4 }}>
              0 = automatic: chapter VO is one flowing script; scene count follows script length and clip length above. Set 1–48 to require exactly
              that many paragraph-separated beats in each generated chapter script (validated after generation), and at least that many scenes when
              planning. Save, then Generate chapters again for new scripts. Scene planning still allows more scenes when the model needs it (up to 48).
              Not honored when agent fast mode skips the storyboard LLM.
            </p>
            <input
              id="cfg-scene-plan-target"
              type="number"
              min={0}
              max={48}
              step={1}
              value={Number(appConfig.scene_plan_target_scenes_per_chapter ?? 0)}
              onChange={(e) =>
                setAppConfig((p) => ({
                  ...p,
                  scene_plan_target_scenes_per_chapter: Number(e.target.value),
                }))
              }
            />
            <label htmlFor="cfg-chapter-title-card-sec" style={{ marginTop: 14 }}>
              Chapter title cards (rough / final export)
            </label>
            <p className="subtle" style={{ marginTop: -4 }}>
              Seconds of black full-screen title before each chapter’s first clip in the picture edit, with matching silent audio in the final mix. Set to 0
              to disable. After changing, save settings and re-run rough cut and final cut. Fine-cut overlays with fixed timestamps may need retiming if you
              enable this.
            </p>
            <input
              id="cfg-chapter-title-card-sec"
              type="number"
              min={0}
              max={30}
              step={0.5}
              value={Number(appConfig.export_chapter_title_card_sec ?? 0)}
              onChange={(e) =>
                setAppConfig((p) => ({
                  ...p,
                  export_chapter_title_card_sec: Number(e.target.value),
                }))
              }
            />
                    </div>
                  </div>
                  )}
                  {generationSettingsTab === "narration_styles" && (
                  <div className="settings-section">
                    <h3 className="settings-section-heading settings-section-heading-static">Narration styles</h3>
                    <div className="settings-section-body">
                      <p className="subtle">
                        Voice briefs passed to the text models for chapter scripts, scene plans, and narration revisions. Each production
                        stores a <code>preset:</code> or <code>user:</code> reference on the project; new agent runs use the default below.
                        Scene VO may use <code>[brackets]</code> around visual emphases — those drive image/video prompts when present (see{" "}
                        <strong>Visual styles</strong>).
                      </p>
                      {narrationStylesLibErr ? <p className="err">{narrationStylesLibErr}</p> : null}
                      <label htmlFor="cfg-default-narration-ref" style={{ marginTop: 12 }}>
                        Default narration style (new agent runs)
                      </label>
                      <p className="subtle" style={{ marginTop: -4 }}>
                        Saved with <strong>Save changes</strong>. Custom styles require signing in.
                      </p>
                      <select
                        id="cfg-default-narration-ref"
                        disabled={narrationStylesLibBusy}
                        value={(() => {
                          const dr = String(appConfig.default_narration_style_ref || "").trim();
                          const fb = `preset:${String(
                            appConfig.narration_style_preset ||
                              stylePresets.defaults?.narration_style_preset ||
                              DEFAULT_NARRATION_PRESET_ID,
                          ).trim()}`;
                          return dr && (dr.startsWith("preset:") || dr.startsWith("user:")) ? dr : fb;
                        })()}
                        onChange={(e) =>
                          setAppConfig((p) => ({ ...p, default_narration_style_ref: e.target.value }))
                        }
                      >
                        {(narrationStylesLib.length
                          ? narrationStylesLib
                          : (stylePresets.narration_presets || []).map((p) => ({
                              ref: `preset:${p.id}`,
                              kind: "preset",
                              title: p.label,
                              prompt: p.prompt || "",
                              is_builtin: true,
                            }))
                        ).map((s) => (
                          <option key={s.ref} value={s.ref}>
                            {s.is_builtin ? s.title : `${s.title} (custom)`}
                          </option>
                        ))}
                      </select>
                      <p className="subtle" style={{ marginTop: 10 }}>
                        When a project has no narration style set, the worker uses this default, then falls back to the workspace{" "}
                        <code>narration_style_preset</code> from saved settings.
                      </p>
                      <h4 className="settings-inline-heading" style={{ marginTop: 20 }}>
                        Built-in presets
                      </h4>
                      <p className="subtle" style={{ marginTop: -4 }}>
                        Read-only summaries; choose one above or duplicate the idea into a custom style.
                      </p>
                      <ul className="narration-preset-summary-list subtle" style={{ marginTop: 8, paddingLeft: 18 }}>
                        {(stylePresets.narration_presets || []).map((p) => (
                          <li key={p.id} style={{ marginBottom: 8 }}>
                            <strong>{p.label}</strong>
                            {p.prompt ? (
                              <span>
                                {" "}
                                — {String(p.prompt).slice(0, 160)}
                                {String(p.prompt).length > 160 ? "…" : ""}
                              </span>
                            ) : null}
                          </li>
                        ))}
                      </ul>
                      <h4 className="settings-inline-heading" style={{ marginTop: 20 }}>
                        {narEditingRef ? "Edit custom style" : "Add custom style"}
                      </h4>
                      {!accountProfile?.email ? (
                        <p className="subtle">Sign in to create or edit custom narration styles.</p>
                      ) : (
                        <>
                          <label htmlFor="cfg-nar-form-title">Title</label>
                          <input
                            id="cfg-nar-form-title"
                            type="text"
                            value={narFormTitle}
                            onChange={(e) => setNarFormTitle(e.target.value)}
                            placeholder="e.g. True-crime cold open"
                            maxLength={200}
                          />
                          <label htmlFor="cfg-nar-form-prompt" style={{ marginTop: 10 }}>
                            Voice brief (min. 10 characters)
                          </label>
                          <textarea
                            id="cfg-nar-form-prompt"
                            rows={6}
                            value={narFormPrompt}
                            onChange={(e) => setNarFormPrompt(e.target.value)}
                            placeholder="Instructions for tone, pacing, POV, and what to avoid in documentary VO…"
                          />
                          <div style={{ display: "flex", gap: 8, flexWrap: "wrap", marginTop: 10 }}>
                            <button
                              type="button"
                              disabled={narrationStylesLibBusy}
                              onClick={() => void createOrUpdateNarrationStyle()}
                            >
                              {narEditingRef ? "Save changes" : "Add style"}
                            </button>
                            {narEditingRef ? (
                              <button
                                type="button"
                                className="secondary"
                                disabled={narrationStylesLibBusy}
                                onClick={() => {
                                  setNarEditingRef(null);
                                  setNarFormTitle("");
                                  setNarFormPrompt("");
                                }}
                              >
                                Cancel
                              </button>
                            ) : null}
                          </div>
                        </>
                      )}
                      <h4 className="settings-inline-heading" style={{ marginTop: 20 }}>
                        Your custom styles
                      </h4>
                      {narrationStylesLibBusy && !narrationStylesLib.length ? (
                        <p className="subtle">Loading…</p>
                      ) : (
                        <ul className="narration-custom-style-list" style={{ listStyle: "none", padding: 0, margin: 0 }}>
                          {narrationStylesLib
                            .filter((s) => !s.is_builtin)
                            .map((s) => (
                              <li
                                key={s.ref}
                                style={{
                                  border: "1px solid rgb(255 255 255 / 12%)",
                                  borderRadius: 8,
                                  padding: "10px 12px",
                                  marginBottom: 10,
                                }}
                              >
                                <div style={{ display: "flex", flexWrap: "wrap", gap: 8, alignItems: "center" }}>
                                  <strong>{s.title}</strong>
                                  <code className="subtle" style={{ fontSize: "0.78rem" }}>
                                    {s.ref}
                                  </code>
                                  {accountProfile?.email ? (
                                    <>
                                      <button
                                        type="button"
                                        className="secondary"
                                        disabled={narrationStylesLibBusy}
                                        onClick={() => {
                                          setNarEditingRef(s.ref);
                                          setNarFormTitle(s.title);
                                          setNarFormPrompt(s.prompt || "");
                                        }}
                                      >
                                        Edit
                                      </button>
                                      <button
                                        type="button"
                                        className="secondary"
                                        disabled={narrationStylesLibBusy}
                                        onClick={() => void deleteNarrationStyleByRef(s.ref)}
                                      >
                                        Delete
                                      </button>
                                    </>
                                  ) : null}
                                </div>
                              </li>
                            ))}
                        </ul>
                      )}
                      {!narrationStylesLibBusy &&
                      narrationStylesLib.length > 0 &&
                      !narrationStylesLib.some((s) => !s.is_builtin) &&
                      accountProfile?.email ? (
                        <p className="subtle">No custom styles yet — add one above.</p>
                      ) : null}
                    </div>
                  </div>
                  )}
                  {generationSettingsTab === "visual" && (
                  <div className="settings-section">
                    <h3 className="settings-section-heading settings-section-heading-static">Visual styles</h3>
                    <div className="settings-section-body">
            <label htmlFor="cfg-visual-preset" style={{ marginTop: 8 }}>
              Default visual style (scene images &amp; video prompts)
            </label>
            <p className="subtle" style={{ marginTop: -4 }}>
              Default for new projects (<code>preset:&lt;id&gt;</code>). Override per-preset prompts below; those strings are fused into image and
              video generation.
            </p>
            <p className="subtle" style={{ marginTop: 10, lineHeight: 1.45 }}>
              <strong>Visual hints in scene narration:</strong> In each scene’s VO / narration field, wrap short subjects or beats in square brackets
              (e.g. <code>There [mermaids] were rare until one [reappeared on the beach]</code>). When present, still-image and video text prompts
              prioritize those hints (plus this visual style and character consistency). Optional: in the Scene panel, check{" "}
              <strong>Refine [bracket] hints with LLM</strong> before generating an image to merge hints into one precise prompt (off by default).
            </p>
            <select
              id="cfg-visual-preset"
              value={
                appConfig.visual_style_preset ||
                stylePresets.defaults?.visual_style_preset ||
                "cinematic_documentary"
              }
              onChange={(e) => setAppConfig((p) => ({ ...p, visual_style_preset: e.target.value }))}
            >
              {(stylePresets.visual_presets?.length ? stylePresets.visual_presets : VISUAL_STYLE_PRESET_FALLBACK).map((p) => {
                const ov = appConfig.visual_preset_overrides?.[p.id] || {};
                const optLabel = (typeof ov.label === "string" && ov.label.trim() ? ov.label : p.label) || p.id;
                return (
                  <option key={p.id} value={p.id}>
                    {optLabel}
                  </option>
                );
              })}
            </select>
            <div className="visual-style-presets-editor" style={{ marginTop: 16 }}>
              <h4 className="settings-inline-heading">Per-preset overrides</h4>
              <p className="subtle" style={{ marginTop: -4 }}>
                Description is for your notes; prompt overrides apply when a project uses that preset (or the default above).
              </p>
              {(stylePresets.visual_presets?.length ? stylePresets.visual_presets : VISUAL_STYLE_PRESET_FALLBACK).map((p) => {
                const ov = appConfig.visual_preset_overrides?.[p.id] || {};
                const desc = ov.description ?? p.description ?? "";
                const pr = ov.prompt ?? p.prompt ?? "";
                const lbl = ov.label ?? p.label ?? p.id;
                return (
                  <details key={p.id} className="visual-preset-item" style={{ marginBottom: 12 }}>
                    <summary style={{ cursor: "pointer", fontWeight: 600 }}>{lbl}</summary>
                    <label className="subtle" htmlFor={`cfg-vis-label-${p.id}`} style={{ display: "block", marginTop: 8 }}>
                      Label (dropdown)
                    </label>
                    <input
                      id={`cfg-vis-label-${p.id}`}
                      type="text"
                      value={typeof ov.label === "string" ? ov.label : ""}
                      placeholder={p.label}
                      onChange={(e) =>
                        setAppConfig((prev) => ({
                          ...prev,
                          visual_preset_overrides: {
                            ...(prev.visual_preset_overrides || {}),
                            [p.id]: { ...(prev.visual_preset_overrides?.[p.id] || {}), label: e.target.value },
                          },
                        }))
                      }
                    />
                    <label className="subtle" htmlFor={`cfg-vis-desc-${p.id}`} style={{ display: "block", marginTop: 8 }}>
                      Description
                    </label>
                    <textarea
                      id={`cfg-vis-desc-${p.id}`}
                      rows={3}
                      value={desc}
                      onChange={(e) =>
                        setAppConfig((prev) => ({
                          ...prev,
                          visual_preset_overrides: {
                            ...(prev.visual_preset_overrides || {}),
                            [p.id]: { ...(prev.visual_preset_overrides?.[p.id] || {}), description: e.target.value },
                          },
                        }))
                      }
                    />
                    <label className="subtle" htmlFor={`cfg-vis-prompt-${p.id}`} style={{ display: "block", marginTop: 8 }}>
                      Prompt (fused into image &amp; video generation)
                    </label>
                    <textarea
                      id={`cfg-vis-prompt-${p.id}`}
                      rows={6}
                      value={pr}
                      onChange={(e) =>
                        setAppConfig((prev) => ({
                          ...prev,
                          visual_preset_overrides: {
                            ...(prev.visual_preset_overrides || {}),
                            [p.id]: { ...(prev.visual_preset_overrides?.[p.id] || {}), prompt: e.target.value },
                          },
                        }))
                      }
                    />
                  </details>
                );
              })}
            </div>
                    </div>
                  </div>
                  )}
                </>
              )}
              {settingsTab === "automation" && (
                <details className="settings-section" defaultOpen>
                  <summary className="settings-section-summary">
                    <span className="settings-section-heading">Automation &amp; reviews</span>
                  </summary>
                  <div className="settings-section-body">
                  <p className="subtle">Background pipeline behavior and how strict automated quality checks are.</p>
            <label htmlFor="cfg-chapter-crit-rounds" style={{ marginTop: 4 }}>
              Chapter review retries (auto pipeline)
            </label>
            <p className="subtle" style={{ marginTop: -4 }}>
              If a chapter critic gate fails, the worker re-runs only failing chapters with a fresh LLM call, up to this many rounds,
              before the run is marked blocked (default 5, max 20).
            </p>
            <input
              id="cfg-chapter-crit-rounds"
              type="number"
              min={1}
              max={20}
              value={Number(appConfig.agent_run_chapter_critique_max_rounds ?? 5)}
              onChange={(e) => {
                const n = Math.min(20, Math.max(1, Number.parseInt(e.target.value, 10) || 5));
                setAppConfig((p) => ({ ...p, agent_run_chapter_critique_max_rounds: n }));
              }}
            />
            <label htmlFor="cfg-auto-scene-images" style={{ marginTop: 14, textTransform: "none", letterSpacing: 0, fontSize: "0.78rem" }}>
              <input
                id="cfg-auto-scene-images"
                type="checkbox"
                checked={agentRunAutoGenerateSceneImages(appConfig)}
                onChange={(e) =>
                  setAppConfig((p) => ({ ...p, agent_run_auto_generate_scene_images: e.target.checked }))
                }
                style={{ width: "auto", marginRight: 8 }}
              />
              Auto / Hands-off: generate scene stills (preview images per scene in the media tail)
            </label>
            <div style={{ display: "flex", flexWrap: "wrap", gap: "12px 24px", alignItems: "center", marginTop: 8 }}>
              <label htmlFor="cfg-min-scene-images" style={{ textTransform: "none", letterSpacing: 0, fontSize: "0.78rem" }}>
                Min stills per scene (1–10)
                <input
                  id="cfg-min-scene-images"
                  type="number"
                  min={1}
                  max={10}
                  value={agentRunMinSceneImages(appConfig)}
                  disabled={!agentRunAutoGenerateSceneImages(appConfig)}
                  onChange={(e) => {
                    const n = Math.min(10, Math.max(1, Number.parseInt(e.target.value, 10) || 1));
                    setAppConfig((p) => ({ ...p, agent_run_min_scene_images: n }));
                  }}
                  style={{ marginLeft: 8, width: "3.5rem" }}
                />
              </label>
              <label htmlFor="cfg-min-scene-videos" style={{ textTransform: "none", letterSpacing: 0, fontSize: "0.78rem" }}>
                Min clips per scene (1–10)
                <input
                  id="cfg-min-scene-videos"
                  type="number"
                  min={1}
                  max={10}
                  value={agentRunMinSceneVideos(appConfig)}
                  disabled={!agentRunAutoGenerateSceneVideos(appConfig)}
                  onChange={(e) => {
                    const n = Math.min(10, Math.max(1, Number.parseInt(e.target.value, 10) || 1));
                    setAppConfig((p) => ({ ...p, agent_run_min_scene_videos: n }));
                  }}
                  style={{ marginLeft: 8, width: "3.5rem" }}
                />
              </label>
            </div>
            <label htmlFor="cfg-auto-scene-videos" style={{ marginTop: 14, textTransform: "none", letterSpacing: 0, fontSize: "0.78rem" }}>
              <input
                id="cfg-auto-scene-videos"
                type="checkbox"
                checked={agentRunAutoGenerateSceneVideos(appConfig)}
                onChange={(e) =>
                  setAppConfig((p) => ({ ...p, agent_run_auto_generate_scene_videos: e.target.checked }))
                }
                style={{ width: "auto", marginRight: 8 }}
              />
              Auto / Hands-off: generate scene video clips (when enabled, full auto queues clips until each scene meets the minimum)
            </label>
            <p className="subtle" style={{ marginTop: -6 }}>
              Defaults match new projects: stills and clips on, minimum one each per scene. Turn off either type if you only want images or only motion clips.
            </p>
            <label htmlFor="cfg-scene-repair-rounds" style={{ marginTop: 14 }}>
              Auto pipeline: scene critic repair cycles
            </label>
            <p className="subtle" style={{ marginTop: -4 }}>
              After the first scene review pass, failing scenes get an automatic script rewrite from their latest report, then another review.
              Set to 0 to turn off. Uses your configured OpenAI text model.
            </p>
            <input
              id="cfg-scene-repair-rounds"
              type="number"
              min={0}
              max={8}
              value={Number(appConfig.agent_run_scene_repair_max_rounds ?? 2)}
              onChange={(e) => {
                const n = Math.min(8, Math.max(0, Number.parseInt(e.target.value, 10) || 0));
                setAppConfig((p) => ({ ...p, agent_run_scene_repair_max_rounds: n }));
              }}
            />
            <label htmlFor="cfg-chapter-repair" style={{ marginTop: 14 }}>
              Auto pipeline: chapter critic narration repair
            </label>
            <p className="subtle" style={{ marginTop: -4 }}>
              When a chapter gate fails and another chapter-critic attempt will run, apply one LLM batch edit to scene narrations from the
              latest chapter critic report, then re-run scene critic on edited scenes (0 = off).
            </p>
            <input
              id="cfg-chapter-repair"
              type="number"
              min={0}
              max={5}
              value={Number(appConfig.agent_run_chapter_repair_max_rounds ?? 1)}
              onChange={(e) => {
                const n = Math.min(5, Math.max(0, Number.parseInt(e.target.value, 10) || 0));
                setAppConfig((p) => ({ ...p, agent_run_chapter_repair_max_rounds: n }));
              }}
            />
            <label htmlFor="cfg-vo-tail-padding-sec" style={{ marginTop: 14 }}>
              Scene VO tail padding (seconds)
            </label>
            <p className="subtle" style={{ marginTop: -4 }}>
              Minimum silence after spoken scene narration before the next visual beat. Used when merging narration with the picture edit, bumping{" "}
              <code>planned_duration_sec</code> after TTS, and expanding timeline slots so VO is padded instead of trimmed. Default 5; set 0 to disable
              extra hold. Save settings, then re-run rough/final cut to apply.
            </p>
            <input
              id="cfg-vo-tail-padding-sec"
              type="number"
              min={0}
              max={120}
              step={0.5}
              value={Number(appConfig.scene_vo_tail_padding_sec ?? 1.5)}
              onChange={(e) => {
                const v = Math.max(0, Math.min(120, Number.parseFloat(e.target.value) || 5));
                setAppConfig((p) => ({ ...p, scene_vo_tail_padding_sec: v }));
              }}
            />
            <h4 style={{ display: "flex", alignItems: "center", gap: 6 }}>Review thresholds <InfoTip>How strict automated scene and chapter reviews are. Per-project overrides exist on the project record.</InfoTip></h4>
            <label htmlFor="cfg-scene-pass-threshold">Scene critic: pass threshold (0–1)</label>
            <input
              id="cfg-scene-pass-threshold"
              type="number"
              min={0}
              max={1}
              step={0.05}
              value={Number(appConfig.critic_pass_threshold ?? 0.55)}
              onChange={(e) => {
                const v = Math.max(0, Math.min(1, Number.parseFloat(e.target.value) || 0.55));
                setAppConfig((p) => ({ ...p, critic_pass_threshold: v }));
              }}
            />
            <label htmlFor="cfg-chapter-min-scene-ratio" style={{ marginTop: 10 }}>
              Chapter critic: min. fraction of scenes that must pass scene critic (0–1)
            </label>
            <input
              id="cfg-chapter-min-scene-ratio"
              type="number"
              min={0}
              max={1}
              step={0.05}
              value={Number(appConfig.chapter_min_scene_pass_ratio ?? 0.85)}
              onChange={(e) => {
                const v = Math.max(0, Math.min(1, Number.parseFloat(e.target.value) || 0.85));
                setAppConfig((p) => ({ ...p, chapter_min_scene_pass_ratio: v }));
              }}
            />
            <label htmlFor="cfg-chapter-pass-score" style={{ marginTop: 10 }}>
              Chapter critic: minimum aggregate score (0–1)
            </label>
            <p className="subtle" style={{ marginTop: -4 }}>
              Mean of chapter dimension scores (narrative arc, transitions, runtime fit, etc.) must be at least this value for the chapter gate
              to pass, along with the scene ratio above.
            </p>
            <input
              id="cfg-chapter-pass-score"
              type="number"
              min={0}
              max={1}
              step={0.05}
              value={Number(appConfig.chapter_pass_score_threshold ?? 0.5)}
              onChange={(e) => {
                const v = Math.max(0, Math.min(1, Number.parseFloat(e.target.value) || 0.5));
                setAppConfig((p) => ({ ...p, chapter_pass_score_threshold: v }));
              }}
            />
            <label htmlFor="cfg-critic-missing-dim" style={{ marginTop: 10 }}>
              Critic: default score for missing or non-numeric dimensions (0–1)
            </label>
            <p className="subtle" style={{ marginTop: -4 }}>
              Used when the model omits a dimension or returns a non-numeric value — fills script_alignment, narrative_arc, etc. before
              averaging. Not the pass threshold (that is the scene/chapter bars above). Per-project overrides in critic policy JSON can still
              set <code>missing_dimension_default</code> and <code>dimension_invalid_fallback</code> separately.
            </p>
            <input
              id="cfg-critic-missing-dim"
              type="number"
              min={0}
              max={1}
              step={0.05}
              value={Number(appConfig.critic_missing_dimension_default ?? 0.6)}
              onChange={(e) => {
                const v = Math.max(0, Math.min(1, Number.parseFloat(e.target.value) || 0.6));
                setAppConfig((p) => ({ ...p, critic_missing_dimension_default: v }));
              }}
            />
                  </div>
                </details>
              )}
              {settingsTab === "studio" && (
                <details className="settings-section" defaultOpen>
                  <summary className="settings-section-summary">
                    <span className="settings-section-heading">Studio (web UI)</span>
                  </summary>
                  <div className="settings-section-body">
            <label htmlFor="cfg-studio-batch-image-interval">Batch image spacing (seconds)</label>
            <p className="subtle" style={{ marginTop: -4 }}>
              Pause between enqueueing each job for <strong>All images (chapter)</strong>. Does not change how long each Fal/Gemini image takes once queued; raise this if you hit provider rate limits.
            </p>
            <input
              id="cfg-studio-batch-image-interval"
              type="number"
              min={2}
              max={3600}
              step={1}
              value={Number(appConfig.studio_batch_image_interval_sec ?? 5)}
              onChange={(e) => {
                const v = Math.max(2, Math.min(3600, Number.parseInt(e.target.value, 10) || 5));
                setAppConfig((p) => ({ ...p, studio_batch_image_interval_sec: v }));
              }}
            />
            <label htmlFor="cfg-studio-job-poll-ms" style={{ marginTop: 12 }}>
              Job status poll interval (milliseconds)
            </label>
            <p className="subtle" style={{ marginTop: -4 }}>
              How often the UI refreshes media and character job status.
            </p>
            <input
              id="cfg-studio-job-poll-ms"
              type="number"
              min={500}
              max={120000}
              step={100}
              value={Number(appConfig.studio_job_poll_interval_ms ?? 800)}
              onChange={(e) => {
                const v = Math.max(500, Math.min(120_000, Number.parseInt(e.target.value, 10) || 800));
                setAppConfig((p) => ({ ...p, studio_job_poll_interval_ms: v }));
              }}
            />
                  </div>
                </details>
              )}
              {settingsTab === "integrations" && (
                <>
                <div className="settings-section" style={{ marginBottom: 14 }}>
                  <div className="settings-section-body">
                    <span className="settings-section-heading" style={{ display: "flex", alignItems: "center", gap: 6, marginBottom: 8 }}>
                      Provider connection tests
                      <InfoTip>Queues a short <code>adapter_smoke</code> job on the server (Celery) using your saved workspace keys. Requires the API and a worker; results appear as toasts.</InfoTip>
                    </span>
                    <div className="action-row" style={{ flexWrap: "wrap", gap: 8, marginTop: 8 }}>
                      {[
                        { provider: "openai", label: "OpenAI" },
                        { provider: "lm_studio", label: "LM Studio" },
                        { provider: "openrouter", label: "OpenRouter" },
                        { provider: "fal", label: "FAL" },
                        { provider: "gemini", label: "Gemini" },
                        { provider: "google", label: "Google" },
                      ].map(({ provider, label }) => (
                        <button
                          key={provider}
                          type="button"
                          className="secondary"
                          disabled={adapterSmokePollActive || settingsBusy}
                          onClick={() => void runAdapterSmokeTest(provider, label)}
                        >
                          Test {label}
                        </button>
                      ))}
                    </div>
                  </div>
                </div>
                <details className="settings-section" defaultOpen>
                  <summary className="settings-section-summary">
                    <span className="settings-section-heading">Telegram bot</span>
                  </summary>
                  <div className="settings-section-body">
                    {telegramPlanLocked ? (
                      <p className="subtle" style={{ marginBottom: 12 }}>
                        Telegram is not included in your workspace plan. Open <strong>Account</strong> to review access.
                      </p>
                    ) : null}
                    <p className="subtle">
                      Create the bot in Telegram (BotFather). Directely stores credentials and handles the same{" "}
                      <strong>Chat Studio</strong> flow in Telegram; send <strong>RUN</strong> alone when you are ready
                      to queue the full pipeline. Full setup for self-hosted installs is in{" "}
                      <code>INSTALLATION.md</code> §9 in the repo.
                    </p>
                    <ol
                      className="subtle"
                      style={{
                        marginTop: 10,
                        marginBottom: 12,
                        paddingLeft: 22,
                        lineHeight: 1.55,
                        fontSize: "0.95rem",
                      }}
                    >
                      <li>
                        Paste <strong>Bot token</strong> and <strong>Chat ID</strong> below. Use <strong>Generate</strong>{" "}
                        for the webhook secret (or paste your own).
                      </li>
                      <li>
                        Click <strong>Save settings</strong> in the main Settings actions so the API can read them.
                      </li>
                      <li>
                        <strong>Register the webhook with Telegram</strong> (required — Telegram does not call Directely
                        until you do). On the server, from the repo root, with the same token and secret as above:{" "}
                        <code>./scripts/telegram-set-webhook.sh {telegramWebhookPublicOrigin || "https://YOUR_PUBLIC_HOST"}</code>
                        {telegramWebhookPublicOrigin ? (
                          <>
                            {" "}
                            or use the curl block below. Re-run after you change the secret or URL.
                          </>
                        ) : (
                          <>
                            {" "}
                            (set <code>TELEGRAM_BOT_TOKEN</code> and <code>TELEGRAM_WEBHOOK_SECRET</code> in the shell
                            first), or use curl below. Re-run after you change the secret or URL.
                          </>
                        )}
                      </li>
                      <li>
                        Use <strong>Test Telegram connection</strong> — it verifies the bot and shows whether Telegram has
                        a webhook URL registered.
                      </li>
                    </ol>
                    <p className="subtle" style={{ marginTop: 4 }}>
                      Webhook path:{" "}
                      <code>{apiPath("/v1/integrations/telegram/webhook")}</code> — register with Telegram{" "}
                      <code>setWebhook</code>. The <strong>webhook secret</strong> must match <code>secret_token</code>.
                    </p>
                    {telegramWebhookPublicUrl ? (
                      <p className="subtle" style={{ marginTop: 8, fontSize: "0.92rem" }}>
                        <strong>This site (HTTPS):</strong> webhook <code>url</code> should be{" "}
                        <code style={{ wordBreak: "break-all" }}>{telegramWebhookPublicUrl}</code> (nginx proxies{" "}
                        <code>/v1/</code> to the API).
                      </p>
                    ) : null}
                    {telegramWebhookPublicUrl && telegramWebhookPublicOrigin ? (
                      <details className="subtle" style={{ marginTop: 10, marginBottom: 12 }}>
                        <summary style={{ cursor: "pointer", fontWeight: 600 }}>
                          Server: repo script or curl (same host as this page)
                        </summary>
                        <p style={{ marginTop: 8, fontSize: "0.88rem", lineHeight: 1.5 }}>
                          From the machine that has the repo and your bot credentials (export the same values you saved in
                          Studio):
                        </p>
                        <pre
                          className="mono"
                          style={{
                            fontSize: "0.72rem",
                            overflow: "auto",
                            padding: 10,
                            marginTop: 6,
                            background: "var(--panel-2, #1a1a1e)",
                            borderRadius: 6,
                            lineHeight: 1.4,
                          }}
                        >{`export TELEGRAM_BOT_TOKEN='…'
export TELEGRAM_WEBHOOK_SECRET='…'
./scripts/telegram-set-webhook.sh ${telegramWebhookPublicOrigin}`}</pre>
                        <p style={{ fontSize: "0.88rem", marginTop: 10 }}>Or curl:</p>
                        <pre
                          className="mono"
                          style={{
                            fontSize: "0.72rem",
                            overflow: "auto",
                            padding: 10,
                            marginTop: 6,
                            background: "var(--panel-2, #1a1a1e)",
                            borderRadius: 6,
                            lineHeight: 1.4,
                          }}
                        >
                          {`curl -sS -X POST "https://api.telegram.org/bot<BOT_TOKEN>/setWebhook" \\
  --data-urlencode "url=${telegramWebhookPublicUrl}" \\
  --data-urlencode "secret_token=<same as webhook secret above>"`}
                        </pre>
                      </details>
                    ) : null}
                    <details className="subtle" style={{ marginTop: 10, marginBottom: 12 }}>
                      <summary style={{ cursor: "pointer", fontWeight: 600 }}>
                        Local API: use a tunnel (ngrok, Cloudflare Tunnel, …)
                      </summary>
                      <p style={{ marginTop: 8, fontSize: "0.9rem", lineHeight: 1.5 }}>
                        Telegram cannot call <code>http://127.0.0.1</code>. Run the Directely API on port{" "}
                        <code>8000</code> (or your <code>API_PORT</code>), expose it with a tunnel, then use{" "}
                        <strong>https://YOUR-TUNNEL-HOST</strong>
                        {apiPath("/v1/integrations/telegram/webhook")} as the webhook <code>url</code>. Chat ID must be
                        the same user or group that sends messages to the bot.
                      </p>
                      <p style={{ fontSize: "0.88rem", marginTop: 8 }}>
                        Example <code>setWebhook</code> (replace placeholders; keep <code>secret_token</code> identical to
                        the webhook secret field):
                      </p>
                      <pre
                        className="mono"
                        style={{
                          fontSize: "0.72rem",
                          overflow: "auto",
                          padding: 10,
                          marginTop: 6,
                          background: "var(--panel-2, #1a1a1e)",
                          borderRadius: 6,
                          lineHeight: 1.4,
                        }}
                      >
                        {`curl -sS -X POST "https://api.telegram.org/bot<BOT_TOKEN>/setWebhook" \\
  --data-urlencode "url=https://YOUR_TUNNEL_HOST${apiPath("/v1/integrations/telegram/webhook")}" \\
  --data-urlencode "secret_token=<same as webhook secret above>"`}
                      </pre>
                    </details>
                    <label htmlFor="cfg-telegram-token">Bot token</label>
                    {credKeyNote("telegram_bot_token")}
                    <input
                      id="cfg-telegram-token"
                      type="password"
                      autoComplete="off"
                      placeholder="123456:ABC…"
                      disabled={telegramPlanLocked}
                      value={appConfig.telegram_bot_token || ""}
                      onChange={(e) => setAppConfig((p) => ({ ...p, telegram_bot_token: e.target.value }))}
                    />
                    <label htmlFor="cfg-telegram-chat" style={{ marginTop: 12 }}>
                      Chat ID (your user or group id)
                    </label>
                    <input
                      id="cfg-telegram-chat"
                      placeholder="e.g. 123456789 or -100…"
                      disabled={telegramPlanLocked}
                      value={appConfig.telegram_chat_id || ""}
                      onChange={(e) => setAppConfig((p) => ({ ...p, telegram_chat_id: e.target.value }))}
                    />
                    <label htmlFor="cfg-telegram-webhook-secret" style={{ marginTop: 12 }}>
                      Webhook secret (must match Telegram <code>secret_token</code>)
                    </label>
                    {credKeyNote("telegram_webhook_secret")}
                    <div style={{ display: "flex", flexWrap: "wrap", gap: 8, alignItems: "stretch", marginBottom: 6 }}>
                      <input
                        id="cfg-telegram-webhook-secret"
                        type="password"
                        autoComplete="off"
                        disabled={telegramPlanLocked}
                        value={appConfig.telegram_webhook_secret || ""}
                        onChange={(e) => setAppConfig((p) => ({ ...p, telegram_webhook_secret: e.target.value }))}
                        style={{ flex: "1 1 200px", minWidth: 0 }}
                      />
                      <button
                        type="button"
                        className="secondary"
                        disabled={telegramPlanLocked}
                        title="Fills in a random 64-character hex string — then Save settings and use the same value in setWebhook"
                        style={{ flex: "0 0 auto", whiteSpace: "nowrap" }}
                        onClick={() => {
                          try {
                            const buf = new Uint8Array(32);
                            crypto.getRandomValues(buf);
                            const hex = Array.from(buf, (b) => b.toString(16).padStart(2, "0")).join("");
                            setAppConfig((p) => ({ ...p, telegram_webhook_secret: hex }));
                            showToast(
                              "Webhook secret filled in. Click Save settings below, then run setWebhook with the same secret_token.",
                              { type: "success", durationMs: 9000 },
                            );
                          } catch {
                            showToast("Could not generate a secret in this browser.", { type: "error" });
                          }
                        }}
                      >
                        Generate
                      </button>
                    </div>
                    <p className="subtle" style={{ fontSize: "0.82rem", marginTop: 0, marginBottom: 0 }}>
                      Use the same value for Telegram <code>setWebhook</code> <code>secret_token</code> (see curl above).
                    </p>
                    <label style={{ display: "flex", gap: 10, alignItems: "flex-start", cursor: "pointer", marginTop: 14 }}>
                      <input
                        type="checkbox"
                        disabled={telegramPlanLocked}
                        checked={Boolean(appConfig.telegram_notify_pipeline_failures)}
                        onChange={(e) =>
                          setAppConfig((p) => ({ ...p, telegram_notify_pipeline_failures: e.target.checked }))
                        }
                      />
                      <span style={{ fontSize: "0.88rem", lineHeight: 1.45 }}>
                        <strong>Notify on Telegram</strong> when a pipeline run fails, is cancelled, or stops (blocked).
                        Messages can include <strong>Open Studio</strong> and <strong>Retry</strong> when{" "}
                        <strong>Public Studio URL</strong> is set below.
                      </span>
                    </label>
                    <div className="action-row" style={{ marginTop: 12 }}>
                      <button
                        type="button"
                        className="secondary"
                        disabled={telegramPlanLocked || telegramTestLoading || settingsBusy}
                        onClick={() => void runTelegramConnectionTest()}
                      >
                        {telegramTestLoading ? "Testing…" : "Test Telegram connection"}
                      </button>
                    </div>
                  </div>
                </details>
                <details className="settings-section" defaultOpen>
                  <summary className="settings-section-summary">
                    <span className="settings-section-heading">YouTube &amp; export links</span>
                  </summary>
                  <div className="settings-section-body">
                    <p className="subtle" style={{ marginTop: -4 }}>
                      OAuth uses Google&apos;s consent screen. Register redirect URI{" "}
                      <code>
                        {(appConfig.public_api_base_url || "").replace(/\/$/, "") || "(set PUBLIC_API_BASE_URL)"}
                        /v1/integrations/youtube/oauth-callback
                      </code>{" "}
                      in Google Cloud Console for your OAuth client.
                    </p>
                    <label htmlFor="cfg-public-api-base">PUBLIC_API_BASE_URL (API base as Telegram / Google reach it, no trailing slash)</label>
                    <input
                      id="cfg-public-api-base"
                      placeholder="https://api.yourdomain.com"
                      value={appConfig.public_api_base_url || ""}
                      onChange={(e) => setAppConfig((p) => ({ ...p, public_api_base_url: e.target.value }))}
                    />
                    <label htmlFor="cfg-director-public-url" style={{ marginTop: 12 }}>
                      DIRECTOR_PUBLIC_APP_URL (Studio in the browser — for Telegram deep links)
                    </label>
                    <input
                      id="cfg-director-public-url"
                      placeholder="https://studio.yourdomain.com"
                      value={appConfig.director_public_app_url || ""}
                      onChange={(e) => setAppConfig((p) => ({ ...p, director_public_app_url: e.target.value }))}
                    />
                    <label htmlFor="cfg-youtube-client-id" style={{ marginTop: 12 }}>
                      YouTube OAuth client ID
                    </label>
                    <input
                      id="cfg-youtube-client-id"
                      value={appConfig.youtube_client_id || ""}
                      onChange={(e) => setAppConfig((p) => ({ ...p, youtube_client_id: e.target.value }))}
                    />
                    <label htmlFor="cfg-youtube-client-secret" style={{ marginTop: 12 }}>
                      YouTube OAuth client secret
                    </label>
                    {credKeyNote("youtube_client_secret")}
                    <input
                      id="cfg-youtube-client-secret"
                      type="password"
                      autoComplete="off"
                      value={appConfig.youtube_client_secret || ""}
                      onChange={(e) => setAppConfig((p) => ({ ...p, youtube_client_secret: e.target.value }))}
                    />
                    <p className="subtle" style={{ fontSize: "0.85rem", marginTop: 8 }}>
                      After saving client id + secret, open Google consent (stores refresh token in workspace settings).
                    </p>
                    <div className="action-row" style={{ marginTop: 8, flexWrap: "wrap", gap: 8 }}>
                      <button
                        type="button"
                        className="secondary"
                        disabled={settingsBusy}
                        onClick={async () => {
                          setError("");
                          try {
                            const r = await api("/v1/integrations/youtube/auth-url");
                            const b = await parseJson(r);
                            if (!r.ok) throw new Error(apiErrorMessage(b));
                            const u = b.data?.authorize_url;
                            if (!u) throw new Error("No authorize_url from API");
                            window.open(u, "_blank", "noopener,noreferrer");
                            showToast("Complete sign-in in the new tab, then save settings if you changed client id/secret.", {
                              type: "success",
                              durationMs: 8000,
                            });
                          } catch (e) {
                            setError(formatUserFacingError(e));
                          }
                        }}
                      >
                        Connect YouTube (OAuth)
                      </button>
                      <button
                        type="button"
                        className="secondary"
                        disabled={settingsBusy}
                        onClick={async () => {
                          setError("");
                          try {
                            const r = await api("/v1/integrations/youtube/disconnect", { method: "POST" });
                            const b = await parseJson(r);
                            if (!r.ok) throw new Error(apiErrorMessage(b));
                            await loadAppSettings();
                            showToast("YouTube disconnected for this workspace.", { type: "success" });
                          } catch (e) {
                            setError(formatUserFacingError(e));
                          }
                        }}
                      >
                        Disconnect YouTube
                      </button>
                    </div>
                    <label style={{ display: "flex", gap: 10, alignItems: "flex-start", cursor: "pointer", marginTop: 14 }}>
                      <input
                        type="checkbox"
                        checked={Boolean(appConfig.youtube_auto_upload_after_export)}
                        onChange={(e) =>
                          setAppConfig((p) => ({ ...p, youtube_auto_upload_after_export: e.target.checked }))
                        }
                      />
                      <span style={{ fontSize: "0.88rem", lineHeight: 1.45 }}>
                        <strong>Auto-upload to YouTube</strong> after each successful final cut (uses default privacy
                        below). Requires a connected YouTube account.
                      </span>
                    </label>
                    <label style={{ display: "flex", gap: 10, alignItems: "flex-start", cursor: "pointer", marginTop: 10 }}>
                      <input
                        type="checkbox"
                        checked={Boolean(appConfig.youtube_share_watch_link_in_telegram)}
                        onChange={(e) =>
                          setAppConfig((p) => ({ ...p, youtube_share_watch_link_in_telegram: e.target.checked }))
                        }
                      />
                      <span style={{ fontSize: "0.88rem", lineHeight: 1.45 }}>
                        When a pipeline succeeds, send the <strong>YouTube watch link</strong> on Telegram (if an upload
                        ran for that export).
                      </span>
                    </label>
                    <label htmlFor="cfg-youtube-privacy" style={{ marginTop: 12 }}>
                      Default YouTube privacy
                    </label>
                    <select
                      id="cfg-youtube-privacy"
                      value={appConfig.youtube_default_privacy || "unlisted"}
                      onChange={(e) => setAppConfig((p) => ({ ...p, youtube_default_privacy: e.target.value }))}
                    >
                      <option value="public">public</option>
                      <option value="unlisted">unlisted</option>
                      <option value="private">private</option>
                    </select>
                    <label style={{ display: "flex", gap: 10, alignItems: "flex-start", cursor: "pointer", marginTop: 14 }}>
                      <input
                        type="checkbox"
                        checked={Boolean(appConfig.burn_subtitles_in_final_cut_default)}
                        onChange={(e) =>
                          setAppConfig((p) => ({ ...p, burn_subtitles_in_final_cut_default: e.target.checked }))
                        }
                      />
                      <span style={{ fontSize: "0.88rem", lineHeight: 1.45 }}>
                        <strong>Hands-off / agent runs:</strong> burn project subtitles into the final MP4 when{" "}
                        <code>subtitles.vtt</code> exists (same as the per-export checkbox under Compile video).
                      </span>
                    </label>
                  </div>
                </details>
                <details className="settings-section" defaultOpen>
                  <summary className="settings-section-summary">
                    <span className="settings-section-heading">OpenAI SDK — text chat backend</span>
                  </summary>
                  <div className="settings-section-body">
                    <p className="subtle" style={{ marginTop: -4 }}>
                      When <strong>Generation → Text provider</strong> is <strong>openai</strong>, choose whether chat
                      hits OpenAI/Azure (OpenAI section below) or <strong>LM Studio</strong> (next section). If the text
                      provider is <strong>lm_studio</strong>, chat always uses the LM Studio section and this choice is
                      ignored. TTS and image settings always use the OpenAI block (cloud).
                    </p>
                    <label htmlFor="cfg-openai-text-source">Chat backend</label>
                    <select
                      id="cfg-openai-text-source"
                      value={appConfig.openai_compatible_text_source === "lm_studio" ? "lm_studio" : "openai"}
                      onChange={(e) =>
                        setAppConfig((p) => ({
                          ...p,
                          openai_compatible_text_source: e.target.value === "lm_studio" ? "lm_studio" : "openai",
                        }))
                      }
                    >
                      <option value="openai">OpenAI cloud / Azure (OpenAI section)</option>
                      <option value="lm_studio">LM Studio (local — LM Studio section)</option>
                    </select>
                  </div>
                </details>
                <details className="settings-section" defaultOpen>
                  <summary className="settings-section-summary">
                    <span className="settings-section-heading">OpenAI</span>
                  </summary>
                  <div className="settings-section-body">
            <p className="subtle">
              API key, optional custom API base (Azure or proxy), text model for cloud, image model, and TTS. Used for
              chat only when <strong>OpenAI SDK — text chat backend</strong> is set to OpenAI cloud / Azure.
            </p>
            <label htmlFor="cfg-openai">OPENAI_API_KEY</label>
            {credKeyNote("openai_api_key")}
            <input
              id="cfg-openai"
              value={appConfig.openai_api_key || ""}
              onChange={(e) => setAppConfig((p) => ({ ...p, openai_api_key: e.target.value }))}
            />
            <label htmlFor="cfg-openai-base-url" style={{ marginTop: 12 }}>
              OPENAI_API_BASE_URL (optional — Azure OpenAI or OpenAI-compatible corporate endpoint)
            </label>
            <p className="subtle" style={{ marginTop: -4 }}>
              Not for LM Studio — use the LM Studio section. No trailing <code>/v1</code>; the API appends it. Empty =
              official OpenAI.
            </p>
            <input
              id="cfg-openai-base-url"
              placeholder="https://… or empty"
              value={appConfig.openai_api_base_url || ""}
              onChange={(e) => setAppConfig((p) => ({ ...p, openai_api_base_url: e.target.value }))}
            />
            <label htmlFor="cfg-openai-text-model">OPENAI_TEXT_MODEL (cloud / Azure chat)</label>
            <input
              id="cfg-openai-text-model"
              value={appConfig.openai_smoke_model || "gpt-4o-mini"}
              onChange={(e) => setAppConfig((p) => ({ ...p, openai_smoke_model: e.target.value }))}
            />
            <label htmlFor="cfg-openai-image-model">OPENAI_IMAGE_MODEL</label>
            <input
              id="cfg-openai-image-model"
              value={appConfig.openai_image_model || "gpt-image-1"}
              onChange={(e) => setAppConfig((p) => ({ ...p, openai_image_model: e.target.value }))}
            />
            <label htmlFor="cfg-tts">OPENAI_TTS_MODEL</label>
            <input
              id="cfg-tts"
              value={appConfig.openai_tts_model || "tts-1"}
              onChange={(e) => setAppConfig((p) => ({ ...p, openai_tts_model: e.target.value }))}
            />
            <label htmlFor="cfg-openai-tts-voice">OPENAI_TTS_VOICE (when speech provider is OpenAI)</label>
            <select
              id="cfg-openai-tts-voice"
              value={
                OPENAI_TTS_VOICE_OPTIONS.includes(String(appConfig.openai_tts_voice || "").toLowerCase())
                  ? String(appConfig.openai_tts_voice || "alloy").toLowerCase()
                  : "alloy"
              }
              onChange={(e) => setAppConfig((p) => ({ ...p, openai_tts_voice: e.target.value }))}
            >
              {OPENAI_TTS_VOICE_OPTIONS.map((v) => (
                <option key={v} value={v}>
                  {v}
                </option>
              ))}
            </select>
                  </div>
                </details>

                <details className="settings-section">
                  <summary className="settings-section-summary">
                    <span className="settings-section-heading">LM Studio</span>
                  </summary>
                  <div className="settings-section-body">
                    <p className="subtle">
                      Local OpenAI-compatible server. Set <strong>OpenAI SDK — text chat backend</strong> to LM Studio
                      so chat uses these fields. Base URL example: <code>http://127.0.0.1:1234</code> — no trailing{" "}
                      <code>/v1</code>. Model id must match the model loaded in LM Studio; leave API key blank unless
                      your server requires it.
                    </p>
                    <label htmlFor="cfg-lm-studio-base">LM_STUDIO_API_BASE_URL</label>
                    <input
                      id="cfg-lm-studio-base"
                      placeholder="http://host:port"
                      value={appConfig.lm_studio_api_base_url || ""}
                      onChange={(e) => setAppConfig((p) => ({ ...p, lm_studio_api_base_url: e.target.value }))}
                    />
                    <label htmlFor="cfg-lm-studio-key" style={{ marginTop: 12 }}>
                      LM_STUDIO_API_KEY (optional)
                    </label>
                    {credKeyNote("lm_studio_api_key")}
                    <input
                      id="cfg-lm-studio-key"
                      type="password"
                      autoComplete="off"
                      value={appConfig.lm_studio_api_key || ""}
                      onChange={(e) => setAppConfig((p) => ({ ...p, lm_studio_api_key: e.target.value }))}
                    />
                    <label htmlFor="cfg-lm-studio-text-model" style={{ marginTop: 12 }}>
                      LM_STUDIO_TEXT_MODEL
                    </label>
                    <p className="subtle" style={{ marginTop: -4 }}>
                      Empty = fall back to <strong>OPENAI_TEXT_MODEL</strong> in the OpenAI section.
                    </p>
                    <input
                      id="cfg-lm-studio-text-model"
                      placeholder="e.g. qwen3-32b"
                      value={appConfig.lm_studio_text_model || ""}
                      onChange={(e) => setAppConfig((p) => ({ ...p, lm_studio_text_model: e.target.value }))}
                    />
                    <label htmlFor="cfg-openai-local-max-tokens" style={{ marginTop: 12 }}>
                      OPENAI_LOCAL_CHAT_MAX_TOKENS
                    </label>
                    <p className="subtle" style={{ marginTop: -4 }}>
                      Completion budget for local OpenAI-compatible chat (long JSON / Qwen-class models).
                    </p>
                    <input
                      id="cfg-openai-local-max-tokens"
                      type="number"
                      min={512}
                      max={200000}
                      step={256}
                      value={Number(appConfig.openai_local_chat_max_tokens ?? 16384)}
                      onChange={(e) => {
                        const v = Math.max(512, Math.min(200_000, Number.parseInt(e.target.value, 10) || 16384));
                        setAppConfig((p) => ({ ...p, openai_local_chat_max_tokens: v }));
                      }}
                    />
                  </div>
                </details>

                <details className="settings-section">
                  <summary className="settings-section-summary">
                    <span className="settings-section-heading">OpenRouter (text)</span>
                  </summary>
                  <div className="settings-section-body">
            <label htmlFor="cfg-openrouter-key">OPENROUTER_API_KEY</label>
            {credKeyNote("openrouter_api_key")}
            <input
              id="cfg-openrouter-key"
              value={appConfig.openrouter_api_key || ""}
              onChange={(e) => setAppConfig((p) => ({ ...p, openrouter_api_key: e.target.value }))}
            />
            <label htmlFor="cfg-openrouter-model">OPENROUTER_TEXT_MODEL</label>
            <input
              id="cfg-openrouter-model"
              value={appConfig.openrouter_smoke_model || "openai/gpt-4o-mini"}
              onChange={(e) => setAppConfig((p) => ({ ...p, openrouter_smoke_model: e.target.value }))}
              placeholder="e.g. openai/gpt-4o-mini, anthropic/claude-3.5-sonnet"
            />
                  </div>
                </details>

                <details className="settings-section">
                  <summary className="settings-section-summary">
                    <span className="settings-section-heading">ElevenLabs (narration)</span>
                  </summary>
                  <div className="settings-section-body">
            <p className="subtle">
              Used when speech provider is ElevenLabs. Voices are loaded from your account (saved API key on the server).
            </p>
            <label htmlFor="cfg-elevenlabs-key">ELEVENLABS_API_KEY</label>
            {credKeyNote("elevenlabs_api_key")}
            <input
              id="cfg-elevenlabs-key"
              type="password"
              autoComplete="off"
              value={appConfig.elevenlabs_api_key || ""}
              onChange={(e) => setAppConfig((p) => ({ ...p, elevenlabs_api_key: e.target.value }))}
            />
            <div className="action-row" style={{ marginTop: 8 }}>
              <button type="button" className="secondary" onClick={() => void loadElevenlabsVoices()}>
                Refresh ElevenLabs voices
              </button>
            </div>
            {elevenlabsVoicesNote ? <p className="subtle">{elevenlabsVoicesNote}</p> : null}
            <label htmlFor="cfg-elevenlabs-voice">ELEVENLABS_VOICE_ID</label>
            {(() => {
              const curEl = String(appConfig.elevenlabs_voice_id || "").trim();
              const elInList = elevenlabsVoices.some((x) => x.id === curEl);
              return (
            <select
              id="cfg-elevenlabs-voice"
              value={curEl}
              onChange={(e) => setAppConfig((p) => ({ ...p, elevenlabs_voice_id: e.target.value }))}
            >
              <option value="">— select voice —</option>
              {!elInList && curEl ? (
                <option value={curEl}>
                  {curEl} (saved)
                </option>
              ) : null}
              {elevenlabsVoices.map((v) => (
                <option key={v.id} value={v.id}>
                  {v.label || v.id}
                </option>
              ))}
            </select>
              );
            })()}
            <label htmlFor="cfg-elevenlabs-model">ELEVENLABS_MODEL_ID</label>
            <input
              id="cfg-elevenlabs-model"
              value={appConfig.elevenlabs_model_id || "eleven_multilingual_v2"}
              onChange={(e) => setAppConfig((p) => ({ ...p, elevenlabs_model_id: e.target.value }))}
              placeholder="eleven_multilingual_v2"
            />
                  </div>
                </details>

                <details className="settings-section">
                  <summary className="settings-section-summary">
                    <span className="settings-section-heading">Grok / xAI</span>
                  </summary>
                  <div className="settings-section-body">
            <p className="subtle">Use either key name; both are kept in sync for compatibility.</p>
            <label htmlFor="cfg-grok-key">GROK_API_KEY / XAI_API_KEY</label>
            {credKeyNoteXaiGrok()}
            <input
              id="cfg-grok-key"
              value={appConfig.grok_api_key || appConfig.xai_api_key || ""}
              onChange={(e) =>
                setAppConfig((p) => ({ ...p, grok_api_key: e.target.value, xai_api_key: e.target.value }))
              }
            />
            <label htmlFor="cfg-xai-model">GROK_TEXT_MODEL</label>
            <input
              id="cfg-xai-model"
              value={appConfig.xai_text_model || "grok-2-latest"}
              onChange={(e) => setAppConfig((p) => ({ ...p, xai_text_model: e.target.value }))}
            />
            <label htmlFor="cfg-grok-image-model">GROK_IMAGE_MODEL</label>
            <input
              id="cfg-grok-image-model"
              value={appConfig.grok_image_model || "grok-2-image-1212"}
              onChange={(e) => setAppConfig((p) => ({ ...p, grok_image_model: e.target.value }))}
            />
            <label htmlFor="cfg-grok-video-model">GROK_VIDEO_MODEL</label>
            <input
              id="cfg-grok-video-model"
              value={appConfig.grok_video_model || "grok-2-video"}
              onChange={(e) => setAppConfig((p) => ({ ...p, grok_video_model: e.target.value }))}
            />
                  </div>
                </details>

                <details className="settings-section">
                  <summary className="settings-section-summary">
                    <span className="settings-section-heading">Google Gemini</span>
                  </summary>
                  <div className="settings-section-body">
            <p className="subtle">
              Use an API key from{" "}
              <a href="https://aistudio.google.com/apikey" target="_blank" rel="noreferrer">
                Google AI Studio
              </a>
              . Text uses Gemini; images use Imagen; video uses Veo (availability varies by account/region).
            </p>
            <label htmlFor="cfg-gemini-key">GEMINI_API_KEY</label>
            {credKeyNote("gemini_api_key")}
            <input
              id="cfg-gemini-key"
              type="password"
              autoComplete="off"
              value={appConfig.gemini_api_key || ""}
              onChange={(e) => setAppConfig((p) => ({ ...p, gemini_api_key: e.target.value }))}
            />
            <label htmlFor="cfg-gemini-text-model">GEMINI_TEXT_MODEL</label>
            <input
              id="cfg-gemini-text-model"
              value={appConfig.gemini_text_model || "gemini-2.0-flash"}
              onChange={(e) => setAppConfig((p) => ({ ...p, gemini_text_model: e.target.value }))}
            />
            <label htmlFor="cfg-gemini-image-model">GEMINI_IMAGE_MODEL</label>
            <input
              id="cfg-gemini-image-model"
              value={appConfig.gemini_image_model || "imagen-4.0-generate-001"}
              onChange={(e) => setAppConfig((p) => ({ ...p, gemini_image_model: e.target.value }))}
            />
            <label htmlFor="cfg-gemini-video-model">GEMINI_VIDEO_MODEL</label>
            <input
              id="cfg-gemini-video-model"
              value={appConfig.gemini_video_model || "veo-3.1-generate-preview"}
              onChange={(e) => setAppConfig((p) => ({ ...p, gemini_video_model: e.target.value }))}
            />
            <label htmlFor="cfg-gemini-tts-model">GEMINI_TTS_MODEL (when speech provider is Gemini)</label>
            <input
              id="cfg-gemini-tts-model"
              value={appConfig.gemini_tts_model || "gemini-2.5-flash-preview-tts"}
              onChange={(e) => setAppConfig((p) => ({ ...p, gemini_tts_model: e.target.value }))}
              placeholder="gemini-2.5-flash-preview-tts"
            />
            <label htmlFor="cfg-gemini-tts-voice">GEMINI_TTS_VOICE (prebuilt Gemini TTS)</label>
            {(() => {
              const gemRows = geminiTtsVoices.length ? geminiTtsVoices : GEMINI_TTS_VOICE_FALLBACK;
              const curG = String(appConfig.gemini_tts_voice || "Kore").trim() || "Kore";
              const gInList = gemRows.some((x) => x.id === curG);
              return (
            <select
              id="cfg-gemini-tts-voice"
              value={curG}
              onChange={(e) => setAppConfig((p) => ({ ...p, gemini_tts_voice: e.target.value }))}
            >
              {!gInList ? (
                <option value={curG}>
                  {curG} (saved)
                </option>
              ) : null}
              {gemRows.map((v) => (
                <option key={v.id} value={v.id}>
                  {v.label || v.id}
                </option>
              ))}
            </select>
              );
            })()}
                  </div>
                </details>

                <details className="settings-section">
                  <summary className="settings-section-summary">
                    <span className="settings-section-heading">FAL</span>
                  </summary>
                  <div className="settings-section-body">
            <p className="subtle">
              Model suggestions are read from <code>data/media_models_catalog.json</code> in the repo (no live HTTP on each page load).{" "}
              <strong>Sync from fal API</strong> downloads the latest active endpoints once and updates that file. <strong>Image/video generation</strong>{" "}
              calls <code>fal.run</code> when <code>FAL_KEY</code> is set and the <strong>Celery worker</strong> is running. Saving with an empty key field does not wipe{" "}
              <code>FAL_KEY</code> from <code>.env</code>.
            </p>
            {falCatalogNote ? <p className="subtle">{falCatalogNote}</p> : null}
            <label htmlFor="cfg-fal">FAL_KEY</label>
            {credKeyNote("fal_key")}
            <input
              id="cfg-fal"
              value={appConfig.fal_key || ""}
              onChange={(e) => setAppConfig((p) => ({ ...p, fal_key: e.target.value }))}
            />
            <div className="action-row" style={{ marginTop: 8 }}>
              <button type="button" className="secondary" onClick={() => loadFalCatalog({ force: true })}>
                Reload lists from disk
              </button>
              <button type="button" className="secondary" onClick={() => loadFalCatalog({ force: true, sync: true })}>
                Sync from fal API
              </button>
            </div>
            <p className="subtle" style={{ marginTop: 6 }}>
              Sync uses the fal Platform API (<strong>text-to-image</strong> + <strong>image-to-image</strong> merged for image; <strong>text-to-video</strong> +{" "}
              <strong>image-to-video</strong> merged for video). You can always type any endpoint id manually.
            </p>
            <p className="subtle" style={{ marginTop: 8 }}>
              <strong>Loaded:</strong> {falImageModels.length} image · {falVideoByKind.t2v.length} text-to-video · {falVideoByKind.i2v.length}{" "}
              image-to-video (after Reload/Sync). If counts are tiny, click <strong>Sync from fal API</strong> or check the API worker logs.
            </p>
            <p className="subtle" style={{ marginTop: 4 }}>
              <strong>Browser note:</strong> HTML <code>&lt;datalist&gt;</code> does not show every suggestion in a scrollable list — it filters as you
              type. Focus the field and type e.g. <code>flux</code> or <code>wan</code> to see matching endpoints; the full set is still loaded above.
            </p>
            <label htmlFor="cfg-fal-model">FAL image model (when image provider is fal)</label>
            <p className="subtle" style={{ marginTop: -4 }}>
              Type an endpoint id or use datalist suggestions (type to filter). Catalog entries are hints; any valid fal endpoint id works.
            </p>
            <input
              id="cfg-fal-model"
              list="fal-image-endpoints-datalist"
              placeholder="e.g. fal-ai/fast-sdxl"
              value={appConfig.fal_smoke_model || ""}
              onChange={(e) => setAppConfig((p) => ({ ...p, fal_smoke_model: e.target.value }))}
            />
            <datalist id="fal-image-endpoints-datalist">
              {falImageModels.map((m) => (
                <option key={m.endpoint_id} value={m.endpoint_id} label={`${m.display_name} — ${m.endpoint_id}`} />
              ))}
            </datalist>
            {falImageModels.length > 0 ? (
              <details style={{ marginTop: 10 }}>
                <summary className="subtle">Browse image catalog (scrollable list)</summary>
                <select
                  aria-label="Pick image endpoint id"
                  className="fal-catalog-browse"
                  size={14}
                  style={{ width: "100%", marginTop: 6 }}
                  value=""
                  onChange={(e) => {
                    const v = e.target.value;
                    if (v) setAppConfig((p) => ({ ...p, fal_smoke_model: v }));
                  }}
                >
                  <option value="">— select to copy into the field above —</option>
                  {falImageModels.map((m) => (
                    <option key={m.endpoint_id} value={m.endpoint_id}>
                      {m.display_name} — {m.endpoint_id}
                    </option>
                  ))}
                </select>
              </details>
            ) : null}
            <label htmlFor="cfg-fal-video-model" style={{ marginTop: 12 }}>
              FAL video model (when video provider is fal)
            </label>
            <p className="subtle" style={{ marginTop: -4 }}>
              {selectedFalVideoKind === "i2v" ? (
                <>
                  <strong>Image-to-video:</strong> animates the scene still. Generate or approve a scene image before running Video in the studio.
                </>
              ) : selectedFalVideoKind === "t2v" ? (
                <>
                  <strong>Text-to-video:</strong> uses narration-driven prompt only; no scene still required.
                </>
              ) : (
                <>
                  Choose <strong>text-to-video</strong> (prompt only) or <strong>image-to-video</strong> (needs a scene still). Browse groups below or type
                  an endpoint id.
                </>
              )}
            </p>
            <input
              id="cfg-fal-video-model"
              list="fal-video-endpoints-datalist"
              placeholder="e.g. fal-ai/minimax/video-01-live"
              value={appConfig.fal_video_model || ""}
              onChange={(e) => setAppConfig((p) => ({ ...p, fal_video_model: e.target.value }))}
            />
            <datalist id="fal-video-endpoints-datalist">
              {falVideoModels.map((m) => {
                const tag = falVideoEndpointKind(m) === "i2v" ? "I2V" : "T2V";
                return (
                  <option
                    key={m.endpoint_id}
                    value={m.endpoint_id}
                    label={`[${tag}] ${m.display_name} — ${m.endpoint_id}`}
                  />
                );
              })}
            </datalist>
            {falVideoModels.length > 0 ? (
              <details style={{ marginTop: 10 }}>
                <summary className="subtle">Browse video catalog (scrollable list)</summary>
                <select
                  aria-label="Pick video endpoint id"
                  className="fal-catalog-browse"
                  size={14}
                  style={{ width: "100%", marginTop: 6 }}
                  value=""
                  onChange={(e) => {
                    const v = e.target.value;
                    if (v) setAppConfig((p) => ({ ...p, fal_video_model: v }));
                  }}
                >
                  <option value="">— select to copy into the field above —</option>
                  {falVideoByKind.t2v.length > 0 ? (
                    <optgroup label="Text-to-video · prompt only">
                      {falVideoByKind.t2v.map((m) => (
                        <option key={m.endpoint_id} value={m.endpoint_id}>
                          {m.display_name} — {m.endpoint_id}
                        </option>
                      ))}
                    </optgroup>
                  ) : null}
                  {falVideoByKind.i2v.length > 0 ? (
                    <optgroup label="Image-to-video · uses scene still">
                      {falVideoByKind.i2v.map((m) => (
                        <option key={m.endpoint_id} value={m.endpoint_id}>
                          {m.display_name} — {m.endpoint_id}
                        </option>
                      ))}
                    </optgroup>
                  ) : null}
                </select>
              </details>
            ) : null}
                  </div>
                </details>

                <details className="settings-section">
                  <summary className="settings-section-summary">
                    <span className="settings-section-heading">ComfyUI</span>
                  </summary>
                  <div className="settings-section-body">
            <p className="subtle">
              API-format workflow JSON from the ComfyUI app. <strong>oss</strong> = your server; <strong>cloud</strong> ={" "}
              <a href="https://docs.comfy.org/development/cloud/api-reference" target="_blank" rel="noreferrer">
                Comfy Cloud
              </a>{" "}
              (<code>X-API-Key</code>). Still file → Image provider ComfyUI / auto-still before WAN; video file → <code>comfyui_wan</code>.
            </p>
            <label htmlFor="cfg-comfy-flavor">API flavor</label>
            <select
              id="cfg-comfy-flavor"
              value={appConfig.comfyui_api_flavor === "cloud" ? "cloud" : "oss"}
              onChange={(e) => setAppConfig((p) => ({ ...p, comfyui_api_flavor: e.target.value }))}
            >
              <option value="oss">OSS (self-hosted)</option>
              <option value="cloud">Comfy Cloud</option>
            </select>
            <label htmlFor="cfg-comfy-base">Base URL</label>
            <input
              id="cfg-comfy-base"
              placeholder="http://127.0.0.1:8188 — cloud: leave empty for cloud.comfy.org"
              value={appConfig.comfyui_base_url || ""}
              onChange={(e) => setAppConfig((p) => ({ ...p, comfyui_base_url: e.target.value }))}
            />
            <label htmlFor="cfg-comfy-api-key">API key</label>
            {credKeyNote("comfyui_api_key")}
            <input
              id="cfg-comfy-api-key"
              type="password"
              autoComplete="off"
              placeholder={appConfig.comfyui_api_flavor === "cloud" ? "Required for cloud (also COMFY_CLOUD_API_KEY in .env)" : "Optional Bearer for OSS proxies"}
              value={appConfig.comfyui_api_key || ""}
              onChange={(e) => setAppConfig((p) => ({ ...p, comfyui_api_key: e.target.value }))}
            />
            <label htmlFor="cfg-comfy-workflow">Still workflow JSON path</label>
            <input
              id="cfg-comfy-workflow"
              placeholder="absolute or repo-relative, e.g. data/comfyui_workflows/still_api.json"
              value={appConfig.comfyui_workflow_json_path || ""}
              onChange={(e) => setAppConfig((p) => ({ ...p, comfyui_workflow_json_path: e.target.value }))}
            />
            <label htmlFor="cfg-comfy-timeout">Still timeout (seconds)</label>
            <input
              id="cfg-comfy-timeout"
              type="number"
              min={30}
              max={7200}
              step={1}
              value={Number(appConfig.comfyui_timeout_sec) || 900}
              onChange={(e) =>
                setAppConfig((p) => ({ ...p, comfyui_timeout_sec: Number(e.target.value) }))
              }
            />
            <label htmlFor="cfg-comfy-vid-workflow">Video workflow JSON path (comfyui_wan)</label>
            <input
              id="cfg-comfy-vid-workflow"
              placeholder="separate file from still workflow"
              value={appConfig.comfyui_video_workflow_json_path || ""}
              onChange={(e) => setAppConfig((p) => ({ ...p, comfyui_video_workflow_json_path: e.target.value }))}
            />
            <label htmlFor="cfg-comfy-vid-timeout">Video timeout (seconds)</label>
            <input
              id="cfg-comfy-vid-timeout"
              type="number"
              min={60}
              max={7200}
              step={1}
              value={Number(appConfig.comfyui_video_timeout_sec) || 1800}
              onChange={(e) =>
                setAppConfig((p) => ({ ...p, comfyui_video_timeout_sec: Number(e.target.value) }))
              }
            />
            <label htmlFor="cfg-comfy-vid-load-img">LoadImage node id (i2v)</label>
            <input
              id="cfg-comfy-vid-load-img"
              placeholder="when using scene still as input"
              value={appConfig.comfyui_video_load_image_node_id || ""}
              onChange={(e) => setAppConfig((p) => ({ ...p, comfyui_video_load_image_node_id: e.target.value }))}
            />
            <label htmlFor="cfg-comfy-vid-use-scene" style={{ display: "flex", alignItems: "center", gap: 8, marginTop: 8 }}>
              <input
                id="cfg-comfy-vid-use-scene"
                type="checkbox"
                checked={appConfig.comfyui_video_use_scene_image !== false}
                onChange={(e) =>
                  setAppConfig((p) => ({ ...p, comfyui_video_use_scene_image: e.target.checked }))
                }
              />
              Use scene image for video (image-to-video)
            </label>
            <details style={{ marginTop: 16 }}>
              <summary className="subtle" style={{ cursor: "pointer", userSelect: "none" }}>
                Advanced — node overrides, polling, asset labels
              </summary>
              <div style={{ marginTop: 12, display: "flex", flexDirection: "column", gap: 10 }}>
                <label htmlFor="cfg-comfy-poll">Poll interval (seconds)</label>
                <input
                  id="cfg-comfy-poll"
                  type="number"
                  min={0.2}
                  max={10}
                  step={0.1}
                  value={Number(appConfig.comfyui_poll_interval_sec) || 1}
                  onChange={(e) =>
                    setAppConfig((p) => ({ ...p, comfyui_poll_interval_sec: Number(e.target.value) }))
                  }
                />
                <label htmlFor="cfg-comfy-prompt-node">COMFYUI_PROMPT_NODE_ID</label>
                <input
                  id="cfg-comfy-prompt-node"
                  placeholder="optional — else first CLIPTextEncode"
                  value={appConfig.comfyui_prompt_node_id || ""}
                  onChange={(e) => setAppConfig((p) => ({ ...p, comfyui_prompt_node_id: e.target.value }))}
                />
                <label htmlFor="cfg-comfy-prompt-key">COMFYUI_PROMPT_INPUT_KEY</label>
                <input
                  id="cfg-comfy-prompt-key"
                  placeholder="default text"
                  value={appConfig.comfyui_prompt_input_key || ""}
                  onChange={(e) => setAppConfig((p) => ({ ...p, comfyui_prompt_input_key: e.target.value }))}
                />
                <label htmlFor="cfg-comfy-neg-node">COMFYUI_NEGATIVE_NODE_ID</label>
                <input
                  id="cfg-comfy-neg-node"
                  value={appConfig.comfyui_negative_node_id || ""}
                  onChange={(e) => setAppConfig((p) => ({ ...p, comfyui_negative_node_id: e.target.value }))}
                />
                <label htmlFor="cfg-comfy-neg-text">COMFYUI_DEFAULT_NEGATIVE_PROMPT</label>
                <input
                  id="cfg-comfy-neg-text"
                  value={appConfig.comfyui_default_negative_prompt || ""}
                  onChange={(e) => setAppConfig((p) => ({ ...p, comfyui_default_negative_prompt: e.target.value }))}
                />
                <label htmlFor="cfg-comfy-model-label">COMFYUI_MODEL_NAME</label>
                <input
                  id="cfg-comfy-model-label"
                  placeholder="asset label — default workflow filename"
                  value={appConfig.comfyui_model_name || ""}
                  onChange={(e) => setAppConfig((p) => ({ ...p, comfyui_model_name: e.target.value }))}
                />
                <label htmlFor="cfg-comfy-vid-model">COMFYUI_VIDEO_MODEL_NAME</label>
                <input
                  id="cfg-comfy-vid-model"
                  value={appConfig.comfyui_video_model_name ?? "wan-2.1-comfyui"}
                  onChange={(e) => setAppConfig((p) => ({ ...p, comfyui_video_model_name: e.target.value }))}
                />
                <label htmlFor="cfg-comfy-vid-prompt-node">COMFYUI_VIDEO_PROMPT_NODE_ID</label>
                <input
                  id="cfg-comfy-vid-prompt-node"
                  value={appConfig.comfyui_video_prompt_node_id || ""}
                  onChange={(e) => setAppConfig((p) => ({ ...p, comfyui_video_prompt_node_id: e.target.value }))}
                />
                <label htmlFor="cfg-comfy-vid-prompt-key">COMFYUI_VIDEO_PROMPT_INPUT_KEY</label>
                <input
                  id="cfg-comfy-vid-prompt-key"
                  value={appConfig.comfyui_video_prompt_input_key || ""}
                  onChange={(e) => setAppConfig((p) => ({ ...p, comfyui_video_prompt_input_key: e.target.value }))}
                />
                <label htmlFor="cfg-comfy-vid-neg-node">COMFYUI_VIDEO_NEGATIVE_NODE_ID</label>
                <input
                  id="cfg-comfy-vid-neg-node"
                  value={appConfig.comfyui_video_negative_node_id || ""}
                  onChange={(e) => setAppConfig((p) => ({ ...p, comfyui_video_negative_node_id: e.target.value }))}
                />
                <label htmlFor="cfg-comfy-vid-neg-text">COMFYUI_VIDEO_DEFAULT_NEGATIVE_PROMPT</label>
                <input
                  id="cfg-comfy-vid-neg-text"
                  value={appConfig.comfyui_video_default_negative_prompt || ""}
                  onChange={(e) => setAppConfig((p) => ({ ...p, comfyui_video_default_negative_prompt: e.target.value }))}
                />
              </div>
            </details>
                  </div>
                </details>

                <details className="settings-section">
                  <summary className="settings-section-summary">
                    <span className="settings-section-heading">Research</span>
                  </summary>
                  <div className="settings-section-body">
            <label htmlFor="cfg-tavily">TAVILY_API_KEY</label>
            {credKeyNote("tavily_api_key")}
            <input
              id="cfg-tavily"
              value={appConfig.tavily_api_key || ""}
              onChange={(e) => setAppConfig((p) => ({ ...p, tavily_api_key: e.target.value }))}
            />
                  </div>
                </details>
                </>
              )}
              {settingsTab === "voice_ref" && (
                <details className="settings-section" defaultOpen>
                  <summary className="settings-section-summary">
                    <span className="settings-section-heading">Chatterbox voice reference</span>
                  </summary>
                  <div className="settings-section-body">
                  <InfoTip>
                    Record or upload a short, clear speech sample (a few seconds). The API saves it as a mono 24 kHz WAV under your storage root and
                    sets <code>chatterbox_voice_ref_path</code> for this workspace. Use <strong>Generation</strong> → <strong>Speech provider</strong>{" "}
                    → Chatterbox turbo or multilingual, then run <strong>Narration generate</strong> on a chapter.
                  </InfoTip>
                  <p className="subtle">
                    Requires <strong>ffmpeg</strong> on the API server. The browser will ask for microphone permission when you record.
                  </p>
                  {chatterboxVoiceRefErr ? <p className="err">{chatterboxVoiceRefErr}</p> : null}
                  <div className="action-row" style={{ marginTop: 12, flexWrap: "wrap", gap: 8 }}>
                    <button
                      type="button"
                      disabled={chatterboxVoiceRefBusy || chatterboxRecording}
                      onClick={() => void startChatterboxRecording()}
                    >
                      Record
                    </button>
                    <button
                      type="button"
                      className="secondary"
                      disabled={chatterboxVoiceRefBusy || !chatterboxRecording}
                      onClick={() => finishChatterboxRecording()}
                    >
                      Stop &amp; upload
                    </button>
                    <label className="secondary" style={{ display: "inline-flex", alignItems: "center", cursor: "pointer" }}>
                      <span style={{ marginRight: 8 }}>Upload file</span>
                      <input
                        type="file"
                        accept="audio/*,.webm,video/webm"
                        disabled={chatterboxVoiceRefBusy || chatterboxRecording}
                        style={{ maxWidth: 200 }}
                        onChange={(e) => {
                          const f = e.target.files?.[0];
                          e.target.value = "";
                          if (f) void uploadChatterboxFile(f);
                        }}
                      />
                    </label>
                    <button
                      type="button"
                      className="secondary"
                      disabled={
                        chatterboxVoiceRefBusy ||
                        !chatterboxVoiceRef?.has_reference
                      }
                      onClick={() => void deleteChatterboxVoiceRef()}
                    >
                      Clear reference
                    </button>
                    <button
                      type="button"
                      className="secondary"
                      disabled={chatterboxVoiceRefBusy}
                      onClick={() => void loadChatterboxVoiceRef()}
                    >
                      Refresh status
                    </button>
                  </div>
                  <p className="subtle" style={{ marginTop: 12 }}>
                    Status:{" "}
                    {chatterboxVoiceRef?.has_reference ? (
                      <>
                        <strong>reference saved</strong>
                        {chatterboxVoiceRef.storage_key ? (
                          <>
                            {" "}
                            (<code>{chatterboxVoiceRef.storage_key}</code>)
                          </>
                        ) : null}
                      </>
                    ) : (
                      <span>no file on disk (or path missing)</span>
                    )}
                    {chatterboxRecording ? (
                      <span style={{ marginLeft: 8, color: "var(--accent-warn, #c9a227)" }}>Recording…</span>
                    ) : null}
                  </p>
                  {chatterboxVoiceRef?.has_reference ? (
                    <div style={{ marginTop: 16 }}>
                      <p className="subtle" style={{ marginBottom: 8 }}>
                        Preview (normalized WAV)
                      </p>
                      <audio
                        key={chatterboxVoiceRef.updated_at || "ref"}
                        className="director-audio"
                        controls
                        src={apiChatterboxVoiceRefContentUrl(chatterboxVoiceRef.updated_at || "1")}
                      />
                    </div>
                  ) : null}
                  </div>
                </details>
              )}
            </div>
          </div>
        </section>
      ) : activePage === "characters" ? (
        <section className="panel usage-page characters-page">
          <header className="usage-page-header">
            <div>
              <h2>Characters</h2>
              <p className="subtle">
                Define recurring people and visual identities for this production. The character agent reads your director brief, research summary,
                and chapter scripts (or outlines), then fills this list. Edits here are prepended to image and video prompts so models keep faces and
                wardrobe consistent. Re-run Plan scenes on a chapter if you want the storyboard agent to re-align scene prompts with an updated bible.
              </p>
            </div>
            <div className="usage-page-toolbar">
              <button
                type="button"
                disabled={busy || !projectId || Boolean(charactersJobId)}
                onClick={async () => {
                  if (!projectId) return;
                  setBusy(true);
                  setError("");
                  try {
                    const b = await apiPostIdempotent(api, `/v1/projects/${projectId}/characters/generate`, {}, idem);
                    const jid = b.job?.id;
                    if (jid) setCharactersJobId(jid);
                    setMessage("Character agent job queued (replaces the current character list when it succeeds).");
                  } catch (e) {
                    setError(formatUserFacingError(e));
                  } finally {
                    setBusy(false);
                  }
                }}
              >
                Generate from story
              </button>
              <button type="button" className="secondary" disabled={!projectId} onClick={() => loadProjectCharacters(projectId)}>
                Reload
              </button>
            </div>
          </header>
          {!projectId ? (
            <p className="subtle">Open or create a project from the Editor tab first.</p>
          ) : charactersJobId ? (
            <p className="subtle">
              Job status: {charactersJob?.status ? friendlyRunStatus(charactersJob.status) : "…"}
            </p>
          ) : null}
          <div className="character-card-list">
            {projectCharacters.map((c) => (
              <div key={c.id} className="panel character-card" style={{ marginBottom: 16, padding: 14 }}>
                <div className="action-row" style={{ marginBottom: 10, flexWrap: "wrap", gap: 8 }}>
                  <input
                    aria-label="Character name"
                    value={c.name}
                    onChange={(e) =>
                      setProjectCharacters((prev) =>
                        prev.map((x) => (x.id === c.id ? { ...x, name: e.target.value } : x)),
                      )
                    }
                    style={{ flex: "1 1 180px", minWidth: 120 }}
                  />
                  <input
                    type="number"
                    aria-label="Sort order"
                    value={c.sort_order}
                    onChange={(e) =>
                      setProjectCharacters((prev) =>
                        prev.map((x) =>
                          x.id === c.id ? { ...x, sort_order: Number(e.target.value) || 0 } : x,
                        ),
                      )
                    }
                    style={{ width: 88 }}
                  />
                  <button
                    type="button"
                    disabled={busy}
                    onClick={async () => {
                      setBusy(true);
                      setError("");
                      try {
                        const r = await api(`/v1/projects/${projectId}/characters/${c.id}`, {
                          method: "PATCH",
                          body: JSON.stringify({
                            name: c.name,
                            sort_order: c.sort_order,
                            role_in_story: c.role_in_story,
                            visual_description: c.visual_description,
                            time_place_scope_notes: c.time_place_scope_notes || null,
                          }),
                        });
                        const b = await parseJson(r);
                        if (!r.ok) throw new Error(apiErrorMessage(b));
                        const row = b.data;
                        if (row?.id) {
                          setProjectCharacters((prev) => prev.map((x) => (x.id === row.id ? { ...x, ...row } : x)));
                        }
                        setMessage("Character saved.");
                      } catch (e) {
                        setError(formatUserFacingError(e));
                      } finally {
                        setBusy(false);
                      }
                    }}
                  >
                    Save
                  </button>
                  <button
                    type="button"
                    className="secondary"
                    disabled={busy}
                    onClick={async () => {
                      if (!window.confirm(`Remove “${c.name}” from this project?`)) return;
                      setBusy(true);
                      setError("");
                      try {
                        const r = await api(`/v1/projects/${projectId}/characters/${c.id}`, { method: "DELETE" });
                        const b = await parseJson(r);
                        if (!r.ok) throw new Error(apiErrorMessage(b));
                        setProjectCharacters((prev) => prev.filter((x) => x.id !== c.id));
                        setMessage("Character removed.");
                      } catch (e) {
                        setError(formatUserFacingError(e));
                      } finally {
                        setBusy(false);
                      }
                    }}
                  >
                    Delete
                  </button>
                </div>
                <label className="subtle" style={{ display: "block", marginBottom: 4 }}>
                  Role in story
                </label>
                <textarea
                  rows={2}
                  value={c.role_in_story || ""}
                  onChange={(e) =>
                    setProjectCharacters((prev) =>
                      prev.map((x) => (x.id === c.id ? { ...x, role_in_story: e.target.value } : x)),
                    )
                  }
                  style={{ width: "100%", marginBottom: 10 }}
                />
                <label className="subtle" style={{ display: "block", marginBottom: 4 }}>
                  Visual description (for image/video models)
                </label>
                <textarea
                  rows={4}
                  value={c.visual_description || ""}
                  onChange={(e) =>
                    setProjectCharacters((prev) =>
                      prev.map((x) => (x.id === c.id ? { ...x, visual_description: e.target.value } : x)),
                    )
                  }
                  style={{ width: "100%", marginBottom: 10 }}
                />
                <label className="subtle" style={{ display: "block", marginBottom: 4 }}>
                  Time / place / scope notes (optional)
                </label>
                <textarea
                  rows={2}
                  value={c.time_place_scope_notes || ""}
                  onChange={(e) =>
                    setProjectCharacters((prev) =>
                      prev.map((x) => (x.id === c.id ? { ...x, time_place_scope_notes: e.target.value } : x)),
                    )
                  }
                  style={{ width: "100%" }}
                />
              </div>
            ))}
          </div>
          {projectId && projectCharacters.length === 0 && !charactersJobId ? (
            <p className="subtle">No characters yet. Run script/chapters first, then use Generate from story.</p>
          ) : null}
          {projectId ? (
            <div className="action-row" style={{ marginTop: 12 }}>
              <button
                type="button"
                className="secondary"
                disabled={busy}
                onClick={async () => {
                  setBusy(true);
                  setError("");
                  try {
                    const r = await api(`/v1/projects/${projectId}/characters`, {
                      method: "POST",
                      body: JSON.stringify({ name: "New character" }),
                    });
                    const b = await parseJson(r);
                    if (!r.ok) throw new Error(apiErrorMessage(b));
                    const row = b.data;
                    if (row?.id) setProjectCharacters((prev) => [...prev, row]);
                    setMessage("Empty character added — edit and Save.");
                  } catch (e) {
                    setError(formatUserFacingError(e));
                  } finally {
                    setBusy(false);
                  }
                }}
              >
                Add character
              </button>
            </div>
          ) : null}
        </section>
      ) : activePage === "editor" ? (
        <div
          className="workspace-grid"
          ref={workspaceRef}
          style={{
            "--left-width": `${panelSizes.left}px`,
            "--right-width": `${panelSizes.right}px`,
            "--bottom-height": `${panelSizes.bottom}px`,
          }}
        >
        <section className="panel assets-panel">
          <h2>Project &amp; story</h2>
          <EditorCardColumn
            column="left"
            sections={[
              {
                id: "projects",
                title: "Projects",
                children: (
                  <>
                    <div className="action-row">
                      <button type="button" onClick={startNewProjectDraft} title="Opens the Pipeline panel to enter a brief and start an agent run">
                        New project
                      </button>
                      <button type="button" className="secondary" onClick={loadProjects}>
                        Reload list
                      </button>
                    </div>
                    <div className="projects-list">
                      {projects.map((p) => (
                        <div key={p.id} className="project-row-card">
                          <button
                            type="button"
                            className={`secondary project-row ${projectId === p.id ? "active" : ""}`}
                            onClick={() => openProject(p.id)}
                          >
                            <span className="project-title">{p.title}</span>
                            <small>
                              {p.status} · {p.workflow_phase}
                            </small>
                          </button>
                          <button
                            type="button"
                            className="project-row-delete"
                            onClick={() => deleteProject(p.id)}
                            title="Delete project"
                            aria-label="Delete project"
                          >
                            <i className="fa-solid fa-trash-can" aria-hidden="true" />
                          </button>
                        </div>
                      ))}
                      {projects.length === 0 ? (
                        <div className="subtle" style={{ padding: "16px 8px", textAlign: "center", lineHeight: 1.6 }}>
                          <div style={{ fontSize: "1.5rem", marginBottom: 6 }}>🎬</div>
                          No projects yet.
                          <br />
                          Click <strong>New project</strong> above (or open the <strong>Pipeline &amp; agent</strong> column on the right →{" "}
                          <strong>Project brief</strong>). Enter title &amp; topic, choose Manual / Auto / Hands-off, then press{" "}
                          <strong>Start</strong> (or <strong>Manual Run</strong> if a project is already open) to create the project and start the agent.{" "}
                          <strong>Automate</strong> only appears after you open an existing project from the list.
                        </div>
                      ) : null}
                    </div>
                  </>
                ),
              },
              {
                id: "musicMix",
                title: "Background music & final mix",
                info: <>Open a project to upload. Paste <strong>Timeline version ID</strong> under <strong>Timeline &amp; export → Compile video</strong>, then <strong>Save mix to timeline</strong> here.</>,
                children: (
                  <>
                    <label htmlFor="mbpick">Music bed</label>
                    <select
                      id="mbpick"
                      value={musicBedPick}
                      onChange={(e) => setMusicBedPick(e.target.value)}
                    >
                      <option value="">— None —</option>
                      {musicBeds.map((m) => (
                        <option key={m.id} value={m.id}>
                          {(m.title || m.id).slice(0, 60)}
                        </option>
                      ))}
                    </select>
                    <div style={{ marginTop: 8 }}>
                      <label htmlFor="mulic">License / source (required for upload)</label>
                      <input
                        id="mulic"
                        value={musicUploadLicense}
                        onChange={(e) => setMusicUploadLicense(e.target.value)}
                        placeholder="e.g. Original, Artlist license #…"
                      />
                    </div>
                    <div className="action-row" style={{ marginTop: 6 }}>
                      <input ref={musicFileInputRef} type="file" accept="audio/*,.mp3,.wav,.m4a,.aac,.flac,.ogg" />
                      <button type="button" className="secondary" disabled={busy || !projectId} onClick={() => void uploadMusicBedFile()}>
                        Upload music
                      </button>
                    </div>
                    <p className="subtle" style={{ marginTop: 6, fontSize: "0.72rem", lineHeight: 1.45 }}>
                      <strong>Uploaded</strong> beds appear in this picker for <strong>every project</strong> you open: when signed in they follow your account;
                      without account auth they are shared across the whole workspace.
                    </p>
                    <p className="subtle" style={{ marginTop: 6, fontSize: "0.72rem", lineHeight: 1.45 }}>
                      <strong>Scene timeline</strong> narration: each scene&rsquo;s VO is aligned to its clip in the final cut.
                    </p>
                    <div style={{ marginTop: 8 }}>
                      <label htmlFor="mmv">Music volume (0–1)</label>
                      <input
                        id="mmv"
                        type="range"
                        min={0}
                        max={1}
                        step={0.02}
                        value={mixMusicVol}
                        onChange={(e) => setMixMusicVol(Number(e.target.value))}
                      />
                      <span className="subtle" style={{ marginLeft: 8 }}>
                        {mixMusicVol.toFixed(2)}
                      </span>
                    </div>
                    <div style={{ marginTop: 8 }}>
                      <label htmlFor="mnv">Narration volume (0–4)</label>
                      <input
                        id="mnv"
                        type="range"
                        min={0}
                        max={4}
                        step={0.05}
                        value={mixNarrVol}
                        onChange={(e) => setMixNarrVol(Number(e.target.value))}
                      />
                      <span className="subtle" style={{ marginLeft: 8 }}>
                        {mixNarrVol.toFixed(2)}
                      </span>
                    </div>
                    <div className="action-row" style={{ marginTop: 8 }}>
                      <button
                        type="button"
                        className="secondary"
                        disabled={busy || !projectId || !String(timelineVersionId || "").trim()}
                        onClick={() => void loadTimelineMixFields()}
                      >
                        Reload mix from timeline
                      </button>
                      <button
                        type="button"
                        disabled={busy || !projectId || !String(timelineVersionId || "").trim()}
                        onClick={() => void saveTimelineMixToServer()}
                      >
                        Save mix to timeline
                      </button>
                    </div>
                  </>
                ),
              },
              {
                id: "transitions",
                title: "Transitions",
                info: (
                  <>
                    Dissolve between <strong>consecutive still images</strong> when the rough cut batches them (same timeline as music mix).
                    Set timeline ID under <strong>Timeline &amp; export → Compile video</strong>, then save.
                  </>
                ),
                children: (
                  <>
                    <label htmlFor="ccxf">Crossfade between stills (seconds)</label>
                    <input
                      id="ccxf"
                      type="range"
                      min={0}
                      max={2}
                      step={0.05}
                      value={clipCrossfadeSec}
                      onChange={(e) => setClipCrossfadeSec(Number(e.target.value))}
                    />
                    <span className="subtle" style={{ marginLeft: 8 }}>
                      {clipCrossfadeSec.toFixed(2)}s (0 = hard cuts)
                    </span>
                    <p className="subtle" style={{ marginTop: 8, fontSize: "0.72rem", lineHeight: 1.45 }}>
                      Does not add dissolves between full video clips—only between stills merged in one slideshow step. Re-run{" "}
                      <strong>Rough cut</strong> after changing this.
                    </p>
                    <div className="action-row" style={{ marginTop: 8 }}>
                      <button
                        type="button"
                        className="secondary"
                        disabled={busy || !projectId || !String(timelineVersionId || "").trim()}
                        onClick={() => void loadTimelineMixFields()}
                      >
                        Reload from timeline
                      </button>
                      <button
                        type="button"
                        disabled={busy || !projectId || !String(timelineVersionId || "").trim()}
                        onClick={() => void saveTimelineMixToServer()}
                      >
                        Save transitions to timeline
                      </button>
                    </div>
                  </>
                ),
              },
            ]}
          />
        </section>

        <section className="panel canvas-panel">
          <div className="canvas-panel-heading">
            <h2>Selected scene</h2>
            <div className="topbar-stats" role="group" aria-label="Project and automation status">
              <button
                type="button"
                className="topbar-stats-help"
                onClick={() => setShowShortcutHelp(true)}
                title="Keyboard shortcuts (?)"
              >
                ?
              </button>
              <div className="topbar-stat-chip">
                <span className="topbar-stat-chip__k">Project</span>
                <span className="topbar-stat-chip__sep" aria-hidden="true">
                  :
                </span>
                <span className="topbar-stat-chip__v">{projectId ? "Open" : "No project"}</span>
              </div>
              <div className="topbar-stat-chip">
                <span className="topbar-stat-chip__k">Automation</span>
                <span className="topbar-stat-chip__sep" aria-hidden="true">
                  :
                </span>
                <span className="topbar-stat-chip__v">
                  {headerProgressBanner ? (
                    <i className={headerProgressBanner.iconClassName} aria-hidden="true" />
                  ) : null}
                  {friendlyAgentRunStatus(run)}
                </span>
              </div>
              <div className="topbar-stat-chip">
                <span className="topbar-stat-chip__k">Chapter</span>
                <span className="topbar-stat-chip__sep" aria-hidden="true">
                  :
                </span>
                <span className="topbar-stat-chip__v">
                  {chapterId ? (chapterHumanNumber(chapters, chapterId) ?? "—") : "—"}
                </span>
              </div>
            </div>
          </div>
          <EditorCardColumn
            column="center"
            sceneTabIds={EDITOR_CENTER_SCENE_TAB_IDS}
            splitPreviewRow={{ leftIds: ["previewVisual"], rightIds: ["chapter", "scenes"] }}
            sections={[
              {
                id: "previewVisual",
                title: "Media preview",
                children: (
                  <div className="media-preview-card-inner">
                    <div className="media-preview-tablist" role="tablist" aria-label="Media preview">
                      <button
                        type="button"
                        role="tab"
                        id="media-preview-tab-scene"
                        aria-selected={mediaPreviewTab === "scene"}
                        aria-controls="media-preview-panel"
                        onClick={() => setMediaPreviewTab("scene")}
                      >
                        Scene media
                      </button>
                      <button
                        type="button"
                        role="tab"
                        id="media-preview-tab-compiled"
                        aria-selected={mediaPreviewTab === "compiled"}
                        aria-controls="media-preview-panel"
                        onClick={() => setMediaPreviewTab("compiled")}
                      >
                        Compiled video
                      </button>
                    </div>
                    <div
                      id="media-preview-panel"
                      className="media-preview-tabpanel"
                      role="tabpanel"
                      aria-labelledby={
                        mediaPreviewTab === "compiled" ? "media-preview-tab-compiled" : "media-preview-tab-scene"
                      }
                    >
                      {mediaPreviewTab === "compiled" ? (
                        <CompiledVideoPreview projectId={projectId} timelineVersionId={timelineVersionId} />
                      ) : selectedScene ? (
                        <div className="canvas-stage">
                        <div className="canvas-label">Scene {selectedScene.order_index + 1}</div>
                        <p>{selectedScene.purpose || selectedScene.visual_type}</p>
                        {previewUrl ? (
                          previewMediaError ? (
                            <div className="err" style={{ marginTop: 8 }}>
                              <strong>Preview couldn’t load.</strong>
                              <ul className="subtle" style={{ margin: "8px 0 0", paddingLeft: 18, lineHeight: 1.45 }}>
                                <li>
                                  Loaded from: <code>{apiBase || "(same origin)"}</code>
                                  {import.meta.env.DEV && !viteApiBaseEnvRaw ? (
                                    <>
                                      {" "}
                                      (dev: same-origin via Vite proxy; set <code>VITE_API_BASE_URL</code> in{" "}
                                      <code>apps/web/.env.development</code> only if the API is not proxied from this dev server)
                                    </>
                                  ) : null}
                                </li>
                                <li>
                                  <a href={previewUrl} target="_blank" rel="noreferrer">
                                    Open this asset URL
                                  </a>{" "}
                                  in a new tab. <strong>404</strong> usually means the file is missing under{" "}
                                  <code>LOCAL_STORAGE_ROOT</code> on the machine running the API, or the asset row still has a bad path (restart API
                                  after storage fixes). <strong>401</strong> means the API requires auth and the request had no valid credentials
                                  (reload the app after signing in).
                                </li>
                                <li>
                                  Production static hosting: build with <code>VITE_API_BASE_URL=https://your-api</code> and add your UI origin to API{" "}
                                  <code>CORS_EXTRA_ORIGINS</code>.
                                </li>
                              </ul>
                            </div>
                          ) : previewKind === "video" ? (
                            <video
                              key={previewUrl}
                              className="canvas-preview"
                              controls
                              playsInline
                              muted={Boolean(narrationPreviewSrc)}
                              src={previewUrl}
                              onError={() => setPreviewMediaError(true)}
                            />
                          ) : (
                            <img
                              key={previewUrl}
                              className="canvas-preview"
                              src={previewUrl}
                              alt="Scene preview"
                              onError={() => setPreviewMediaError(true)}
                            />
                          )
                        ) : (
                          <div className="subtle" style={{ marginTop: 8 }}>
                            {gallerySceneAssets.some((a) => a.status === "succeeded")
                              ? "No preview selected."
                              : gallerySceneAssets.length > 0
                                ? "No succeeded image or video yet — run Generate or wait for the job to finish."
                                : "No assets yet for this scene."}
                          </div>
                        )}
                      </div>
                    ) : (
                      <div className="canvas-stage subtle" style={{ padding: "12px 4px", textAlign: "center", lineHeight: 1.5 }}>
                        Select a scene in the Scenes list to preview media.
                      </div>
                    )}
                    </div>
                  </div>
                ),
              },
              {
                id: "chapter",
                title: "Chapter",
                children: (
                  <>
                    <label htmlFor="chap">Current chapter</label>
                    <select
                      id="chap"
                      value={chapterId}
                      onChange={(e) => {
                        setChapterId(e.target.value);
                        setScenes([]);
                        setExpandedScene(null);
                        setPinnedPreviewAssetId(null);
                      }}
                    >
                      <option value="">— select —</option>
                      {chaptersSorted(chapters).map((c, i) => (
                        <option key={c.id} value={c.id}>
                          {i + 1}. {c.title}
                        </option>
                      ))}
                    </select>
                    <div
                      className="action-row chapter-actions-row"
                      style={{
                        marginTop: 10,
                        display: "flex",
                        flexWrap: "nowrap",
                        gap: 6,
                        alignItems: "stretch",
                      }}
                    >
                      <button
                        type="button"
                        className="secondary chapter-reload-btn"
                        style={{ flex: 1, minWidth: 0, padding: "6px 8px", fontSize: "0.8rem" }}
                        disabled={!projectId}
                        onClick={() => loadChapters(projectId)}
                        title="Reload the chapter list from the server"
                      >
                        <i className="fa-solid fa-arrow-rotate-right fa-fw" aria-hidden="true" />
                        Chapters
                      </button>
                      <button
                        type="button"
                        className="secondary chapter-reload-btn"
                        style={{ flex: 1, minWidth: 0, padding: "6px 8px", fontSize: "0.8rem" }}
                        disabled={!chapterId}
                        onClick={() => loadScenes(chapterId)}
                        title="Reload scenes for the current chapter"
                      >
                        <i className="fa-solid fa-arrow-rotate-right fa-fw" aria-hidden="true" />
                        Scenes
                      </button>
                      <button
                        type="button"
                        style={{ flex: 1, minWidth: 0, padding: "6px 8px", fontSize: "0.8rem" }}
                        disabled={busy || !chapterId}
                        onClick={postScenesGenerate}
                        title={
                          scenes.length > 0
                            ? "Re-run scene planner — replaces all scenes (you will confirm). Use Extend scene in the Scenes card to add one beat."
                            : "Generate scene plan from the chapter script"
                        }
                      >
                        Plan
                      </button>
                    </div>
                  </>
                ),
              },
              {
                id: "scenes",
                title: "Scenes",
                children: (
                  <>
                    {scenesLoading && scenes.length === 0 ? (
                      <SkeletonSceneList rows={5} />
                    ) : null}
                    <div className="asset-tree" style={scenesLoading && scenes.length === 0 ? { display: "none" } : {}}>
                      {scenes.map((s) => {
                        const rows = sceneAssets[String(s.id)] || [];
                        const thumbAsset = bestSceneListThumbAsset(rows);
                        const thumbType = thumbAsset ? String(thumbAsset.asset_type || "").toLowerCase() : "";
                        const thumbSrc = thumbAsset
                          ? apiAssetContentUrl(
                              thumbAsset.id,
                              thumbAsset.updated_at || thumbAsset.created_at || thumbAsset.id,
                            )
                          : "";
                        const placeholderKind = sceneListFallbackThumbKind(s, rows);
                        const sceneActive = String(selectedSceneId) === String(s.id);
                        return (
                          <button
                            key={s.id}
                            type="button"
                            className={`asset-row asset-row--scene${sceneActive ? " active" : ""}${
                              exportAttentionSceneIdSet.has(String(s.id)) ? " asset-row--export-attention" : ""
                            }`}
                            onClick={() => {
                              setPinnedPreviewAssetId(null);
                              setExpandedScene(s.id);
                              loadSceneAssets(s.id);
                            }}
                            aria-current={sceneActive ? "true" : undefined}
                            aria-label={`Scene ${s.order_index + 1}`}
                          >
                            <div className="asset-row-thumb" aria-hidden="true">
                              {thumbSrc && thumbType === "image" ? (
                                <img src={thumbSrc} alt="" className="asset-row-thumb-media" loading="lazy" />
                              ) : thumbSrc && thumbType === "video" ? (
                                <video
                                  className="asset-row-thumb-media"
                                  muted
                                  playsInline
                                  preload="metadata"
                                  src={thumbSrc}
                                />
                              ) : (
                                <span className="asset-row-thumb-placeholder">
                                  <i
                                    className={`fa-solid ${placeholderKind === "video" ? "fa-video" : "fa-image"}`}
                                    aria-hidden="true"
                                  />
                                </span>
                              )}
                            </div>
                            <div className="asset-meta">
                              <div>{s.purpose?.slice(0, 64) || s.visual_type}</div>
                              <small>
                                {(() => {
                                  const g = sceneNarrationGuideMap.get(String(s.id));
                                  if (g) {
                                    return (
                                      <>
                                        ~{Math.round(g.narrationSec)}s VO · ~{g.clipHint} clip{g.clipHint !== 1 ? "s" : ""} @{" "}
                                        {sceneClipSec}s · assets {s.asset_count ?? 0}
                                      </>
                                    );
                                  }
                                  return (
                                    <>
                                      {s.planned_duration_sec}s planned · assets {s.asset_count ?? 0}
                                    </>
                                  );
                                })()}
                              </small>
                            </div>
                          </button>
                        );
                      })}
                    </div>
                    {!scenesLoading && scenes.length === 0 && chapterId ? (
                      <div className="subtle" style={{ padding: "14px 8px", textAlign: "center", lineHeight: 1.6 }}>
                        <div style={{ fontSize: "1.4rem", marginBottom: 6 }}>🎞️</div>
                        No scenes yet for this chapter.
                        <br />
                        Run the <strong>Scene planning</strong> step from the Pipeline tab to generate them automatically.
                      </div>
                    ) : null}
                    {!scenesLoading && scenes.length === 0 && !chapterId ? (
                      <div className="subtle" style={{ padding: "14px 8px", textAlign: "center", lineHeight: 1.6 }}>
                        Select a chapter in the Chapter card to load its scenes.
                      </div>
                    ) : null}
                    {scenes.length > 0 ? (
                      <div className="subtle" style={{ marginTop: 10, fontSize: "0.72rem", lineHeight: 1.45 }}>
                        <strong>Tip:</strong> Generate scene VO to get accurate per-scene duration targets. Until then, row hints use each
                        scene&apos;s planned duration and your {sceneClipSec}s clip setting from Settings.
                      </div>
                    ) : null}
                    <div
                      style={{
                        marginTop: 10,
                        display: "flex",
                        justifyContent: "center",
                        width: "100%",
                      }}
                    >
                      <button
                        type="button"
                        className="secondary"
                        disabled={busy || !chapterId || scenes.length === 0}
                        onClick={postScenesExtend}
                        title="Add one more scene that continues from the last planned beats (uses chapter script + prior scenes)"
                      >
                        Extend scene
                      </button>
                    </div>
                  </>
                ),
              },
              {
                id: "previewNarration",
                  title: "Scene narration (audio)",
                  tabShortTitle: "Narration",
                  show: Boolean(projectId),
                  info: (
                    <>
                      Queue TTS for <strong>every scene</strong> in this project that has narration text but no audio yet. Each scene gets its own job; progress shows in{" "}
                      <strong>Background jobs</strong> below.
                    </>
                  ),
                  children: (
                    <div className="canvas-narration" style={{ marginTop: 0 }}>
                      <p className="subtle" style={{ margin: "0 0 10px" }}>
                        Audio for the selected scene (per-scene TTS).
                      </p>
                      <div className="audio-panel-actions" style={{ flexWrap: "wrap", gap: 8, marginBottom: 12 }}>
                        <button
                          type="button"
                          disabled={busy || !projectId}
                          onClick={async () => {
                            if (!projectId) return;
                            setBusy(true);
                            setMessage("");
                            setError("");
                            try {
                              const r = await api(`/v1/projects/${projectId}/narration/generate-all-scenes`, { method: "POST" });
                              const b = await parseJson(r);
                              if (!r.ok) throw new Error(apiErrorMessage(b));
                              const d = b.data || {};
                              setMessage(`Queued ${d.jobs_queued || 0} scene VO jobs (${d.scenes_skipped || 0} skipped).`);
                            } catch (err) {
                              setError(String(err.message || err));
                            } finally {
                              setBusy(false);
                            }
                          }}
                        >
                          Generate all scene VO
                        </button>
                      </div>
                      {narrationPreviewSrc ? (
                        <audio
                          key={narrationPreviewSrc}
                          className="canvas-narration-audio director-audio"
                          controls
                          src={narrationPreviewSrc}
                        >
                          {narrationPreviewIsSceneTrack &&
                          sceneNarrationMeta?.has_subtitles &&
                          selectedSceneId ? (
                            <track
                              kind="captions"
                              srcLang="en"
                              label="Narration"
                              src={apiSceneNarrationSubtitlesUrl(
                                selectedSceneId,
                                sceneNarrationMeta.created_at || sceneNarrationMeta.track_id || "",
                              )}
                            />
                          ) : null}
                        </audio>
                      ) : (
                        <p className="subtle" style={{ margin: 0, fontSize: "0.85rem" }}>
                          Select a scene with generated VO to preview it here.
                        </p>
                      )}
                    </div>
                  ),
                },
                {
                  id: "mediaJobs",
                  title: "Background jobs",
                  tabShortTitle: "Jobs",
                  children: (
                    <div className="subtle">
                      <div
                        title={celeryStatusDetail || undefined}
                        style={{
                          display: "flex",
                          alignItems: "center",
                          gap: 10,
                          marginBottom: 10,
                          padding: "8px 10px",
                          borderRadius: 6,
                          background: celeryStatus === "online"
                            ? "rgba(40,167,69,0.12)"
                            : celeryStatus === "restarting"
                              ? "rgba(255,193,7,0.12)"
                              : "rgba(220,53,69,0.12)",
                        }}
                      >
                        <span
                          style={{
                            width: 10,
                            height: 10,
                            borderRadius: "50%",
                            flexShrink: 0,
                            background: celeryStatus === "online"
                              ? "#28a745"
                              : celeryStatus === "restarting"
                                ? "#ffc107"
                                : "#dc3545",
                            boxShadow: celeryStatus === "online"
                              ? "0 0 6px rgba(40,167,69,0.6)"
                              : celeryStatus === "restarting"
                                ? "0 0 6px rgba(255,193,7,0.6)"
                                : "0 0 6px rgba(220,53,69,0.6)",
                          }}
                        />
                        <span style={{ fontWeight: 600, fontSize: "0.82rem" }}>
                          Celery worker:{" "}
                          {celeryStatus === "online"
                            ? "Online"
                            : celeryStatus === "restarting"
                              ? "Restarting…"
                              : celeryStatus === "unknown"
                                ? "Checking…"
                                : "Offline"}
                        </span>
                        {celeryWorkers.length > 0 && (
                          <span className="subtle" style={{ fontSize: "0.7rem" }}>
                            ({celeryWorkers.length} worker{celeryWorkers.length !== 1 ? "s" : ""})
                          </span>
                        )}
                        <button
                          type="button"
                          className="secondary"
                          style={{ marginLeft: "auto", fontSize: "0.75rem", padding: "3px 10px" }}
                          disabled={celeryRestarting}
                          onClick={() => {
                            const ok = window.confirm(
                              "Restart the Celery worker? Running tasks will be interrupted.",
                            );
                            if (ok) void restartCelery();
                          }}
                        >
                          {celeryRestarting ? "Restarting…" : "Restart"}
                        </button>
                      </div>
                      <p style={{ marginTop: 0 }}>
                        Tracked in UI: {mediaJobId ? `${mediaJobId.slice(0, 8)}…` : "—"}
                        {mediaPoll ? " (polling…)" : ""}
                        {mediaJob?.status ? ` — ${friendlyRunStatus(mediaJob.status)}` : ""}
                      </p>
                      <div style={{ display: "flex", alignItems: "center", gap: 6, marginBottom: 8, marginTop: 4 }}>
                        <span style={{ fontSize: "0.78rem", color: "rgba(255,255,255,0.45)" }}>Job queue</span>
                        <InfoTip>Queued and running work for this project (refreshes every few seconds). Job concurrency caps are off by default; cancel revokes the Celery task when possible.</InfoTip>
                      </div>
                      <div className="action-row" style={{ marginBottom: 10, alignItems: "center" }}>
                        <button
                          type="button"
                          className="secondary"
                          disabled={busy}
                          onClick={() => {
                            const ok = window.confirm(
                              "Cancel all queued jobs and agent runs, and purge the Celery queue? Running tasks are not stopped.",
                            );
                            if (ok) void clearTaskBacklog();
                          }}
                        >
                          Clear queue backlog
                        </button>
                        <InfoTip>Cancels every <em>queued</em> job and agent run for this workspace, then purges pending Celery messages. Does <strong>not</strong> stop work already running on the worker.</InfoTip>
                      </div>
                      {activeJobsLoadErr ? <p className="err">{activeJobsLoadErr}</p> : null}
                      {!projectId ? (
                        <p className="subtle">Open a project to list jobs.</p>
                      ) : activeProjectJobs.length === 0 ? (
                        <p className="subtle">No queued or running jobs for this project.</p>
                      ) : (
                        <ul className="active-jobs-list" style={{ listStyle: "none", padding: 0, margin: 0 }}>
                          {activeProjectJobs.map((j) => (
                            <li
                              key={j.id}
                              style={{
                                display: "flex",
                                flexWrap: "wrap",
                                alignItems: "center",
                                gap: 8,
                                padding: "6px 0",
                                borderBottom: "1px solid var(--border-subtle, #333)",
                              }}
                            >
                              <span style={{ fontFamily: "monospace", fontSize: 12 }}>{String(j.id).slice(0, 8)}…</span>
                              <span>{j.type}</span>
                              <span>{friendlyRunStatus(j.status)}</span>
                              <button
                                type="button"
                                className="secondary"
                                style={{ marginLeft: "auto" }}
                                onClick={() => void cancelBackgroundJob(j.id)}
                              >
                                Cancel
                              </button>
                            </li>
                          ))}
                        </ul>
                      )}
                      <p className="subtle" style={{ marginTop: 10, marginBottom: 0 }}>
                        After a browser refresh, the app reloads the project and resumes polling active jobs from the API.
                      </p>
                    </div>
                  ),
                },
                {
                  id: "mediaGen",
                  title: "Generate media",
                  tabShortTitle: "Generate",
                  show: Boolean(selectedScene),
                  children: selectedScene ? (
                    <>
                      {selectedNarrGuide ? (
                        <div style={{ marginBottom: 12 }}>
                          <p className="subtle" style={{ margin: "0 0 6px", fontSize: "0.78rem", lineHeight: 1.45 }}>
                            <strong>Vs narration:</strong> ~{Math.round(selectedNarrGuide.narrationSec)}s VO for this beat (
                            {selectedNarrGuide.source === "narration_audio"
                              ? "chapter audio × this scene’s script share"
                              : "planned duration"}
                            ) — about <strong>{selectedNarrGuide.clipHint}</strong> image or video clip
                            {selectedNarrGuide.clipHint !== 1 ? "s" : ""} @ {sceneClipSec}s.
                          </p>
                          <div className="subtle" style={{ fontSize: "0.7rem", marginBottom: 4 }}>
                            Media vs target: ~{Math.round(selectedCoveredSec)}s / ~{Math.round(selectedNarrGuide.narrationSec)}s (succeeded assets;
                            open this scene so assets load)
                          </div>
                          <div className="studio-narr-progress" aria-label="Scene media coverage vs narration target">
                            <div style={{ width: `${selectedNarrProgressPct}%` }} />
                          </div>
                        </div>
                      ) : null}
                      <div className="action-row">
                        <button
                          type="button"
                          data-testid="studio-scene-generate-image"
                          disabled={busy}
                          onClick={() => postImage(selectedScene.id, "generate-image", {})}
                        >
                          Image
                        </button>
                        <button type="button" disabled={busy} onClick={() => postImage(selectedScene.id, "generate-video", {})}>
                          Video
                        </button>
                      </div>
                      <label className="subtle" style={{ display: "flex", gap: 8, alignItems: "center", margin: "8px 0 4px", fontSize: "0.8rem", cursor: "pointer" }}>
                        <input
                          type="checkbox"
                          checked={refineBracketImageWithLlm}
                          onChange={(e) => setRefineBracketImageWithLlm(e.target.checked)}
                        />
                        Refine <code>[bracket]</code> hints with LLM (optional; uses your configured text API)
                      </label>
                      <p className="subtle" style={{ margin: "0 0 8px", fontSize: "0.76rem", lineHeight: 1.45 }}>
                        In narration, wrap key visuals in square brackets — e.g.{" "}
                        <code>There [mermaids] were thought gone until one [reappeared on the shores of Atlantis].</code> Still images
                        (and video text prompts) prioritize those hints, combined with the project art style. Checking the box above runs an
                        extra LLM pass to merge hints into one precise still prompt (never automatic).
                      </p>
                      {String(appConfig.active_video_provider || "fal").trim().toLowerCase() === "fal" &&
                      selectedFalVideoKind === "i2v" ? (
                        <p className="subtle" style={{ margin: "8px 0 0", fontSize: "0.78rem", lineHeight: 1.45 }}>
                          <strong>FAL · image-to-video:</strong> animates the latest scene image using <code>video_prompt</code> + character/style
                          — run <strong>Image</strong> first (or approve a still), then <strong>Video</strong>.
                        </p>
                      ) : String(appConfig.active_video_provider || "fal").trim().toLowerCase() === "fal" &&
                        selectedFalVideoKind === "t2v" ? (
                        <p className="subtle" style={{ margin: "8px 0 0", fontSize: "0.78rem", lineHeight: 1.45 }}>
                          <strong>FAL · text-to-video:</strong> uses <code>video_prompt</code> from the scene package when present, otherwise
                          narration / purpose (no still required).
                        </p>
                      ) : null}
                      <p className="subtle" style={{ margin: "10px 0 6px" }}>
                        Queue one image job per scene <strong>in story order</strong> (optional range below for sectional runs). Waits the{" "}
                        <strong>batch image spacing</strong> from Settings → Studio (default 5s) between each enqueue so providers are not flooded.
                      </p>
                      <div
                        className="action-row subtle"
                        style={{ flexWrap: "wrap", alignItems: "center", gap: 8, marginBottom: 8, fontSize: "0.85rem" }}
                      >
                        <span>Scene range (1–{scenes.length}, leave blank for all):</span>
                        <label style={{ display: "inline-flex", alignItems: "center", gap: 4 }}>
                          From
                          <input
                            type="number"
                            min={1}
                            max={scenes.length || 1}
                            value={batchImageRangeFrom}
                            onChange={(e) => setBatchImageRangeFrom(e.target.value)}
                            disabled={Boolean(batchImagesProgress) || !chapterId || scenes.length === 0}
                            placeholder="1"
                            style={{ width: 56 }}
                            aria-label="Batch from scene number"
                          />
                        </label>
                        <label style={{ display: "inline-flex", alignItems: "center", gap: 4 }}>
                          To
                          <input
                            type="number"
                            min={1}
                            max={scenes.length || 1}
                            value={batchImageRangeTo}
                            onChange={(e) => setBatchImageRangeTo(e.target.value)}
                            disabled={Boolean(batchImagesProgress) || !chapterId || scenes.length === 0}
                            placeholder={String(scenes.length || "")}
                            style={{ width: 56 }}
                            aria-label="Batch to scene number"
                          />
                        </label>
                      </div>
                      <div className="action-row" style={{ flexWrap: "wrap", alignItems: "center" }}>
                        <button
                          type="button"
                          className="secondary"
                          disabled={Boolean(batchImagesProgress) || busy || !chapterId || scenes.length === 0}
                          onClick={() => void startBatchChapterImages()}
                        >
                          All images (chapter)
                        </button>
                        {batchImagesProgress ? (
                          <button type="button" className="secondary" onClick={stopBatchChapterImages}>
                            Stop batch
                          </button>
                        ) : null}
                      </div>
                      {batchImagesProgress ? (
                        <p className="subtle" style={{ marginTop: 8 }}>
                          Batch: {batchImagesProgress.done} / {batchImagesProgress.total} — {batchImagesProgress.label}
                        </p>
                      ) : null}
                    </>
                  ) : null,
                },
                {
                  id: "retryPrompt",
                  title: "Image prompt for retry",
                  tabShortTitle: "Img prompt",
                  show: Boolean(selectedScene),
                  children: selectedScene ? (
                    <>
                      <p className="subtle" style={{ margin: "0 0 6px" }}>
                        Pre-filled like <strong>Image</strong> above; edit the full prompt, then retry.
                      </p>
                      <textarea rows={5} value={retryPrompt} onChange={(e) => setRetryPrompt(e.target.value)} />
                      <div className="action-row">
                        <button
                          type="button"
                          className="secondary"
                          disabled={busy || promptEnhanceImageBusy || !String(retryPrompt || "").trim()}
                          onClick={() => void enhanceRetryImagePrompt()}
                          title="Rewrite the prompt using the previous scene and project character details"
                        >
                          {promptEnhanceImageBusy ? "Improving…" : "Improve prompt"}
                        </button>
                        <button
                          type="button"
                          className="secondary"
                          disabled={busy}
                          onClick={() =>
                            postImage(selectedScene.id, "retry", {
                              image_prompt_override: retryPrompt.trim() || undefined,
                              generation_tier: "preview",
                            })
                          }
                        >
                          Retry image
                        </button>
                      </div>
                    </>
                  ) : null,
                },
                {
                  id: "retryVideoPrompt",
                  title: "Video / motion prompt for retry",
                  tabShortTitle: "Motion",
                  show: Boolean(selectedScene),
                  children: selectedScene ? (
                    <>
                      <p className="subtle" style={{ margin: "0 0 6px" }}>
                        Motion and camera for generative video (zoom, pan, angle, pace). Pre-filled from{" "}
                        <code>prompt_package_json.video_prompt</code> when the storyboard set it; edit and queue <strong>Retry video</strong>.{" "}
                        <strong>Local still→video</strong> uses the same text for coarse Ken Burns / pan hints (e.g. &quot;zoom in&quot;, &quot;pan
                        left&quot;).
                      </p>
                      <textarea rows={4} value={retryVideoPrompt} onChange={(e) => setRetryVideoPrompt(e.target.value)} />
                      <div className="action-row">
                        <button
                          type="button"
                          className="secondary"
                          disabled={busy}
                          onClick={() =>
                            postImage(selectedScene.id, "generate-video", {
                              video_prompt_override: retryVideoPrompt.trim() || undefined,
                              generation_tier: "preview",
                            })
                          }
                        >
                          Retry video
                        </button>
                      </div>
                    </>
                  ) : null,
                },
                {
                  id: "scriptExcerpt",
                  title: "Scene script (VO)",
                  tabShortTitle: "Script",
                  show: Boolean(selectedScene),
                  children: selectedScene ? (
                    <>
                      <p className="subtle" style={{ margin: "0 0 6px" }}>
                        Spoken narration for this beat. Saved to the server; used for image fallbacks, scene VO TTS, and exports. Video uses{" "}
                        <code>video_prompt</code> in the scene package when present (storyboard / refine). Max 12k characters.
                      </p>
                      <textarea
                        className="scene-script-excerpt scene-script-editor"
                        rows={10}
                        maxLength={12000}
                        value={sceneNarrationDraft}
                        onChange={(e) => {
                          setSceneNarrationDraft(e.target.value);
                          setSceneNarrationDirty(true);
                        }}
                        spellCheck
                        aria-label="Scene narration script"
                      />
                      <div className="subtle" style={{ marginTop: 6, fontSize: "0.72rem" }}>
                        {(() => {
                          const words = narrationWordCount(sceneNarrationDraft);
                          const readSec = Math.round((words / 125) * 60);
                          const budget = Number(selectedScene?.planned_duration_sec) || 0;
                          const diff = budget > 0 ? readSec - budget : 0;
                          return (
                            <>
                              {words.toLocaleString()} words · ~{readSec}s read time
                              {budget > 0 ? (
                                diff > 3 ? (
                                  <span style={{ marginLeft: 6, color: "var(--accent-err, #e05252)" }}>
                                    ↑ {diff}s over budget ({budget}s)
                                  </span>
                                ) : diff < -3 ? (
                                  <span style={{ marginLeft: 6, color: "var(--accent-warn, #c9a227)" }}>
                                    ↓ {Math.abs(diff)}s under budget ({budget}s)
                                  </span>
                                ) : words > 0 ? (
                                  <span style={{ marginLeft: 6, color: "var(--accent-ok, #4caf50)" }}>
                                    ✓ On budget ({budget}s)
                                  </span>
                                ) : null
                              ) : null}
                              {sceneNarrationDirty ? (
                                <span style={{ marginLeft: 8, color: "var(--accent-warn, #c9a227)" }}>Unsaved changes</span>
                              ) : null}
                            </>
                          );
                        })()}
                      </div>
                      <div
                        className="panel"
                        style={{
                          marginTop: 10,
                          padding: 10,
                          background: "var(--panel-elevated, rgba(0,0,0,0.04))",
                        }}
                      >
                        <p className="subtle" style={{ margin: "0 0 8px", fontSize: "0.85rem" }}>
                          <strong>Expand script</strong> — lengthen the current text with the model. Set a rough sentence target
                          and optional notes (facts to add, tone, pacing).
                        </p>
                        <div
                          className="action-row"
                          style={{ flexWrap: "wrap", gap: 10, alignItems: "flex-end" }}
                        >
                          <label className="subtle" style={{ display: "flex", flexDirection: "column", gap: 4, fontSize: "0.85rem" }}>
                            Sentences (approx.)
                            <input
                              type="number"
                              min={1}
                              max={40}
                              value={sceneVoExpandSentenceTarget}
                              onChange={(e) => {
                                const v = parseInt(e.target.value, 10);
                                setSceneVoExpandSentenceTarget(Number.isFinite(v) ? Math.min(40, Math.max(1, v)) : 6);
                              }}
                              style={{ width: 80 }}
                              aria-label="Target sentence count for expansion"
                            />
                          </label>
                          <label
                            className="subtle"
                            style={{
                              display: "flex",
                              flexDirection: "column",
                              gap: 4,
                              flex: 1,
                              minWidth: 160,
                              fontSize: "0.85rem",
                            }}
                          >
                            Expansion context (optional)
                            <textarea
                              rows={2}
                              maxLength={2000}
                              placeholder="e.g. Mention the year, add one human detail, keep sentences short…"
                              value={sceneVoExpandContext}
                              onChange={(e) => setSceneVoExpandContext(e.target.value)}
                              style={{ width: "100%", minHeight: 44, resize: "vertical", fontSize: "0.85rem" }}
                              aria-label="Optional context for script expansion"
                            />
                          </label>
                          <button
                            type="button"
                            className="secondary"
                            disabled={
                              busy ||
                              promptEnhanceVoBusy ||
                              promptExpandVoBusy ||
                              sceneNarrationSaving ||
                              !String(sceneNarrationDraft || "").trim()
                            }
                            onClick={() => void expandSceneVoScript()}
                            title="Call the text model to expand this scene’s narration"
                          >
                            {promptExpandVoBusy ? "Expanding…" : "Expand script"}
                          </button>
                        </div>
                      </div>
                      <div className="action-row" style={{ marginTop: 10, flexWrap: "wrap", gap: 8 }}>
                        <button
                          type="button"
                          className="secondary"
                          disabled={
                            busy ||
                            promptEnhanceVoBusy ||
                            promptExpandVoBusy ||
                            sceneNarrationSaving ||
                            !String(sceneNarrationDraft || "").trim()
                          }
                          onClick={() => void enhanceSceneVoFromStyle()}
                          title="Rewrite narration to match the project narration style (e.g. question-and-answer structure from the style prompt)"
                        >
                          {promptEnhanceVoBusy ? "Improving…" : "Improve VO"}
                        </button>
                        <button
                          type="button"
                          disabled={sceneNarrationSaving || !sceneNarrationDirty}
                          onClick={() => void saveSceneNarrationDraft()}
                        >
                          Save narration
                        </button>
                        <button
                          type="button"
                          className="secondary"
                          disabled={sceneNarrationSaving || !sceneNarrationDirty}
                          onClick={() => revertSceneNarrationDraft()}
                        >
                          Revert
                        </button>
                        <button
                          type="button"
                          className="secondary"
                          disabled={
                            busy ||
                            !selectedScene ||
                            !(String(sceneNarrationDraft || "").trim().length >= 2)
                          }
                          onClick={() => {
                            if (!selectedScene) return;
                            void queueMediaJob(
                              `/v1/scenes/${encodeURIComponent(selectedScene.id)}/narration/generate`,
                              {},
                              "Scene narration (VO) queued…",
                            );
                          }}
                          title="Synthesize this scene’s script as audio for the final mix (per-scene timeline)."
                        >
                          Generate scene VO
                        </button>
                      </div>
                    </>
                  ) : null,
                },
                {
                  id: "sceneAssets",
                  title: "Assets for this scene",
                  tabShortTitle: "Assets",
                  show: Boolean(selectedScene),
                  children: selectedScene ? (
                    <>
                      <p className="subtle" style={{ margin: "0 0 8px" }}>
                        Rejected assets are hidden. Use Earlier / Later to set playback order for approved images in the rough cut.
                      </p>
                      <div
                        className="action-row"
                        style={{ marginBottom: 10, flexWrap: "wrap", gap: 8, alignItems: "center" }}
                      >
                        <label className="subtle" style={{ display: "flex", gap: 6, alignItems: "center", fontSize: "0.85rem" }}>
                          Upload clip
                          <select
                            value={sceneClipUploadKind}
                            onChange={(e) => setSceneClipUploadKind(e.target.value)}
                            disabled={busy}
                            style={{ fontSize: "0.8rem" }}
                          >
                            <option value="auto">Auto-detect</option>
                            <option value="image">Image</option>
                            <option value="video">Video</option>
                            <option value="audio">Audio</option>
                          </select>
                        </label>
                        <input
                          ref={sceneClipFileInputRef}
                          type="file"
                          accept="image/*,video/*,audio/*,.mp3,.wav,.m4a,.aac,.flac,.ogg,.opus,.webm,.mkv"
                          style={{ display: "none" }}
                          id="scene-clip-upload-input"
                        />
                        <button
                          type="button"
                          className="secondary"
                          disabled={busy || !selectedSceneId}
                          onClick={() => sceneClipFileInputRef.current?.click()}
                        >
                          Choose file…
                        </button>
                        <button type="button" disabled={busy || !selectedSceneId} onClick={() => void uploadSceneClipFile()}>
                          Upload
                        </button>
                        <span className="subtle" style={{ fontSize: "0.78rem", maxWidth: "42ch", lineHeight: 1.35 }}>
                          Video and audio clips must be ≤10s. Files are stored under{" "}
                          <code style={{ fontSize: "0.72rem" }}>assets/&lt;project&gt;/&lt;scene&gt;/&lt;asset&gt;.…</code>
                        </span>
                      </div>
                      {gallerySceneAssets.length > 0 ? (
                        <div className="action-row" style={{ marginBottom: 8, flexWrap: "wrap", gap: 6 }}>
                          <button type="button" className="secondary" style={{ fontSize: "0.78rem", padding: "2px 8px" }} onClick={selectAllAssets}>
                            Select all
                          </button>
                          <button
                            type="button"
                            className="secondary"
                            style={{ fontSize: "0.78rem", padding: "2px 8px" }}
                            onClick={() => void rejectAllAssets()}
                            title="Reject every asset in this scene’s list (asks for confirmation)"
                          >
                            Reject all
                          </button>
                          {selectedAssetIds.size > 0 ? (
                            <>
                              <span className="subtle" style={{ fontSize: "0.78rem", alignSelf: "center" }}>
                                {selectedAssetIds.size} selected
                              </span>
                              <button
                                type="button"
                                className="secondary"
                                style={{ fontSize: "0.78rem", padding: "2px 8px" }}
                                onClick={(e) => {
                                  e.preventDefault();
                                  e.stopPropagation();
                                  void bulkApproveAssets();
                                }}
                              >
                                Approve selected
                              </button>
                              <button
                                type="button"
                                className="secondary"
                                style={{ fontSize: "0.78rem", padding: "2px 8px" }}
                                onClick={(e) => {
                                  e.preventDefault();
                                  e.stopPropagation();
                                  void bulkRejectAssets();
                                }}
                              >
                                Reject selected
                              </button>
                              <button type="button" className="secondary" style={{ fontSize: "0.78rem", padding: "2px 8px" }} onClick={clearAssetSelection}>
                                Clear
                              </button>
                            </>
                          ) : null}
                        </div>
                      ) : null}
                      <div className="scene-asset-gallery">
                        {gallerySceneAssets.map((a, idx) => {
                          const at = String(a.asset_type || "").toLowerCase();
                          const thumbSrc = apiAssetContentUrl(a.id, a.updated_at || a.created_at || a.id);
                          const isSelected = selectedAssetIds.has(String(a.id));
                          return (
                          <div
                            key={a.id}
                            className={`scene-asset-card${
                              exportAttentionAssetIdSet.has(String(a.id)) ? " scene-asset-card--export-attention" : ""
                            }${isSelected ? " scene-asset-card--selected" : ""}`}
                          >
                            <div className="scene-asset-thumb-wrap" style={{ position: "relative" }}>
                              <input
                                type="checkbox"
                                checked={isSelected}
                                onChange={() => toggleAssetSelected(String(a.id))}
                                aria-label={`Select asset ${a.id}`}
                                style={{
                                  position: "absolute",
                                  top: 4,
                                  left: 4,
                                  zIndex: 2,
                                  width: 16,
                                  height: 16,
                                  cursor: "pointer",
                                  accentColor: "var(--accent, #6c63ff)",
                                }}
                              />
                              {a.status === "succeeded" && at === "image" ? (
                                <img key={thumbSrc} className="scene-asset-thumb" alt="" src={thumbSrc} />
                              ) : a.status === "succeeded" && at === "video" ? (
                                <video
                                  key={thumbSrc}
                                  className="scene-asset-thumb"
                                  muted
                                  playsInline
                                  controls
                                  preload="metadata"
                                  src={thumbSrc}
                                />
                              ) : a.status === "succeeded" && at === "audio" ? (
                                <audio key={thumbSrc} className="scene-asset-thumb" controls preload="metadata" src={thumbSrc} style={{ width: "100%" }} />
                              ) : (
                                <div className="scene-asset-thumb-placeholder subtle">{a.asset_type}</div>
                              )}
                            </div>
                            <div className="scene-asset-card-meta">
                              <div>
                                <strong>{a.asset_type}</strong> · {a.status} · {a.generation_tier}
                                {a.approved_at ? " · approved" : ""}
                              </div>
                              <div className="subtle">
                                provider: {a.provider || "—"} · model: {a.model_name || "—"}
                              </div>
                              <div className="action-row scene-asset-card-actions">
                                <button
                                  type="button"
                                  className="secondary"
                                  disabled={idx === 0}
                                  onClick={() => moveSceneAssetInSequence(idx, -1)}
                                >
                                  Earlier
                                </button>
                                <button
                                  type="button"
                                  className="secondary"
                                  disabled={idx >= gallerySceneAssets.length - 1}
                                  onClick={() => moveSceneAssetInSequence(idx, 1)}
                                >
                                  Later
                                </button>
                                <button
                                  type="button"
                                  className="secondary"
                                  onClick={(e) => {
                                    e.preventDefault();
                                    e.stopPropagation();
                                    void approveAsset(String(a.id));
                                  }}
                                >
                                  Approve
                                </button>
                                <button
                                  type="button"
                                  className="secondary"
                                  onClick={(e) => {
                                    e.preventDefault();
                                    e.stopPropagation();
                                    void rejectAsset(String(a.id));
                                  }}
                                >
                                  Reject
                                </button>
                              </div>
                            </div>
                          </div>
                          );
                        })}
                      </div>
                      {gallerySceneAssets.length === 0 ? (
                        <div className="subtle" style={{ padding: "16px 8px", textAlign: "center", lineHeight: 1.6 }}>
                          <div style={{ fontSize: "1.4rem", marginBottom: 6 }}>🖼️</div>
                          No assets yet for this scene.
                          <br />
                          Use <strong>Upload clip</strong> above for a short image, video, or audio file, press{" "}
                          <kbd style={{ background: "var(--bg-2,#333)", padding: "1px 5px", borderRadius: 3, fontFamily: "monospace" }}>G</kbd>{" "}
                          to generate an image,
                          or run <strong>Batch generate images</strong> for all scenes at once.
                        </div>
                      ) : null}
                    </>
                  ) : null,
                },
              ]}
          />
          {!selectedScene ? (
            <p className="subtle" style={{ marginTop: 10 }}>
              Pick a scene from the left under <strong>Scenes</strong>, or click the timeline strip below.
            </p>
          ) : null}
        </section>

        <InspectorPipelinePanel
          p={{
            pipelineMode,
            setPipelineMode,
            autoThrough,
            setAutoThrough,
            projectId,
            pipelineStatus: pipelineStatusWithActivity,
            pipelineStepActivityIconClass,
            friendlyPipelineStepStatus,
            title,
            setTitle,
            topic,
            setTopic,
            runtime,
            setRuntime,
            frameAspectRatio,
            setFrameAspectRatio,
            busy,
            startAgentRun,
            continuePipelineAuto,
            openRestartAutomationModal,
            restartAutomationOpen,
            setRestartAutomationOpen,
            restartAutomationSteps: RESTART_AUTOMATION_STEPS,
            restartAutomationForce,
            setRestartAutomationForce,
            restartAutomationThrough,
            setRestartAutomationThrough,
            restartRerunWebResearch,
            setRestartRerunWebResearch,
            submitRestartAutomation,
            rerunPipelineFromStep,
            pipelineRerunLocked: Boolean(busy || agentRunLocksPipelineControls(run)),
            PIPELINE_STEP_ID_TO_AGENT_EFF_KEY,
            forceReplanScenesOnContinue,
            setForceReplanScenesOnContinue,
            appConfig,
            patchWorkspaceConfig,
            settingsBusy,
            agentRunId,
            refreshRun,
            pipelineControl,
            friendlyRunStatus,
            friendlyAgentRunStatus,
            friendlyPipelineStep,
            runStepNow,
            pipelineBanner: headerProgressBanner,
            agentRunStallInfo,
            pipelineActivityRunStatus,
            blocked,
            run,
            friendlyBlockReason,
            criticGateChapterIds,
            chapterTitleForId,
            goToChapterScene,
            postChapterCritique,
            loadCriticReport,
            phase5Ready,
            friendlyReadinessIssue,
            failedReadinessIssues,
            postSceneCritique,
            sceneLabelForId,
            loadChapters,
            loadProjectCriticReports,
            criticListError,
            projectCriticReports,
            blockedChapterReportHints,
            criticReportTargetLabel,
            openSceneForCriticReport,
            openSceneForTimelineAttentionAsset,
            criticReport,
            humanizeMetaKey,
            events,
            friendlyEventMeta,
            entitlementFullThrough: accountProfile?.entitlements?.full_through_automation_enabled !== false,
            entitlementUnattended: accountProfile?.entitlements?.hands_off_unattended_enabled !== false,
            accountProfile,
            queueMediaJob,
          }}
        />

        <section className="panel timeline-panel">
          <h2>Timeline &amp; export</h2>
          <EditorCardColumn
            column="timeline"
            sections={[
              {
                id: "sceneOrder",
                title: "Scene order & trim",
                info: "Drag clips to reorder. Click a clip to select that scene.",
                children: (
                  <>
                    <div className="timeline-strip">
                      {scenes.map((s) => (
                        <div
                          key={s.id}
                          className={`timeline-clip ${selectedSceneId === s.id ? "active" : ""}${
                            exportAttentionSceneIdSet.has(String(s.id)) ? " timeline-clip--export-attention" : ""
                          }`}
                          draggable
                          onDragStart={(e) => e.dataTransfer.setData("text/scene-id", s.id)}
                          onDragOver={(e) => e.preventDefault()}
                          onDrop={(e) => {
                            e.preventDefault();
                            const fromId = e.dataTransfer.getData("text/scene-id");
                            reorderScenes(fromId, s.id);
                          }}
                        >
                          {(() => {
                            const rows = sceneAssets[String(s.id)] || [];
                            const ta = bestSceneListThumbAsset(rows);
                            const ttype = ta ? String(ta.asset_type || "").toLowerCase() : "";
                            const tsrc = ta
                              ? apiAssetContentUrl(ta.id, ta.updated_at || ta.created_at || ta.id)
                              : "";
                            const phKind = sceneListFallbackThumbKind(s, rows);
                            return (
                              <div className="timeline-clip-thumb" aria-hidden="true">
                                {tsrc && ttype === "image" ? (
                                  <img src={tsrc} alt="" className="timeline-clip-thumb-media" loading="lazy" />
                                ) : tsrc && ttype === "video" ? (
                                  <video
                                    className="timeline-clip-thumb-media"
                                    muted
                                    playsInline
                                    preload="metadata"
                                    src={tsrc}
                                  />
                                ) : (
                                  <span className="timeline-clip-thumb-placeholder">
                                    <i
                                      className={`fa-solid ${phKind === "video" ? "fa-video" : "fa-image"}`}
                                      aria-hidden="true"
                                    />
                                  </span>
                                )}
                              </div>
                            );
                          })()}
                          <button
                            type="button"
                            className="secondary timeline-clip-btn"
                            onClick={() => {
                              setPinnedPreviewAssetId(null);
                              setExpandedScene(s.id);
                            }}
                          >
                            <span>S{s.order_index + 1}</span>
                            <small>{s.planned_duration_sec || 0}s</small>
                          </button>
                          <div className="trim-row">
                            <label>In</label>
                            <input
                              type="number"
                              min={0}
                              value={trimByScene[s.id]?.in ?? 0}
                              onChange={(e) =>
                                setTrimByScene((prev) => ({
                                  ...prev,
                                  [s.id]: { ...prev[s.id], in: Number(e.target.value || 0) },
                                }))
                              }
                            />
                            <label>Out</label>
                            <input
                              type="number"
                              min={0}
                              value={trimByScene[s.id]?.out ?? Number(s.planned_duration_sec || 0)}
                              onChange={(e) =>
                                setTrimByScene((prev) => ({
                                  ...prev,
                                  [s.id]: { ...prev[s.id], out: Number(e.target.value || 0) },
                                }))
                              }
                            />
                          </div>
                        </div>
                      ))}
                    </div>
                    <div className="subtle" style={{ marginTop: 8 }}>
                      Total storyboard duration: {timelineTotalSec}s
                    </div>
                  </>
                ),
              },
              {
                id: "compile",
                title: "Compile video",
                info: <>Paste the timeline ID from your last automated export (or from your team). <strong>Check readiness</strong> updates the export checklist.</>,
                children: (
                  <>
                    <label htmlFor="tvid">Timeline version ID</label>
                    <input
                      id="tvid"
                      value={timelineVersionId}
                      onChange={(e) => setTimelineVersionId(e.target.value)}
                      placeholder="e.g. from your last full render"
                    />
                    <p className="subtle" style={{ marginTop: 10 }}>
                      <strong>Music &amp; mix</strong> is under <strong>Project &amp; story → Background music &amp; final mix</strong> (left).{" "}
                      <strong>Final cut</strong> and <strong>Export</strong> save that mix to this timeline automatically before queuing. Final cut uses{" "}
                      per-scene narration aligned to each scene clip in the timeline.{" "}
                      <strong>Rough + final cut</strong> runs the same steps as the local compile scripts (worker jobs, not in-browser FFmpeg).
                    </p>
                    <div
                      style={{
                        marginTop: 12,
                        padding: "10px 12px",
                        borderRadius: 8,
                        background: "var(--panel-elevated-bg, rgba(0,0,0,0.04))",
                      }}
                    >
                      <label style={{ display: "flex", gap: 10, alignItems: "flex-start", cursor: "pointer", fontSize: "0.85rem" }}>
                        <input
                          type="checkbox"
                          checked={useAllApprovedSceneMedia}
                          disabled={busy || !projectId}
                          onChange={(e) => void saveUseAllApprovedSceneMedia(e.target.checked)}
                        />
                        <span>
                          <strong>Use all approved scene media</strong>
                          <span className="subtle" style={{ display: "block", marginTop: 4, lineHeight: 1.45 }}>
                            After review, include <strong>every</strong> approved image and video on each scene in the edit timeline (gallery order).
                            Applies to <strong>Reconcile timeline clips</strong>, export auto-heal, and <strong>Auto / hands-off</strong> timeline build.
                            Turn off to keep one primary clip per scene.
                          </span>
                        </span>
                      </label>
                    </div>
                    <div
                      style={{
                        marginTop: 12,
                        padding: "10px 12px",
                        borderRadius: 8,
                        background: "var(--panel-elevated-bg, rgba(0,0,0,0.04))",
                      }}
                    >
                      <label style={{ display: "flex", gap: 10, alignItems: "flex-start", cursor: "pointer", fontSize: "0.85rem" }}>
                        <input
                          type="checkbox"
                          checked={burnSubtitlesOnFinalCut}
                          disabled={busy || !projectId}
                          onChange={(e) => setBurnSubtitlesOnFinalCut(e.target.checked)}
                        />
                        <span>
                          <strong>Burn subtitles into final MP4</strong>
                          <span className="subtle" style={{ display: "block", marginTop: 4, lineHeight: 1.45 }}>
                            When project <code>subtitles.vtt</code> exists under <code>exports/</code> (from Subtitles generate), re-encode the final cut with captions drawn on-frame. Workspace default:{" "}
                            {appConfig.burn_subtitles_in_final_cut_default ? "on" : "off"} — toggle is saved under Settings → YouTube &amp; export links.
                          </span>
                        </span>
                      </label>
                    </div>
                    <div className="action-row">
                      <button
                        type="button"
                        className="secondary"
                        disabled={!projectId}
                        onClick={() => {
                          if (!projectId) return;
                          setError("");
                          const tv = sanitizeStudioUuid(timelineVersionId);
                          if (!tv) {
                            setError(
                              "Enter the timeline version ID in the field above so export preflight can validate clips.",
                            );
                            return;
                          }
                          if (!PHASE5_TIMELINE_UUID_RE.test(tv)) {
                            setError(
                              "Timeline version ID must be a UUID (e.g. from your last export). Check for extra spaces or missing characters.",
                            );
                            return;
                          }
                          void refreshPhase5Readiness({ reportError: true, timelineVersionIdHint: tv });
                        }}
                      >
                        Check readiness
                      </button>
                      <button
                        type="button"
                        disabled={busy || !projectId || !timelineVersionId}
                        onClick={async () => {
                          if (!projectId || !timelineVersionId) return;
                          await queueMediaJob(
                            `/v1/projects/${projectId}/rough-cut`,
                            {
                              timeline_version_id: timelineVersionId,
                              allow_unapproved_media: pipelineMode === "unattended",
                            },
                            "Rough cut queued…",
                          );
                        }}
                      >
                        Rough cut
                      </button>
                      <button
                        type="button"
                        disabled={busy || !projectId || !timelineVersionId}
                        title="Queues rough cut, waits for it to finish, saves mix to timeline, then queues final cut and waits — same flow as run_rough_cut + run_final_cut scripts."
                        onClick={() => void queueRoughThenFinalCompile()}
                      >
                        Rough + final cut
                      </button>
                      <button
                        type="button"
                        disabled={busy || !projectId || !timelineVersionId}
                        onClick={async () => {
                          if (!projectId || !timelineVersionId) return;
                          const sync = await patchTimelineMixToServer();
                          if (!sync.ok) {
                            setError(sync.error ? humanizeErrorText(sync.error) : "Could not save mix to timeline");
                            return;
                          }
                          await queueMediaJob(
                            `/v1/projects/${projectId}/final-cut`,
                            {
                              timeline_version_id: timelineVersionId,
                              allow_unapproved_media: pipelineMode === "unattended",
                              burn_subtitles_into_video: burnSubtitlesOnFinalCut,
                            },
                            "Final cut queued…",
                          );
                        }}
                      >
                        Final cut
                      </button>
                      <button
                        type="button"
                        disabled={busy || !projectId || !timelineVersionId}
                        onClick={async () => {
                          if (!projectId || !timelineVersionId) return;
                          const sync = await patchTimelineMixToServer();
                          if (!sync.ok) {
                            setError(sync.error ? humanizeErrorText(sync.error) : "Could not save mix to timeline");
                            return;
                          }
                          await queueMediaJob(
                            `/v1/projects/${projectId}/export`,
                            { timeline_version_id: timelineVersionId, include_subtitles: true },
                            "Export bundle queued…",
                          );
                        }}
                      >
                        Export
                      </button>
                    </div>
                    <p className="subtle" style={{ marginTop: 12, marginBottom: 6 }}>
                      <strong>Rough-cut image repair:</strong> try <strong>Reconcile timeline clips</strong> first — it re-points clips at valid
                      scene media (and related sync). Use <strong>Reject &amp; regen flagged stills</strong> only when flagged stills are bad and you
                      need new scene images (rejects assets and queues generation).
                    </p>
                    <div className="action-row" style={{ flexWrap: "wrap", gap: 8 }}>
                      <button
                        type="button"
                        className="secondary"
                        disabled={busy || !projectId || !timelineVersionId}
                        onClick={() => void reconcileTimelineClipImages()}
                        title="Relink timeline clips to viable scene media, sync storyboard order, and related fixes"
                      >
                        Reconcile timeline clips
                      </button>
                      <button
                        type="button"
                        className="secondary"
                        disabled={busy || !projectId || !timelineVersionId}
                        onClick={() => void rejectAndRegenerateRoughCutImages()}
                        title="Destructive: reject flagged rough-cut stills and queue new scene image jobs per scene"
                      >
                        Reject &amp; regen flagged stills
                      </button>
                    </div>
                    {phase5Ready ? (
                      <p className="subtle timeline-readiness-line">
                        {phase5Ready.ready
                          ? "Export checklist: all clear."
                          : `Export checklist: ${phase5Ready.issues?.length || 0} open item(s) — see “What’s blocking export” in the pipeline panel.`}
                      </p>
                    ) : null}
                    <ExportAttentionTimelineAssetsBlock
                      rows={phase5Ready?.export_attention_timeline_assets}
                      busy={busy}
                      onOpenScene={openSceneForTimelineAttentionAsset}
                      onReconcile={reconcileTimelineClipImages}
                      reconcileDisabled={!projectId || !String(timelineVersionId || "").trim()}
                    />
                  </>
                ),
              },
            ]}
          />
        </section>

        <div className="splitter splitter-left" onMouseDown={() => setDragState({ type: "left" })} />
        <div className="splitter splitter-right" onMouseDown={() => setDragState({ type: "right" })} />
        <div className="splitter splitter-bottom" onMouseDown={() => setDragState({ type: "bottom" })} />
      </div>
      ) : null}
      </StudioPanelErrorBoundary>
        </div>
      </div>
      <footer className="studio-footer">
        <nav className="studio-footer__nav" aria-label="Legal and copyright">
          <button type="button" className="studio-footer__link" onClick={() => setActivePage("terms")}>
            Terms of Service
          </button>
          <span className="studio-footer__sep" aria-hidden="true">
            ·
          </span>
          <button type="button" className="studio-footer__link" onClick={() => setActivePage("privacy")}>
            Privacy Policy
          </button>
          <span className="studio-footer__sep" aria-hidden="true">
            ·
          </span>
          <button type="button" className="studio-footer__link" onClick={() => setActivePage("copyright")}>
            Copyright
          </button>
        </nav>
        <p className="studio-footer__copy subtle">© 2026 Directely. All rights reserved.</p>
      </footer>
    </div>
    <ToastContainer toasts={toasts} onDismiss={dismissToast} />
    <ShortcutHelp open={showShortcutHelp} onClose={() => setShowShortcutHelp(false)} />
    </EditorLayoutProvider>
  );
}
