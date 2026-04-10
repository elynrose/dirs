---
phase: 3
slug: scenes-media
title: "Scene planning and media generation"
status: completed
progress_percent: 100
updated: 2026-03-22
depends_on: ["phase-02-research-writing"]
source: "../project.md §8 Phase 3, §9.4–9.5, §14, §20 Phase 3 exit; docs/webhooks.md; docs/preview-vs-production.md; docs/error-codes.md"
---

# Phase 3 — Scene planning and media generation

**Goal:** Turn the script into structured scenes and assets.

**Spec links:** [`project.md`](../project.md) §8 Phase 3, §20 · [`docs/api-spec.md`](../docs/api-spec.md) · [`docs/webhooks.md`](../docs/webhooks.md) · [`docs/preview-vs-production.md`](../docs/preview-vs-production.md) · [`docs/error-codes.md`](../docs/error-codes.md)

## Notes (review)

**Strengths:** Scene cards, dual pipelines, and approval flow match the hybrid media strategy.

**Gaps addressed:** No **job completion** story (poll vs webhook), no **preview vs production** tier, no **usage/cost stub** for later billing—added. Clarified **provider routing** must read project/scene prefs from §13 fields.

**Residual risk:** Long-running GPU jobs need **timeouts and partial failure** UX (per-scene failure must not strand the whole chapter without visibility).

## Deliverables

### Planning

- [x] **P3-D01** Storyboard / scene planner agent: scene cards with purpose, **planned_duration_sec**, narration reference, **visual_type**, **prompt_package_json**, **continuity_tags_json**, **generation provider** hints (validated schema) — _deterministic split from chapter `script_text` + optional OpenAI `refine_scene_plan_batch`; schema `scene-plan-batch/v1` in `packages/schemas/json/`_
- [x] **P3-D02** Scene cards UI: list by chapter, detail drawer, status, prompts, continuity tags — _`apps/web` Phase 3 section: chapter picker, scene list, expand prompts/tags, image/video/retry, asset list, approve/reject_

### Media pipelines

- [x] **P3-D03** Image generation: enqueue from `POST /scenes/:id/generate-image`, persist `Asset` rows, upload to object storage, preview URL — _Celery job `scene_generate_image`, **fal** via `generate_scene_image`, files under `assets/{project_id}/{scene_id}/`; `GET /scenes/:id/assets`_
- [x] **P3-D04** Video generation: enqueue from `POST /scenes/:id/generate-video` — _job `scene_generate_video` encodes **local FFmpeg** still→MP4 from latest succeeded scene image; `provider=local_ffmpeg`; optional cloud I2V can reuse same route later_
- [x] **P3-D05** Retry path: `POST /scenes/:id/retry` with variant prompts without orphaning old assets (history preserved) — _optional `image_prompt_override` + `generation_tier`; separate idempotency route from generate-image_
- [x] **P3-D06** Asset approval: `POST /assets/:id/approve` (and reject path) gates “approved” assets for downstream use — _`POST /assets/:id/reject` with optional reason in `params_json.rejection`_

### APIs and operations

- [x] **P3-D07** `POST /chapters/:id/scenes/generate`, `GET /chapters/:id/scenes`, `PATCH /scenes/:id` — _plus `GET /scenes/:id/assets`_
- [x] **P3-D08** Worker completion: polling and/or **signed webhooks** from providers; consistent terminal states on failure — _poll `GET /v1/jobs/{id}`; optional `WEBHOOK_URL` + HMAC signing per [webhooks.md](../docs/webhooks.md); phase2/phase3/adapter_smoke call `notify_job_terminal` after terminal commit; phase3 `bind=True` + `SoftTimeLimitExceeded` → `failed` + notify_
- [x] **P3-D09** Dead-letter or explicit **failed** state with error payload visible in UI — _`Asset.status` / `error_message`; job poll shows `error_message`; UI lists asset errors_

### Observability and cost (lightweight)

- [x] **P3-D10** Write **UsageRecord** or equivalent stub: provider, service type, units, **cost_estimate**, request id (even if billing UI is Phase 6) — _`usage_records` table + row per image job_

## Requirements

- [x] **P3-R01** Each scene includes: id, chapter id, purpose, duration, narration reference, visual type, prompt package, continuity notes, provider selection, status — _provider hints in `prompt_package_json` / project prefs; status `planned` / `image_ready`_
- [x] **P3-R02** Every asset references **scene_id** (and thus chapter/project via join) — _`project_id` denormalized on `assets`_
- [x] **P3-R03** Continuity tags on scenes copied or summarized into asset **params_json** / metadata for traceability — _`continuity_tags_json` + `continuity_tags_summary` on assets_
- [x] **P3-R04** Retries are **idempotent-safe** (no duplicate billing rows for the same logical attempt without intent) — _separate `Idempotency-Key` per attempt; replay only on same key+route+body hash_
- [x] **P3-R05** **Preview** vs **production** quality (resolution, model tier) selectable at project or scene level and recorded on the asset — _`generation_tier` on POST body → `Asset.generation_tier`_
- [x] **P3-R06** Routing respects `preferred_image_provider` / `preferred_video_provider` unless overridden per scene with audit metadata — _scene `prompt_package_json._preferred_*` + project fallbacks; non-`fal` image provider fails fast with message; `routing_audit` on `params_json`_

## Success metric

- [x] **P3-M01** One full chapter: generated scene cards → generated images and/or clips → **approved** assets linked to scenes — _`GET /v1/chapters/{id}/phase3-summary` + studio “Phase 3 summary” block_

## Exit criteria

- [x] **P3-X01** At least one chapter has scene cards plus **linked** image and/or video assets in **approved** (or explicitly approved-for-demo) state, with failures visible and retryable — _same summary + UI; video stub may remain failed until encoder; approve path on image assets satisfies exit_
