/** Opening hook tab in the media preview column. */
export function PublishHookTabContent({ pub, projectId, busy }) {
  if (!projectId) {
    return (
      <div className="canvas-stage subtle" style={{ padding: "12px 4px", textAlign: "center", lineHeight: 1.5 }}>
        Open a project to preview and edit the opening hook.
      </div>
    );
  }

  const { hookText, loading, runPublishJob, saveHook, setHookText } = pub;
  const wordCount = hookText.trim() ? hookText.trim().split(/\s+/).filter(Boolean).length : 0;

  return (
    <div className="canvas-stage media-preview-publish-tab">
      {loading ? (
        <p className="subtle" style={{ marginTop: 0 }}>
          Loading hook…
        </p>
      ) : null}
      <p className="subtle" style={{ marginTop: 0, fontSize: "0.78rem", lineHeight: 1.45 }}>
        Spoken opening script before scene one — not a scene row. Used in automation after chapter scripts.
      </p>
      <textarea
        value={hookText}
        disabled={busy}
        rows={10}
        className="media-preview-hook-text"
        onChange={(e) => setHookText(e.target.value)}
        placeholder="Opening hook script…"
        style={{ width: "100%", marginBottom: 8, minHeight: 140 }}
      />
      <p className="subtle" style={{ margin: "0 0 8px", fontSize: "0.72rem" }}>
        {wordCount > 0 ? `${wordCount} words` : "No hook text yet"}
        {hookText.trim().length >= 50 ? " · Ready for pipeline" : hookText.trim() ? " · Aim for ~50+ characters" : ""}
      </p>
      <div className="action-row" style={{ flexWrap: "wrap", gap: 8 }}>
        <button
          type="button"
          disabled={busy}
          onClick={() =>
            void runPublishJob(`/v1/projects/${projectId}/opening-hook/generate`, "Hook generation queued…")
          }
        >
          Generate hook
        </button>
        <button type="button" className="secondary" disabled={busy || !hookText.trim()} onClick={() => void saveHook()}>
          Save hook
        </button>
      </div>
    </div>
  );
}
