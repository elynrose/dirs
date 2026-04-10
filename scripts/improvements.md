Director Codebase Review — Suggested Improvements
Overview
Three layers examined: Python backend (apps/api/), React frontend (apps/web/), and the FFmpeg pipeline package. Issues are grouped by severity within each layer.

🐍 Backend (Python / FastAPI / Celery)
🔴 High Priority
1. N+1 query pattern in every manifest-processing function
_final_cut_audio_slots_from_manifest, _rough_cut_visual_segments_with_chapter_cards, and _expand_manifest_and_slots_for_full_narration each issue 3 separate db.get() calls per clip (Asset → Scene → Chapter). On a 50-clip timeline that's 150 round-trips to the DB. All three could be replaced by a single JOIN query or by preloading with selectinload(Asset.scene).selectinload(Scene.chapter) before entering the loop.

2. _agent_run_checkpoint busy-polls with time.sleep(1.0)
While an agent run is paused, the function loops indefinitely, sleeping 1 second and re-querying the DB each iteration. On --pool=solo (the typical Windows deployment), this freezes the entire Celery worker — no other tasks can run. Even with prefork, it wastes a worker slot for the full pause duration. A better approach: have the pause signal use celery.task.revoke + countdown or a Celery chord-based checkpoint that yields the worker slot back.

3. worker_tasks.py is a 6041-line God Module
The split plan is already documented at the top of the file (sections labelled phase2_tasks.py, phase3_tasks.py, etc.) but has not been executed. Until it is, every change to any phase requires navigating 6000 lines, and any import error in one phase brings down all phases. The extraction path is already planned — it just needs to be followed.

4. _final_cut_narration_mode always returns "scene_timeline" — dead code
The function is defined with a signature suggesting it could route between modes but unconditionally returns the same string. Any caller that branches on its return value gets a constant. Either remove it and inline the string, or implement the routing logic it implies.

5. run_phase3_job imported in projects.py (API router)

from director_api.tasks.worker_tasks import run_phase3_job

This import in a FastAPI router pulls the entire worker_tasks import graph into the API process at startup — all providers, FFmpeg wrappers, TTS imports, etc. It appears to never actually be called (the router uses enqueue_job_task). If it's unused, remove the import. If it is used somewhere, move the import to a lazy local import inside the function body.

🟡 Medium Priority
6. Provider validation logic duplicated 4–5 times
The provider routing block:

if (text_provider in _TEXT_USES_OPENAI_SDK and openai_compatible_configured(settings))
or (text_provider == "openrouter" and bool(settings.openrouter_api_key))
or (text_provider in ("xai", "grok") and ...)
or (text_provider in ("gemini", "google") and ...)

appears nearly identically in _phase2_research_core, _phase2_outline_core, _phase2_chapters_core, _phase2_chapter_script_regenerate_core, and as a validation-only check in several others. This should be extracted to a single helper like _text_provider_is_configured(provider, settings) -> bool.

7. Missing compound database indexes

NarrationTrack: filtered frequently on (project_id, scene_id) and sorted by created_at.desc(). No compound index exists.
ResearchClaim: filtered on (dossier_id, adequately_sourced, disputed) in _phase2_chapters_core. Only dossier_id is individually indexed.
CriticReport: _scene_critic_revision_apply_from_latest_report scans by (target_type, target_id) with order_by(created_at). Composite index would help on projects with many critic cycles.
8. list_projects has no offset/cursor pagination

rows = db.scalars(select(Project).where(...).order_by(...).limit(n)).all()

The cap is 200 but the API exposes limit as a query parameter with min(int(limit), 200). There's no offset or cursor, so workspaces with >200 projects can only ever see the most recent 200. Additionally, the response doesn't include a total_count field, so the frontend can't show "showing 50 of 312".

9. cost_estimate always 0.0 in _record_usage()
Every provider call records cost_estimate=0.0. The usage tab UI presumably renders a cost dashboard, but it's always zero. Either wire up real cost estimates per provider, or remove the cost_estimate column from the UI to avoid misleading users.

10. DB connection pool hardcoded
session.py hardcodes pool_size=5, max_overflow=10. In a multi-worker deployment (e.g., 4 Celery workers + 2 Uvicorn processes × 5 pool slots each), this can reach 30+ concurrent connections against a typical PostgreSQL default max of 100. These values should be environment-configurable (DB_POOL_SIZE, DB_MAX_OVERFLOW).

