import { useCallback, useMemo } from "react";
import { EditorCardColumn } from "../editor/EditorCard.jsx";
import { InspectorPipelinePanel } from "../editor/InspectorPipelinePanel.jsx";
import { CompiledVideoPreview } from "../editor/CompiledVideoPreview.jsx";
import {
  PublishCoverTabContent,
  PublishHookTabContent,
  PublishOutroTabContent,
  useProjectPublish,
} from "../editor/ProjectPublishPanel.jsx";
import { InfoTip } from "./InfoTip.jsx";
import {
  SkeletonSceneList,
  SkeletonAssetGrid,
  SkeletonMediaCanvas,
} from "./LoadingSkeleton.jsx";
import { SceneWorkflowCard } from "./SceneWorkflowCard.jsx";
import { ExportAttentionTimelineAssetsBlock } from "./ExportAttentionTimelineAssetsBlock.jsx";
import { useStudioEditor } from "../context/StudioEditorContext.jsx";
import {
  api,
  apiAssetContentUrl,
  apiSceneNarrationSubtitlesUrl,
  apiChapterNarrationSubtitlesUrl,
  apiBase,
  viteApiBaseEnvRaw,
  sanitizeStudioUuid,
  downloadEditorExportZip,
} from "../lib/api.js";
import { apiErrorMessage, parseJson, formatUserFacingError } from "../lib/apiHelpers.js";
import { copyTextToClipboard } from "../lib/clipboard.js";
import { EDITOR_CENTER_SCENE_TAB_IDS } from "../editor/EditorLayoutContext.jsx";
import {
  agentRunLocksPipelineControls,
  friendlyPipelineStep,
  friendlyRunStatus,
  friendlyAgentRunStatus,
  friendlyPipelineStepStatus,
  friendlyBlockReason,
  agentStageHeadline,
  pipelineStepActivityIconClass,
  agentPipelineActivityIconClass,
  mergePipelineStepsWithAgentActivity,
} from "../lib/studio/pipelineHelpers.js";
import { chaptersSorted, chapterHumanNumber, bestSceneListThumbAsset, sceneListFallbackThumbKind } from "../lib/studio/sceneHelpers.js";
import {
  RUN_STEP_LABEL,
  AGENT_PROGRESS_ORDER,
  PIPELINE_STEP_TO_RERUN_FROM,
  RESTART_AUTOMATION_STEPS,
  PIPELINE_STEP_ID_TO_AGENT_EFF_KEY,
  PHASE5_TIMELINE_UUID_RE,
} from "../lib/constants.js";
import { formatPipelineStageSummary } from "../lib/studioLabels.js";

