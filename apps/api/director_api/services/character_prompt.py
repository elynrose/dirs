"""Project character bible → prompt prefixes for image/video generation."""

from __future__ import annotations

import re
import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from director_api.db.models import ProjectCharacter
from director_api.services.llm_prompt_runtime import get_llm_prompt_text

_MATCH_STOPWORDS = frozenset(
    {
        "the",
        "a",
        "an",
        "of",
        "and",
        "or",
        "in",
        "on",
        "at",
        "to",
        "for",
        "is",
        "as",
        "by",
        "with",
        "from",
        "voice",
        "divine",
        "presence",
    }
)


def _ordered_characters(db: Session, project_id: uuid.UUID) -> list[ProjectCharacter]:
    return list(
        db.scalars(
            select(ProjectCharacter)
            .where(ProjectCharacter.project_id == project_id)
            .order_by(ProjectCharacter.sort_order.asc(), ProjectCharacter.name.asc())
        ).all()
    )


def _effective_match_keys(row: ProjectCharacter) -> list[str]:
    raw = row.match_keys if isinstance(row.match_keys, list) else []
    keys = [str(k).strip().lower() for k in raw if str(k).strip()]
    if keys:
        return keys
    parts = re.split(r"[^\w]+", (row.name or "").strip().lower())
    return [p for p in parts if len(p) >= 2 and p not in _MATCH_STOPWORDS]


def _name_appears_in_text(name: str, hay: str) -> bool:
    name_s = (name or "").strip()
    if not name_s or not hay.strip():
        return False
    low_name = name_s.lower()
    if low_name in hay:
        return True
    tokens = [t for t in re.split(r"[^\w]+", low_name) if len(t) >= 2 and t not in _MATCH_STOPWORDS]
    return any(t in hay for t in tokens)


def _row_matches_scene(row: ProjectCharacter, scene_text: str) -> bool:
    hay = (scene_text or "").lower()
    if not hay.strip():
        return False
    for key in _effective_match_keys(row):
        if key in hay:
            return True
    return False


def load_project_character_bible_chunks(db: Session, project_id: uuid.UUID) -> list[tuple[str, str]]:
    """(name, visual_description) pairs for per-scene bible filtering in scene planning."""
    rows = _ordered_characters(db, project_id)
    return [(r.name, r.visual_description) for r in rows]


def character_prefix_from_chunks(
    chunks: list[tuple[str, str]],
    *,
    scene_text: str,
    max_chars: int = 2000,
) -> str:
    """Build a compact prefix from chunks, keeping only characters mentioned in ``scene_text``."""
    if not chunks:
        return ""
    hay = (scene_text or "").lower()
    parts: list[str] = []
    for name, visual in chunks:
        if not _name_appears_in_text(name, hay):
            continue
        parts.append(f"{name} — {visual}")
    if not parts:
        return ""
    lead = get_llm_prompt_text("character_consistency_prefix_lead")
    text = lead + " || ".join(parts)
    return text[:max_chars] if len(text) > max_chars else text


def character_consistency_prefix_for_scene(
    db: Session,
    project_id: uuid.UUID,
    *,
    scene_text: str,
    max_chars: int = 2000,
) -> str:
    """Full visual bible for characters whose ``match_keys`` appear in this scene."""
    rows = [r for r in _ordered_characters(db, project_id) if _row_matches_scene(r, scene_text)]
    if not rows:
        return ""
    parts: list[str] = []
    for r in rows:
        chunk = f"{r.name} — {r.role_in_story}: {r.visual_description}"
        if r.time_place_scope_notes:
            chunk += f" [context: {r.time_place_scope_notes}]"
        parts.append(chunk)
    lead = get_llm_prompt_text("character_consistency_prefix_lead")
    text = lead + " || ".join(parts)
    return text[:max_chars] if len(text) > max_chars else text


def character_short_prefix_for_scene(
    db: Session,
    project_id: uuid.UUID,
    *,
    scene_text: str,
    max_chars: int = 800,
) -> str:
    """One-line visual tags for matched characters (video prompts)."""
    rows = [r for r in _ordered_characters(db, project_id) if _row_matches_scene(r, scene_text)]
    if not rows:
        return ""
    parts: list[str] = []
    for r in rows:
        tag = (r.short_visual_tag or "").strip()
        if not tag:
            vis = (r.visual_description or "").strip()
            tag = vis.split(".")[0].strip() if vis else r.name
        parts.append(f"{r.name}: {tag}")
    text = " || ".join(parts)
    return text[:max_chars] if len(text) > max_chars else text


def character_consistency_prefix(db: Session, project_id: uuid.UUID, *, max_chars: int = 2000) -> str:
    """Compact prefix for provider prompts (image/video). Empty if no characters."""
    rows = _ordered_characters(db, project_id)
    if not rows:
        return ""
    parts: list[str] = []
    for r in rows:
        chunk = f"{r.name} — {r.role_in_story}: {r.visual_description}"
        if r.time_place_scope_notes:
            chunk += f" [context: {r.time_place_scope_notes}]"
        parts.append(chunk)
    text = get_llm_prompt_text("character_consistency_prefix_lead") + " || ".join(parts)
    return text[:max_chars] if len(text) > max_chars else text


def prompt_already_has_character_prefix(prompt: str | None, prefix: str | None) -> bool:
    """True when prompt already starts with the same block (avoids doubling at image/video job time)."""
    if not prefix or not str(prefix).strip():
        return True
    p = str(prefix).strip()
    b = str(prompt or "").strip()
    return b.startswith(p)


def character_bible_for_llm_context(db: Session, project_id: uuid.UUID, *, max_chars: int = 6000) -> str:
    """Multi-line bible for scene-plan refinement (may be long)."""
    rows = _ordered_characters(db, project_id)
    if not rows:
        return ""
    lines: list[str] = []
    for r in rows:
        lines.append(f"### {r.name}")
        lines.append(f"Role: {r.role_in_story}")
        lines.append(f"Visual bible: {r.visual_description}")
        if r.time_place_scope_notes:
            lines.append(f"Time/place/scope: {r.time_place_scope_notes}")
        lines.append("")
    text = "\n".join(lines).strip()
    return text[:max_chars] if len(text) > max_chars else text
