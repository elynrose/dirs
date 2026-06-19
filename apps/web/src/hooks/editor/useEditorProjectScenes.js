import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api, sanitizeStudioUuid } from "../../lib/api.js";
import { apiErrorMessage, formatUserFacingError, parseJson } from "../../lib/apiHelpers.js";
import { STUDIO_MEDIA_JOB_TYPES } from "../../lib/constants.js";
import { chapterHumanNumber } from "../../lib/studio/sceneHelpers.js";

/** Compare GET /v1/projects payloads so silent polls skip setState when nothing changed. */
function projectsPollSnapshotFromRows(rows) {
  if (!Array.isArray(rows)) return "[]";
  return JSON.stringify(
    [...rows]
      .map((p) => ({
        id: String(p?.id ?? ""),
        title: String(p?.title ?? ""),
        status: String(p?.status ?? ""),
        workflow_phase: String(p?.workflow_phase ?? ""),
        ar: String(p?.active_agent_run_id ?? ""),
        ars: String(p?.active_agent_run_status ?? ""),
      }))
      .sort((a, b) => a.id.localeCompare(b.id)),
  );
}

const DEFAULT_TOPIC =
  "Urban community gardens and neighborhood food security in one mid-size city.";

/**
 * Project list, open project, chapters/scenes, and scene selection for the Editor.
 *
 * Cross-domain resets during open/delete/new-draft go through ``collabRef`` (filled by App
 * after asset/pipeline hooks exist — safe because openProject runs async).
 */
