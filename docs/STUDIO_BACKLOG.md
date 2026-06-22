# Studio backlog

Tracked follow-ups from prompt-pipeline, YouTube publish, and architect review (2026-06).

---

## Prompt pipeline (scene images & video)

### P1 — Plan-time character bible embed bypasses portrait filter

**Problem:** `phase3.py` still prepends character bible into stored `prompt_package_json.image_prompt` / `video_prompt` at scene-plan time via `_embed_character_prefix_in_prompt_package`, without passing the draft prompt as `base_prompt` to `_character_prefix_from_chunks`. Runtime assembly may skip re-injection, but Victoria (etc.) can already be baked into the package.

**Fix:** Pass draft `image_prompt` into `character_prefix_from_chunks(..., base_prompt=...)` before embed, or stop embedding at plan time and rely only on `_assemble_scene_generative_prompt` at job time.

**Files:** `apps/api/director_api/services/phase3.py`

---

### P1 — Resolved preview ≠ worker image prompt

**Problem:** `_scene_still_prompt_for_comfy` adds framing-safety and era-anchor clauses; `_phase3_image_generate` does not. Studio “Resolved prompts” can show text the worker never sends.

**Fix:** Pick one — either add the same flags to the job path, or remove them from preview so preview === worker.

**Files:** `apps/api/director_api/tasks/prompt_runtime_helpers.py`, `apps/api/director_api/tasks/phase3_impl.py`

---

### P2 — Portrait heuristic false positives

**Problem:** Generic narration mentions (e.g. “debated Victoria’s legacy”) without portrait keywords still inject full character bible.

**Fix (optional):** Tighten heuristics or require explicit on-screen cues; document in LLM catalog / Studio that chalkboard/portrait beats should describe the portrait in `Subject:`.

**Files:** `apps/api/director_api/services/character_prompt.py`

---

### P2 — `local_ffmpeg` video path uses different prompt recipe

**Problem:** Still→MP4 / slideshow uses base motion text only — no `assemble_scene_video_prompt`. Expected, but UI label “video prompt” is ambiguous across providers.

**Fix:** Document in API or Studio; optional unified “motion hint” field.

---

### P3 — Naming / schema hygiene

- Rename `character_consistency_block_for_image` → shared name (used for video too).
- Consolidate `documentary_brief.schema.json` (apps/api vs `packages/schemas/json/` drift).
- Replace ad-hoc `brief_dict()` excludes with an allowlist matching JSON schema, or split `DocumentaryBriefCreate` vs project run flags.
- Add lightweight `pipeline_options` schema/docs (known keys: `publish_to_youtube`, `include_outro_scene`, etc.).

---

## YouTube publish

### P1 — Deploy migration `042`

**Problem:** `projects.publish_to_youtube` column required in production.

**Action:** Run alembic `042_project_publish_to_youtube` on directely.com; configure OAuth in Settings → Integrations.

---

### P2 — Surface upload result in Studio

**Problem:** Upload is best-effort after export; failures may be invisible in UI.

**Action:** Ensure agent run step `publish_youtube` shows success/warning with watch URL or error (verify `AgentRunWarningAlert` / step labels).

**Files:** `apps/api/director_api/tasks/agent_impl.py`, `apps/web/src/components/AgentRunWarningAlert.jsx`, `constants.js`

---

### Done — Brief schema vs `publish_to_youtube`

- `ProjectCreate.brief_dict()` excludes `publish_to_youtube` from JSON schema validation.
- Frontend sends `publish_to_youtube` only in `pipeline_options`, not in `brief`.

---

## Cover / thumbnail generation (new)

### P1 — Thumbnail should include title (and packaging), not a generic scene still

**Problem:** Today `thumbnail_core` sends only `meta["thumbnail_prompt"]` to Comfy/Fal. The LLM catalog explicitly says *“no tiny text in the image”*, so covers tend to be generic hero art with title/description stored separately in `publish_pack_json` (`youtube_title`, `youtube_description`) but **not rendered on the image**.

**User expectation:** Cover/thumbnail image should read like a YouTube/documentary poster — **visible title** (and optionally subtitle/hook), high contrast, on-brand with the project — not an unrelated generic photo.

**Proposed approach (pick one or combine):**

1. **Two-stage compose (recommended)**  
   - Stage A: Generate hero still (current flow, portrait-safe if needed).  
   - Stage B: Overlay title (+ optional subtitle) with FFmpeg/PIL using `youtube_title` / project title — safe zones for 16:9, readable type, stroke/shadow.  
   - Store composed asset as `thumbnail_storage_key`; keep raw hero key optional for re-layout.

2. **Prompt-only (weaker)**  
   - Update `publish_thumbnail_pack` LLM prompt to ask for “bold documentary poster layout with large readable title text: …”  
   - Flux/Comfy often garble text; treat as supplement to compose, not replacement.

3. **Studio preview**  
   - Publish tab shows title/description fields and live preview of composed thumbnail before upload/regen.

**Files to touch:**

- `apps/api/director_api/services/publish_pack.py` — `_generate_thumbnail_image`, `thumbnail_core`
- `apps/api/director_api/llm_prompt_catalog.py` — `publish_thumbnail_pack` prompt
- `apps/api/director_api/agents/phase2_publish_llm.py` — pass title into image pipeline explicitly
- `apps/web/src/editor/publish/PublishCoverTabContent.jsx` (or equivalent) — preview composed cover
- Tests: `test_publish_pack` / new thumbnail compose tests

**Acceptance criteria:**

- Regenerated thumbnail visibly includes project/YouTube title (readable at thumbnail size).
- Metadata (`youtube_title`, `youtube_description`) stays in `publish_pack_json` for upload.
- Manual upload path unchanged; optional “re-compose text overlay” on existing hero image.

---

## Test gaps

- Scene plan-time character embed (portrait filter at planning).
- Preview vs worker prompt byte parity.
- End-to-end agent run with YouTube upload success/failure UI.
- Thumbnail title overlay compose.

---

## Reference — prompt assembly (done)

- `_assemble_scene_generative_prompt` shared for image + video (fal/WAN).
- Labeled Flux prompts pass through; bracket hints lose to substantial package.
- Portrait/chalkboard-only characters skip bible injection at runtime.
- Tests: `test_assemble_scene_*`, `test_character_portrait_skip`, `test_character_bible_injection`, `test_brief_publish_to_youtube`.
