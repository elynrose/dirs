import { useCallback, useEffect, useState } from "react";
import { api } from "../lib/api.js";
import {
  apiPostIdempotent,
  parseJson,
  apiErrorMessage,
  formatUserFacingError,
} from "../lib/apiHelpers.js";

/** Research dossier + chapter script editing for the Research & scripts tab. */
export function useStudioResearch({
  projectId,
  activePage,
  activeProjectJobs,
  setChapters,
  setMessage,
  setError,
  idem,
}) {
  const [researchJsonDraft, setResearchJsonDraft] = useState("");
  const [researchMeta, setResearchMeta] = useState(null);
  const [researchPageBusy, setResearchPageBusy] = useState(false);
  const [researchPageErr, setResearchPageErr] = useState("");
  const [researchPipelineBusy, setResearchPipelineBusy] = useState(false);
  const [chapterRegenerateId, setChapterRegenerateId] = useState("");
  const [chapterScriptsDraft, setChapterScriptsDraft] = useState({});

  const resetResearch = useCallback(() => {
    setResearchJsonDraft("");
    setResearchMeta(null);
    setResearchPageErr("");
    setResearchPipelineBusy(false);
    setChapterRegenerateId("");
    setChapterScriptsDraft({});
  }, []);

  const loadResearchChaptersEditor = useCallback(
    async (pid) => {
      if (!pid) return;
      setResearchPageBusy(true);
      setResearchPageErr("");
      try {
        const [rr, cr] = await Promise.all([api(`/v1/projects/${pid}/research`), api(`/v1/projects/${pid}/chapters`)]);
        const rb = await parseJson(rr);
        const cb = await parseJson(cr);
        if (!rr.ok) throw new Error(apiErrorMessage(rb) || `Research HTTP ${rr.status}`);
        if (!cr.ok) throw new Error(apiErrorMessage(cb) || `Chapters HTTP ${cr.status}`);
        const data = rb.data || {};
        const dossier = data.dossier;
        const body = dossier?.body;
        setResearchJsonDraft(body !== undefined && body !== null ? JSON.stringify(body, null, 2) : "{}");
        setResearchMeta({
          script_gate_open: Boolean(data.script_gate_open),
          dossier,
          sourceCount: Array.isArray(data.sources) ? data.sources.length : 0,
          claimCount: Array.isArray(data.claims) ? data.claims.length : 0,
        });
        const list = cb.data?.chapters || [];
        setChapters(list);
        const drafts = {};
        for (const ch of list) {
          drafts[ch.id] = {
            title: ch.title ?? "",
            summary: ch.summary ?? "",
            target_duration_sec: ch.target_duration_sec != null ? String(ch.target_duration_sec) : "",
            script_text: ch.script_text ?? "",
          };
        }
        setChapterScriptsDraft(drafts);
      } catch (e) {
        setResearchPageErr(formatUserFacingError(e));
        setResearchJsonDraft("");
        setResearchMeta(null);
        setChapterScriptsDraft({});
      } finally {
        setResearchPageBusy(false);
      }
    },
    [setChapters],
  );

  const rerunResearch = useCallback(async () => {
    if (!projectId) return;
    setResearchPipelineBusy(true);
    setResearchPageErr("");
    try {
      await apiPostIdempotent(api, `/v1/projects/${projectId}/research/run`, {}, idem);
      setMessage("Research rerun queued — worker will refresh the dossier. Reload this tab when the job finishes.");
    } catch (e) {
      setResearchPipelineBusy(false);
      setResearchPageErr(formatUserFacingError(e));
    }
  }, [projectId, idem, setMessage]);

  const saveDossier = useCallback(async () => {
    if (!projectId || !researchMeta?.dossier) return;
    let parsed;
    try {
      parsed = JSON.parse(researchJsonDraft || "{}");
    } catch (e) {
      setResearchPageErr(`Invalid JSON: ${e instanceof Error ? e.message : String(e)}`);
      return;
    }
    if (parsed === null || typeof parsed !== "object" || Array.isArray(parsed)) {
      setResearchPageErr("Dossier body must be a JSON object.");
      return;
    }
    setResearchPageBusy(true);
    setResearchPageErr("");
    try {
      const r = await api(`/v1/projects/${projectId}/research/body`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ body: parsed }),
      });
      const b = await parseJson(r);
      if (!r.ok) throw new Error(apiErrorMessage(b) || `HTTP ${r.status}`);
      setMessage("Research dossier saved.");
      await loadResearchChaptersEditor(projectId);
    } catch (e) {
      setResearchPageErr(formatUserFacingError(e));
    } finally {
      setResearchPageBusy(false);
    }
  }, [projectId, researchMeta, researchJsonDraft, loadResearchChaptersEditor, setMessage]);

  const regenerateChapterScript = useCallback(
    async (ch, d) => {
      const notes = (d.summary || "").trim();
      if (notes.length < 8) {
        setResearchPageErr("Chapter summary must be at least 8 characters to regenerate (use it as editorial direction).");
        return;
      }
      setResearchPageErr("");
      setChapterRegenerateId(ch.id);
      try {
        await apiPostIdempotent(api, `/v1/chapters/${ch.id}/script/regenerate`, { enhancement_notes: notes }, idem);
        setMessage(`Chapter “${(d.title || ch.title || "").trim() || ch.id}” script regeneration queued.`);
      } catch (e) {
        setChapterRegenerateId("");
        setResearchPageErr(formatUserFacingError(e));
      }
    },
    [idem, setMessage],
  );

  const saveChapter = useCallback(
    async (ch, d) => {
      const title = (d.title || "").trim();
      if (!title) {
        setResearchPageErr("Chapter title cannot be empty.");
        return;
      }
      let target_duration_sec = null;
      const ts = (d.target_duration_sec || "").trim();
      if (ts !== "") {
        const n = Number(ts);
        if (!Number.isFinite(n) || n < 30 || n > 7200) {
          setResearchPageErr("Target duration must be between 30 and 7200 seconds, or leave blank.");
          return;
        }
        target_duration_sec = Math.round(n);
      }
      setResearchPageBusy(true);
      setResearchPageErr("");
      try {
        const r = await api(`/v1/chapters/${ch.id}`, {
          method: "PATCH",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            title,
            summary: d.summary,
            target_duration_sec,
            script_text: d.script_text,
          }),
        });
        const b = await parseJson(r);
        if (!r.ok) throw new Error(apiErrorMessage(b) || `HTTP ${r.status}`);
        const row = b.data;
        setMessage(`Chapter “${title}” saved.`);
        if (row?.id) {
          setChapters((prev) => prev.map((c) => (c.id === row.id ? { ...c, ...row } : c)));
        }
      } catch (e) {
        setResearchPageErr(formatUserFacingError(e));
      } finally {
        setResearchPageBusy(false);
      }
    },
    [setChapters, setMessage],
  );

  useEffect(() => {
    if (activePage !== "research_chapters" || !projectId) return;
    void loadResearchChaptersEditor(projectId);
  }, [activePage, projectId, loadResearchChaptersEditor]);

  useEffect(() => {
    if (!researchPipelineBusy || !Array.isArray(activeProjectJobs)) return;
    const busy = activeProjectJobs.some(
      (j) => j && j.type === "research_run" && (j.status === "queued" || j.status === "running"),
    );
    if (!busy) setResearchPipelineBusy(false);
  }, [activeProjectJobs, researchPipelineBusy]);

  useEffect(() => {
    if (!chapterRegenerateId || !Array.isArray(activeProjectJobs)) return;
    const busy = activeProjectJobs.some(
      (j) => j && j.type === "script_chapter_regenerate" && (j.status === "queued" || j.status === "running"),
    );
    if (!busy) {
      setChapterRegenerateId("");
      if (projectId) void loadResearchChaptersEditor(projectId);
    }
  }, [activeProjectJobs, chapterRegenerateId, projectId, loadResearchChaptersEditor]);

  return {
    researchJsonDraft,
    setResearchJsonDraft,
    researchMeta,
    researchPageBusy,
    researchPageErr,
    researchPipelineBusy,
    chapterRegenerateId,
    chapterScriptsDraft,
    setChapterScriptsDraft,
    loadResearchChaptersEditor,
    rerunResearch,
    saveDossier,
    regenerateChapterScript,
    saveChapter,
    resetResearch,
  };
}
