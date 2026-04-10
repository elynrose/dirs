/**
 * Studio-wide constants and lookup tables.
 * Extracted from App.jsx to allow reuse across feature modules.
 */

export const DIRECTOR_UI_SESSION_KEY = "director_ui_session";
/** After plan selection on the public pricing page, sign-in completes checkout for this plan slug. */
export const PENDING_CHECKOUT_PLAN_KEY = "directely_pending_checkout_plan_slug";
export const FAL_CATALOG_MIN_REFRESH_MS = 60_000;

/** Default narration preset id (must match API `style_presets.DEFAULT_NARRATION_PRESET`). */
export const DEFAULT_NARRATION_PRESET_ID = "narrative_documentary";

/**
 * Job types that the main Studio active-job poller tracks.
 * Matches GET /v1/projects/{id}/jobs/active payload types.
 */
export const STUDIO_MEDIA_JOB_TYPES = new Set([
  "scene_generate",
  "scene_extend",
  "scene_generate_image",
  "scene_generate_video",
  "scene_critique",
  "chapter_critique",
  "scene_critic_revision",
  "narration_generate",
  "narration_generate_scene",
  "subtitles_generate",
  "rough_cut",
  "fine_cut",
  "final_cut",
  "export",
  "research_run",
  "script_outline",
  "script_chapters",
  "script_chapter_regenerate",
]);

/** Job types that trigger the Phase-5 export gate (readiness check). */
export const EXPORT_COMPILE_JOB_TYPES = new Set(["rough_cut", "fine_cut", "final_cut", "export"]);

/**
 * OpenAI TTS voices — must stay in sync with API `speech_openai.OPENAI_TTS_VOICES`.
 */
export const OPENAI_TTS_VOICE_OPTIONS = [
  "alloy",
  "ash",
  "ballad",
  "coral",
  "echo",
  "fable",
  "onyx",
  "nova",
  "sage",
  "shimmer",
  "verse",
  "marin",
  "cedar",
];

/**
 * Fallback Gemini TTS voice list when GET /v1/settings/gemini-tts-voices fails.
 * Mirrors API `voice_catalog.GEMINI_TTS`.
 */
export const GEMINI_TTS_VOICE_FALLBACK = [
  { id: "Zephyr", label: "Zephyr — Bright" },
  { id: "Puck", label: "Puck — Upbeat" },
  { id: "Charon", label: "Charon — Informative" },
  { id: "Kore", label: "Kore — Firm" },
  { id: "Fenrir", label: "Fenrir — Excitable" },
  { id: "Leda", label: "Leda — Youthful" },
  { id: "Orus", label: "Orus — Firm" },
  { id: "Aoede", label: "Aoede — Breezy" },
  { id: "Callirrhoe", label: "Callirrhoe — Easy-going" },
  { id: "Autonoe", label: "Autonoe — Bright" },
  { id: "Enceladus", label: "Enceladus — Breathy" },
  { id: "Iapetus", label: "Iapetus — Clear" },
  { id: "Umbriel", label: "Umbriel — Easy-going" },
  { id: "Algieba", label: "Algieba — Smooth" },
  { id: "Despina", label: "Despina — Smooth" },
  { id: "Erinome", label: "Erinome — Clear" },
  { id: "Algenib", label: "Algenib — Gravelly" },
  { id: "Rasalgethi", label: "Rasalgethi — Informative" },
  { id: "Laomedeia", label: "Laomedeia — Upbeat" },
  { id: "Achernar", label: "Achernar — Soft" },
  { id: "Alnilam", label: "Alnilam — Firm" },
  { id: "Schedar", label: "Schedar — Even" },
  { id: "Gacrux", label: "Gacrux — Mature" },
  { id: "Pulcherrima", label: "Pulcherrima — Forward" },
  { id: "Achird", label: "Achird — Friendly" },
  { id: "Zubenelgenubi", label: "Zubenelgenubi — Casual" },
  { id: "Vindemiatrix", label: "Vindemiatrix — Gentle" },
  { id: "Sadachbia", label: "Sadachbia — Lively" },
  { id: "Sadaltager", label: "Sadaltager — Knowledgeable" },
  { id: "Sulafat", label: "Sulafat — Warm" },
];

/**
 * Kokoro local TTS (`hexgrad/Kokoro-82M` voice `.pt` stems). Must match files under `voices/` on the HF repo.
 * @type {{ id: string, label: string }[]}
 */
