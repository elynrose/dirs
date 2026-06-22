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
import { activeOAuthRedirectUri, oauthRedirectUriForBase } from "../../lib/oauthRedirectUri.js";

/** Settings workspace UI — state/handlers passed via `p` from App (props must move with state). */

/** Lazy-loaded Settings sub-panel (SettingsIntegrationsPanel). */
export default function SettingsIntegrationsPanel(props) {
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
                    <>
                    <div className="settings-section" style={{ marginBottom: 14 }}>
                      <div className="settings-section-body">
                        <span className="settings-section-heading" style={{ display: "flex", alignItems: "center", gap: 6, marginBottom: 8 }}>
                          Provider connection tests
                          <InfoTip>Queues a short <code>adapter_smoke</code> job on the server (Celery) using your saved workspace keys. Requires the API and a worker; results appear as toasts.</InfoTip>
                        </span>
                        <div className="action-row" style={{ flexWrap: "wrap", gap: 8, marginTop: 8 }}>
                          {[
                            { provider: "openai", label: "OpenAI" },
                            { provider: "lm_studio", label: "LM Studio" },
                            { provider: "openrouter", label: "OpenRouter" },
                            { provider: "fal", label: "FAL" },
                            { provider: "gemini", label: "Gemini" },
                            { provider: "google", label: "Google" },
                            { provider: "comfyui", label: "ComfyUI" },
                          ].map(({ provider, label }) => (
                            <button
                              key={provider}
                              type="button"
                              className="secondary"
                              disabled={adapterSmokePollActive || settingsBusy}
                              onClick={() => void runAdapterSmokeTest(provider, label)}
                            >
                              Test {label}
                            </button>
                          ))}
                        </div>
                      </div>
                    </div>
                    <details className="settings-section" defaultOpen>
                      <summary className="settings-section-summary">
                        <span className="settings-section-heading">Telegram bot</span>
                      </summary>
                      <div className="settings-section-body">
                        {telegramPlanLocked ? (
                          <p className="subtle" style={{ marginBottom: 12 }}>
                            Telegram is not included in your workspace plan. Open <strong>Account</strong> to review access.
                          </p>
                        ) : null}
                        <p className="subtle">
                          Create the bot in Telegram (BotFather). Directely stores credentials and handles the same{" "}
                          <strong>Chat Studio</strong> flow in Telegram; send <strong>RUN</strong> alone when you are ready
                          to queue the full pipeline. Full setup for self-hosted installs is in{" "}
                          <code>INSTALLATION.md</code> §9 in the repo.
                        </p>
                        <ol
                          className="subtle"
                          style={{
                            marginTop: 10,
                            marginBottom: 12,
                            paddingLeft: 22,
                            lineHeight: 1.55,
                            fontSize: "0.95rem",
                          }}
                        >
                          <li>
                            Paste <strong>Bot token</strong> and <strong>Chat ID</strong> below. Use <strong>Generate</strong>{" "}
                            for the webhook secret (or paste your own).
                          </li>
                          <li>
                            Click <strong>Save settings</strong> in the main Settings actions so the API can read them.
                          </li>
                          <li>
                            <strong>Register the webhook with Telegram</strong> (required — Telegram does not call Directely
                            until you do). On the server, from the repo root, with the same token and secret as above:{" "}
                            <code>./scripts/telegram-set-webhook.sh {telegramWebhookPublicOrigin || "https://YOUR_PUBLIC_HOST"}</code>
                            {telegramWebhookPublicOrigin ? (
                              <>
                                {" "}
                                or use the curl block below. Re-run after you change the secret or URL.
                              </>
                            ) : (
                              <>
                                {" "}
                                (set <code>TELEGRAM_BOT_TOKEN</code> and <code>TELEGRAM_WEBHOOK_SECRET</code> in the shell
                                first), or use curl below. Re-run after you change the secret or URL.
                              </>
                            )}
                          </li>
                          <li>
                            Use <strong>Test Telegram connection</strong> — it verifies the bot and shows whether Telegram has
                            a webhook URL registered.
                          </li>
                        </ol>
                        <p className="subtle" style={{ marginTop: 4 }}>
                          Webhook path:{" "}
                          <code>{apiPath("/v1/integrations/telegram/webhook")}</code> — register with Telegram{" "}
                          <code>setWebhook</code>. The <strong>webhook secret</strong> must match <code>secret_token</code>.
                        </p>
                        {telegramWebhookPublicUrl ? (
                          <p className="subtle" style={{ marginTop: 8, fontSize: "0.92rem" }}>
                            <strong>This site (HTTPS):</strong> webhook <code>url</code> should be{" "}
                            <code style={{ wordBreak: "break-all" }}>{telegramWebhookPublicUrl}</code> (nginx proxies{" "}
                            <code>/v1/</code> to the API).
                          </p>
                        ) : null}
                        {telegramWebhookPublicUrl && telegramWebhookPublicOrigin ? (
                          <details className="subtle" style={{ marginTop: 10, marginBottom: 12 }}>
                            <summary style={{ cursor: "pointer", fontWeight: 600 }}>
                              Server: repo script or curl (same host as this page)
                            </summary>
                            <p style={{ marginTop: 8, fontSize: "0.88rem", lineHeight: 1.5 }}>
                              From the machine that has the repo and your bot credentials (export the same values you saved in
                              Studio):
                            </p>
                            <pre
                              className="mono"
                              style={{
                                fontSize: "0.72rem",
                                overflow: "auto",
                                padding: 10,
                                marginTop: 6,
                                background: "var(--panel-2, #1a1a1e)",
                                borderRadius: 6,
                                lineHeight: 1.4,
                              }}
                            >{`export TELEGRAM_BOT_TOKEN='…'
    export TELEGRAM_WEBHOOK_SECRET='…'
    ./scripts/telegram-set-webhook.sh ${telegramWebhookPublicOrigin}`}</pre>
                            <p style={{ fontSize: "0.88rem", marginTop: 10 }}>Or curl:</p>
                            <pre
                              className="mono"
                              style={{
                                fontSize: "0.72rem",
                                overflow: "auto",
                                padding: 10,
                                marginTop: 6,
                                background: "var(--panel-2, #1a1a1e)",
                                borderRadius: 6,
                                lineHeight: 1.4,
                              }}
                            >
                              {`curl -sS -X POST "https://api.telegram.org/bot<BOT_TOKEN>/setWebhook" \\
      --data-urlencode "url=${telegramWebhookPublicUrl}" \\
      --data-urlencode "secret_token=<same as webhook secret above>"`}
                            </pre>
                          </details>
                        ) : null}
                        <details className="subtle" style={{ marginTop: 10, marginBottom: 12 }}>
                          <summary style={{ cursor: "pointer", fontWeight: 600 }}>
                            Local API: use a tunnel (ngrok, Cloudflare Tunnel, …)
                          </summary>
                          <p style={{ marginTop: 8, fontSize: "0.9rem", lineHeight: 1.5 }}>
                            Telegram cannot call <code>http://127.0.0.1</code>. Run the Directely API on port{" "}
                            <code>8000</code> (or your <code>API_PORT</code>), expose it with a tunnel, then use{" "}
                            <strong>https://YOUR-TUNNEL-HOST</strong>
                            {apiPath("/v1/integrations/telegram/webhook")} as the webhook <code>url</code>. Chat ID must be
                            the same user or group that sends messages to the bot.
                          </p>
                          <p style={{ fontSize: "0.88rem", marginTop: 8 }}>
                            Example <code>setWebhook</code> (replace placeholders; keep <code>secret_token</code> identical to
                            the webhook secret field):
                          </p>
                          <pre
                            className="mono"
                            style={{
                              fontSize: "0.72rem",
                              overflow: "auto",
                              padding: 10,
                              marginTop: 6,
                              background: "var(--panel-2, #1a1a1e)",
                              borderRadius: 6,
                              lineHeight: 1.4,
                            }}
                          >
                            {`curl -sS -X POST "https://api.telegram.org/bot<BOT_TOKEN>/setWebhook" \\
      --data-urlencode "url=https://YOUR_TUNNEL_HOST${apiPath("/v1/integrations/telegram/webhook")}" \\
      --data-urlencode "secret_token=<same as webhook secret above>"`}
                          </pre>
                        </details>
                        <label htmlFor="cfg-telegram-token">Bot token</label>
                        {credKeyNote("telegram_bot_token")}
                        <input
                          id="cfg-telegram-token"
                          type="password"
                          autoComplete="off"
                          placeholder="123456:ABC…"
                          disabled={telegramPlanLocked}
                          value={appConfig.telegram_bot_token || ""}
                          onChange={(e) => setAppConfig((p) => ({ ...p, telegram_bot_token: e.target.value }))}
                        />
                        <label htmlFor="cfg-telegram-chat" style={{ marginTop: 12 }}>
                          Chat ID (your user or group id)
                        </label>
                        <input
                          id="cfg-telegram-chat"
                          placeholder="e.g. 123456789 or -100…"
                          disabled={telegramPlanLocked}
                          value={appConfig.telegram_chat_id || ""}
                          onChange={(e) => setAppConfig((p) => ({ ...p, telegram_chat_id: e.target.value }))}
                        />
                        <label htmlFor="cfg-telegram-webhook-secret" style={{ marginTop: 12 }}>
                          Webhook secret (must match Telegram <code>secret_token</code>)
                        </label>
                        {credKeyNote("telegram_webhook_secret")}
                        <div style={{ display: "flex", flexWrap: "wrap", gap: 8, alignItems: "stretch", marginBottom: 6 }}>
                          <input
                            id="cfg-telegram-webhook-secret"
                            type="password"
                            autoComplete="off"
                            disabled={telegramPlanLocked}
                            value={appConfig.telegram_webhook_secret || ""}
                            onChange={(e) => setAppConfig((p) => ({ ...p, telegram_webhook_secret: e.target.value }))}
                            style={{ flex: "1 1 200px", minWidth: 0 }}
                          />
                          <button
                            type="button"
                            className="secondary"
                            disabled={telegramPlanLocked}
                            title="Fills in a random 64-character hex string — then Save settings and use the same value in setWebhook"
                            style={{ flex: "0 0 auto", whiteSpace: "nowrap" }}
                            onClick={() => {
                              try {
                                const buf = new Uint8Array(32);
                                crypto.getRandomValues(buf);
                                const hex = Array.from(buf, (b) => b.toString(16).padStart(2, "0")).join("");
                                setAppConfig((p) => ({ ...p, telegram_webhook_secret: hex }));
                                showToast(
                                  "Webhook secret filled in. Click Save settings below, then run setWebhook with the same secret_token.",
                                  { type: "success", durationMs: 9000 },
                                );
                              } catch {
                                showToast("Could not generate a secret in this browser.", { type: "error" });
                              }
                            }}
                          >
                            Generate
                          </button>
                        </div>
                        <p className="subtle" style={{ fontSize: "0.82rem", marginTop: 0, marginBottom: 0 }}>
                          Use the same value for Telegram <code>setWebhook</code> <code>secret_token</code> (see curl above).
                        </p>
                        <label style={{ display: "flex", gap: 10, alignItems: "flex-start", cursor: "pointer", marginTop: 14 }}>
                          <input
                            type="checkbox"
                            disabled={telegramPlanLocked}
                            checked={Boolean(appConfig.telegram_notify_pipeline_failures)}
                            onChange={(e) =>
                              setAppConfig((p) => ({ ...p, telegram_notify_pipeline_failures: e.target.checked }))
                            }
                          />
                          <span style={{ fontSize: "0.88rem", lineHeight: 1.45 }}>
                            <strong>Notify on Telegram</strong> when a pipeline run fails, is cancelled, or stops (blocked).
                            Messages can include <strong>Open Studio</strong> and <strong>Retry</strong> when{" "}
                            <strong>Public Studio URL</strong> is set below.
                          </span>
                        </label>
                        <div className="action-row" style={{ marginTop: 12 }}>
                          <button
                            type="button"
                            className="secondary"
                            disabled={telegramPlanLocked || telegramTestLoading || settingsBusy}
                            onClick={() => void runTelegramConnectionTest()}
                          >
                            {telegramTestLoading ? "Testing…" : "Test Telegram connection"}
                          </button>
                        </div>
                      </div>
                    </details>
                    <details className="settings-section" defaultOpen>
                      <summary className="settings-section-summary">
                        <span className="settings-section-heading">YouTube &amp; export links</span>
                      </summary>
                      <div className="settings-section-body">
                        {(() => {
                          const activeRedirect =
                            activeOAuthRedirectUri(appConfig, typeof window !== "undefined" ? window.location.hostname : "") ||
                            oauthRedirectUriForBase(appConfig.public_api_base_url) ||
                            "(incoming request host)";
                          const localRedirect = oauthRedirectUriForBase(appConfig.local_api_base_url);
                          const publicRedirect = oauthRedirectUriForBase(appConfig.public_api_base_url);
                          return (
                            <>
                        <p className="subtle" style={{ marginTop: -4 }}>
                          OAuth uses Google&apos;s consent screen. Register the <strong>active</strong> redirect URI in
                          Google Cloud Console (add both local and public if you switch environments):
                        </p>
                        <p className="subtle" style={{ fontSize: "0.85rem" }}>
                          Active now: <code>{activeRedirect}</code>
                        </p>
                        {localRedirect ? (
                          <p className="subtle" style={{ fontSize: "0.85rem" }}>
                            Local: <code>{localRedirect}</code>
                          </p>
                        ) : null}
                        {publicRedirect ? (
                          <p className="subtle" style={{ fontSize: "0.85rem" }}>
                            Public: <code>{publicRedirect}</code>
                          </p>
                        ) : null}
                            </>
                          );
                        })()}
                        <label htmlFor="cfg-local-api-base">LOCAL_API_BASE_URL (loopback API, no trailing slash)</label>
                        <input
                          id="cfg-local-api-base"
                          placeholder="http://127.0.0.1:8000"
                          value={appConfig.local_api_base_url || ""}
                          onChange={(e) => setAppConfig((p) => ({ ...p, local_api_base_url: e.target.value }))}
                        />
                        <label htmlFor="cfg-public-api-base" style={{ marginTop: 12 }}>
                          PUBLIC_API_BASE_URL (production API as Google reaches it, no trailing slash)
                        </label>
                        <input
                          id="cfg-public-api-base"
                          placeholder="https://directely.com"
                          value={appConfig.public_api_base_url || ""}
                          onChange={(e) => setAppConfig((p) => ({ ...p, public_api_base_url: e.target.value }))}
                        />
                        <label htmlFor="cfg-oauth-redirect-base" style={{ marginTop: 12 }}>
                          OAUTH_REDIRECT_BASE
                        </label>
                        <select
                          id="cfg-oauth-redirect-base"
                          value={appConfig.oauth_redirect_base || "auto"}
                          onChange={(e) => setAppConfig((p) => ({ ...p, oauth_redirect_base: e.target.value }))}
                        >
                          <option value="auto">auto — loopback → local, else public</option>
                          <option value="local">local — always LOCAL_API_BASE_URL</option>
                          <option value="public">public — always PUBLIC_API_BASE_URL</option>
                          <option value="request">request — incoming API host only</option>
                        </select>
                        <label htmlFor="cfg-director-public-url" style={{ marginTop: 12 }}>
                          DIRECTOR_PUBLIC_APP_URL (Studio in the browser — for Telegram deep links)
                        </label>
                        <input
                          id="cfg-director-public-url"
                          placeholder="https://studio.yourdomain.com"
                          value={appConfig.director_public_app_url || ""}
                          onChange={(e) => setAppConfig((p) => ({ ...p, director_public_app_url: e.target.value }))}
                        />
                        <label htmlFor="cfg-youtube-client-id" style={{ marginTop: 12 }}>
                          YouTube OAuth client ID
                        </label>
                        <input
                          id="cfg-youtube-client-id"
                          value={appConfig.youtube_client_id || ""}
                          onChange={(e) => setAppConfig((p) => ({ ...p, youtube_client_id: e.target.value }))}
                        />
                        <label htmlFor="cfg-youtube-client-secret" style={{ marginTop: 12 }}>
                          YouTube OAuth client secret
                        </label>
                        {credKeyNote("youtube_client_secret")}
                        <input
                          id="cfg-youtube-client-secret"
                          type="password"
                          autoComplete="off"
                          value={appConfig.youtube_client_secret || ""}
                          onChange={(e) => setAppConfig((p) => ({ ...p, youtube_client_secret: e.target.value }))}
                        />
                        <p className="subtle" style={{ fontSize: "0.85rem", marginTop: 8 }}>
                          After saving client id + secret, open Google consent (stores refresh token in workspace settings).
                        </p>
                        <div className="action-row" style={{ marginTop: 8, flexWrap: "wrap", gap: 8 }}>
                          <button
                            type="button"
                            className="secondary"
                            disabled={settingsBusy}
                            onClick={async () => {
                              setError("");
                              try {
                                const r = await api("/v1/integrations/youtube/auth-url");
                                const b = await parseJson(r);
                                if (!r.ok) throw new Error(apiErrorMessage(b));
                                const u = b.data?.authorize_url;
                                if (!u) throw new Error("No authorize_url from API");
                                window.open(u, "_blank", "noopener,noreferrer");
                                showToast("Complete sign-in in the new tab, then save settings if you changed client id/secret.", {
                                  type: "success",
                                  durationMs: 8000,
                                });
                              } catch (e) {
                                setError(formatUserFacingError(e));
                              }
                            }}
                          >
                            Connect YouTube (OAuth)
                          </button>
                          <button
                            type="button"
                            className="secondary"
                            disabled={settingsBusy}
                            onClick={async () => {
                              setError("");
                              try {
                                const r = await api("/v1/integrations/youtube/disconnect", { method: "POST" });
                                const b = await parseJson(r);
                                if (!r.ok) throw new Error(apiErrorMessage(b));
                                await loadAppSettings();
                                showToast("YouTube disconnected for this workspace.", { type: "success" });
                              } catch (e) {
                                setError(formatUserFacingError(e));
                              }
                            }}
                          >
                            Disconnect YouTube
                          </button>
                        </div>
                        <label style={{ display: "flex", gap: 10, alignItems: "flex-start", cursor: "pointer", marginTop: 14 }}>
                          <input
                            type="checkbox"
                            checked={Boolean(appConfig.youtube_auto_upload_after_export)}
                            onChange={(e) =>
                              setAppConfig((p) => ({ ...p, youtube_auto_upload_after_export: e.target.checked }))
                            }
                          />
                          <span style={{ fontSize: "0.88rem", lineHeight: 1.45 }}>
                            <strong>Auto-upload to YouTube</strong> after each successful final cut (uses default privacy
                            below). Requires a connected YouTube account.
                          </span>
                        </label>
                        <label style={{ display: "flex", gap: 10, alignItems: "flex-start", cursor: "pointer", marginTop: 10 }}>
                          <input
                            type="checkbox"
                            checked={Boolean(appConfig.youtube_share_watch_link_in_telegram)}
                            onChange={(e) =>
                              setAppConfig((p) => ({ ...p, youtube_share_watch_link_in_telegram: e.target.checked }))
                            }
                          />
                          <span style={{ fontSize: "0.88rem", lineHeight: 1.45 }}>
                            When a pipeline succeeds, send the <strong>YouTube watch link</strong> on Telegram (if an upload
                            ran for that export).
                          </span>
                        </label>
                        <label htmlFor="cfg-youtube-privacy" style={{ marginTop: 12 }}>
                          Default YouTube privacy
                        </label>
                        <select
                          id="cfg-youtube-privacy"
                          value={appConfig.youtube_default_privacy || "unlisted"}
                          onChange={(e) => setAppConfig((p) => ({ ...p, youtube_default_privacy: e.target.value }))}
                        >
                          <option value="public">public</option>
                          <option value="unlisted">unlisted</option>
                          <option value="private">private</option>
                        </select>
                        <label style={{ display: "flex", gap: 10, alignItems: "flex-start", cursor: "pointer", marginTop: 14 }}>
                          <input
                            type="checkbox"
                            checked={Boolean(appConfig.burn_subtitles_in_final_cut_default)}
                            onChange={(e) =>
                              setAppConfig((p) => ({ ...p, burn_subtitles_in_final_cut_default: e.target.checked }))
                            }
                          />
                          <span style={{ fontSize: "0.88rem", lineHeight: 1.45 }}>
                            <strong>Hands-off / agent runs:</strong> burn project subtitles into the final MP4 when{" "}
                            <code>subtitles.vtt</code> exists (same as the per-export checkbox under Compile video).
                          </span>
                        </label>
                      </div>
                    </details>
                    <details className="settings-section" defaultOpen>
                      <summary className="settings-section-summary">
                        <span className="settings-section-heading">OpenAI SDK — text chat backend</span>
                      </summary>
                      <div className="settings-section-body">
                        <p className="subtle" style={{ marginTop: -4 }}>
                          When <strong>Generation → Text provider</strong> is <strong>openai</strong>, choose whether chat
                          hits OpenAI/Azure (OpenAI section below) or <strong>LM Studio</strong> (next section). If the text
                          provider is <strong>lm_studio</strong>, chat always uses the LM Studio section and this choice is
                          ignored. TTS and image settings always use the OpenAI block (cloud).
                        </p>
                        <label htmlFor="cfg-openai-text-source">Chat backend</label>
                        <select
                          id="cfg-openai-text-source"
                          value={appConfig.openai_compatible_text_source === "lm_studio" ? "lm_studio" : "openai"}
                          onChange={(e) =>
                            setAppConfig((p) => ({
                              ...p,
                              openai_compatible_text_source: e.target.value === "lm_studio" ? "lm_studio" : "openai",
                            }))
                          }
                        >
                          <option value="openai">OpenAI cloud / Azure (OpenAI section)</option>
                          <option value="lm_studio">LM Studio (local — LM Studio section)</option>
                        </select>
                      </div>
                    </details>
                    <details className="settings-section" defaultOpen>
                      <summary className="settings-section-summary">
                        <span className="settings-section-heading">OpenAI</span>
                      </summary>
                      <div className="settings-section-body">
                <p className="subtle">
                  API key, optional custom API base (Azure or proxy), text model for cloud, image model, and TTS. Used for
                  chat only when <strong>OpenAI SDK — text chat backend</strong> is set to OpenAI cloud / Azure.
                </p>
                <label htmlFor="cfg-openai">OPENAI_API_KEY</label>
                {credKeyNote("openai_api_key")}
                <input
                  id="cfg-openai"
                  value={appConfig.openai_api_key || ""}
                  onChange={(e) => setAppConfig((p) => ({ ...p, openai_api_key: e.target.value }))}
                />
                <label htmlFor="cfg-openai-base-url" style={{ marginTop: 12 }}>
                  OPENAI_API_BASE_URL (optional — Azure OpenAI or OpenAI-compatible corporate endpoint)
                </label>
                <p className="subtle" style={{ marginTop: -4 }}>
                  Not for LM Studio — use the LM Studio section. No trailing <code>/v1</code>; the API appends it. Empty =
                  official OpenAI.
                </p>
                <input
                  id="cfg-openai-base-url"
                  placeholder="https://… or empty"
                  value={appConfig.openai_api_base_url || ""}
                  onChange={(e) => setAppConfig((p) => ({ ...p, openai_api_base_url: e.target.value }))}
                />
                <label htmlFor="cfg-openai-text-model">OPENAI_TEXT_MODEL (cloud / Azure chat)</label>
                <input
                  id="cfg-openai-text-model"
                  value={appConfig.openai_smoke_model || "gpt-4o-mini"}
                  onChange={(e) => setAppConfig((p) => ({ ...p, openai_smoke_model: e.target.value }))}
                />
                <label htmlFor="cfg-openai-image-model">OPENAI_IMAGE_MODEL</label>
                <input
                  id="cfg-openai-image-model"
                  value={appConfig.openai_image_model || "gpt-image-1"}
                  onChange={(e) => setAppConfig((p) => ({ ...p, openai_image_model: e.target.value }))}
                />
                <label htmlFor="cfg-tts">OPENAI_TTS_MODEL</label>
                <input
                  id="cfg-tts"
                  value={appConfig.openai_tts_model || "tts-1"}
                  onChange={(e) => setAppConfig((p) => ({ ...p, openai_tts_model: e.target.value }))}
                />
                <label htmlFor="cfg-openai-tts-voice">OPENAI_TTS_VOICE (when speech provider is OpenAI)</label>
                <select
                  id="cfg-openai-tts-voice"
                  value={
                    OPENAI_TTS_VOICE_OPTIONS.includes(String(appConfig.openai_tts_voice || "").toLowerCase())
                      ? String(appConfig.openai_tts_voice || "alloy").toLowerCase()
                      : "alloy"
                  }
                  onChange={(e) => setAppConfig((p) => ({ ...p, openai_tts_voice: e.target.value }))}
                >
                  {OPENAI_TTS_VOICE_OPTIONS.map((v) => (
                    <option key={v} value={v}>
                      {v}
                    </option>
                  ))}
                </select>
                      </div>
                    </details>
    
                    <details className="settings-section">
                      <summary className="settings-section-summary">
                        <span className="settings-section-heading">LM Studio</span>
                      </summary>
                      <div className="settings-section-body">
                        <p className="subtle">
                          Local OpenAI-compatible server. Set <strong>OpenAI SDK — text chat backend</strong> to LM Studio
                          so chat uses these fields. Base URL example: <code>http://127.0.0.1:1234</code> — no trailing{" "}
                          <code>/v1</code>. Model id must match the model loaded in LM Studio; leave API key blank unless
                          your server requires it.
                        </p>
                        <label htmlFor="cfg-lm-studio-base">LM_STUDIO_API_BASE_URL</label>
                        <input
                          id="cfg-lm-studio-base"
                          placeholder="http://host:port"
                          value={appConfig.lm_studio_api_base_url || ""}
                          onChange={(e) => setAppConfig((p) => ({ ...p, lm_studio_api_base_url: e.target.value }))}
                        />
                        <label htmlFor="cfg-lm-studio-key" style={{ marginTop: 12 }}>
                          LM_STUDIO_API_KEY (optional)
                        </label>
                        {credKeyNote("lm_studio_api_key")}
                        <input
                          id="cfg-lm-studio-key"
                          type="password"
                          autoComplete="off"
                          value={appConfig.lm_studio_api_key || ""}
                          onChange={(e) => setAppConfig((p) => ({ ...p, lm_studio_api_key: e.target.value }))}
                        />
                        <label htmlFor="cfg-lm-studio-text-model" style={{ marginTop: 12 }}>
                          LM_STUDIO_TEXT_MODEL
                        </label>
                        <p className="subtle" style={{ marginTop: -4 }}>
                          Empty = fall back to <strong>OPENAI_TEXT_MODEL</strong> in the OpenAI section.
                        </p>
                        <input
                          id="cfg-lm-studio-text-model"
                          placeholder="e.g. qwen3-32b"
                          value={appConfig.lm_studio_text_model || ""}
                          onChange={(e) => setAppConfig((p) => ({ ...p, lm_studio_text_model: e.target.value }))}
                        />
                        <label htmlFor="cfg-openai-local-max-tokens" style={{ marginTop: 12 }}>
                          OPENAI_LOCAL_CHAT_MAX_TOKENS
                        </label>
                        <p className="subtle" style={{ marginTop: -4 }}>
                          Completion budget for local OpenAI-compatible chat (long JSON / Qwen-class models).
                        </p>
                        <input
                          id="cfg-openai-local-max-tokens"
                          type="number"
                          min={512}
                          max={200000}
                          step={256}
                          value={Number(appConfig.openai_local_chat_max_tokens ?? 16384)}
                          onChange={(e) => {
                            const v = Math.max(512, Math.min(200_000, Number.parseInt(e.target.value, 10) || 16384));
                            setAppConfig((p) => ({ ...p, openai_local_chat_max_tokens: v }));
                          }}
                        />
                      </div>
                    </details>
    
                    <details className="settings-section">
                      <summary className="settings-section-summary">
                        <span className="settings-section-heading">OpenRouter (text)</span>
                      </summary>
                      <div className="settings-section-body">
                <label htmlFor="cfg-openrouter-key">OPENROUTER_API_KEY</label>
                {credKeyNote("openrouter_api_key")}
                <input
                  id="cfg-openrouter-key"
                  value={appConfig.openrouter_api_key || ""}
                  onChange={(e) => setAppConfig((p) => ({ ...p, openrouter_api_key: e.target.value }))}
                />
                <label htmlFor="cfg-openrouter-model">OPENROUTER_TEXT_MODEL</label>
                <input
                  id="cfg-openrouter-model"
                  value={appConfig.openrouter_smoke_model || "openai/gpt-4o-mini"}
                  onChange={(e) => setAppConfig((p) => ({ ...p, openrouter_smoke_model: e.target.value }))}
                  placeholder="e.g. openai/gpt-4o-mini, anthropic/claude-3.5-sonnet"
                />
                      </div>
                    </details>
    
                    <details className="settings-section">
                      <summary className="settings-section-summary">
                        <span className="settings-section-heading">ElevenLabs (narration)</span>
                      </summary>
                      <div className="settings-section-body">
                <p className="subtle">
                  Used when speech provider is ElevenLabs. Voices are loaded from your account (saved API key on the server).
                </p>
                <label htmlFor="cfg-elevenlabs-key">ELEVENLABS_API_KEY</label>
                {credKeyNote("elevenlabs_api_key")}
                <input
                  id="cfg-elevenlabs-key"
                  type="password"
                  autoComplete="off"
                  value={appConfig.elevenlabs_api_key || ""}
                  onChange={(e) => setAppConfig((p) => ({ ...p, elevenlabs_api_key: e.target.value }))}
                />
                <div className="action-row" style={{ marginTop: 8 }}>
                  <button type="button" className="secondary" onClick={() => void loadElevenlabsVoices()}>
                    Refresh ElevenLabs voices
                  </button>
                </div>
                {elevenlabsVoicesNote ? <p className="subtle">{elevenlabsVoicesNote}</p> : null}
                <label htmlFor="cfg-elevenlabs-voice">ELEVENLABS_VOICE_ID</label>
                {(() => {
                  const curEl = String(appConfig.elevenlabs_voice_id || "").trim();
                  const elInList = elevenlabsVoices.some((x) => x.id === curEl);
                  return (
                <select
                  id="cfg-elevenlabs-voice"
                  value={curEl}
                  onChange={(e) => setAppConfig((p) => ({ ...p, elevenlabs_voice_id: e.target.value }))}
                >
                  <option value="">— select voice —</option>
                  {!elInList && curEl ? (
                    <option value={curEl}>
                      {curEl} (saved)
                    </option>
                  ) : null}
                  {elevenlabsVoices.map((v) => (
                    <option key={v.id} value={v.id}>
                      {v.label || v.id}
                    </option>
                  ))}
                </select>
                  );
                })()}
                <label htmlFor="cfg-elevenlabs-model">ELEVENLABS_MODEL_ID</label>
                <input
                  id="cfg-elevenlabs-model"
                  value={appConfig.elevenlabs_model_id || "eleven_multilingual_v2"}
                  onChange={(e) => setAppConfig((p) => ({ ...p, elevenlabs_model_id: e.target.value }))}
                  placeholder="eleven_multilingual_v2"
                />
                      </div>
                    </details>
    
                    <details className="settings-section">
                      <summary className="settings-section-summary">
                        <span className="settings-section-heading">Grok / xAI</span>
                      </summary>
                      <div className="settings-section-body">
                <p className="subtle">Use either key name; both are kept in sync for compatibility.</p>
                <label htmlFor="cfg-grok-key">GROK_API_KEY / XAI_API_KEY</label>
                {credKeyNoteXaiGrok()}
                <input
                  id="cfg-grok-key"
                  value={appConfig.grok_api_key || appConfig.xai_api_key || ""}
                  onChange={(e) =>
                    setAppConfig((p) => ({ ...p, grok_api_key: e.target.value, xai_api_key: e.target.value }))
                  }
                />
                <label htmlFor="cfg-xai-model">GROK_TEXT_MODEL</label>
                <input
                  id="cfg-xai-model"
                  value={appConfig.xai_text_model || "grok-2-latest"}
                  onChange={(e) => setAppConfig((p) => ({ ...p, xai_text_model: e.target.value }))}
                />
                <label htmlFor="cfg-grok-image-model">GROK_IMAGE_MODEL</label>
                <input
                  id="cfg-grok-image-model"
                  value={appConfig.grok_image_model || "grok-2-image-1212"}
                  onChange={(e) => setAppConfig((p) => ({ ...p, grok_image_model: e.target.value }))}
                />
                <label htmlFor="cfg-grok-video-model">GROK_VIDEO_MODEL</label>
                <input
                  id="cfg-grok-video-model"
                  value={appConfig.grok_video_model || "grok-2-video"}
                  onChange={(e) => setAppConfig((p) => ({ ...p, grok_video_model: e.target.value }))}
                />
                      </div>
                    </details>
    
                    <details className="settings-section">
                      <summary className="settings-section-summary">
                        <span className="settings-section-heading">Google Gemini</span>
                      </summary>
                      <div className="settings-section-body">
                <p className="subtle">
                  Use an API key from{" "}
                  <a href="https://aistudio.google.com/apikey" target="_blank" rel="noreferrer">
                    Google AI Studio
                  </a>
                  . Text uses Gemini; images use Imagen; video uses Veo (availability varies by account/region).
                </p>
                <label htmlFor="cfg-gemini-key">GEMINI_API_KEY</label>
                {credKeyNote("gemini_api_key")}
                <input
                  id="cfg-gemini-key"
                  type="password"
                  autoComplete="off"
                  value={appConfig.gemini_api_key || ""}
                  onChange={(e) => setAppConfig((p) => ({ ...p, gemini_api_key: e.target.value }))}
                />
                <label htmlFor="cfg-gemini-text-model">GEMINI_TEXT_MODEL</label>
                <input
                  id="cfg-gemini-text-model"
                  value={appConfig.gemini_text_model || "gemini-2.0-flash"}
                  onChange={(e) => setAppConfig((p) => ({ ...p, gemini_text_model: e.target.value }))}
                />
                <label htmlFor="cfg-gemini-image-model">GEMINI_IMAGE_MODEL</label>
                <input
                  id="cfg-gemini-image-model"
                  value={appConfig.gemini_image_model || "imagen-4.0-generate-001"}
                  onChange={(e) => setAppConfig((p) => ({ ...p, gemini_image_model: e.target.value }))}
                />
                <label htmlFor="cfg-gemini-video-model">GEMINI_VIDEO_MODEL</label>
                <input
                  id="cfg-gemini-video-model"
                  value={appConfig.gemini_video_model || "veo-3.1-generate-preview"}
                  onChange={(e) => setAppConfig((p) => ({ ...p, gemini_video_model: e.target.value }))}
                />
                <label htmlFor="cfg-gemini-tts-model">GEMINI_TTS_MODEL (when speech provider is Gemini)</label>
                <input
                  id="cfg-gemini-tts-model"
                  value={appConfig.gemini_tts_model || "gemini-2.5-flash-preview-tts"}
                  onChange={(e) => setAppConfig((p) => ({ ...p, gemini_tts_model: e.target.value }))}
                  placeholder="gemini-2.5-flash-preview-tts"
                />
                <label htmlFor="cfg-gemini-tts-voice">GEMINI_TTS_VOICE (prebuilt Gemini TTS)</label>
                {(() => {
                  const gemRows = geminiTtsVoices.length ? geminiTtsVoices : GEMINI_TTS_VOICE_FALLBACK;
                  const curG = String(appConfig.gemini_tts_voice || "Kore").trim() || "Kore";
                  const gInList = gemRows.some((x) => x.id === curG);
                  return (
                <select
                  id="cfg-gemini-tts-voice"
                  value={curG}
                  onChange={(e) => setAppConfig((p) => ({ ...p, gemini_tts_voice: e.target.value }))}
                >
                  {!gInList ? (
                    <option value={curG}>
                      {curG} (saved)
                    </option>
                  ) : null}
                  {gemRows.map((v) => (
                    <option key={v.id} value={v.id}>
                      {v.label || v.id}
                    </option>
                  ))}
                </select>
                  );
                })()}
                      </div>
                    </details>
    
                    <details className="settings-section">
                      <summary className="settings-section-summary">
                        <span className="settings-section-heading">FAL</span>
                      </summary>
                      <div className="settings-section-body">
                <p className="subtle">
                  Model suggestions are read from <code>data/media_models_catalog.json</code> in the repo (no live HTTP on each page load).{" "}
                  <strong>Sync from fal API</strong> downloads the latest active endpoints once and updates that file. <strong>Image/video generation</strong>{" "}
                  calls <code>fal.run</code> when <code>FAL_KEY</code> is set and the <strong>Celery worker</strong> is running. Saving with an empty key field does not wipe{" "}
                  <code>FAL_KEY</code> from <code>.env</code>.
                </p>
                {falCatalogNote ? <p className="subtle">{falCatalogNote}</p> : null}
                <label htmlFor="cfg-fal">FAL_KEY</label>
                {credKeyNote("fal_key")}
                <input
                  id="cfg-fal"
                  value={appConfig.fal_key || ""}
                  onChange={(e) => setAppConfig((p) => ({ ...p, fal_key: e.target.value }))}
                />
                <div className="action-row" style={{ marginTop: 8 }}>
                  <button type="button" className="secondary" onClick={() => loadFalCatalog({ force: true })}>
                    Reload lists from disk
                  </button>
                  <button type="button" className="secondary" onClick={() => loadFalCatalog({ force: true, sync: true })}>
                    Sync from fal API
                  </button>
                </div>
                <p className="subtle" style={{ marginTop: 6 }}>
                  Sync uses the fal Platform API (<strong>text-to-image</strong> + <strong>image-to-image</strong> merged for image; <strong>text-to-video</strong> +{" "}
                  <strong>image-to-video</strong> merged for video). You can always type any endpoint id manually.
                </p>
                <p className="subtle" style={{ marginTop: 8 }}>
                  <strong>Loaded:</strong> {falImageModels.length} image · {falVideoByKind.t2v.length} text-to-video · {falVideoByKind.i2v.length}{" "}
                  image-to-video (after Reload/Sync). If counts are tiny, click <strong>Sync from fal API</strong> or check the API worker logs.
                </p>
                <p className="subtle" style={{ marginTop: 4 }}>
                  <strong>Browser note:</strong> HTML <code>&lt;datalist&gt;</code> does not show every suggestion in a scrollable list — it filters as you
                  type. Focus the field and type e.g. <code>flux</code> or <code>wan</code> to see matching endpoints; the full set is still loaded above.
                </p>
                <label htmlFor="cfg-fal-model">FAL image model (when image provider is fal)</label>
                <p className="subtle" style={{ marginTop: -4 }}>
                  Type an endpoint id or use datalist suggestions (type to filter). Catalog entries are hints; any valid fal endpoint id works.
                </p>
                <input
                  id="cfg-fal-model"
                  list="fal-image-endpoints-datalist"
                  placeholder="e.g. fal-ai/fast-sdxl"
                  value={appConfig.fal_smoke_model || ""}
                  onChange={(e) => setAppConfig((p) => ({ ...p, fal_smoke_model: e.target.value }))}
                />
                <datalist id="fal-image-endpoints-datalist">
                  {falImageModels.map((m) => (
                    <option key={m.endpoint_id} value={m.endpoint_id} label={`${m.display_name} — ${m.endpoint_id}`} />
                  ))}
                </datalist>
                {falImageModels.length > 0 ? (
                  <details style={{ marginTop: 10 }}>
                    <summary className="subtle">Browse image catalog (scrollable list)</summary>
                    <select
                      aria-label="Pick image endpoint id"
                      className="fal-catalog-browse"
                      size={14}
                      style={{ width: "100%", marginTop: 6 }}
                      value=""
                      onChange={(e) => {
                        const v = e.target.value;
                        if (v) setAppConfig((p) => ({ ...p, fal_smoke_model: v }));
                      }}
                    >
                      <option value="">— select to copy into the field above —</option>
                      {falImageModels.map((m) => (
                        <option key={m.endpoint_id} value={m.endpoint_id}>
                          {m.display_name} — {m.endpoint_id}
                        </option>
                      ))}
                    </select>
                  </details>
                ) : null}
                <label htmlFor="cfg-fal-video-model" style={{ marginTop: 12 }}>
                  FAL video model (when video provider is fal)
                </label>
                <p className="subtle" style={{ marginTop: -4 }}>
                  {selectedFalVideoKind === "i2v" ? (
                    <>
                      <strong>Image-to-video:</strong> animates the scene still. Generate or approve a scene image before running Video in the studio.
                    </>
                  ) : selectedFalVideoKind === "t2v" ? (
                    <>
                      <strong>Text-to-video:</strong> uses narration-driven prompt only; no scene still required.
                    </>
                  ) : (
                    <>
                      Choose <strong>text-to-video</strong> (prompt only) or <strong>image-to-video</strong> (needs a scene still). Browse groups below or type
                      an endpoint id.
                    </>
                  )}
                </p>
                <input
                  id="cfg-fal-video-model"
                  list="fal-video-endpoints-datalist"
                  placeholder="e.g. fal-ai/minimax/video-01-live"
                  value={appConfig.fal_video_model || ""}
                  onChange={(e) => setAppConfig((p) => ({ ...p, fal_video_model: e.target.value }))}
                />
                <datalist id="fal-video-endpoints-datalist">
                  {falVideoModels.map((m) => {
                    const tag = falVideoEndpointKind(m) === "i2v" ? "I2V" : "T2V";
                    return (
                      <option
                        key={m.endpoint_id}
                        value={m.endpoint_id}
                        label={`[${tag}] ${m.display_name} — ${m.endpoint_id}`}
                      />
                    );
                  })}
                </datalist>
                {falVideoModels.length > 0 ? (
                  <details style={{ marginTop: 10 }}>
                    <summary className="subtle">Browse video catalog (scrollable list)</summary>
                    <select
                      aria-label="Pick video endpoint id"
                      className="fal-catalog-browse"
                      size={14}
                      style={{ width: "100%", marginTop: 6 }}
                      value=""
                      onChange={(e) => {
                        const v = e.target.value;
                        if (v) setAppConfig((p) => ({ ...p, fal_video_model: v }));
                      }}
                    >
                      <option value="">— select to copy into the field above —</option>
                      {falVideoByKind.t2v.length > 0 ? (
                        <optgroup label="Text-to-video · prompt only">
                          {falVideoByKind.t2v.map((m) => (
                            <option key={m.endpoint_id} value={m.endpoint_id}>
                              {m.display_name} — {m.endpoint_id}
                            </option>
                          ))}
                        </optgroup>
                      ) : null}
                      {falVideoByKind.i2v.length > 0 ? (
                        <optgroup label="Image-to-video · uses scene still">
                          {falVideoByKind.i2v.map((m) => (
                            <option key={m.endpoint_id} value={m.endpoint_id}>
                              {m.display_name} — {m.endpoint_id}
                            </option>
                          ))}
                        </optgroup>
                      ) : null}
                    </select>
                  </details>
                ) : null}
                      </div>
                    </details>
    
                    <details className="settings-section">
                      <summary className="settings-section-summary">
                        <span className="settings-section-heading">ComfyUI</span>
                      </summary>
                      <div className="settings-section-body">
                <p className="subtle">
                  API-format workflow JSON from the ComfyUI app. <strong>oss</strong> = your server; <strong>cloud</strong> ={" "}
                  <a href="https://docs.comfy.org/development/cloud/api-reference" target="_blank" rel="noreferrer">
                    Comfy Cloud
                  </a>{" "}
                  (<code>X-API-Key</code>). Still file → Image provider ComfyUI / auto-still before WAN; video file → <code>comfyui_wan</code>.
                </p>
                <label htmlFor="cfg-comfy-flavor">API flavor</label>
                <select
                  id="cfg-comfy-flavor"
                  value={appConfig.comfyui_api_flavor === "cloud" ? "cloud" : "oss"}
                  onChange={(e) => setAppConfig((p) => ({ ...p, comfyui_api_flavor: e.target.value }))}
                >
                  <option value="oss">OSS (self-hosted)</option>
                  <option value="cloud">Comfy Cloud</option>
                </select>
                <label htmlFor="cfg-comfy-base">Base URL</label>
                <input
                  id="cfg-comfy-base"
                  placeholder="http://127.0.0.1:8188 — cloud: leave empty for cloud.comfy.org"
                  value={appConfig.comfyui_base_url || ""}
                  onChange={(e) => setAppConfig((p) => ({ ...p, comfyui_base_url: e.target.value }))}
                />
                <label htmlFor="cfg-comfy-api-key">API key</label>
                {credKeyNote("comfyui_api_key")}
                <input
                  id="cfg-comfy-api-key"
                  type="password"
                  autoComplete="off"
                  placeholder={appConfig.comfyui_api_flavor === "cloud" ? "Required for cloud (also COMFY_CLOUD_API_KEY in .env)" : "Optional Bearer for OSS proxies"}
                  value={appConfig.comfyui_api_key || ""}
                  onChange={(e) => setAppConfig((p) => ({ ...p, comfyui_api_key: e.target.value }))}
                />
                <label htmlFor="cfg-comfy-workflow">Still workflow JSON path</label>
                <input
                  id="cfg-comfy-workflow"
                  placeholder="absolute or repo-relative, e.g. data/comfyui_workflows/still_api.json"
                  value={appConfig.comfyui_workflow_json_path || ""}
                  onChange={(e) => setAppConfig((p) => ({ ...p, comfyui_workflow_json_path: e.target.value }))}
                />
                <div className="settings-subblock" style={{ marginTop: 10, marginBottom: 12 }}>
                  <p className="subtle" style={{ marginBottom: 8 }}>
                    Upload ComfyUI <strong>API format</strong> JSON (Workflow → Export API). Saved under workspace storage
                    and wired to the path above automatically.
                  </p>
                  {comfyuiWorkflowsErr ? <p className="err">{comfyuiWorkflowsErr}</p> : null}
                  <div className="action-row" style={{ flexWrap: "wrap", gap: 8 }}>
                    <label className="secondary" style={{ display: "inline-flex", alignItems: "center", cursor: "pointer" }}>
                      <span style={{ marginRight: 8 }}>Upload still workflow</span>
                      <input
                        type="file"
                        accept=".json,application/json"
                        disabled={comfyuiWorkflowsBusy}
                        onChange={(e) => {
                          const f = e.target.files?.[0];
                          e.target.value = "";
                          if (f) void uploadComfyuiWorkflowFile("image", f);
                        }}
                      />
                    </label>
                    <label className="secondary" style={{ display: "inline-flex", alignItems: "center", cursor: "pointer" }}>
                      <span style={{ marginRight: 8 }}>Upload video workflow</span>
                      <input
                        type="file"
                        accept=".json,application/json"
                        disabled={comfyuiWorkflowsBusy}
                        onChange={(e) => {
                          const f = e.target.files?.[0];
                          e.target.value = "";
                          if (f) void uploadComfyuiWorkflowFile("video", f);
                        }}
                      />
                    </label>
                    <button
                      type="button"
                      className="secondary"
                      disabled={comfyuiWorkflowsBusy || !comfyuiWorkflows?.roles?.find((r) => r.role === "image")?.has_workflow}
                      onClick={() => void deleteComfyuiWorkflow("image")}
                    >
                      Clear still JSON
                    </button>
                    <button
                      type="button"
                      className="secondary"
                      disabled={comfyuiWorkflowsBusy || !comfyuiWorkflows?.roles?.find((r) => r.role === "video")?.has_workflow}
                      onClick={() => void deleteComfyuiWorkflow("video")}
                    >
                      Clear video JSON
                    </button>
                    <button
                      type="button"
                      className="secondary"
                      disabled={comfyuiWorkflowsBusy}
                      onClick={() => void loadComfyuiWorkflows()}
                    >
                      Refresh workflows
                    </button>
                  </div>
                  {comfyuiWorkflows?.roles?.length ? (
                    <ul className="subtle" style={{ marginTop: 10, paddingLeft: 20, lineHeight: 1.5 }}>
                      {comfyuiWorkflows.roles.map((row) => (
                        <li key={row.role}>
                          <strong>{row.role}</strong>:{" "}
                          {row.has_workflow ? (
                            <>
                              {row.node_count ?? "?"} nodes
                              {row.workflow_env && !row.workflow_env.ok ? (
                                <span className="err"> — env check: {(row.workflow_env.errors || []).join(", ")}</span>
                              ) : row.workflow_env?.ok ? (
                                <span> — env OK</span>
                              ) : null}
                            </>
                          ) : (
                            "no file uploaded"
                          )}
                        </li>
                      ))}
                    </ul>
                  ) : null}
                  <div className="action-row" style={{ flexWrap: "wrap", gap: 8, marginTop: 12 }}>
                    <button
                      type="button"
                      className="secondary"
                      disabled={comfyuiTestBusy || settingsBusy}
                      onClick={() => void runComfyuiWorkflowTest("connection")}
                    >
                      Test connection
                    </button>
                    <button
                      type="button"
                      className="secondary"
                      disabled={comfyuiTestBusy || settingsBusy}
                      onClick={() => void runComfyuiWorkflowTest("image")}
                    >
                      Test still generation
                    </button>
                    <button
                      type="button"
                      className="secondary"
                      disabled={comfyuiTestBusy || settingsBusy}
                      onClick={() => void runComfyuiWorkflowTest("video")}
                    >
                      Test video generation
                    </button>
                  </div>
                  <p className="subtle" style={{ marginTop: 8 }}>
                    Connection checks ComfyUI reachability and workflow node ids. Image/video tests queue a real render on
                    your ComfyUI server (can take minutes). Save settings first so base URL and node overrides apply.
                  </p>
                  {comfyuiWorkflows?.roles?.some((r) => r.has_test_output) ? (
                    <div style={{ marginTop: 12, display: "flex", flexWrap: "wrap", gap: 16, alignItems: "flex-start" }}>
                      {comfyuiWorkflows.roles
                        .filter((r) => r.has_test_output)
                        .map((row) =>
                          row.role === "image" ? (
                            <figure key={row.role} style={{ margin: 0 }}>
                              <figcaption className="subtle">Last still test</figcaption>
                              <img
                                alt="ComfyUI test still"
                                src={apiComfyuiWorkflowTestOutputUrl("image", comfyuiTestOutputBust || "1")}
                                style={{ maxWidth: 280, maxHeight: 200, borderRadius: 6, display: "block" }}
                              />
                            </figure>
                          ) : (
                            <figure key={row.role} style={{ margin: 0 }}>
                              <figcaption className="subtle">Last video test</figcaption>
                              <video
                                controls
                                src={apiComfyuiWorkflowTestOutputUrl("video", comfyuiTestOutputBust || "1")}
                                style={{ maxWidth: 320, maxHeight: 200, borderRadius: 6, display: "block" }}
                              />
                            </figure>
                          ),
                        )}
                    </div>
                  ) : null}
                </div>
                <label htmlFor="cfg-comfy-timeout">Still timeout (seconds)</label>
                <input
                  id="cfg-comfy-timeout"
                  type="number"
                  min={30}
                  max={7200}
                  step={1}
                  value={Number(appConfig.comfyui_timeout_sec) || 900}
                  onChange={(e) =>
                    setAppConfig((p) => ({ ...p, comfyui_timeout_sec: Number(e.target.value) }))
                  }
                />
                <label htmlFor="cfg-comfy-vid-workflow">Video workflow JSON path (comfyui_wan)</label>
                <input
                  id="cfg-comfy-vid-workflow"
                  placeholder="separate file from still workflow"
                  value={appConfig.comfyui_video_workflow_json_path || ""}
                  onChange={(e) => setAppConfig((p) => ({ ...p, comfyui_video_workflow_json_path: e.target.value }))}
                />
                <label htmlFor="cfg-comfy-vid-timeout">Video timeout (seconds)</label>
                <input
                  id="cfg-comfy-vid-timeout"
                  type="number"
                  min={60}
                  max={7200}
                  step={1}
                  value={Number(appConfig.comfyui_video_timeout_sec) || 1800}
                  onChange={(e) =>
                    setAppConfig((p) => ({ ...p, comfyui_video_timeout_sec: Number(e.target.value) }))
                  }
                />
                <label htmlFor="cfg-comfy-vid-load-img">LoadImage node id (i2v)</label>
                <input
                  id="cfg-comfy-vid-load-img"
                  placeholder="when using scene still as input"
                  value={appConfig.comfyui_video_load_image_node_id || ""}
                  onChange={(e) => setAppConfig((p) => ({ ...p, comfyui_video_load_image_node_id: e.target.value }))}
                />
                <label htmlFor="cfg-comfy-vid-use-scene" style={{ display: "flex", alignItems: "center", gap: 8, marginTop: 8 }}>
                  <input
                    id="cfg-comfy-vid-use-scene"
                    type="checkbox"
                    checked={appConfig.comfyui_video_use_scene_image !== false}
                    onChange={(e) =>
                      setAppConfig((p) => ({ ...p, comfyui_video_use_scene_image: e.target.checked }))
                    }
                  />
                  Use scene image for video (image-to-video)
                </label>
                <details style={{ marginTop: 16 }}>
                  <summary className="subtle" style={{ cursor: "pointer", userSelect: "none" }}>
                    Advanced — node overrides, polling, asset labels
                  </summary>
                  <div style={{ marginTop: 12, display: "flex", flexDirection: "column", gap: 10 }}>
                    <label htmlFor="cfg-comfy-poll">Poll interval (seconds)</label>
                    <input
                      id="cfg-comfy-poll"
                      type="number"
                      min={0.2}
                      max={10}
                      step={0.1}
                      value={Number(appConfig.comfyui_poll_interval_sec) || 1}
                      onChange={(e) =>
                        setAppConfig((p) => ({ ...p, comfyui_poll_interval_sec: Number(e.target.value) }))
                      }
                    />
                    <label htmlFor="cfg-comfy-prompt-node">COMFYUI_PROMPT_NODE_ID</label>
                    <input
                      id="cfg-comfy-prompt-node"
                      placeholder="optional — else first CLIPTextEncode"
                      value={appConfig.comfyui_prompt_node_id || ""}
                      onChange={(e) => setAppConfig((p) => ({ ...p, comfyui_prompt_node_id: e.target.value }))}
                    />
                    <label htmlFor="cfg-comfy-prompt-key">COMFYUI_PROMPT_INPUT_KEY</label>
                    <input
                      id="cfg-comfy-prompt-key"
                      placeholder="default text"
                      value={appConfig.comfyui_prompt_input_key || ""}
                      onChange={(e) => setAppConfig((p) => ({ ...p, comfyui_prompt_input_key: e.target.value }))}
                    />
                    <label htmlFor="cfg-comfy-neg-node">COMFYUI_NEGATIVE_NODE_ID</label>
                    <input
                      id="cfg-comfy-neg-node"
                      value={appConfig.comfyui_negative_node_id || ""}
                      onChange={(e) => setAppConfig((p) => ({ ...p, comfyui_negative_node_id: e.target.value }))}
                    />
                    <label htmlFor="cfg-comfy-neg-text">COMFYUI_DEFAULT_NEGATIVE_PROMPT</label>
                    <input
                      id="cfg-comfy-neg-text"
                      value={appConfig.comfyui_default_negative_prompt || ""}
                      onChange={(e) => setAppConfig((p) => ({ ...p, comfyui_default_negative_prompt: e.target.value }))}
                    />
                    <label htmlFor="cfg-comfy-model-label">COMFYUI_MODEL_NAME</label>
                    <input
                      id="cfg-comfy-model-label"
                      placeholder="asset label — default workflow filename"
                      value={appConfig.comfyui_model_name || ""}
                      onChange={(e) => setAppConfig((p) => ({ ...p, comfyui_model_name: e.target.value }))}
                    />
                    <label htmlFor="cfg-comfy-vid-model">COMFYUI_VIDEO_MODEL_NAME</label>
                    <input
                      id="cfg-comfy-vid-model"
                      value={appConfig.comfyui_video_model_name ?? "wan-2.1-comfyui"}
                      onChange={(e) => setAppConfig((p) => ({ ...p, comfyui_video_model_name: e.target.value }))}
                    />
                    <label htmlFor="cfg-comfy-vid-prompt-node">COMFYUI_VIDEO_PROMPT_NODE_ID</label>
                    <input
                      id="cfg-comfy-vid-prompt-node"
                      value={appConfig.comfyui_video_prompt_node_id || ""}
                      onChange={(e) => setAppConfig((p) => ({ ...p, comfyui_video_prompt_node_id: e.target.value }))}
                    />
                    <label htmlFor="cfg-comfy-vid-prompt-key">COMFYUI_VIDEO_PROMPT_INPUT_KEY</label>
                    <input
                      id="cfg-comfy-vid-prompt-key"
                      value={appConfig.comfyui_video_prompt_input_key || ""}
                      onChange={(e) => setAppConfig((p) => ({ ...p, comfyui_video_prompt_input_key: e.target.value }))}
                    />
                    <label htmlFor="cfg-comfy-vid-neg-node">COMFYUI_VIDEO_NEGATIVE_NODE_ID</label>
                    <input
                      id="cfg-comfy-vid-neg-node"
                      value={appConfig.comfyui_video_negative_node_id || ""}
                      onChange={(e) => setAppConfig((p) => ({ ...p, comfyui_video_negative_node_id: e.target.value }))}
                    />
                    <label htmlFor="cfg-comfy-vid-neg-text">COMFYUI_VIDEO_DEFAULT_NEGATIVE_PROMPT</label>
                    <input
                      id="cfg-comfy-vid-neg-text"
                      value={appConfig.comfyui_video_default_negative_prompt || ""}
                      onChange={(e) => setAppConfig((p) => ({ ...p, comfyui_video_default_negative_prompt: e.target.value }))}
                    />
                  </div>
                </details>
                      </div>
                    </details>
    
                    <details className="settings-section">
                      <summary className="settings-section-summary">
                        <span className="settings-section-heading">Research</span>
                      </summary>
                      <div className="settings-section-body">
                <label htmlFor="cfg-tavily">TAVILY_API_KEY</label>
                {credKeyNote("tavily_api_key")}
                <input
                  id="cfg-tavily"
                  value={appConfig.tavily_api_key || ""}
                  onChange={(e) => setAppConfig((p) => ({ ...p, tavily_api_key: e.target.value }))}
                />
                      </div>
                    </details>
    
                    <details className="settings-section">
                      <summary className="settings-section-summary">
                        <span className="settings-section-heading">Pexels stock media</span>
                      </summary>
                      <div className="settings-section-body">
                        <label htmlFor="cfg-pexels">PEXELS_API_KEY</label>
                        {credKeyNote("pexels_api_key")}
                        <input
                          id="cfg-pexels"
                          value={appConfig.pexels_api_key || ""}
                          onChange={(e) => setAppConfig((p) => ({ ...p, pexels_api_key: e.target.value }))}
                          autoComplete="off"
                        />
                        <p className="subtle" style={{ marginTop: 6 }}>
                          Used for <strong>Studio</strong> → scene <strong>Assets</strong> → stock search/import (server-side only). Get a key at{" "}
                          <a href="https://www.pexels.com/api/" target="_blank" rel="noreferrer">
                            pexels.com/api
                          </a>
                          . Empty save does not clear a key already set in <code>.env</code>.
                        </p>
                      </div>
                    </details>
    
                    <details className="settings-section">
                      <summary className="settings-section-summary">
                        <span className="settings-section-heading">Storyblocks stock media</span>
                      </summary>
                      <div className="settings-section-body">
                        <label htmlFor="cfg-sb-pub">STORYBLOCKS_PUBLIC_KEY (APIKEY)</label>
                        {credKeyNote("storyblocks_public_key")}
                        <input
                          id="cfg-sb-pub"
                          value={appConfig.storyblocks_public_key || ""}
                          onChange={(e) => setAppConfig((p) => ({ ...p, storyblocks_public_key: e.target.value }))}
                          autoComplete="off"
                        />
                        <label htmlFor="cfg-sb-priv" style={{ marginTop: 12 }}>
                          STORYBLOCKS_PRIVATE_KEY
                        </label>
                        {credKeyNote("storyblocks_private_key")}
                        <input
                          id="cfg-sb-priv"
                          value={appConfig.storyblocks_private_key || ""}
                          onChange={(e) => setAppConfig((p) => ({ ...p, storyblocks_private_key: e.target.value }))}
                          autoComplete="off"
                        />
                        <label htmlFor="cfg-sb-vbase" className="subtle" style={{ marginTop: 12, fontSize: "0.78rem" }}>
                          Video API base (optional)
                        </label>
                        <input
                          id="cfg-sb-vbase"
                          value={appConfig.storyblocks_video_api_base || ""}
                          placeholder="https://api.videoblocks.com"
                          onChange={(e) => setAppConfig((p) => ({ ...p, storyblocks_video_api_base: e.target.value }))}
                        />
                        <label htmlFor="cfg-sb-ibase" className="subtle" style={{ marginTop: 8, fontSize: "0.78rem" }}>
                          Image API base (optional)
                        </label>
                        <input
                          id="cfg-sb-ibase"
                          value={appConfig.storyblocks_image_api_base || ""}
                          placeholder="https://api.graphicstock.com"
                          onChange={(e) => setAppConfig((p) => ({ ...p, storyblocks_image_api_base: e.target.value }))}
                        />
                        <p className="subtle" style={{ marginTop: 6 }}>
                          Partner HMAC keys for <strong>Studio</strong> → scene <strong>Assets</strong> when Source is Storyblocks (still images via
                          GraphicStock, footage via VideoBlocks). Overview:{" "}
                          <a href="https://www.storyblocks.com/resources/business-solutions/api" target="_blank" rel="noreferrer">
                            storyblocks.com/…/api
                          </a>
                          . Empty save does not clear keys already set in <code>.env</code>.
                        </p>
                      </div>
                    </details>
                    </>
  );
}
