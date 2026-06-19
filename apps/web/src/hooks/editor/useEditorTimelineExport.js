import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api, sanitizeStudioUuid } from "../../lib/api.js";
import { PHASE5_TIMELINE_UUID_RE } from "../../lib/constants.js";
import {
  apiErrorMessage,
  apiPostIdempotent,
  fetchProjectPhase5Readiness,
  formatUserFacingError,
  humanizeErrorText,
  parseJson,
  pollJobUntilTerminal,
} from "../../lib/apiHelpers.js";
import {
  buildPhase5ReadinessFetchOpts,
  friendlyReadinessIssue,
  pipelineStatusPollSnapshot,
} from "../../lib/studio/exportHelpers.js";

/**
 * Timeline version, phase-5 export readiness, rough/final compile, and pipeline-status polling.
 */
export function useEditorTimelineExport({
  bootTimelineVersionId = "",
  studioReady,
  projectId,
  gatedProjectId,
  pipelineMode,
  sseConnectedRef,
  jobPollIntervalMs,
  appConfig,
  run,
  setBusy,
  setError,
  setMessage,
  setMediaJobId,
  setMediaPoll,
  setActivePage,
  setExpandedScene,
  setTimelineExportWarnings,
  loadActiveProjectJobsRef,
  exportCollabRef,
  patchTimelineMixRef,
  idem,
}) {
  const [phase5Ready, setPhase5Ready] = useState(null);
  const [phase5ExportGateModal, setPhase5ExportGateModal] = useState(null);
  const [approveAllMediaBusy, setApproveAllMediaBusy] = useState(false);
  const [timelineVersionId, setTimelineVersionId] = useState(() => bootTimelineVersionId);
  const [burnSubtitlesOnFinalCut, setBurnSubtitlesOnFinalCut] = useState(false);
  const [trimByScene, setTrimByScene] = useState({});
  const [pipelineStatus, setPipelineStatus] = useState(null);

  const lastPhase5BundledRefreshRef = useRef(0);
  const phase5BundledPidRef = useRef("");
  const lastPolledLatestTimelineRef = useRef(null);
  const lastPipelineStatusPollSnapshotRef = useRef("");

  const phase5ReadinessFetchOpts = useMemo(
    () => buildPhase5ReadinessFetchOpts(pipelineMode, timelineVersionId, "rough_cut"),
    [pipelineMode, timelineVersionId],
  );

  const exportAttentionSceneIdSet = useMemo(() => {
    if (pipelineMode === "unattended") return new Set();
    const issues = phase5Ready?.issues || [];
    const wants = issues.some((i) =>
      [
        "missing_approved_scene_image",
        "missing_succeeded_scene_image",
        "timeline_asset_not_approved",
        "timeline_clip_not_visual_asset",
        "timeline_asset_rejected_or_failed",
        "timeline_asset_not_succeeded",
        "timeline_asset_file_missing",
      ].includes(i.code),
    );
    if (!wants) return new Set();
    const ids = new Set();
    for (const id of phase5Ready?.export_attention_scene_ids || []) ids.add(String(id));
    for (const row of phase5Ready?.export_attention_timeline_assets || []) {
      if (row?.scene_id) ids.add(String(row.scene_id));
    }
    return ids;
  }, [pipelineMode, phase5Ready]);

  const exportAttentionAssetIdSet = useMemo(() => {
    if (pipelineMode === "unattended") return new Set();
    const issues = phase5Ready?.issues || [];
    const wants = issues.some((i) =>
      [
        "timeline_asset_not_approved",
        "timeline_asset_not_in_project",
        "timeline_clip_not_visual_asset",
        "timeline_asset_rejected_or_failed",
        "timeline_asset_not_succeeded",
        "timeline_asset_file_missing",
      ].includes(i.code),
    );
    if (!wants) return new Set();
    const s = new Set();
    for (const row of phase5Ready?.export_attention_timeline_assets || []) {
      if (row?.asset_id) s.add(String(row.asset_id));
    }
    return s;
  }, [pipelineMode, phase5Ready]);

  const resetTimelineExportSlice = useCallback(() => {
    setPhase5Ready(null);
    setPhase5ExportGateModal(null);
    setTimelineVersionId("");
    setTrimByScene({});
    setPipelineStatus(null);
    setTimelineExportWarnings([]);
    lastPolledLatestTimelineRef.current = null;
    lastPipelineStatusPollSnapshotRef.current = "";
    lastPhase5BundledRefreshRef.current = 0;
    phase5BundledPidRef.current = "";
  }, [setTimelineExportWarnings]);

  const refreshPhase5Readiness = useCallback(
    async ({ pid = projectId, reportError = false, timelineVersionIdHint } = {}) => {
      if (!pid) return;
      const tvSource =
        timelineVersionIdHint !== undefined && timelineVersionIdHint !== null
          ? timelineVersionIdHint
          : timelineVersionId;
      const opts = buildPhase5ReadinessFetchOpts(pipelineMode, tvSource, "rough_cut");
      const { ok, body, data } = await fetchProjectPhase5Readiness(api, pid, opts);
      if (ok) setPhase5Ready(data);
      else if (reportError) setError(apiErrorMessage(body));
    },
    [projectId, pipelineMode, timelineVersionId, setError],
  );

  const loadPipelineStatus = useCallback(
    async (pid) => {
      if (!pid) {
        setPipelineStatus(null);
        return null;
      }
      try {
        const r = await api(`/v1/projects/${pid}/pipeline-status`);
        const body = await parseJson(r);
        if (r.ok && body.data) {
          const d = body.data;
          const ps = pipelineStatusPollSnapshot(d);
          if (ps !== lastPipelineStatusPollSnapshotRef.current) {
            lastPipelineStatusPollSnapshotRef.current = ps;
            setPipelineStatus(d);
          }
          const lid = d.latest_timeline_version_id;
          if (lid) {
            const prevLatest = lastPolledLatestTimelineRef.current;
            lastPolledLatestTimelineRef.current = lid;
            setTimelineVersionId((cur) => {
              const curTrim = cur && String(cur).trim() ? String(cur).trim() : "";
              if (!curTrim) return lid;
              if (prevLatest != null && lid !== prevLatest && curTrim === prevLatest) {
                return lid;
              }
              return curTrim;
            });
          }
          const pidS = String(pid);
          if (pidS !== phase5BundledPidRef.current) {
            phase5BundledPidRef.current = pidS;
            lastPhase5BundledRefreshRef.current = 0;
          }
          const now = Date.now();
          if (now - lastPhase5BundledRefreshRef.current >= 12_000) {
            lastPhase5BundledRefreshRef.current = now;
            await refreshPhase5Readiness({ pid, reportError: false, timelineVersionIdHint: lid || undefined });
          }
          return lid ? String(lid) : null;
        }
      } catch {
        /* ignore */
      }
      return null;
    },
    [refreshPhase5Readiness],
  );

  const queueRoughThenFinalCompile = useCallback(async () => {
    if (!projectId || !timelineVersionId) return;
    const tv = sanitizeStudioUuid(timelineVersionId);
    if (!tv || !PHASE5_TIMELINE_UUID_RE.test(tv)) {
      setError("Enter a valid timeline version UUID in the field above.");
      return;
    }
    const bodyBase = {
      timeline_version_id: tv,
      allow_unapproved_media: pipelineMode === "unattended",
    };
    const finalBody = {
      ...bodyBase,
      burn_subtitles_into_video: burnSubtitlesOnFinalCut,
    };
    const roughPath = `/v1/projects/${projectId}/rough-cut`;
    const finalPath = `/v1/projects/${projectId}/final-cut`;
    const pollOpts = {
      intervalMs: sseConnectedRef.current
        ? Math.min(60_000, Math.max(2_000, jobPollIntervalMs * 4))
        : jobPollIntervalMs,
      timeoutMs: 120 * 60 * 1000,
    };
    setBusy(true);
    setError("");
    try {
      setMessage("Rough cut running (step 1 of 2)…");
      const rb = await apiPostIdempotent(api, roughPath, bodyBase, idem);
      const rid = rb.job?.id;
      if (!rid) throw new Error("Rough cut did not return a job id.");
      setMediaJobId(rid);
      setMediaPoll(true);
      const r1 = await pollJobUntilTerminal(api, rid, pollOpts);
      void loadActiveProjectJobsRef.current?.();
      if (!r1.ok) {
        throw new Error(r1.job?.error_message || "Rough cut failed.");
      }
      const sync = await patchTimelineMixRef.current?.();
      if (!sync?.ok) {
        throw new Error(sync?.error ? humanizeErrorText(sync.error) : "Could not save mix to timeline before final cut.");
      }
      setMessage("Final cut running (step 2 of 2)…");
      const fb = await apiPostIdempotent(api, finalPath, finalBody, idem);
      const fid = fb.job?.id;
      if (!fid) throw new Error("Final cut did not return a job id.");
      setMediaJobId(fid);
      const r2 = await pollJobUntilTerminal(api, fid, pollOpts);
      void loadActiveProjectJobsRef.current?.();
      if (!r2.ok) {
        throw new Error(r2.job?.error_message || "Final cut failed.");
      }
      setMediaPoll(false);
      setMessage("Rough cut and final cut finished (full compile).");
    } catch (e) {
      setError(formatUserFacingError(e));
    } finally {
      setBusy(false);
    }
  }, [
    projectId,
    timelineVersionId,
    pipelineMode,
    idem,
    jobPollIntervalMs,
    sseConnectedRef,
    patchTimelineMixRef,
    loadActiveProjectJobsRef,
    burnSubtitlesOnFinalCut,
    setBusy,
    setError,
    setMessage,
    setMediaJobId,
    setMediaPoll,
  ]);

  const dismissPhase5ExportGateModal = useCallback(() => {
    setPhase5ExportGateModal(null);
  }, []);

  const reviewScenesForExportGate = useCallback(async () => {
    setPhase5ExportGateModal(null);
    setActivePage("editor");
    if (!projectId) return;
    const collab = exportCollabRef.current;
    const { ok, data } = await fetchProjectPhase5Readiness(api, projectId, phase5ReadinessFetchOpts);
    if (ok && data) {
      setPhase5Ready(data);
      const fromTimeline = data.export_attention_timeline_assets?.find((r) => r?.scene_id)?.scene_id;
      const first = data.export_attention_scene_ids?.[0] || fromTimeline;
      if (first) {
        const sid = String(first);
        setExpandedScene(sid);
        collab.setPinnedPreviewAssetId?.(null);
        await collab.loadSceneAssets?.(sid);
      }
      setMessage(
        first
          ? "Open the highlighted scene rows (or timeline clips) to fix the listed media, then re-run export."
          : "Use the Scenes list, timeline checklist, and pipeline panel to fix media, then check readiness and re-run export.",
      );
    }
  }, [
    projectId,
    phase5ReadinessFetchOpts,
    setActivePage,
    setExpandedScene,
    setMessage,
    exportCollabRef,
  ]);

  const approveAllSucceededMediaForExport = useCallback(async () => {
    if (!projectId) return;
    const collab = exportCollabRef.current;
    setApproveAllMediaBusy(true);
    setError("");
    try {
      const r = await api(`/v1/projects/${encodeURIComponent(projectId)}/assets/approve-all-succeeded`, {
        method: "POST",
      });
      const b = await parseJson(r);
      if (!r.ok) throw new Error(apiErrorMessage(b));
      const n = Number(b.data?.approved_count ?? 0);
      setPhase5ExportGateModal(null);
      setMessage(
        n > 0
          ? `Approved ${n} image(s) and video(s). When the export checklist is clear, queue Rough cut or Final cut again.`
          : "No unapproved succeeded images or videos were left to approve. Fix remaining timeline or generation issues, then retry export.",
      );
      await refreshPhase5Readiness({});
      const cid = collab.chapterId;
      if (cid) {
        collab.loadScenes?.(cid);
        collab.loadPhase3Summary?.(cid);
      }
      const sid = collab.sceneIdForAssetGalleryRefresh?.();
      if (sid) void collab.loadSceneAssets?.(sid);
    } catch (e) {
      setError(formatUserFacingError(e));
    } finally {
      setApproveAllMediaBusy(false);
    }
  }, [projectId, refreshPhase5Readiness, setError, setMessage, exportCollabRef]);

  const reconcileTimelineClipImages = useCallback(async () => {
    const pid = sanitizeStudioUuid(projectId);
    const tv = sanitizeStudioUuid(timelineVersionId);
    if (!pid || !tv) return;
    setBusy(true);
    setError("");
    try {
      const q = pipelineMode === "unattended" ? "?allow_unapproved_media=true" : "";
      const b = await apiPostIdempotent(
        api,
        `/v1/projects/${encodeURIComponent(pid)}/timeline-versions/${encodeURIComponent(tv)}/reconcile-clip-images${q}`,
        {},
        idem,
      );
      const d = b.data || {};
      const rel = Number(d.relinked_assets) || 0;
      const appr = Number(d.approved_scene_stills) || 0;
      const sync = Number(d.storyboard_synced_clips) || 0;
      const reb = Number(d.rebound_clips) || 0;
      const up = Number(d.updated_clips) || 0;
      const un = Number(d.unchanged_clips) || 0;
      const bits = [
        rel ? `${rel} asset row(s) relinked from storage paths` : null,
        appr ? `${appr} scene still(s) auto-approved on disk` : null,
        sync ? `${sync} timeline clip(s) aligned to storyboard order` : null,
        reb ? `${reb} orphan clip(s) rebound to scene media` : null,
        `${up} clip(s) reconciled to viable scene media`,
        `${un} unchanged`,
      ].filter(Boolean);
      setMessage(`${bits.join(" · ")}. Run Check readiness, then try export again.`);
      await refreshPhase5Readiness({ pid, timelineVersionIdHint: tv, reportError: true });
    } catch (e) {
      setError(formatUserFacingError(e));
    } finally {
      setBusy(false);
    }
  }, [projectId, timelineVersionId, pipelineMode, idem, refreshPhase5Readiness, setBusy, setError, setMessage]);

  const rejectAndRegenerateRoughCutImages = useCallback(async () => {
    const pid = sanitizeStudioUuid(projectId);
    const tv = sanitizeStudioUuid(timelineVersionId);
    if (!pid || !tv) return;
    const collab = exportCollabRef.current;
    setBusy(true);
    setError("");
    try {
      const q = pipelineMode === "unattended" ? "?allow_unapproved_media=true" : "";
      const b = await apiPostIdempotent(
        api,
        `/v1/projects/${encodeURIComponent(pid)}/timeline-versions/${encodeURIComponent(tv)}/reject-and-regenerate-rough-cut-images${q}`,
        {},
        idem,
      );
      const d = b.data || {};
      const n = (d.rejected_asset_ids || []).length;
      const m = (d.scene_ids_queued || []).length;
      setMessage(
        n || m
          ? `Rejected ${n} timeline image(s), queued ${m} scene image job(s). When jobs finish, use Reconcile timeline clips, then Check readiness.`
          : String(d.note || "Nothing matched rough-cut image repair rules."),
      );
      await refreshPhase5Readiness({ pid, timelineVersionIdHint: tv, reportError: false });
      void loadActiveProjectJobsRef.current?.();
      const cid = collab.chapterId;
      if (cid) collab.loadScenes?.(cid);
      const es = collab.expandedScene;
      if (es) collab.loadSceneAssets?.(es);
    } catch (e) {
      setError(formatUserFacingError(e));
    } finally {
      setBusy(false);
    }
  }, [
    projectId,
    timelineVersionId,
    pipelineMode,
    idem,
    refreshPhase5Readiness,
    loadActiveProjectJobsRef,
    exportCollabRef,
    setBusy,
    setError,
    setMessage,
  ]);

  useEffect(() => {
    lastPolledLatestTimelineRef.current = null;
  }, [projectId]);

  useEffect(() => {
    if (!gatedProjectId) {
      lastPipelineStatusPollSnapshotRef.current = "";
      setPipelineStatus(null);
      return undefined;
    }
    let cancelled = false;
    let timerId = 0;
    const poll = async () => {
      if (cancelled) return;
      await loadPipelineStatus(gatedProjectId);
      if (cancelled) return;
      const ms = sseConnectedRef.current ? 5000 : 2500;
      timerId = window.setTimeout(poll, ms);
    };
    void loadPipelineStatus(gatedProjectId);
    timerId = window.setTimeout(poll, sseConnectedRef.current ? 5000 : 2500);
    return () => {
      cancelled = true;
      window.clearTimeout(timerId);
    };
  }, [gatedProjectId, loadPipelineStatus, sseConnectedRef]);

  useEffect(() => {
    setBurnSubtitlesOnFinalCut(Boolean(appConfig.burn_subtitles_in_final_cut_default));
  }, [appConfig.burn_subtitles_in_final_cut_default]);

  useEffect(() => {
    if (!gatedProjectId || run?.status !== "blocked" || run?.block_code !== "CRITIC_GATE") return undefined;
    let cancelled = false;
    void (async () => {
      const { ok, data } = await fetchProjectPhase5Readiness(api, gatedProjectId, phase5ReadinessFetchOpts);
      if (!cancelled && ok) setPhase5Ready(data);
    })();
    return () => {
      cancelled = true;
    };
  }, [gatedProjectId, run?.status, run?.block_code, phase5ReadinessFetchOpts]);

  return {
    approveAllMediaBusy,
    approveAllSucceededMediaForExport,
    burnSubtitlesOnFinalCut,
    dismissPhase5ExportGateModal,
    exportAttentionAssetIdSet,
    exportAttentionSceneIdSet,
    friendlyReadinessIssue,
    loadPipelineStatus,
    phase5ExportGateModal,
    phase5ReadinessFetchOpts,
    phase5Ready,
    queueRoughThenFinalCompile,
    reconcileTimelineClipImages,
    refreshPhase5Readiness,
    rejectAndRegenerateRoughCutImages,
    resetTimelineExportSlice,
    reviewScenesForExportGate,
    setBurnSubtitlesOnFinalCut,
    setPhase5ExportGateModal,
    setPhase5Ready,
    setTimelineVersionId,
    setTrimByScene,
    timelineVersionId,
    trimByScene,
    pipelineStatus,
    setPipelineStatus,
  };
}
