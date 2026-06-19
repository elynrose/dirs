import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api, apiForm, apiAssetContentUrl, sanitizeStudioUuid } from "../../lib/api.js";
import {
  apiErrorMessage,
  apiPostIdempotent,
  formatUserFacingError,
  parseJson,
} from "../../lib/apiHelpers.js";
import { assetGenerationPrompt, estAssetCoverSec } from "../../lib/studio/assetHelpers.js";
import { fetchResolvedPromptsForScene } from "../../lib/studio/promptHelpers.js";

const PROMPT_ENHANCE_API_TIMEOUT_MS = 110_000;

function formatPromptEnhanceClientError(e) {
  const msg = formatUserFacingError(e);
  if (/timed?\s*out/i.test(msg) || /timeout/i.test(msg)) {
    return `${msg} Prompt improve can take up to ~2 minutes — try again or shorten the draft.`;
  }
  return msg;
}

/**
 * Scene assets gallery, preview, stock import, approve/reject, and image/video job enqueue.
 */
export function useEditorAssetsMedia({
  studioReady,
  projectId,
  chapterId,
  scenes,
  expandedScene,
  selectedSceneId,
  sceneIdForAssetGalleryRefresh,
  appConfig,
  setBusy,
  setError,
  setMessage,
  setExpandedScene,
  setMediaJobId,
  setMediaPoll,
  loadPhase3Summary,
  refreshPhase5Readiness,
  loadActiveProjectJobs,
  refineBracketImageWithLlm,
  excludeCharacterBibleFromPrompts,
  idem,
}) {
  const [sceneAssets, setSceneAssets] = useState({});
  const [sceneAssetsFetchError, setSceneAssetsFetchError] = useState(null);
  const [retryPrompt, setRetryPrompt] = useState("");
  const retryPromptSceneRef = useRef(null);
  const [retryVideoPrompt, setRetryVideoPrompt] = useState("");
  const retryVideoPromptSceneRef = useRef(null);
  const [pinnedPreviewAssetId, setPinnedPreviewAssetId] = useState(null);
  const [previewMediaError, setPreviewMediaError] = useState(false);
  const [mediaPreviewTab, setMediaPreviewTab] = useState("scene");
  const sceneClipFileInputRef = useRef(null);
  const [sceneClipUploadKind, setSceneClipUploadKind] = useState("auto");
  const [sceneStockLibrary, setSceneStockLibrary] = useState("pexels");
  const [pexelsStockTab, setPexelsStockTab] = useState("photos");
  const [pexelsSearchQuery, setPexelsSearchQuery] = useState("");
  const [pexelsSearchResults, setPexelsSearchResults] = useState([]);
  const [pexelsSearchBusy, setPexelsSearchBusy] = useState(false);
  const [pexelsSearchErr, setPexelsSearchErr] = useState("");
  const [pexelsImportKey, setPexelsImportKey] = useState("");
  const [stockVideoTrimModal, setStockVideoTrimModal] = useState(null);
  const [pexelsTrimHint, setPexelsTrimHint] = useState(null);
  const [pexelsTrimHintBusy, setPexelsTrimHintBusy] = useState(false);
  const [promptEnhanceImageBusy, setPromptEnhanceImageBusy] = useState(false);
  const [selectedAssetIds, setSelectedAssetIds] = useState(() => new Set());

  const sceneClipSec = Number(appConfig.scene_clip_duration_sec) === 5 ? 5 : 10;

  const mergeSceneAssetFromEvent = useCallback((asset) => {
    if (!asset?.scene_id) return;
    setSceneAssets((prev) => {
      const existing = prev[asset.scene_id] ?? [];
      if (existing.some((a) => a.id === asset.id)) return prev;
      return { ...prev, [asset.scene_id]: [...existing, asset] };
    });
  }, []);

  const resetAssetsMediaSlice = useCallback(() => {
    setSceneAssets({});
    setSceneAssetsFetchError(null);
    setPinnedPreviewAssetId(null);
    setMediaPreviewTab("scene");
    setRetryPrompt("");
    setRetryVideoPrompt("");
    retryPromptSceneRef.current = null;
    retryVideoPromptSceneRef.current = null;
    setSelectedAssetIds(new Set());
  }, []);

  const runSceneStockSearch = useCallback(async () => {
    const q = String(pexelsSearchQuery || "").trim();
    if (!q) {
      setPexelsSearchResults([]);
      setPexelsSearchErr("");
      return;
    }
    setPexelsSearchBusy(true);
    setPexelsSearchErr("");
    try {
      const lib = String(sceneStockLibrary || "pexels").toLowerCase();
      const isPhotos = pexelsStockTab === "photos";
      let path;
      let sp;
      if (lib === "storyblocks") {
        path = isPhotos ? "/v1/storyblocks/photos/search" : "/v1/storyblocks/videos/search";
        sp = new URLSearchParams({ query: q, page: "1", per_page: isPhotos ? "20" : "15" });
      } else {
        path = isPhotos ? "/v1/pexels/photos/search" : "/v1/pexels/videos/search";
        sp = new URLSearchParams({ query: q, page: "1", per_page: isPhotos ? "20" : "15" });
      }
      const r = await api(`${path}?${sp.toString()}`);
      const body = await parseJson(r);
      if (!r.ok) {
        throw new Error(apiErrorMessage(body) || "Stock search failed");
      }
      setPexelsSearchResults(Array.isArray(body.data?.results) ? body.data.results : []);
    } catch (e) {
      setPexelsSearchResults([]);
      setPexelsSearchErr(formatUserFacingError(e));
    } finally {
      setPexelsSearchBusy(false);
    }
  }, [pexelsSearchQuery, pexelsStockTab, sceneStockLibrary]);

  useEffect(() => {
    const q = String(pexelsSearchQuery || "").trim();
    if (!q) {
      setPexelsSearchResults([]);
      setPexelsSearchErr("");
      return;
    }
    const t = window.setTimeout(() => {
      void runSceneStockSearch();
    }, 450);
    return () => window.clearTimeout(t);
  }, [pexelsSearchQuery, pexelsStockTab, sceneStockLibrary, runSceneStockSearch]);

  const loadSceneAssets = useCallback(async (sid) => {
    if (!sid) return;
    setSceneAssetsFetchError((prev) => (prev && String(prev.sceneId) === String(sid) ? null : prev));
    try {
      const r = await api(`/v1/scenes/${encodeURIComponent(sid)}/assets`);
      const body = await parseJson(r);
      if (r.ok) {
        setSceneAssets((prev) => ({ ...prev, [sid]: body.data?.assets || [] }));
        setSceneAssetsFetchError((prev) => (prev && String(prev.sceneId) === String(sid) ? null : prev));
      } else {
        setSceneAssets((prev) => ({ ...prev, [sid]: [] }));
        setSceneAssetsFetchError({
          sceneId: String(sid),
          message: apiErrorMessage(body) || "Could not load assets for this scene.",
        });
      }
    } catch (e) {
      setSceneAssets((prev) => ({ ...prev, [sid]: [] }));
      setSceneAssetsFetchError({
        sceneId: String(sid),
        message: formatUserFacingError(e),
      });
    }
  }, []);

  const approveAsset = useCallback(
    async (assetId, opts = {}) => {
      const quiet = Boolean(opts.quiet);
      const id = sanitizeStudioUuid(assetId);
      if (!id) {
        setError("Invalid asset id.");
        return false;
      }
      if (!quiet) setError("");
      try {
        const r = await api(`/v1/assets/${encodeURIComponent(id)}/approve`, {
          method: "POST",
          body: JSON.stringify({}),
        });
        const body = await parseJson(r);
        if (!r.ok) {
          setError(apiErrorMessage(body) || "approve failed");
          return false;
        }
        const sid = sceneIdForAssetGalleryRefresh();
        if (sid) void loadSceneAssets(sid);
        if (chapterId) void loadPhase3Summary(chapterId);
        if (projectId) void refreshPhase5Readiness({ reportError: false });
        if (!quiet) setMessage("Approval saved.");
        return true;
      } catch (e) {
        setError(formatUserFacingError(e));
        return false;
      }
    },
    [
      sceneIdForAssetGalleryRefresh,
      loadSceneAssets,
      chapterId,
      loadPhase3Summary,
      projectId,
      refreshPhase5Readiness,
      setError,
      setMessage,
    ],
  );

  const rejectAsset = useCallback(
    async (assetId, opts = {}) => {
      const quiet = Boolean(opts.quiet);
      const id = sanitizeStudioUuid(assetId);
      if (!id) {
        setError("Invalid asset id.");
        return false;
      }
      if (!quiet) setError("");
      try {
        const r = await api(`/v1/assets/${encodeURIComponent(id)}/reject`, {
          method: "POST",
          body: JSON.stringify({ reason: "Rejected from studio UI" }),
        });
        const body = await parseJson(r);
        if (!r.ok) {
          setError(apiErrorMessage(body) || "reject failed");
          return false;
        }
        const sid = sceneIdForAssetGalleryRefresh();
        if (sid) void loadSceneAssets(sid);
        if (chapterId) void loadPhase3Summary(chapterId);
        if (projectId) void refreshPhase5Readiness({ reportError: false });
        if (!quiet) setMessage("Asset rejected.");
        return true;
      } catch (e) {
        setError(formatUserFacingError(e));
        return false;
      }
    },
    [
      sceneIdForAssetGalleryRefresh,
      loadSceneAssets,
      chapterId,
      loadPhase3Summary,
      projectId,
      refreshPhase5Readiness,
      setError,
      setMessage,
    ],
  );

  const reorderSceneAssets = useCallback(
    async (sceneId, orderedIds) => {
      if (!sceneId || !orderedIds?.length) return;
      setError("");
      try {
        const r = await api(`/v1/scenes/${sceneId}/assets/sequence`, {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ asset_ids: orderedIds }),
        });
        const body = await parseJson(r);
        if (!r.ok) {
          setError(apiErrorMessage(body) || "reorder failed");
          return;
        }
        setSceneAssets((prev) => ({ ...prev, [sceneId]: body.data?.assets || [] }));
        if (chapterId) loadPhase3Summary(chapterId);
      } catch (e) {
        setError(formatUserFacingError(e));
      }
    },
    [chapterId, loadPhase3Summary, setError],
  );

  const postImage = useCallback(
    async (sceneId, path, bodyObj = {}) => {
      setBusy(true);
      setError("");
      setExpandedScene(sceneId);
      try {
        const extra = { ...bodyObj };
        if (path === "generate-image" || path === "retry") {
          const m = String(appConfig.fal_smoke_model || "").trim();
          if (m) extra.fal_image_model = m;
          const p = String(appConfig.active_image_provider || "fal").trim().toLowerCase();
          if (p) extra.image_provider = p;
          if (refineBracketImageWithLlm) extra.refine_bracket_visual_with_llm = true;
        }
        if (path === "generate-video") {
          const m = String(appConfig.fal_video_model || "").trim();
          if (m) extra.fal_video_model = m;
          const vp = String(appConfig.active_video_provider || "fal").trim().toLowerCase();
          if (vp) extra.video_provider = vp;
        }
        if (
          excludeCharacterBibleFromPrompts &&
          (path === "generate-image" || path === "retry" || path === "generate-video")
        ) {
          extra.exclude_character_bible = true;
        }
        const body = await apiPostIdempotent(api, `/v1/scenes/${sceneId}/${path}`, extra, idem);
        const jid = body.job?.id;
        if (jid) {
          setMediaJobId(jid);
          setMediaPoll(true);
        }
        loadSceneAssets(sceneId);
        setMessage(`${path} queued…`);
        void loadActiveProjectJobs();
      } catch (e) {
        setError(formatUserFacingError(e));
      } finally {
        setBusy(false);
      }
    },
    [
      appConfig,
      excludeCharacterBibleFromPrompts,
      idem,
      loadActiveProjectJobs,
      loadSceneAssets,
      refineBracketImageWithLlm,
      setBusy,
      setError,
      setExpandedScene,
      setMediaJobId,
      setMediaPoll,
      setMessage,
    ],
  );

  const uploadSceneClipFile = useCallback(async () => {
    if (!selectedSceneId) return;
    const inp = sceneClipFileInputRef.current;
    const f = inp?.files?.[0];
    if (!f) {
      setError("Choose an image, video, or audio file first.");
      return;
    }
    setBusy(true);
    setError("");
    try {
      const fd = new FormData();
      fd.append("file", f, f.name || "upload");
      fd.append("clip_kind", sceneClipUploadKind);
      const r = await apiForm(`/v1/scenes/${encodeURIComponent(selectedSceneId)}/upload-clip`, {
        method: "POST",
        body: fd,
      });
      const b = await parseJson(r);
      if (!r.ok) throw new Error(apiErrorMessage(b));
      if (inp) inp.value = "";
      void loadSceneAssets(selectedSceneId);
      if (chapterId) void loadPhase3Summary(chapterId);
      if (projectId) void refreshPhase5Readiness({ reportError: false });
      setMessage("Clip uploaded to this scene.");
    } catch (e) {
      setError(formatUserFacingError(e));
    } finally {
      setBusy(false);
    }
  }, [
    selectedSceneId,
    sceneClipUploadKind,
    loadSceneAssets,
    chapterId,
    loadPhase3Summary,
    projectId,
    refreshPhase5Readiness,
    setBusy,
    setError,
    setMessage,
  ]);

  const importSceneAssetFromStock = useCallback(
    async (library, kind, mediaId, videoTrimTarget) => {
      const sid = String(selectedSceneId || "").trim();
      if (!sid) {
        setError("Select a scene before importing stock media.");
        return;
      }
      const idNum = Number(mediaId);
      if (!Number.isFinite(idNum) || idNum < 1) {
        setError("Invalid stock media id.");
        return;
      }
      const lib = String(library || "pexels").toLowerCase();
      setPexelsImportKey(`${lib}:${kind}:${mediaId}`);
      setError("");
      try {
        const k = kind === "video" ? "video" : "photo";
        if (lib === "storyblocks") {
          const payload = { kind: k, storyblocks_id: Math.floor(idNum) };
          if (
            k === "video" &&
            (videoTrimTarget === "5" || videoTrimTarget === "10" || videoTrimTarget === "scene_narration")
          ) {
            payload.video_trim_target = videoTrimTarget;
          }
          const r = await api(`/v1/scenes/${encodeURIComponent(sid)}/assets/import-from-storyblocks`, {
            method: "POST",
            body: JSON.stringify(payload),
          });
          const body = await parseJson(r);
          if (!r.ok) {
            throw new Error(apiErrorMessage(body) || "Import failed");
          }
        } else {
          const payload = { kind: k, pexels_id: Math.floor(idNum) };
          if (
            k === "video" &&
            (videoTrimTarget === "5" || videoTrimTarget === "10" || videoTrimTarget === "scene_narration")
          ) {
            payload.video_trim_target = videoTrimTarget;
          }
          const r = await api(`/v1/scenes/${encodeURIComponent(sid)}/assets/import-from-pexels`, {
            method: "POST",
            body: JSON.stringify(payload),
          });
          const body = await parseJson(r);
          if (!r.ok) {
            throw new Error(apiErrorMessage(body) || "Import failed");
          }
        }
        void loadSceneAssets(sid);
        if (chapterId) void loadPhase3Summary(chapterId);
        if (projectId) void refreshPhase5Readiness({ reportError: false });
        setMessage("Stock media added to this scene.");
      } catch (e) {
        setError(formatUserFacingError(e));
      } finally {
        setPexelsImportKey("");
      }
    },
    [
      selectedSceneId,
      loadSceneAssets,
      chapterId,
      loadPhase3Summary,
      projectId,
      refreshPhase5Readiness,
      setError,
      setMessage,
    ],
  );

  const enhanceRetryImagePrompt = useCallback(async () => {
    const sid = String(selectedSceneId || "").trim();
    if (!sid) return;
    const current = String(retryPrompt || "").trim();
    if (!current.length) {
      setError("Add some text to the image prompt first, then use Improve prompt.");
      return;
    }
    setPromptEnhanceImageBusy(true);
    setError("");
    try {
      const r = await api(`/v1/scenes/${encodeURIComponent(sid)}/prompt-enhance-image`, {
        method: "POST",
        body: JSON.stringify({ current_prompt: current }),
        timeoutMs: PROMPT_ENHANCE_API_TIMEOUT_MS,
      });
      const b = await parseJson(r);
      if (!r.ok) throw new Error(apiErrorMessage(b));
      const text = b.data?.text;
      if (typeof text !== "string" || !String(text).trim()) throw new Error("No improved text returned.");
      setRetryPrompt(String(text).trim());
      setMessage("Image prompt improved with previous scene + character context.");
    } catch (e) {
      setError(formatPromptEnhanceClientError(e));
    } finally {
      setPromptEnhanceImageBusy(false);
    }
  }, [selectedSceneId, retryPrompt, setError, setMessage]);

  const sceneIdsPrefetchKey = useMemo(() => scenes.map((s) => String(s.id)).join(","), [scenes]);

  useEffect(() => {
    if (!studioReady || !sceneIdsPrefetchKey) return;
    for (const id of sceneIdsPrefetchKey.split(",")) {
      if (id) void loadSceneAssets(id);
    }
  }, [studioReady, sceneIdsPrefetchKey, loadSceneAssets]);

  useEffect(() => {
    const sid = expandedSceneOrFirst(scenes, expandedScene);
    if (!sid) {
      setRetryPrompt("");
      setRetryVideoPrompt("");
      retryPromptSceneRef.current = null;
      retryVideoPromptSceneRef.current = null;
      return;
    }
    let cancelled = false;
    if (retryPromptSceneRef.current !== sid || retryVideoPromptSceneRef.current !== sid) {
      void (async () => {
        try {
          const prompts = await fetchResolvedPromptsForScene(sid, api);
          if (cancelled) return;
          retryPromptSceneRef.current = sid;
          retryVideoPromptSceneRef.current = sid;
          setRetryPrompt(prompts.image_prompt || "");
          setRetryVideoPrompt(prompts.video_prompt || "");
        } catch {
          if (cancelled) return;
          retryPromptSceneRef.current = sid;
          retryVideoPromptSceneRef.current = sid;
          setRetryPrompt("");
          setRetryVideoPrompt("");
        }
      })();
    }
    return () => {
      cancelled = true;
    };
  }, [expandedScene, scenes]);

  useEffect(() => {
    if (!stockVideoTrimModal) {
      setPexelsTrimHint(null);
      setPexelsTrimHintBusy(false);
      return;
    }
    const sid = String(selectedSceneId || "").trim();
    if (!sid) {
      setPexelsTrimHint(null);
      setPexelsTrimHintBusy(false);
      return;
    }
    let cancelled = false;
    setPexelsTrimHint(null);
    setPexelsTrimHintBusy(true);
    void (async () => {
      try {
        const r = await api(`/v1/scenes/${encodeURIComponent(sid)}/pexels-video-trim-hint`);
        const body = await parseJson(r);
        if (!cancelled && r.ok) {
          setPexelsTrimHint(body.data ?? null);
        }
      } catch {
        if (!cancelled) setPexelsTrimHint(null);
      } finally {
        if (!cancelled) setPexelsTrimHintBusy(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [selectedSceneId, stockVideoTrimModal]);

  const selectedSceneAssetRows = selectedSceneId ? sceneAssets[selectedSceneId] || [] : [];

  const gallerySceneAssets = useMemo(() => {
    const rows = (selectedSceneAssetRows || []).filter((a) => a.status !== "rejected");
    return [...rows].sort((a, b) => {
      const sa = Number(a.timeline_sequence ?? 0);
      const sb = Number(b.timeline_sequence ?? 0);
      if (sa !== sb) return sa - sb;
      return new Date(a.created_at || 0) - new Date(b.created_at || 0);
    });
  }, [selectedSceneAssetRows]);

  const toggleAssetSelected = useCallback((id) => {
    const key = String(id);
    setSelectedAssetIds((prev) => {
      const next = new Set(Array.from(prev, String));
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });
  }, []);

  const selectAllAssets = useCallback(() => {
    setSelectedAssetIds(new Set(gallerySceneAssets.map((a) => String(a.id))));
  }, [gallerySceneAssets]);

  const rejectAllAssets = useCallback(async () => {
    const ids = gallerySceneAssets.map((a) => String(a.id));
    if (!ids.length) return;
    const ok = window.confirm(
      `Reject all ${ids.length} asset(s) for this scene? They will be hidden from the gallery (same as Reject on each card).`,
    );
    if (!ok) return;
    setSelectedAssetIds(new Set());
    setError("");
    let n = 0;
    for (const id of ids) {
      if (await rejectAsset(id, { quiet: true })) n += 1;
    }
    if (n > 0) setMessage(`Rejected ${n} asset(s).`);
  }, [gallerySceneAssets, rejectAsset, setError, setMessage]);

  const clearAssetSelection = useCallback(() => setSelectedAssetIds(new Set()), []);

  const bulkApproveAssets = useCallback(async () => {
    const ids = Array.from(selectedAssetIds, String);
    if (!ids.length) return;
    setSelectedAssetIds(new Set());
    setError("");
    let n = 0;
    for (const id of ids) {
      if (await approveAsset(id, { quiet: true })) n += 1;
    }
    if (n > 0) setMessage(`Approved ${n} asset(s).`);
  }, [selectedAssetIds, approveAsset, setError, setMessage]);

  const bulkRejectAssets = useCallback(async () => {
    const ids = Array.from(selectedAssetIds, String);
    if (!ids.length) return;
    setSelectedAssetIds(new Set());
    setError("");
    let n = 0;
    for (const id of ids) {
      if (await rejectAsset(id, { quiet: true })) n += 1;
    }
    if (n > 0) setMessage(`Rejected ${n} asset(s).`);
  }, [selectedAssetIds, rejectAsset, setError, setMessage]);

  useEffect(() => {
    setSelectedAssetIds(new Set());
  }, [selectedSceneId]);

  const selectedCoveredSec = useMemo(() => {
    if (!selectedSceneId) return 0;
    const rows = (sceneAssets[selectedSceneId] || []).filter(
      (a) => a.status === "succeeded" || a.status === "approved",
    );
    return rows.reduce((acc, a) => acc + estAssetCoverSec(a, sceneClipSec), 0);
  }, [selectedSceneId, sceneAssets, sceneClipSec]);

  const bestPreviewAsset = useMemo(() => {
    const rows = (selectedSceneAssetRows || []).filter((a) => a.status !== "rejected");
    if (pinnedPreviewAssetId) {
      const pinned = rows.find((r) => String(r.id) === String(pinnedPreviewAssetId));
      if (pinned && pinned.status === "succeeded") {
        return pinned;
      }
    }
    rows.sort((a, b) => {
      const as = a.status === "succeeded" ? 1 : 0;
      const bs = b.status === "succeeded" ? 1 : 0;
      if (bs !== as) return bs - as;
      const seq = Number(a.timeline_sequence ?? 0) - Number(b.timeline_sequence ?? 0);
      if (seq !== 0) return seq;
      const ta = new Date(a.created_at || 0).getTime();
      const tb = new Date(b.created_at || 0).getTime();
      return ta - tb;
    });
    const firstSucceeded = rows.find((r) => r.status === "succeeded");
    return firstSucceeded || null;
  }, [selectedSceneAssetRows, pinnedPreviewAssetId]);

  const moveSceneAssetInSequence = useCallback(
    (index, delta) => {
      if (!selectedSceneId) return;
      const next = [...gallerySceneAssets];
      const j = index + delta;
      if (j < 0 || j >= next.length) return;
      [next[index], next[j]] = [next[j], next[index]];
      void reorderSceneAssets(selectedSceneId, next.map((a) => a.id));
    },
    [selectedSceneId, gallerySceneAssets, reorderSceneAssets],
  );

  const previewUrl = useMemo(() => {
    if (!bestPreviewAsset?.id) return "";
    const v = bestPreviewAsset.updated_at || bestPreviewAsset.created_at || bestPreviewAsset.id;
    return apiAssetContentUrl(bestPreviewAsset.id, v);
  }, [bestPreviewAsset]);

  useEffect(() => {
    setPreviewMediaError(false);
  }, [previewUrl]);

  const previewKind = (bestPreviewAsset?.asset_type || "").toLowerCase();

  return {
    approveAsset,
    assetGenerationPrompt,
    bulkApproveAssets,
    bulkRejectAssets,
    clearAssetSelection,
    enhanceRetryImagePrompt,
    gallerySceneAssets,
    importSceneAssetFromStock,
    loadSceneAssets,
    mediaPreviewTab,
    mergeSceneAssetFromEvent,
    moveSceneAssetInSequence,
    pexelsImportKey,
    pexelsSearchBusy,
    pexelsSearchErr,
    pexelsSearchQuery,
    pexelsSearchResults,
    pexelsStockTab,
    pexelsTrimHint,
    pexelsTrimHintBusy,
    pinnedPreviewAssetId,
    postImage,
    previewKind,
    previewMediaError,
    previewUrl,
    promptEnhanceImageBusy,
    rejectAllAssets,
    rejectAsset,
    reorderSceneAssets,
    resetAssetsMediaSlice,
    retryPrompt,
    retryVideoPrompt,
    runSceneStockSearch,
    sceneAssets,
    sceneAssetsFetchError,
    sceneClipFileInputRef,
    sceneClipSec,
    sceneClipUploadKind,
    sceneStockLibrary,
    selectAllAssets,
    selectedAssetIds,
    selectedCoveredSec,
    setMediaPreviewTab,
    setPexelsSearchQuery,
    setPexelsStockTab,
    setPinnedPreviewAssetId,
    setPreviewMediaError,
    setRetryPrompt,
    setRetryVideoPrompt,
    setSceneClipUploadKind,
    setSceneStockLibrary,
    setStockVideoTrimModal,
    stockVideoTrimModal,
    toggleAssetSelected,
    uploadSceneClipFile,
  };
}

function expandedSceneOrFirst(scenes, expandedScene) {
  const sid = expandedScene || scenes[0]?.id || null;
  return sid ? String(sid) : null;
}
