/** Primary areas — vertical rail with sideways labels (Blender-style). */
export const STUDIO_PAGE_RAILS = [
  { id: "ideas", label: "Ideas" },
  { id: "editor", label: "Editor" },
  { id: "chat", label: "Chat" },
  { id: "research_chapters", label: "Research & scripts" },
  { id: "characters", label: "Characters" },
  { id: "usage", label: "Usage" },
  { id: "prompts", label: "Prompts" },
  { id: "settings", label: "Settings" },
  { id: "account", label: "Account" },
  { id: "admin", label: "Admin" },
];

export const STUDIO_PAGE_IDS = new Set(STUDIO_PAGE_RAILS.map((r) => r.id));

/** In-app legal views (not shown in the primary rail). */
export const LEGAL_PAGE_IDS = new Set(["terms", "privacy", "copyright"]);

export function normalizeDirectorActivePage(v) {
  const id = typeof v === "string" ? v.trim() : "";
  if (LEGAL_PAGE_IDS.has(id)) return id;
  return STUDIO_PAGE_IDS.has(id) ? id : "editor";
}

/** Pages loaded via ``React.lazy`` in ``StudioPageRouter`` (chat uses keep-alive in App). */
export const STUDIO_LAZY_PAGE_IDS = [
  "ideas",
  "editor",
  "research_chapters",
  "characters",
  "usage",
  "prompts",
  "settings",
  "account",
  "admin",
  "terms",
  "privacy",
  "copyright",
];
