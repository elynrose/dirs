"""Phase 3 job implementation — scene planning, image, and video generation.

Shared runtime helpers live in ``worker_tasks`` and are accessed via :func:`_wt` to avoid
circular imports during Celery/API startup.
"""
from __future__ import annotations

import shutil
import uuid
from pathlib import Path
from typing import Any

import structlog
from sqlalchemy import select
from sqlalchemy.orm import Session

from director_api.tasks.worker_helpers import worker_tenant_id
from director_api.db.models import Asset, Chapter, Job, Project, Scene
from director_api.providers.media_comfyui import generate_scene_image_comfyui, generate_scene_video_comfyui
from director_api.providers.media_fal import (
    fal_model_is_image_to_video,
    format_fal_result_message,
    generate_scene_video_fal,
)
from director_api.services.image_provider_routing import dispatch_image_generation
from director_api.services.character_prompt import (
    character_bible_for_llm_context,
    character_consistency_prefix,
)
from director_api.services.image_prompt_assembly import (
    assemble_scene_still_image_prompt,
    scene_text_for_character_match,
)
from director_api.agents import phase3_llm
from director_api.services import phase3 as phase3_svc
from director_api.services import agent_resume as agent_resume_svc
from director_api.services.narration_bracket_visual import (
    base_image_prompt_from_scene_fields,
    maybe_prepend_topic_setting_anchor,
)
from director_api.services.prompt_enhance import refine_bracket_visual_prompt_llm
from director_api.services.research_service import sanitize_jsonb_text
from director_api.services.clip_duration import clip_seconds_for_scene
from director_api.storage.filesystem import FilesystemStorage
from director_api.style_presets import effective_narration_style, effective_visual_style
from director_api.validation.phase3_schemas import validate_scene_plan_batch

from ffmpeg_pipelines.errors import FFmpegCompileError
from ffmpeg_pipelines.paths import path_from_storage_url, path_is_readable_file
from ffmpeg_pipelines.slideshow import compile_image_slideshow
from ffmpeg_pipelines.still_to_video import encode_image_to_mp4
from director_api.tasks.media_normalize_helpers import (
    _image_bytes_magic_ok,
    _normalize_image_bytes_to_dims,
    _normalize_video_bytes_to_dims,
    _package_negative_prompt,
    _project_export_dimensions,
)
from director_api.tasks.prompt_runtime_helpers import (
    _local_ffmpeg_motion_from_video_prompt,
    _resolve_phase3_video_text_prompt,
    _scene_still_prompt_for_comfy,
    _scene_video_prompt_for_provider,
)

log = structlog.get_logger(__name__)


def _schedule_scene_precompile(db: Session, settings: Any, asset: Asset) -> None:
    try:
        from director_api.services.scene_precompile_enqueue import schedule_scene_precompile_if_on_timeline

        schedule_scene_precompile_if_on_timeline(db, settings, asset)
    except Exception as e:  # noqa: BLE001
        log.warning(
            "scene_precompile_schedule_failed",
            asset_id=str(asset.id),
            error=str(e)[:400],
        )

_WT = None


def _wt():
    """Late bind to ``worker_tasks`` after it has finished loading."""
    global _WT
    if _WT is None:
        import director_api.tasks.worker_tasks as m

        _WT = m
    return _WT


def _phase3_video_job_result(asset: Asset) -> dict[str, Any]:
    out: dict[str, Any] = {"asset_id": str(asset.id), "ok": asset.status == "succeeded"}
    if asset.status != "succeeded" and asset.error_message:
        out["error_message"] = str(asset.error_message)[:2000]
    return out


def _phase3_scenes_plan_for_chapter(
    db,
    chapter: Chapter,
    project: Project,
    settings: Any,
    *,
    cached_character_consistency_prefix: str | None = None,
    cached_character_bible_for_llm: str | None = None,
    confirm_erase_assets: bool = False,
) -> None:
    """Agentic scene planning (same as scene_generate job body)."""
    if not phase3_svc.chapter_eligible_for_scene_planning(chapter):
        raise ValueError(
            "chapter needs script_text or a substantive summary (12+ chars) before scene planning"
        )
    # Defence-in-depth erase gate: the loop near the bottom does
    # ``db.delete(sc)`` for every existing scene, which cascades to all
    # image/video Asset rows under that chapter. Refuse without explicit
    # consent. See :mod:`director_api.services.erase_consent` for the full
    # rationale and the matching API-side check.
    from director_api.services.erase_consent import assert_chapter_replan_erase_consent

    assert_chapter_replan_erase_consent(chapter, consent=confirm_erase_assets)

    vis_prompt = effective_visual_style(project.visual_style, settings)
    if cached_character_consistency_prefix is None:
        char_prefix = character_consistency_prefix(db, project.id, max_chars=2000)
    else:
        char_prefix = cached_character_consistency_prefix
    try:
        min_sc = max(0, min(48, int(getattr(settings, "scene_plan_target_scenes_per_chapter", 0) or 0)))
    except (TypeError, ValueError):
        min_sc = 0
    try:
        clip_sec = int(getattr(settings, "scene_clip_duration_sec", None) or 10)
    except (TypeError, ValueError):
        clip_sec = 10
    if clip_sec not in (5, 10):
        clip_sec = 10
    seed_batch = phase3_svc.build_scene_plan_batch(
        chapter,
        project,
        visual_style_prompt=vis_prompt,
        min_scenes=min_sc,
        scene_clip_duration_sec=clip_sec,
        character_consistency_prefix=char_prefix or None,
    )
    batch = seed_batch
    refined = None
    llm_u: list[dict[str, Any]] = []
    if cached_character_bible_for_llm is None:
        char_ctx = character_bible_for_llm_context(db, project.id, max_chars=6000)
    else:
        char_ctx = cached_character_bible_for_llm
    plan_hints = phase3_svc.scene_plan_refine_context(chapter, settings)
    if not bool(getattr(settings, "agent_run_fast", False)):
        refined = phase3_llm.refine_scene_plan_batch(
            seed_batch,
            chapter_title=chapter.title,
            project_topic=project.topic,
            settings=settings,
            narration_style=effective_narration_style(
                project.narration_style, settings, db=db, tenant_id=project.tenant_id
            ),
            planning_hints=plan_hints,
            target_duration_sec=chapter.target_duration_sec,
            character_bible=char_ctx or None,
            frame_aspect_ratio=str(getattr(project, "frame_aspect_ratio", None) or "16:9"),
            visual_style_resolved=vis_prompt,
            visual_preset_id=phase3_svc.resolve_visual_preset_id_for_project(project, settings),
            usage_sink=llm_u,
            no_narration=bool(getattr(project, "no_narration", False)),
        )
    _wt()._flush_llm_usage(db, project.tenant_id, project.id, None, None, llm_u)
    if refined:
        try:
            validate_scene_plan_batch(refined)
            n_ref = len(refined.get("scenes") or [])
            n_seed = len(seed_batch.get("scenes") or [])
            collapsed = n_ref == 1 and n_seed > 1
            under_floor = min_sc > 0 and n_ref < min_sc
            wct = int(plan_hints.get("word_count") or 0)
            sugg = int(plan_hints.get("suggested_scene_count") or 1)
            # LLM often collapses to one scene; keep seed when hints call for 2+ beats (not only long scripts).
            ref_too_coarse = n_ref == 1 and sugg >= 2 and wct >= 18
            if not collapsed and not under_floor and not ref_too_coarse:
                batch = refined
        except Exception:
            pass
    validate_scene_plan_batch(batch)

    from director_api.services.publish_hook import (
        delete_chapter_scenes_preserving_hook,
        order_index_offset_for_chapter_replan,
    )

    delete_chapter_scenes_preserving_hook(db, chapter, project.id)
    idx_offset = order_index_offset_for_chapter_replan(db, chapter)

    for item in sorted(batch["scenes"], key=lambda x: int(x["order_index"])):
        pp = item.get("prompt_package_json")
        if not isinstance(pp, dict):
            pp = {}
        for pref_key in ("preferred_image_provider", "preferred_video_provider"):
            if item.get(pref_key):
                pp[f"_{pref_key}"] = str(item[pref_key])[:64]
        phase3_svc.merge_stock_search_terms_from_plan_row(item, pp)
        ct = item.get("continuity_tags_json")
        if not isinstance(ct, list):
            ct = []
        ct = [str(x)[:256] for x in ct if x is not None][:32]
        narr_out = str(item["narration_text"])
        if getattr(project, "no_narration", False):
            narr_out = phase3_svc.NO_NARRATION_SCENE_TEXT
        db.add(
            Scene(
                id=uuid.uuid4(),
                chapter_id=chapter.id,
                order_index=int(item["order_index"]) + idx_offset,
                purpose=sanitize_jsonb_text(str(item["purpose"]), 2000),
                planned_duration_sec=int(item["planned_duration_sec"]),
                narration_text=sanitize_jsonb_text(narr_out, 12_000),
                visual_type=str(item["visual_type"])[:64],
                prompt_package_json=pp,
                continuity_tags_json=ct,
                status="planned",
            )
        )
    if agent_resume_svc.all_scripted_chapters_have_scenes(db, project):
        project.workflow_phase = "scenes_planned"
    else:
        project.workflow_phase = "chapters_ready"
    db.flush()


