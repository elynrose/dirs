import { useCallback, useEffect, useRef, useState } from "react";
import { api, apiForm, apiProjectThumbnailContentUrl } from "../../lib/api.js";
import {
  apiErrorMessage,
  apiPostIdempotent,
  formatUserFacingError,
  parseJson,
  pollJobUntilTerminal,
} from "../../lib/apiHelpers.js";

/**
 * Thumbnail / YouTube pack, opening hook, and outro settings for a project.
 */
export function useProjectPublish({ projectId, busy, setBusy, setError, setMessage, idem, onScenesReload }) {
  const [pack, setPack] = useState(null);
  const [hookText, setHookText] = useState("");
  const [includeOutro, setIncludeOutro] = useState(false);
  const [publishToYouTube, setPublishToYouTube] = useState(false);
  const [ytTitle, setYtTitle] = useState("");
  const [ytDescription, setYtDescription] = useState("");
  const [thumbKey, setThumbKey] = useState("");
  const [loading, setLoading] = useState(false);
  const fileInputRef = useRef(null);

  const loadPublishMeta = useCallback(async () => {
    if (!projectId) return;
    setLoading(true);
    try {
      const r = await api(`/v1/projects/${encodeURIComponent(projectId)}`);
      const body = await parseJson(r);
      if (!r.ok) throw new Error(apiErrorMessage(body));
      const p = body.data || {};
      const pp = p.publish_pack_json && typeof p.publish_pack_json === "object" ? p.publish_pack_json : {};
      setPack(pp);
      setHookText(String(p.opening_hook_text || ""));
      setIncludeOutro(Boolean(p.include_outro_scene));
      setPublishToYouTube(Boolean(p.publish_to_youtube));
      setYtTitle(String(pp.youtube_title || p.title || ""));
      setYtDescription(String(pp.youtube_description || ""));
      setThumbKey(String(pp.thumbnail_storage_key || pp.updated_at || ""));
    } catch (e) {
      setError?.(formatUserFacingError(e));
    } finally {
      setLoading(false);
    }
  }, [projectId, setError]);

  useEffect(() => {
    void loadPublishMeta();
  }, [loadPublishMeta]);

  const thumbUrl =
    projectId && thumbKey ? apiProjectThumbnailContentUrl(projectId, thumbKey) : "";

  const runPublishJob = useCallback(
    async (path, okMsg) => {
      if (!projectId) return;
      setBusy?.(true);
      setError?.("");
      try {
        const body = await apiPostIdempotent(api, path, {}, idem);
        const jobId = body?.job?.id;
        if (!jobId) throw new Error(apiErrorMessage(body) || "Job not queued");
        setMessage?.(okMsg);
        await pollJobUntilTerminal(api, jobId);
        await loadPublishMeta();
        if (typeof onScenesReload === "function") await onScenesReload();
      } catch (e) {
        setError?.(formatUserFacingError(e));
      } finally {
        setBusy?.(false);
      }
    },
    [projectId, setBusy, setError, setMessage, idem, loadPublishMeta, onScenesReload],
  );

  const savePublishPack = useCallback(async () => {
    if (!projectId) return;
    setBusy?.(true);
    setError?.("");
    try {
      const r = await api(`/v1/projects/${encodeURIComponent(projectId)}/publish-pack`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          youtube_title: ytTitle.trim(),
          youtube_description: ytDescription.trim(),
        }),
      });
      const body = await parseJson(r);
      if (!r.ok) throw new Error(apiErrorMessage(body));
      setPack(body.data?.publish_pack || {});
      setMessage?.("YouTube copy saved.");
    } catch (e) {
      setError?.(formatUserFacingError(e));
    } finally {
      setBusy?.(false);
    }
  }, [projectId, ytTitle, ytDescription, setBusy, setError, setMessage]);

  const saveHook = useCallback(async () => {
    if (!projectId || !hookText.trim()) return;
    setBusy?.(true);
    setError?.("");
    try {
      const r = await api(`/v1/projects/${encodeURIComponent(projectId)}/opening-hook`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ text: hookText.trim() }),
      });
      const body = await parseJson(r);
      if (!r.ok) throw new Error(apiErrorMessage(body));
      setHookText(String(body.data?.opening_hook_text || hookText));
      setMessage?.("Opening hook saved.");
    } catch (e) {
      setError?.(formatUserFacingError(e));
    } finally {
      setBusy?.(false);
    }
  }, [projectId, hookText, setBusy, setError, setMessage]);

  const toggleOutro = useCallback(
    async (next) => {
      if (!projectId) return;
      setBusy?.(true);
      setError?.("");
      try {
        const r = await api(`/v1/projects/${encodeURIComponent(projectId)}/outro-settings`, {
          method: "PATCH",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ include_outro_scene: next }),
        });
        const body = await parseJson(r);
        if (!r.ok) throw new Error(apiErrorMessage(body));
        setIncludeOutro(Boolean(body.data?.include_outro_scene));
        if (!next && typeof onScenesReload === "function") await onScenesReload();
        setMessage?.(next ? "Outro enabled — run automation or Append outro to add the scene." : "Outro disabled.");
      } catch (e) {
        setError?.(formatUserFacingError(e));
      } finally {
        setBusy?.(false);
      }
    },
    [projectId, setBusy, setError, setMessage, onScenesReload],
  );

  const togglePublishToYouTube = useCallback(
    async (next) => {
      if (!projectId) return;
      setBusy?.(true);
      setError?.("");
      try {
        const r = await api(`/v1/projects/${encodeURIComponent(projectId)}/publish-settings`, {
          method: "PATCH",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ publish_to_youtube: next }),
        });
        const body = await parseJson(r);
        if (!r.ok) throw new Error(apiErrorMessage(body));
        setPublishToYouTube(Boolean(body.data?.publish_to_youtube));
        setMessage?.(next ? "YouTube publish enabled for this project." : "YouTube publish disabled.");
      } catch (e) {
        setError?.(formatUserFacingError(e));
      } finally {
        setBusy?.(false);
      }
    },
    [projectId, setBusy, setError, setMessage],
  );

  const uploadThumbnail = useCallback(
    async (file) => {
      if (!projectId || !file) return;
      setBusy?.(true);
      setError?.("");
      try {
        const fd = new FormData();
        fd.append("file", file);
        if (ytTitle.trim()) fd.append("youtube_title", ytTitle.trim());
        if (ytDescription.trim()) fd.append("youtube_description", ytDescription.trim());
        const r = await apiForm(`/v1/projects/${encodeURIComponent(projectId)}/thumbnail/upload`, {
          method: "POST",
          body: fd,
        });
        const body = await parseJson(r);
        if (!r.ok) throw new Error(apiErrorMessage(body));
        const pp = body.data?.publish_pack;
        if (pp && typeof pp === "object") {
          setPack(pp);
          setThumbKey(String(pp.updated_at || pp.thumbnail_storage_key || Date.now()));
        }
        setMessage?.("Thumbnail uploaded.");
      } catch (e) {
        setError?.(formatUserFacingError(e));
      } finally {
        setBusy?.(false);
      }
    },
    [projectId, ytTitle, ytDescription, setBusy, setError, setMessage],
  );

  return {
    fileInputRef,
    hookText,
    includeOutro,
    publishToYouTube,
    loading,
    loadPublishMeta,
    pack,
    runPublishJob,
    saveHook,
    savePublishPack,
    setHookText,
    setYtDescription,
    setYtTitle,
    thumbUrl,
    toggleOutro,
    togglePublishToYouTube,
    uploadThumbnail,
    ytDescription,
    ytTitle,
  };
}
