/**
 * ShortcutHelp — modal overlay that lists all registered keyboard shortcuts.
 * Triggered by pressing "?" anywhere outside a text input.
 */

import { useEffect } from "react";

const SHORTCUTS = [
  { key: "↓ / ↑",         desc: "Navigate to next / previous scene" },
  { key: "A",             desc: "Approve the first un-approved asset in the current scene" },
  { key: "R",             desc: "Reject the currently focused asset" },
  { key: "G",             desc: "Generate an image for the current scene" },
  { key: "Space",         desc: "Play / pause audio preview" },
  { key: "Ctrl / ⌘ + S", desc: "Save narration draft" },
  { key: "?",             desc: "Show / hide this shortcut reference" },
];

export function ShortcutHelp({ open, onClose }) {
  // Close on Escape
  useEffect(() => {
    if (!open) return;
    const handler = (e) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [open, onClose]);

  if (!open) return null;

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-label="Keyboard shortcuts"
      style={{
        position: "fixed",
        inset: 0,
        zIndex: 9000,
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        background: "rgba(0,0,0,0.55)",
        backdropFilter: "blur(3px)",
      }}
      onClick={(e) => { if (e.target === e.currentTarget) onClose(); }}
    >
      <div
        style={{
          background: "var(--bg-1, #1e1e2e)",
          border: "1px solid var(--border, #3a3a4a)",
          borderRadius: 12,
          padding: "24px 28px",
          minWidth: 340,
          maxWidth: 480,
          boxShadow: "0 8px 40px rgba(0,0,0,0.5)",
        }}
      >
        <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 18 }}>
          <h3 style={{ margin: 0, fontSize: "1rem", fontWeight: 600 }}>⌨️ Keyboard Shortcuts</h3>
          <button
            type="button"
            className="secondary"
            onClick={onClose}
            style={{ padding: "2px 10px", fontSize: "0.8rem" }}
            aria-label="Close shortcuts help"
          >
            ✕
          </button>
        </div>
        <table style={{ width: "100%", borderCollapse: "collapse" }}>
          <tbody>
            {SHORTCUTS.map(({ key, desc }) => (
              <tr key={key} style={{ borderBottom: "1px solid var(--border, #3a3a4a)" }}>
                <td style={{ padding: "8px 12px 8px 0", whiteSpace: "nowrap" }}>
                  <kbd
                    style={{
                      background: "var(--bg-2, #2a2a3e)",
                      border: "1px solid var(--border, #3a3a4a)",
                      borderRadius: 4,
                      padding: "2px 7px",
                      fontFamily: "monospace",
                      fontSize: "0.82rem",
                      letterSpacing: "0.02em",
                    }}
                  >
                    {key}
                  </kbd>
                </td>
                <td style={{ padding: "8px 0", fontSize: "0.85rem", color: "var(--text-muted, #aaa)" }}>
                  {desc}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        <p style={{ margin: "14px 0 0", fontSize: "0.72rem", color: "var(--text-muted, #aaa)", textAlign: "center" }}>
          Shortcuts are disabled while editing text fields.  Press <strong>?</strong> or <strong>Esc</strong> to close.
        </p>
      </div>
    </div>
  );
}