def _phase3_scenes_generate(db, job: Job) -> None:
    payload = job.payload or {}
    cid = uuid.UUID(str(payload["chapter_id"]))
    chapter = db.get(Chapter, cid)
    if not chapter:
        raise ValueError("chapter not found")
    project = db.get(Project, chapter.project_id)
    if not project:
        raise ValueError("project not found")
    if project.tenant_id != job.tenant_id:
        raise ValueError("job tenant does not match project")
    settings = _wt()._worker_runtime_for_job(db, job)
    _phase3_scenes_plan_for_chapter(
        db,
        chapter,
        project,
        settings,
        confirm_erase_assets=bool(payload.get("confirm_erase_assets")),
    )


def _phase3_scene_extend(db, job: Job) -> dict[str, Any]:
    """Append one scene after existing plans, using LLM (or deterministic seed when agent_run_fast)."""
    db.refresh(job)
    payload = job.payload if isinstance(job.payload, dict) else {}
    raw_cid = payload.get("chapter_id")
    if raw_cid is None:
        raise ValueError("scene_extend job missing chapter_id in payload")
    cid = uuid.UUID(str(raw_cid))
    chapter = db.get(Chapter, cid)
    if not chapter:
        log.error("scene_extend_chapter_missing", job_id=str(job.id), chapter_id=str(raw_cid))
        raise ValueError("chapter not found")
    if job.project_id and chapter.project_id != job.project_id:
        log.error(
            "scene_extend_chapter_project_mismatch",
            job_id=str(job.id),
            chapter_id=str(chapter.id),
            job_project_id=str(job.project_id),
            chapter_project_id=str(chapter.project_id),
        )
        raise ValueError("scene_extend job project_id does not match chapter.project_id")
    project = db.get(Project, chapter.project_id)
    if not project:
        raise ValueError("project not found")
    if not phase3_svc.chapter_eligible_for_scene_extend(chapter):
        raise ValueError(
            "chapter needs script_text, substantive summary, or enough text in existing scene beats "
            "before extending scenes"
        )
    if project.tenant_id != job.tenant_id:
        raise ValueError("job tenant does not match project")
    settings = _wt()._worker_runtime_for_job(db, job)
    scenes_sorted = sorted(list(chapter.scenes), key=lambda s: int(s.order_index or 0))
    if not scenes_sorted:
        raise ValueError("no existing scenes — run Plan scenes first")
    if len(scenes_sorted) >= 48:
        raise ValueError("chapter already has the maximum number of scenes (48)")
    next_idx = max(int(s.order_index or 0) for s in scenes_sorted) + 1

    vis_prompt = effective_visual_style(project.visual_style, settings)
    char_ctx = character_bible_for_llm_context(db, project.id, max_chars=6000)
    plan_hints = phase3_svc.scene_plan_refine_context(chapter, settings)
    try:
        clip = int(plan_hints.get("scene_clip_duration_sec") or 10)
    except (TypeError, ValueError):
        clip = 10
    if clip not in (5, 10):
        clip = 10

    existing: list[dict[str, Any]] = []
    for s in scenes_sorted[-4:]:
        ct = s.continuity_tags_json if isinstance(s.continuity_tags_json, list) else []
        existing.append(
            {
                "order_index": int(s.order_index or 0),
                "purpose": (s.purpose or "")[:400],
                "narration_text": (s.narration_text or "")[:2000],
                "visual_type": s.visual_type or "",
                "continuity_tags": [str(x)[:128] for x in ct[:8]],
            }
        )

    llm_u: list[dict[str, Any]] = []
    batch: dict[str, Any] | None = None
    if not bool(getattr(settings, "agent_run_fast", False)):
        batch = phase3_llm.extend_scene_plan_batch(
            existing,
            chapter_title=chapter.title or "",
            chapter_script=(chapter.script_text or ""),
            chapter_summary=(chapter.summary or ""),
            project_topic=project.topic or "",
            settings=settings,
            narration_style=effective_narration_style(
                project.narration_style, settings, db=db, tenant_id=project.tenant_id
            ),
            target_duration_sec=chapter.target_duration_sec,
            scene_clip_sec=clip,
            character_bible=char_ctx or None,
            frame_aspect_ratio=str(getattr(project, "frame_aspect_ratio", None) or "16:9"),
            visual_style_resolved=vis_prompt,
            visual_preset_id=phase3_svc.resolve_visual_preset_id_for_project(project, settings),
            usage_sink=llm_u,
        )
    if not batch:
        last = scenes_sorted[-1]
        narrs = [(x.narration_text or "") for x in scenes_sorted]
        batch = phase3_svc.build_extend_scene_deterministic(
            chapter,
            project,
            prior_narrations=narrs,
            last_visual_type=str(last.visual_type or ""),
            visual_style_prompt=vis_prompt,
            character_consistency_prefix=character_consistency_prefix(db, project.id, max_chars=2000) or None,
        )
    _wt()._flush_llm_usage(db, project.tenant_id, project.id, None, None, llm_u)
    validate_scene_plan_batch(batch)
    items = batch.get("scenes") or []
    if not items:
        raise ValueError("extend_scene produced no scenes")
    item = dict(items[0])
    item["order_index"] = next_idx

    pp = item.get("prompt_package_json")
    if not isinstance(pp, dict):
        pp = {}
    for pref_key in ("preferred_image_provider", "preferred_video_provider"):
        if item.get(pref_key):
            pp[f"_{pref_key}"] = str(item[pref_key])[:64]
    phase3_svc.merge_stock_search_terms_from_plan_row(item, pp)
    ct = item.get("continuity_tags_json")
    if not isinstance(ct, list):
        ct = []
    ct = [str(x)[:256] for x in ct if x is not None][:32]
    narr_out = str(item["narration_text"])
    if getattr(project, "no_narration", False):
        narr_out = phase3_svc.NO_NARRATION_SCENE_TEXT
    new_id = uuid.uuid4()
    db.add(
        Scene(
            id=new_id,
            chapter_id=chapter.id,
            order_index=int(item["order_index"]),
            purpose=sanitize_jsonb_text(str(item["purpose"]), 2000),
            planned_duration_sec=int(item["planned_duration_sec"]),
            narration_text=sanitize_jsonb_text(narr_out, 12_000),
            visual_type=str(item["visual_type"])[:64],
            prompt_package_json=pp,
            continuity_tags_json=ct,
            status="planned",
        )
    )
    if agent_resume_svc.all_scripted_chapters_have_scenes(db, project):
        project.workflow_phase = "scenes_planned"
    db.flush()
    log.info(
        "scene_extend_appended",
        chapter_id=str(chapter.id),
        scene_id=str(new_id),
        order_index=next_idx,
    )
    return {"scene_id": str(new_id)}


