"""Phase 2 — director pack, Tavily-backed research, outline helpers (no provider SDK here)."""

from __future__ import annotations

import re
import uuid
from typing import Any

from director_api.config import Settings
from director_api.db.models import Project
from director_api.services import research_service


def build_director_pack_from_project(project: Project) -> dict[str, Any]:
    return {
        "schema_id": "director-pack/v1",
        "title": project.title,
        "topic": project.topic,
        "narrative_arc": [
            "Act I — Establish the world and stakes",
            "Act II — Develop evidence and tension",
            "Act III — Resolution and perspective",
        ],
        "style_notes": {
            "tone": project.tone,
            "visual_style": project.visual_style,
            "narration_style": project.narration_style,
        },
        "production_constraints": {
            "target_runtime_minutes": project.target_runtime_minutes,
            "factual_strictness": project.factual_strictness,
            "audience": project.audience,
        },
    }


def build_research_package(
    *,
    settings: Settings,
    project: Project,
    dossier_id: uuid.UUID,
    min_sources: int,
) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]]:
    hits = research_service.search_web(project.topic, settings, min_sources)
    if not hits:
        raise ValueError(
            "RESEARCH_NO_RESULTS: Tavily returned no URLs for this topic. "
            "Broaden or rephrase the project topic and retry."
        )

    sources: list[dict[str, Any]] = []
    for i, h in enumerate(hits[:min_sources]):
        sid = uuid.uuid4()
        excerpt = research_service.extract_page_summary(h["url"], settings)
        sources.append(
            {
                "id": sid,
                "project_id": project.id,
                "dossier_id": dossier_id,
                "url_or_reference": h["url"],
                "title": (h.get("title") or "Untitled source")[:500],
                "source_type": "web",
                "credibility_score": float(h.get("score") or max(0.35, 0.85 - i * 0.08)),
                "extracted_facts_json": {"snippet": h.get("snippet"), "excerpt": excerpt},
                "notes": "Web discovery (Tavily and/or Wikipedia OpenSearch fallback) + HTML extraction.",
                "disputed": False,
            }
        )
    claims = []
    for i, s in enumerate(sources):
        excerpt = ((s.get("extracted_facts_json") or {}).get("excerpt") or "").strip()
        claim = excerpt[:280] if excerpt else (s.get("title") or "")[:280]
        if not claim:
            claim = f"Claim candidate from source {i + 1}."
        claims.append(
            {
                "id": uuid.uuid4(),
                "project_id": project.id,
                "dossier_id": dossier_id,
                "claim_text": claim,
                "confidence": float(s.get("credibility_score") or 0.5),
                "disputed": False,
                "adequately_sourced": True,
                "source_refs_json": [str(s["id"])],
            }
        )
    claims.append(
        {
            "id": uuid.uuid4(),
            "project_id": project.id,
            "dossier_id": dossier_id,
            "claim_text": "Potentially disputed interpretation; review manually before narration.",
            "confidence": 0.4,
            "disputed": True,
            "adequately_sourced": False,
            "source_refs_json": [],
        }
    )
    body = {
        "schema_id": "research-dossier/v1",
        "summary": f"Research dossier for «{project.title}» built from web discovery + page extraction.",
        "timeline": [
            {"label": "Context", "notes": f"Topic: {project.topic[:140]}"},
            {"label": "Evidence", "notes": f"Collected {len(sources)} web references"},
            {"label": "Open questions", "notes": "Review disputed claims before script generation"},
        ],
        "sources_min_met": len(sources) >= min_sources,
        "disputed_claims_flagged": True,
    }
    return body, sources, claims


# Neutral VO padding lines — read as documentary narration, not essay transitions.
_DOC_CONNECTORS: list[str] = [
    "In the years that followed, the picture would grow harder to simplify.",
    "On the ground, the story looked different from the official summary.",
    "Archives keep fragments; the narration cannot pretend the record is complete.",
    "The timeline bends — causes and effects rarely line up in a straight row.",
    "Ordinary detail, held on camera, often carries the weight of history.",
    "Local accounts added texture that headlines had flattened away.",
    "The film lingers here, long enough for the implication to land.",
    "Even now, people who were there remember the sequence differently.",
    "What began as a narrow question opened onto wider stakes.",
    "Distance offers perspective; proximity keeps the stakes human.",
    "Evidence accumulates in small pieces before the shape becomes clear.",
    "The narration stays with the human scale of the story.",
]


