import { lazy } from "react";

export const LazyStudioIdeasPage = lazy(() =>
  import("../StudioIdeasPage.jsx").then((m) => ({ default: m.StudioIdeasPage })),
);
export const LazyStudioLegalPage = lazy(() =>
  import("../StudioLegalPage.jsx").then((m) => ({ default: m.StudioLegalPage })),
);
export const LazyStudioAccountPage = lazy(() =>
  import("../StudioAccountPage.jsx").then((m) => ({ default: m.StudioAccountPage })),
);
export const LazyStudioAdminPage = lazy(() =>
  import("../StudioAdminPage.jsx").then((m) => ({ default: m.StudioAdminPage })),
);
export const LazyStudioUsagePage = lazy(() =>
  import("../StudioUsagePage.jsx").then((m) => ({ default: m.StudioUsagePage })),
);
export const LazyStudioPromptsPage = lazy(() =>
  import("../StudioPromptsPage.jsx").then((m) => ({ default: m.StudioPromptsPage })),
);
export const LazyStudioResearchPage = lazy(() =>
  import("../StudioResearchPage.jsx").then((m) => ({ default: m.StudioResearchPage })),
);
export const LazyStudioSettingsPage = lazy(() =>
  import("../StudioSettingsPage.jsx").then((m) => ({ default: m.StudioSettingsPage })),
);
export const LazyStudioCharactersPage = lazy(() =>
  import("../StudioCharactersPage.jsx").then((m) => ({ default: m.StudioCharactersPage })),
);
export const LazyStudioEditorView = lazy(() =>
  import("../StudioEditorView.jsx").then((m) => ({ default: m.StudioEditorView })),
);
