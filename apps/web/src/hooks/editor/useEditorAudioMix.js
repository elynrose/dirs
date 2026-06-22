import { useCallback, useEffect, useRef, useState } from "react";
import { api, apiForm } from "../../lib/api.js";
import { DEFAULT_CLIP_CROSSFADE_SEC } from "../../lib/constants.js";
import {
  apiErrorMessage,
  formatUserFacingError,
  humanizeErrorText,
  parseJson,
} from "../../lib/apiHelpers.js";

const LS_STUDIO_MIX_MUSIC = "director_studio_default_mix_music_volume";
const LS_STUDIO_MIX_NARR = "director_studio_default_mix_narration_volume";

function readStoredDefaultMixVolumes() {
  try {
    const mm = localStorage.getItem(LS_STUDIO_MIX_MUSIC);
    const mn = localStorage.getItem(LS_STUDIO_MIX_NARR);
    return {
      mm:
        mm != null && mm !== ""
          ? Math.max(0, Math.min(1, Number.parseFloat(mm)))
          : null,
      mn:
        mn != null && mn !== ""
          ? Math.max(0, Math.min(4, Number.parseFloat(mn)))
          : null,
    };
  } catch {
    return { mm: null, mn: null };
  }
}

/**
 * Music beds, timeline mix sliders, and debounced mix persistence for the Editor.
 */
