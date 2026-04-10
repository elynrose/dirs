"""Project character bible → prompt prefixes for image/video generation."""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from director_api.db.models import ProjectCharacter
from director_api.services.llm_prompt_runtime import get_llm_prompt_text


def _ordered_characters(db: Session, project_id: uuid.UUID) -> list[ProjectCharacter]:
    return list(
        db.scalars(
            select(ProjectCharacter)
            .where(ProjectCharacter.project_id == project_id)
            .order_by(ProjectCharacter.sort_order.asc(), ProjectCharacter.name.asc())
        ).all()
    )


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
