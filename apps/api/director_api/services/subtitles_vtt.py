"""Naive WebVTT from script text (equal time split by paragraph weight)."""

from __future__ import annotations

from typing import Any


def assemble_project_subtitle_markdown(chapters: list[Any], scenes_ordered: list[Any]) -> tuple[str, float]:
    """
    Build the markdown body passed to :func:`script_to_webvtt` for project-level subtitles.

    Primary source: each scene's ``narration_text`` in story order (chapter ``order_index``,
    then scene ``order_index``). Headings use ``chapter.title`` and scene ``purpose`` or a
    default label.

    Fallback: if no scene has narration text, concatenate chapter ``script_text`` blocks
    (legacy chapter-only scripts).

    ``total_sec`` is the sum of ``planned_duration_sec`` over all ordered scenes (for timing
    against the visual timeline); falls back to ``0.0`` when empty.
    """
    chapter_by_id = {c.id: c for c in chapters}
    total_sec = sum(float(getattr(sc, "planned_duration_sec", None) or 0) for sc in scenes_ordered)

    blocks: list[str] = []
    for sc in scenes_ordered:
        nt = (getattr(sc, "narration_text", None) or "").strip()
        if not nt:
            continue
        ch = chapter_by_id.get(getattr(sc, "chapter_id", None))
        if ch is None:
            continue
        title = (getattr(ch, "title", None) or "Chapter").strip()
        purpose = (getattr(sc, "purpose", None) or "").strip()[:200]
        oi = int(getattr(sc, "order_index", 0) or 0)
        label = purpose or f"Scene {oi + 1}"
        blocks.append(f"## {title} · {label}\n\n{nt}")

    if not blocks:
        for ch in chapters:
            st = (getattr(ch, "script_text", None) or "").strip()
            if st:
                ct = (getattr(ch, "title", None) or "Chapter").strip()
                blocks.append(f"## {ct}\n\n{st}")

    return "\n\n".join(blocks).strip(), total_sec


def _fmt_ts(sec: float) -> str:
    if sec < 0:
        sec = 0.0
    h = int(sec // 3600)
    m = int((sec % 3600) // 60)
    s = sec - h * 3600 - m * 60
    return f"{h:02d}:{m:02d}:{s:06.3f}"


def _escape_vtt(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;")


def script_to_webvtt(script: str, *, total_sec: float) -> str:
    """Split ``script`` into cues; duration proportional to character count."""
    text = script.strip()
    if not text:
        return "WEBVTT\n\n"
    paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]
    if not paragraphs:
        paragraphs = [text]
    total_sec = max(float(total_sec), 1.0)
    weights = [max(8, len(p)) for p in paragraphs]
    wsum = float(sum(weights))
    lines = ["WEBVTT", ""]
    t0 = 0.0
    for p, w in zip(paragraphs, weights, strict=True):
        dur = total_sec * (w / wsum)
        t1 = min(total_sec, t0 + dur)
        lines.append(f"{_fmt_ts(t0)} --> {_fmt_ts(t1)}")
        lines.append(_escape_vtt(p))
        lines.append("")
        t0 = t1
    return "\n".join(lines) + "\n"
