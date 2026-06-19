import { useCallback, useEffect, useState } from "react";
import { api, apiAssetContentUrl } from "../../lib/api.js";
import { apiErrorMessage, parseJson } from "../../lib/apiHelpers.js";
import { bestSceneListThumbAsset } from "../../lib/studio/sceneHelpers.js";

/** Subscribe outro tab in the media preview column. */
export function PublishOutroTabContent({ pub, projectId, busy }) {
  const [outroScene, setOutroScene] = useState(null);
  const [outroPreviewUrl, setOutroPreviewUrl] = useState("");
  const [outroPreviewKind, setOutroPreviewKind] = useState("image");
  const [outroLoading, setOutroLoading] = useState(false);

  const loadOutroPreview = useCallback(async () => {
    if (!projectId) return;
    setOutroLoading(true);
    try {
      const r = await api(`/v1/projects/${encodeURIComponent(projectId)}/outro`);
      const body = await parseJson(r);
      if (!r.ok) throw new Error(apiErrorMessage(body));
      const sc = body.data?.outro_scene;
      setOutroScene(sc && typeof sc === "object" ? sc : null);
      if (!sc?.id) {
        setOutroPreviewUrl("");
        return;
      }
      const ar = await api(`/v1/scenes/${encodeURIComponent(sc.id)}/assets`);
      const ab = await parseJson(ar);
      if (!ar.ok) throw new Error(apiErrorMessage(ab));
      const rows = Array.isArray(ab.data?.assets) ? ab.data.assets : [];
      const thumb = bestSceneListThumbAsset(rows);
      if (!thumb) {
        setOutroPreviewUrl("");
        return;
      }
      setOutroPreviewKind(String(thumb.asset_type || "image").toLowerCase() === "video" ? "video" : "image");
      setOutroPreviewUrl(
        apiAssetContentUrl(thumb.id, thumb.updated_at || thumb.created_at || thumb.id),
      );
    } catch {
      setOutroScene(null);
      setOutroPreviewUrl("");
    } finally {
      setOutroLoading(false);
    }
  }, [projectId]);

  useEffect(() => {
    void loadOutroPreview();
  }, [loadOutroPreview, pub.includeOutro]);

  if (!projectId) {
    return (
      <div className="canvas-stage subtle" style={{ padding: "12px 4px", textAlign: "center", lineHeight: 1.5 }}>
        Open a project to configure the subscribe outro.
      </div>
    );
  }

  const { includeOutro, loading, runPublishJob, toggleOutro } = pub;

  return (
    <div className="canvas-stage media-preview-publish-tab">
      {loading || outroLoading ? (
        <p className="subtle" style={{ marginTop: 0 }}>
          Loading outro…
        </p>
      ) : null}
      <label style={{ display: "flex", gap: 10, alignItems: "flex-start", cursor: "pointer", fontSize: "0.85rem" }}>
        <input
          type="checkbox"
          checked={includeOutro}
          disabled={busy}
          onChange={(e) => void toggleOutro(e.target.checked)}
        />
        <span>
          <strong>Include subscribe outro scene</strong>
          <span className="subtle" style={{ display: "block", marginTop: 4, lineHeight: 1.45 }}>
            When enabled, automation appends a last scene with subscribe CTA narration (off by default).
          </span>
        </span>
      </label>
      {includeOutro ? (
        <div className="action-row" style={{ marginTop: 10, flexWrap: "wrap", gap: 8 }}>
          <button
            type="button"
            disabled={busy}
            onClick={async () => {
              await runPublishJob(`/v1/projects/${projectId}/outro/append`, "Outro scene queued…");
              await loadOutroPreview();
            }}
          >
            Append outro scene
          </button>
          <button type="button" className="secondary" disabled={busy} onClick={() => void loadOutroPreview()}>
            Refresh preview
          </button>
        </div>
      ) : null}
      <div className="media-preview-publish-outro-preview" style={{ marginTop: 12 }}>
        {outroScene ? (
          <>
            <div className="canvas-label">Outro scene</div>
            {outroPreviewUrl ? (
              outroPreviewKind === "video" ? (
                <video className="canvas-preview" controls playsInline muted src={outroPreviewUrl} />
              ) : (
                <img className="canvas-preview" src={outroPreviewUrl} alt="Outro scene preview" />
              )
            ) : (
              <p className="subtle" style={{ marginTop: 8 }}>
                Outro scene exists — generate scene media or run automation to fill visuals.
              </p>
            )}
            {outroScene.narration_text ? (
              <p className="subtle" style={{ marginTop: 10, fontSize: "0.78rem", lineHeight: 1.5, whiteSpace: "pre-wrap" }}>
                {String(outroScene.narration_text).trim()}
              </p>
            ) : null}
          </>
        ) : includeOutro ? (
          <p className="subtle" style={{ marginTop: 8 }}>
            Outro enabled but no outro scene yet — click Append outro scene or continue the pipeline.
          </p>
        ) : (
          <p className="subtle" style={{ marginTop: 8 }}>
            Enable the outro to add a subscribe scene at the end of the video.
          </p>
        )}
      </div>
    </div>
  );
}
