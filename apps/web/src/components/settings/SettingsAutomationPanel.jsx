import { InfoTip } from "../InfoTip.jsx";
import {
  api,
  apiForm,
  apiBase,
  apiPath,
  apiComfyuiWorkflowTestOutputUrl,
  apiChatterboxVoiceRefContentUrl,
} from "../../lib/api.js";
import {
  parseJson,
  apiErrorMessage,
  formatUserFacingError,
  humanizeErrorText,
} from "../../lib/apiHelpers.js";
import {
  OPENAI_TTS_VOICE_OPTIONS,
  GEMINI_TTS_VOICE_FALLBACK,
  KOKORO_VOICE_OPTIONS,
  KOKORO_LANG_OPTIONS,
  VISUAL_STYLE_PRESET_FALLBACK,
  agentRunAutoGenerateSceneImages,
  agentRunAutoGenerateSceneVideos,
  agentRunMinSceneImages,
  agentRunMinSceneVideos,
} from "../../lib/constants.js";
import { formatPipelineStageSummary, PIPELINE_SPEED_SELECT_OPTIONS } from "../../lib/studioLabels.js";

/** Settings workspace UI — state/handlers passed via `p` from App (props must move with state). */

/** Lazy-loaded Settings sub-panel (SettingsAutomationPanel). */
export default function SettingsAutomationPanel(props) {
  const p = props.p ?? props;
  const {
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
  } = p;
  return (
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
                <label htmlFor="cfg-pexels-scenes" style={{ marginTop: 12, textTransform: "none", letterSpacing: 0, fontSize: "0.78rem", display: "block" }}>
                  <input
                    id="cfg-pexels-scenes"
                    type="checkbox"
                    checked={Boolean(appConfig.agent_run_use_pexels_for_scenes)}
                    onChange={(e) =>
                      setAppConfig((p) => ({ ...p, agent_run_use_pexels_for_scenes: e.target.checked }))
                    }
                    style={{ width: "auto", marginRight: 8 }}
                  />
                  Full-video automation: import first Pexels match per scene during the auto images / auto videos tail (needs PEXELS_API_KEY on the API)
                </label>
                <div style={{ display: "flex", flexWrap: "wrap", gap: "12px 16px", alignItems: "center", marginTop: 8 }}>
                  <label htmlFor="cfg-pexels-scene-mode" className="subtle" style={{ fontSize: "0.78rem" }}>
                    Pexels stock type
                    <select
                      id="cfg-pexels-scene-mode"
                      value={String(appConfig.agent_run_pexels_scene_media_mode || "photos")}
                      disabled={!appConfig.agent_run_use_pexels_for_scenes}
                      onChange={(e) =>
                        setAppConfig((p) => ({ ...p, agent_run_pexels_scene_media_mode: e.target.value }))
                      }
                      style={{ marginLeft: 8, maxWidth: 200 }}
                    >
                      <option value="photos">Photos only</option>
                      <option value="videos">Videos only</option>
                      <option value="both">Photos then videos if empty</option>
                    </select>
                  </label>
                  <label htmlFor="cfg-pexels-interval" className="subtle" style={{ fontSize: "0.78rem" }}>
                    Seconds between Pexels calls (0–120)
                    <input
                      id="cfg-pexels-interval"
                      type="number"
                      min={0}
                      max={120}
                      step={0.5}
                      disabled={!appConfig.agent_run_use_pexels_for_scenes}
                      value={Number(appConfig.agent_run_pexels_scene_search_interval_sec ?? 2)}
                      onChange={(e) => {
                        const n = Math.min(120, Math.max(0, Number.parseFloat(e.target.value) || 0))
                        setAppConfig((p) => ({ ...p, agent_run_pexels_scene_search_interval_sec: n }))
                      }}
                      style={{ marginLeft: 8, width: "4.5rem" }}
                    />
                  </label>
                </div>
                <p className="subtle" style={{ marginTop: 4, fontSize: "0.74rem" }}>
                  Uses each scene&apos;s stock search terms when present; otherwise purpose or narration. Runs with the media tail, not during scene planning. Imports are best-effort and skipped when a scene already has a Pexels asset.
                </p>
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
                  <label htmlFor="cfg-auto-images-concurrency" style={{ textTransform: "none", letterSpacing: 0, fontSize: "0.78rem" }}>
                    Parallel stills (1–8 scenes at once)
                    <input
                      id="cfg-auto-images-concurrency"
                      type="number"
                      min={1}
                      max={8}
                      value={Math.min(8, Math.max(1, Number(appConfig.agent_run_auto_images_max_concurrency ?? 1)))}
                      disabled={!agentRunAutoGenerateSceneImages(appConfig)}
                      title="Full-video automation tail only. 1 = sequential (safest). 2+ runs multiple scene image jobs at once; respect your image provider rate limits. PostgreSQL is recommended — SQLite can hit lock contention under parallel writes."
                      onChange={(e) => {
                        const n = Math.min(8, Math.max(1, Number.parseInt(e.target.value, 10) || 1));
                        setAppConfig((p) => ({ ...p, agent_run_auto_images_max_concurrency: n }));
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
                <p className="subtle" style={{ marginTop: 6, fontSize: "0.74rem", lineHeight: 1.45 }}>
                  <strong>Parallel stills</strong> speeds up the automation tail when your image API allows concurrent requests. Keep at <strong>1</strong> on SQLite or if you see database lock errors; use{" "}
                  <strong>PostgreSQL</strong> for multi-write concurrency.
                </p>
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
                <label htmlFor="cfg-pipeline-speed" style={{ marginTop: 14, display: "block", textTransform: "none", letterSpacing: 0, fontSize: "0.78rem" }}>
                  Full-video automation preset
                </label>
                <p className="subtle" style={{ marginTop: -4 }}>
                  <strong>Standard</strong> uses the toggles and min counts above. <strong>Demo (fast)</strong> sends a server preset: one still per scene, no auto scene
                  videos (shorter runs for testers). <strong>Production (heavy)</strong> requests two stills and two clips per scene when those types are enabled.
                </p>
                <select
                  id="cfg-pipeline-speed"
                  value={String(appConfig.agent_run_pipeline_speed || "standard")}
                  onChange={(e) => setAppConfig((p) => ({ ...p, agent_run_pipeline_speed: e.target.value }))}
                  style={{ marginTop: 6, maxWidth: 360 }}
                >
                  <option value="standard">Standard (workspace min stills / clips)</option>
                  <option value="demo_fast">Demo (fast) — one still, no auto scene videos</option>
                  <option value="production_heavy">Production (heavy) — two stills, two clips</option>
                </select>
                <label htmlFor="cfg-abort-on-auto-video-failure" style={{ marginTop: 12, textTransform: "none", letterSpacing: 0, fontSize: "0.78rem" }}>
                  <input
                    id="cfg-abort-on-auto-video-failure"
                    type="checkbox"
                    checked={Boolean(appConfig.agent_run_abort_on_auto_video_failure)}
                    onChange={(e) =>
                      setAppConfig((p) => ({ ...p, agent_run_abort_on_auto_video_failure: e.target.checked }))
                    }
                    style={{ width: "auto", marginRight: 8 }}
                  />
                  Abort the whole agent run when automated scene video generation still fails after retries (default off: continue to narration and exports)
                </label>
                <label htmlFor="cfg-auto-scene-coverage-clips" style={{ marginTop: 12, textTransform: "none", letterSpacing: 0, fontSize: "0.78rem" }}>
                  <input
                    id="cfg-auto-scene-coverage-clips"
                    type="checkbox"
                    checked={Boolean(appConfig.agent_run_auto_scene_coverage_clips)}
                    onChange={(e) =>
                      setAppConfig((p) => ({ ...p, agent_run_auto_scene_coverage_clips: e.target.checked }))
                    }
                    style={{ width: "auto", marginRight: 8 }}
                  />
                  Auto / Hands-off: extra scene clips vs narration length (varied angles / B-roll; avoids looping one short clip)
                </label>
                <p className="subtle" style={{ marginTop: -6 }}>
                  When on, the automation tail generates enough image or video segments per scene to cover spoken VO (using your scene clip length), with randomized shot prompts. The timeline uses every approved visual per scene (same idea as{" "}
                  <strong>Use all approved scene media</strong> under Compile). Save settings below.
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
  );
}
