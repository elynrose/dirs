"""Remove D0-extracted helpers from App.jsx (now in lib/studio/*)."""
from __future__ import annotations

from pathlib import Path

APP = Path(__file__).resolve().parents[2] / "web/src/App.jsx"

# 1-based inclusive line ranges to delete (merged after sort)
RANGES = [
    (343, 392),  # prompt helpers
    (515, 555),  # scene helpers (chapterHumanNumber through sceneListFallback)
    (557, 610),  # friendly pipeline status helpers
    (853, 1267),  # agent step / stall / merge pipeline
]

IMPORT_BLOCK = """
import {
  chaptersSorted,
  chapterHumanNumber,
  bestSceneListThumbAsset,
  sceneListFallbackThumbKind,
} from "./lib/studio/sceneHelpers.js";
import {
  fetchResolvedPromptsForScene,
} from "./lib/studio/promptHelpers.js";
import {
  friendlyPipelineStep,
  friendlyRunStatus,
  pipelineStopRequested,
  friendlyAgentRunStatus,
  agentRunLocksPipelineControls,
  friendlyPipelineStepStatus,
  friendlyBlockReason,
  agentThroughFromRun,
  agentStageHeadline,
  lastAgentEventWithStatus,
  lastScenesProgressEvent,
  lastAutoNarrationProgressEvent,
  agentPipelineActivityIconClass,
  jobTypeToMacroStepKey,
  inferAgentStepKeyFromActiveJobs,
  inferMacroStepKeyFromJobType,
  studioJobKindHeadline,
  resolveEffectiveAgentStepKey,
  pipelineStepActivityIconClass,
  mergePipelineStepsWithAgentActivity,
  computeAgentRunStallInfo,
} from "./lib/studio/pipelineHelpers.js";
import { StudioUsagePage } from "./components/StudioUsagePage.jsx";
import { StudioPromptsPage } from "./components/StudioPromptsPage.jsx";
"""


def main() -> None:
    lines = APP.read_text(encoding="utf-8").splitlines(keepends=True)
    remove = set()
    for start, end in RANGES:
        for i in range(start - 1, end):
            remove.add(i)
    new_lines = [ln for i, ln in enumerate(lines) if i not in remove]
    text = "".join(new_lines)
    marker = 'import { StudioIdeasPage } from "./components/StudioIdeasPage.jsx";\n'
    if "lib/studio/pipelineHelpers.js" not in text:
        text = text.replace(marker, marker + IMPORT_BLOCK)
    APP.write_text(text, encoding="utf-8")
    print(f"removed {len(remove)} lines from App.jsx")


if __name__ == "__main__":
    main()
