import { InfoTip } from "../../InfoTip.jsx";
import {
  api,
  apiForm,
  apiBase,
  apiPath,
  apiComfyuiWorkflowTestOutputUrl,
  apiChatterboxVoiceRefContentUrl,
} from "../../../lib/api.js";
import {
  parseJson,
  apiErrorMessage,
  formatUserFacingError,
  humanizeErrorText,
} from "../../../lib/apiHelpers.js";
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
} from "../../../lib/constants.js";
import { formatPipelineStageSummary, PIPELINE_SPEED_SELECT_OPTIONS } from "../../../lib/studioLabels.js";

/** Settings workspace UI — state/handlers passed via `p` from App (props must move with state). */

/** Lazy-loaded Settings sub-panel (SettingsGenerationVisualPanel). */
export default function SettingsGenerationVisualPanel(props) {
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
  );
}
