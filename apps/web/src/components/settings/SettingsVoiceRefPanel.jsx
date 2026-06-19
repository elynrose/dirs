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

/** Lazy-loaded Settings sub-panel (SettingsVoiceRefPanel). */
export default function SettingsVoiceRefPanel(props) {
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
  );
}
