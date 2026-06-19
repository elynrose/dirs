#!/usr/bin/env python3
"""Extract editor JSX; bag keys = (App bindings ∪ imports) ∩ JSX tokens, minus false positives."""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "src/App.jsx"
OUT = ROOT / "src/components/StudioEditorView.jsx"

EDITOR_START = 6893
EDITOR_END = 9101

COMPONENT_IMPORTS = {
    "EditorCardColumn", "InspectorPipelinePanel", "CompiledVideoPreview", "InfoTip",
    "SkeletonSceneList", "SkeletonAssetGrid", "SkeletonMediaCanvas", "SceneWorkflowCard",
    "ExportAttentionTimelineAssetsBlock",
}

FALSE_POSITIVE = {
    "at", "b", "c", "d", "e", "em", "g", "err", "data", "i", "m", "n", "p", "s", "v", "x", "y",
    "row", "col", "ev", "a", "as", "id", "key", "ref", "src", "alt", "type", "style", "class",
    "current", "detail", "diff", "dur", "existing", "extra", "first", "fit", "clip", "fromId",
    "toId", "left", "right", "top", "bottom", "width", "height", "size", "text", "html", "body",
    "head", "meta", "link", "script", "form", "input", "label", "option", "select", "table",
    "tr", "td", "th", "div", "span", "button", "section", "header", "nav", "small", "br", "pre",
    "img", "video", "code", "strong", "summary", "details", "optgroup", "datalist", "activeAr",
    "canListStart", "blocked", "budget", "events", "assets", "clip", "generationPrompt",
    "goToChapterScene", "autoThrough", "allowUnapprovedMedia",
    "j", "idx", "i2v", "t2v", "r", "re", "rk", "ta", "tsrc", "ttype", "tv", "path", "local",
    "lib", "list", "jobs", "hints", "ok", "order", "pid", "prev", "rows", "scene", "session",
    "short", "silent", "start", "steps", "sync", "t", "used", "words", "phKind", "placeholderKind",
    "previewKind", "importKey", "isImporting", "isSelected", "mediaJobs", "planned", "readSec", "rel",
    "prompts",
}

JSX_SKIP = {
    "true", "false", "null", "undefined", "void", "return", "new", "class", "if", "else",
    "for", "while", "do", "try", "catch", "finally", "throw", "typeof", "await", "async",
    "function", "const", "let", "var", "import", "export", "from", "this", "delete", "in", "of",
}


def parse_imports(app_src: str) -> set[str]:
    names: set[str] = set()
    for m in re.finditer(
        r"import\s+(?:type\s+)?(?:\{([^}]+)\}|\*\s+as\s+(\w+)|(\w+))\s+from",
        app_src,
    ):
        if m.group(1):
            for part in m.group(1).split(","):
                part = part.strip()
                if not part:
                    continue
                if part.startswith("type "):
                    part = part[5:].strip()
                if " as " in part:
                    names.add(part.split(" as ")[-1].strip())
                else:
                    names.add(part.split("=")[0].strip())
        elif m.group(2):
            names.add(m.group(2))
        elif m.group(3):
            names.add(m.group(3))
    return names


def app_bindings(app_src: str) -> set[str]:
    names: set[str] = set()
    # Module-level functions (defined before App component)
    for m in re.finditer(r"^function\s+(\w+)\s*\(", app_src, re.MULTILINE):
        names.add(m.group(1))
    for line in app_src.splitlines():
        m = re.match(r"^\s*const\s*\[([^\]]+)\]\s*=", line)
        if m:
            for part in m.group(1).split(","):
                part = part.strip()
                if part:
                    names.add(part.split("=")[0].strip())
            continue
        m = re.match(r"^\s*const\s+(\w+)\s*=", line)
        if m:
            names.add(m.group(1))
            continue
        m = re.match(r"^\s*const\s*\{([^}]+)\}\s*=", line)
        if m:
            for part in m.group(1).split(","):
                part = part.strip()
                if not part:
                    continue
                if ":" in part:
                    part = part.split(":")[-1].strip()
                names.add(part.split("=")[0].strip())
            continue
        m = re.match(r"^\s*function\s+(\w+)\s*\(", line)
        if m:
            names.add(m.group(1))
    return names


