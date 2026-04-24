import { useCallback, useEffect, useState } from "react";

import { api } from "../lib/api.js";
import { parseJson, apiErrorMessage, formatUserFacingError } from "../lib/apiHelpers.js";

function toIsoWithOffset(dtLocal) {
  if (!dtLocal) return null;
  const d = new Date(dtLocal);
  if (Number.isNaN(d.getTime())) return null;
  return d.toISOString();
}

export function StudioIdeasPage({
  showToast,
  loadProjects,
  setAgentRunId,
  setProjectId,
  setActivePage,
}) {
  const [topic, setTopic] = useState("");
  const [generating, setGenerating] = useState(false);
  const [generated, setGenerated] = useState([]);
  const [saved, setSaved] = useState([]);
  const [schedules, setSchedules] = useState([]);
  const [loadingSaved, setLoadingSaved] = useState(true);
  const [runtimeMinutes, setRuntimeMinutes] = useState(10);
  const [scheduleInputs, setScheduleInputs] = useState({});
  const [deletingId, setDeletingId] = useState(null);

  const loadSaved = useCallback(async () => {
    setLoadingSaved(true);
    try {
      const r = await api("/v1/ideas");
      const body = await parseJson(r);
      if (!r.ok) throw new Error(apiErrorMessage(body));
      setSaved(Array.isArray(body.data) ? body.data : []);
    } catch (e) {
      showToast(formatUserFacingError(e), { type: "error" });
      setSaved([]);
    } finally {
      setLoadingSaved(false);
    }
  }, [showToast]);

  const loadSchedules = useCallback(async () => {
    try {
      const r = await api("/v1/ideas/schedules/list");
      const body = await parseJson(r);
      if (!r.ok) throw new Error(apiErrorMessage(body));
      setSchedules(Array.isArray(body.data) ? body.data : []);
    } catch {
      setSchedules([]);
    }
  }, []);

  useEffect(() => {
    void loadSaved();
    void loadSchedules();
  }, [loadSaved, loadSchedules]);

  const onGenerate = async () => {
    const t = topic.trim();
    if (t.length < 2) {
      showToast("Enter a topic (at least 2 characters).", { type: "warning" });
      return;
    }
    setGenerating(true);
    setGenerated([]);
    try {
      const r = await api("/v1/ideas/generate", {
        method: "POST",
        body: JSON.stringify({ topic: t }),
      });
      const body = await parseJson(r);
      if (!r.ok) throw new Error(apiErrorMessage(body));
      const ideas = body.data?.ideas;
      if (!Array.isArray(ideas) || ideas.length === 0) throw new Error("No ideas returned");
      setGenerated(ideas.map((x, i) => ({ ...x, _key: `g-${i}-${Date.now()}` })));
      showToast(`Generated ${ideas.length} ideas.`, { type: "success" });
    } catch (e) {
      showToast(formatUserFacingError(e), { type: "error" });
    } finally {
      setGenerating(false);
    }
  };

  const applyRunResponse = async (r, body) => {
    if (!r.ok) throw new Error(apiErrorMessage(body));
    const ar = body.data?.agent_run;
    const proj = body.data?.project;
    if (proj?.id) setProjectId(proj.id);
    if (ar?.id) {
      setAgentRunId(ar.id);
      showToast("Pipeline started — opening the editor.", { type: "success" });
      setActivePage("editor");
    }
    void loadProjects();
  };

  const onSave = async (title, description) => {
    try {
      const r = await api("/v1/ideas", {
        method: "POST",
        body: JSON.stringify({
          title,
          description,
          source_topic: topic.trim() || title,
        }),
      });
      const body = await parseJson(r);
      if (!r.ok) throw new Error(apiErrorMessage(body));
      showToast("Idea saved.", { type: "success" });
      void loadSaved();
    } catch (e) {
      showToast(formatUserFacingError(e), { type: "error" });
    }
  };

  const onRunGenerated = async (title, description) => {
    try {
      const r = await api("/v1/ideas/run-instant", {
        method: "POST",
        body: JSON.stringify({
          title,
          description,
          target_runtime_minutes: Number(runtimeMinutes) || 10,
        }),
      });
      const body = await parseJson(r);
      await applyRunResponse(r, body);
    } catch (e) {
      showToast(formatUserFacingError(e), { type: "error" });
    }
  };

  const onRunSaved = async (ideaId) => {
    try {
      const r = await api(`/v1/ideas/${encodeURIComponent(ideaId)}/run`, {
        method: "POST",
        body: JSON.stringify({ target_runtime_minutes: Number(runtimeMinutes) || 10 }),
      });
      const body = await parseJson(r);
      await applyRunResponse(r, body);
    } catch (e) {
      showToast(formatUserFacingError(e), { type: "error" });
    }
  };

  const onScheduleSaved = async (ideaId) => {
    const raw = scheduleInputs[ideaId];
    const iso = toIsoWithOffset(raw);
    if (!iso) {
      showToast("Pick a date and time for the scheduled run.", { type: "warning" });
      return;
    }
    try {
      const r = await api(`/v1/ideas/${encodeURIComponent(ideaId)}/schedule`, {
        method: "POST",
        body: JSON.stringify({ scheduled_at: iso }),
      });
      const body = await parseJson(r);
      if (!r.ok) throw new Error(apiErrorMessage(body));
      showToast("Scheduled — the pipeline will start at that time (Celery beat must be running).", {
        type: "success",
        durationMs: 8000,
      });
      void loadSchedules();
    } catch (e) {
      showToast(formatUserFacingError(e), { type: "error" });
    }
  };

  const onCancelSchedule = async (scheduleId) => {
    try {
      const r = await api(`/v1/ideas/schedules/${encodeURIComponent(scheduleId)}`, { method: "DELETE" });
      const body = await parseJson(r);
      if (!r.ok) throw new Error(apiErrorMessage(body));
      showToast("Schedule cancelled.", { type: "success" });
      void loadSchedules();
    } catch (e) {
      showToast(formatUserFacingError(e), { type: "error" });
    }
  };

  const onDeleteSaved = async (ideaId) => {
    const id = String(ideaId || "").trim();
    if (!id) return;
    const ok = window.confirm(
      "Delete this saved idea? Any pending schedules for it are removed as well. This cannot be undone.",
    );
    if (!ok) return;
    setDeletingId(id);
    try {
      const r = await api(`/v1/ideas/${encodeURIComponent(id)}`, { method: "DELETE" });
      const body = await parseJson(r);
      if (!r.ok) throw new Error(apiErrorMessage(body));
      showToast("Saved idea deleted.", { type: "success" });
      setScheduleInputs((prev) => {
        const next = { ...prev };
        delete next[id];
        return next;
      });
      void loadSaved();
      void loadSchedules();
    } catch (e) {
      showToast(formatUserFacingError(e), { type: "error" });
    } finally {
      setDeletingId(null);
    }
  };

  const renderIdeaCard = (idea, { savedRow = false, ideaId = null }) => (
    <div
      key={ideaId || idea._key}
      className="panel"
      style={{
        marginBottom: 12,
        padding: "14px 16px",
        border: "1px solid var(--border-subtle, #333)",
        borderRadius: 8,
      }}
    >
      <div
        style={{
          display: "flex",
          justifyContent: "space-between",
          alignItems: "flex-start",
          gap: 12,
          marginBottom: 8,
        }}
      >
        <h3 style={{ margin: 0, fontSize: "1.05rem", flex: "1 1 auto", minWidth: 0 }}>{idea.title}</h3>
        {savedRow && ideaId ? (
          <button
            type="button"
            className="brief-idea-delete-btn"
            title="Delete saved idea"
            aria-label="Delete saved idea"
            disabled={Boolean(deletingId)}
            onClick={() => void onDeleteSaved(ideaId)}
          >
            Delete
          </button>
        ) : null}
      </div>
      <p className="subtle" style={{ margin: "0 0 12px", whiteSpace: "pre-wrap", lineHeight: 1.45 }}>
        {idea.description}
      </p>
      <div style={{ display: "flex", flexWrap: "wrap", gap: 8, alignItems: "center" }}>
        <button type="button" className="secondary" onClick={() => onSave(idea.title, idea.description)}>
          Save
        </button>
        <button
          type="button"
          onClick={() =>
            savedRow && ideaId ? onRunSaved(ideaId) : onRunGenerated(idea.title, idea.description)
          }
        >
          Run
        </button>
        {savedRow && ideaId ? (
          <>
            <label className="subtle" style={{ display: "flex", alignItems: "center", gap: 6 }}>
              Run at
              <input
                type="datetime-local"
                value={scheduleInputs[ideaId] ?? ""}
                onChange={(e) =>
                  setScheduleInputs((prev) => ({ ...prev, [ideaId]: e.target.value }))
                }
              />
            </label>
            <button type="button" className="secondary" onClick={() => onScheduleSaved(ideaId)}>
              Schedule
            </button>
          </>
        ) : null}
      </div>
    </div>
  );

  return (
    <section
      className="panel usage-page"
      style={{ maxWidth: 920, width: "100%", minWidth: 0, margin: "0 auto", boxSizing: "border-box" }}
    >
      <header className="usage-page-header">
        <div>
          <h2>Ideas</h2>
          <p className="subtle">
            Enter a topic to generate documentary-style project ideas (title + description). Save ideas for later, run
            immediately to start the full automation pipeline, or schedule a saved idea to run at a specific time (requires
            Celery beat).
          </p>
        </div>
      </header>

      <div style={{ marginBottom: 20, minWidth: 0, width: "100%", boxSizing: "border-box" }}>
        <label htmlFor="ideas-topic" className="subtle">
          Topic
        </label>
        <textarea
          id="ideas-topic"
          className="ideas-topic"
          rows={3}
          value={topic}
          onChange={(e) => setTopic(e.target.value)}
          placeholder="e.g. Renewable energy in Nordic countries, history of jazz in New Orleans…"
        />
        <div style={{ marginTop: 10, display: "flex", flexWrap: "wrap", gap: 12, alignItems: "center" }}>
          <button type="button" disabled={generating} onClick={() => void onGenerate()}>
            {generating ? "Generating…" : "Generate ideas"}
          </button>
          <label className="subtle" style={{ display: "flex", alignItems: "center", gap: 6 }}>
            Target runtime (min)
            <input
              type="number"
              min={2}
              max={120}
              value={runtimeMinutes}
              onChange={(e) => setRuntimeMinutes(Number(e.target.value))}
              style={{ width: 72 }}
            />
          </label>
        </div>
      </div>

      {generated.length > 0 ? (
        <div style={{ marginBottom: 28 }}>
          <h3 className="usage-section-title">Generated</h3>
          {generated.map((idea) => renderIdeaCard(idea, { savedRow: false }))}
        </div>
      ) : null}

      <div style={{ marginBottom: 28 }}>
        <h3 className="usage-section-title">Saved ideas</h3>
        {loadingSaved ? (
          <p className="subtle">Loading…</p>
        ) : saved.length === 0 ? (
          <p className="subtle">No saved ideas yet. Generate above and click Save, or save from this list after generating.</p>
        ) : (
          saved.map((row) =>
            renderIdeaCard(
              { title: row.title, description: row.description },
              { savedRow: true, ideaId: row.id },
            ),
          )
        )}
      </div>

      <div>
        <h3 className="usage-section-title">Upcoming schedules</h3>
        {schedules.filter((s) => s.status === "pending").length === 0 ? (
          <p className="subtle">No pending schedules. Schedule from a saved idea above.</p>
        ) : (
          <ul className="usage-table-wrap" style={{ listStyle: "none", padding: 0 }}>
            {schedules
              .filter((s) => s.status === "pending")
              .map((s) => {
                const ideaRow = saved.find((x) => x.id === s.idea_id);
                const label = ideaRow ? ideaRow.title : String(s.idea_id);
                return (
                <li
                  key={s.id}
                  className="panel"
                  style={{
                    marginBottom: 8,
                    padding: "10px 12px",
                    display: "flex",
                    flexWrap: "wrap",
                    justifyContent: "space-between",
                    gap: 8,
                    alignItems: "center",
                  }}
                >
                  <span className="subtle">
                    <strong>{label}</strong> — {new Date(s.scheduled_at).toLocaleString()}
                  </span>
                  <button type="button" className="secondary" onClick={() => void onCancelSchedule(s.id)}>
                    Cancel
                  </button>
                </li>
              );
              })}
          </ul>
        )}
      </div>
    </section>
  );
}