export const KOKORO_VOICE_OPTIONS = [
  { id: "af_bella", label: "American English — Bella (female)" },
  { id: "af_heart", label: "American English — Heart (female)" },
  { id: "af_sarah", label: "American English — Sarah (female)" },
  { id: "af_sky", label: "American English — Sky (female)" },
  { id: "af_nicole", label: "American English — Nicole (female)" },
  { id: "af_nova", label: "American English — Nova (female)" },
  { id: "af_jessica", label: "American English — Jessica (female)" },
  { id: "af_kore", label: "American English — Kore (female)" },
  { id: "af_alloy", label: "American English — Alloy (female)" },
  { id: "af_aoede", label: "American English — Aoede (female)" },
  { id: "am_adam", label: "American English — Adam (male)" },
  { id: "am_michael", label: "American English — Michael (male)" },
  { id: "am_echo", label: "American English — Echo (male)" },
  { id: "am_eric", label: "American English — Eric (male)" },
  { id: "am_fenrir", label: "American English — Fenrir (male)" },
  { id: "am_liam", label: "American English — Liam (male)" },
  { id: "am_onyx", label: "American English — Onyx (male)" },
  { id: "am_puck", label: "American English — Puck (male)" },
  { id: "am_santa", label: "American English — Santa (male)" },
  { id: "bf_emma", label: "British English — Emma (female)" },
  { id: "bf_isabella", label: "British English — Isabella (female)" },
  { id: "bf_alice", label: "British English — Alice (female)" },
  { id: "bf_lily", label: "British English — Lily (female)" },
  { id: "bm_george", label: "British English — George (male)" },
  { id: "bm_lewis", label: "British English — Lewis (male)" },
  { id: "bm_daniel", label: "British English — Daniel (male)" },
  { id: "bm_fable", label: "British English — Fable (male)" },
];

/** Kokoro `lang_code` for G2P (see `kokoro.pipeline.LANG_CODES`). */
export const KOKORO_LANG_OPTIONS = [
  { id: "a", label: "American English (en-us)" },
  { id: "b", label: "British English (en-gb)" },
  { id: "e", label: "Spanish (es)" },
  { id: "f", label: "French (fr-fr)" },
  { id: "h", label: "Hindi (hi)" },
  { id: "i", label: "Italian (it)" },
  { id: "p", label: "Portuguese Brazil (pt-br)" },
  { id: "j", label: "Japanese (ja)" },
  { id: "z", label: "Mandarin Chinese (zh)" },
];

/** Fallback visual style presets when GET /v1/settings/style-presets fails. */
export const VISUAL_STYLE_PRESET_FALLBACK = [
  { id: "cinematic_documentary", label: "Cinematic Documentary (Live-Action Feel)" },
  { id: "archival_historical", label: "Archival / Historical Stills" },
  { id: "aerial_epic", label: "Aerial / Epic Landscape" },
  { id: "noir_dramatic", label: "Noir / Dramatic Reenactment" },
  { id: "three_d_animation", label: "Stylized 3D Animation" },
  { id: "hand_drawn_2d", label: "Hand-Drawn 2D" },
  { id: "flat_infographic", label: "Flat / Infographic" },
  { id: "sci_tech_cgi", label: "Sci-Tech CGI" },
  { id: "cinematic_historical_epic", label: "Cinematic Historical Epic" },
];

// ---------------------------------------------------------------------------
// Pipeline step maps (shared between inspector panel + banner)
// ---------------------------------------------------------------------------

/** Short labels for pipeline steps in the inspector list. */
export const RUN_STEP_LABEL = {
  queued: "Queued",
  rerun: "Re-run from phase",
  director: "Directely",
  research: "Research",
  outline: "Outline",
  scripts: "Scripts",
  chapters: "Scripts",
  scenes: "Scenes",
  story_research_review: "Story vs research",
  scene_critique: "Scene reviews (legacy)",
  scene_critic_repair: "Scene fixes (legacy)",
  chapter_critique: "Chapter reviews (legacy)",
  chapter_critic_repair: "Chapter fixes (legacy)",
  auto_characters: "Character bible",
  auto_images: "Images",
  auto_narration: "Narration",
  auto_videos: "Videos",
  auto_timeline: "Timeline build",
  auto_rough_cut: "Rough cut (auto)",
  auto_final_cut: "Final mix (auto)",
  rough_cut: "Rough cut",
  subtitles: "Subtitles",
  final_cut: "Final mix",
  export: "Export",
  full_video: "Full render",
  done: "Finished",
};

/** Ordered stages per agent `through` mode (used for progress bar). */
export const AGENT_PROGRESS_ORDER = {
  chapters: ["director", "research", "outline", "chapters"],
  critique: ["director", "research", "outline", "chapters", "scenes"],
  full_video: [
    "director",
    "research",
    "outline",
    "chapters",
    "scenes",
    "auto_characters",
    "auto_images",
    "auto_videos",
    "auto_narration",
    "auto_timeline",
    "auto_rough_cut",
    "auto_final_cut",
  ],
};

