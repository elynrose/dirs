import { Suspense } from "react";
import { StudioPageLoading } from "./studio/StudioPageLoading.jsx";
import SettingsGenerationPanel from "./settings/SettingsGenerationPanel.jsx";
import {
  LazySettingsAutomationPanel,
  LazySettingsIntegrationsPanel,
  LazySettingsStudioPanel,
  LazySettingsVoiceRefPanel,
} from "./settings/lazyPanels.js";

/** Settings workspace UI — state/handlers passed via `p` from App. */
export function StudioSettingsPage({ p }) {
  const {
    platformCredentialKeysInherited,
    settingsLoadError,
    settingsBusy,
    loadAppSettings,
    saveAppSettings,
    settingsTab,
    setSettingsTab,
  } = p;

  return (
    <section className="panel settings-page">
      <header className="settings-page-header">
        <div>
          <h2>Settings</h2>
          <p className="subtle">
            Workspace defaults (stored on the server). Open a project to override some options per production.
          </p>
          {settingsLoadError ? <p className="err settings-page-error">{settingsLoadError}</p> : null}
          {platformCredentialKeysInherited.length > 0 ? (
            <p className="subtle" style={{ marginTop: 8, maxWidth: 640 }}>
              Some API keys are supplied by your administrator and are not shown (
              {platformCredentialKeysInherited.join(", ")}). Leave those fields empty to keep using them, or enter your
              own to override for this workspace.
            </p>
          ) : null}
        </div>
        <div className="settings-page-toolbar">
          <button type="button" className="secondary" disabled={settingsBusy} onClick={loadAppSettings}>
            Reload from server
          </button>
          <button type="button" disabled={settingsBusy} onClick={saveAppSettings}>
            Save changes
          </button>
        </div>
      </header>

      <div className="settings-layout">
        <nav className="settings-nav" aria-label="Settings sections">
          <button
            type="button"
            className={settingsTab === "generation" ? "is-active" : ""}
            onClick={() => setSettingsTab("generation")}
          >
            Generation
          </button>
          <button
            type="button"
            className={settingsTab === "automation" ? "is-active" : ""}
            onClick={() => setSettingsTab("automation")}
          >
            Automation
          </button>
          <button
            type="button"
            className={settingsTab === "studio" ? "is-active" : ""}
            onClick={() => setSettingsTab("studio")}
          >
            Studio
          </button>
          <button
            type="button"
            className={settingsTab === "integrations" ? "is-active" : ""}
            onClick={() => setSettingsTab("integrations")}
          >
            API keys
          </button>
          <button
            type="button"
            className={settingsTab === "voice_ref" ? "is-active" : ""}
            onClick={() => setSettingsTab("voice_ref")}
          >
            Voice reference
          </button>
        </nav>
        <div className="settings-tab-panel">
          <Suspense fallback={<StudioPageLoading label="Loading settings section…" />}>
            {settingsTab === "generation" ? <SettingsGenerationPanel p={p} /> : null}
            {settingsTab === "automation" ? <LazySettingsAutomationPanel p={p} /> : null}
            {settingsTab === "studio" ? <LazySettingsStudioPanel p={p} /> : null}
            {settingsTab === "integrations" ? <LazySettingsIntegrationsPanel p={p} /> : null}
            {settingsTab === "voice_ref" ? <LazySettingsVoiceRefPanel p={p} /> : null}
          </Suspense>
        </div>
      </div>
    </section>
  );
}