/** Editor workspace UI — state/handlers via useStudioEditor() (props must move with state). */
export function StudioEditorView() {
  const {
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
    youtubeConnected,
    youtubeStatusLoading,
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
    workspaceRef
  } = useStudioEditor();

  const onPublishScenesReload = useCallback(async () => {
    if (projectId) await loadChapters(projectId);
    if (chapterId) await loadScenes(chapterId);
  }, [projectId, chapterId, loadChapters, loadScenes]);

  const projectPublish = useProjectPublish({
    projectId,
    busy,
    setBusy,
    setError,
    setMessage,
    idem,
    onScenesReload: onPublishScenesReload,
  });

  const mediaPreviewTabs = useMemo(
    () => [
      { id: "cover", label: "Cover image" },
      { id: "hook", label: "Hook" },
      { id: "scene", label: "Scene media" },
      { id: "outro", label: "Outro" },
      { id: "compiled", label: "Compiled video" },
    ],
    [],
  );

  const mediaPreviewTabId = mediaPreviewTabs.some((t) => t.id === mediaPreviewTab)
    ? mediaPreviewTab
    : "scene";

  return (
        <div
          className="workspace-grid"
          ref={workspaceRef}
          style={{
            "--left-width": `${panelSizes.left}px`,
            "--right-width": `${panelSizes.right}px`,
            "--bottom-height": `${Math.max(120, Number(panelSizes?.bottom) || 240)}px`,
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
                      {projects.map((p) => {
                        const activeAr =
                          p.active_agent_run_id &&
                          ["running", "queued", "paused"].includes(String(p.active_agent_run_status || ""));
                        const canListStart = pipelineMode === "auto" || pipelineMode === "unattended";
                        return (
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
                          <div
                            className="project-row-actions"
                            onClick={(e) => {
                              e.stopPropagation();
                            }}
                          >
                            {activeAr ? (
                              <button
                                type="button"
                                className="project-row-icon-btn project-row-automation project-row-automation--stop"
                                disabled={busy}
                                title="Stop automation for this project"
                                aria-label="Stop automation for this project"
                                onClick={(e) => void stopProjectAgentFromList(p.id, p.active_agent_run_id, e)}
                              >
                                <i className="fa-solid fa-stop" aria-hidden="true" />
                              </button>
                            ) : canListStart ? (
                              <button
                                type="button"
                                className="project-row-icon-btn project-row-automation project-row-automation--start"
                                disabled={busy}
                                title="Queue Automate for this project (same options as Pipeline → Automate)"
                                aria-label="Start automation for this project"
                                onClick={(e) => void startProjectAgentFromList(p.id, e)}
                              >
                                <i className="fa-solid fa-play" aria-hidden="true" />
                              </button>
                            ) : null}
                            <button
                              type="button"
                              className="project-row-icon-btn project-row-delete"
                              onClick={() => deleteProject(p.id)}
                              title="Delete project"
                              aria-label="Delete project"
                            >
                              <i className="fa-solid fa-trash-can" aria-hidden="true" />
                            </button>
                          </div>
                        </div>
                        );
                      })}
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
                    <p className="subtle" style={{ marginTop: 6, fontSize: "0.72rem", lineHeight: 1.45 }}>
                      Moving the sliders auto-saves workspace defaults and, when a <strong>Timeline version ID</strong> is set, patches the open timeline after a short pause (same as <strong>Save mix to timeline</strong>).
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
                        onChange={(e) => {
                          const v = Number(e.target.value);
                          setMixMusicVol(v);
                          schedulePersistStudioMixDefaults(v, mixNarrVol);
                          scheduleDebouncedTimelineMixSave();
                        }}
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
                        onChange={(e) => {
                          const v = Number(e.target.value);
                          setMixNarrVol(v);
                          schedulePersistStudioMixDefaults(mixMusicVol, v);
                          scheduleDebouncedTimelineMixSave();
                        }}
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
                      {mediaPreviewTabs.map((tab) => (
                        <button
                          key={tab.id}
                          type="button"
                          role="tab"
                          id={`media-preview-tab-${tab.id}`}
                          aria-selected={mediaPreviewTabId === tab.id}
                          aria-controls="media-preview-panel"
                          onClick={() => setMediaPreviewTab(tab.id)}
                        >
                          {tab.label}
                        </button>
                      ))}
                    </div>
                    <div
                      id="media-preview-panel"
                      className="media-preview-tabpanel"
                      role="tabpanel"
                      aria-labelledby={`media-preview-tab-${mediaPreviewTabId}`}
                    >
                      {mediaPreviewTabId === "compiled" ? (
                        <CompiledVideoPreview projectId={projectId} timelineVersionId={timelineVersionId} />
                      ) : mediaPreviewTabId === "cover" ? (
                        <PublishCoverTabContent pub={projectPublish} projectId={projectId} busy={busy} />
                      ) : mediaPreviewTabId === "hook" ? (
                        <PublishHookTabContent pub={projectPublish} projectId={projectId} busy={busy} />
                      ) : mediaPreviewTabId === "outro" ? (
                        <PublishOutroTabContent pub={projectPublish} projectId={projectId} busy={busy} />
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
                        const sceneRole =
                          s.prompt_package_json && typeof s.prompt_package_json === "object"
                            ? s.prompt_package_json.scene_role
                            : null;
                        const isOutroScene = sceneRole === "outro";
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
                              <div>
                                {isOutroScene ? (
                                  <span
                                    style={{
                                      display: "inline-block",
                                      marginRight: 6,
                                      padding: "1px 6px",
                                      fontSize: "0.65rem",
                                      fontWeight: 600,
                                      borderRadius: 4,
                                      background: "rgb(99 102 241 / 18%)",
                                      color: "var(--text, inherit)",
                                    }}
                                    title="Subscribe outro"
                                  >
                                    Outro
                                  </span>
                                ) : null}
                                {s.purpose?.slice(0, 64) || s.visual_type}
                              </div>
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
                id: "sceneMediaHub",
                title: "Scene media workspace",
                tabShortTitle: "Assets",
                show: Boolean(projectId),
                info: (
                  <>
                    Generate media, refine the image retry prompt, and manage this scene&apos;s gallery — together in one tab. Select a scene first.
                  </>
                ),
                children: (
                  <>
                    {!projectId ? (
                      <p className="subtle">Open a project to use scene media tools.</p>
                    ) : !selectedScene ? (
                      <p className="subtle">Select a scene in <strong>Scenes</strong> to generate media, edit prompts, and manage assets.</p>
                    ) : (
                      <>
                        <SceneWorkflowCard title="Generate">
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
                      <label className="subtle" style={{ display: "flex", gap: 8, alignItems: "center", margin: "4px 0 4px", fontSize: "0.8rem", cursor: "pointer" }}>
                        <input
                          type="checkbox"
                          checked={excludeCharacterBibleFromPrompts}
                          onChange={(e) => setExcludeCharacterBibleFromPrompts(e.target.checked)}
                        />
                        Exclude character bible from image/video prompts (this session; Image, Video, Retry, batch)
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
                        </SceneWorkflowCard>
                        <SceneWorkflowCard title="Image prompt (retry)">
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
                        </SceneWorkflowCard>
                        <SceneWorkflowCard title="Assets for this scene">
                    <>
                      {sceneAssetsFetchError &&
                      selectedSceneId &&
                      String(sceneAssetsFetchError.sceneId) === String(selectedSceneId) ? (
                        <p className="subtle" role="alert" style={{ margin: "0 0 10px", color: "var(--danger, #f87171)" }}>
                          {sceneAssetsFetchError.message}
                        </p>
                      ) : null}
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
                      <div style={{ marginTop: 14, paddingTop: 12, borderTop: "1px solid var(--border, rgba(255,255,255,0.08))" }}>
                        <div className="subtle" style={{ margin: "0 0 8px", fontSize: "0.82rem" }}>
                          <strong style={{ color: "var(--fg, inherit)" }}>Stock media</strong> — search and add to this scene
                          (API keys on server only).
                        </div>
                        <div
                          className="action-row"
                          style={{ marginBottom: 8, flexWrap: "wrap", gap: 8, alignItems: "center" }}
                        >
                          <label className="subtle" style={{ display: "flex", gap: 6, alignItems: "center", fontSize: "0.8rem" }}>
                            Source
                            <select
                              value={sceneStockLibrary}
                              onChange={(e) => setSceneStockLibrary(e.target.value)}
                              disabled={busy}
                              style={{ fontSize: "0.8rem" }}
                              aria-label="Stock provider"
                            >
                              <option value="pexels">Pexels</option>
                              <option value="storyblocks">Storyblocks</option>
                            </select>
                          </label>
                          <button
                            type="button"
                            className={pexelsStockTab === "photos" ? undefined : "secondary"}
                            disabled={busy}
                            onClick={() => setPexelsStockTab("photos")}
                          >
                            Photos
                          </button>
                          <button
                            type="button"
                            className={pexelsStockTab === "videos" ? undefined : "secondary"}
                            disabled={busy}
                            onClick={() => setPexelsStockTab("videos")}
                          >
                            Videos
                          </button>
                          <input
                            type="search"
                            placeholder={sceneStockLibrary === "storyblocks" ? "Search Storyblocks…" : "Search Pexels…"}
                            value={pexelsSearchQuery}
                            onChange={(e) => setPexelsSearchQuery(e.target.value)}
                            disabled={busy}
                            aria-label="Search stock media"
                            style={{ minWidth: "11em", flex: "1 1 160px", maxWidth: "100%" }}
                          />
                        </div>
                        {pexelsSearchErr ? (
                          <p className="subtle" role="alert" style={{ margin: "0 0 8px", color: "var(--danger, #f87171)" }}>
                            {pexelsSearchErr}
                          </p>
                        ) : null}
                        {pexelsSearchBusy && String(pexelsSearchQuery || "").trim() ? (
                          <p className="subtle" style={{ margin: "0 0 8px" }}>
                            Searching…
                          </p>
                        ) : null}
                        {pexelsSearchResults.length > 0 ? (
                          <div
                            style={{
                              display: "grid",
                              gridTemplateColumns: "repeat(auto-fill, minmax(104px, 1fr))",
                              gap: 8,
                              maxHeight: 240,
                              overflowY: "auto",
                            }}
                          >
                            {pexelsSearchResults.map((row) => {
                              const rk = String(row.kind || "photo");
                              const lib =
                                String(row.provider || (sceneStockLibrary === "storyblocks" ? "storyblocks" : "pexels")).toLowerCase() ===
                                "storyblocks"
                                  ? "storyblocks"
                                  : "pexels";
                              const pid = lib === "storyblocks" ? row.storyblocks_id : row.pexels_id;
                              const importKey = `${lib}:${rk}:${pid}`;
                              const isImporting = pexelsImportKey === importKey;
                              return (
                                <div
                                  key={importKey}
                                  style={{
                                    border: "1px solid var(--border, rgba(255,255,255,0.12))",
                                    borderRadius: 6,
                                    overflow: "hidden",
                                    background: "var(--panel-2, rgba(0,0,0,0.2))",
                                  }}
                                >
                                  {row.thumb_url ? (
                                    rk === "video" ? (
                                      <img
                                        alt=""
                                        src={row.thumb_url}
                                        style={{ width: "100%", aspectRatio: "16/10", objectFit: "cover", display: "block" }}
                                      />
                                    ) : (
                                      <img
                                        alt=""
                                        src={row.thumb_url}
                                        style={{ width: "100%", aspectRatio: "1", objectFit: "cover", display: "block" }}
                                      />
                                    )
                                  ) : (
                                    <div className="subtle" style={{ padding: 10, fontSize: "0.75rem" }}>
                                      No preview
                                    </div>
                                  )}
                                  <button
                                    type="button"
                                    className="secondary"
                                    style={{ width: "100%", fontSize: "0.72rem", padding: "5px 4px", borderRadius: 0 }}
                                    disabled={
                                      busy ||
                                      Boolean(pexelsImportKey) ||
                                      !selectedSceneId ||
                                      Boolean(stockVideoTrimModal)
                                    }
                                    onClick={() => {
                                      if (rk !== "video") {
                                        void importSceneAssetFromStock(lib, "photo", pid, null);
                                        return;
                                      }
                                      const dur =
                                        row.duration_sec != null && row.duration_sec !== ""
                                          ? Number(row.duration_sec)
                                          : NaN;
                                      const needsTrimChoice = !Number.isFinite(dur) || dur > 10;
                                      if (!needsTrimChoice) {
                                        void importSceneAssetFromStock(lib, "video", pid, null);
                                        return;
                                      }
                                      setStockVideoTrimModal({
                                        library: lib,
                                        mediaId: pid,
                                        reportedDurationSec: Number.isFinite(dur) ? dur : null,
                                      });
                                    }}
                                  >
                                    {isImporting ? "Adding…" : "Add to scene"}
                                  </button>
                                </div>
                              );
                            })}
                          </div>
                        ) : null}
                        <p className="subtle" style={{ fontSize: "0.72rem", marginTop: 8, marginBottom: 0, lineHeight: 1.4 }}>
                          <a href="https://www.pexels.com/" target="_blank" rel="noreferrer">
                            Pexels
                          </a>{" "}
                          and{" "}
                          <a href="https://www.storyblocks.com/resources/business-solutions/api" target="_blank" rel="noreferrer">
                            Storyblocks
                          </a>
                          . Configure <code style={{ fontSize: "0.68rem" }}>PEXELS_API_KEY</code> or Storyblocks public/private keys
                          in workspace Settings (or API environment) if search returns &quot;not configured&quot;.
                        </p>
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
                          const generationPrompt = assetGenerationPrompt(a);
                          const canCopyPrompt = Boolean(generationPrompt);
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
                              <button
                                type="button"
                                className="scene-asset-copy-prompt-btn"
                                disabled={!canCopyPrompt}
                                title={
                                  canCopyPrompt
                                    ? "Copy generation prompt"
                                    : "No generation prompt stored for this asset"
                                }
                                aria-label={
                                  canCopyPrompt
                                    ? `Copy ${at || "asset"} generation prompt`
                                    : "No generation prompt to copy"
                                }
                                onClick={(e) => {
                                  e.preventDefault();
                                  e.stopPropagation();
                                  void (async () => {
                                    const ok = await copyTextToClipboard(generationPrompt);
                                    if (ok) {
                                      showToast("Generation prompt copied", { type: "success", durationMs: 2500 });
                                    } else {
                                      showToast("Could not copy prompt", { type: "error", durationMs: 4000 });
                                    }
                                  })();
                                }}
                              >
                                <i className="fa-solid fa-copy" aria-hidden="true" />
                              </button>
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
                        </SceneWorkflowCard>
                      </>
                    )}
                  </>
                ),
              },
              {
                id: "scriptAndVoice",
                title: "Script & narration",
                tabShortTitle: "Script",
                show: Boolean(projectId),
                info: (
                  <>
                    Spoken script, style tools, per-scene VO, and project-wide scene narration queue.
                  </>
                ),
                children: (
                  <>
                    <SceneWorkflowCard title="Narration (audio)">
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
                    </SceneWorkflowCard>
                    {selectedScene ? (
                      <SceneWorkflowCard title="Scene script (VO)">
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
                      <div
                        className="panel"
                        style={{
                          marginTop: 12,
                          padding: 10,
                          background: "var(--panel-elevated, rgba(0,0,0,0.04))",
                        }}
                      >
                        <p className="subtle" style={{ margin: "0 0 8px", fontSize: "0.85rem", lineHeight: 1.45 }}>
                          <strong>Record microphone VO</strong> — alternative to TTS. Press <strong>Stop &amp; save</strong> to upload and replace
                          this scene&apos;s narration audio (same as generated VO for the timeline). Max ~10 minutes. Requires mic permission.
                        </p>
                        <div className="action-row" style={{ flexWrap: "wrap", gap: 8, alignItems: "center" }}>
                          <button
                            type="button"
                            className="secondary"
                            disabled={
                              busy ||
                              sceneVoRecordPhase === "saving" ||
                              sceneVoRecordPhase === "recording" ||
                              !selectedScene
                            }
                            onClick={() => void startSceneVoRecording()}
                          >
                            {sceneVoRecordPhase === "recording" ? "Recording…" : "Start recording"}
                          </button>
                          <button
                            type="button"
                            disabled={busy || sceneVoRecordPhase !== "recording"}
                            onClick={() => stopSceneVoRecording()}
                          >
                            Stop &amp; save
                          </button>
                          {sceneVoRecordPhase === "saving" ? (
                            <span className="subtle" style={{ fontSize: "0.78rem" }}>
                              Encoding &amp; uploading…
                            </span>
                          ) : null}
                        </div>
                      </div>
                    </>
                      </SceneWorkflowCard>
                    ) : (
                      <p className="subtle" style={{ marginTop: 8 }}>
                        Select a scene to edit its script, save, and generate per-scene VO.
                      </p>
                    )}
                  </>
                ),
              },
              {
                id: "retryVideoPrompt",
                title: "Video & motion",
                tabShortTitle: "Motion",
                show: Boolean(projectId),
                info: (
                  <>
                    Motion and camera for generative video (and still→video). Select a scene to edit.
                  </>
                ),
                children: (
                  <>
                    {selectedScene ? (
                      <SceneWorkflowCard title="Motion & video prompt">
                    <>
                      <p className="subtle" style={{ margin: "0 0 6px" }}>
                        Motion and camera for generative video (zoom, pan, angle, pace). Pre-filled from{" "}
                        <code>prompt_package_json.video_prompt</code> when the storyboard set it; edit and queue <strong>Retry video</strong>.{" "}
                        <strong>Local still→video</strong> uses the same text for coarse Ken Burns / pan hints (e.g. &quot;zoom in&quot;, &quot;pan
                        left&quot;).
                      </p>
                      <textarea rows={4} value={retryVideoPrompt} onChange={(e) => setRetryVideoPrompt(e.target.value)} />
                      {includeSpokenDialogueInVideoPrompt ? (
                        <div
                          className="panel"
                          style={{
                            marginTop: 12,
                            padding: 10,
                            background: "var(--panel-elevated, rgba(0,0,0,0.04))",
                          }}
                        >
                          <p className="subtle" style={{ margin: "0 0 6px", fontSize: "0.85rem" }}>
                            <strong>What the main character says</strong> (optional) — saved as{" "}
                            <code>prompt_package_json.video_character_dialogue</code>. Appended to the video prompt as{" "}
                            <code>saying: &quot;…&quot;</code> for models that generate speech in the same pass (e.g. Veo). Leave empty for silent
                            scenes.
                          </p>
                          <textarea
                            rows={2}
                            maxLength={800}
                            value={sceneVideoCharacterDialogueDraft}
                            onChange={(e) => {
                              setSceneVideoCharacterDialogueDraft(e.target.value);
                              setSceneVideoCharacterDialogueDirty(true);
                            }}
                            placeholder='e.g. We need to leave now.'
                            aria-label="Optional main character dialogue for generative video"
                          />
                          <div className="action-row" style={{ marginTop: 8 }}>
                            <button
                              type="button"
                              disabled={busy || !sceneVideoCharacterDialogueDirty}
                              onClick={() => void saveSceneVideoCharacterDialogue()}
                            >
                              Save character dialogue
                            </button>
                          </div>
                        </div>
                      ) : null}
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
                      </SceneWorkflowCard>
                    ) : (
                      <p className="subtle">Select a scene to edit video/motion prompts.</p>
                    )}
                  </>
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
            noNarration,
            setNoNarration,
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
            publishToYouTube,
            setPublishToYouTube,
            youtubeConnected,
            youtubeStatusLoading,
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
            setBusy,
            setError,
            setMessage,
            idem,
            onScenesReload: chapterId ? () => loadScenes(chapterId) : undefined,
          }}
        />

        <section className="panel timeline-panel">
          <h2>Timeline &amp; export</h2>
          {Array.isArray(timelineExportWarnings) && timelineExportWarnings.length > 0 ? (
            <div
              className="panel"
              role="status"
              style={{
                marginBottom: 12,
                padding: "10px 12px",
                border: "1px solid rgb(251 191 36 / 35%)",
                background: "rgb(251 191 36 / 10%)",
                borderRadius: 8,
              }}
            >
              <strong style={{ display: "block", marginBottom: 6, fontSize: "0.85rem" }}>Export notice</strong>
              <ul style={{ margin: 0, paddingLeft: "1.2rem", lineHeight: 1.5, fontSize: "0.82rem" }}>
                {timelineExportWarnings.map((w, i) => (
                  <li key={i}>{w}</li>
                ))}
              </ul>
            </div>
          ) : null}
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
                          checked={includeSpokenDialogueInVideoPrompt}
                          disabled={busy || !projectId}
                          onChange={(e) => void saveIncludeSpokenDialogueInVideoPrompt(e.target.checked)}
                        />
                        <span>
                          <strong>Include spoken dialogue in video prompts</strong>
                          <span className="subtle" style={{ display: "block", marginTop: 4, lineHeight: 1.45 }}>
                            For video models that generate speech in the same pass (e.g. Google Veo). When on, optional per-scene lines under{" "}
                            <strong>Video &amp; motion</strong> are appended to the scene video prompt. Leave scenes blank for silent shots.
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
                      <label htmlFor="cffit" style={{ display: "block", fontSize: "0.85rem", marginBottom: 6 }}>
                        <strong>Stock / Pexels frame fit</strong> (this project)
                      </label>
                      <select
                        id="cffit"
                        value={clipFrameFit}
                        disabled={busy || !projectId}
                        onChange={(e) => void saveClipFrameFit(e.target.value)}
                        style={{ maxWidth: "100%", fontSize: "0.85rem" }}
                      >
                        <option value="center_crop">Center crop — fill frame (edges may be cut)</option>
                        <option value="letterbox">Letterbox — show full image (black bars if aspect differs)</option>
                      </select>
                      <p className="subtle" style={{ marginTop: 8, marginBottom: 0, fontSize: "0.78rem", lineHeight: 1.45 }}>
                        Applies when importing <strong>Pexels</strong> photos or videos into a scene. Timeline rough cut still uses its own scale+pad for mixed clips.
                      </p>
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
                      <button
                        type="button"
                        className="secondary"
                        disabled={busy || !projectId || !timelineVersionId}
                        title="ZIP with media, CapCut draft_content.json, and OpenShot-importable XML"
                        onClick={async () => {
                          if (!projectId || !timelineVersionId) return;
                          setBusy(true);
                          setError("");
                          try {
                            await downloadEditorExportZip(projectId, timelineVersionId, {
                              allowUnapprovedMedia: pipelineMode === "unattended",
                            });
                            showToast("Editor export downloaded (CapCut + OpenShot). See README inside the ZIP.");
                          } catch (e) {
                            setError(formatUserFacingError(e));
                          } finally {
                            setBusy(false);
                          }
                        }}
                      >
                        CapCut / OpenShot
                      </button>
                    </div>
                    <p className="subtle" style={{ marginTop: 8, marginBottom: 6, fontSize: "0.78rem" }}>
                      CapCut / OpenShot downloads a ZIP: copy the CapCut folder into your CapCut projects directory, or import{" "}
                      <code>openshot/directely_fcpxml.xml</code> in OpenShot (File → Import → Final Cut Pro XML).
                    </p>
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
  );
}
