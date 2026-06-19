import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api } from "../../lib/api.js";
import {
  apiErrorMessage,
  formatUserFacingError,
  parseJson,
  summarizeAgentRunFailure,
} from "../../lib/apiHelpers.js";
import {
  DEFAULT_NARRATION_PRESET_ID,
  PIPELINE_RERUN_NEEDS_FULL_VIDEO,
  PIPELINE_STEP_TO_RERUN_FROM,
  RESTART_AUTOMATION_STEPS,
  briefPreferredMediaProvidersFromAppConfig,
  pipelineSpeedPatchForOptions,
  sceneAutomationMediaPipelineOptions,
} from "../../lib/constants.js";
import {
  agentRunPollSnapshot,
  buildContinuePipelineOptions,
  computeAgentRunStallInfo,
  lastAutoImagesProgressEvent,
  lastAutoNarrationProgressEvent,
  lastAutoSceneCoverageProgressEvent,
  lastScenesProgressEvent,
  pipelineStopRequested,
  resolveEffectiveAgentStepKey,
} from "../../lib/studio/pipelineHelpers.js";

/**
 * Agent run state, pipeline mode, refreshRun polling, and pipeline control actions.
 *
 * Cross-domain reloads during ``refreshRun`` / ``startAgentRun`` use ``pipelineCollabRef``
 * (filled by App after timeline + scene hooks exist).
 */
