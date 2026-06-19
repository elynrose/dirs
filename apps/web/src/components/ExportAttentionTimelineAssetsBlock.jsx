function friendlyIssueCodesList(codes) {
  if (!Array.isArray(codes) || !codes.length) return "—";
  return codes.map((c) => String(c).replace(/_/g, " ")).join(", ");
}

/** Surfaces API `export_attention_timeline_assets` in the export gate modal and timeline panel. */
export function ExportAttentionTimelineAssetsBlock({ rows, busy, onOpenScene, onReconcile, reconcileDisabled }) {
  if (!Array.isArray(rows) || rows.length === 0) return null;
  return (
    <div className="export-attention-timeline-block" style={{ marginTop: 12 }}>
      <p style={{ margin: "0 0 8px", fontSize: "0.85rem", fontWeight: 600 }}>
        Timeline media to fix
      </p>
      <p className="subtle" style={{ margin: "0 0 10px", fontSize: "0.75rem", lineHeight: 1.5 }}>
        Export checks each clip: <strong>image or video</strong>, <strong>approved</strong> (unless hands-off), <strong>file on disk</strong>, and not
        <strong> rejected/failed</strong> in the DB — <strong>succeeded</strong> status is not required if the file is already there.{" "}
        <strong>Reconcile timeline clips</strong> re-points bad clips (same scene, then other scenes / project fallbacks). Each row shows{" "}
        <strong>type</strong> and <strong>status</strong> from the server when available.
      </p>
      {typeof onReconcile === "function" ? (
        <div className="action-row" style={{ marginBottom: 12, flexWrap: "wrap", gap: 8 }}>
          <button
            type="button"
            className="secondary"
            disabled={Boolean(reconcileDisabled) || busy}
            onClick={() => void onReconcile()}
            title="Relink timeline clips to viable scene media, sync storyboard order, and related fixes"
          >
            Reconcile timeline clips
          </button>
        </div>
      ) : null}
      <ul style={{ margin: 0, paddingLeft: 18, fontSize: "0.8rem", lineHeight: 1.45 }}>
        {rows.map((row) => {
          const aid = String(row?.asset_id || "");
          const short = aid.length > 14 ? `${aid.slice(0, 8)}…${aid.slice(-4)}` : aid || "—";
          const sid = row?.scene_id ? String(row.scene_id) : "";
          return (
            <li key={aid || short} style={{ marginBottom: 10 }}>
              <span className="mono">{short}</span>
              {" — "}
              <span className="subtle">{friendlyIssueCodesList(row?.issue_codes)}</span>
              {row?.asset_type || row?.status ? (
                <div className="subtle" style={{ marginTop: 4, fontSize: "0.72rem" }}>
                  DB: {row?.asset_type ? String(row.asset_type) : "—"} · status {row?.status ? String(row.status) : "—"}
                </div>
              ) : null}
              <div className="action-row" style={{ marginTop: 6, flexWrap: "wrap", gap: 6 }}>
                <button
                  type="button"
                  className="secondary"
                  style={{ padding: "2px 8px", fontSize: "0.72rem" }}
                  disabled={!aid}
                  onClick={() => {
                    if (aid) void navigator.clipboard?.writeText(aid);
                  }}
                >
                  Copy asset id
                </button>
                {sid ? (
                  <button
                    type="button"
                    className="secondary"
                    style={{ padding: "2px 8px", fontSize: "0.72rem" }}
                    disabled={busy}
                    onClick={() => void onOpenScene?.(aid)}
                  >
                    Open scene
                  </button>
                ) : (
                  <span className="subtle">
                    Export still flagged this clip — Reconcile timeline clips, or replace the asset reference.
                  </span>
                )}
              </div>
            </li>
          );
        })}
      </ul>
    </div>
  );
}