11. _redis_unavailable sticky flag never resets
In rate_limit.py, once Redis is unreachable, _redis_unavailable = True suppresses all further warnings. But the flag is never set back to False when Redis recovers. After a Redis restart, the limiter would silently allow all traffic indefinitely until the API process itself restarts.

12. ffmpeg_compile_enabled=False silently produces no file
When settings.ffmpeg_compile_enabled is False, _rough_cut returns success with render_status = "manifest_ready" but no MP4 is written. run_phase5_job marks the Celery task succeeded. A user who disabled FFmpeg by accident would see green checkmarks but no output file and no warning.

🟢 Low Priority
13. validate_timeline_document called twice
_rough_cut calls validate_timeline_document(tj) indirectly through _build_timeline_export_manifest, and _fine_cut calls it directly again on the same timeline JSON. Redundant for back-to-back calls.

14. No database migration framework (Alembic)
No alembic.ini or migrations/ directory exists. Schema changes must be applied manually to every deployment. In production this means schema drift is guaranteed on any multi-instance deployment and rollbacks are manual.

15. True multi-tenancy not wired
The full multi-tenant data model exists (Tenant, TenantMembership, tenant_id on every table) but every route and worker uses settings.default_tenant_id. The infrastructure is ready but the routing layer is missing.

⚛️ Frontend (React / App.jsx)
🔴 High Priority
1. 8533-line App.jsx monolith
Despite extracting some hooks and components, App.jsx still defines the entire application in a single component function with 50+ useState declarations, hundreds of useCallback/useMemo blocks, and all JSX rendering for every page. Every state change in any corner of the app triggers re-render evaluation of the entire component tree. The existing extraction pattern (useProjectEvents, usePollJob, etc.) shows the right direction — it needs to continue into data-grouped contexts and page-level sub-components.

2. No error boundaries
There are no <ErrorBoundary> wrappers anywhere in the tree. A render error in any sub-component — including a third-party component receiving unexpected data — crashes the entire application to a white screen with no recovery path. At minimum, each major panel (Editor, Research, Settings) should be wrapped in an error boundary.

3. parsePhase5GateModalPayload parses error codes from text strings

const re = /[•\u2022\-]\s*([a-z0-9_]+)\s*:/gi;

This function reads error code strings from raw backend error message text using regex. It also has a hardcoded list of known codes that it checks via string .includes(). Any change to the backend error message format (even adding a period or changing a bullet character) silently breaks the gate modal. The backend should instead return structured error data ({ code, issues: [...] }) and the frontend should render from the structured payload.

🟡 Medium Priority
4. buildSceneNarrationGuide not memoized
This function iterates all scenes, calls narrationWordCount on each narration text, sorts, and builds a Map. It's called in render without useMemo, so it recomputes on every render regardless of whether scenes or chapter audio changed. With 20 scenes each having 200-word narration texts, this is ~4000 string splits per render.

5. inferMacroStepKeyFromJobType and inferAgentStepKeyFromActiveJobs duplicate the job-type mapping
Both functions contain nearly identical if (t === "scene_generate_image" || t === "scene_generate"...) return "auto_images" chains. The mapping only needs to be defined once as a lookup table, then both functions derive from it.

6. No React.memo on stateless child components
Inline components like ExportAttentionTimelineAssetsBlock, friendlyReadinessIssue-based render blocks, and other pure display components are re-evaluated on every parent render. Wrapping them in React.memo or extracting them as top-level components would prevent unnecessary reconciliation.

7. Frontend prompt logic duplicates backend
baseImagePromptFromScene and baseVideoPromptFromScene in App.jsx mirror exactly what _scene_still_prompt_for_comfy and _resolve_phase3_video_text_prompt do in worker_tasks.py. If a product change updates the backend prompt resolution (e.g. falling back to a different field), the frontend preview won't match what actually gets generated. The backend should expose the resolved prompt via an API endpoint, or document the contract explicitly as a stable interface.

8. readDirectorUiSession whitelist of valid activePage values is hardcoded

const ap = o.activePage === "settings" || o.activePage === "usage" || ... ? o.activePage : "editor";

This list diverges from STUDIO_PAGE_RAILS. When a new page rail is added (e.g. "music" or "assets"), sessions saved with that page silently default to "editor" on restore. The list should be derived from STUDIO_PAGE_RAILS.map(r => r.id).

