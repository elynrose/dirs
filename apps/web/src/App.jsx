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
  apiComfyuiWorkflowTestOutputUrl,
  downloadEditorExportZip,
} from "./lib/api.js";
import {
  DIRECTOR_UI_SESSION_KEY,
  FAL_CATALOG_MIN_REFRESH_MS,
  STUDIO_MEDIA_JOB_TYPES,
  EXPORT_COMPILE_JOB_TYPES,
  OPENAI_TTS_VOICE_OPTIONS,
  GEMINI_TTS_VOICE_FALLBACK,
  KOKORO_VOICE_OPTIONS,
  KOKORO_LANG_OPTIONS,
  VISUAL_STYLE_PRESET_FALLBACK,
  AGENT_PROGRESS_ORDER,
  PHASE5_TIMELINE_UUID_RE,
  EVENT_META_LABELS,
  agentRunAutoGenerateSceneVideos,
  agentRunAutoGenerateSceneImages,
  agentRunMinSceneImages,
  agentRunMinSceneVideos,
} from "./lib/constants.js";
import { usePollJob } from "./hooks/usePollJob.js";
import { useToast } from "./hooks/useToast.js";
import { useKeyboardShortcuts } from "./hooks/useKeyboardShortcuts.js";
import { useProjectEvents } from "./hooks/useProjectEvents.js";
import { useStudioCharacters } from "./hooks/useStudioCharacters.js";
import { useStudioEditorComposition } from "./hooks/editor/useStudioEditorComposition.js";
import { useStudioResearch } from "./hooks/useStudioResearch.js";
import { useEditorAudioMix } from "./hooks/editor/useEditorAudioMix.js";
import { useEditorAssetsMedia } from "./hooks/editor/useEditorAssetsMedia.js";
import { useEditorProjectScenes } from "./hooks/editor/useEditorProjectScenes.js";
import { useEditorTimelineExport } from "./hooks/editor/useEditorTimelineExport.js";
import { useEditorPipelineAgent } from "./hooks/editor/useEditorPipelineAgent.js";
import { ToastContainer } from "./components/Toast.jsx";
import { ShortcutHelp } from "./components/ShortcutHelp.jsx";
import {
  SkeletonSceneList,
  SkeletonAssetGrid,
  SkeletonMediaCanvas,
} from "./components/LoadingSkeleton.jsx";
import { StudioPageRouter } from "./components/studio/StudioPageRouter.jsx";
import { ChatStudioPage } from "./components/ChatStudioPage.jsx";
import {
  chaptersSorted,
  chapterHumanNumber,
  bestSceneListThumbAsset,
  sceneListFallbackThumbKind,
} from "./lib/studio/sceneHelpers.js";
import { parsePhase5GateModalPayload } from "./lib/studio/exportHelpers.js";
import {
  friendlyPipelineStep,
  friendlyRunStatus,
  pipelineStopRequested,
  friendlyAgentRunStatus,
  agentRunLocksPipelineControls,
  friendlyPipelineStepStatus,
  friendlyBlockReason,
  agentThroughFromRun,
  agentStageHeadline,
  lastAgentEventWithStatus,
  agentPipelineActivityIconClass,
  jobTypeToMacroStepKey,
  inferAgentStepKeyFromActiveJobs,
  inferMacroStepKeyFromJobType,
  studioJobKindHeadline,
  resolveEffectiveAgentStepKey,
  pipelineStepActivityIconClass,
  mergePipelineStepsWithAgentActivity,
} from "./lib/studio/pipelineHelpers.js";
import { ExportAttentionTimelineAssetsBlock } from "./components/ExportAttentionTimelineAssetsBlock.jsx";
import { StudioAuthPanel } from "./components/StudioAuthPanel.jsx";
import { StudioUpgradeModal } from "./components/StudioUpgradeModal.jsx";
import { StudioPanelErrorBoundary } from "./components/StudioPanelErrorBoundary.jsx";
import {
  LEGAL_PAGE_IDS,
  STUDIO_PAGE_RAILS,
  normalizeDirectorActivePage,
} from "./lib/studio/studioPageRegistry.js";
import {
  clearDirectorAuthSession,
  getDirectorTenantId,
  normalizeDirectorAuthStorage,
  setDirectorAuthSession,
  setDirectorSaaSClientActive,
  syncDirectorTenantFromMePayload,
} from "./lib/directorAuthSession.js";

/**
 * Browser-side cap for synchronous prompt-improve LLM routes (server default ~90s via
 * OPENAI_PROMPT_ENHANCE_TIMEOUT_SEC). Slightly higher so the API can return a JSON error first.
 */
const PROMPT_ENHANCE_API_TIMEOUT_MS = 110_000;

function formatPromptEnhanceClientError(e) {
  if (e && typeof e === "object" && e.name === "AbortError") {
    return "That request timed out. Check OpenAI or LM Studio is reachable and API keys are set, then try again.";
  }
  return formatUserFacingError(e);
}

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

function activeJobsPollSnapshot(jobs) {
  if (!Array.isArray(jobs)) return "[]";
  return JSON.stringify(
    [...jobs]
      .map((j) => ({
        id: String(j?.id ?? ""),
        t: String(j?.type ?? ""),
        st: String(j?.status ?? ""),
        pct: j?.progress_pct,
        ua: String(j?.updated_at ?? ""),
      }))
      .sort((a, b) => a.id.localeCompare(b.id)),
  );
}

