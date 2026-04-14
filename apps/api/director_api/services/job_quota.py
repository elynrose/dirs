"""Per-tenant job categories (for docs/tests).

Queue caps (429) are not enforced on enqueue. Optional **credit budget** blocking uses
:func:`usage_credits.assert_credit_budget` when tenant entitlements set ``credits_enforce`` and ``monthly_credits``.
"""

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from director_api.config import Settings
from director_api.db.models import Job
from director_api.services.usage_credits import assert_credit_budget

COMPILE_TYPES = frozenset(
    {
        "rough_cut",
        "fine_cut",
        "final_cut",
        "export",
        "narration_generate",
        "narration_generate_scene",
        "subtitles_generate",
        "youtube_upload",
    }
)
MEDIA_TYPES = frozenset({"scene_generate_image", "scene_generate_video", "scene_generate", "scene_extend"})
TEXT_TYPES = frozenset(
    {
        "research_run",
        "script_outline",
        "script_chapters",
        "script_chapter_regenerate",
        "characters_generate",
        "scene_critique",
        "chapter_critique",
        "scene_critic_revision",
    }
)


def active_job_count(db: Session, *, tenant_id: str, types: frozenset[str]) -> int:
    return int(
        db.scalar(
            select(func.count())
            .select_from(Job)
            .where(
                Job.tenant_id == tenant_id,
                Job.status.in_(("queued", "running")),
                Job.type.in_(types),
            )
        )
        or 0
    )


def assert_can_enqueue(db: Session, settings: Settings, job_type: str, *, tenant_id: str | None = None) -> None:
    """Optional credit-budget gate; job-cap 429s remain disabled (see module docstring)."""
    tid = (tenant_id or settings.default_tenant_id or "").strip()
    if tid:
        assert_credit_budget(db, settings, tid)