9. Asset gallery has no virtualization
The scene asset gallery renders all asset cards as DOM nodes. For scenes that accumulate 50–100 assets (common on heavy iteration projects), all cards are mounted simultaneously, each with an <img> or <video> element and event handlers. A windowed list (react-window or react-virtual) would keep DOM size constant.

10. SSE connection not audited for cleanup edge cases
useProjectEvents opens a Server-Sent Events connection. Worth confirming that: (a) the EventSource is closed when the project changes, not just when the component unmounts; (b) rapid project switching doesn't accumulate orphaned connections; and (c) browser tab backgrounding doesn't cause missed reconnect events.

🟢 Low Priority
11. friendlyReadinessIssue has hardcoded backend error code strings
The function contains 15+ if (iss.code === "missing_approved_scene_image") branches mapping backend codes to UI copy. These codes are not typed or imported from a shared constants file. If a backend code changes, the function silently falls through to the generic message. These should be imported from constants.js or validated against a known set at build time.

12. studioJobKindHeadline and inferMacroStepKeyFromJobType will silently return fallbacks for new job types
Any new job type added on the backend will show "Background job" / "pipeline" in the UI until someone updates both functions. A TypeScript-style exhaustiveness check or a comment listing the expected types would prevent this class of miss.

🎬 FFmpeg Pipeline
🟡 Medium Priority
1. write_silence_aac generates a new file per slot — no deduplication
In _build_scene_timeline_narration_stem, a unique silence file is written for every chapter title card and every non-first clip of a scene. A 10-chapter, 100-scene project with full narration could generate 100+ silence files, all of identical 5-second content. Silence files of the same duration could be cached (at least within one invocation) to reduce FFmpeg subprocess count and temp file churn.

2. _local_ffmpeg_motion_from_video_prompt is English-only
The motion detection keywords ("pan left", "zoom in", "dolly out", etc.) are hardcoded English. Prompts generated by non-English LLMs return ("none") silently. Either document this as English-only, or convert the detected motion from the structured prompt_package_json fields rather than parsing free text.

3. timeout_sec is not budgeted across chained calls
Each FFmpeg subprocess call in a pipeline receives the full timeout_sec from settings (typically matching the Celery task soft limit). A 5-step pipeline with timeout_sec=7200 could theoretically run for 36000 seconds before any individual step times out. The pipeline functions should accept a "wall clock deadline" and compute per-call timeouts as min(per_call_default, deadline - time.time()).

4. No FFmpeg version gate
The pipeline uses libx264, setsar, fps filter, amix, and concat demuxer. No version check is performed. If an older FFmpeg build is present (pre-4.x is common on older Linux distros), filter syntax differences cause cryptic errors. A startup-time ffmpeg -version check with a minimum version assertion would fail fast with a clear message.

🟢 Low Priority
5. compile_image_slideshow and compile_video_concat entry points still call _stream_copy_join internally but mixed_timeline calls compile_video_concat([path]) to normalize then _stream_copy_join for the join
This is correct since the last session's fix, but worth documenting with a comment in mixed_timeline.py explaining why single-clip compile_video_concat is still needed for the dimension-normalize step (so a future reader doesn't "simplify" it away).

6. image_batch_crossfade_sec only applies between images
The crossfade_sec parameter flows through slideshow and image batches but video clips always join with hard cuts in _stream_copy_join. If smooth transitions between all clip types are ever desired, the current architecture would require a significant rework (re-encode at join time).

🏗️ Infrastructure / Systemic
1. No migration framework — The DB schema is defined only in Python models. Any model change requires hand-crafted SQL ALTER TABLE statements on every deployment. Alembic with alembic revision --autogenerate would give versioned, repeatable, reviewable migrations.

2. No Celery task deduplication guard — Nothing prevents two concurrent scene_generate_image tasks targeting the same scene (e.g., user double-clicks). idempotency_key exists on Asset but isn't checked at enqueue time. A Redis-backed lock or a DB uniqueness constraint on (scene_id, job_type, status IN ('queued', 'running')) would prevent duplicate work.

3. Worker and API share the same SessionLocal — Both the FastAPI app and Celery workers import from db/session.py and get the same engine. This is intentional for a monorepo but means the pool settings affect both runtimes with different connection usage patterns.

4. ffprobe called on every video asset during manifest-build — Even when duration_sec is already stored in the manifest row, _manifest_row_duration_sec calls ffprobe for videos without an explicit duration. This is avoidable for assets whose duration was recorded at ingest time and hasn't changed. Storing duration_sec reliably at asset-creation time would eliminate many ffprobe calls during export.