function humanizeMetaKey(key) {
  if (EVENT_META_LABELS[key]) return EVENT_META_LABELS[key];
  return String(key || "").replace(/_/g, " ");
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

function narrationWordCount(text) {
  if (!text || typeof text !== "string") return 0;
  const t = text.trim();
  if (!t) return 0;
  return t.split(/\s+/).filter(Boolean).length;
}

/** Timeline cover from succeeded assets: explicit planned_duration_sec or one clip per image/video. */
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
  const projectScenesCollabRef = useRef({});
  const pipelineCollabRef = useRef({});
  const mergeSceneAssetRef = useRef(() => {});
  const exportCollabRef = useRef({});
  const patchTimelineMixRef = useRef(async () => ({ ok: false, error: "Mix not ready" }));
  const sseConnectedRef = useRef(false);
  const [activePage, setActivePage] = useState(() => readDirectorUiSession()?.activePage ?? "editor");
  const [appConfig, setAppConfig] = useState({});
  const appConfigRef = useRef(appConfig);
  useEffect(() => {
    appConfigRef.current = appConfig;
  }, [appConfig]);
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
  /** Locked to the open project after load; drives new agent-run brief before a project exists. */
  const [frameAspectRatio, setFrameAspectRatio] = useState("16:9");
  /** Pexels / stock import fit: center_crop (fill, may cut edges) vs letterbox (pad). */
  const [clipFrameFit, setClipFrameFit] = useState("center_crop");
  /** Slideshow / music-only: skip TTS; final export is visuals + background music. */
  const [noNarration, setNoNarration] = useState(false);
  const [sceneVideoCharacterDialogueDraft, setSceneVideoCharacterDialogueDraft] = useState("");
  const [sceneVideoCharacterDialogueDirty, setSceneVideoCharacterDialogueDirty] = useState(false);
  const sceneVideoDialogueSceneRef = useRef(null);
  /** Inline scene narration editor: sync draft when switching scene vs. when scenes[] reloads from API. */
  const scriptEditSceneIdRef = useRef("");
  /** Last ``narration_text`` applied from ``scenes[]`` for the expanded scene — detects server-side updates (automation). */
  const lastServerNarrationForExpandedRef = useRef("");
  const [sceneNarrationDraft, setSceneNarrationDraft] = useState("");
  const [sceneNarrationDirty, setSceneNarrationDirty] = useState(false);
  const [sceneNarrationSaving, setSceneNarrationSaving] = useState(false);
  const [promptEnhanceVoBusy, setPromptEnhanceVoBusy] = useState(false);
  const [promptExpandVoBusy, setPromptExpandVoBusy] = useState(false);
  /** Scene script expand: approximate sentence count + optional user hints for the LLM. */
  const [sceneVoExpandSentenceTarget, setSceneVoExpandSentenceTarget] = useState(6);
  const [sceneVoExpandContext, setSceneVoExpandContext] = useState("");
  /** idle | recording | saving — browser MediaRecorder for scene VO upload */
  const [sceneVoRecordPhase, setSceneVoRecordPhase] = useState("idle");
  const sceneVoRecorderRef = useRef(null);
  const sceneVoMediaChunksRef = useRef([]);
  const [mediaJobId, setMediaJobId] = useState(() => UI_BOOT.mediaJobId);
  const [mediaPoll, setMediaPoll] = useState(() => UI_BOOT.mediaPoll);
  const [lastHandledMediaJobId, setLastHandledMediaJobId] = useState("");
  const [phase3Summary, setPhase3Summary] = useState(null);
  const [criticReport, setCriticReport] = useState(null);
  /** Summary rows from `GET /v1/projects/{id}/critic-reports` (newest first). */
  const [projectCriticReports, setProjectCriticReports] = useState([]);
  const [criticListError, setCriticListError] = useState("");
  /** From timeline_json.export_warnings (e.g. manifest-only rough cut when FFmpeg compile is off). */
  const [timelineExportWarnings, setTimelineExportWarnings] = useState([]);
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
  /** Settings → Integrations: `POST /v1/jobs` adapter_smoke poll target. */
  const [adapterSmokeJobId, setAdapterSmokeJobId] = useState(null);
  const [adapterSmokePollActive, setAdapterSmokePollActive] = useState(false);
  const [telegramTestLoading, setTelegramTestLoading] = useState(false);
  const adapterSmokeLabelRef = useRef("");
  /** Queued + running `Job` rows for the open project (GET /v1/projects/{id}/jobs/active). */
  const [activeProjectJobs, setActiveProjectJobs] = useState([]);
  const [activeJobsLoadErr, setActiveJobsLoadErr] = useState("");
  const { toasts, toast: showToast, dismissToast } = useToast({ durationMs: 5000 });
  /** Prior snapshot to detect jobs that left the active list (completed/cancelled) without UI refresh. */
  const activeJobsPrevRef = useRef([]);
  /** Latest ``loadActiveProjectJobs`` for SSE ``onConnected`` (handler is stable; ref avoids stale projectId). */
  const loadActiveProjectJobsRef = useRef(() => Promise.resolve());
  const lastActiveJobsPollSnapshotRef = useRef("");
  const jobPollIntervalMs = Math.max(
    500,
    Math.min(120_000, Number(appConfig.studio_job_poll_interval_ms) || 800),
  );
  /** Background refresh for the project list (Project & story → Projects). Keep ≥8s to avoid constant full-list GETs. */
  const projectsListPollMs = useMemo(
    () => Math.min(20_000, Math.max(8_000, jobPollIntervalMs * 4)),
    [jobPollIntervalMs],
  );

  const {
    chapterId,
    chapters,
    chapterTitleForId,
    deleteProject,
    expandedScene,
    gatedProjectId,
    goToChapterScene,
    loadChapters,
    loadProjects,
    loadScenes,
    onChatStudioProjectOpen,
    openProject,
    openProjectRef,
    projectId,
    projects,
    reorderScenes,
    resetProjectSlice,
    runtime,
    sceneIdForAssetGalleryRefresh,
    sceneLabelForId,
    scenes,
    scenesLoading,
    selectedScene,
    selectedSceneId,
    setChapterId,
    setChapters,
    setExpandedScene,
    setProjectId,
    setRuntime,
    setScenes,
    setTitle,
    setTopic,
    startNewProjectDraft,
    title,
    topic,
  } = useEditorProjectScenes({
    studioReady,
    projectsListPollMs,
    bootProjectId: UI_BOOT.projectId,
    bootChapterId: UI_BOOT.chapterId,
    bootExpandedScene: UI_BOOT.expandedScene,
    setError,
    setMessage,
    setBusy,
    collabRef: projectScenesCollabRef,
  });

  const {
    projectCharacters,
    setProjectCharacters,
    charactersJobId,
    setCharactersJobId,
    charactersJob,
    loadProjectCharacters,
    generateFromStory,
    saveCharacter,
    deleteCharacter,
    addCharacter,
    resetCharacters,
    friendlyRunStatus: charactersFriendlyRunStatus,
  } = useStudioCharacters({
    projectId,
    activePage,
    studioReady,
    jobPollIntervalMs,
    initialCharactersJobId: UI_BOOT.charactersJobId,
    busy,
    setBusy,
    setError,
    setMessage,
    idem: () => crypto.randomUUID(),
  });

  const {
    researchJsonDraft,
    setResearchJsonDraft,
    researchMeta,
    researchPageBusy,
    researchPageErr,
    researchPipelineBusy,
    chapterRegenerateId,
    chapterScriptsDraft,
    setChapterScriptsDraft,
    loadResearchChaptersEditor,
    rerunResearch,
    saveDossier,
    regenerateChapterScript,
    saveChapter,
    resetResearch,
  } = useStudioResearch({
    projectId,
    activePage,
    activeProjectJobs,
    setChapters,
    setMessage,
    setError,
    idem: () => crypto.randomUUID(),
  });

  const {
    agentRunId,
    agentRunStallInfo,
    autoThrough,
    blocked,
    continuePipelineAuto,
    events,
    forceReplanScenesOnContinue,
    publishToYouTube,
    openRestartAutomationModal,
    pipelineControl,
    pipelineMode,
    refreshRun,
    resetPipelineAgentSlice,
    rerunPipelineFromStep,
    restartAutomationForce,
    restartAutomationOpen,
    restartAutomationThrough,
    restartRerunWebResearch,
    run,
    runStepGuidance,
    runStepNow,
    setAgentRunId,
    setAutoThrough,
    setForceReplanScenesOnContinue,
    setPublishToYouTube,
    setPipelineMode,
    setRestartAutomationForce,
    setRestartAutomationOpen,
    setRestartAutomationThrough,
    setRestartRerunWebResearch,
    setRun,
    startAgentRun,
    startProjectAgentFromList,
    stopProjectAgentFromList,
    submitRestartAutomation,
    youtubeConnected,
    youtubeStatusLoading,
  } = useEditorPipelineAgent({
    bootAgentRunId: UI_BOOT.agentRunId,
    studioReady,
    accountProfile,
    appConfig,
    chapterId,
    expandedScene,
    activeProjectJobs,
    sseConnectedRef,
    pipelineCollabRef,
    setBusy,
    setError,
    setMessage,
    showToast,
  });

  const {
    approveAllMediaBusy,
    approveAllSucceededMediaForExport,
    burnSubtitlesOnFinalCut,
    dismissPhase5ExportGateModal,
    exportAttentionAssetIdSet,
    exportAttentionSceneIdSet,
    friendlyReadinessIssue,
    loadPipelineStatus,
    phase5ExportGateModal,
    phase5ReadinessFetchOpts,
    phase5Ready,
    pipelineStatus,
    queueRoughThenFinalCompile,
    reconcileTimelineClipImages,
    refreshPhase5Readiness,
    rejectAndRegenerateRoughCutImages,
    resetTimelineExportSlice,
    reviewScenesForExportGate,
    setBurnSubtitlesOnFinalCut,
    setPhase5ExportGateModal,
    setPhase5Ready,
    setTimelineVersionId,
    setTrimByScene,
    timelineVersionId,
    trimByScene,
  } = useEditorTimelineExport({
    bootTimelineVersionId: UI_BOOT.timelineVersionId,
    studioReady,
    projectId,
    gatedProjectId,
    pipelineMode,
    sseConnectedRef,
    jobPollIntervalMs,
    appConfig,
    run,
    setBusy,
    setError,
    setMessage,
    setMediaJobId,
    setMediaPoll,
    setActivePage,
    setExpandedScene,
    setTimelineExportWarnings,
    loadActiveProjectJobsRef,
    exportCollabRef,
    patchTimelineMixRef,
    idem: () => crypto.randomUUID(),
  });

  const {
    clipCrossfadeSec,
    loadTimelineMixFields,
    mixMusicVol,
    mixNarrVol,
    musicBedPick,
    musicBeds,
    musicFileInputRef,
    musicUploadLicense,
    patchTimelineMixToServer,
    saveTimelineMixToServer,
    scheduleDebouncedTimelineMixSave,
    schedulePersistStudioMixDefaults,
    setClipCrossfadeSec,
    setMixMusicVol,
    setMixNarrVol,
    setMusicBedPick,
    setMusicUploadLicense,
    uploadMusicBedFile,
  } = useEditorAudioMix({
    projectId,
    gatedProjectId,
    timelineVersionId,
    appConfig,
    appConfigRef,
    setAppConfig,
    setTimelineExportWarnings,
    busy,
    setBusy,
    setError,
    setMessage,
  });

  patchTimelineMixRef.current = patchTimelineMixToServer;

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
    resetProjectSlice();
    projectScenesCollabRef.current.onResetTenantBoundaryExtras?.();
    resetCharacters();
  }, [resetProjectSlice, resetCharacters]);

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
      mergeSceneAssetRef.current(asset);
      showToast(
        `${asset.asset_type === "video" ? "Video" : "Image"} ready`,
        { type: "success" },
      );
    }, [showToast]),
    onCeleryStatus: useCallback((online) => {
      setCeleryStatus(online ? "online" : "offline");
    }, []),
  }, eventAuthKey);
  sseConnectedRef.current = sseConnected;

  const [panelSizes, setPanelSizes] = useState({ left: 300, right: 360, bottom: 240 });
  const [dragState, setDragState] = useState(null);
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
  /** GET /v1/settings/comfyui-workflows */
  const [comfyuiWorkflows, setComfyuiWorkflows] = useState(null);
  const [comfyuiWorkflowsBusy, setComfyuiWorkflowsBusy] = useState(false);
  const [comfyuiWorkflowsErr, setComfyuiWorkflowsErr] = useState("");
  const [comfyuiTestBusy, setComfyuiTestBusy] = useState(false);
  const [comfyuiTestOutputBust, setComfyuiTestOutputBust] = useState("");
  const [chatterboxRecording, setChatterboxRecording] = useState(false);
  const chatterboxMediaStreamRef = useRef(null);
  const chatterboxMediaRecRef = useRef(null);
  const chatterboxMediaChunksRef = useRef([]);
  /** When true, ``MediaRecorder`` ``onstop`` discards the clip (e.g. user left the settings tab). */
  const chatterboxDiscardRecordingRef = useRef(false);
  /** When true, storyboard sync / reconcile / auto timeline add one clip per approved export-ready visual per scene. */
  const [useAllApprovedSceneMedia, setUseAllApprovedSceneMedia] = useState(false);
  /** Project: append optional per-scene video_character_dialogue to generative video prompts (Veo-class). */
  const [includeSpokenDialogueInVideoPrompt, setIncludeSpokenDialogueInVideoPrompt] = useState(false);
  /** After first session restore attempt; avoids clobbering localStorage before hydrate. */
  const [uiSessionReady, setUiSessionReady] = useState(false);
  const sessionRestoreStartedRef = useRef(false);
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
  /** Skip ProjectCharacter consistency prefix on image/video jobs (generate, retry, batch images). */
  const [excludeCharacterBibleFromPrompts, setExcludeCharacterBibleFromPrompts] = useState(false);

  useEffect(() => {
    setBatchImageRangeFrom("");
    setBatchImageRangeTo("");
  }, [chapterId]);

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
      const jobs = body.data?.jobs || [];
      const js = activeJobsPollSnapshot(jobs);
      if (js !== lastActiveJobsPollSnapshotRef.current) {
        lastActiveJobsPollSnapshotRef.current = js;
        setActiveProjectJobs(jobs);
      }
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

  const saveIncludeSpokenDialogueInVideoPrompt = useCallback(
    async (nextBool) => {
      if (!projectId) return;
      setBusy(true);
      setError("");
      try {
        const r = await api(`/v1/projects/${encodeURIComponent(projectId)}`, {
          method: "PATCH",
          body: JSON.stringify({ include_spoken_dialogue_in_video_prompt: nextBool }),
        });
        const b = await parseJson(r);
        if (!r.ok) throw new Error(apiErrorMessage(b));
        setIncludeSpokenDialogueInVideoPrompt(Boolean(b.data?.include_spoken_dialogue_in_video_prompt));
        setMessage(
          nextBool
            ? "Spoken dialogue in video prompts is on — add lines per scene under Video & motion when you want speech."
            : "Spoken dialogue in video prompts is off — scene video prompts are visual-only.",
        );
      } catch (e) {
        setError(formatUserFacingError(e));
      } finally {
        setBusy(false);
      }
    },
    [projectId],
  );

  const saveClipFrameFit = useCallback(
    async (nextFit) => {
      if (!projectId) return;
      const fit = nextFit === "letterbox" ? "letterbox" : "center_crop";
      setBusy(true);
      setError("");
      try {
        const r = await api(`/v1/projects/${encodeURIComponent(projectId)}`, {
          method: "PATCH",
          body: JSON.stringify({ clip_frame_fit: fit }),
        });
        const b = await parseJson(r);
        if (!r.ok) throw new Error(apiErrorMessage(b));
        setClipFrameFit(b.data?.clip_frame_fit === "letterbox" ? "letterbox" : "center_crop");
        setMessage(
          fit === "letterbox"
            ? "Stock / Pexels imports use letterboxing (full image visible, may add black bars)."
            : "Stock / Pexels imports use center crop (fills the frame; edges may be cut).",
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
      lastActiveJobsPollSnapshotRef.current = "";
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

  const saveSceneVideoCharacterDialogue = useCallback(async () => {
    const sid = expandedScene || scenes[0]?.id || "";
    if (!sid || !sceneVideoCharacterDialogueDirty) return;
    setBusy(true);
    setError("");
    try {
      const sc = scenes.find((s) => String(s.id) === String(sid));
      const prev =
        sc?.prompt_package_json && typeof sc.prompt_package_json === "object"
          ? { ...sc.prompt_package_json }
          : {};
      const trimmed = sceneVideoCharacterDialogueDraft.trim();
      if (trimmed) prev.video_character_dialogue = trimmed;
      else delete prev.video_character_dialogue;
      const r = await api(`/v1/scenes/${encodeURIComponent(sid)}`, {
        method: "PATCH",
        body: JSON.stringify({ prompt_package_json: prev }),
      });
      const b = await parseJson(r);
      if (!r.ok) throw new Error(apiErrorMessage(b));
      const row = b.data;
      setScenes((prevScenes) =>
        prevScenes.map((s) => {
          if (String(s.id) !== String(sid)) return s;
          return { ...s, ...row, asset_count: row.asset_count ?? s.asset_count };
        }),
      );
      setSceneVideoCharacterDialogueDirty(false);
      setSceneVideoCharacterDialogueDraft(trimmed);
      setMessage("Scene character dialogue saved.");
    } catch (e) {
      setError(formatUserFacingError(e));
    } finally {
      setBusy(false);
    }
  }, [
    expandedScene,
    scenes,
    sceneVideoCharacterDialogueDraft,
    sceneVideoCharacterDialogueDirty,
  ]);

  const loadPhase3Summary = useCallback(async (cid) => {
    if (!cid) {
      setPhase3Summary(null);
      return;
    }
    const r = await api(`/v1/chapters/${cid}/phase3-summary`);
    const body = await parseJson(r);
    if (r.ok) setPhase3Summary(body.data ?? null);
  }, []);

  const {
    approveAsset,
    assetGenerationPrompt,
    bulkApproveAssets,
    bulkRejectAssets,
    clearAssetSelection,
    enhanceRetryImagePrompt,
    gallerySceneAssets,
    importSceneAssetFromStock,
    loadSceneAssets,
    mediaPreviewTab,
    mergeSceneAssetFromEvent,
    moveSceneAssetInSequence,
    pexelsImportKey,
    pexelsSearchBusy,
    pexelsSearchErr,
    pexelsSearchQuery,
    pexelsSearchResults,
    pexelsStockTab,
    pexelsTrimHint,
    pexelsTrimHintBusy,
    pinnedPreviewAssetId,
    postImage,
    previewKind,
    previewMediaError,
    previewUrl,
    promptEnhanceImageBusy,
    rejectAllAssets,
    rejectAsset,
    reorderSceneAssets,
    resetAssetsMediaSlice,
    retryPrompt,
    retryVideoPrompt,
    runSceneStockSearch,
    sceneAssets,
    sceneAssetsFetchError,
    sceneClipFileInputRef,
    sceneClipSec,
    sceneClipUploadKind,
    sceneStockLibrary,
    selectAllAssets,
    selectedAssetIds,
    selectedCoveredSec,
    setMediaPreviewTab,
    setPexelsSearchQuery,
    setPexelsStockTab,
    setPinnedPreviewAssetId,
    setPreviewMediaError,
    setRetryPrompt,
    setRetryVideoPrompt,
    setSceneClipUploadKind,
    setSceneStockLibrary,
    setStockVideoTrimModal,
    stockVideoTrimModal,
    toggleAssetSelected,
    uploadSceneClipFile,
  } = useEditorAssetsMedia({
    studioReady,
    projectId,
    chapterId,
    scenes,
    expandedScene,
    selectedSceneId,
    sceneIdForAssetGalleryRefresh,
    appConfig,
    setBusy,
    setError,
    setMessage,
    setExpandedScene,
    setMediaJobId,
    setMediaPoll,
    loadPhase3Summary,
    refreshPhase5Readiness,
    loadActiveProjectJobs,
    refineBracketImageWithLlm,
    excludeCharacterBibleFromPrompts,
    idem,
  });

  mergeSceneAssetRef.current = mergeSceneAssetFromEvent;

  exportCollabRef.current = {
    chapterId,
    expandedScene,
    loadPhase3Summary,
    loadSceneAssets,
    loadScenes,
    sceneIdForAssetGalleryRefresh,
    setPinnedPreviewAssetId,
  };

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

  const loadComfyuiWorkflows = useCallback(async () => {
    setComfyuiWorkflowsErr("");
    try {
      const r = await api("/v1/settings/comfyui-workflows");
      const body = await parseJson(r);
      if (!r.ok) {
        setComfyuiWorkflowsErr(apiErrorMessage(body));
        setComfyuiWorkflows(null);
        return;
      }
      setComfyuiWorkflows(body.data || null);
    } catch (e) {
      setComfyuiWorkflowsErr(formatUserFacingError(e));
      setComfyuiWorkflows(null);
    }
  }, []);

  const uploadComfyuiWorkflowFile = useCallback(
    async (role, file) => {
      if (!file) return;
      setComfyuiWorkflowsBusy(true);
      setComfyuiWorkflowsErr("");
      try {
        const fd = new FormData();
        fd.append("file", file, file.name || "workflow.json");
        const r = await apiForm(`/v1/settings/comfyui-workflows/${role}`, {
          method: "POST",
          body: fd,
        });
        const body = await parseJson(r);
        if (!r.ok) {
          setComfyuiWorkflowsErr(apiErrorMessage(body));
          return;
        }
        await loadComfyuiWorkflows();
        await loadAppSettings();
        showToast(`ComfyUI ${role} workflow saved.`, { type: "success" });
      } catch (e) {
        setComfyuiWorkflowsErr(formatUserFacingError(e));
      } finally {
        setComfyuiWorkflowsBusy(false);
      }
    },
    [loadAppSettings, loadComfyuiWorkflows, showToast],
  );

  const deleteComfyuiWorkflow = useCallback(
    async (role) => {
      setComfyuiWorkflowsBusy(true);
      setComfyuiWorkflowsErr("");
      try {
        const r = await api(`/v1/settings/comfyui-workflows/${role}`, { method: "DELETE" });
        const body = await parseJson(r);
        if (!r.ok) {
          setComfyuiWorkflowsErr(apiErrorMessage(body));
          return;
        }
        await loadComfyuiWorkflows();
        await loadAppSettings();
      } catch (e) {
        setComfyuiWorkflowsErr(formatUserFacingError(e));
      } finally {
        setComfyuiWorkflowsBusy(false);
      }
    },
    [loadAppSettings, loadComfyuiWorkflows],
  );

  const runComfyuiWorkflowTest = useCallback(
    async (mode) => {
      if (comfyuiTestBusy) return;
      setComfyuiTestBusy(true);
      setComfyuiWorkflowsErr("");
      try {
        const r = await api("/v1/settings/comfyui-workflows/test", {
          method: "POST",
          body: JSON.stringify({ mode }),
        });
        let body = await parseJson(r);
        if (r.status === 202 && body?.data?.test_id) {
          if (mode === "video") {
            showToast("ComfyUI video test started — local WAN renders often take several minutes.", {
              type: "info",
              durationMs: 10000,
            });
          }
          const pollPath =
            body.data.poll_url || `/v1/settings/comfyui-workflows/test/${body.data.test_id}`;
          const deadline = Date.now() + (mode === "video" ? 45 * 60 * 1000 : 20 * 60 * 1000);
          while (Date.now() < deadline) {
            await new Promise((res) => window.setTimeout(res, 2500));
            const pr = await api(pollPath);
            body = await parseJson(pr);
            const st = body?.data?.status;
            if (st && st !== "running") break;
          }
        }
        if (!r.ok && r.status !== 202) {
          showToast(apiErrorMessage(body) || "ComfyUI test failed", { type: "error", durationMs: 10000 });
          return;
        }
        const d = body.data || {};
        if (d.status === "running") {
          showToast("ComfyUI test is still running — check again in a few minutes.", {
            type: "info",
            durationMs: 10000,
          });
          return;
        }
        if (d.ok) {
          if (mode === "image" || mode === "video") {
            setComfyuiTestOutputBust(String(Date.now()));
          }
          const extra =
            mode === "connection"
              ? d.result?.workflow_env_ok === false
                ? " — check workflow node ids"
                : ""
              : d.bytes_written
                ? ` (${Math.round(d.bytes_written / 1024)} KB)`
                : "";
          showToast(`ComfyUI ${mode} test OK${extra}`, {
            type: "success",
            durationMs: mode === "video" ? 8000 : 6000,
          });
        } else {
          showToast(
            humanizeErrorText(d.detail || d.error || "ComfyUI test failed"),
            { type: "error", durationMs: 12000 },
          );
        }
        await loadComfyuiWorkflows();
      } catch (e) {
        showToast(formatUserFacingError(e), { type: "error", durationMs: 10000 });
      } finally {
        setComfyuiTestBusy(false);
      }
    },
    [comfyuiTestBusy, loadComfyuiWorkflows, showToast],
  );

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

  useEffect(() => {
    if (settingsTab !== "integrations") return undefined;
    void loadComfyuiWorkflows();
    return undefined;
  }, [settingsTab, loadComfyuiWorkflows]);

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

  useEffect(() => {
    if (!studioReady) return;
    void loadAppSettings();
    loadFalCatalog();
    loadStylePresets();
    void loadGeminiTtsVoices();
    void loadElevenlabsVoices();
  }, [
    studioReady,
    loadAppSettings,
    loadFalCatalog,
    loadStylePresets,
    loadGeminiTtsVoices,
    loadElevenlabsVoices,
  ]);

  useEffect(() => {
    if (activePage !== "usage") return;
    void loadUsageSummary(usageDays);
  }, [activePage, usageDays, loadUsageSummary]);

  useEffect(() => {
    if (activePage !== "prompts") return;
    void loadLlmPrompts();
  }, [activePage, loadLlmPrompts]);

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

  useEffect(() => {
    if (!studioReady) return;
    loadChapterNarration(chapterId);
  }, [studioReady, chapterId, loadChapterNarration]);

  useEffect(() => {
    if (!studioReady) return;
    loadPhase3Summary(chapterId);
  }, [studioReady, chapterId, loadPhase3Summary]);

  useEffect(() => {
    const sid = expandedScene || scenes[0]?.id || "";
    if (!sid) {
      setSceneVideoCharacterDialogueDraft("");
      setSceneVideoCharacterDialogueDirty(false);
      sceneVideoDialogueSceneRef.current = null;
      return;
    }
    const switched = String(sceneVideoDialogueSceneRef.current) !== String(sid);
    sceneVideoDialogueSceneRef.current = String(sid);
    const sc = scenes.find((s) => String(s.id) === String(sid));
    const pkg = sc?.prompt_package_json;
    const v =
      pkg && typeof pkg === "object" && typeof pkg.video_character_dialogue === "string"
        ? pkg.video_character_dialogue
        : "";
    if (switched) {
      setSceneVideoCharacterDialogueDraft(v);
      setSceneVideoCharacterDialogueDirty(false);
      return;
    }
    if (!sceneVideoCharacterDialogueDirty) {
      setSceneVideoCharacterDialogueDraft(v);
    }
  }, [expandedScene, scenes, sceneVideoCharacterDialogueDirty]);

  useEffect(() => {
    const sid = expandedScene || scenes[0]?.id || "";
    if (!sid) {
      setSceneNarrationDraft("");
      setSceneNarrationDirty(false);
      scriptEditSceneIdRef.current = "";
      lastServerNarrationForExpandedRef.current = "";
      return;
    }
    const switched = String(scriptEditSceneIdRef.current) !== String(sid);
    scriptEditSceneIdRef.current = String(sid);
    const sc = scenes.find((s) => String(s.id) === String(sid));
    const next = sc?.narration_text ?? "";
    if (switched) {
      setSceneNarrationDraft(next);
      setSceneNarrationDirty(false);
      lastServerNarrationForExpandedRef.current = next;
      return;
    }
    if (next !== lastServerNarrationForExpandedRef.current) {
      lastServerNarrationForExpandedRef.current = next;
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

  /** When project SSE is live, ``jobs_update`` / ``agent_run_update`` reduce the need to hammer ``GET /v1/jobs/{id}`` for media. */
  const effectiveMediaJobPollMs = useMemo(() => {
    if (!sseConnected) return jobPollIntervalMs;
    return Math.min(60_000, Math.max(2_000, jobPollIntervalMs * 4));
  }, [sseConnected, jobPollIntervalMs]);

  const { job: mediaJob } = usePollJob(mediaJobId, mediaPoll && studioReady, effectiveMediaJobPollMs);

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
        mediaJob.status === "failed" && (mediaJob.error_message || mediaJob.result)
          ? parsePhase5GateModalPayload(mediaJob.error_message, mediaJob.result)
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

  /** Enqueue one image job per scene in the current chapter, spacing by Settings → Studio (default 5s, not provider generation time). */
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
        if (excludeCharacterBibleFromPrompts) extra.exclude_character_bible = true;
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
        void postImage(expandedScene, "generate-image", {});
      }, [expandedScene, projectId, busy, postImage]),

      onSaveNarration: useCallback(() => {
        if (sceneNarrationDirty) void saveSceneNarrationDraft();
      }, [sceneNarrationDirty, saveSceneNarrationDraft]),

      onToggleHelp: useCallback(() => {
        setShowShortcutHelp((prev) => !prev);
      }, []),
    },
    { enabled: activePage === "editor" },
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

  const uploadRecordedSceneVo = useCallback(
    async (blob) => {
      const sid = String(selectedSceneId || "").trim();
      if (!sid || !blob?.size) {
        setSceneVoRecordPhase("idle");
        return;
      }
      setSceneVoRecordPhase("saving");
      setError("");
      try {
        const fd = new FormData();
        const name =
          blob.type && String(blob.type).includes("webm")
            ? "scene-vo.webm"
            : blob.type && String(blob.type).includes("mp4")
              ? "scene-vo.m4a"
              : "scene-vo.webm";
        fd.append("file", blob, name);
        const r = await apiForm(`/v1/scenes/${encodeURIComponent(sid)}/narration/upload`, {
          method: "POST",
          body: fd,
        });
        const b = await parseJson(r);
        if (!r.ok) throw new Error(apiErrorMessage(b));
        void loadSceneNarrationMeta(sid);
        if (chapterId) void loadPhase3Summary(chapterId);
        if (projectId) void refreshPhase5Readiness({ reportError: false });
        setMessage("Microphone VO saved for this scene (replaces previous scene narration audio).");
      } catch (e) {
        setError(formatUserFacingError(e));
      } finally {
        setSceneVoRecordPhase("idle");
      }
    },
    [selectedSceneId, chapterId, loadSceneNarrationMeta, loadPhase3Summary, projectId, refreshPhase5Readiness],
  );

  const startSceneVoRecording = useCallback(async () => {
    const sid = String(selectedSceneId || "").trim();
    if (!sid) return;
    setError("");
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      sceneVoMediaChunksRef.current = [];
      const mimeCandidates = ["audio/webm;codecs=opus", "audio/webm", "audio/mp4"];
      let mime = "";
      for (const m of mimeCandidates) {
        if (typeof MediaRecorder !== "undefined" && MediaRecorder.isTypeSupported(m)) {
          mime = m;
          break;
        }
      }
      const mr = mime ? new MediaRecorder(stream, { mimeType: mime }) : new MediaRecorder(stream);
      mr.addEventListener("dataavailable", (ev) => {
        if (ev.data && ev.data.size) sceneVoMediaChunksRef.current.push(ev.data);
      });
      mr.addEventListener("stop", () => {
        stream.getTracks().forEach((t) => t.stop());
        const chunks = sceneVoMediaChunksRef.current;
        sceneVoRecorderRef.current = null;
        const blob = new Blob(chunks, { type: mr.mimeType || mime || "audio/webm" });
        void uploadRecordedSceneVo(blob);
      });
      mr.start(250);
      sceneVoRecorderRef.current = mr;
      setSceneVoRecordPhase("recording");
    } catch (e) {
      const err = e && typeof e === "object" ? e : null;
      setError(
        err && "name" in err && err.name === "NotAllowedError"
          ? "Microphone permission denied. Allow access in the browser to record."
          : formatUserFacingError(e) || "Could not start recording.",
      );
    }
  }, [selectedSceneId, uploadRecordedSceneVo]);

  const stopSceneVoRecording = useCallback(() => {
    const mr = sceneVoRecorderRef.current;
    if (mr && mr.state === "recording") {
      setSceneVoRecordPhase("saving");
      try {
        if (typeof mr.requestData === "function") mr.requestData();
      } catch {
        /* ignore */
      }
      mr.stop();
    }
  }, []);

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
        timeoutMs: PROMPT_ENHANCE_API_TIMEOUT_MS,
      });
      const b = await parseJson(r);
      if (!r.ok) throw new Error(apiErrorMessage(b));
      const text = b.data?.text;
      if (typeof text !== "string" || !String(text).trim()) throw new Error("No improved text returned.");
      setSceneNarrationDraft(String(text).trim());
      setSceneNarrationDirty(true);
      setMessage("Narration rewritten to match project narration style.");
    } catch (e) {
      setError(formatPromptEnhanceClientError(e));
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
        timeoutMs: PROMPT_ENHANCE_API_TIMEOUT_MS,
      });
      const b = await parseJson(r);
      if (!r.ok) throw new Error(apiErrorMessage(b));
      const text = b.data?.text;
      if (typeof text !== "string" || !String(text).trim()) throw new Error("No expanded text returned.");
      setSceneNarrationDraft(String(text).trim());
      setSceneNarrationDirty(true);
      setMessage("Narration expanded. Review and save if it reads well.");
    } catch (e) {
      setError(formatPromptEnhanceClientError(e));
    } finally {
      setPromptExpandVoBusy(false);
    }
  }, [selectedSceneId, sceneNarrationDraft, sceneVoExpandSentenceTarget, sceneVoExpandContext]);

  useEffect(() => {
    setSceneVoExpandContext("");
  }, [selectedSceneId]);

  const timelineTotalSec = scenes.reduce((acc, s) => acc + Number(s.planned_duration_sec || 0), 0);
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
  const selectedNarrProgressPct =
    selectedNarrGuide && selectedNarrGuide.narrationSec > 0
      ? Math.min(100, (selectedCoveredSec / selectedNarrGuide.narrationSec) * 100)
      : 0;

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

  pipelineCollabRef.current = {
    projectId,
    setProjectId,
    chapterId,
    expandedScene,
    title,
    topic,
    runtime,
    frameAspectRatio,
    clipFrameFit,
    noNarration,
    stylePresets,
    loadProjects,
    setChapters,
    setScenes,
    setChapterId,
    loadChapters,
    loadScenes,
    loadPhase3Summary,
    loadChapterNarration,
    loadSceneAssets,
    loadSceneNarrationMeta,
    loadProjectCriticReports,
    loadPipelineStatus,
    refreshPhase5Readiness,
    setTimelineVersionId,
  };

  projectScenesCollabRef.current = {
    loadSceneAssets,
    refreshPhase5Readiness,
    resetCharacters,
    onOpenProjectStart(restore) {
      const keepAgent = Boolean(restore?.agentRunId && String(restore.agentRunId).length >= 32);
      if (!keepAgent) {
        resetPipelineAgentSlice();
      }
      setPinnedPreviewAssetId(null);
      setMediaPreviewTab("scene");
      resetAssetsMediaSlice();
      resetTimelineExportSlice();
      setTimelineVersionId(
        restore?.timelineVersionId && String(restore.timelineVersionId).trim()
          ? restore.timelineVersionId
          : "",
      );
      setMediaJobId("");
      setMediaPoll(false);
      resetCharacters();
      setLastHandledMediaJobId("");
      setPhase3Summary(null);
      setCriticReport(null);
    },
    onOpenProjectLoadedMeta(p) {
      setFrameAspectRatio(p.frame_aspect_ratio === "9:16" ? "9:16" : "16:9");
      setClipFrameFit(p.clip_frame_fit === "letterbox" ? "letterbox" : "center_crop");
      setNoNarration(Boolean(p.no_narration));
      setUseAllApprovedSceneMedia(Boolean(p.use_all_approved_scene_media));
      setIncludeSpokenDialogueInVideoPrompt(Boolean(p.include_spoken_dialogue_in_video_prompt));
    },
    onRestoreAgentRun(agentRunId) {
      setAgentRunId(agentRunId);
    },
    onOpenProjectActiveJobs(jobs, restore) {
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
    },
    onOpenProjectFailed() {
      setTitle("");
      setTopic("");
    },
    onClearCurrentProjectExtras() {
      resetPipelineAgentSlice();
      setPinnedPreviewAssetId(null);
      resetAssetsMediaSlice();
      resetTimelineExportSlice();
      setPhase3Summary(null);
      setCriticReport(null);
    },
    onStartNewProjectDraftExtras() {
      setActivePage("editor");
      resetPipelineAgentSlice();
      setPinnedPreviewAssetId(null);
      resetAssetsMediaSlice();
      resetTimelineExportSlice();
      setMediaJobId("");
      setMediaPoll(false);
      setLastHandledMediaJobId("");
      setPhase3Summary(null);
      setCriticReport(null);
      setProjectCriticReports([]);
      setCriticListError("");
      setActiveProjectJobs([]);
      setActiveJobsLoadErr("");
      setFrameAspectRatio("16:9");
      setClipFrameFit("center_crop");
      setNoNarration(false);
      queueMicrotask(() => {
        document.getElementById("studio-pipeline-panel")?.scrollIntoView({ behavior: "smooth", block: "start" });
      });
    },
    onResetTenantBoundaryExtras() {
      resetPipelineAgentSlice();
      setPinnedPreviewAssetId(null);
      resetAssetsMediaSlice();
      resetTimelineExportSlice();
      setMediaJobId("");
      setMediaPoll(false);
      setLastHandledMediaJobId("");
      setPhase3Summary(null);
      setCriticReport(null);
      setProjectCriticReports([]);
      setCriticListError("");
      setActiveProjectJobs([]);
      setActiveJobsLoadErr("");
      resetResearch();
    },
  };

  const studioEditorValue = useStudioEditorComposition({
    accountProfile,
    activeJobsLoadErr,
    activeProjectJobs,
    agentRunId,
    agentRunStallInfo,
    appConfig,
    approveAsset,
    assetGenerationPrompt,
    autoThrough,
    batchImageRangeFrom,
    batchImageRangeTo,
    batchImagesProgress,
    blocked,
    blockedChapterReportHints,
    bulkApproveAssets,
    bulkRejectAssets,
    burnSubtitlesOnFinalCut,
    busy,
    cancelBackgroundJob,
    celeryRestarting,
    celeryStatus,
    celeryStatusDetail,
    celeryWorkers,
    chapterId,
    chapterTitleForId,
    chapters,
    clearAssetSelection,
    clearTaskBacklog,
    clipCrossfadeSec,
    clipFrameFit,
    continuePipelineAuto,
    criticGateChapterIds,
    criticListError,
    criticReport,
    criticReportTargetLabel,
    deleteProject,
    enhanceRetryImagePrompt,
    enhanceSceneVoFromStyle,
    error,
    events,
    excludeCharacterBibleFromPrompts,
    expandSceneVoScript,
    exportAttentionAssetIdSet,
    exportAttentionSceneIdSet,
    failedReadinessIssues,
    forceReplanScenesOnContinue,
    publishToYouTube,
    youtubeConnected,
    youtubeStatusLoading,
    frameAspectRatio,
    friendlyEventMeta,
    friendlyReadinessIssue,
    gallerySceneAssets,
    goToChapterScene,
    headerProgressBanner,
    humanizeMetaKey,
    idem,
    importSceneAssetFromStock,
    includeSpokenDialogueInVideoPrompt,
    loadChapters,
    loadCriticReport,
    loadProjectCriticReports,
    loadProjects,
    loadSceneAssets,
    loadScenes,
    loadTimelineMixFields,
    mediaJob,
    mediaJobId,
    mediaPoll,
    mediaPreviewTab,
    mixMusicVol,
    mixNarrVol,
    moveSceneAssetInSequence,
    musicBedPick,
    musicBeds,
    musicFileInputRef,
    musicUploadLicense,
    narrationPreviewIsSceneTrack,
    narrationPreviewSrc,
    narrationWordCount,
    noNarration,
    openProject,
    openRestartAutomationModal,
    openSceneForCriticReport,
    openSceneForTimelineAttentionAsset,
    panelSizes,
    patchTimelineMixToServer,
    patchWorkspaceConfig,
    pexelsImportKey,
    pexelsSearchBusy,
    pexelsSearchErr,
    pexelsSearchQuery,
    pexelsSearchResults,
    pexelsStockTab,
    phase5Ready,
    pipelineActivityRunStatus,
    pipelineControl,
    pipelineMode,
    pipelineStatus,
    pipelineStatusWithActivity,
    postChapterCritique,
    postImage,
    postSceneCritique,
    postScenesExtend,
    postScenesGenerate,
    previewMediaError,
    previewKind,
    previewUrl,
    projectCriticReports,
    projectId,
    projects,
    promptEnhanceImageBusy,
    promptEnhanceVoBusy,
    promptExpandVoBusy,
    queueMediaJob,
    queueRoughThenFinalCompile,
    reconcileTimelineClipImages,
    refineBracketImageWithLlm,
    refreshPhase5Readiness,
    refreshRun,
    rejectAllAssets,
    rejectAndRegenerateRoughCutImages,
    rejectAsset,
    reorderScenes,
    rerunPipelineFromStep,
    restartAutomationForce,
    restartAutomationOpen,
    restartAutomationThrough,
    restartCelery,
    restartRerunWebResearch,
    retryPrompt,
    retryVideoPrompt,
    revertSceneNarrationDraft,
    run,
    runStepNow,
    runtime,
    saveClipFrameFit,
    saveIncludeSpokenDialogueInVideoPrompt,
    saveSceneNarrationDraft,
    saveSceneVideoCharacterDialogue,
    saveTimelineMixToServer,
    saveUseAllApprovedSceneMedia,
    sceneAssets,
    sceneAssetsFetchError,
    sceneClipFileInputRef,
    sceneClipSec,
    sceneClipUploadKind,
    sceneLabelForId,
    sceneNarrationDirty,
    sceneNarrationDraft,
    sceneNarrationGuideMap,
    sceneNarrationMeta,
    sceneNarrationSaving,
    sceneStockLibrary,
    sceneVideoCharacterDialogueDirty,
    sceneVideoCharacterDialogueDraft,
    sceneVoExpandContext,
    sceneVoExpandSentenceTarget,
    sceneVoRecordPhase,
    scenes,
    scenesLoading,
    scheduleDebouncedTimelineMixSave,
    schedulePersistStudioMixDefaults,
    selectAllAssets,
    selectedAssetIds,
    selectedCoveredSec,
    selectedFalVideoKind,
    selectedNarrGuide,
    selectedNarrProgressPct,
    selectedScene,
    selectedSceneId,
    setAutoThrough,
    setBatchImageRangeFrom,
    setBatchImageRangeTo,
    setBurnSubtitlesOnFinalCut,
    setBusy,
    setChapterId,
    setClipCrossfadeSec,
    setDragState,
    setError,
    setExcludeCharacterBibleFromPrompts,
    setExpandedScene,
    setForceReplanScenesOnContinue,
    setPublishToYouTube,
    setFrameAspectRatio,
    setMediaPreviewTab,
    setMessage,
    setMixMusicVol,
    setMixNarrVol,
    setMusicBedPick,
    setMusicUploadLicense,
    setNoNarration,
    setPexelsSearchQuery,
    setPexelsStockTab,
    setPinnedPreviewAssetId,
    setPipelineMode,
    setPreviewMediaError,
    setRefineBracketImageWithLlm,
    setRestartAutomationForce,
    setRestartAutomationOpen,
    setRestartAutomationThrough,
    setRestartRerunWebResearch,
    setRetryPrompt,
    setRetryVideoPrompt,
    setRuntime,
    setSceneClipUploadKind,
    setSceneNarrationDirty,
    setSceneNarrationDraft,
    setSceneStockLibrary,
    setSceneVideoCharacterDialogueDirty,
    setSceneVideoCharacterDialogueDraft,
    setSceneVoExpandContext,
    setSceneVoExpandSentenceTarget,
    setScenes,
    setShowShortcutHelp,
    setStockVideoTrimModal,
    setTimelineVersionId,
    setTitle,
    setTopic,
    setTrimByScene,
    settingsBusy,
    showToast,
    startAgentRun,
    startBatchChapterImages,
    startNewProjectDraft,
    startProjectAgentFromList,
    startSceneVoRecording,
    stockVideoTrimModal,
    stopBatchChapterImages,
    stopProjectAgentFromList,
    stopSceneVoRecording,
    submitRestartAutomation,
    timelineExportWarnings,
    timelineTotalSec,
    timelineVersionId,
    title,
    toggleAssetSelected,
    topic,
    trimByScene,
    uploadMusicBedFile,
    uploadSceneClipFile,
    useAllApprovedSceneMedia,
    workspaceRef,
  });

  const studioPageProps = useMemo(
    () => ({
      legal: { setActivePage },
      ideas: {
        showToast,
        loadProjects: () => void loadProjects(),
        setAgentRunId,
        setProjectId,
        setActivePage,
      },
      account: {
        authMode: authBootstrap.mode,
        accountProfile,
        onRefreshProfile: refreshAccountProfile,
        onSignOut: signOutSaas,
        showToast,
      },
      admin: { showToast, workspaceTenantId: adminToolsWorkspaceTenantId },
      usage: {
        usageSummary,
        usageErr,
        usageLoading,
        usageDays,
        setUsageDays,
        loadUsageSummary,
      },
      prompts: {
        llmPromptsErr,
        llmPromptsBusy,
        llmPrompts,
        loadLlmPrompts,
        llmPromptDrafts,
        setLlmPromptDrafts,
        saveLlmPrompt,
        resetLlmPrompt,
      },
      research: {
        projectId,
        chapters,
        researchJsonDraft,
        setResearchJsonDraft,
        researchMeta,
        researchPageBusy,
        researchPageErr,
        researchPipelineBusy,
        chapterRegenerateId,
        chapterScriptsDraft,
        setChapterScriptsDraft,
        loadResearchChaptersEditor,
        rerunResearch,
        saveDossier,
        regenerateChapterScript,
        saveChapter,
      },
      settings: {
        accountProfile,
        adapterSmokePollActive,
        appConfig,
        chapters,
        chatterboxRecording,
        chatterboxVoiceRef,
        chatterboxVoiceRefBusy,
        chatterboxVoiceRefErr,
        comfyuiTestBusy,
        comfyuiTestOutputBust,
        comfyuiWorkflows,
        comfyuiWorkflowsBusy,
        comfyuiWorkflowsErr,
        createOrUpdateNarrationStyle,
        credKeyNote,
        credKeyNoteXaiGrok,
        deleteChatterboxVoiceRef,
        deleteComfyuiWorkflow,
        deleteNarrationStyleByRef,
        elevenlabsVoices,
        elevenlabsVoicesNote,
        falCatalogNote,
        falImageModels,
        falVideoByKind,
        falVideoEndpointKind,
        falVideoModels,
        finishChatterboxRecording,
        geminiTtsVoices,
        generationSettingsTab,
        loadAppSettings,
        loadChatterboxVoiceRef,
        loadComfyuiWorkflows,
        loadElevenlabsVoices,
        loadFalCatalog,
        narEditingRef,
        narFormPrompt,
        narFormTitle,
        narrationStylesLib,
        narrationStylesLibBusy,
        narrationStylesLibErr,
        platformCredentialKeysInherited,
        projects,
        run,
        runAdapterSmokeTest,
        runComfyuiWorkflowTest,
        runTelegramConnectionTest,
        runtime,
        saveAppSettings,
        scenes,
        selectedFalVideoKind,
        setAppConfig,
        setError,
        setGenerationSettingsTab,
        setNarEditingRef,
        setNarFormPrompt,
        setNarFormTitle,
        setSettingsTab,
        settingsBusy,
        settingsLoadError,
        settingsTab,
        showToast,
        speechProviderSettingSelectValue,
        startChatterboxRecording,
        stylePresets,
        telegramPlanLocked,
        telegramTestLoading,
        telegramWebhookPublicOrigin,
        telegramWebhookPublicUrl,
        toasts,
        uploadChatterboxFile,
        uploadComfyuiWorkflowFile,
      },
      characters: {
        projectId,
        busy,
        projectCharacters,
        setProjectCharacters,
        charactersJobId,
        charactersJob,
        loadProjectCharacters,
        generateFromStory,
        saveCharacter,
        deleteCharacter,
        addCharacter,
        friendlyRunStatus: charactersFriendlyRunStatus,
      },
      editor: { studioEditorValue },
    }),
    [
      authBootstrap.mode,
      accountProfile,
      refreshAccountProfile,
      signOutSaas,
      showToast,
      loadProjects,
      setAgentRunId,
      setProjectId,
      adminToolsWorkspaceTenantId,
      usageSummary,
      usageErr,
      usageLoading,
      usageDays,
      loadUsageSummary,
      llmPromptsErr,
      llmPromptsBusy,
      llmPrompts,
      loadLlmPrompts,
      llmPromptDrafts,
      saveLlmPrompt,
      resetLlmPrompt,
      projectId,
      chapters,
      researchJsonDraft,
      researchMeta,
      researchPageBusy,
      researchPageErr,
      researchPipelineBusy,
      chapterRegenerateId,
      chapterScriptsDraft,
      loadResearchChaptersEditor,
      rerunResearch,
      saveDossier,
      regenerateChapterScript,
      saveChapter,
      adapterSmokePollActive,
      appConfig,
      chatterboxRecording,
      chatterboxVoiceRef,
      chatterboxVoiceRefBusy,
      chatterboxVoiceRefErr,
      comfyuiTestBusy,
      comfyuiTestOutputBust,
      comfyuiWorkflows,
      comfyuiWorkflowsBusy,
      comfyuiWorkflowsErr,
      createOrUpdateNarrationStyle,
      credKeyNote,
      credKeyNoteXaiGrok,
      deleteChatterboxVoiceRef,
      deleteComfyuiWorkflow,
      deleteNarrationStyleByRef,
      elevenlabsVoices,
      elevenlabsVoicesNote,
      falCatalogNote,
      falImageModels,
      falVideoByKind,
      falVideoModels,
      finishChatterboxRecording,
      geminiTtsVoices,
      generationSettingsTab,
      loadAppSettings,
      loadChatterboxVoiceRef,
      loadComfyuiWorkflows,
      loadElevenlabsVoices,
      loadFalCatalog,
      narEditingRef,
      narFormPrompt,
      narFormTitle,
      narrationStylesLib,
      narrationStylesLibBusy,
      narrationStylesLibErr,
      platformCredentialKeysInherited,
      projects,
      run,
      runAdapterSmokeTest,
      runComfyuiWorkflowTest,
      runTelegramConnectionTest,
      runtime,
      saveAppSettings,
      scenes,
      selectedFalVideoKind,
      settingsBusy,
      settingsLoadError,
      settingsTab,
      speechProviderSettingSelectValue,
      startChatterboxRecording,
      stylePresets,
      telegramPlanLocked,
      telegramTestLoading,
      telegramWebhookPublicOrigin,
      telegramWebhookPublicUrl,
      toasts,
      uploadChatterboxFile,
      uploadComfyuiWorkflowFile,
      busy,
      projectCharacters,
      charactersJobId,
      charactersJob,
      loadProjectCharacters,
      generateFromStory,
      saveCharacter,
      deleteCharacter,
      addCharacter,
      charactersFriendlyRunStatus,
      studioEditorValue,
    ],
  );


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
                      if (!r.ok) {
                        throw new Error(apiErrorMessage(b) || `Tenant switch failed (HTTP ${r.status})`);
                      }
                      setDirectorAuthSession({ tenantId: v });
                      setEventAuthKey((k) => k + 1);
                      window.location.reload();
                    } catch (err) {
                      setError(formatUserFacingError(err));
                    }
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

      {stockVideoTrimModal ? (
        <div
          className="restart-automation-modal-backdrop"
          role="presentation"
          onClick={() => {
            setStockVideoTrimModal(null);
            setPexelsTrimHint(null);
            setPexelsTrimHintBusy(false);
          }}
        >
          <div
            className="panel"
            role="dialog"
            aria-modal="true"
            aria-labelledby="stock-trim-title"
            style={{ maxWidth: 440 }}
            onClick={(e) => e.stopPropagation()}
            onKeyDown={(e) => {
              if (e.key === "Escape") {
                setStockVideoTrimModal(null);
                setPexelsTrimHint(null);
                setPexelsTrimHintBusy(false);
              }
            }}
          >
            <h3 id="stock-trim-title">Trim this stock video</h3>
            <p className="subtle" style={{ marginTop: 8 }}>
              {stockVideoTrimModal.reportedDurationSec != null && Number.isFinite(stockVideoTrimModal.reportedDurationSec)
                ? `${stockVideoTrimModal.library === "storyblocks" ? "Storyblocks" : "Pexels"} reports about ${Math.round(stockVideoTrimModal.reportedDurationSec)}s. `
                : "Length may be unknown until download. "}
              Clips are capped at 10s. Choose how much to keep from the start:
            </p>
            {pexelsTrimHintBusy ? (
              <p className="subtle" style={{ marginTop: 10 }}>
                Loading scene audio hint…
              </p>
            ) : pexelsTrimHint ? (
              <p className="subtle" style={{ marginTop: 10, fontSize: "0.85rem" }}>
                {pexelsTrimHint.scene_narration_sec != null &&
                Number.isFinite(Number(pexelsTrimHint.scene_narration_sec)) &&
                Number(pexelsTrimHint.scene_narration_sec) > 0
                  ? `Latest scene narration ≈ ${Number(pexelsTrimHint.scene_narration_sec).toFixed(1)}s (“Match scene audio length” uses this, capped at 10s).`
                  : pexelsTrimHint.planned_duration_sec != null &&
                      Number.isFinite(Number(pexelsTrimHint.planned_duration_sec)) &&
                      Number(pexelsTrimHint.planned_duration_sec) > 0
                    ? `No scene VO file yet — planned scene duration ${Number(pexelsTrimHint.planned_duration_sec)}s is used for “Match scene audio length”, capped at 10s.`
                    : `No narration or plan yet — “Match scene audio length” falls back to your studio default clip (${pexelsTrimHint.studio_clip_default_sec ?? 10}s).`}
              </p>
            ) : null}
            <div className="restart-automation-modal-actions" style={{ marginTop: 16, flexWrap: "wrap", gap: 8 }}>
              <button
                type="button"
                className="secondary"
                onClick={() => {
                  setStockVideoTrimModal(null);
                  setPexelsTrimHint(null);
                  setPexelsTrimHintBusy(false);
                }}
              >
                Cancel
              </button>
              <button
                type="button"
                disabled={busy || Boolean(pexelsImportKey)}
                onClick={() => {
                  const lib = stockVideoTrimModal.library;
                  const id = stockVideoTrimModal.mediaId;
                  setStockVideoTrimModal(null);
                  setPexelsTrimHint(null);
                  setPexelsTrimHintBusy(false);
                  void importSceneAssetFromStock(lib, "video", id, "5");
                }}
              >
                Trim to 5s
              </button>
              <button
                type="button"
                disabled={busy || Boolean(pexelsImportKey)}
                onClick={() => {
                  const lib = stockVideoTrimModal.library;
                  const id = stockVideoTrimModal.mediaId;
                  setStockVideoTrimModal(null);
                  setPexelsTrimHint(null);
                  setPexelsTrimHintBusy(false);
                  void importSceneAssetFromStock(lib, "video", id, "10");
                }}
              >
                Trim to 10s
              </button>
              <button
                type="button"
                disabled={busy || Boolean(pexelsImportKey)}
                onClick={() => {
                  const lib = stockVideoTrimModal.library;
                  const id = stockVideoTrimModal.mediaId;
                  setStockVideoTrimModal(null);
                  setPexelsTrimHint(null);
                  setPexelsTrimHintBusy(false);
                  void importSceneAssetFromStock(lib, "video", id, "scene_narration");
                }}
              >
                Match scene audio length
              </button>
            </div>
          </div>
        </div>
      ) : null}

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
      <div
        className="chat-studio-keepalive"
        style={{ display: activePage === "chat" ? undefined : "none" }}
        aria-hidden={activePage !== "chat"}
        hidden={activePage !== "chat" ? true : undefined}
      >
        <ChatStudioPage
          appConfig={appConfig}
          stylePresets={stylePresets}
          projects={projects}
          onReloadProjects={() => void loadProjects()}
          studioProjectId={projectId}
          onStudioProjectOpen={onChatStudioProjectOpen}
          isPageActive={activePage === "chat"}
        />
      </div>
      {activePage === "chat" ? null : (
        <StudioPageRouter activePage={activePage} pages={studioPageProps} />
      )}
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
