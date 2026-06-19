/** Cover image (YouTube thumbnail) tab in the media preview column. */
export function PublishCoverTabContent({ pub, projectId, busy }) {
  if (!projectId) {
    return (
      <div className="canvas-stage subtle" style={{ padding: "12px 4px", textAlign: "center", lineHeight: 1.5 }}>
        Open a project to preview and edit the cover image.
      </div>
    );
  }

  const {
    fileInputRef,
    loading,
    pack,
    runPublishJob,
    savePublishPack,
    setYtDescription,
    setYtTitle,
    thumbUrl,
    uploadThumbnail,
    ytDescription,
    ytTitle,
  } = pub;

  return (
    <div className="canvas-stage media-preview-publish-tab">
      {loading ? (
        <p className="subtle" style={{ marginTop: 0 }}>
          Loading cover image…
        </p>
      ) : null}
      <div className="media-preview-publish-thumb">
        {thumbUrl ? (
          <img src={thumbUrl} alt="Cover image preview" className="canvas-preview" />
        ) : (
          <div className="media-preview-publish-thumb-empty subtle">No cover image yet — generate or upload a 16:9 still.</div>
        )}
      </div>
      <div className="action-row" style={{ flexWrap: "wrap", gap: 8, marginTop: 10 }}>
        <button
          type="button"
          disabled={busy}
          onClick={() =>
            void runPublishJob(`/v1/projects/${projectId}/thumbnail/generate`, "Cover image generation queued…")
          }
        >
          Generate
        </button>
        <button type="button" className="secondary" disabled={busy} onClick={() => fileInputRef.current?.click()}>
          Upload
        </button>
        <input
          ref={fileInputRef}
          type="file"
          accept="image/png,image/jpeg,image/webp"
          style={{ display: "none" }}
          onChange={(e) => {
            const f = e.target.files?.[0];
            e.target.value = "";
            if (f) void uploadThumbnail(f);
          }}
        />
      </div>
      <label htmlFor="preview-pub-yt-title" className="media-preview-publish-label">
        YouTube title
      </label>
      <input
        id="preview-pub-yt-title"
        value={ytTitle}
        maxLength={100}
        disabled={busy}
        onChange={(e) => setYtTitle(e.target.value)}
        style={{ width: "100%", marginBottom: 8 }}
      />
      <label htmlFor="preview-pub-yt-desc" className="media-preview-publish-label">
        YouTube description
      </label>
      <textarea
        id="preview-pub-yt-desc"
        value={ytDescription}
        maxLength={5000}
        disabled={busy}
        rows={3}
        onChange={(e) => setYtDescription(e.target.value)}
        style={{ width: "100%", marginBottom: 8 }}
      />
      <button type="button" className="secondary" disabled={busy} onClick={() => void savePublishPack()}>
        Save YouTube copy
      </button>
      <label style={{ display: "flex", gap: 10, alignItems: "flex-start", cursor: "pointer", marginTop: 14, fontSize: "0.85rem" }}>
        <input
          type="checkbox"
          checked={Boolean(pub.publishToYouTube)}
          disabled={busy}
          onChange={(e) => void pub.togglePublishToYouTube(e.target.checked)}
        />
        <span>
          <strong>Publish to YouTube after export</strong>
          <span className="subtle" style={{ display: "block", marginTop: 4, lineHeight: 1.45 }}>
            Uploads the final video when the pipeline finishes (uses title and description above).
          </span>
        </span>
      </label>
      {pack?.source ? (
        <p className="subtle mono" style={{ marginTop: 10, fontSize: "0.7rem" }}>
          source: {pack.source}
        </p>
      ) : null}
    </div>
  );
}