def main() -> None:
    app_src = APP.read_text(encoding="utf-8")
    lines = app_src.splitlines()
    jsx = "\n".join(lines[EDITOR_START - 1 : EDITOR_END])

    bindings = app_bindings(app_src) | parse_imports(app_src) | COMPONENT_IMPORTS
    tokens = set(re.findall(r"(?<![.\w$])([A-Za-z_$][\w$]*)", jsx))
    bag_ids = sorted(
        t for t in tokens
        if t in bindings and t not in JSX_SKIP and t not in FALSE_POSITIVE and t not in COMPONENT_IMPORTS
    )

    # imports used in view file directly (stable helpers)
    direct_imports = sorted(
        t for t in bag_ids
        if t in parse_imports(app_src) and t not in COMPONENT_IMPORTS
    )
    bag_ids = [t for t in bag_ids if t not in direct_imports]

    destructure = ",\n    ".join(bag_ids)

    import_lines = ""
    if direct_imports:
        import_lines = (
            "import {\n  " + ",\n  ".join(direct_imports) + ",\n} from \"../lib/api.js\";\n"
            if all(x.startswith("api") for x in direct_imports)
            else ""
        )
    # TODO: group imports properly - for now add common ones manually in template

    header = '''import { EditorCardColumn } from "../editor/EditorCard.jsx";
import { InspectorPipelinePanel } from "../editor/InspectorPipelinePanel.jsx";
import { CompiledVideoPreview } from "../editor/CompiledVideoPreview.jsx";
import { InfoTip } from "./InfoTip.jsx";
import {
  SkeletonSceneList,
  SkeletonAssetGrid,
  SkeletonMediaCanvas,
} from "./LoadingSkeleton.jsx";
import { ExportAttentionTimelineAssetsBlock } from "../editor/ExportAttentionTimelineAssetsBlock.jsx";
import { useStudioEditor } from "../context/StudioEditorContext.jsx";
import { api, apiAssetContentUrl, apiSceneNarrationSubtitlesUrl, apiChapterNarrationSubtitlesUrl } from "../lib/api.js";
import { apiErrorMessage } from "../lib/apiHelpers.js";
import {
  agentRunLocksPipelineControls,
  friendlyPipelineStep,
  friendlyRunStatus,
  friendlyAgentRunStatus,
  friendlyPipelineStepStatus,
  friendlyBlockReason,
  agentStageHeadline,
  pipelineStepActivityIconClass,
  agentPipelineActivityIconClass,
  mergePipelineStepsWithAgentActivity,
} from "../lib/studio/pipelineHelpers.js";
import { chaptersSorted, chapterHumanNumber, bestSceneListThumbAsset, sceneListFallbackThumbKind } from "../lib/studio/sceneHelpers.js";
import { RUN_STEP_LABEL, AGENT_PROGRESS_ORDER, PIPELINE_STEP_TO_RERUN_FROM } from "../lib/constants.js";
import { formatPipelineStageSummary } from "../lib/studioLabels.js";

/** Editor workspace UI — state/handlers via useStudioEditor() (props must move with state). */
export function StudioEditorView() {
  const {
    ''' + destructure + '''
  } = useStudioEditor();
  return (
'''

    OUT.write_text(header + jsx + "\n  );\n}\n", encoding="utf-8")
    keys_path = ROOT / "scripts/_editor_bag_keys.txt"
    keys_path.write_text("\n".join(bag_ids), encoding="utf-8")
    print(f"bag keys: {len(bag_ids)} (direct imports in view: {len(direct_imports)})")

    # Generate bag snippet for App
    bag_snippet = ROOT / "scripts/_editor_bag_snippet.txt"
    bag_snippet.write_text("\n".join(f"            {k}," for k in bag_ids), encoding="utf-8")


if __name__ == "__main__":
    main()
