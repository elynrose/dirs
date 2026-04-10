/**
 * useToast — lightweight toast notification manager.
 *
 * Returns `{ toasts, toast, dismissToast }`.
 *
 * Usage:
 *   const { toasts, toast } = useToast();
 *   toast("Scene 4 image ready", { type: "success", action: { label: "View", onClick: openScene } });
 *
 * Toast shapes:
 *   { id, message, type: "success"|"error"|"info", action?: { label, onClick }, durationMs }
 *
 * Wire <ToastContainer toasts={toasts} onDismiss={dismissToast} /> near the root.
 */

import { useCallback, useRef, useState } from "react";

let _nextId = 1;

/**
 * @param {Object} [defaults]
 * @param {number} [defaults.durationMs=4000]  Auto-dismiss delay in ms (0 = persistent).
 */
export function useToast(defaults = {}) {
  const { durationMs: defaultDuration = 4000 } = defaults;
  const [toasts, setToasts] = useState([]);
  const timers = useRef({});

  const dismissToast = useCallback((id) => {
    setToasts((prev) => prev.filter((t) => t.id !== id));
    clearTimeout(timers.current[id]);
    delete timers.current[id];
  }, []);

  const toast = useCallback(
    (message, opts = {}) => {
      const id = _nextId++;
      const duration = opts.durationMs ?? defaultDuration;
      const entry = {
        id,
        message: String(message),
        type: opts.type ?? "info",       // "success" | "error" | "info"
        action: opts.action ?? null,      // { label: string, onClick: fn }
      };
      setToasts((prev) => [...prev, entry]);
      if (duration > 0) {
        timers.current[id] = setTimeout(() => dismissToast(id), duration);
      }
      return id;
    },
    [defaultDuration, dismissToast],
  );

  return { toasts, toast, dismissToast };
}
