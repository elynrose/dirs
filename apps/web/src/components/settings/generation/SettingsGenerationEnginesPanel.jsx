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

/** Lazy-loaded Settings sub-panel (SettingsGenerationEnginesPanel). */
export default function SettingsGenerationEnginesPanel(props) {
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
                        <h3 className="settings-section-heading settings-section-heading-static">Engines &amp; timing</h3>
                        <div className="settings-section-body">
                        <p className="subtle">Which engines to use and how long scene clips should run.</p>
                <label htmlFor="cfg-active-text">Text provider</label>
                <select
                  id="cfg-active-text"
                  value={appConfig.active_text_provider || "openai"}
                  onChange={(e) => setAppConfig((p) => ({ ...p, active_text_provider: e.target.value }))}
                >
                  <option value="openai">openai (text)</option>
                  <option value="lm_studio">lm_studio (text, local)</option>
                  <option value="openrouter">openrouter (text)</option>
                  <option value="xai">grok/xai (text)</option>
                  <option value="gemini">gemini (text)</option>
                </select>
                <p className="subtle" style={{ marginTop: -6 }}>
                  <strong>lm_studio</strong> uses the LM Studio block under <strong>API keys</strong> (base URL + model). The OpenAI/LM routing dropdown there applies only when this is set to <strong>openai</strong>.
                </p>
                <label htmlFor="cfg-active-image">Image provider</label>
                <select
                  id="cfg-active-image"
                  value={appConfig.active_image_provider || "fal"}
                  onChange={(e) => setAppConfig((p) => ({ ...p, active_image_provider: e.target.value }))}
                >
                  <option value="fal">fal (image)</option>
                  <option value="comfyui">ComfyUI (stills)</option>
                </select>
                <p className="subtle" style={{ marginTop: -6 }}>
                  Studio scene images use <strong>fal</strong> or <strong>ComfyUI</strong> only. Save settings to persist; unsaved changes still apply for this browser session. Configure ComfyUI under <strong>ComfyUI</strong> below.
                </p>
                <label htmlFor="cfg-active-video">Video provider</label>
                <select
                  id="cfg-active-video"
                  value={appConfig.active_video_provider || "fal"}
                  onChange={(e) => setAppConfig((p) => ({ ...p, active_video_provider: e.target.value }))}
                >
                  <option value="fal">fal (generative video)</option>
                  <option value="comfyui_wan">ComfyUI (video workflow)</option>
                  <option value="local_ffmpeg">local_ffmpeg (still→MP4 encode)</option>
                </select>
                <p className="subtle" style={{ marginTop: -6 }}>
                  Generative clips use <strong>fal</strong> or <strong>comfyui_wan</strong>. <strong>local_ffmpeg</strong> turns existing scene images into MP4s without an external API. ComfyUI video workflow path is under <strong>ComfyUI</strong> below.
                </p>
                <label htmlFor="cfg-active-speech">Speech provider (scene narration TTS)</label>
                <p className="subtle" style={{ marginTop: -4 }}>
                  Default TTS for <strong>Generate narration</strong>. Cloud engines use keys under <strong>API keys</strong>. Local{" "}
                  <strong>Chatterbox</strong> needs a reference clip from Settings → <strong>Voice reference</strong>; Kokoro and Chatterbox need the
                  optional API/worker Python extras and a GPU/CPU where applicable.
                </p>
                <select
                  id="cfg-active-speech"
                  value={speechProviderSettingSelectValue(appConfig.active_speech_provider)}
                  onChange={(e) => setAppConfig((p) => ({ ...p, active_speech_provider: e.target.value }))}
                >
                  <optgroup label="Cloud narration">
                    <option value="openai">OpenAI (tts-1 / gpt-4o-mini-tts)</option>
                    <option value="elevenlabs">ElevenLabs</option>
                    <option value="gemini">Gemini (preview TTS)</option>
                  </optgroup>
                  <optgroup label="Local narration">
                    <option value="kokoro">Kokoro (local TTS)</option>
                    <option value="chatterbox_turbo">Chatterbox turbo (voice clone — use Voice reference)</option>
                    <option value="chatterbox_mtl">Chatterbox multilingual (voice clone — use Voice reference)</option>
                  </optgroup>
                </select>
                {speechProviderSettingSelectValue(appConfig.active_speech_provider) === "kokoro" ? (
                  <>
                    <label htmlFor="cfg-kokoro-voice" style={{ marginTop: 14 }}>
                      Kokoro voice
                    </label>
                    <p className="subtle" style={{ marginTop: -4 }}>
                      Voice tensors download from the Hugging Face repo on first synthesis. Use a language below that matches your script; American vs
                      British English use different G2P pipelines.
                    </p>
                    {(() => {
                      const rawSp = String(appConfig.active_speech_provider || "").trim();
                      const fromPrefix =
                        rawSp.toLowerCase().startsWith("kokoro:") ? rawSp.slice("kokoro:".length).trim() : "";
                      const curVoice =
                        fromPrefix ||
                        String(appConfig.kokoro_voice || "af_bella").trim() ||
                        "af_bella";
                      const voiceInList = KOKORO_VOICE_OPTIONS.some((x) => x.id === curVoice);
                      return (
                        <select
                          id="cfg-kokoro-voice"
                          value={curVoice}
                          onChange={(e) =>
                            setAppConfig((p) => ({
                              ...p,
                              kokoro_voice: e.target.value,
                              active_speech_provider: "kokoro",
                            }))
                          }
                        >
                          {!voiceInList && curVoice ? (
                            <option value={curVoice}>
                              {curVoice} (saved)
                            </option>
                          ) : null}
                          {KOKORO_VOICE_OPTIONS.map((v) => (
                            <option key={v.id} value={v.id}>
                              {v.label}
                            </option>
                          ))}
                        </select>
                      );
                    })()}
                    <label htmlFor="cfg-kokoro-lang" style={{ marginTop: 12 }}>
                      Kokoro language (G2P)
                    </label>
                    {(() => {
                      const curLang = String(appConfig.kokoro_lang_code || "a").trim().toLowerCase() || "a";
                      const langInList = KOKORO_LANG_OPTIONS.some((x) => x.id === curLang);
                      return (
                        <select
                          id="cfg-kokoro-lang"
                          value={curLang}
                          onChange={(e) => setAppConfig((p) => ({ ...p, kokoro_lang_code: e.target.value }))}
                        >
                          {!langInList ? (
                            <option value={curLang}>
                              {curLang} (saved)
                            </option>
                          ) : null}
                          {KOKORO_LANG_OPTIONS.map((o) => (
                            <option key={o.id} value={o.id}>
                              {o.label}
                            </option>
                          ))}
                        </select>
                      );
                    })()}
                    <label htmlFor="cfg-kokoro-speed" style={{ marginTop: 12 }}>
                      Kokoro speed
                    </label>
                    <p className="subtle" style={{ marginTop: -4 }}>
                      1.0 = default. Requires worker Python extra <code>kokoro</code> + model weights.
                    </p>
                    <input
                      id="cfg-kokoro-speed"
                      type="number"
                      min={0.5}
                      max={2}
                      step={0.05}
                      value={Number(appConfig.kokoro_speed ?? 1)}
                      onChange={(e) => {
                        const v = Math.min(2, Math.max(0.5, Number.parseFloat(e.target.value) || 1));
                        setAppConfig((p) => ({ ...p, kokoro_speed: v }));
                      }}
                    />
                  </>
                ) : null}
                <p className="subtle" style={{ marginTop: 6 }}>
                  Multilingual default language is <code>chatterbox_mtl_language_id</code> in saved settings (or override with{" "}
                  <code>chatterbox_mtl:&lt;lang&gt;</code> in advanced config). Saving this dropdown sets the base engine only (prefix overrides are
                  replaced when you pick a new option).
                </p>
                <label htmlFor="cfg-scene-clip-sec" style={{ marginTop: 14 }}>
                  Scene clip length (seconds)
                </label>
                <p className="subtle" style={{ marginTop: -4 }}>
                  Length for generated scene clips and for still-image beats when a scene doesn’t specify its own duration. Save, then run the next
                  generation to apply.
                </p>
                <select
                  id="cfg-scene-clip-sec"
                  value={Number(appConfig.scene_clip_duration_sec) === 5 ? "5" : "10"}
                  onChange={(e) =>
                    setAppConfig((p) => ({ ...p, scene_clip_duration_sec: Number(e.target.value) }))
                  }
                >
                  <option value="5">5 seconds</option>
                  <option value="10">10 seconds</option>
                </select>
                <label htmlFor="cfg-scene-plan-target" style={{ marginTop: 14 }}>
                  Target scenes per chapter (scripts + scene plan)
                </label>
                <p className="subtle" style={{ marginTop: -4 }}>
                  0 = automatic: chapter VO is one flowing script; scene count follows script length and clip length above. Set 1–48 to require exactly
                  that many paragraph-separated beats in each generated chapter script (validated after generation), and at least that many scenes when
                  planning. Save, then Generate chapters again for new scripts. Scene planning still allows more scenes when the model needs it (up to 48).
                  Not honored when agent fast mode skips the storyboard LLM.
                </p>
                <input
                  id="cfg-scene-plan-target"
                  type="number"
                  min={0}
                  max={48}
                  step={1}
                  value={Number(appConfig.scene_plan_target_scenes_per_chapter ?? 0)}
                  onChange={(e) =>
                    setAppConfig((p) => ({
                      ...p,
                      scene_plan_target_scenes_per_chapter: Number(e.target.value),
                    }))
                  }
                />
                <label htmlFor="cfg-chapter-title-card-sec" style={{ marginTop: 14 }}>
                  Chapter title cards (rough / final export)
                </label>
                <p className="subtle" style={{ marginTop: -4 }}>
                  Seconds of black full-screen title before each chapter’s first clip in the picture edit, with matching silent audio in the final mix. Set to 0
                  to disable. After changing, save settings and re-run rough cut and final cut. Fine-cut overlays with fixed timestamps may need retiming if you
                  enable this.
                </p>
                <input
                  id="cfg-chapter-title-card-sec"
                  type="number"
                  min={0}
                  max={30}
                  step={0.5}
                  value={Number(appConfig.export_chapter_title_card_sec ?? 0)}
                  onChange={(e) =>
                    setAppConfig((p) => ({
                      ...p,
                      export_chapter_title_card_sec: Number(e.target.value),
                    }))
                  }
                />
                <label style={{ display: "flex", gap: 10, alignItems: "flex-start", cursor: "pointer", marginTop: 14 }}>
                  <input
                    type="checkbox"
                    checked={appConfig.scene_precompile_enabled !== false}
                    onChange={(e) =>
                      setAppConfig((p) => ({ ...p, scene_precompile_enabled: e.target.checked }))
                    }
                  />
                  <span style={{ fontSize: "0.88rem", lineHeight: 1.45 }}>
                    <strong>Background scene precompile</strong> — after each scene image or video succeeds, encode a
                    timeline-ready clip in the background so rough and final cuts are faster. Uses extra disk under{" "}
                    <code>precompiled/</code> until you download the final MP4. Turn off on slower machines or to reduce
                    background CPU use.
                  </span>
                </label>
                        </div>
                      </div>
  );
}
