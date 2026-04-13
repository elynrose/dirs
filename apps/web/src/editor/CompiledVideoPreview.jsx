import { useCallback, useEffect, useState } from "react";
import { apiBase, apiCompiledVideoUrl, apiForm, apiRelativePathFromFullUrl, sanitizeStudioUuid } from "../lib/api.js";

/**
 * Polls the API for an on-disk compiled video (exports/…/final_cut.mp4, etc.) and shows inline playback + download.
 */
export function CompiledVideoPreview({ projectId, timelineVersionId }) {
  const [available, setAvailable] = useState(null);
  const [videoError, setVideoError] = useState(false);
  const [cacheBust, setCacheBust] = useState("0");

  const tv = sanitizeStudioUuid(timelineVersionId);
  const pid = sanitizeStudioUuid(projectId);

  const probe = useCallback(async () => {
    if (!pid || !tv) {
      setAvailable(false);
      return;
    }
    const url = apiCompiledVideoUrl(pid, tv, { cacheBust: Date.now() });
    try {
      const r = await apiForm(apiRelativePathFromFullUrl(url), { method: "HEAD" });
      const ok = r.ok;
      setAvailable(ok);
      if (ok) {
        setCacheBust(String(Date.now()));
        setVideoError(false);
      }
    } catch {
      setAvailable(false);
    }
  }, [pid, tv]);

  useEffect(() => {
    setAvailable(null);
    setVideoError(false);
    void probe();
  }, [probe]);

  useEffect(() => {
    if (available) return undefined;
    if (!pid || !tv) return undefined;
    const id = setInterval(() => void probe(), 12_000);
    return () => clearInterval(id);
  }, [available, probe, pid, tv]);

  if (!pid || !tv) {
    return (
      <div className="canvas-stage subtle" style={{ padding: "12px 4px", lineHeight: 1.5 }}>
        Open a project and enter a <strong>Timeline version ID</strong> under <strong>Timeline &amp; export → Compile video</strong>, then run{" "}
        <strong>Rough cut</strong> or <strong>Final cut</strong>. When the file exists on the API host under{" "}
        <code>exports/…</code>, it appears here.
      </div>
    );
  }

  if (available === null) {
    return (
      <div className="canvas-stage subtle" style={{ padding: "12px 4px" }}>
        Checking for compiled video…
      </div>
    );
  }

  if (!available) {
    return (
      <div className="canvas-stage subtle" style={{ padding: "12px 4px", lineHeight: 1.55 }}>
        <p style={{ margin: "0 0 8px" }}>No compiled MP4 on disk for this timeline yet.</p>
        <p className="subtle" style={{ margin: 0 }}>
          Queue <strong>Rough cut</strong> or <strong>Final cut</strong> in <strong>Timeline &amp; export</strong> and wait until the job succeeds. This tab refreshes automatically every few seconds while you stay here.
        </p>
      </div>
    );
  }

  const playUrl = apiCompiledVideoUrl(pid, tv, { cacheBust });
  const downloadUrl = apiCompiledVideoUrl(pid, tv, { download: true, cacheBust });

  return (
    <div className="canvas-stage compiled-video-preview">
      <div className="compiled-video-preview__actions">
        <a href={downloadUrl} download rel="noreferrer">
          Download compiled video
        </a>
        <span className="subtle" style={{ fontSize: "0.72rem" }}>
          Uses final_cut when present, otherwise fine_cut or rough_cut on the server.
        </span>
      </div>
      {videoError ? (
        <div className="err" style={{ marginTop: 10 }}>
          Could not play video in the browser. Try <a href={downloadUrl}>download</a> or open the video URL from DevTools → Network.
          {String(apiBase || "").trim() ? (
            <>
              {" "}
              You are using <code className="mono">VITE_API_BASE_URL</code> (cross-origin). Ensure the API allows your Studio origin in{" "}
              <code className="mono">CORS_EXTRA_ORIGINS</code> (e.g. <code className="mono">http://YOUR_HOST:5173</code>), or leave{" "}
              <code className="mono">VITE_API_BASE_URL</code> unset so <code className="mono">/v1</code> is same-origin behind the Vite proxy.
            </>
          ) : null}
        </div>
      ) : (
        <video
          key={playUrl}
          className="canvas-preview"
          controls
          playsInline
          {...(String(apiBase || "").trim()
            ? {
                // Direct API origin (VITE_API_BASE_URL): needs CORS + anonymous cross-origin load.
                crossOrigin: "anonymous",
              }
            : {})}
          src={playUrl}
          onError={() => setVideoError(true)}
        />
      )}
    </div>
  );
}