def target_narration_word_count(target_duration_sec: int, wpm: float = 130.0) -> int:
    """Spoken-word budget at ~wpm for a chapter target in seconds."""
    return max(40, int((max(30, target_duration_sec) / 60.0) * wpm))


def sanitize_for_script(text: str, max_len: int = 120_000) -> str:
    return research_service.sanitize_jsonb_text(text, max_len)


def script_scene_beat_paragraph_count(script_text: str) -> int:
    """
    Count narrative beats delimited by a blank line (paragraph breaks).
    Used with scene_plan_target_scenes_per_chapter > 0 to validate chapter script generation.
    """
    t = (script_text or "").strip()
    if not t:
        return 0
    parts = [p.strip() for p in re.split(r"\r?\n\s*\r?\n", t) if p.strip()]
    return len(parts)


def pad_narration_to_min_words(text: str, min_words: int, topic: str) -> str:
    """Append neutral connector sentences until min_words (for LLM outputs that land short)."""
    if min_words <= 0:
        return text
    out = text.rstrip()
    words = len(out.split())
    if words >= min_words:
        return out
    topic_snippet = (topic or "")[:220].strip()
    i = 0
    while len(out.split()) < min_words and i < 100:
        if i % 5 == 0 and topic_snippet:
            out += f" We keep the story anchored to: {topic_snippet}"
        else:
            out += " " + _DOC_CONNECTORS[i % len(_DOC_CONNECTORS)]
        i += 1
    return sanitize_for_script(out.strip(), 120_000)


def deterministic_chapter_script_emergency(
    *,
    chapter_title: str | None,
    chapter_summary: str | None,
    project_topic: str | None,
    min_words: int,
    target_scenes_per_chapter: int = 0,
) -> str:
    """
    Last-resort narration when LLM batch and per-chapter script calls return nothing usable.
    Builds VO only from outline fields (title, summary, topic) plus neutral padding — no new factual claims.
    """
    min_words_i = max(80, int(min_words or 80))
    title = ((chapter_title or "").strip() or "This chapter")[:500]
    summary_raw = (chapter_summary or "").strip()
    topic = (project_topic or "").strip()
    anchor = (topic[:220] if topic else "") or (summary_raw[:220] if summary_raw else title)
    tsp = max(0, min(48, int(target_scenes_per_chapter or 0)))

    summary = summary_raw
    if not summary:
        summary = (
            "In documentary voice, the chapter advances the story with steady pacing, holding human detail "
            "alongside the broader arc the film is tracing for the audience."
        )

    title_safe = sanitize_for_script(title, 500)
    summary_safe = sanitize_for_script(summary, 8000)
    core_seed = (
        f"In «{title_safe}», the narration continues in measured documentary style. {summary_safe}"
    )

    if tsp > 0:
        per_beat = max(40, (min_words_i + tsp - 1) // tsp)
        parts: list[str] = []
        for i in range(tsp):
            beat_prefix = f"Scene beat {i + 1} of {tsp}. "
            body = pad_narration_to_min_words(beat_prefix + core_seed, per_beat, anchor)
            parts.append(body.strip())
        joined = "\n\n".join(parts)
        if script_scene_beat_paragraph_count(joined) != tsp:
            one = pad_narration_to_min_words(core_seed, per_beat, anchor).strip()
            joined = "\n\n".join([one for _ in range(tsp)])
        return sanitize_for_script(joined.strip(), 120_000)

    return sanitize_for_script(
        pad_narration_to_min_words(core_seed, min_words_i, anchor).strip(),
        120_000,
    )


def chapter_outline_from_director(director: dict[str, Any], project: Project) -> list[dict[str, Any]]:
    arcs = director.get("narrative_arc") or ["Chapter 1", "Chapter 2", "Chapter 3"]
    total_sec = max(300, (project.target_runtime_minutes or 10) * 60)
    per = max(60, total_sec // max(1, len(arcs)))
    chapters: list[dict[str, Any]] = []
    for idx, title in enumerate(arcs):
        chapters.append(
            {
                "order_index": idx,
                "title": str(title)[:500],
                # LLM hint only — must not be read as VO; phase3 rejects this pattern without script_text.
                "summary": (
                    f"Producer note (do not use as narration): Expand «{str(title)[:500]}» into full spoken script; "
                    f"target ~{per}s at ~130 wpm. Program: «{project.title}»."
                ),
                "target_duration_sec": per,
            }
        )
    return chapters