def _phase3_scene_still_job_succeeded(out: Any, db: Session) -> bool:
    """True when scene_generate_image produced a usable succeeded image (not failed/stopped)."""
    if not isinstance(out, dict):
        return False
    if out.get("stopped"):
        return False
    o = out.get("ok")
    if o is False:
        return False
    if o is True:
        return True
    aid = out.get("asset_id")
    if not aid:
        return False
    try:
        ast = db.get(Asset, uuid.UUID(str(aid)))
    except Exception:
        return False
    return bool(
        ast is not None
        and getattr(ast, "asset_type", None) == "image"
        and ast.status == "succeeded"
    )


def _phase3_image_generate(db, job: Job) -> dict[str, Any]:
    settings = _wt()._worker_runtime_for_job(db, job)
    storage = FilesystemStorage(settings.local_storage_root)
    payload = job.payload or {}
    sid = uuid.UUID(str(payload["scene_id"]))
    tier = str(payload.get("generation_tier") or "preview")

    scene = db.get(Scene, sid)
    if not scene:
        raise ValueError("scene not found")
    chapter = db.get(Chapter, scene.chapter_id)
    if not chapter:
        raise ValueError("chapter not found")
    project = db.get(Project, chapter.project_id)
    if not project:
        raise ValueError("project not found")
    if project.tenant_id != job.tenant_id:
        raise ValueError("job tenant does not match project")
    tenant_id = worker_tenant_id(job, payload)
    exp_w, exp_h = _wt()._project_export_dimensions(project)

    ar_uuid = _wt()._payload_agent_run_uuid(payload)
    if ar_uuid is not None and _wt()._agent_run_checkpoint(db, ar_uuid) == "stop":
        return {"ok": False, "error_message": "Stopped by user", "stopped": True}

    pp = scene.prompt_package_json if isinstance(scene.prompt_package_json, dict) else {}
    override = payload.get("image_prompt_override")
    # See ``services.scene_coverage.pick_coverage_payload`` — suffix is
    # appended (coverage variant), override replaces (explicit user edit).
    suffix = payload.get("image_prompt_suffix")
    vis_style = effective_visual_style(project.visual_style, settings)
    prompt, used_brackets, bracket_phrases = base_image_prompt_from_scene_fields(
        narration_text=scene.narration_text,
        prompt_package_json=pp,
        image_prompt_override=override if isinstance(override, str) else None,
        visual_style_effective=vis_style,
        image_prompt_suffix=suffix if isinstance(suffix, str) else None,
    )
    bracket_llm_refined = False
    want_refine = bool(payload.get("refine_bracket_visual_with_llm"))
    if (
        bracket_phrases
        and want_refine
        and not (isinstance(override, str) and override.strip())
    ):
        refined, rerr = refine_bracket_visual_prompt_llm(
            db,
            settings,
            scene_id=scene.id,
            draft_prompt=prompt,
            bracket_phrases=bracket_phrases,
            narration_excerpt=scene.narration_text,
        )
        if refined:
            prompt = sanitize_jsonb_text(refined, 4000)
            bracket_llm_refined = True
        elif rerr:
            log.warning(
                "bracket_visual_llm_refine_failed",
                scene_id=str(scene.id),
                err=str(rerr)[:500],
            )

    bracket_visual_audit: dict[str, Any] | None = None
    if bracket_phrases:
        bracket_visual_audit = {
            "phrases": bracket_phrases,
            "refined_with_llm": bracket_llm_refined,
            "used_bracket_hints": used_brackets,
        }

    prompt = str(prompt).strip()
    prompt = assemble_scene_still_image_prompt(
        db,
        scene,
        project,
        settings,
        prompt,
        exclude_character_bible=bool(payload.get("exclude_character_bible")),
        automation_character_prefix=(
            str(payload.get("_automation_character_prefix") or "")[:2000]
            if "_automation_character_prefix" in payload
            else None
        ),
    )

    scene_neg = _package_negative_prompt(pp, project=project, settings=settings)

    payload_override = payload.get("image_provider")
    if isinstance(payload_override, str) and payload_override.strip():
        requested = payload_override.strip()
    else:
        requested = (
            pp.get("_preferred_image_provider")
            or pp.get("preferred_image_provider")
            or project.preferred_image_provider
            or getattr(settings, "active_image_provider", None)
            or "fal"
        )
    req_l = str(requested).lower().strip()
    if req_l in ("auto", "default", ""):
        req_l = str(getattr(settings, "active_image_provider", None) or "fal").lower().strip()
    if req_l == "xai":
        req_l = "grok"
    if req_l == "google":
        req_l = "gemini"

    if req_l == "placeholder":
        from director_api.providers.media_placeholder import render_placeholder_scene_png_bytes

        image_params_ph: dict[str, Any] = {
            "continuity_tags_json": scene.continuity_tags_json,
            "continuity_tags_summary": (scene.continuity_tags_json or [])
            if isinstance(scene.continuity_tags_json, list)
            else [],
            "prompt_package_json": scene.prompt_package_json,
            "image_prompt_used": prompt[:4000],
            "routing_audit": {"requested_provider": requested, "resolved_provider": "placeholder"},
        }
        if bracket_visual_audit:
            image_params_ph["bracket_visual"] = bracket_visual_audit
        asset_ph = Asset(
            id=uuid.uuid4(),
            tenant_id=tenant_id,
            scene_id=scene.id,
            project_id=project.id,
            asset_type="image",
            status="running",
            generation_tier=tier,
            timeline_sequence=_wt()._next_timeline_sequence_for_scene(db, scene.id),
            provider="placeholder",
            model_name="lavfi_color",
            params_json=image_params_ph,
        )
        db.add(asset_ph)
        db.flush()
        try:
            raw_png = render_placeholder_scene_png_bytes(
                ffmpeg_bin=(settings.ffmpeg_bin or "ffmpeg").strip() or "ffmpeg",
                timeout_sec=min(float(settings.ffmpeg_timeout_sec), 120.0),
                width=exp_w,
                height=exp_h,
            )
            img_bytes, content_type, norm_trusted = _normalize_image_bytes_to_dims(
                settings, raw_png, "image/png", exp_w, exp_h
            )
            if not (norm_trusted or _image_bytes_magic_ok(img_bytes)):
                raise RuntimeError("placeholder PNG normalize failed or invalid magic")
            ext = "png" if "png" in content_type.lower() else "jpg"
            key = f"assets/{project.id}/{scene.id}/{asset_ph.id}.{ext}"
            url = storage.put_bytes(key, img_bytes)
            _wt()._bind_asset_local_file(asset_ph, url, key)
            asset_ph.status = "succeeded"
            asset_ph.error_message = None
            scene.status = "image_ready"
            _wt()._record_usage(
                db,
                tenant_id=tenant_id,
                project_id=project.id,
                scene_id=scene.id,
                asset_id=asset_ph.id,
                provider="placeholder",
                service_type="image_gen",
                meta={"ok": True, "model": "placeholder", "tier": tier},
            )
        except Exception as exc:
            asset_ph.status = "failed"
            asset_ph.error_message = str(exc)[:8000]
            _wt()._record_usage(
                db,
                tenant_id=tenant_id,
                project_id=project.id,
                scene_id=scene.id,
                asset_id=asset_ph.id,
                provider="placeholder",
                service_type="image_gen",
                meta={"ok": False, "error": str(exc)[:500], "tier": tier},
            )
        db.flush()
        out_ph: dict[str, Any] = {"asset_id": str(asset_ph.id), "ok": asset_ph.status == "succeeded"}
        if asset_ph.status != "succeeded" and asset_ph.error_message:
            out_ph["error_message"] = str(asset_ph.error_message)[:2000]
        return out_ph

    if req_l not in ("fal", "comfyui", "comfy", "openai", "grok", "gemini"):
        failed_params: dict[str, Any] = {
            "continuity_tags_json": scene.continuity_tags_json,
            "continuity_tags_summary": scene.continuity_tags_json
            if isinstance(scene.continuity_tags_json, list)
            else [],
            "image_prompt_used": prompt[:2000],
            "routing_audit": {"requested_provider": requested, "resolved_provider": None},
        }
        if bracket_visual_audit:
            failed_params["bracket_visual"] = bracket_visual_audit
        asset = Asset(
            id=uuid.uuid4(),
            tenant_id=tenant_id,
            scene_id=scene.id,
            project_id=project.id,
            asset_type="image",
            status="failed",
            generation_tier=tier,
            timeline_sequence=_wt()._next_timeline_sequence_for_scene(db, scene.id),
            provider=str(requested)[:64],
            model_name=None,
            params_json=failed_params,
            error_message=(
                f"Image provider '{requested}' is not supported; use fal, ComfyUI, OpenAI, Grok, "
                f"Gemini, or placeholder (see scripts/budget_pipeline_test.py / DIRECTOR_PLACEHOLDER_MEDIA)."
            ),
        )
        db.add(asset)
        db.flush()
        _wt()._record_usage(
            db,
            tenant_id=tenant_id,
            project_id=project.id,
            scene_id=scene.id,
            asset_id=asset.id,
            provider=str(requested)[:64],
            service_type="image_gen",
            meta={"ok": False, "skipped": True, "reason": "provider_not_supported", "tier": tier},
        )
        db.flush()
        return {
            "asset_id": str(asset.id),
            "ok": False,
            "error_message": asset.error_message or "provider_not_supported",
        }

    fal_image_override = payload.get("fal_image_model")
    if not isinstance(fal_image_override, str) or not fal_image_override.strip():
        fal_image_override = None
    else:
        fal_image_override = fal_image_override.strip().lstrip("/")

    resolved_provider = "fal"
    model_name = fal_image_override or settings.fal_smoke_model
    if req_l in ("comfyui", "comfy"):
        resolved_provider = "comfyui"
        wf = (settings.comfyui_workflow_json_path or "").strip()
        model_name = (settings.comfyui_model_name or "").strip() or (
            Path(wf).name if wf else "comfyui"
        )
    elif req_l == "openai":
        resolved_provider = "openai"
        model_name = (getattr(settings, "openai_image_model", None) or "gpt-image-1").strip()
    elif req_l == "grok":
        resolved_provider = "grok"
        model_name = (getattr(settings, "grok_image_model", None) or "grok-2-image-1212").strip()
    elif req_l == "gemini":
        resolved_provider = "gemini"
        model_name = (getattr(settings, "gemini_image_model", None) or "imagen-4.0-generate-001").strip()

    image_params: dict[str, Any] = {
        "continuity_tags_json": scene.continuity_tags_json,
        "continuity_tags_summary": (scene.continuity_tags_json or [])
        if isinstance(scene.continuity_tags_json, list)
        else [],
        "prompt_package_json": scene.prompt_package_json,
        "image_prompt_used": prompt[:4000],
        "routing_audit": {"requested_provider": requested, "resolved_provider": resolved_provider},
    }
    if bracket_visual_audit:
        image_params["bracket_visual"] = bracket_visual_audit
    if resolved_provider == "comfyui":
        image_params["comfyui_base_url"] = (settings.comfyui_base_url or "")[:256]
        image_params["comfyui_workflow_json_path"] = (settings.comfyui_workflow_json_path or "")[:512]
        image_params["comfyui_api_flavor"] = str(
            getattr(settings, "comfyui_api_flavor", "oss") or "oss"
        )[:32]

    asset = Asset(
        id=uuid.uuid4(),
        tenant_id=tenant_id,
        scene_id=scene.id,
        project_id=project.id,
        asset_type="image",
        status="running",
        generation_tier=tier,
        timeline_sequence=_wt()._next_timeline_sequence_for_scene(db, scene.id),
        provider=resolved_provider,
        model_name=model_name,
        params_json=image_params,
    )
    db.add(asset)
    db.flush()

    log.info(
        "phase3_image_dispatch",
        job_id=str(job.id),
        scene_id=str(scene.id),
        resolved_provider=resolved_provider,
        model_name=model_name,
        fal_key_configured=bool((settings.fal_key or "").strip()),
    )

    if resolved_provider == "comfyui":
        res = generate_scene_image_comfyui(
            settings,
            str(prompt),
            negative_prompt=scene_neg,
            frame_aspect_ratio=str(getattr(project, "frame_aspect_ratio", None) or "16:9"),
            generation_tier=tier,
        )
    else:
        res = dispatch_image_generation(
            settings,
            resolved_provider,
            str(prompt),
            model_path=fal_image_override if resolved_provider == "fal" else None,
            negative_prompt=scene_neg,
            frame_aspect_ratio=str(getattr(project, "frame_aspect_ratio", None) or "16:9"),
        )

    if res.get("ok") and res.get("bytes"):
        content_type = str(res.get("content_type") or "image/png")
        img_bytes, content_type, norm_trusted = _normalize_image_bytes_to_dims(
            settings, res["bytes"], content_type, exp_w, exp_h
        )
        if not (norm_trusted or _image_bytes_magic_ok(img_bytes)):
            asset.status = "failed"
            asset.error_message = (
                "Image bytes were empty or not a recognized image format after generation/normalize "
                "(check fal model output and ffmpeg image step)."
            )[:8000]
            _wt()._record_usage(
                db,
                tenant_id=tenant_id,
                project_id=project.id,
                scene_id=scene.id,
                asset_id=asset.id,
                provider=str(res.get("provider") or resolved_provider),
                service_type="image_gen",
                meta={"ok": False, "error": "invalid_image_bytes", "tier": tier},
            )
        else:
            ext = "png" if "png" in content_type.lower() else "jpg"
            key = f"assets/{project.id}/{scene.id}/{asset.id}.{ext}"
            url = storage.put_bytes(key, img_bytes)
            _wt()._bind_asset_local_file(asset, url, key)
            asset.status = "succeeded"
            asset.error_message = None
            scene.status = "image_ready"
            _schedule_scene_precompile(db, settings, asset)
            _wt()._record_usage(
                db,
                tenant_id=tenant_id,
                project_id=project.id,
                scene_id=scene.id,
                asset_id=asset.id,
                provider=str(res.get("provider") or resolved_provider),
                service_type="image_gen",
                meta={"ok": True, "model": str(res.get("model") or model_name), "tier": tier},
            )
    else:
        asset.status = "failed"
        err = format_fal_result_message(res)
        asset.error_message = err
        _wt()._record_usage(
            db,
            tenant_id=tenant_id,
            project_id=project.id,
            scene_id=scene.id,
            asset_id=asset.id,
            provider=str(res.get("provider") or "fal"),
            service_type="image_gen",
            meta={"ok": False, "error": err[:500], "tier": tier},
        )
    db.flush()
    out: dict[str, Any] = {"asset_id": str(asset.id), "ok": asset.status == "succeeded"}
    if asset.status != "succeeded" and asset.error_message:
        out["error_message"] = str(asset.error_message)[:2000]
    return out


