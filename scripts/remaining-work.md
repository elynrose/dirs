# Remaining work (save for later)

Checkpoint list: what is **not** done yet, plus product ideas. Full audit text lives in `scripts/improvements.md`.

---

## Product / UX (separate spec)

- **`simpleui.md`** — **Chat tab shipped** (`ChatStudioPage.jsx`, rail id `chat`): sidebar projects, hands-off `POST /v1/agent-runs`, step bubbles from `steps_json`, video via `pipeline-status` + compiled-video URL. **Still to do:** research-phase “interviewer” agent (conversational guidance).

---

## Backend

| Item | Notes |
|------|--------|
| **Split `worker_tasks.py`** | Plan is in the file header (phase2/3/4/5_tasks, agent_tasks). Large refactor; move one section at a time. |
| **`cost_estimate` in `_record_usage`** | Always `0.0` — wire per-provider hints or hide in Usage UI. |
| **`validate_timeline_document` twice** | Rough + fine path; dedupe if safe (low priority). |
| **True multi-tenant routing** | Models exist; routes/workers still mostly `default_tenant_id`-shaped. Major product/architecture effort. |

Already addressed elsewhere (ignore in improvements.md if still listed): manifest N+1 prefetch, compound indexes (Alembic 019), text LLM gating helper, lazy phase3 enqueue, `list_projects` offset + `total_count`, batched scene-visual checks, DB pool env vars, Redis rate-limit reconnect + JWT `tid` rate key, weak JWT option, celery restart limit, `rough_cut` log when compile disabled, **pause → Celery re-queue** (no solo sleep loop).

---

## Frontend (`apps/web`)

| Item | Notes |
|------|--------|
| **Decompose `App.jsx`** | Contexts / page-level components; shrink re-renders. |
| **Per-panel error boundaries** | `StudioPanelErrorBoundary` wraps the whole main column; optional: separate boundaries for Editor vs Settings vs Research. |
| **Phase 5 export gate — structured errors** | API returns `{ code, issues }`; frontend stops regex on bullet text (`parsePhase5GateModalPayload`). |
| **`React.memo` / extracted pure blocks** | e.g. readiness rows, export attention block. |
| **Resolved prompts from API** | Align preview with `worker_tasks` prompt resolution. |
| **Virtualized asset gallery** | Many assets per scene → windowed list. |
| **`useProjectEvents` / SSE** | Close on project switch; no orphaned `EventSource`; backoff. |
| **`friendlyReadinessIssue`** | Shared constants for backend codes vs hardcoded strings. |
| **`studioJobKindHeadline`** | Exhaustiveness or comment list when adding job types. |
| **AbortController on fetches** | Where long-lived or cancellable requests matter. |

Already done: `STUDIO_PAGE_RAILS` → session `activePage` validation; `JOB_TYPE_MACRO_STEP_RULES`; `sceneNarrationGuideMap` `useMemo` (incl. `chapterNarration` dep); main-shell error boundary.

---

## FFmpeg / infra / product

- Silence AAC cache/dedup; `setsar=1` in slideshow / still_to-video; chained timeout budget; `ffmpeg -version` gate.
- Pre-flight mixed timeline; docs for mixed timeline; CI job for FFmpeg tests.
- Compose: Redis AOF, restart policies; pin `tavily-python`; pre-commit / optional mypy.
- Electron: bundle FFmpeg in `extraResources`; dev DB credentials story.

---

## Ops reminder

- After schema changes: **`alembic upgrade head`** on deployments (e.g. migration 019+).

---

*Last updated from backlog pass — merge new items here as you go.*
