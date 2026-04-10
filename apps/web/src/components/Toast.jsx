/**
 * Toast notification UI.
 *
 * Renders a stack of non-blocking toast messages in the bottom-right corner.
 * Wire this near the App root alongside useToast():
 *
 *   const { toasts, toast, dismissToast } = useToast();
 *   // ... in render:
 *   <ToastContainer toasts={toasts} onDismiss={dismissToast} />
 */

import { useEffect, useRef } from "react";

/** Individual toast card. Fades in, then out on auto-dismiss. */
function ToastItem({ toast, onDismiss }) {
  const ref = useRef(null);

  useEffect(() => {
    // Trigger enter animation on mount
    const el = ref.current;
    if (!el) return;
    requestAnimationFrame(() => el.classList.add("toast--visible"));
  }, []);

  const typeClass =
    toast.type === "success"
      ? "toast--success"
      : toast.type === "error"
        ? "toast--error"
        : "toast--info";

  return (
    <div ref={ref} className={`toast ${typeClass}`} role="status" aria-live="polite">
      <span className="toast__message">{toast.message}</span>
      {toast.action && (
        <button
          type="button"
          className="toast__action"
          onClick={() => {
            toast.action.onClick?.();
            onDismiss(toast.id);
          }}
        >
          {toast.action.label}
        </button>
      )}
      <button
        type="button"
        className="toast__close"
        aria-label="Dismiss"
        onClick={() => onDismiss(toast.id)}
      >
        ×
      </button>
    </div>
  );
}

/** Container — place once near the root of the app. */
export function ToastContainer({ toasts, onDismiss }) {
  if (!toasts || toasts.length === 0) return null;
  return (
    <div className="toast-container" aria-label="Notifications">
      {toasts.map((t) => (
        <ToastItem key={t.id} toast={t} onDismiss={onDismiss} />
      ))}
    </div>
  );
}
