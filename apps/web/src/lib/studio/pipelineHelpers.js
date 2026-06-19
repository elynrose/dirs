import {
  RUN_STEP_LABEL,
  AGENT_PROGRESS_ORDER,
  AGENT_STEP_TO_PIPELINE_STEP_ID,
  PIPELINE_STEP_ID_TO_AGENT_EFF_KEY,
  sceneAutomationMediaPipelineOptions,
  pipelineSpeedPatchForOptions,
} from "../constants.js";

export function friendlyPipelineStep(step) {
  if (!step) return "—";
  return RUN_STEP_LABEL[step] || String(step).replace(/_/g, " ");
}

export function friendlyRunStatus(status) {
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
export function pipelineStopRequested(ctrl) {
  return Boolean(ctrl && typeof ctrl === "object" && ctrl.stop_requested);
}

export function friendlyAgentRunStatus(run) {
  if (!run) return friendlyRunStatus(null);
  if (run.status === "running" && pipelineStopRequested(run.pipeline_control_json)) return "Stopping";
  return friendlyRunStatus(run.status);
}

/** While true, disable Re-run row / Restart… so users do not stack agent runs on an active one. */
export function agentRunLocksPipelineControls(run) {
  if (!run) return false;
  const st = run.status;
  if (st === "running" || st === "queued" || st === "paused") return true;
  return false;
}

export function friendlyPipelineStepStatus(status) {
  const m = {
    done: "Done",
    pending: "Waiting",
    running: "In progress",
    blocked: "Needs attention",
    skipped: "Skipped",
  };
  return m[status] || String(status || "").replace(/_/g, " ");
}

export function friendlyBlockReason(code) {
  if (code === "CRITIC_GATE") return "Chapter review gate";
  return String(code || "unknown").replace(/_/g, " ");
}

export function agentThroughFromRun(run, autoThroughFallback) {
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
export function agentStageHeadline(stepKey) {
  const m = {
    queued: "Waiting for the automation worker to start…",
    working: "Working on the pipeline…",
    director: "Now preparing the director brief…",
    research: "Now gathering research and sources…",
    outline: "Now outlining chapters…",
    chapters: "Now writing chapter scripts…",
    thumbnail: "Now creating thumbnail and YouTube copy…",
    opening_hook: "Now writing the opening hook…",
    scenes: "Now planning scenes and visuals…",
    outro: "Now adding the subscribe outro…",
    story_research_review: "Now reviewing the story against research…",
    auto_characters: "Now building the character bible…",
    auto_images: "Now generating scene images…",
    auto_videos: "Now generating scene videos…",
    auto_narration: "Now synthesizing narration audio…",
    auto_scene_coverage: "Now filling extra scene media for voice-over length…",
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

export function lastAgentEventWithStatus(stepsJson, status) {
  const evs = Array.isArray(stepsJson) ? stepsJson : [];
  for (let i = evs.length - 1; i >= 0; i--) {
    const e = evs[i];
    if (e && e.status === status && e.step) return e;
  }
  return null;
}

/** Latest per-chapter progress while the worker is in the ``scenes`` step (``steps_json`` row). */
export function lastScenesProgressEvent(stepsJson) {
  const evs = Array.isArray(stepsJson) ? stepsJson : [];
  for (let i = evs.length - 1; i >= 0; i--) {
    const e = evs[i];
    if (e && e.step === "scenes" && e.status === "progress") return e;
  }
  return null;
}

/** Latest per-scene progress while the worker is in the ``auto_narration`` step (inline TTS loop). */
export function lastAutoNarrationProgressEvent(stepsJson) {
  return lastAgentStepProgressEvent(stepsJson, "auto_narration");
}

/** Latest per-scene progress for an inline agent step (``status: progress`` rows in ``steps_json``). */
export function lastAgentStepProgressEvent(stepsJson, stepKey) {
  const evs = Array.isArray(stepsJson) ? stepsJson : [];
  const step = String(stepKey || "");
  if (!step) return null;
  for (let i = evs.length - 1; i >= 0; i--) {
    const e = evs[i];
    if (e && e.step === step && e.status === "progress") return e;
  }
  return null;
}

export function lastAutoSceneCoverageProgressEvent(stepsJson) {
  return lastAgentStepProgressEvent(stepsJson, "auto_scene_coverage");
}

export function lastAutoImagesProgressEvent(stepsJson) {
  return lastAgentStepProgressEvent(stepsJson, "auto_images");
}

/** Font Awesome classes per stage (`fa-solid` + animation). Paused uses a static icon. */
export function agentPipelineActivityIconClass(effKey, runStatus) {
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
    thumbnail: "fa-solid fa-image fa-beat-fade fa-fw pipeline-fa-icon",
    opening_hook: "fa-solid fa-bolt fa-shake fa-fw pipeline-fa-icon",
    scenes: "fa-solid fa-photo-film fa-beat-fade fa-fw pipeline-fa-icon",
    outro: "fa-solid fa-bell fa-beat fa-fw pipeline-fa-icon",
    story_research_review: "fa-solid fa-scale-balanced fa-shake fa-fw pipeline-fa-icon",
    auto_characters: "fa-solid fa-users fa-beat-fade fa-fw pipeline-fa-icon",
    auto_images: "fa-solid fa-image fa-beat-fade fa-fw pipeline-fa-icon",
    auto_videos: "fa-solid fa-clapperboard fa-beat-fade fa-fw pipeline-fa-icon",
    auto_narration: "fa-solid fa-microphone-lines fa-beat fa-fw pipeline-fa-icon",
    auto_scene_coverage: "fa-solid fa-images fa-beat-fade fa-fw pipeline-fa-icon",
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
  { macro: "thumbnail", types: new Set(["thumbnail_generate"]) },
  { macro: "opening_hook", types: new Set(["opening_hook_generate"]) },
  { macro: "outro", types: new Set(["outro_append"]) },
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

export function jobTypeToMacroStepKey(jobType) {
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
export function inferAgentStepKeyFromActiveJobs(jobs) {
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
export function inferMacroStepKeyFromJobType(jobType) {
  return jobTypeToMacroStepKey(jobType) ?? "pipeline";
}

export function studioJobKindHeadline(jobType) {
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
    thumbnail_generate: "Thumbnail",
    opening_hook_generate: "The Hook",
    outro_append: "Subscribe outro",
    characters_generate: "Character bible",
  };
  return m[t] || "Background job";
}

/** Resolve macro-step for progress UI (handles full_video tail where `current_step` is null). */
export function resolveEffectiveAgentStepKey(run, opts = {}) {
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
  const running = lastAgentEventWithStatus(evs, "running");
  if (run.status === "running" && running?.step) return running.step;
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

export function pipelineStepActivityIconClass(stepId, runStatus) {
  const effKey = PIPELINE_STEP_ID_TO_AGENT_EFF_KEY[stepId];
  if (!effKey) return "fa-solid fa-spinner fa-spin fa-fw pipeline-step-fa-icon";
  return agentPipelineActivityIconClass(effKey, runStatus);
}

/**
 * While the agent is running, `workflow_phase` can lag (e.g. chapter scripts stay "pending" until the
 * batched LLM returns). Reflect `current_step` / last running event / active jobs so each phase shows in progress.
 */
export function mergePipelineStepsWithAgentActivity(pipelineStatus, run, activeProjectJobs) {
  if (!pipelineStatus || !Array.isArray(pipelineStatus.steps)) return pipelineStatus;

  const agentSt = run?.status;
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
    if (effKey === "chapters") {
      next.detail =
        "Batched model call for all chapters — often several minutes with no intermediate database updates.";
    } else if (effKey === "auto_scene_coverage") {
      next.detail = "Extra preview stills/clips to match narration length — can take several minutes per scene.";
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
  if (effKey === "auto_narration") return 600_000;
  if (effKey === "auto_scene_coverage" || effKey === "auto_images" || effKey === "auto_videos") return 600_000;
  if (effKey === "auto_rough_cut" || effKey === "auto_final_cut") return 1_800_000;
  return 180_000;
}

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
  thumbnail: {
    title: "Thumbnail & YouTube copy (text + image)",
    body: "Generates title, description, and a 16:9 still via your configured image provider. Slow image APIs or missing keys can stall this step.",
  },
  opening_hook: {
    title: "The Hook (text model)",
    body: "Writes the spoken opening hook from your script and research. Same LLM connectivity rules as other text steps.",
  },
  outro: {
    title: "Subscribe outro (text model + scene)",
    body: "Appends an optional last scene with subscribe CTA narration. Skipped when outro is disabled on the project.",
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
  auto_scene_coverage: {
    title: "Extra media pass (coverage stills)",
    body: "Adds preview-tier stills or clips so each scene has enough visuals for its narration length. ComfyUI / Flux can take several minutes per image with no run update between scenes — per-scene progress events reset the stall timer when present.",
  },
  auto_images: {
    title: "Scene images (image providers)",
    body: "Hero stills use production quality when configured (e.g. ComfyUI at 32 steps). Each image can take several minutes; per-scene progress heartbeats should advance during sequential passes. Slow or failing provider APIs also look like a stall.",
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
    body: "Rendering can take a long time for long programs; a single ffmpeg pass may run many minutes without updating the automation run. Very long stalls may still indicate a stuck encoder or disk issue — see worker logs.",
  },
  auto_final_cut: {
    title: "Final cut / mux",
    body: "Final mux combines narration, music, and mix. ffmpeg may run a long time (especially for long timelines) before the run record updates again. If it never completes, check worker logs for I/O or encoder errors.",
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
 * Client-side stall signal: no recent `updated_at` on the agent run (and no scenes / auto_narration progress
 * heartbeat when relevant), while not explained by active Studio jobs for the same macro-step.
 */
export function computeAgentRunStallInfo(run, activeProjectJobs, nowMs) {
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
  if (effKey === "auto_narration") {
    hb = Math.max(hb, agentStepProgressHeartbeatMs(run.steps_json, "auto_narration"));
  }
  if (effKey === "auto_scene_coverage") {
    hb = Math.max(hb, agentStepProgressHeartbeatMs(run.steps_json, "auto_scene_coverage"));
  }
  if (effKey === "auto_images") {
    hb = Math.max(hb, agentStepProgressHeartbeatMs(run.steps_json, "auto_images"));
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

function scenesProgressHeartbeatMs(stepsJson) {
  const p = lastScenesProgressEvent(stepsJson);
  if (!p?.at) return 0;
  return safeIsoMs(p.at);
}

function agentStepProgressHeartbeatMs(stepsJson, stepKey) {
  const p = lastAgentStepProgressEvent(stepsJson, stepKey);
  if (!p?.at) return 0;
  return safeIsoMs(p.at);
}

/** Skip ``setRun`` when GET /v1/agent-runs/{id} payload is unchanged (poll otherwise re-renders the whole app). */
export function agentRunPollSnapshot(data) {
  if (!data || typeof data !== "object") return "";
  const steps = Array.isArray(data.steps_json) ? data.steps_json : [];
  const tail = steps.length ? steps[steps.length - 1] : null;
  const ctrl = data.pipeline_control_json;
  const stopReq = ctrl && typeof ctrl === "object" ? Boolean(ctrl.stop_requested) : false;
  return JSON.stringify({
    id: String(data.id ?? ""),
    st: String(data.status ?? ""),
    step: String(data.current_step ?? ""),
    ua: String(data.updated_at ?? ""),
    err: String(data.error_message ?? ""),
    bc: String(data.block_code ?? ""),
    slen: steps.length,
    tail:
      tail && typeof tail === "object"
        ? {
            step: String(tail.step ?? ""),
            status: String(tail.status ?? ""),
            at: String(tail.at ?? tail.ts ?? ""),
          }
        : null,
    sr: stopReq,
  });
}

/** Same payload as Inspector → Automate, for POST /v1/agent-runs with ``project_id``. */
export function buildContinuePipelineOptions(
  pipelineMode,
  autoThrough,
  appConfig,
  forceReplanScenesOnContinue,
  publishToYouTube,
) {
  const publishPatch =
    publishToYouTube &&
    (pipelineMode === "unattended" || (pipelineMode === "auto" && autoThrough === "full_video"))
      ? { publish_to_youtube: true }
      : {};
  return {
    continue_from_existing: true,
    through: pipelineMode === "unattended" ? "full_video" : autoThrough,
    rerun_web_research: false,
    ...(pipelineMode === "unattended" ? { unattended: true } : {}),
    ...(forceReplanScenesOnContinue ? { force_replan_scenes: true } : {}),
    ...(pipelineMode === "unattended" || (pipelineMode === "auto" && autoThrough === "full_video")
      ? {
          ...sceneAutomationMediaPipelineOptions(appConfig),
          narration_granularity: "scene",
          ...pipelineSpeedPatchForOptions(appConfig),
        }
      : {}),
    ...publishPatch,
  };
}
