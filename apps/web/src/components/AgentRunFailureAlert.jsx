import { apiPath } from "../lib/api.js";
import { formatAgentRunFailure } from "../lib/apiHelpers.js";

/**
 * Short failure copy + link to downloadable technical log (full worker error, stats, steps).
 */
export function AgentRunFailureAlert({
  run,
  title = "Automation failed",
  eraseConfirmation = false,
  onConfirmErase,
  busy = false,
  className = "pipeline-run-failed-alert",
}) {
  if (!run?.error_message && !eraseConfirmation) return null;

  if (eraseConfirmation) {
    return (
      <div className={className} role="alert" style={{ marginBottom: 14 }}>
        <i className="fa-solid fa-circle-xmark" aria-hidden="true" />
        <div>
          <strong>Confirmation required</strong>
          <div style={{ marginTop: 6 }}>
            This run would replace existing chapters, scenes, or generated media. Confirm before continuing.
          </div>
          <div className="action-row" style={{ marginTop: 10, flexWrap: "wrap", gap: 8 }}>
            <button type="button" disabled={busy} onClick={() => onConfirmErase?.()}>
              Review &amp; confirm
            </button>
          </div>
        </div>
      </div>
    );
  }

  const { summary, diagnosticsPath } = formatAgentRunFailure(run);
  const logHref = diagnosticsPath ? apiPath(diagnosticsPath) : null;

  return (
    <div className={className} role="alert" style={{ marginBottom: 14 }}>
      <i className="fa-solid fa-circle-xmark" aria-hidden="true" />
      <div>
        <strong>{title}</strong>
        <div style={{ marginTop: 6 }}>{summary}</div>
        {logHref ? (
          <p className="subtle" style={{ marginTop: 10, marginBottom: 0, fontSize: "0.85rem" }}>
            <a href={logHref} target="_blank" rel="noreferrer">
              Download technical log
            </a>
            {" "}
            — full error, media counts, and step history.
          </p>
        ) : null}
      </div>
    </div>
  );
}
