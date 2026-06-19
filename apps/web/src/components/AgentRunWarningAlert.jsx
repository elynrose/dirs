import { apiPath } from "../lib/api.js";
import { formatAgentRunWarning } from "../lib/apiHelpers.js";

/**
 * Amber alert for non-fatal pipeline events (partial_failed videos, timeline visual heal).
 */
export function AgentRunWarningAlert({
  run,
  title = "Pipeline notice",
  className = "pipeline-run-warning-alert",
}) {
  const formatted = formatAgentRunWarning(run);
  if (!formatted?.summary) return null;

  const logHref = formatted.diagnosticsPath ? apiPath(formatted.diagnosticsPath) : null;

  return (
    <div className={className} role="status" style={{ marginBottom: 14 }}>
      <i className="fa-solid fa-triangle-exclamation" aria-hidden="true" />
      <div>
        <strong>{title}</strong>
        <div style={{ marginTop: 6 }}>{formatted.summary}</div>
        {logHref ? (
          <p className="subtle" style={{ marginTop: 10, marginBottom: 0, fontSize: "0.85rem" }}>
            <a href={logHref} target="_blank" rel="noreferrer">
              Download technical log
            </a>
          </p>
        ) : null}
      </div>
    </div>
  );
}
