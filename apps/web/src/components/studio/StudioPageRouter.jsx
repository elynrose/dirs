import { Suspense } from "react";
import { LEGAL_PAGE_IDS } from "../../lib/studio/studioPageRegistry.js";
import { StudioEditorProvider } from "../../context/StudioEditorContext.jsx";
import { StudioPageLoading } from "./StudioPageLoading.jsx";
import {
  LazyStudioAccountPage,
  LazyStudioAdminPage,
  LazyStudioCharactersPage,
  LazyStudioEditorView,
  LazyStudioIdeasPage,
  LazyStudioLegalPage,
  LazyStudioPromptsPage,
  LazyStudioResearchPage,
  LazyStudioSettingsPage,
  LazyStudioUsagePage,
} from "./studioLazyPages.js";

/**
 * Lazy-loaded studio pages (chat stays mounted in App for keep-alive).
 *
 * @param {object} props
 * @param {string} props.activePage
 * @param {object} props.pages — keyed payloads from App (`ideas`, `editor`, …)
 */
export function StudioPageRouter({ activePage, pages }) {
  if (activePage === "chat") return null;

  if (LEGAL_PAGE_IDS.has(activePage)) {
    return (
      <Suspense fallback={<StudioPageLoading label="Loading…" />}>
        <LazyStudioLegalPage docId={activePage} setActivePage={pages.legal.setActivePage} />
      </Suspense>
    );
  }

  switch (activePage) {
    case "ideas":
      return (
        <Suspense fallback={<StudioPageLoading label="Loading Ideas…" />}>
          <LazyStudioIdeasPage {...pages.ideas} />
        </Suspense>
      );
    case "account":
      return (
        <Suspense fallback={<StudioPageLoading label="Loading Account…" />}>
          <LazyStudioAccountPage {...pages.account} />
        </Suspense>
      );
    case "admin":
      return (
        <Suspense fallback={<StudioPageLoading label="Loading Admin…" />}>
          <LazyStudioAdminPage {...pages.admin} />
        </Suspense>
      );
    case "usage":
      return (
        <Suspense fallback={<StudioPageLoading label="Loading Usage…" />}>
          <LazyStudioUsagePage {...pages.usage} />
        </Suspense>
      );
    case "prompts":
      return (
        <Suspense fallback={<StudioPageLoading label="Loading Prompts…" />}>
          <LazyStudioPromptsPage {...pages.prompts} />
        </Suspense>
      );
    case "research_chapters":
      return (
        <Suspense fallback={<StudioPageLoading label="Loading Research…" />}>
          <LazyStudioResearchPage {...pages.research} />
        </Suspense>
      );
    case "settings":
      return (
        <Suspense fallback={<StudioPageLoading label="Loading Settings…" />}>
          <LazyStudioSettingsPage p={pages.settings} />
        </Suspense>
      );
    case "characters":
      return (
        <Suspense fallback={<StudioPageLoading label="Loading Characters…" />}>
          <LazyStudioCharactersPage {...pages.characters} />
        </Suspense>
      );
    case "editor":
      return (
        <Suspense fallback={<StudioPageLoading label="Loading Editor…" />}>
          <StudioEditorProvider value={pages.editor.studioEditorValue}>
            <LazyStudioEditorView />
          </StudioEditorProvider>
        </Suspense>
      );
    default:
      return null;
  }
}