Summary Table
Priority	Layer	Count
🔴 High	Backend	5
🔴 High	Frontend	3
🟡 Medium	Backend	7
🟡 Medium	Frontend	7
🟡 Medium	FFmpeg	4
🟢 Low	Backend	3
🟢 Low	Frontend	2
🟢 Low	FFmpeg	2
🏗️	Infrastructure	4
The highest-ROI items in order: (1) compound DB indexes (easy, measurable perf win), (2) N+1 query fixes in manifest processing (biggest latency win on export), (3) error boundaries in frontend (zero user-visible crashes), (4) extract worker_tasks.py per the existing plan (enables isolated testing and maintainability), (5) structured phase5 readiness errors (removes brittle frontend regex parsing of backend text).

The frontend review agent just completed. Its findings largely confirm the review above, plus a few additional points worth noting:

🆕 Addenda from the Secondary Frontend Audit
Root-level error boundary does exist in main.jsx (lines 7–61) — the gap is that it only catches at the root. Each major page (Editor, Research, Settings) still needs its own boundary so a crash in one page doesn't blank the entire app.

useToast toast ID counter never resets (nextId increments on every show() call with no upper bound). For a very long-lived browser session this reaches Number.MAX_SAFE_INTEGER. Easy fix: wrap with modulo or use crypto.randomUUID().

useProjectEvents swallows JSON parse errors silently — the SSE message handler has a bare catch {} block. If the backend sends malformed JSON in an event, the failure is invisible. Should at minimum console.warn in development.

loadSceneAssets() silently fails — unlike loadScenes() which sets scenesLoading, asset loading has no error state exposed to the user. A broken asset fetch shows an empty gallery with no explanation.

No AbortController on fetch calls — async data fetching functions don't cancel inflight requests when the component switches project/scene. The sceneNarrationMetaFetchGenRef pattern exists as a manual staleness guard, but the HTTP requests themselves still complete. Real cancellation would require AbortController passed to fetch().

idem function (the idempotency key generator) has an opaque name. newIdempotencyKey() or generateIdemKey() would self-document.

Asset thumbnails missing loading="lazy" — all <img> tags in the asset gallery eagerly load. With 50+ assets per scene, this is a meaningful first-render bandwidth cost.

The backend review agent has also completed. Here are the notable additional findings that complement the earlier review:

🆕 Addenda from the Secondary Backend Audit
Celery time limits partially unwired
celery_app.py sets a global task_time_limit=1800 (30 min), task_soft_time_limit=1500 (25 min). The phase-specific constants (_CELERY_PHASE3_SOFT_SEC = 7200, _CELERY_PHASE5_SOFT_SEC = 7200, etc.) are wired on the phase3/5 task decorators but the global 30-min hard cap would kill GPU generation tasks without those per-task overrides being present. Worth auditing all @celery_app.task decorators to confirm each uses the right per-phase limits.

No retry logic on transient external failures
Provider calls (fal.ai, OpenAI, Gemini, ElevenLabs, etc.) have no Celery retry on transient errors. A brief network timeout causes the entire 2-hour Phase 3 job to fail with no auto-recovery. @celery_app.task(autoretry_for=(httpx.TimeoutException, ...), max_retries=3, default_retry_delay=60) on the task entry points would handle the majority of transient cases without code changes inside the task body.

No rate limiting on expensive external API calls
Phase 3 with multiple workers can fire parallel fal.ai / OpenAI requests with no inter-process coordination. Rate-limit errors from the provider cascade into job failures. A Celery rate_limit per task or a shared Redis semaphore across workers would prevent this.

resolve_runtime_settings called on every API request
deps.py resolves runtime settings from the DB on every single request (querying AppSetting by tenant). At 100 req/s, that's 100 DB reads/s for the settings row that almost never changes. A short TTL (60–300s) in-process cache keyed on tenant ID would reduce this to near-zero, and Redis would make it shared across Uvicorn workers.

No per-task JobContext abstraction
Every Celery task entry point repeats: load job by ID → load project from job → load settings for tenant → parse payload → load related entities. This ceremony is ~20–30 lines per task. A shared JobTaskContext dataclass built once at the top of each task would eliminate the duplication and make unit testing task logic trivial (inject a mock context).

