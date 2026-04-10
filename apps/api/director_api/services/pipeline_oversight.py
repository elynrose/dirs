"""
Pipeline oversight: merge deterministic gaps with an LLM advisory so Continue/Automate runs
re-enter at the earliest incomplete stage instead of fast-skipping past real holes.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from director_api.agents.phase2_llm import _chat_json_object_ex
from director_api.services.llm_prompt_runtime import get_llm_prompt_text
from director_api.config import Settings
from director_api.db.models import Chapter, Project, ProjectCharacter, Scene
from director_api.services import agent_resume as agent_resume_svc
from director_api.services.phase5_readiness import _project_structural_issues
from director_api.services.research_service import sanitize_jsonb_text
from sqlalchemy import func, select

log = logging.getLogger(__name__)

# Ordered pipeline checkpoints (lower index = earlier). Must match worker skip order + tail.
OVERSIGHT_STEP_RANK: dict[str, int] = {
    "director": 0,
    "research": 1,
    "outline": 2,
    "chapters": 3,
    "scenes": 4,
    "story_research_review": 5,
    "auto_characters": 6,
    "auto_images": 7,
    "auto_videos": 8,
    "auto_narration": 9,
    "auto_timeline": 10,
    "auto_rough_cut": 11,
    "auto_final_cut": 12,
}

TAIL_STEPS: tuple[str, ...] = (
    "auto_characters",
    "auto_images",
    "auto_videos",
    "auto_narration",
    "auto_timeline",
    "auto_rough_cut",
    "auto_final_cut",
)

_ISSUE_TO_STEP: dict[str, str] = {
    "no_scenes": "scenes",
    "missing_approved_scene_image": "auto_images",
    "missing_scene_narration": "auto_narration",
    "narration_audio_missing_on_disk": "auto_narration",
}


def _canonical_step(name: str | None) -> str | None:
    if not name or not isinstance(name, str):
        return None
    s = name.strip().lower().replace("-", "_")
    if s in OVERSIGHT_STEP_RANK:
        return s
    aliases = {
        "characters": "auto_characters",
        "character_bible": "auto_characters",
        "images": "auto_images",
        "video": "auto_videos",
        "videos": "auto_videos",
        "narration": "auto_narration",
        "timeline": "auto_timeline",
        "rough_cut": "auto_rough_cut",
        "final_cut": "auto_final_cut",
        "story_review": "story_research_review",
    }
    return aliases.get(s)


def oversight_blocks_resume_skip(oversight_earliest: str | None, step: str) -> bool:
    """When True, the worker must not fast-skip this step on continue_from_existing."""
    if not oversight_earliest:
        return False
    target = _canonical_step(oversight_earliest)
    cur = _canonical_step(step)
    if not target or not cur:
        return False
    rt = OVERSIGHT_STEP_RANK.get(target)
    rs = OVERSIGHT_STEP_RANK.get(cur)
    if rt is None or rs is None:
        return False
    return rs >= rt


def effective_resume_skip(cont: bool, oversight_earliest: str | None, step: str, would_skip: bool) -> bool:
    return bool(cont and would_skip and not oversight_blocks_resume_skip(oversight_earliest, step))


def parse_force_pipeline_steps(raw: Any) -> frozenset[str]:
    """Steps the client asked to re-execute even when continue_from_existing would fast-skip them."""
    if not isinstance(raw, dict):
        return frozenset()
    v = raw.get("force_pipeline_steps")
    if not isinstance(v, list):
        return frozenset()
    out: set[str] = set()
    for x in v:
        c = _canonical_step(str(x).strip())
        if c:
            out.add(c)
    return frozenset(out)


def effective_resume_skip_with_force(
    cont: bool,
    oversight_earliest: str | None,
    step: str,
    would_skip: bool,
    force_steps: frozenset[str],
) -> bool:
    """Like effective_resume_skip, but never skip when ``step`` is listed in ``force_steps``."""
    c = _canonical_step(step)
    if c and c in force_steps:
        return False
    return effective_resume_skip(cont, oversight_earliest, step, would_skip)


def tail_should_run_with_force(step: str, resume_from: str | None, force_steps: frozenset[str]) -> bool:
    """Tail sub-step runs if forced or if tail_should_run allows it."""
    c = _canonical_step(step)
    if c and c in force_steps:
        return True
    return tail_should_run(step, resume_from)


def merge_earliest_steps(a: str | None, b: str | None) -> str | None:
    """Pick the earliest pipeline stage (most conservative) among two gap hints."""
    ca, cb = _canonical_step(a), _canonical_step(b)
    candidates = [x for x in (ca, cb) if x]
    if not candidates:
        return None
    return min(candidates, key=lambda s: OVERSIGHT_STEP_RANK[s])


def earliest_gap_deterministic(
    db: Session,
    project: Project,
    storage_root: Path | None,
) -> str | None:
    """Rule-based first gap; None if no obvious structural hole."""
    if project.director_output_json is None:
        return "director"
    rk = agent_resume_svc.workflow_phase_rank(project.workflow_phase)
    if rk < 3 and agent_resume_svc.latest_dossier(db, project.id) is None:
        return "research"
    if rk < 4:
        return "outline"
    if rk < 5:
        return "chapters"
    if not agent_resume_svc.all_scripted_chapters_have_scenes(db, project):
        return "scenes"
    n_chars = (
        db.scalar(select(func.count()).select_from(ProjectCharacter).where(ProjectCharacter.project_id == project.id))
        or 0
    )
    if int(n_chars) == 0:
        return "auto_characters"
    if storage_root is not None and storage_root.is_dir():
        issues = _project_structural_issues(db, project_id=project.id, storage_root=storage_root)
        if issues:
            code = str(issues[0].get("code") or "")
            mapped = _ISSUE_TO_STEP.get(code)
            if mapped:
                return mapped
    return None


def build_oversight_snapshot(
    db: Session,
    project: Project,
    storage_root: Path | None,
) -> dict[str, Any]:
    chapters = list(
        db.scalars(select(Chapter).where(Chapter.project_id == project.id).order_by(Chapter.order_index)).all()
    )
    ch_rows: list[dict[str, Any]] = []
    for ch in chapters:
        n_sc = db.scalar(select(func.count()).select_from(Scene).where(Scene.chapter_id == ch.id)) or 0
        script = (ch.script_text or "").strip()
        ch_rows.append(
            {
                "chapter_id": str(ch.id),
                "title": sanitize_jsonb_text(ch.title or "", 200),
                "order_index": ch.order_index,
                "script_chars": len(script),
                "scene_rows": int(n_sc),
            }
        )
    issues: list[dict[str, Any]] = []
    if storage_root is not None and storage_root.is_dir():
        issues = _project_structural_issues(db, project_id=project.id, storage_root=storage_root)
    det = earliest_gap_deterministic(db, project, storage_root)
    return {
        "project_id": str(project.id),
        "workflow_phase": project.workflow_phase,
        "workflow_phase_rank": agent_resume_svc.workflow_phase_rank(project.workflow_phase),
        "topic_excerpt": sanitize_jsonb_text(project.topic or "", 1200),
        "deterministic_earliest_gap": det,
        "structural_issues": issues[:12],
        "chapters": ch_rows[:48],
    }


def oversight_llm_advisory(
    snapshot: dict[str, Any],
    *,
    settings: Settings,
    usage_sink: list[dict[str, Any]] | None = None,
) -> tuple[str | None, list[dict[str, Any]], str]:
    """
    LLM picks earliest incomplete step (may match or refine deterministic hint).
    Returns (canonical_step_or_none, gaps, rationale).
    """
    sys = get_llm_prompt_text("pipeline_oversight")
    user = json.dumps(snapshot, ensure_ascii=False)[:28000]
    data, err = _chat_json_object_ex(
        settings,
        system=sys,
        user=user,
        service_type="pipeline_oversight",
        usage_sink=usage_sink,
        temperature=0.2,
    )
    if not data or err:
        log.warning("oversight_llm_parse_failed", error=(err or "")[:400])
        return None, [], str(err or "llm_unavailable")
    raw_step = data.get("earliest_incomplete_step")
    if isinstance(raw_step, str) and raw_step.strip().lower() in ("none", "null", ""):
        return None, [], str(data.get("rationale") or "")
    step = _canonical_step(str(raw_step) if raw_step else "")
    gaps_raw = data.get("gaps")
    gaps: list[dict[str, Any]] = []
    if isinstance(gaps_raw, list):
        for g in gaps_raw[:8]:
            if isinstance(g, dict):
                gaps.append(
                    {
                        "where": str(g.get("where", ""))[:240],
                        "what": str(g.get("what", ""))[:400],
                        "severity": str(g.get("severity", "medium"))[:16],
                    }
                )
    rationale = str(data.get("rationale") or "")[:1200]
    return step, gaps, rationale


def tail_resume_from_oversight(oversight_earliest: str | None) -> str | None:
    """
    Map oversight to a tail entry point we can safely resume without rebuilding a timeline ID.
    Timeline/rough/final gaps re-run the full tail from images (return None).
    """
    o = _canonical_step(oversight_earliest)
    if o in ("auto_characters", "auto_images", "auto_videos", "auto_narration"):
        return o
    return None


def normalize_tail_resume(
    resume_from: str | None,
    *,
    auto_scene_videos: bool,
) -> str | None:
    """If resuming at auto_videos but scene videos are disabled, jump to narration."""
    if not resume_from:
        return None
    if resume_from == "auto_videos" and not auto_scene_videos:
        return "auto_narration"
    return resume_from


def tail_step_index(name: str) -> int:
    return TAIL_STEPS.index(name)


def tail_should_run(step: str, resume_from: str | None) -> bool:
    if not resume_from:
        return True
    if resume_from not in TAIL_STEPS:
        return True
    return tail_step_index(step) >= tail_step_index(resume_from)


def clamp_oversight_floor(oversight_earliest: str | None, floor: str) -> str | None:
    """
    ``floor`` is the earliest pipeline step the user chose to (re)run (``rerun_from_step`` or the
    earliest entry in ``force_pipeline_steps``). Pin resume routing to that step so an LLM gap
    cannot restart earlier phases (wiping focused runs) or pull **past** the chosen step and
    skip it (e.g. oversight ``auto_final_cut`` vs ``rerun_from_step: scenes``).
    """
    if floor not in OVERSIGHT_STEP_RANK:
        return oversight_earliest
    return floor


def merge_oversight_with_rerun_anchor(
    oversight_earliest: str | None,
    rerun_from: str | None,
) -> str | None:
    """Backward-compatible alias: clamp oversight so we never start before ``rerun_from``."""
    if not rerun_from or rerun_from not in OVERSIGHT_STEP_RANK:
        return oversight_earliest
    return clamp_oversight_floor(oversight_earliest, rerun_from)