def _phase3_video_generate(db, job: Job) -> dict[str, Any]:
    """Encode a still frame from the latest succeeded scene image to MP4 (local FFmpeg + storage)."""
    settings = _wt()._worker_runtime_for_job(db, job)
    payload = job.payload or {}
    sid = uuid.UUID(str(payload["scene_id"]))
    tier = str(payload.get("generation_tier") or "preview")
    notes = payload.get("notes")

    scene = db.get(Scene, sid)
    if not scene:
        raise ValueError("scene not found")
    chapter = db.get(Chapter, scene.chapter_id)
    if not chapter:
        raise ValueError("chapter not found")
    project = db.get(Project, chapter.project_id)
    if not project:
        raise ValueError("project not found")
    if project.tenant_id != job.tenant_id:
        raise ValueError("job tenant does not match project")
    tenant_id = worker_tenant_id(job, payload)
    exp_w, exp_h = _wt()._project_export_dimensions(project)

    ar_uuid = _wt()._payload_agent_run_uuid(payload)
    if ar_uuid is not None and _wt()._agent_run_checkpoint(db, ar_uuid) == "stop":
        return {"ok": False, "error_message": "Stopped by user", "stopped": True}

    pp = scene.prompt_package_json if isinstance(scene.prompt_package_json, dict) else {}
    base_video_text_prompt = _resolve_phase3_video_text_prompt(
        scene,
        pp,
        override=payload.get("video_prompt_override"),
        project=project,
        settings=settings,
        suffix=payload.get("video_prompt_suffix"),
    )
    payload_override = payload.get("video_provider")
    if isinstance(payload_override, str) and payload_override.strip():
        requested = payload_override.strip()
    else:
        requested = (
            pp.get("_preferred_video_provider")
            or pp.get("preferred_video_provider")
            or project.preferred_video_provider
            or getattr(settings, "active_video_provider", None)
            or "fal"
        )
    selected_video_provider = str(requested).strip().lower()
    if selected_video_provider in ("auto", "default", ""):
        selected_video_provider = "fal"
    if selected_video_provider in ("grok", "xai", "gemini", "google", "local_ltx", "local_wan"):
        selected_video_provider = "fal"
    # Stock libraries are valid workspace defaults for browse/import flows, but they are
    # not direct generators. When selected for generation jobs, use the configured
    # local still->video path so scene jobs remain runnable.
    if selected_video_provider in ("pexels", "storyblocks"):
        selected_video_provider = "local_ffmpeg"
    if selected_video_provider not in (
        "local_ffmpeg",
        "fal",
        "comfyui_wan",
    ):
        raise ValueError(
            f"Video provider '{selected_video_provider}' is not supported; use local_ffmpeg (still→MP4), fal, or comfyui_wan."
        )

    if selected_video_provider in ("fal", "comfyui_wan"):
        prompt = _scene_video_prompt_for_provider(
            db,
            scene,
            project,
            settings,
            override=payload.get("video_prompt_override"),
            suffix=payload.get("video_prompt_suffix"),
            exclude_character_bible=bool(payload.get("exclude_character_bible")),
            automation_character_prefix=(
                str(payload.get("_automation_character_prefix") or "")[:2000]
                if "_automation_character_prefix" in payload
                else None
            ),
        )
        fal_video_override = payload.get("fal_video_model")
        if not isinstance(fal_video_override, str) or not fal_video_override.strip():
            fal_video_override = None
        else:
            fal_video_override = fal_video_override.strip().lstrip("/")
        # Honor scene.planned_duration_sec, clamped to the active provider's safe cap.
        duration_sec = clip_seconds_for_scene(
            settings,
            scene,
            provider=selected_video_provider,
            fal_model=fal_video_override or getattr(settings, "fal_video_model", None),
        )

        scene_comfy_path: Path | None = None
        prereq_image_asset_id: str | None = None
        if selected_video_provider == "comfyui_wan" and settings.comfyui_video_use_scene_image:
            storage_root_c = Path(settings.local_storage_root).resolve()
            imgs_c = list(
                db.scalars(
                    select(Asset)
                    .where(
                        Asset.scene_id == sid,
                        Asset.asset_type == "image",
                        Asset.status == "succeeded",
                        Asset.storage_url.is_not(None),
                    )
                    .order_by(Asset.timeline_sequence.asc(), Asset.created_at.asc())
                ).all()
            )
            approved_c = [a for a in imgs_c if a.approved_at is not None]
            pick_c = approved_c if approved_c else imgs_c
            if not pick_c:
                wf_still = (settings.comfyui_workflow_json_path or "").strip()
                if not wf_still:
                    fail = Asset(
                        id=uuid.uuid4(),
                        tenant_id=tenant_id,
                        scene_id=scene.id,
                        project_id=project.id,
                        asset_type="video",
                        status="failed",
                        generation_tier=tier,
                        timeline_sequence=_wt()._next_timeline_sequence_for_scene(db, scene.id),
                        provider="comfyui_wan",
                        model_name=(settings.comfyui_video_model_name or "wan-2.1-comfyui").strip(),
                        params_json={
                            "routing_audit": {"requested_provider": requested, "resolved_provider": "comfyui_wan"},
                            "notes": str(notes)[:2000] if notes else None,
                        },
                        error_message=(
                            "comfyui_wan: no scene image and COMFYUI_WORKFLOW_JSON_PATH is empty — set it to your "
                            "Flux/still API JSON so Directely can generate a still before WAN video, or add a scene image."
                        ),
                    )
                    db.add(fail)
                    db.flush()
                    _wt()._record_usage(
                        db,
                        tenant_id=tenant_id,
                        project_id=project.id,
                        scene_id=scene.id,
                        asset_id=fail.id,
                        provider="comfyui_wan",
                        service_type="video_gen",
                        meta={"ok": False, "reason": "no_still_workflow", "tier": tier},
                    )
                    db.flush()
                    return _phase3_video_job_result(fail)
                img_prompt = _scene_still_prompt_for_comfy(db, scene, project, settings)
                log.info(
                    "comfyui_wan_auto_still",
                    scene_id=str(scene.id),
                    workflow_hint=wf_still[:120],
                )
                ires = generate_scene_image_comfyui(
                    settings,
                    img_prompt,
                    negative_prompt=_package_negative_prompt(
                        scene.prompt_package_json, project=project, settings=settings
                    ),
                    frame_aspect_ratio=str(getattr(project, "frame_aspect_ratio", None) or "16:9"),
                )
                if not ires.get("ok") or not ires.get("bytes"):
                    err = str(ires.get("detail") or ires.get("error") or "comfyui_still_failed")[:8000]
                    fail = Asset(
                        id=uuid.uuid4(),
                        tenant_id=tenant_id,
                        scene_id=scene.id,
                        project_id=project.id,
                        asset_type="video",
                        status="failed",
                        generation_tier=tier,
                        timeline_sequence=_wt()._next_timeline_sequence_for_scene(db, scene.id),
                        provider="comfyui_wan",
                        model_name=(settings.comfyui_video_model_name or "wan-2.1-comfyui").strip(),
                        params_json={
                            "routing_audit": {"requested_provider": requested, "resolved_provider": "comfyui_wan"},
                            "notes": str(notes)[:2000] if notes else None,
                            "prereq": "comfyui_still_for_video",
                            "comfyui_workflow_json_path": wf_still[:512],
                        },
                        error_message=f"comfyui_wan: auto still (ComfyUI) failed: {err[:2000]}",
                    )
                    db.add(fail)
                    db.flush()
                    _wt()._record_usage(
                        db,
                        tenant_id=tenant_id,
                        project_id=project.id,
                        scene_id=scene.id,
                        asset_id=fail.id,
                        provider="comfyui_wan",
                        service_type="video_gen",
                        meta={"ok": False, "reason": "auto_still_failed", "tier": tier},
                    )
                    db.flush()
                    return _phase3_video_job_result(fail)
                storage = FilesystemStorage(settings.local_storage_root)
                img_wf_name = (settings.comfyui_model_name or "").strip() or Path(wf_still).name
                img_asset = Asset(
                    id=uuid.uuid4(),
                    tenant_id=tenant_id,
                    scene_id=scene.id,
                    project_id=project.id,
                    asset_type="image",
                    status="running",
                    generation_tier=tier,
                    timeline_sequence=_wt()._next_timeline_sequence_for_scene(db, scene.id),
                    provider="comfyui",
                    model_name=img_wf_name,
                    params_json={
                        "continuity_tags_json": scene.continuity_tags_json,
                        "continuity_tags_summary": (scene.continuity_tags_json or [])
                        if isinstance(scene.continuity_tags_json, list)
                        else [],
                        "prompt_package_json": scene.prompt_package_json,
                        "image_prompt_used": img_prompt[:4000],
                        "routing_audit": {
                            "requested_provider": "comfyui_wan_prereq",
                            "resolved_provider": "comfyui",
                        },
                        "auto_generated_for_comfyui_wan_video": True,
                        "comfyui_base_url": (settings.comfyui_base_url or "")[:256],
                        "comfyui_workflow_json_path": wf_still[:512],
                    },
                )
                db.add(img_asset)
                db.flush()
                ct = str(ires.get("content_type") or "image/png")
                img_bytes, ct, norm_trusted = _normalize_image_bytes_to_dims(
                    settings, ires["bytes"], ct, exp_w, exp_h
                )
                if not (norm_trusted or _image_bytes_magic_ok(img_bytes)):
                    img_asset.status = "failed"
                    img_asset.error_message = (
                        "Image bytes were empty or not a recognized image format after generation/normalize "
                        "(check fal model output and ffmpeg image step)."
                    )[:8000]
                    db.flush()
                    fail = Asset(
                        id=uuid.uuid4(),
                        tenant_id=tenant_id,
                        scene_id=scene.id,
                        project_id=project.id,
                        asset_type="video",
                        status="failed",
                        generation_tier=tier,
                        timeline_sequence=_wt()._next_timeline_sequence_for_scene(db, scene.id),
                        provider="comfyui_wan",
                        model_name=(settings.comfyui_video_model_name or "wan-2.1-comfyui").strip(),
                        params_json={
                            "routing_audit": {"requested_provider": requested, "resolved_provider": "comfyui_wan"},
                            "notes": str(notes)[:2000] if notes else None,
                            "prereq": "comfyui_still_for_video",
                            "comfyui_workflow_json_path": wf_still[:512],
                        },
                        error_message=(
                            "comfyui_wan: auto still bytes invalid after normalize (empty or unrecognized format)."
                        ),
                    )
                    db.add(fail)
                    db.flush()
                    _wt()._record_usage(
                        db,
                        tenant_id=tenant_id,
                        project_id=project.id,
                        scene_id=scene.id,
                        asset_id=fail.id,
                        provider="comfyui_wan",
                        service_type="video_gen",
                        meta={"ok": False, "reason": "auto_still_invalid_bytes", "tier": tier},
                    )
                    db.flush()
                    return _phase3_video_job_result(fail)
                ext = "png" if "png" in ct.lower() else "jpg"
                ikey = f"assets/{project.id}/{scene.id}/{img_asset.id}.{ext}"
                iurl = storage.put_bytes(ikey, img_bytes)
                _wt()._bind_asset_local_file(img_asset, iurl, ikey)
                img_asset.status = "succeeded"
                img_asset.error_message = None
                scene.status = "image_ready"
                _schedule_scene_precompile(db, settings, img_asset)
                _wt()._record_usage(
                    db,
                    tenant_id=tenant_id,
                    project_id=project.id,
                    scene_id=scene.id,
                    asset_id=img_asset.id,
                    provider=str(ires.get("provider") or "comfyui"),
                    service_type="image_gen",
                    meta={
                        "ok": True,
                        "model": str(ires.get("model") or img_wf_name),
                        "tier": tier,
                        "prereq": "comfyui_wan_video",
                    },
                )
                db.flush()
                prereq_image_asset_id = str(img_asset.id)
                scene_comfy_path = path_from_storage_url(img_asset.storage_url, storage_root=storage_root_c)
                if scene_comfy_path is None or not path_is_readable_file(scene_comfy_path):
                    raise ValueError(
                        "auto-generated ComfyUI still saved but file not found under LOCAL_STORAGE_ROOT"
                    )
            else:
                ip_c = path_from_storage_url(pick_c[0].storage_url, storage_root=storage_root_c)
                if ip_c is None or not path_is_readable_file(ip_c):
                    raise ValueError(
                        "scene image missing on local storage for comfyui_wan (file:// under LOCAL_STORAGE_ROOT)"
                    )
                scene_comfy_path = ip_c

        if selected_video_provider == "fal":
            resolved_provider = "fal"
            model_name = fal_video_override or settings.fal_video_model
        else:
            resolved_provider = "comfyui_wan"
            model_name = (settings.comfyui_video_model_name or "wan-2.1-comfyui").strip() or "wan-2.1-comfyui"
        vparams: dict[str, Any] = {
            "prompt_used": prompt,
            "video_prompt_base": base_video_text_prompt[:3000],
            "planned_duration_sec": duration_sec,
            "duration_source": (
                "scene.planned_duration_sec"
                if scene.planned_duration_sec
                else "runtime_setting:scene_clip_duration_sec"
            ),
            "routing_audit": {
                "requested_provider": requested,
                "resolved_provider": resolved_provider,
            },
            "notes": str(notes)[:2000] if notes else None,
        }
        if resolved_provider == "comfyui_wan":
            vparams["comfyui_base_url"] = (settings.comfyui_base_url or "")[:256]
            vparams["comfyui_video_workflow_json_path"] = (settings.comfyui_video_workflow_json_path or "")[:512]
            vparams["comfyui_video_use_scene_image"] = bool(settings.comfyui_video_use_scene_image)
            vparams["comfyui_api_flavor"] = str(
                getattr(settings, "comfyui_api_flavor", "oss") or "oss"
            )[:32]
            if prereq_image_asset_id:
                vparams["prereq_image_asset_id"] = prereq_image_asset_id
                vparams["prereq_still_auto_generated"] = True
        asset = Asset(
            id=uuid.uuid4(),
            tenant_id=tenant_id,
            scene_id=scene.id,
            project_id=project.id,
            asset_type="video",
            status="running",
            generation_tier=tier,
            timeline_sequence=_wt()._next_timeline_sequence_for_scene(db, scene.id),
            provider=resolved_provider,
            model_name=model_name,
            params_json=vparams,
        )
        db.add(asset)
        db.flush()
        log.info(
            "phase3_video_dispatch",
            job_id=str(job.id),
            scene_id=str(scene.id),
            resolved_provider=resolved_provider,
            model_name=model_name,
            fal_key_configured=bool((settings.fal_key or "").strip()),
        )
        if resolved_provider == "fal":
            vm_path = fal_video_override or settings.fal_video_model
            scene_image_bytes: bytes | None = None
            scene_image_ct: str | None = None
            if fal_model_is_image_to_video(vm_path):
                storage_root_f = Path(settings.local_storage_root).resolve()
                imgs_f = list(
                    db.scalars(
                        select(Asset)
                        .where(
                            Asset.scene_id == sid,
                            Asset.asset_type == "image",
                            Asset.status == "succeeded",
                            Asset.storage_url.is_not(None),
                        )
                        .order_by(Asset.timeline_sequence.asc(), Asset.created_at.asc())
                    ).all()
                )
                approved_f = [a for a in imgs_f if a.approved_at is not None]
                pick_f = approved_f if approved_f else imgs_f
                if not pick_f:
                    asset.status = "failed"
                    asset.error_message = (
                        "fal image-to-video requires a scene image first — generate an image for this scene, "
                        "then run video again."
                    )[:8000]
                    db.flush()
                    _wt()._record_usage(
                        db,
                        tenant_id=tenant_id,
                        project_id=project.id,
                        scene_id=scene.id,
                        asset_id=asset.id,
                        provider="fal",
                        service_type="video_gen",
                        meta={"ok": False, "reason": "i2v_no_scene_image", "tier": tier},
                    )
                    db.flush()
                    return _phase3_video_job_result(asset)
                ip_f = path_from_storage_url(pick_f[0].storage_url, storage_root=storage_root_f)
                if ip_f is None or not path_is_readable_file(ip_f):
                    asset.status = "failed"
                    asset.error_message = (
                        "Scene image file missing under LOCAL_STORAGE_ROOT (cannot run fal image-to-video)."
                    )[:8000]
                    db.flush()
                    _wt()._record_usage(
                        db,
                        tenant_id=tenant_id,
                        project_id=project.id,
                        scene_id=scene.id,
                        asset_id=asset.id,
                        provider="fal",
                        service_type="video_gen",
                        meta={"ok": False, "reason": "i2v_image_not_on_disk", "tier": tier},
                    )
                    db.flush()
                    return _phase3_video_job_result(asset)
                scene_image_bytes = ip_f.read_bytes()
                suf = ip_f.suffix.lower()
                if suf == ".png":
                    scene_image_ct = "image/png"
                elif suf in (".jpg", ".jpeg"):
                    scene_image_ct = "image/jpeg"
                elif suf == ".webp":
                    scene_image_ct = "image/webp"
            vres = generate_scene_video_fal(
                settings,
                prompt,
                duration_sec,
                model=fal_video_override,
                image_bytes=scene_image_bytes,
                image_content_type=scene_image_ct,
                frame_aspect_ratio=str(getattr(project, "frame_aspect_ratio", None) or "16:9"),
            )
        else:
            vres = generate_scene_video_comfyui(
                settings,
                prompt,
                scene_image_path=scene_comfy_path,
                duration_sec=duration_sec,
                frame_aspect_ratio=str(getattr(project, "frame_aspect_ratio", None) or "16:9"),
                should_stop=_wt()._make_job_stop_signal(
                    agent_run_uuid=ar_uuid,
                    job_uuid=job.id,
                    project_uuid=project.id,
                ),
            )
        if vres.get("ok") and vres.get("bytes"):
            storage = FilesystemStorage(settings.local_storage_root)
            key = f"assets/{project.id}/{scene.id}/{asset.id}.mp4"
            vbytes = _normalize_video_bytes_to_dims(settings, vres["bytes"], exp_w, exp_h)
            url = storage.put_bytes(key, vbytes)
            _wt()._bind_asset_local_file(asset, url, key)
            asset.status = "succeeded"
            asset.error_message = None
            _schedule_scene_precompile(db, settings, asset)
            _wt()._record_usage(
                db,
                tenant_id=tenant_id,
                project_id=project.id,
                scene_id=scene.id,
                asset_id=asset.id,
                provider=str(vres.get("provider") or resolved_provider),
                service_type="video_gen",
                meta={"ok": True, "model": str(vres.get("model") or model_name), "tier": tier},
            )
        else:
            err = format_fal_result_message(vres)
            asset.status = "failed"
            asset.error_message = err
            _wt()._record_usage(
                db,
                tenant_id=tenant_id,
                project_id=project.id,
                scene_id=scene.id,
                asset_id=asset.id,
                provider=str(vres.get("provider") or resolved_provider),
                service_type="video_gen",
                meta={"ok": False, "error": err[:500], "tier": tier},
            )
        db.flush()
        return _phase3_video_job_result(asset)

    ffmpeg_bin = (settings.ffmpeg_bin or "ffmpeg").strip() or "ffmpeg"
    if not shutil.which(ffmpeg_bin):
        asset = Asset(
            id=uuid.uuid4(),
            tenant_id=tenant_id,
            scene_id=scene.id,
            project_id=project.id,
            asset_type="video",
            status="failed",
            generation_tier=tier,
            timeline_sequence=_wt()._next_timeline_sequence_for_scene(db, scene.id),
            provider="local_ffmpeg",
            model_name="still_to_mp4",
            params_json={
                "source_image_asset_id": None,
                "routing_audit": {"requested_provider": requested, "resolved_provider": "local_ffmpeg"},
                "notes": str(notes)[:2000] if notes else None,
            },
            error_message="ffmpeg binary not found on worker PATH (required for local still→video encode)",
        )
        db.add(asset)
        db.flush()
        _wt()._record_usage(
            db,
            tenant_id=tenant_id,
            project_id=project.id,
            scene_id=scene.id,
            asset_id=asset.id,
            provider="local_ffmpeg",
            service_type="video_gen",
            meta={"ok": False, "reason": "ffmpeg_missing", "tier": tier},
        )
        db.flush()
        return _phase3_video_job_result(asset)

    imgs = list(
        db.scalars(
            select(Asset)
            .where(
                Asset.scene_id == sid,
                Asset.asset_type == "image",
                Asset.status == "succeeded",
                Asset.storage_url.is_not(None),
            )
            .order_by(Asset.timeline_sequence.asc(), Asset.created_at.asc())
        ).all()
    )
    approved_imgs = [a for a in imgs if a.approved_at is not None]
    pick_imgs = approved_imgs if approved_imgs else imgs
    if not pick_imgs:
        asset = Asset(
            id=uuid.uuid4(),
            tenant_id=tenant_id,
            scene_id=scene.id,
            project_id=project.id,
            asset_type="video",
            status="failed",
            generation_tier=tier,
            timeline_sequence=_wt()._next_timeline_sequence_for_scene(db, scene.id),
            provider="local_ffmpeg",
            model_name="still_to_mp4",
            params_json={
                "routing_audit": {"requested_provider": requested, "resolved_provider": "local_ffmpeg"},
                "notes": str(notes)[:2000] if notes else None,
            },
            error_message="no succeeded image with storage for this scene; run generate-image first",
        )
        db.add(asset)
        db.flush()
        _wt()._record_usage(
            db,
            tenant_id=tenant_id,
            project_id=project.id,
            scene_id=scene.id,
            asset_id=asset.id,
            provider="local_ffmpeg",
            service_type="video_gen",
            meta={"ok": False, "reason": "no_source_image", "tier": tier},
        )
        db.flush()
        return _phase3_video_job_result(asset)

    storage_root = Path(settings.local_storage_root).resolve()
    resolved_paths: list[tuple[Asset, Path]] = []
    for img in pick_imgs:
        ip = path_from_storage_url(img.storage_url, storage_root=storage_root)
        if ip is None or not path_is_readable_file(ip):
            raise ValueError(
                "source image missing on local storage (file:// expected under LOCAL_STORAGE_ROOT)"
                f" for asset {img.id}"
            )
        resolved_paths.append((img, ip))

    duration_sec = max(0.5, min(_wt()._scene_clip_duration_sec(settings), 300.0))
    src_image = pick_imgs[0]
    use_slideshow = len(resolved_paths) > 1
    model_name = "image_slideshow_mp4" if use_slideshow else "still_to_mp4"
    per_slide_sec = duration_sec / len(resolved_paths) if use_slideshow else duration_sec
    video_text_for_motion = maybe_prepend_topic_setting_anchor(
        base_video_text_prompt, project.topic, max_total=3000
    )
    slow_zoom_ff, kb_dir, slide_motion = _local_ffmpeg_motion_from_video_prompt(video_text_for_motion)

    params_json: dict[str, Any] = {
        "continuity_tags_json": scene.continuity_tags_json,
        "continuity_tags_summary": (scene.continuity_tags_json or [])
        if isinstance(scene.continuity_tags_json, list)
        else [],
        "prompt_package_json": scene.prompt_package_json,
        "source_image_asset_id": str(src_image.id),
        "source_image_asset_ids": [str(a.id) for a in pick_imgs],
        "planned_duration_sec": duration_sec,
        "duration_source": "runtime_setting:scene_clip_duration_sec",
        "routing_audit": {
            "requested_provider": requested,
            "resolved_provider": "local_ffmpeg",
        },
        "notes": str(notes)[:2000] if notes else None,
        "video_prompt_resolved": video_text_for_motion[:3000],
        "ffmpeg_motion_hint": slide_motion,
        "slow_zoom": slow_zoom_ff,
        "ken_burns_direction": kb_dir,
    }
    if use_slideshow:
        params_json["slide_count"] = len(resolved_paths)
        params_json["per_slide_duration_sec"] = per_slide_sec

    asset = Asset(
        id=uuid.uuid4(),
        tenant_id=tenant_id,
        scene_id=scene.id,
        project_id=project.id,
        asset_type="video",
        status="running",
        generation_tier=tier,
        timeline_sequence=_wt()._next_timeline_sequence_for_scene(db, scene.id),
        provider="local_ffmpeg",
        model_name=model_name,
        params_json=params_json,
    )
    db.add(asset)
    db.flush()

    log.info(
        "phase3_video_dispatch",
        job_id=str(job.id),
        scene_id=str(scene.id),
        resolved_provider="local_ffmpeg",
        model_name=model_name,
        slide_count=len(resolved_paths),
    )

    storage = FilesystemStorage(str(storage_root))
    key = f"assets/{project.id}/{scene.id}/{asset.id}.mp4"
    out_path = storage.get_path(key)

    try:
        w = exp_w
        h = exp_h
        tmo = float(settings.ffmpeg_timeout_sec)
        if use_slideshow:
            slides = [(p, per_slide_sec) for _a, p in resolved_paths]
            sm = slide_motion if slide_motion in ("pan", "zoom") else "none"
            enc = compile_image_slideshow(
                slides,
                out_path,
                width=w,
                height=h,
                fps=30,
                ffmpeg_bin=ffmpeg_bin,
                timeout_sec=tmo,
                motion=sm,
                crossfade_sec=0.0,
                slow_zoom=False,
            )
        else:
            enc = encode_image_to_mp4(
                resolved_paths[0][1],
                out_path,
                duration_sec=duration_sec,
                width=w,
                height=h,
                slow_zoom=slow_zoom_ff,
                ken_burns_direction=kb_dir if kb_dir in ("in", "out") else "in",
                ffmpeg_bin=ffmpeg_bin,
                timeout_sec=tmo,
            )
    except FFmpegCompileError as e:
        err = str(e)[:8000]
        asset.status = "failed"
        asset.error_message = err
        _wt()._record_usage(
            db,
            tenant_id=tenant_id,
            project_id=project.id,
            scene_id=scene.id,
            asset_id=asset.id,
            provider="local_ffmpeg",
            service_type="video_gen",
            meta={"ok": False, "error": err[:500], "tier": tier},
        )
        db.flush()
        return _phase3_video_job_result(asset)

    url = out_path.resolve().as_uri()
    _wt()._bind_asset_local_file(asset, url, key)
    asset.status = "succeeded"
    asset.error_message = None
    _schedule_scene_precompile(db, settings, asset)
    _wt()._record_usage(
        db,
        tenant_id=tenant_id,
        project_id=project.id,
        scene_id=scene.id,
        asset_id=asset.id,
        provider="local_ffmpeg",
        service_type="video_gen",
        meta={
            "ok": True,
            "tier": tier,
            "duration_sec": float(duration_sec),
            **{k: v for k, v in enc.items() if k != "output_path"},
        },
    )
    db.flush()
    return _phase3_video_job_result(asset)