export function useEditorPipelineAgent({
  bootAgentRunId = "",
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
}) {
  const [agentRunId, setAgentRunId] = useState(() => bootAgentRunId);
  const [run, setRun] = useState(null);
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
  const [autoThrough, setAutoThrough] = useState(() => {
    try {
      const v = localStorage.getItem("director_auto_through");
      return v === "critique" || v === "full_video" ? v : "full_video";
    } catch {
      return "full_video";
    }
  });
  const [forceReplanScenesOnContinue, setForceReplanScenesOnContinue] = useState(() => {
    try {
      return localStorage.getItem("director_force_replan_scenes_continue") === "true";
    } catch {
      return false;
    }
  });
  const [publishToYouTube, setPublishToYouTube] = useState(() => {
    try {
      const v = localStorage.getItem("director_publish_to_youtube");
      if (v === "true" || v === "false") return v === "true";
    } catch {
      /* ignore */
    }
    return false;
  });
  const [youtubeConnected, setYoutubeConnected] = useState(false);
  const [youtubeStatusLoading, setYoutubeStatusLoading] = useState(true);
  const [restartAutomationOpen, setRestartAutomationOpen] = useState(false);
  const [restartAutomationForce, setRestartAutomationForce] = useState(() =>
    Object.fromEntries(RESTART_AUTOMATION_STEPS.map((s) => [s.key, true])),
  );
  const [restartAutomationThrough, setRestartAutomationThrough] = useState("full_video");
  const [restartRerunWebResearch, setRestartRerunWebResearch] = useState(false);
  const [agentRunStallTick, setAgentRunStallTick] = useState(0);

  const lastAgentRunHeavySyncRef = useRef(0);
  const lastAgentRunPollSnapshotRef = useRef("");
  const agentRunFailedToastKeyRef = useRef("");

  const resetPipelineAgentSlice = useCallback(() => {
    setAgentRunId("");
    setRun(null);
    lastAgentRunHeavySyncRef.current = 0;
    lastAgentRunPollSnapshotRef.current = "";
    agentRunFailedToastKeyRef.current = "";
  }, []);

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
    try {
      localStorage.setItem("director_publish_to_youtube", publishToYouTube ? "true" : "false");
    } catch {
      /* ignore */
    }
  }, [publishToYouTube]);

  useEffect(() => {
    if (!studioReady) return undefined;
    let cancelled = false;
    (async () => {
      setYoutubeStatusLoading(true);
      try {
        const r = await api("/v1/integrations/youtube/status");
        const body = await parseJson(r);
        if (!cancelled && r.ok) {
          setYoutubeConnected(Boolean(body.data?.connected));
        }
      } catch {
        if (!cancelled) setYoutubeConnected(false);
      } finally {
        if (!cancelled) setYoutubeStatusLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [studioReady]);

  const refreshRun = useCallback(async () => {
    if (!studioReady || !agentRunId) return;
    const collab = pipelineCollabRef.current;
    const r = await api(`/v1/agent-runs/${agentRunId}`);
    const body = await parseJson(r);
    if (!r.ok) return;
    const data = body.data;
    const runSnap = agentRunPollSnapshot(data);
    if (runSnap !== lastAgentRunPollSnapshotRef.current) {
      lastAgentRunPollSnapshotRef.current = runSnap;
      setRun(data);
    }
    const runProjectId = data?.project_id != null ? String(data.project_id) : "";
    if (runProjectId && !String(collab.projectId || "").trim()) {
      collab.setProjectId?.(runProjectId);
    }
    const pid = (runProjectId || String(collab.projectId || "").trim()) || "";

    const st = data?.status;
    const runHot = st === "running" || st === "queued";
    const terminal = ["succeeded", "cancelled", "failed", "blocked"].includes(st);

    const AGENT_RUN_HEAVY_MS = 12_000;
    const now = Date.now();
    const chapterId = collab.chapterId;
    const expandedScene = collab.expandedScene;
    const needHeavyHot =
      runHot &&
      pid &&
      (lastAgentRunHeavySyncRef.current === 0 ||
        now - lastAgentRunHeavySyncRef.current >= AGENT_RUN_HEAVY_MS);

    if (needHeavyHot) {
      lastAgentRunHeavySyncRef.current = now;
      collab.loadProjectCriticReports?.(pid);
      collab.loadChapters?.(pid);
      if (chapterId) {
        collab.loadScenes?.(chapterId);
        collab.loadPhase3Summary?.(chapterId);
        collab.loadChapterNarration?.(chapterId);
      }
      const sid = expandedScene ? String(expandedScene) : "";
      if (sid) {
        collab.loadSceneAssets?.(sid);
        void collab.loadSceneNarrationMeta?.(sid);
      }
    }

    if (terminal && pid) {
      void (async () => {
        const latestTl = await collab.loadPipelineStatus?.(pid);
        if (st === "succeeded" && latestTl) {
          collab.setTimelineVersionId?.(latestTl);
          await collab.refreshPhase5Readiness?.({
            pid,
            reportError: false,
            timelineVersionIdHint: latestTl,
          });
        }
        collab.loadProjectCriticReports?.(pid);
        collab.loadChapters?.(pid);
        if (chapterId) {
          collab.loadScenes?.(chapterId);
          collab.loadPhase3Summary?.(chapterId);
          collab.loadChapterNarration?.(chapterId);
        }
        if (expandedScene) {
          const es = String(expandedScene);
          collab.loadSceneAssets?.(es);
          void collab.loadSceneNarrationMeta?.(es);
        }
      })();
    }
  }, [studioReady, agentRunId, pipelineCollabRef]);

  useEffect(() => {
    lastAgentRunHeavySyncRef.current = 0;
    lastAgentRunPollSnapshotRef.current = "";
  }, [agentRunId, chapterId, expandedScene]);

  useEffect(() => {
    if (!studioReady || !agentRunId || !run || run.status !== "failed") return;
    const key = `${agentRunId}:${run.error_message || ""}`;
    if (agentRunFailedToastKeyRef.current === key) return;
    agentRunFailedToastKeyRef.current = key;
    const msg = summarizeAgentRunFailure(run.error_message || "");
    showToast(`Automation failed — ${msg}`, { type: "error", durationMs: 14000 });
  }, [studioReady, agentRunId, run?.status, run?.error_message, showToast]);

  useEffect(() => {
    if (!studioReady || !agentRunId) return undefined;
    refreshRun();
    const intervalMs = sseConnectedRef.current ? 9_000 : 3_500;
    const id = setInterval(refreshRun, intervalMs);
    return () => clearInterval(id);
  }, [studioReady, agentRunId, refreshRun, sseConnectedRef]);

  const startAgentRun = useCallback(async () => {
    const collab = pipelineCollabRef.current;
    setBusy(true);
    setError("");
    setMessage("");
    setRun(null);
    collab.setChapters?.([]);
    collab.setScenes?.([]);
    collab.setChapterId?.("");
    try {
      const stylePresets = collab.stylePresets;
      const narRefRaw = String(appConfig.default_narration_style_ref || "").trim();
      const narPresetFallback = String(
        appConfig.narration_style_preset ||
          stylePresets?.defaults?.narration_style_preset ||
          DEFAULT_NARRATION_PRESET_ID,
      ).trim();
      const narration_style =
        narRefRaw && (narRefRaw.startsWith("preset:") || narRefRaw.startsWith("user:"))
          ? narRefRaw
          : `preset:${narPresetFallback || DEFAULT_NARRATION_PRESET_ID}`;
      const visId = String(
        appConfig.visual_style_preset ||
          stylePresets?.defaults?.visual_style_preset ||
          "cinematic_documentary",
      ).trim();
      const publishPatch =
        publishToYouTube &&
        (pipelineMode === "unattended" || (pipelineMode === "auto" && autoThrough === "full_video"))
          ? { publish_to_youtube: true }
          : {};
      const pipeline_options =
        pipelineMode === "manual"
          ? { through: "chapters" }
          : pipelineMode === "unattended"
            ? {
                through: "full_video",
                unattended: true,
                narration_granularity: "scene",
                ...sceneAutomationMediaPipelineOptions(appConfig),
                ...pipelineSpeedPatchForOptions(appConfig),
                ...(forceReplanScenesOnContinue ? { force_replan_scenes: true } : {}),
                ...publishPatch,
              }
            : {
                through: autoThrough,
                narration_granularity: "scene",
                ...sceneAutomationMediaPipelineOptions(appConfig),
                ...(autoThrough === "full_video" ? pipelineSpeedPatchForOptions(appConfig) : {}),
                ...(forceReplanScenesOnContinue ? { force_replan_scenes: true } : {}),
                ...publishPatch,
              };
      const r = await api("/v1/agent-runs", {
        method: "POST",
        body: JSON.stringify({
          brief: {
            title: collab.title,
            topic: collab.topic,
            target_runtime_minutes: Number(collab.runtime),
            audience: "general",
            tone: "documentary",
            narration_style,
            visual_style: `preset:${visId || "cinematic_documentary"}`,
            frame_aspect_ratio: collab.frameAspectRatio === "9:16" ? "9:16" : "16:9",
            clip_frame_fit: collab.clipFrameFit === "letterbox" ? "letterbox" : "center_crop",
            no_narration: Boolean(collab.noNarration),
            ...briefPreferredMediaProvidersFromAppConfig(appConfig),
            ...publishPatch,
          },
          pipeline_options,
        }),
      });
      const body = await parseJson(r);
      if (!r.ok) throw new Error(apiErrorMessage(body));
      const ar = body.data?.agent_run;
      const proj = body.data?.project;
      if (proj?.id) collab.setProjectId?.(proj.id);
      collab.loadProjects?.();
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
              ? "Agent run queued — by default Auto stops after the one-time story vs research check (no media tail). Switch Auto target to “Through final video” for one-pass character bible → narration → scene images → cuts, or Automate again later."
              : "Agent run queued — runs through final video after scenes and story check; polling status…",
      );
    } catch (e) {
      setError(formatUserFacingError(e));
    } finally {
      setBusy(false);
    }
  }, [
    appConfig,
    pipelineMode,
    autoThrough,
    forceReplanScenesOnContinue,
    publishToYouTube,
    pipelineCollabRef,
    setBusy,
    setError,
    setMessage,
  ]);

  const continuePipelineAuto = useCallback(async () => {
    const collab = pipelineCollabRef.current;
    const projectId = collab.projectId;
    if (!projectId) return;
    setBusy(true);
    setError("");
    setMessage("");
    try {
      const r = await api("/v1/agent-runs", {
        method: "POST",
        body: JSON.stringify({
          project_id: projectId,
          pipeline_options: buildContinuePipelineOptions(
            pipelineMode,
            autoThrough,
            appConfig,
            forceReplanScenesOnContinue,
            publishToYouTube,
          ),
        }),
      });
      const body = await parseJson(r);
      if (!r.ok) throw new Error(apiErrorMessage(body));
      const ar = body.data?.agent_run;
      if (ar?.id) {
        setAgentRunId(ar.id);
        setRun(ar);
      }
      collab.loadPipelineStatus?.(projectId);
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
  }, [
    pipelineMode,
    autoThrough,
    appConfig,
    forceReplanScenesOnContinue,
    publishToYouTube,
    pipelineCollabRef,
    setBusy,
    setError,
    setMessage,
  ]);

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
    const collab = pipelineCollabRef.current;
    const projectId = collab.projectId;
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
        [
          "auto_characters",
          "auto_images",
          "auto_videos",
          "auto_narration",
          "auto_timeline",
          "auto_rough_cut",
          "auto_final_cut",
        ].includes(k),
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
                  ...pipelineSpeedPatchForOptions(appConfig),
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
      void collab.loadPipelineStatus?.(projectId);
      setMessage(
        "Restart automation queued — checked steps re-run even when already complete; unchecked steps stay as fast-skip when satisfied.",
      );
    } catch (e) {
      setError(formatUserFacingError(e));
    } finally {
      setBusy(false);
    }
  }, [
    restartAutomationForce,
    restartAutomationThrough,
    restartRerunWebResearch,
    pipelineMode,
    appConfig,
    pipelineCollabRef,
    setBusy,
    setError,
    setMessage,
  ]);

  const rerunPipelineFromStep = useCallback(
    async (pipelineStepId) => {
      const collab = pipelineCollabRef.current;
      const projectId = collab.projectId;
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
          "Using full video for this re-run (character bible → narration → scene images, timeline, cuts). Switch Auto target to full video to match next time.",
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
                    ...pipelineSpeedPatchForOptions(appConfig),
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
        void collab.loadPipelineStatus?.(projectId);
        setMessage(`Re-run queued from “${rerunFrom.replace(/_/g, " ")}” — new run id in Run activity.`);
      } catch (e) {
        setError(formatUserFacingError(e));
      } finally {
        setBusy(false);
      }
    },
    [pipelineMode, autoThrough, appConfig, pipelineCollabRef, setBusy, setError, setMessage],
  );

  const pipelineControl = useCallback(
    async (action) => {
      if (!agentRunId) return;
      const collab = pipelineCollabRef.current;
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
        if (collab.projectId) collab.loadPipelineStatus?.(collab.projectId);
        setMessage(`Pipeline: ${action}`);
      } catch (e) {
        setError(formatUserFacingError(e));
      } finally {
        setBusy(false);
      }
    },
    [agentRunId, pipelineCollabRef, setBusy, setError, setMessage],
  );

  const stopProjectAgentFromList = useCallback(
    async (pid, runId, e) => {
      e?.preventDefault?.();
      e?.stopPropagation?.();
      if (!runId) return;
      const collab = pipelineCollabRef.current;
      setBusy(true);
      setError("");
      try {
        const r = await api(`/v1/agent-runs/${encodeURIComponent(runId)}/control`, {
          method: "POST",
          body: JSON.stringify({ action: "stop" }),
        });
        const body = await parseJson(r);
        if (!r.ok) throw new Error(apiErrorMessage(body));
        if (String(agentRunId) === String(runId) && body.data) {
          setRun(body.data);
        }
        await collab.loadProjects?.({ silent: false });
        if (String(collab.projectId) === String(pid)) {
          void collab.loadPipelineStatus?.(pid);
        }
        setMessage("Stop requested — the worker stops after the current pipeline step.");
      } catch (err) {
        setError(formatUserFacingError(err));
      } finally {
        setBusy(false);
      }
    },
    [agentRunId, pipelineCollabRef, setBusy, setError, setMessage],
  );

  const startProjectAgentFromList = useCallback(
    async (pid, e) => {
      e?.preventDefault?.();
      e?.stopPropagation?.();
      if (pipelineMode !== "auto" && pipelineMode !== "unattended") {
        setMessage("Switch Pipeline & agent to Auto or Hands-off to queue automation from the project list.");
        return;
      }
      const collab = pipelineCollabRef.current;
      setBusy(true);
      setError("");
      setMessage("");
      try {
        const r = await api("/v1/agent-runs", {
          method: "POST",
          body: JSON.stringify({
            project_id: pid,
            pipeline_options: buildContinuePipelineOptions(
              pipelineMode,
              autoThrough,
              appConfig,
              forceReplanScenesOnContinue,
              publishToYouTube,
            ),
          }),
        });
        const body = await parseJson(r);
        if (!r.ok) throw new Error(apiErrorMessage(body));
        const ar = body.data?.agent_run;
        if (ar?.id && String(collab.projectId) === String(pid)) {
          setAgentRunId(ar.id);
          setRun(ar);
        }
        await collab.loadProjects?.({ silent: false });
        if (String(collab.projectId) === String(pid)) {
          void collab.loadPipelineStatus?.(pid);
        }
        setMessage("Automation queued for this project.");
      } catch (err) {
        setError(formatUserFacingError(err));
      } finally {
        setBusy(false);
      }
    },
    [
      pipelineMode,
      autoThrough,
      appConfig,
      forceReplanScenesOnContinue,
      publishToYouTube,
      pipelineCollabRef,
      setBusy,
      setError,
      setMessage,
    ],
  );

  const events = useMemo(
    () => (Array.isArray(run?.steps_json) ? run.steps_json : []),
    [run?.steps_json],
  );

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
      thumbnail: "Generating YouTube title, description, and a 16:9 thumbnail still.",
      opening_hook: "Writing the spoken opening hook from your script and research.",
      outro: "Appending the optional subscribe outro as the last scene (skipped when disabled on the project).",
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
      auto_images:
        "Generating hero stills for each scene (production quality when configured). Sequential passes emit per-scene progress; parallel batches can go quiet for several minutes between rounds.",
      auto_scene_coverage:
        "Filling extra preview stills or clips so each scene has enough visuals for its narration length — runs after TTS when coverage is enabled in Settings.",
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
      const narrProg = lastAutoNarrationProgressEvent(run?.steps_json);
      if (
        effKey === "auto_narration" &&
        narrProg &&
        typeof narrProg.scenes_total === "number" &&
        narrProg.scenes_total > 0 &&
        typeof narrProg.scene_index === "number"
      ) {
        base = `${base} Now synthesizing scene ${narrProg.scene_index}/${narrProg.scenes_total}.`;
      }
      const covProg = lastAutoSceneCoverageProgressEvent(run?.steps_json);
      if (
        effKey === "auto_scene_coverage" &&
        covProg &&
        typeof covProg.scenes_total === "number" &&
        covProg.scenes_total > 0 &&
        typeof covProg.scene_index === "number"
      ) {
        base = `${base} Now covering scene ${covProg.scene_index}/${covProg.scenes_total}.`;
      }
      const imgProg = lastAutoImagesProgressEvent(run?.steps_json);
      if (
        effKey === "auto_images" &&
        imgProg &&
        typeof imgProg.scenes_total === "number" &&
        imgProg.scenes_total > 0 &&
        typeof imgProg.scene_index === "number"
      ) {
        base = `${base} Now generating stills for scene ${imgProg.scene_index}/${imgProg.scenes_total}.`;
      }
      return base;
    }
    if (run?.current_step) {
      return runStepGuidance[run.current_step] || "Working on the current step.";
    }
    return null;
  }, [runStoppingUi, run, activeProjectJobs, runStepGuidance]);

  useEffect(() => {
    if (!run || !["running", "queued"].includes(run.status)) return undefined;
    const id = window.setInterval(() => setAgentRunStallTick((x) => x + 1), 10_000);
    return () => window.clearInterval(id);
  }, [run?.id, run?.status]);

  const agentRunStallInfo = useMemo(
    () => computeAgentRunStallInfo(run, activeProjectJobs, Date.now()),
    [run, activeProjectJobs, agentRunStallTick],
  );

  const blocked = run?.status === "blocked";

  return {
    agentRunId,
    agentRunStallInfo,
    autoThrough,
    blocked,
    continuePipelineAuto,
    events,
    forceReplanScenesOnContinue,
    openRestartAutomationModal,
    pipelineControl,
    pipelineMode,
    publishToYouTube,
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
    setPipelineMode,
    setPublishToYouTube,
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
  };
}
