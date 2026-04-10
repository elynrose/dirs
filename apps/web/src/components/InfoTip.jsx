import { useState, useRef, useCallback } from "react";

/**
 * Small ⓘ icon that shows a rich tooltip on hover/focus.
 * Uses position:fixed so it escapes overflow:hidden/auto parent panels.
 */
export function InfoTip({ children }) {
  const [open, setOpen] = useState(false);
  const [coords, setCoords] = useState({ x: 0, y: 0 });
  const ref = useRef(null);

  const show = useCallback(() => {
    if (ref.current) {
      const r = ref.current.getBoundingClientRect();
      setCoords({ x: r.left + r.width / 2, y: r.top });
      setOpen(true);
    }
  }, []);

  const hide = useCallback(() => setOpen(false), []);

  return (
    <span
      ref={ref}
      className="info-tip"
      onMouseEnter={show}
      onFocus={show}
      onMouseLeave={hide}
      onBlur={hide}
      tabIndex={0}
      aria-label="Information"
    >
      ⓘ
      {open && (
        <span
          className="info-tip-popup"
          role="tooltip"
          style={{ left: coords.x, top: coords.y }}
        >
          {children}
        </span>
      )}
    </span>
  );
}
