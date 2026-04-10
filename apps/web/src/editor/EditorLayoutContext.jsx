import { createContext, useCallback, useContext, useMemo, useState, useEffect } from "react";

const STORAGE_KEY = "director_editor_layout_v2";

/**
 * Center column: scene workflow panels shown as tabs (single visible panel; order from layout).
 * Includes narration, jobs, media generation, prompts, script, assets, video prompt.
 */
export const EDITOR_CENTER_SCENE_TAB_IDS = [
  "previewNarration",
  "mediaJobs",
  "mediaGen",
  "retryPrompt",
  "scriptExcerpt",
  "sceneAssets",
  "retryVideoPrompt",
];

/** Default card order per column (ids must match EditorCard usage in App.jsx). */
export const EDITOR_COLUMN_DEFAULT_ORDER = {
  left: ["projects", "musicMix", "transitions"],
  center: [
    "previewVisual",
    "chapter",
    "scenes",
    "chapterNarration",
    "previewNarration",
    "mediaJobs",
    "mediaGen",
    "retryPrompt",
    "scriptExcerpt",
    "sceneAssets",
    "retryVideoPrompt",
  ],
  right: ["progress", "brief", "projectSubtitles", "reviewsAndAlerts"],
  timeline: ["sceneOrder", "compile"],
  audio: [],
};

function loadState() {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) {
      return {
        order: { ...EDITOR_COLUMN_DEFAULT_ORDER },
        collapsed: {},
      };
    }
    const r = JSON.parse(raw);
    const order = { ...EDITOR_COLUMN_DEFAULT_ORDER, ...(r.order || {}) };
    for (const col of Object.keys(EDITOR_COLUMN_DEFAULT_ORDER)) {
      if (!Array.isArray(order[col])) {
        order[col] = [...EDITOR_COLUMN_DEFAULT_ORDER[col]];
      }
    }
    return {
      order,
      collapsed: typeof r.collapsed === "object" && r.collapsed ? r.collapsed : {},
    };
  } catch {
    return { order: { ...EDITOR_COLUMN_DEFAULT_ORDER }, collapsed: {} };
  }
}

function mergeVisibleOrder(column, visibleIds, persistedList) {
  const vis = new Set(visibleIds);
  const base = Array.isArray(persistedList) ? persistedList : EDITOR_COLUMN_DEFAULT_ORDER[column] || [];
  const head = base.filter((id) => vis.has(id));
  for (const id of visibleIds) {
    if (!head.includes(id)) head.push(id);
  }
  return head;
}

const EditorLayoutContext = createContext(null);

export function EditorLayoutProvider({ children }) {
  const [state, setState] = useState(loadState);

  useEffect(() => {
    try {
      localStorage.setItem(STORAGE_KEY, JSON.stringify(state));
    } catch {
      /* ignore */
    }
  }, [state]);

  const moveInColumn = useCallback((column, dragId, dropBeforeId) => {
    if (!dragId || !dropBeforeId || dragId === dropBeforeId) return;
    setState((s) => {
      const list = [...(s.order[column] || EDITOR_COLUMN_DEFAULT_ORDER[column] || [])];
      const fi = list.indexOf(dragId);
      const ti = list.indexOf(dropBeforeId);
      if (fi < 0 || ti < 0) return s;
      list.splice(fi, 1);
      const newTi = list.indexOf(dropBeforeId);
      list.splice(newTi, 0, dragId);
      return { ...s, order: { ...s.order, [column]: list } };
    });
  }, []);

  const appendToColumnEnd = useCallback((column, dragId) => {
    setState((s) => {
      const list = [...(s.order[column] || EDITOR_COLUMN_DEFAULT_ORDER[column] || [])];
      const fi = list.indexOf(dragId);
      if (fi < 0) return s;
      list.splice(fi, 1);
      list.push(dragId);
      return { ...s, order: { ...s.order, [column]: list } };
    });
  }, []);

  const toggleCollapsed = useCallback((column, id) => {
    const key = `${column}:${id}`;
    setState((s) => ({
      ...s,
      collapsed: { ...s.collapsed, [key]: !s.collapsed[key] },
    }));
  }, []);

  /** Expand one card and collapse all `peerIds` in the same column (accordion). */
  const toggleCollapsedAccordion = useCallback((column, id, peerIds) => {
    const k = `${column}:${id}`;
    if (!peerIds?.length) {
      setState((s) => ({
        ...s,
        collapsed: { ...s.collapsed, [k]: !s.collapsed[k] },
      }));
      return;
    }
    const key = (x) => `${column}:${x}`;
    setState((s) => {
      const wasCollapsed = Boolean(s.collapsed[key(id)]);
      const next = { ...s.collapsed };
      if (wasCollapsed) {
        for (const pid of peerIds) {
          if (pid !== id) next[key(pid)] = true;
        }
        next[key(id)] = false;
      } else {
        next[key(id)] = true;
      }
      return { ...s, collapsed: next };
    });
  }, []);

  const isCollapsed = useCallback(
    (column, id) => Boolean(state.collapsed[`${column}:${id}`]),
    [state.collapsed],
  );

  const getOrderedIds = useCallback(
    (column, visibleIds) => mergeVisibleOrder(column, visibleIds, state.order[column]),
    [state.order],
  );

  const value = useMemo(
    () => ({
      moveInColumn,
      appendToColumnEnd,
      toggleCollapsed,
      toggleCollapsedAccordion,
      isCollapsed,
      getOrderedIds,
    }),
    [moveInColumn, appendToColumnEnd, toggleCollapsed, toggleCollapsedAccordion, isCollapsed, getOrderedIds],
  );

  return <EditorLayoutContext.Provider value={value}>{children}</EditorLayoutContext.Provider>;
}

export function useEditorLayout() {
  const ctx = useContext(EditorLayoutContext);
  if (!ctx) {
    throw new Error("EditorLayoutProvider is required");
  }
  return ctx;
}