result_expires=86400 in celery_app.py
Celery stores task results in Redis for 24 hours. Since job status is already persisted to the jobs table in PostgreSQL, the Redis results are redundant. Reducing to 3600s (1 hour — enough for any polling client to read) would significantly reduce Redis memory usage on a busy deployment.

No incoming request body size limit
No Content-Length cap or middleware max-size guard on the FastAPI app. A client posting a multi-MB JSON body to any endpoint (e.g., project create with an enormous topic) would be processed synchronously in the request thread. Adding a size limit middleware (from starlette.middleware.gzip import GZipMiddleware + a body-size guard) prevents this class of DoS.

The full review across all three layers is now complete. The highest-ROI items that appeared independently in both audit passes: splitting worker_tasks.py, adding compound DB indexes, fixing the N+1 manifest queries, wiring per-task Celery time limits, and adding error boundaries to the frontend.

The FFmpeg/infrastructure agent has also completed. Here are the notable findings it surfaced beyond what was already covered:

🆕 Addenda from the FFmpeg & Infrastructure Audit
setsar=1 missing from slideshow.py and still_to_video.py filter chains
video_chain.py and mixed_timeline.py include setsar=1 in their scale_pad filter graphs, but slideshow.py (Ken Burns + zoom paths) and still_to_video.py (static path) do not. On inputs whose Sample Aspect Ratio is not 1:1 (portrait video from phones, some HEIF images), the output will be visibly stretched. Two-line fix: append ,setsar=1 to the filter string in both modules.

Redis container has no persistence enabled
The docker-compose.yml mounts a director_redis:/data volume but launches Redis with default settings (in-memory only, no RDB/AOF). If the Redis container restarts — including during a normal Electron app restart — the entire Celery task queue is lost. Pending jobs (queued scene renders, in-progress narration generates) vanish silently. Fix: command: redis-server --appendonly yes --appendfsync everysec in the compose service.

Docker Compose has no restart policies
All services (postgres, redis, minio) lack restart: unless-stopped. A container crash leaves the service down until someone manually runs docker compose up. Critical for a desktop app where the user might not notice until they click something and get no response.

No upfront validation before expensive timeline encodes
compile_mixed_visual_timeline validates inputs lazily (per-segment as it iterates). If the 50th segment path has a typo, the first 49 segments have already been fully encoded (wasting potentially 40+ minutes of CPU) before the error is raised. An O(n) pre-flight pass checking file existence and basic duration validity before any FFmpeg subprocess is launched would fail fast.

Electron app does not bundle FFmpeg
The Electron build packages the Python backend but not the FFmpeg binary. On a user's machine that doesn't have FFmpeg on PATH, the API launches successfully, but the first compile job fails with a confusing error. The binary (~50MB) could be bundled via electron-builder's extraResources and injected into PATH before spawning the API process.

tavily-python has no version constraint in pyproject.toml
Every other API dependency has a >=x.y.z floor, but tavily-python is listed without any version pin. A new major version with a breaking API would silently pull in during a clean install. Should be pinned to at least >=1.0.0,<2.

No CI/CD pipeline for FFmpeg tests
The test suite requires FFmpeg on PATH and covers ~9 modules. Without a GitHub Actions (or equivalent) workflow that runs pytest on pull requests, regressions to the pipeline can reach production undetected. The test infrastructure already exists — just needs a .github/workflows/test.yml wiring it up.

Pre-commit hooks and mypy not configured
ruff is configured in pyproject.toml but isn't enforced at commit time (no .pre-commit-config.yaml). Type checking (mypy) is not configured at all. Both are low-effort additions that catch a class of bugs before they become runtime errors.

That concludes the full three-layer review. The quick wins that surfaced across all agents: setsar=1 fix (two lines), Redis persistence (one line), Docker restart policies (three lines), and the pre-flight validation in compile_mixed_visual_timeline (a small guard loop before the main encode loop begins). All four have high impact-to-effort ratios.

The second frontend agent just completed, adding precise measurements and a few new specifics:

🆕 Additional Quantification & New Findings
Precise scale of the App.jsx problem:

111 useState calls, 80+ useCallback definitions, 60+ useMemo definitions — all in one component function
React.memo usage: zero (grep-confirmed across entire codebase) — no child component is protected from re-renders caused by parent state changes, making the 111 useState declarations even more impactful
Specific dead refs:

