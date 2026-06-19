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

/** Lazy-loaded Settings sub-panel (SettingsGenerationNarrationStylesPanel). */
export default function SettingsGenerationNarrationStylesPanel(props) {
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
  );
}
