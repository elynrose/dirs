import { Suspense } from "react";
import { StudioPageLoading } from "../studio/StudioPageLoading.jsx";
import {
  LazySettingsGenerationEnginesPanel,
  LazySettingsGenerationNarrationStylesPanel,
  LazySettingsGenerationVisualPanel,
} from "./generation/lazyPanels.js";

/** Generation tab shell — subnav + lazy sub-panels. */
export default function SettingsGenerationPanel({ p }) {
  const { generationSettingsTab, setGenerationSettingsTab } = p;
  return (
    <>
      <nav className="settings-subnav" aria-label="Generation sections">
        <button
          type="button"
          className={generationSettingsTab === "engines" ? "is-active" : ""}
          onClick={() => setGenerationSettingsTab("engines")}
        >
          Engines &amp; timing
        </button>
        <button
          type="button"
          className={generationSettingsTab === "narration_styles" ? "is-active" : ""}
          onClick={() => setGenerationSettingsTab("narration_styles")}
        >
          Narration styles
        </button>
        <button
          type="button"
          className={generationSettingsTab === "visual" ? "is-active" : ""}
          onClick={() => setGenerationSettingsTab("visual")}
        >
          Visual styles
        </button>
      </nav>
      <Suspense fallback={<StudioPageLoading label="Loading generation settings…" />}>
        {generationSettingsTab === "engines" ? <LazySettingsGenerationEnginesPanel p={p} /> : null}
        {generationSettingsTab === "narration_styles" ? (
          <LazySettingsGenerationNarrationStylesPanel p={p} />
        ) : null}
        {generationSettingsTab === "visual" ? <LazySettingsGenerationVisualPanel p={p} /> : null}
      </Suspense>
    </>
  );
}