export function useEditorAudioMix({
  projectId,
  gatedProjectId,
  timelineVersionId,
  appConfig,
  appConfigRef,
  setAppConfig,
  setTimelineExportWarnings,
  busy,
  setBusy,
  setError,
  setMessage,
}) {
  const [musicBeds, setMusicBeds] = useState([]);
  const _mixVolInit = readStoredDefaultMixVolumes();
  const [mixMusicVol, setMixMusicVol] = useState(
    typeof _mixVolInit.mm === "number" && !Number.isNaN(_mixVolInit.mm) ? _mixVolInit.mm : 0.28,
  );
  const [mixNarrVol, setMixNarrVol] = useState(
    typeof _mixVolInit.mn === "number" && !Number.isNaN(_mixVolInit.mn) ? _mixVolInit.mn : 1,
  );
  const [narrMixMode, setNarrMixMode] = useState("scene_timeline");
  const [musicBedPick, setMusicBedPick] = useState("");
  const [clipCrossfadeSec, setClipCrossfadeSec] = useState(DEFAULT_CLIP_CROSSFADE_SEC);
  const [musicUploadLicense, setMusicUploadLicense] = useState("");
  const musicFileInputRef = useRef(null);
  const mixVolPersistTimerRef = useRef(null);
  const mixTimelineVolPersistTimerRef = useRef(null);

  const loadMusicBeds = useCallback(async () => {
    if (!gatedProjectId) {
      setMusicBeds([]);
      return;
    }
    try {
      const r = await api(`/v1/projects/${encodeURIComponent(gatedProjectId)}/music-beds`);
      const b = await parseJson(r);
      if (r.ok) setMusicBeds(Array.isArray(b.data) ? b.data : []);
    } catch {
      setMusicBeds([]);
    }
  }, [gatedProjectId]);

  const resolveStudioMixFallbacks = useCallback(() => {
    let fbMm = 0.28;
    let fbMn = 1;
    const mm = appConfig.studio_default_mix_music_volume;
    const mn = appConfig.studio_default_mix_narration_volume;
    if (typeof mm === "number" && !Number.isNaN(mm)) {
      fbMm = Math.max(0, Math.min(1, mm));
    } else {
      try {
        const ls = localStorage.getItem(LS_STUDIO_MIX_MUSIC);
        if (ls != null && ls !== "") {
          const v = Number.parseFloat(ls);
          if (!Number.isNaN(v)) fbMm = Math.max(0, Math.min(1, v));
        }
      } catch {
        /* ignore */
      }
    }
    if (typeof mn === "number" && !Number.isNaN(mn)) {
      fbMn = Math.max(0, Math.min(4, mn));
    } else {
      try {
        const ls = localStorage.getItem(LS_STUDIO_MIX_NARR);
        if (ls != null && ls !== "") {
          const v = Number.parseFloat(ls);
          if (!Number.isNaN(v)) fbMn = Math.max(0, Math.min(4, v));
        }
      } catch {
        /* ignore */
      }
    }
    return { fbMm, fbMn };
  }, [appConfig.studio_default_mix_music_volume, appConfig.studio_default_mix_narration_volume]);

  const loadTimelineMixFields = useCallback(async () => {
    const tid = String(timelineVersionId || "").trim();
    if (!tid) {
      setTimelineExportWarnings([]);
      return;
    }
    try {
      const r = await api(`/v1/timeline-versions/${encodeURIComponent(tid)}`);
      const b = await parseJson(r);
      if (!r.ok) return;
      const tj = b.data?.timeline_json;
      if (!tj || typeof tj !== "object") return;
      const ew = tj.export_warnings;
      setTimelineExportWarnings(Array.isArray(ew) ? ew.map((x) => String(x || "").trim()).filter(Boolean) : []);
      const { fbMm, fbMn } = resolveStudioMixFallbacks();
      const mmRaw = Number(tj.mix_music_volume);
      const mnRaw = Number(tj.mix_narration_volume);
      setMixMusicVol(Number.isFinite(mmRaw) ? Math.max(0, Math.min(1, mmRaw)) : fbMm);
      setMixNarrVol(Number.isFinite(mnRaw) ? Math.max(0, Math.min(4, mnRaw)) : fbMn);
      setNarrMixMode("scene_timeline");
      setMusicBedPick(tj.music_bed_id ? String(tj.music_bed_id) : "");
      setClipCrossfadeSec(
        typeof tj.clip_crossfade_sec === "number" && Number.isFinite(tj.clip_crossfade_sec)
          ? Math.max(0, Math.min(2, tj.clip_crossfade_sec))
          : DEFAULT_CLIP_CROSSFADE_SEC,
      );
    } catch {
      /* ignore */
    }
  }, [timelineVersionId, resolveStudioMixFallbacks, setTimelineExportWarnings]);

  const patchTimelineMixToServer = useCallback(
    async (opts = {}) => {
      const tid = String(timelineVersionId || "").trim();
      if (!projectId || !tid) {
        return { ok: false, error: "Set a timeline version ID first." };
      }
      const bedId =
        opts.musicBedIdOverride !== undefined
          ? opts.musicBedIdOverride
          : musicBedPick.trim()
            ? musicBedPick.trim()
            : null;
      try {
        const gr = await api(`/v1/timeline-versions/${encodeURIComponent(tid)}`);
        const gb = await parseJson(gr);
        if (!gr.ok) throw new Error(apiErrorMessage(gb));
        const prev = gb.data?.timeline_json;
        if (!prev || typeof prev !== "object") throw new Error("timeline_json missing");
        const mm = Number(mixMusicVol);
        const mn = Number(mixNarrVol);
        const next = {
          ...prev,
          mix_music_volume: Math.max(0, Math.min(1, Number.isFinite(mm) ? mm : 0)),
          mix_narration_volume: Math.max(0, Math.min(4, Number.isFinite(mn) ? mn : 1)),
          final_cut_narration_mode: narrMixMode,
          music_bed_id: bedId && String(bedId).trim() ? String(bedId).trim() : null,
          clip_crossfade_sec: Math.max(
            0,
            Math.min(
              2,
              Number.isFinite(Number(clipCrossfadeSec)) ? Number(clipCrossfadeSec) : DEFAULT_CLIP_CROSSFADE_SEC,
            ),
          ),
        };
        const pr = await api(`/v1/timeline-versions/${encodeURIComponent(tid)}`, {
          method: "PATCH",
          body: JSON.stringify({ timeline_json: next }),
        });
        const pb = await parseJson(pr);
        if (!pr.ok) throw new Error(apiErrorMessage(pb));
        return { ok: true };
      } catch (e) {
        return { ok: false, error: formatUserFacingError(e) };
      }
    },
    [projectId, timelineVersionId, mixMusicVol, mixNarrVol, narrMixMode, musicBedPick, clipCrossfadeSec],
  );

  const patchTimelineMixToServerRef = useRef(patchTimelineMixToServer);
  patchTimelineMixToServerRef.current = patchTimelineMixToServer;

  const scheduleDebouncedTimelineMixSave = useCallback(() => {
    if (!String(projectId || "").trim()) return;
    const tid = String(timelineVersionId || "").trim();
    if (!tid) return;
    if (mixTimelineVolPersistTimerRef.current) {
      clearTimeout(mixTimelineVolPersistTimerRef.current);
    }
    mixTimelineVolPersistTimerRef.current = setTimeout(() => {
      mixTimelineVolPersistTimerRef.current = null;
      void patchTimelineMixToServerRef.current();
    }, 500);
  }, [projectId, timelineVersionId]);

  const saveTimelineMixToServer = useCallback(async () => {
    if (!projectId || !String(timelineVersionId || "").trim()) {
      setError("Set a timeline version ID first.");
      return;
    }
    setBusy(true);
    setError("");
    try {
      const r = await patchTimelineMixToServer();
      if (!r.ok) throw new Error(r.error);
      setMessage("Timeline mix and transition settings saved.");
    } catch (e) {
      setError(formatUserFacingError(e));
    } finally {
      setBusy(false);
    }
  }, [projectId, timelineVersionId, patchTimelineMixToServer, setBusy, setError, setMessage]);

  const uploadMusicBedFile = useCallback(async () => {
    if (!projectId) return;
    const inp = musicFileInputRef.current;
    const f = inp?.files?.[0];
    if (!f) {
      setError("Choose an audio file first.");
      return;
    }
    const lic = musicUploadLicense.trim();
    if (lic.length < 2) {
      setError("Enter a license / source note for the music upload.");
      return;
    }
    setBusy(true);
    setError("");
    try {
      const fd = new FormData();
      fd.append("file", f);
      fd.append("title", f.name || "Uploaded music");
      fd.append("license_or_source_ref", lic);
      const r = await apiForm(`/v1/projects/${encodeURIComponent(projectId)}/music-beds/upload`, {
        method: "POST",
        body: fd,
      });
      const b = await parseJson(r);
      if (!r.ok) throw new Error(apiErrorMessage(b));
      const row = b.data;
      if (row?.id) setMusicBedPick(String(row.id));
      if (inp) inp.value = "";
      void loadMusicBeds();
      const tid = String(timelineVersionId || "").trim();
      if (tid && row?.id) {
        const sync = await patchTimelineMixToServer({ musicBedIdOverride: String(row.id) });
        if (sync.ok) {
          setMessage("Music uploaded and mix saved to timeline.");
        } else {
          setMessage("Music uploaded. Save mix to timeline failed — click Save mix.");
          setError(sync.error ? humanizeErrorText(sync.error) : "");
        }
      } else if (!tid) {
        setMessage("Music uploaded. Paste timeline version ID, then Save mix to timeline before final cut.");
      } else {
        setMessage("Music uploaded.");
      }
    } catch (e) {
      setError(formatUserFacingError(e));
    } finally {
      setBusy(false);
    }
  }, [
    projectId,
    musicUploadLicense,
    loadMusicBeds,
    timelineVersionId,
    patchTimelineMixToServer,
    setBusy,
    setError,
    setMessage,
  ]);

  const schedulePersistStudioMixDefaults = useCallback(
    (musicVol, narrVol) => {
      const mNum = Number(musicVol);
      const nNum = Number(narrVol);
      const mVol = Math.max(0, Math.min(1, Number.isFinite(mNum) ? mNum : 0));
      const nVol = Math.max(0, Math.min(4, Number.isFinite(nNum) ? nNum : 1));
      try {
        localStorage.setItem(LS_STUDIO_MIX_MUSIC, String(mVol));
        localStorage.setItem(LS_STUDIO_MIX_NARR, String(nVol));
      } catch {
        /* ignore */
      }
      if (mixVolPersistTimerRef.current) {
        clearTimeout(mixVolPersistTimerRef.current);
      }
      mixVolPersistTimerRef.current = setTimeout(async () => {
        mixVolPersistTimerRef.current = null;
        const base = appConfigRef.current;
        const next = {
          ...base,
          studio_default_mix_music_volume: mVol,
          studio_default_mix_narration_volume: nVol,
        };
        try {
          const r = await api("/v1/settings", { method: "PATCH", body: JSON.stringify({ config: next }) });
          const body = await parseJson(r);
          if (r.ok) {
            setAppConfig(body.data?.config || next);
          }
        } catch {
          /* offline or transient — values kept in localStorage */
        }
      }, 500);
    },
    [appConfigRef, setAppConfig],
  );

  useEffect(() => {
    void loadMusicBeds();
  }, [loadMusicBeds]);

  useEffect(() => {
    void loadTimelineMixFields();
  }, [loadTimelineMixFields]);

  useEffect(() => {
    return () => {
      if (mixVolPersistTimerRef.current) {
        clearTimeout(mixVolPersistTimerRef.current);
      }
      if (mixTimelineVolPersistTimerRef.current) {
        clearTimeout(mixTimelineVolPersistTimerRef.current);
      }
    };
  }, []);

  useEffect(() => {
    const mm = appConfig.studio_default_mix_music_volume;
    const mn = appConfig.studio_default_mix_narration_volume;
    if (typeof mm === "number" && !Number.isNaN(mm)) {
      const v = Math.max(0, Math.min(1, mm));
      setMixMusicVol(v);
      try {
        localStorage.setItem(LS_STUDIO_MIX_MUSIC, String(v));
      } catch {
        /* ignore */
      }
    }
    if (typeof mn === "number" && !Number.isNaN(mn)) {
      const v = Math.max(0, Math.min(4, mn));
      setMixNarrVol(v);
      try {
        localStorage.setItem(LS_STUDIO_MIX_NARR, String(v));
      } catch {
        /* ignore */
      }
    }
  }, [appConfig.studio_default_mix_music_volume, appConfig.studio_default_mix_narration_volume]);

  return {
    clipCrossfadeSec,
    loadTimelineMixFields,
    mixMusicVol,
    mixNarrVol,
    musicBedPick,
    musicBeds,
    musicFileInputRef,
    musicUploadLicense,
    patchTimelineMixToServer,
    saveTimelineMixToServer,
    scheduleDebouncedTimelineMixSave,
    schedulePersistStudioMixDefaults,
    setClipCrossfadeSec,
    setMixMusicVol,
    setMixNarrVol,
    setMusicBedPick,
    setMusicUploadLicense,
    uploadMusicBedFile,
  };
}
