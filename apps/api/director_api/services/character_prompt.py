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


_PORTRAIT_REFERENCE_RE = re.compile(
    r"(?i)(?:"
    r"chalkboard|blackboard|whiteboard|"
    r"(?:wall\s+)?(?:portrait|portraits|painting|paintings|photograph|photographs|photo|photos)|"
    r"statue|statues|bust|busts|mural|murals|poster|posters|banner|banners|"
    r"plaque|plaques|memorial|effigy|engraving|illustration|drawing|"
    r"framed\s+(?:image|picture|portrait)|"
    r"(?:showing|depicts?|depicting|featuring)\s+(?:a\s+)?(?:portrait|photo|painting|statue)|"
    r"on\s+the\s+(?:chalk)?board|on\s+the\s+wall|"
    r"(?:portrait|photo|painting|statue|bust)\s+of"
    r")"
)

_PHYSICAL_PRESENCE_RE = re.compile(
    r"(?i)\b(?:"
    r"stands?|standing|stood|sits?|sitting|sat|seated|"
    r"walks?|walking|walked|entering|exits?|exiting|"
    r"speaks?|speaking|spoke|addresses?|addressing|"
    r"wears?|wearing|wore|holds?|holding|held|"
    r"in\s+the\s+foreground|center\s+of\s+the\s+(?:frame|scene)|"
    r"full[\s-]body|face\s+(?:visible|shown)|"
    r"live-action\s+(?:cast|actor)|"
    r"physically\s+(?:present|on[\s-]screen)"
    r")\b"
)


def _mention_context(text: str, start: int, end: int, *, radius: int = 120) -> str:
    return text[max(0, start - radius) : min(len(text), end + radius)]


def character_appears_physically_on_screen(
    row: ProjectCharacter,
    scene_text: str,
    base_prompt: str | None = None,
) -> bool:
    """False when every mention is portrait/chalkboard/statue/background art only."""
    return name_appears_physically_on_screen(
        row.name,
        _effective_match_keys(row),
        scene_text,
        base_prompt,
    )


def name_appears_physically_on_screen(
    name: str,
    match_keys: list[str],
    scene_text: str,
    base_prompt: str | None = None,
) -> bool:
    """False when every textual mention is portrait/chalkboard/statue/background art only."""
    combined = f"{scene_text or ''}\n{base_prompt or ''}"
    hay = combined.lower()
    if not hay.strip():
        return False

    keys: list[str] = []
    seen: set[str] = set()
    name_l = (name or "").strip().lower()
    if name_l and len(name_l) >= 2:
        keys.append(name_l)
        seen.add(name_l)
    for k in match_keys:
        kl = (k or "").strip().lower()
        if len(kl) >= 2 and kl not in seen:
            seen.add(kl)
            keys.append(kl)
    if not keys:
        return False

    mention_positions: list[tuple[int, int]] = []
    for key in keys:
        start = 0
        while True:
            idx = hay.find(key, start)
            if idx < 0:
                break
            mention_positions.append((idx, idx + len(key)))
            start = idx + max(1, len(key))

    if not mention_positions:
        return False

    for start, end in mention_positions:
        window = _mention_context(combined, start, end)
        if _PHYSICAL_PRESENCE_RE.search(window):
            return True
        if not _PORTRAIT_REFERENCE_RE.search(window):
            return True
    return False


def _row_is_on_screen_subject(
    row: ProjectCharacter,
    scene_text: str,
    base_prompt: str | None = None,
) -> bool:
    if not _row_matches_scene(row, scene_text):
        return False
    return character_appears_physically_on_screen(row, scene_text, base_prompt)


def load_project_character_bible_chunks(db: Session, project_id: uuid.UUID) -> list[tuple[str, str]]:
    """(name, visual_description) pairs for per-scene bible filtering in scene planning."""
    rows = _ordered_characters(db, project_id)
    return [(r.name, r.visual_description) for r in rows]


def character_prefix_from_chunks(
    chunks: list[tuple[str, str]],
    *,
    scene_text: str,
    base_prompt: str | None = None,
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
        if not name_appears_physically_on_screen(name, [name], scene_text, base_prompt):
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
    base_prompt: str | None = None,
    max_chars: int = 2000,
) -> str:
    """Full visual bible for characters whose ``match_keys`` appear in this scene."""
    rows = [
        r
        for r in _ordered_characters(db, project_id)
        if _row_is_on_screen_subject(r, scene_text, base_prompt)
    ]
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
    base_prompt: str | None = None,
    max_chars: int = 800,
) -> str:
    """One-line visual tags for matched characters (video prompts)."""
    rows = [
        r
        for r in _ordered_characters(db, project_id)
        if _row_is_on_screen_subject(r, scene_text, base_prompt)
    ]
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
