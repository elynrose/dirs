/**
 * Single list row pattern for critic report index (used for “related” + “all reports” blocks).
 */
export function CriticReportIndexList({
  reports,
  busy,
  criticReportTargetLabel,
  loadCriticReport,
  goToChapterScene,
  openSceneForCriticReport,
}) {
  if (!reports?.length) return null;
  return (
    <ul className="critic-report-index">
      {reports.map((rep) => (
        <li key={rep.id} className="critic-report-index-row">
          <span>{criticReportTargetLabel(rep)}</span>
          <span>{rep.passed ? "Passed" : "Needs work"}</span>
          <span className="subtle">score {typeof rep.score === "number" ? rep.score.toFixed(2) : rep.score}</span>
          <div className="critic-report-index-actions">
            <button type="button" className="secondary" onClick={() => loadCriticReport(rep.id)}>
              View report
            </button>
            {rep.target_type === "chapter" && rep.target_id ? (
              <button
                type="button"
                className="secondary"
                disabled={busy}
                onClick={() => goToChapterScene(String(rep.target_id), null)}
              >
                View chapter
              </button>
            ) : null}
            {rep.target_type === "scene" && rep.target_id ? (
              <button
                type="button"
                className="secondary"
                disabled={busy}
                onClick={() => void openSceneForCriticReport(String(rep.target_id))}
              >
                View scene
              </button>
            ) : null}
          </div>
        </li>
      ))}
    </ul>
  );
}