/** Worker `pipeline_options.force_pipeline_steps` keys for the restart automation modal. */
export const RESTART_AUTOMATION_STEPS = [
  { key: "director", label: "Directely pack" },
  { key: "research", label: "Research" },
  { key: "outline", label: "Outline" },
  { key: "chapters", label: "Chapter scripts" },
  { key: "scenes", label: "Scene plan" },
  { key: "auto_characters", label: "Character bible" },
  { key: "auto_images", label: "Scene images" },
  { key: "auto_videos", label: "Scene videos" },
  { key: "auto_narration", label: "Narration (TTS)" },
];

/** Agent step key → pipeline status step `id`. */
export const AGENT_STEP_TO_PIPELINE_STEP_ID = {
  director: "director",
  research: "research",
  outline: "outline",
  chapters: "chapters",
  scenes: "scenes",
  /** Worker-only step (not a pipeline row); tie banner to the next stage. */
  story_research_review: "images",
  auto_characters: "characters",
  auto_images: "images",
  auto_videos: "video_clips",
  auto_narration: "narration",
  auto_timeline: "timeline",
  auto_rough_cut: "rough_cut",
  auto_final_cut: "final_cut",
};

/** Inspector row id → worker `pipeline_options.rerun_from_step` value. */
export const PIPELINE_STEP_TO_RERUN_FROM = {
  director: "director",
  research: "research",
  outline: "outline",
  chapters: "chapters",
  scenes: "scenes",
  characters: "auto_characters",
  images: "auto_images",
  video_clips: "auto_videos",
  narration: "auto_narration",
  timeline: "auto_timeline",
  rough_cut: "auto_rough_cut",
  final_cut: "auto_final_cut",
};

/** Re-running any of these steps requires full-video tail. */
export const PIPELINE_RERUN_NEEDS_FULL_VIDEO = new Set([
  "characters",
  "images",
  "video_clips",
  "narration",
  "timeline",
  "rough_cut",
  "final_cut",
]);

/** Pipeline step id → banner/agent effective step key (for animated icons). */
export const PIPELINE_STEP_ID_TO_AGENT_EFF_KEY = {
  director: "director",
  research: "research",
  outline: "outline",
  chapters: "chapters",
  scenes: "scenes",
  story_research_review: "story_research_review",
  characters: "auto_characters",
  images: "auto_images",
  video_clips: "auto_videos",
  narration: "auto_narration",
  timeline: "auto_timeline",
  rough_cut: "auto_rough_cut",
  final_cut: "auto_final_cut",
};

/** UUID regex for Phase 5 timeline version IDs. */
export const PHASE5_TIMELINE_UUID_RE =
  /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

/** Event meta label overrides for the agent run activity log. */
export const EVENT_META_LABELS = {
  timeline_version_id: "Timeline",
  scene_id: "Scene",
  chapter_id: "Chapter",
  repair_round: "Round",
  generated: "Generated",
  skipped_existing: "Already had",
  chapters_planned: "Chapters planned",
  chapters_skipped_short_script: "Skipped (short script)",
  scene_total: "Scenes",
  reason: "Note",
  timeline_version: "Timeline",
};

/**
 * Workspace `agent_run_auto_generate_scene_videos`.
 * When the key is missing, match scripts/budget_pipeline_test.py (on for full_video / hands-off).
 * Explicit `false` turns auto scene videos off.
 */
export function agentRunAutoGenerateSceneVideos(cfg) {
  const c = cfg && typeof cfg === "object" ? cfg : {};
  return c.agent_run_auto_generate_scene_videos !== false;
}

/**
 * Map Settings → Providers into `brief` preferred_* fields for POST /v1/agent-runs (new projects).
 * Aligns Studio with budget_pipeline_test.py passing project-level image/video/speech (and text) providers.
 */
export function briefPreferredMediaProvidersFromAppConfig(cfg) {
  const c = cfg && typeof cfg === "object" ? cfg : {};
  const out = {};
  const img = String(c.active_image_provider || "").trim();
  const vid = String(c.active_video_provider || "").trim();
  const sp = String(c.active_speech_provider || "").trim();
  const txt = String(c.active_text_provider || "").trim();
  if (img) out.preferred_image_provider = img;
  if (vid) out.preferred_video_provider = vid;
  if (sp) out.preferred_speech_provider = sp;
  if (txt) out.preferred_text_provider = txt;
  return out;
}
