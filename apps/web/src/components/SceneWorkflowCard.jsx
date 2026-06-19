/** Sub-panel inside merged center-column scene tabs (Generate / Assets / Script…). */
export function SceneWorkflowCard({ title, children }) {
  return (
    <div
      className="scene-workflow-card panel"
      style={{
        marginBottom: 14,
        padding: "12px 14px",
        border: "1px solid var(--border-subtle, #333)",
        borderRadius: 8,
        background: "var(--panel-elevated, rgba(255,255,255,0.03))",
      }}
    >
      <div
        style={{
          fontSize: "0.72rem",
          fontWeight: 700,
          letterSpacing: "0.06em",
          textTransform: "uppercase",
          color: "rgba(255,255,255,0.5)",
          margin: "0 0 10px",
        }}
      >
        {title}
      </div>
      {children}
    </div>
  );
}
