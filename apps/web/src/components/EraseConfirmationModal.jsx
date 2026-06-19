import {
  eraseScopeActionLabel,
  formatEraseScopeBullets,
} from "../lib/eraseConsent.js";

export function EraseConfirmationModal({
  open,
  erase,
  busy,
  onCancel,
  onConfirm,
}) {
  if (!open || !erase) return null;
  const action = eraseScopeActionLabel(erase.scopeLabel);
  const bullets = formatEraseScopeBullets(erase.scope);

  return (
    <div
      className="restart-automation-modal-backdrop"
      role="presentation"
      onClick={busy ? undefined : onCancel}
    >
      <div
        className="panel restart-automation-modal erase-confirmation-modal"
        role="dialog"
        aria-modal="true"
        aria-labelledby="erase-confirmation-title"
        onClick={(e) => e.stopPropagation()}
        onKeyDown={(e) => {
          if (!busy && e.key === "Escape") onCancel?.();
        }}
      >
        <h3 id="erase-confirmation-title">Replace existing project content?</h3>
        <p className="subtle" style={{ marginTop: 8, lineHeight: 1.55 }}>
          Re-running the <strong>{action}</strong> will delete existing generated work on this project.
          This cannot be undone.
        </p>
        {bullets.length ? (
          <ul style={{ margin: "12px 0 0", paddingLeft: "1.2rem", lineHeight: 1.6 }}>
            {bullets.map((line) => (
              <li key={line}>{line}</li>
            ))}
          </ul>
        ) : (
          <p className="subtle" style={{ marginTop: 12 }}>
            Scenes and generated media will be removed before the automation continues.
          </p>
        )}
        <div className="restart-automation-modal-actions" style={{ marginTop: 16 }}>
          <button type="button" className="secondary" disabled={busy} onClick={onCancel}>
            Cancel
          </button>
          <button type="button" disabled={busy} onClick={onConfirm}>
            {busy ? "Starting…" : "Yes, erase and continue"}
          </button>
        </div>
      </div>
    </div>
  );
}