export function useEditorProjectScenes({
  studioReady,
  projectsListPollMs,
  bootProjectId = "",
  bootChapterId = "",
  bootExpandedScene = null,
  setError,
  setMessage,
  setBusy,
  collabRef,
}) {
  const [projects, setProjects] = useState([]);
  const lastProjectsPollSnapshotRef = useRef("");
  const [projectId, setProjectId] = useState(() => bootProjectId);
  const [title, setTitle] = useState("Agent run — test");
  const [topic, setTopic] = useState(DEFAULT_TOPIC);
  const [runtime, setRuntime] = useState(15);
  const [chapters, setChapters] = useState([]);
  const [chapterId, setChapterId] = useState(() => bootChapterId);
  const [scenes, setScenes] = useState([]);
  const [expandedScene, setExpandedScene] = useState(() => bootExpandedScene);
  const [scenesLoading, setScenesLoading] = useState(false);
  const openProjectRef = useRef(async () => {});
  /** Bumped when chapter/scenes reset so in-flight `loadScenes` cannot overwrite a newer selection. */
  const loadScenesEpochRef = useRef(0);
  const openProjectInFlightRef = useRef(false);

  const invalidateSceneLoads = useCallback(() => {
    loadScenesEpochRef.current += 1;
  }, []);

  const gatedProjectId = studioReady ? projectId : "";

  const loadChapters = useCallback(async (pid) => {
    if (!pid) return;
    const r = await api(`/v1/projects/${pid}/chapters`);
    const body = await parseJson(r);
    if (r.ok) setChapters(body.data?.chapters || []);
  }, []);

  const loadProjects = useCallback(async (opts) => {
    const silent = Boolean(opts && typeof opts === "object" && opts.silent);
    try {
      const pageLimit = 200;
      let offset = 0;
      let all = [];
      let total = null;
      for (;;) {
        const r = await api(`/v1/projects?limit=${pageLimit}&offset=${offset}`);
        const body = await parseJson(r);
        if (!r.ok) {
          if (!silent) {
            setError(apiErrorMessage(body, r.status) || `Could not load projects (HTTP ${r.status}).`);
            lastProjectsPollSnapshotRef.current = projectsPollSnapshotFromRows([]);
            setProjects([]);
          }
          return;
        }
        const chunk = body.data?.projects || [];
        if (total === null) {
          const tc = body.data?.total_count;
          total = typeof tc === "number" && Number.isFinite(tc) ? tc : chunk.length;
        }
        all = all.concat(chunk);
        if (all.length >= total || chunk.length < pageLimit || offset > 100_000) break;
        offset += pageLimit;
      }
      const snap = projectsPollSnapshotFromRows(all);
      if (silent && snap === lastProjectsPollSnapshotRef.current) {
        return;
      }
      lastProjectsPollSnapshotRef.current = snap;
      setProjects(all);
    } catch (e) {
      if (!silent) {
        const net =
          e instanceof TypeError || String(e).toLowerCase().includes("fetch") || String(e).includes("NetworkError");
        const hint = String(e?.message || e || "").trim();
        setError(
          net
            ? [
                "Could not reach the API to load projects (network error before any HTTP response).",
                hint ? `Details: ${hint}` : null,
                "Check: (1) API is running on the host you expect. (2) If you set VITE_API_BASE_URL=http://127.0.0.1:8000 and open the Studio from another device or https://, the browser cannot reach that URL—leave VITE_API_BASE_URL unset and use the Vite/nginx same-origin /v1 proxy, or point it at a reachable API and set CORS_EXTRA_ORIGINS.",
                "Electron: wait until Docker + API finish starting.",
              ]
                .filter(Boolean)
                .join(" ")
            : formatUserFacingError(e),
        );
        lastProjectsPollSnapshotRef.current = projectsPollSnapshotFromRows([]);
        setProjects([]);
      }
    }
  }, [setError]);

  const loadScenes = useCallback(async (cid) => {
    if (!cid) return;
    const epoch = loadScenesEpochRef.current;
    setScenesLoading(true);
    try {
      const r = await api(`/v1/chapters/${cid}/scenes`);
      const body = await parseJson(r);
      if (epoch !== loadScenesEpochRef.current) return;
      if (r.ok) setScenes(body.data?.scenes || []);
    } finally {
      if (epoch === loadScenesEpochRef.current) {
        setScenesLoading(false);
      }
    }
  }, []);

  const resetProjectSlice = useCallback(() => {
    invalidateSceneLoads();
    setProjectId("");
    setChapters([]);
    setChapterId("");
    setScenes([]);
    setExpandedScene(null);
    setTitle("New documentary");
    setTopic("Describe your topic, audience, and the story you want to tell.");
    setRuntime(15);
  }, [invalidateSceneLoads]);

  const reorderScenes = useCallback((fromId, toId) => {
    if (!fromId || !toId || fromId === toId) return;
    setScenes((prev) => {
      const from = prev.findIndex((s) => s.id === fromId);
      const to = prev.findIndex((s) => s.id === toId);
      if (from < 0 || to < 0) return prev;
      const next = [...prev];
      const [m] = next.splice(from, 1);
      next.splice(to, 0, m);
      return next.map((s, i) => ({ ...s, order_index: i }));
    });
  }, []);

  const goToChapterScene = useCallback(
    async (chId, sceneIdToFocus) => {
      if (!chId) return;
      const collab = collabRef.current;
      invalidateSceneLoads();
      const epoch = loadScenesEpochRef.current;
      const r = await api(`/v1/chapters/${chId}/scenes`);
      const body = await parseJson(r);
      if (epoch !== loadScenesEpochRef.current) return;
      if (r.ok) {
        const next = body.data?.scenes || [];
        setChapterId(chId);
        setScenes(next);
        if (sceneIdToFocus && next.some((s) => s.id === sceneIdToFocus)) {
          setExpandedScene(sceneIdToFocus);
          collab.loadSceneAssets?.(sceneIdToFocus);
        } else if (next[0]?.id) {
          setExpandedScene(next[0].id);
          collab.loadSceneAssets?.(next[0].id);
        } else {
          setExpandedScene(null);
        }
      }
    },
    [collabRef, invalidateSceneLoads],
  );

  const openProject = useCallback(
    async (pid, restore = null) => {
      const id = sanitizeStudioUuid(pid);
      if (!id) return;
      const collab = collabRef.current;
      setBusy(true);
      setError("");
      invalidateSceneLoads();
      openProjectInFlightRef.current = true;
      setProjectId(id);
      collab.onOpenProjectStart?.(restore);
      setChapterId("");
      setScenes([]);
      setExpandedScene(null);
      try {
        const pe = encodeURIComponent(id);
        const pr = await api(`/v1/projects/${pe}`);
        const pb = await parseJson(pr);
        if (!pr.ok) {
          const msg = apiErrorMessage(pb);
          const hint404 =
            pr.status === 404
              ? " Wrong workspace? Open the tenant where this project was created (workspace / account switcher), then choose the project again."
              : "";
          throw new Error(`${msg}${hint404}`);
        }
        const p = pb.data || {};
        setTitle(p.title || "");
        setTopic(p.topic || "");
        setRuntime(Number(p.target_runtime_minutes || 15));
        collab.onOpenProjectLoadedMeta?.(p);

        const cr = await api(`/v1/projects/${pe}/chapters`);
        const cb = await parseJson(cr);
        if (!cr.ok) {
          throw new Error(apiErrorMessage(cb) || `Chapters request failed (HTTP ${cr.status})`);
        }
        const nextChapters = cb.data?.chapters || [];
        setChapters(nextChapters);

        const savedCid =
          restore?.chapterId && nextChapters.some((c) => c.id === restore.chapterId)
            ? restore.chapterId
            : "";
        const pickChapter = savedCid || nextChapters[0]?.id || "";
        if (pickChapter) {
          const sr = await api(`/v1/chapters/${pickChapter}/scenes`);
          const sb = await parseJson(sr);
          if (sr.ok) {
            const nextScenes = sb.data?.scenes || [];
            const savedSid =
              restore?.expandedScene && nextScenes.some((s) => s.id === restore.expandedScene)
                ? restore.expandedScene
                : null;
            const pickScene = savedSid || nextScenes[0]?.id || null;
            setChapterId(pickChapter);
            setScenes(nextScenes);
            if (pickScene) {
              setExpandedScene(pickScene);
              collab.loadSceneAssets?.(pickScene);
            } else {
              setExpandedScene(null);
            }
          } else {
            setChapterId(pickChapter);
          }
        } else {
          setChapterId("");
        }

        await collab.refreshPhase5Readiness?.({ pid: id });

        const keepAgent = Boolean(restore?.agentRunId && String(restore.agentRunId).length >= 32);
        if (keepAgent) {
          collab.onRestoreAgentRun?.(restore.agentRunId);
        }

        try {
          const jr = await api(`/v1/projects/${pe}/jobs/active`);
          const jb = await parseJson(jr);
          if (jr.ok) {
            collab.onOpenProjectActiveJobs?.(jb.data?.jobs || [], restore && typeof restore === "object" ? restore : {});
          }
        } catch {
          /* non-fatal */
        }
      } catch (e) {
        const em = formatUserFacingError(e);
        setError(em);
        resetProjectSlice();
        collab.onOpenProjectFailed?.();
      } finally {
        openProjectInFlightRef.current = false;
        setBusy(false);
      }
    },
    [collabRef, invalidateSceneLoads, resetProjectSlice, setBusy, setError],
  );

  openProjectRef.current = openProject;

  const onChatStudioProjectOpen = useCallback(
    (id) => {
      const clean = sanitizeStudioUuid(id);
      if (!clean || clean === String(projectId || "").trim()) return;
      void openProjectRef.current(clean);
    },
    [projectId],
  );

  const deleteProject = useCallback(
    async (pid) => {
      if (!pid) return;
      const ok = window.confirm(
        "Delete this project? This removes it from the studio and deletes generated media on disk (scene assets, narrations). Files under the exports folder for this project are kept. This cannot be undone.",
      );
      if (!ok) return;
      const collab = collabRef.current;
      setBusy(true);
      setError("");
      try {
        const r = await api(`/v1/projects/${pid}`, { method: "DELETE" });
        const body = await parseJson(r);
        if (!r.ok) {
          throw new Error(apiErrorMessage(body) || "delete failed");
        }
        if (projectId === pid) {
          resetProjectSlice();
          collab.onClearCurrentProjectExtras?.();
          collab.resetCharacters?.();
        }
        await loadProjects();
        setMessage("Project deleted.");
      } catch (e) {
        setError(formatUserFacingError(e));
      } finally {
        setBusy(false);
      }
    },
    [collabRef, loadProjects, projectId, resetProjectSlice, setBusy, setError, setMessage],
  );

  const startNewProjectDraft = useCallback(() => {
    const collab = collabRef.current;
    setError("");
    setMessage("");
    resetProjectSlice();
    collab.onStartNewProjectDraftExtras?.();
    collab.resetCharacters?.();
  }, [collabRef, resetProjectSlice, setError, setMessage]);

  const sceneIdForAssetGalleryRefresh = useCallback(
    () => String(expandedScene || scenes[0]?.id || "").trim(),
    [expandedScene, scenes],
  );

  const chapterTitleForId = useCallback(
    (cid) => {
      const ch = chapters.find((c) => String(c.id) === String(cid));
      if (!ch) return `Chapter id ${String(cid).slice(0, 8)}…`;
      const n = chapterHumanNumber(chapters, ch);
      return `Chapter ${n ?? "?"}: ${ch.title || "(untitled)"}`;
    },
    [chapters],
  );

  const sceneLabelForId = useCallback(
    (sid, chapterIdHint = null) => {
      const sidS = String(sid || "");
      const local = scenes.find((x) => String(x.id) === sidS);
      if (local) return `Scene ${local.order_index + 1}`;
      if (chapterIdHint) return `Scene id ${sidS.slice(0, 8)}… (${chapterTitleForId(chapterIdHint)})`;
      return `Scene id ${sidS.slice(0, 8)}…`;
    },
    [scenes, chapterTitleForId],
  );

  const selectedSceneId = expandedScene || scenes[0]?.id || "";
  const selectedScene = useMemo(
    () => scenes.find((s) => String(s.id) === String(selectedSceneId)) || null,
    [scenes, selectedSceneId],
  );

  useEffect(() => {
    if (!studioReady) return;
    void loadProjects().catch((e) => setError(formatUserFacingError(e)));
  }, [studioReady, loadProjects, setError]);

  useEffect(() => {
    if (!studioReady) return undefined;
    const tick = () => {
      if (typeof document !== "undefined" && document.visibilityState !== "visible") return;
      void loadProjects({ silent: true });
    };
    const id = window.setInterval(tick, projectsListPollMs);
    const onVis = () => {
      if (document.visibilityState === "visible") void loadProjects({ silent: true });
    };
    document.addEventListener("visibilitychange", onVis);
    return () => {
      window.clearInterval(id);
      document.removeEventListener("visibilitychange", onVis);
    };
  }, [studioReady, loadProjects, projectsListPollMs]);

  useEffect(() => {
    if (!studioReady || !chapterId || openProjectInFlightRef.current) return;
    loadScenes(chapterId);
  }, [studioReady, chapterId, loadScenes]);

  useEffect(() => {
    if (!chapterId || scenes.length === 0) return;
    const cur = expandedScene != null && String(expandedScene).trim() !== "" ? String(expandedScene) : "";
    const inChapter = cur && scenes.some((s) => String(s.id) === cur);
    if (inChapter) return;
    const first = scenes[0]?.id;
    if (!first) return;
    setExpandedScene(first);
    collabRef.current.loadSceneAssets?.(String(first));
  }, [chapterId, scenes, expandedScene, collabRef]);

  return {
    chapterId,
    chapters,
    chapterTitleForId,
    deleteProject,
    expandedScene,
    gatedProjectId,
    goToChapterScene,
    loadChapters,
    loadProjects,
    loadScenes,
    onChatStudioProjectOpen,
    openProject,
    openProjectRef,
    projectId,
    projects,
    reorderScenes,
    resetProjectSlice,
    runtime,
    sceneIdForAssetGalleryRefresh,
    sceneLabelForId,
    scenes,
    scenesLoading,
    selectedScene,
    selectedSceneId,
    setChapterId,
    setChapters,
    setExpandedScene,
    setProjectId,
    setRuntime,
    setScenes,
    setTitle,
    setTopic,
    startNewProjectDraft,
    title,
    topic,
  };
}
