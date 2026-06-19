import { useCallback, useEffect, useState } from "react";
import { api } from "../lib/api.js";
import {
  apiPostIdempotent,
  parseJson,
  apiErrorMessage,
  formatUserFacingError,
  humanizeErrorText,
} from "../lib/apiHelpers.js";
import { friendlyRunStatus } from "../lib/studio/pipelineHelpers.js";
import { usePollJob } from "./usePollJob.js";

/**
 * Character bible state + job polling for the Characters Studio tab.
 * Called from App so session persistence can read charactersJobId.
 */
export function useStudioCharacters({
  projectId,
  activePage,
  studioReady,
  jobPollIntervalMs,
  initialCharactersJobId = "",
  busy,
  setBusy,
  setError,
  setMessage,
  idem,
}) {
  const [projectCharacters, setProjectCharacters] = useState([]);
  const [charactersJobId, setCharactersJobId] = useState(initialCharactersJobId);

  const { job: charactersJob } = usePollJob(
    charactersJobId,
    Boolean(charactersJobId) && studioReady,
    jobPollIntervalMs,
  );

  const loadProjectCharacters = useCallback(async (pid) => {
    if (!pid) {
      setProjectCharacters([]);
      return;
    }
    const r = await api(`/v1/projects/${pid}/characters`);
    const body = await parseJson(r);
    if (r.ok) setProjectCharacters(body.data?.characters || []);
  }, []);

  const resetCharacters = useCallback(() => {
    setProjectCharacters([]);
    setCharactersJobId("");
  }, []);

  const generateFromStory = useCallback(async () => {
    if (!projectId) return;
    setBusy(true);
    setError("");
    try {
      const b = await apiPostIdempotent(api, `/v1/projects/${projectId}/characters/generate`, {}, idem);
      const jid = b.job?.id;
      if (jid) setCharactersJobId(jid);
      setMessage("Character agent job queued (replaces the current character list when it succeeds).");
    } catch (e) {
      setError(formatUserFacingError(e));
    } finally {
      setBusy(false);
    }
  }, [projectId, idem, setBusy, setError, setMessage]);

  const saveCharacter = useCallback(
    async (c) => {
      if (!projectId) return;
      setBusy(true);
      setError("");
      try {
        const r = await api(`/v1/projects/${projectId}/characters/${c.id}`, {
          method: "PATCH",
          body: JSON.stringify({
            name: c.name,
            sort_order: c.sort_order,
            role_in_story: c.role_in_story,
            visual_description: c.visual_description,
            time_place_scope_notes: c.time_place_scope_notes || null,
          }),
        });
        const b = await parseJson(r);
        if (!r.ok) throw new Error(apiErrorMessage(b));
        const row = b.data;
        if (row?.id) {
          setProjectCharacters((prev) => prev.map((x) => (x.id === row.id ? { ...x, ...row } : x)));
        }
        setMessage("Character saved.");
      } catch (e) {
        setError(formatUserFacingError(e));
      } finally {
        setBusy(false);
      }
    },
    [projectId, setBusy, setError, setMessage],
  );

  const deleteCharacter = useCallback(
    async (c) => {
      if (!projectId) return;
      if (!window.confirm(`Remove “${c.name}” from this project?`)) return;
      setBusy(true);
      setError("");
      try {
        const r = await api(`/v1/projects/${projectId}/characters/${c.id}`, { method: "DELETE" });
        const b = await parseJson(r);
        if (!r.ok) throw new Error(apiErrorMessage(b));
        setProjectCharacters((prev) => prev.filter((x) => x.id !== c.id));
        setMessage("Character removed.");
      } catch (e) {
        setError(formatUserFacingError(e));
      } finally {
        setBusy(false);
      }
    },
    [projectId, setBusy, setError, setMessage],
  );

  const addCharacter = useCallback(async () => {
    if (!projectId) return;
    setBusy(true);
    setError("");
    try {
      const r = await api(`/v1/projects/${projectId}/characters`, {
        method: "POST",
        body: JSON.stringify({ name: "New character" }),
      });
      const b = await parseJson(r);
      if (!r.ok) throw new Error(apiErrorMessage(b));
      const row = b.data;
      if (row?.id) setProjectCharacters((prev) => [...prev, row]);
      setMessage("Empty character added — edit and Save.");
    } catch (e) {
      setError(formatUserFacingError(e));
    } finally {
      setBusy(false);
    }
  }, [projectId, setBusy, setError, setMessage]);

  useEffect(() => {
    if (activePage !== "characters" || !projectId) return;
    void loadProjectCharacters(projectId);
  }, [activePage, projectId, loadProjectCharacters]);

  useEffect(() => {
    if (!charactersJobId || !charactersJob) return;
    const st = charactersJob.status;
    if (st !== "succeeded" && st !== "failed") return;
    setCharactersJobId("");
    if (st === "succeeded" && projectId) {
      void loadProjectCharacters(projectId);
      setMessage("Character bible updated from story.");
    } else if (st === "failed") {
      setError(humanizeErrorText(charactersJob.error_message || "Character generation failed."));
    }
  }, [charactersJob, charactersJobId, projectId, loadProjectCharacters, setError, setMessage]);

  return {
    projectCharacters,
    setProjectCharacters,
    charactersJobId,
    setCharactersJobId,
    charactersJob,
    loadProjectCharacters,
    generateFromStory,
    saveCharacter,
    deleteCharacter,
    addCharacter,
    resetCharacters,
    friendlyRunStatus,
  };
}
