"""Plain-English summaries for agent-run worker errors (shared API + web copy)."""

from __future__ import annotations

import re

# Known worker error prefixes → user message. Order matters (first match wins).
_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (
        re.compile(r"AUTO_TIMELINE_NO_VISUALS_AT_ALL", re.I),
        "No scene had any image or video for the timeline. Start your image/video provider "
        "(for example ComfyUI on port 8188), then continue the run from the media step.",
    ),
    (
        re.compile(r"AUTO_TIMELINE_MISSING_VISUAL_", re.I),
        "At least one scene has no image or video. Generate media for that scene, or turn on "
        "scene images as a fallback, then continue.",
    ),
    (
        re.compile(r"10061|actively refused|connection refused", re.I),
        "The image or video server could not be reached (connection refused). "
        "Start ComfyUI or your configured media provider, then try again.",
    ),
    (
        re.compile(r"comfyui.*(?:unreachable|failed|error|timeout)", re.I),
        "ComfyUI did not respond. Check that it is running and that COMFYUI_BASE_URL matches your setup.",
    ),
    (
        re.compile(r"ffmpeg binary not found", re.I),
        "FFmpeg is not installed or not on PATH on the worker machine.",
    ),
    (
        re.compile(r"OPENAI|401|api key|unauthorized|authentication", re.I),
        "The text AI rejected the request — check your API key in Settings.",
    ),
]


def summarize_agent_run_failure(raw: str | None) -> str:
    s0 = str(raw or "").strip()
    if not s0:
        return "The automation run stopped with no error details."
    for pat, msg in _PATTERNS:
        if pat.search(s0):
            return msg
    # Strip long UUID lists from timeline errors for display fallback.
    short = re.sub(
        r"(AUTO_TIMELINE_NO_VISUALS_AT_ALL:\s*)[0-9a-f\-,]{80,}",
        r"\1…",
        s0,
        flags=re.I,
    )
    if len(short) > 320:
        short = f"{short[:300].rstrip()}…"
    return short
