"""Plain-English warnings for non-fatal agent-run pipeline events."""

from __future__ import annotations

from typing import Any


def summarize_agent_run_warnings(steps_json: list[dict[str, Any]] | None) -> str:
    if not isinstance(steps_json, list):
        return ""
    parts: list[str] = []
    for ev in steps_json:
        if not isinstance(ev, dict):
            continue
        step = str(ev.get("step") or "")
        status = str(ev.get("status") or "")
        if step == "auto_videos" and status == "partial_failed":
            gen = ev.get("generated")
            if gen == 0:
                summary = ev.get("failure_reason_summary")
                parts.append(
                    str(summary)
                    if summary
                    else "Video generation was enabled but no scene videos were created."
                )
            else:
                parts.append(
                    "Some scenes are still missing videos after retries; the run continued with fallbacks."
                )
        elif step == "auto_timeline" and status == "visual_heal":
            summary = ev.get("summary") or "Timeline generated emergency still images for scenes without video."
            parts.append(str(summary))
        elif step == "publish_youtube" and status == "warning":
            err = str(ev.get("error") or "").strip()
            parts.append(
                f"YouTube upload failed after export{f': {err}' if err else '.'}"
            )
        elif step == "publish_youtube" and status == "skipped" and ev.get("reason") == "youtube_not_connected":
            parts.append(
                "YouTube publish was requested but the workspace is not connected — open Settings → Integrations."
            )
    return " ".join(parts).strip()
