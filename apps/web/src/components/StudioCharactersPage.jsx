export function StudioCharactersPage({
  projectId,
  busy,
  projectCharacters,
  setProjectCharacters,
  charactersJobId,
  charactersJob,
  loadProjectCharacters,
  generateFromStory,
  saveCharacter,
  deleteCharacter,
  addCharacter,
  friendlyRunStatus,
}) {
  return (
    <section className="panel usage-page characters-page">
      <header className="usage-page-header">
        <div>
          <h2>Characters</h2>
          <p className="subtle">
            Define recurring people and visual identities for this production. The character agent reads your director brief, research summary,
            and chapter scripts (or outlines), then fills this list. Edits here are prepended to image and video prompts so models keep faces and
            wardrobe consistent. Re-run Plan scenes on a chapter if you want the storyboard agent to re-align scene prompts with an updated bible.
          </p>
        </div>
        <div className="usage-page-toolbar">
          <button
            type="button"
            disabled={busy || !projectId || Boolean(charactersJobId)}
            onClick={() => void generateFromStory()}
          >
            Generate from story
          </button>
          <button type="button" className="secondary" disabled={!projectId} onClick={() => loadProjectCharacters(projectId)}>
            Reload
          </button>
        </div>
      </header>
      {!projectId ? (
        <p className="subtle">Open or create a project from the Editor tab first.</p>
      ) : charactersJobId ? (
        <p className="subtle">
          Job status: {charactersJob?.status ? friendlyRunStatus(charactersJob.status) : "…"}
        </p>
      ) : null}
      <div className="character-card-list">
        {projectCharacters.map((c) => (
          <div key={c.id} className="panel character-card" style={{ marginBottom: 16, padding: 14 }}>
            <div className="action-row" style={{ marginBottom: 10, flexWrap: "wrap", gap: 8 }}>
              <input
                aria-label="Character name"
                value={c.name}
                onChange={(e) =>
                  setProjectCharacters((prev) =>
                    prev.map((x) => (x.id === c.id ? { ...x, name: e.target.value } : x)),
                  )
                }
                style={{ flex: "1 1 180px", minWidth: 120 }}
              />
              <input
                type="number"
                aria-label="Sort order"
                value={c.sort_order}
                onChange={(e) =>
                  setProjectCharacters((prev) =>
                    prev.map((x) =>
                      x.id === c.id ? { ...x, sort_order: Number(e.target.value) || 0 } : x,
                    ),
                  )
                }
                style={{ width: 88 }}
              />
              <button type="button" disabled={busy} onClick={() => void saveCharacter(c)}>
                Save
              </button>
              <button type="button" className="secondary" disabled={busy} onClick={() => void deleteCharacter(c)}>
                Delete
              </button>
            </div>
            <label className="subtle" style={{ display: "block", marginBottom: 4 }}>
              Role in story
            </label>
            <textarea
              rows={2}
              value={c.role_in_story || ""}
              onChange={(e) =>
                setProjectCharacters((prev) =>
                  prev.map((x) => (x.id === c.id ? { ...x, role_in_story: e.target.value } : x)),
                )
              }
              style={{ width: "100%", marginBottom: 10 }}
            />
            <label className="subtle" style={{ display: "block", marginBottom: 4 }}>
              Visual description (for image/video models)
            </label>
            <textarea
              rows={4}
              value={c.visual_description || ""}
              onChange={(e) =>
                setProjectCharacters((prev) =>
                  prev.map((x) => (x.id === c.id ? { ...x, visual_description: e.target.value } : x)),
                )
              }
              style={{ width: "100%", marginBottom: 10 }}
            />
            <label className="subtle" style={{ display: "block", marginBottom: 4 }}>
              Time / place / scope notes (optional)
            </label>
            <textarea
              rows={2}
              value={c.time_place_scope_notes || ""}
              onChange={(e) =>
                setProjectCharacters((prev) =>
                  prev.map((x) => (x.id === c.id ? { ...x, time_place_scope_notes: e.target.value } : x)),
                )
              }
              style={{ width: "100%" }}
            />
          </div>
        ))}
      </div>
      {projectId && projectCharacters.length === 0 && !charactersJobId ? (
        <p className="subtle">No characters yet. Run script/chapters first, then use Generate from story.</p>
      ) : null}
      {projectId ? (
        <div className="action-row" style={{ marginTop: 12 }}>
          <button type="button" className="secondary" disabled={busy} onClick={() => void addCharacter()}>
            Add character
          </button>
        </div>
      ) : null}
    </section>
  );
}
