export function StudioPromptsPage({
  llmPromptsErr,
  llmPromptsBusy,
  llmPrompts,
  loadLlmPrompts,
  llmPromptDrafts,
  setLlmPromptDrafts,
  saveLlmPrompt,
  resetLlmPrompt,
}) {
  return (
    <section className="panel settings-page prompts-page">
      <header className="settings-page-header">
        <div>
          <h2>Prompts</h2>
          <p className="subtle">
            System instructions for documentary text models. Workspace defaults come from the catalog; your edits are
            stored per signed-in user (and scoped to the current workspace when using multi-tenant auth). Scene-plan prompts
            should mention that writers may use <code>[square brackets]</code> in <code>narration_text</code> for optional visual
            emphasis — generation code uses those hints before raw VO when building image/video prompts (see also{" "}
            <strong>Settings → Visual styles</strong>).
          </p>
          {llmPromptsErr ? <p className="err settings-page-error">{llmPromptsErr}</p> : null}
        </div>
        <div className="settings-page-toolbar">
          <button type="button" className="secondary" disabled={llmPromptsBusy} onClick={() => void loadLlmPrompts()}>
            Reload from server
          </button>
        </div>
      </header>
      <div className="prompts-list">
        {llmPromptsBusy && llmPrompts.length === 0 ? <p className="subtle">Loading prompts…</p> : null}
        {llmPrompts.map((p) => (
          <details key={p.prompt_key} className="settings-section prompts-editor-card">
            <summary className="settings-section-summary">
              <span className="settings-section-heading">
                {p.title}
                {p.is_custom ? <span className="subtle"> — custom</span> : null}
              </span>
            </summary>
            {p.description ? <p className="subtle prompts-editor-desc">{p.description}</p> : null}
            <div className="prompts-editor-meta subtle">{p.prompt_key}</div>
            <textarea
              className="prompts-editor-textarea"
              rows={14}
              value={llmPromptDrafts[p.prompt_key] ?? ""}
              onChange={(e) =>
                setLlmPromptDrafts((d) => ({ ...d, [p.prompt_key]: e.target.value }))
              }
              disabled={llmPromptsBusy}
              spellCheck={false}
            />
            <div className="prompts-editor-actions">
              <button type="button" disabled={llmPromptsBusy} onClick={() => void saveLlmPrompt(p.prompt_key)}>
                Save
              </button>
              <button
                type="button"
                className="secondary"
                disabled={llmPromptsBusy || !p.is_custom}
                onClick={() => void resetLlmPrompt(p.prompt_key)}
              >
                Revert to default
              </button>
            </div>
          </details>
        ))}
      </div>
    </section>
  );
}
