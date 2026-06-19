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

/** Lazy-loaded Settings sub-panel (SettingsStudioPanel). */
export default function SettingsStudioPanel(props) {
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
                  How often the UI refreshes media and character job status. When the project <strong>SSE</strong> stream is connected,
                  media job polling uses a slower effective interval (up to 4× this value, capped at 60s) so updates from the stream are not
                  duplicated by constant HTTP polling — helpful on high-latency links.
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
  );
}
