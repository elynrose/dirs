export function StudioResearchPage({
  projectId,
  chapters,
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
}) {
  return (
    <section className="panel usage-page research-scripts-page">
      <header className="usage-page-header">
        <div>
          <h2>Research dossier &amp; chapters</h2>
          <p className="subtle">
            Edit the structured research JSON and chapter scripts before <strong>Plan scenes</strong>. Use this in <strong>manual</strong> mode to
            fix facts, tighten summaries, or rewrite VO. Saving the dossier checks it against the server schema; chapter saves update titles,
            summaries, target duration, and full script text.
          </p>
          {researchPageErr ? <p className="err usage-page-error">{researchPageErr}</p> : null}
        </div>
        <div className="usage-page-toolbar">
          <button
            type="button"
            disabled={researchPageBusy || researchPipelineBusy || !projectId}
            onClick={() => void rerunResearch()}
          >
            {researchPipelineBusy ? "Research running…" : "Rerun research"}
          </button>
          <button
            type="button"
            className="secondary"
            disabled={researchPageBusy || researchPipelineBusy || !projectId}
            onClick={() => projectId && void loadResearchChaptersEditor(projectId)}
          >
            Reload from server
          </button>
        </div>
      </header>
      {!projectId ? (
        <p className="subtle">Open a project from the Editor tab first.</p>
      ) : (
        <>
          {researchPageBusy ? <p className="subtle">Loading or saving…</p> : null}
          <div className="research-scripts-meta subtle" style={{ marginBottom: 16 }}>
            {researchMeta?.dossier ? (
              <>
                Dossier v{researchMeta.dossier.version ?? "—"} · status <code>{researchMeta.dossier.status}</code>
                {researchMeta.script_gate_open ? (
                  <span> · script gate open</span>
                ) : (
                  <span> · script gate closed (approve or override research in Pipeline if scripts are blocked)</span>
                )}
                {researchMeta.sourceCount != null ? (
                  <span>
                    {" "}
                    · {researchMeta.sourceCount} source(s), {researchMeta.claimCount} claim(s) (edit sources/claims via API only)
                  </span>
                ) : null}
              </>
            ) : (
              <span>No dossier yet — run research from the Pipeline panel, then reload.</span>
            )}
          </div>
          <details className="settings-section" open>
            <summary className="settings-section-summary">
              <span className="settings-section-heading">Research dossier (JSON)</span>
            </summary>
            <div className="settings-section-body">
              <p className="subtle" style={{ marginTop: 0 }}>
                Must stay valid for <code>research-dossier.schema.json</code>. Invalid JSON or schema errors return 422 from the server.
                Use <strong>Rerun research</strong> above to fetch a fresh dossier from the web (same as Pipeline).
              </p>
              <textarea
                className="research-dossier-json"
                rows={16}
                spellCheck={false}
                value={researchJsonDraft}
                onChange={(e) => setResearchJsonDraft(e.target.value)}
                disabled={researchPageBusy || researchPipelineBusy || !researchMeta?.dossier}
              />
              <div className="action-row" style={{ marginTop: 10 }}>
                <button
                  type="button"
                  disabled={researchPageBusy || researchPipelineBusy || !projectId || !researchMeta?.dossier}
                  onClick={() => void saveDossier()}
                >
                  Save dossier
                </button>
              </div>
            </div>
          </details>
          <h3 className="usage-section-title" style={{ marginTop: 24 }}>
            Chapters
          </h3>
          {chapters.length === 0 ? (
            <p className="subtle">No chapters yet — generate outline and scripts from the Pipeline panel, then reload.</p>
          ) : (
            chapters.map((ch) => {
              const d = chapterScriptsDraft[ch.id] || {
                title: ch.title ?? "",
                summary: ch.summary ?? "",
                target_duration_sec: ch.target_duration_sec != null ? String(ch.target_duration_sec) : "",
                script_text: ch.script_text ?? "",
              };
              const setD = (patch) =>
                setChapterScriptsDraft((prev) => ({
                  ...prev,
                  [ch.id]: { ...d, ...patch },
                }));
              return (
                <details key={ch.id} className="settings-section" style={{ marginBottom: 12 }}>
                  <summary className="settings-section-summary">
                    <span className="settings-section-heading">
                      {ch.order_index + 1}. {d.title || ch.title || "Chapter"}
                    </span>
                  </summary>
                  <div className="settings-section-body">
                    <label className="subtle" htmlFor={`rch-title-${ch.id}`}>
                      Title
                    </label>
                    <input
                      id={`rch-title-${ch.id}`}
                      value={d.title}
                      onChange={(e) => setD({ title: e.target.value })}
                      disabled={researchPageBusy}
                    />
                    <label className="subtle" htmlFor={`rch-sum-${ch.id}`} style={{ display: "block", marginTop: 10 }}>
                      Summary (also used as <strong>regeneration notes</strong> — describe what to improve in the script)
                    </label>
                    <textarea
                      id={`rch-sum-${ch.id}`}
                      rows={3}
                      value={d.summary}
                      onChange={(e) => setD({ summary: e.target.value })}
                      disabled={researchPageBusy || Boolean(chapterRegenerateId)}
                    />
                    <label className="subtle" htmlFor={`rch-sec-${ch.id}`} style={{ display: "block", marginTop: 10 }}>
                      Target duration (seconds, optional)
                    </label>
                    <input
                      id={`rch-sec-${ch.id}`}
                      type="number"
                      min={30}
                      max={7200}
                      value={d.target_duration_sec}
                      onChange={(e) => setD({ target_duration_sec: e.target.value })}
                      disabled={researchPageBusy}
                      style={{ maxWidth: 120 }}
                    />
                    <label className="subtle" htmlFor={`rch-script-${ch.id}`} style={{ display: "block", marginTop: 10 }}>
                      Script / VO (used for scene planning)
                    </label>
                    <textarea
                      id={`rch-script-${ch.id}`}
                      className="research-chapter-script"
                      rows={12}
                      value={d.script_text}
                      onChange={(e) => setD({ script_text: e.target.value })}
                      disabled={researchPageBusy}
                    />
                    <div className="action-row" style={{ marginTop: 10, flexWrap: "wrap", gap: 8 }}>
                      <button
                        type="button"
                        className="secondary"
                        disabled={researchPageBusy || researchPipelineBusy || chapterRegenerateId !== ""}
                        title="Queues an LLM job: passes the summary above as enhancement_notes to rewrite the script."
                        onClick={() => void regenerateChapterScript(ch, d)}
                      >
                        {chapterRegenerateId === ch.id ? "Regenerating…" : "Regenerate script from summary"}
                      </button>
                      <button
                        type="button"
                        disabled={researchPageBusy || researchPipelineBusy || Boolean(chapterRegenerateId)}
                        onClick={() => void saveChapter(ch, d)}
                      >
                        Save chapter
                      </button>
                    </div>
                    {chapterRegenerateId === ch.id ? (
                      <p className="subtle" style={{ marginTop: 8 }}>
                        Worker is rewriting this chapter’s script from your summary. This tab will reload when the job finishes (or use{" "}
                        <strong>Reload from server</strong>).
                      </p>
                    ) : null}
                  </div>
                </details>
              );
            })
          )}
        </>
      )}
    </section>
  );
}