authModeRef — set at lines 971 and 985, never read anywhere
activeJobsPrevRef — declared (line 918), documented as "prior snapshot" in comment, but never compared against
Both could be removed without behavioral change.
SSE reconnect has no exponential backoff
useProjectEvents's reconnect delay is hardcoded at 4000ms flat. If the API is down for 10 minutes, the frontend fires 150 reconnect attempts at a fixed rate with no back-off. Should grow geometrically (e.g., 4s → 8s → 16s → cap at 60s).

Usage chart renders with an unoptimized IIFE
The usage model chart uses an immediately-invoked function expression in JSX that calls Math.max(...rows.map(...)) — O(n) — on every render of the usage page. Should be wrapped in useMemo.

shouldIgnoreUnauthorizedForPath uses substring .includes() matching

path.includes("/v1/auth/login")

This would accidentally match a hypothetical /v1/auth/login-history path. Should use exact equality or a Set.

director_editor_layout_v2 storage key implies a prior version was abandoned
The EditorLayoutContext uses "director_editor_layout_v2" as its localStorage key. The _v2 suffix suggests an earlier _v1 schema was incompatible and silently dropped. There's no migration path or user-facing explanation. Any future schema change would need a _v3 bump and should consider migrating compatible fields forward rather than silently discarding them.

Font Awesome full package in production bundle
@fortawesome/fontawesome-free (the full icon set, ~150KB gzipped) is loaded as a single dependency. If only a subset of icons is used, switching to the SVG core + individual icon imports (tree-shakeable) could meaningfully reduce the initial bundle.

That's the full picture from all five audit passes. The codebase is functionally sound and has good bones — the suggestions above are all about making it easier to maintain, scale, and deploy reliably.

The second backend agent has completed with several additional specifics worth calling out:

🆕 Additional Backend Findings
AuditEvent model exists but is never written to
models.py defines an AuditEvent table (lines 473–483) but no code in the entire codebase ever inserts into it. The usage tab and compliance scenarios that might rely on an audit trail get nothing. Either the table should be wired up (route mutations writing events) or it should be removed to avoid the misleading impression of audit coverage.

_scene_has_visual_media_for_auto() called inside a list comprehension

missing_img_scenes = [s for s in all_scenes if not _scene_has_visual_media_for_auto(db, s.id)]

Each call issues its own DB query. With 100 scenes this is 100 sequential DB queries in a single comprehension. The check should be replaced with a single query that returns scene IDs lacking visual media.

Default JWT secret is an empty string in config.py
director_jwt_secret: str = Field(default="") — if director_auth_enabled=True but the env var is not set, tokens are signed with an empty-string secret. main.py logs an error but does not refuse to start. Any request that guesses to send an empty-secret-signed JWT would pass validation.

Database URL has dev credentials hardcoded in config.py
The default database_url contains director:director_dev_change_me@localhost in plain text in the source file. In a public repo fork or a CI log, this is immediately visible. It should be Field(default=None) with a startup assertion that fails loudly if unset in non-dev environments.

/v1/admin/celery/restart has no rate limiting
The health router exposes a restart endpoint that spawns a subprocess (PowerShell on Windows, kill on Linux). It only checks assert_ops_route_allowed() — no rate limit on how many times it can be called per minute. Repeated calls could cycle workers faster than they can recover.

X-Tenant-Id header used in rate limiter without JWT validation
The rate limiter reads request.headers.get("x-tenant-id") as-is to construct the rate-limit key. In a multi-tenant deployment, any client could set X-Tenant-Id: other-tenant and consume another tenant's rate-limit budget, or avoid their own. The tenant should be extracted from the validated JWT, not the raw header.

NarrationTrack has no uniqueness constraint on (chapter_id, scene_id)
Nothing prevents creating two NarrationTracks for the same scene. The query that fetches the track uses .order_by(created_at.desc()).limit(1), which implicitly picks the latest — but duplicate rows accumulate over time and waste storage. A partial unique index on (chapter_id, scene_id) where audio_url IS NOT NULL would enforce one authoritative track.

create_project has no idempotency guard
Two simultaneous POST /v1/projects requests with identical titles and topics create two separate project rows. Unlike the job-creation endpoint (which has an Idempotency-Key header mechanism), project creation has no deduplication. A frontend double-submit or a network retry creates duplicate projects that must be deleted manually.

The codebase review is now fully consolidated across all five audit passes. All findings have been surfaced — the implementation priority is yours to set.