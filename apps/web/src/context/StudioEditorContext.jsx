import { createContext, useContext } from "react";

/**
 * Editor workspace context — state/handlers live in App (or future useStudioEditor hook).
 * Sub-panels call useStudioEditor() instead of a flat prop list (props must move with state).
 */
const StudioEditorContext = createContext(null);

export function StudioEditorProvider({ value, children }) {
  return <StudioEditorContext.Provider value={value}>{children}</StudioEditorContext.Provider>;
}

export function useStudioEditor() {
  const ctx = useContext(StudioEditorContext);
  if (!ctx) {
    throw new Error("useStudioEditor must be used within StudioEditorProvider");
  }
  return ctx;
}
