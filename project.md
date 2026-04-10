AI Documentary Director Studio — Production Specification
1. Product summary

Build a production-grade agentic documentary creation platform that can generate a coherent 30–45 minute documentary from a topic prompt, using a multi-agent workflow for research, direction, writing, storyboarding, visual generation, scene critique, editing, and compilation. The system is **local-first**: projects, assets, and queues run primarily on **local storage** and local services; remote cloud storage is optional, not required.

The system must support multiple providers through a unified orchestration layer:

OpenAI for planning, writing, critique, structured outputs, and tool-calling workflows via the Responses API and function calling.
xAI / Grok for text, search-connected workflows, image generation, and video generation through xAI’s API and Imagine capabilities.
fal for production media generation pipelines, especially image/video jobs with queue-oriented execution and model diversity.
Wan2.2 (self-hosted) for open text-to-video, image-to-video, and related video tasks via the Wan reference implementation and weights ([Wan-Video/Wan2.2](https://github.com/Wan-Video/Wan2.2)), executed on GPU workers behind the same video adapter interface as cloud APIs.
OpenRouter as a routing and fallback layer for text-model access, structured outputs, and provider selection across supported models.
Self-hosted open-weight models for text (e.g. Qwen instruct and Qwen-VL checkpoints served via vLLM, llama.cpp, Ollama, or similar) and for narration audio (e.g. CosyVoice; optional lighter engines such as Piper or XTTS) on GPU or CPU workers, behind the same adapter interfaces as cloud APIs.
2. Product goal

Create a system that behaves like an AI documentary studio, not a prompt toy.

The platform should:

take a documentary topic and creative brief
research and organize the subject
write a chaptered long-form script
produce scene plans and asset requirements
infer and maintain a character bible for recurring figures (used before still and motion generation for visual consistency)
generate still and motion visuals
produce narration and edit sequences
critique and revise weak scenes
compile a final documentary and supporting assets
3. Primary outcomes

The platform should optimize for:

long-form coherence
factual grounding
visual continuity
efficient compute usage
traceability of claims and assets
repeatable production workflows
provider portability
4. Core design principles
4.1 Agent-first, deterministic where needed

Use agents for planning, ideation, drafting, critique, and revision. Use deterministic services for orchestration, manifests, storage, asset lineage, job state, and compilation.

4.2 Provider abstraction

No business logic should depend directly on one provider SDK. All providers must be accessed through internal adapters.

4.3 Structured outputs first

All agents that feed downstream tasks must return validated structured JSON, not loose prose, wherever possible. OpenAI and OpenRouter both support structured output patterns for this. For self-hosted text models (Qwen and similar), use the same JSON schemas with constrained decoding, tool-style templates, or parse-and-repair plus validation retries so downstream steps never accept unvalidated blobs.

4.4 Research before writing

No long-form documentary script should be produced without a research dossier and source graph.

4.5 Critique before publish

All scenes and chapters must pass automated quality gates before final compilation.

4.6 Hybrid media strategy

For runtime and cost control, the documentary should mix:

still images
animated stills
text overlays
maps
charts
generated motion clips
archival-style treatments
rather than relying on full-motion generation for the entire runtime.

4.7 Local-first architecture and local storage

The product is **local-first**: a single machine or LAN can run the full studio—**metadata, queues, and binary assets stay on local disk** by default. No cloud object store is required for core operation.

**Defaults**

- **Asset storage:** implement `AssetStorage` behind a small interface; **default backend = local filesystem** under a configurable root (e.g. `LOCAL_STORAGE_ROOT`), with content-addressed or project-scoped paths and stable `file://` or served **HTTP range** URLs for the local UI. Optional **MinIO** (or equivalent) on **localhost** provides an **S3-compatible** backend for the same interface when you want presigned-URL semantics without remote cloud.
- **Database:** **SQLite** acceptable for single-user local installs; **PostgreSQL** recommended for multi-user, heavier concurrency, or when aligning with §10.6 backup drills—still on localhost or LAN.
- **Redis:** local instance for queues (Docker or native); no managed Redis required.
- **Optional cloud sync** (out of core scope unless productized): export zip + manifest for user-managed backup; do not require ongoing sync for “running the app.”

**Implications**

- FFmpeg and workers read/write **local paths** or **localhost object APIs**; compilers must not assume AWS-only ARIs.
- Multi-tenant **SaaS** deployments may still use remote Postgres/S3; they are a **deployment profile**, not the default product assumption.

4.8 Python-based core

The **orchestration API, job workers, provider adapters, media helpers, FFmpeg orchestration, and automated tests** for those layers are implemented in **Python** (recommended **3.11+**). The studio **web UI** remains **TypeScript** (Next.js) per §11.1. Shared contracts are **JSON Schema** with validation in Python (**Pydantic** or equivalent) at service boundaries; TypeScript types may be generated from schemas where useful.

5. Supported providers and responsibilities
5.1 OpenAI responsibilities

Use OpenAI for:

Director Agent planning
Script Writer Agent
Scene Critic Agent
structured scene manifests
chapter summaries
tool orchestration
evaluation workflows

Reason: OpenAI officially supports Responses API, function calling, tools, and structured generation patterns useful for orchestration-heavy systems.

5.2 xAI / Grok responsibilities

Use Grok for:

topic exploration
search-connected research assistance where appropriate
image generation
video generation
alternative script ideation or creative variants

Reason: xAI documents Grok API access along with image generation, video generation, and agent tooling/search capabilities.

5.3 fal responsibilities

Use fal for:

high-volume media generation
image generation
image-to-video
text-to-video
queue-based reliable media jobs
multi-model experimentation and fallback

Reason: fal exposes model APIs for image, video, audio, and multimodal generation with queue-based, production-oriented execution.

5.4 OpenRouter responsibilities

Use OpenRouter for:

text-model routing and fallback
structured outputs where supported
provider selection
cost/performance routing
cross-model experimentation for planning, writing, and critique

Reason: OpenRouter provides a unified endpoint, structured outputs, and routing/provider selection controls.

5.5 Wan2.2 (self-hosted video) responsibilities

Use Wan2.2 for:

text-to-video and image-to-video (and optional unified TI2V) when running open weights on owned or rented GPU infrastructure
longer or higher-fidelity motion segments where API pricing or model choice favors local inference
optional speech-to-video (S2V) or character animation (Animate) workflows when those modes are enabled in production

Implementation expectations:

expose Wan only through a VideoProvider adapter that enqueues jobs to dedicated GPU workers (no direct `generate.py` calls from orchestration business logic)
support configurable model variants (e.g. T2V-A14B, I2V-A14B, TI2V-5B, S2V-14B, Animate-14B per upstream docs) via `model_name` / params on the asset job
store checkpoint locations or Hugging Face model IDs, resolution, fps, and CLI-equivalent flags in `params_json` so renders are reproducible
treat VRAM and multi-GPU (FSDP / Ulysses) as worker infrastructure concerns; the API surface remains async job submit + poll or webhook completion like other video providers

Reason: Wan2.2 is an open, Apache-2.0-licensed large-scale video stack suitable for self-hosted pipelines; see [Wan-Video/Wan2.2](https://github.com/Wan-Video/Wan2.2).

5.6 Self-hosted text (Qwen and similar) responsibilities

Use local or VPC-hosted text inference for:

drafting, summarization, and prompt expansion when API cost or data residency requires on-prem generation
preview and development tiers mapped to smaller checkpoints (e.g. Qwen2.5-7B/14B-Instruct)
optional full production paths when a sufficiently large instruct model is deployed (e.g. Qwen2.5-32B-Instruct or newer Qwen generations) and quality gates pass
multimodal captioning or visual QA when Qwen-VL (or an equivalent open VL model) is enabled on workers

Implementation expectations:

implement `LLMProvider` via a worker-backed adapter (HTTP to vLLM, TensorRT-LLM, llama.cpp server, Ollama, etc.); orchestration never imports model runtimes directly
record `model_id` / revision, quantization, temperature, max tokens, and server endpoint id in usage or job metadata for reproducibility
prefer instruct-tuned chat templates that match the serving stack; document the template id in the prompt registry

Reason: Qwen-family models are widely deployed for open long-context and multilingual text; they align with stacks (e.g. Wan prompt extension) that already assume Qwen-class expanders.

5.7 Self-hosted speech and audio (CosyVoice and similar) responsibilities

Use local or VPC-hosted TTS / voice cloning for:

narration synthesis from approved script segments
retakes when timing or wording changes
optional zero-shot or prompt-based voice consistency when the engine supports reference audio (e.g. CosyVoice-style workflows)

Implementation expectations:

define a `SpeechProvider` (or equivalent) adapter that returns audio artifacts consistent with `NarrationTrack` (wav/flac → stored URL, duration, sample rate) and supports async jobs for long chapters
primary recommended stack: [CosyVoice](https://github.com/FunAudioLLM/CosyVoice) class models for quality and control; optional alternates: Piper (fast CPU-friendly), Coqui XTTS or similar for experimentation—select per `preferred_speech_provider` and budget
store voice profile id, reference clip hashes, speaking rate, and engine version in `voice_config_json` / `params_json` for reproducibility

Reason: Open TTS reduces per-minute narration cost and keeps audio generation on the same job-queue model as video; CosyVoice is a strong default in the Qwen / Alibaba open ecosystem the rest of the stack already references.

6. High-level product workflow
User creates project
Director Agent defines documentary brief
Research Agent builds source dossier
Script Writer Agent creates long-form outline and chapter scripts
Storyboard Agent breaks chapters into scenes
Scene generation agents create visual assets
Narration pipeline generates voiceover
Critic Agent evaluates scenes and chapter cuts
Editor Agent assembles rough cut
Compiler renders final documentary
QA pipeline validates runtime, continuity, and exports
7. Agent architecture
7.1 Director Agent

Responsibilities:

define style, theme, pacing, target audience
set runtime budget by chapter
approve chapter structure
coordinate revisions
maintain global continuity

Inputs:

topic
target runtime
tone
audience
visual style preferences
factual strictness

Outputs:

project brief JSON
creative style guide
narrative structure
production constraints
7.2 Research Agent

Responsibilities:

gather source material
extract facts, dates, themes, locations, people
create evidence packets by chapter
assign confidence scores
flag disputed claims

Outputs:

source manifest
fact graph
timeline
chapter evidence packs
7.3 Script Writer Agent

Responsibilities:

create feature outline
write chapter-level narration
generate scene-by-scene script
preserve recurring motifs and transitions

Outputs:

documentary outline
chapter scripts
narration script
transition script
7.4 Storyboard / Scene Planner Agent

Responsibilities:

convert chapter script into scenes
define scene purpose
assign scene duration
determine required asset types
create prompts and references

Outputs:

scene cards
shot plans
asset requirements
timing allocations
7.5 Scene Image Generator Agent

Responsibilities:

generate stills
produce alt variations
maintain visual continuity references
create environment plates, chapter intros, inserts

Outputs:

approved still assets
prompt history
continuity tags
7.6 Scene Video Generator Agent

Responsibilities:

generate short motion sequences
animate still images
create establishing shots and reenactment-style clips
output source-aligned scene media

Outputs:

clip files
metadata
generation settings
revision variants
7.7 Narration Agent

Responsibilities:

generate voiceover from approved script
maintain narrator consistency
manage chapter timing and pauses
retake lines when timing shifts

Outputs:

narration audio
alignment timings
subtitle text source
7.8 Scene Critic Agent

Responsibilities:

review script-visual alignment
assess factual risk
score continuity
detect pacing problems
request revisions

Outputs:

critic reports
issue lists
revision instructions
scene pass/fail
7.9 Video Editor Agent

Responsibilities:

assemble scenes
align visuals to narration
add overlays, maps, captions, lower thirds
balance pacing across chapters

Outputs:

chapter timelines
rough cut
fine cut
7.10 Compiler Agent

Responsibilities:

drive **FFmpeg** as the authoritative final render: merge approved video timeline, **narration (voiceover)**, and **music** (and optional SFX) into output masters using filter graphs (`amix`, `loudnorm` / LUFS targets, ducking sidechain or level automation as configured)
burn or mux subtitles (soft subs + optional burned-in preview)
encode delivery formats (e.g. MP4/H.264 or HEVC) from the same timeline manifest so renders are reproducible
create project manifest
produce export package

Outputs:

final master (video + mixed audio)
stems or intermediate mixes when required for QA
subtitle files
chapter cuts
export manifest (must list FFmpeg command equivalents or structured graph spec + input asset URLs)
8. Phase-based build plan
Phase 1 — Foundation and orchestration

Goal: create a stable backend and agent control plane.

Deliverables
monorepo setup
provider adapters for OpenAI, Grok, fal, OpenRouter, Wan2.2 video (worker-backed VideoProvider), self-hosted text (worker-backed LLMProvider targeting Qwen-class or equivalent), and self-hosted speech (worker-backed SpeechProvider; CosyVoice-class default)
job queue
project model
scene and chapter schema
**local-first asset storage layer** (filesystem default per §4.7; optional localhost MinIO/S3-compatible or remote profile behind same interface)
structured logging
tracing
prompt/version registry
Requirements
all providers behind a common interface
all agent outputs validated against JSON schemas
each task has retry, timeout, and status states
all generation artifacts tracked in DB
Out of scope
final polished editing
full 45-minute generation
advanced multi-user collaboration
Phase 2 — Research and writing studio

Goal: produce a strong documentary plan and script.

Deliverables
project intake UI
Director Agent
Research Agent
Script Writer Agent
source manifest and claim tracking
outline editor
chapter script editor
Requirements
user can create a documentary brief
research dossier generated before script
claims stored with source references
chapter runtime targets enforced
Success metric
generate a complete 30–45 minute outline and script package
Phase 3 — Scene planning and media generation

Goal: turn the script into structured scenes and assets.

Deliverables
Storyboard Agent
scene cards UI
image generation pipeline
video generation pipeline (cloud APIs and/or self-hosted Wan2.2 workers)
provider switching per asset
asset approval workflow
Requirements
each scene has purpose, duration, visual prompt, and status
assets linked back to source scene
continuity tags recorded
failed jobs retry safely
Success metric
one full chapter can be turned into approved scene assets
Phase 4 — Critique, revision, and continuity

Goal: make output usable and coherent.

Deliverables
Scene Critic Agent
continuity validator
revision queue
chapter-level quality gates
scene scorecards
Requirements
no scene reaches edit stage without passing quality thresholds
continuity issues logged across scenes and chapters
revision loops capped and visible
Success metric
entire documentary can pass automated chapter QA
**Agentic orchestration:** the default `POST /v1/agent-runs` worker continues after scripted chapters with automated scene planning (per chapter with script), per-scene critic passes, and per-chapter critic gates (`steps_json`: `scenes`, `scene_critique`, `chapter_critique`; may `blocked` with `CRITIC_GATE`).
Phase 5 — Editing, narration, and compilation

Goal: produce a full watchable documentary.

Deliverables
narration pipeline (cloud APIs and/or self-hosted CosyVoice-class TTS)
subtitle generation
timeline assembler
video editor service
compiler/export service implemented on **FFmpeg** (via `packages/ffmpeg-pipelines` and compiler workers): final mux of picture edit + narration layer + music beds + merges
Requirements
rough cut and final cut supported
audio sync and subtitles included
narration and music (and optional SFX) mixed under FFmpeg with documented levels, fades, and ducking rules in the manifest
final exports reproducible from manifest
Success metric
export a complete 10–15 minute MVP documentary, then scale to 30–45 minutes
Phase 6 — Production hardening

Goal: make it commercially deployable.

Deliverables
usage accounting
multi-tenant auth
admin dashboard
provider cost controls
observability dashboards
evaluation suite
resumable renders
**Deferred from Phase 3:** end-to-end **video generation** validation (real encoder vs stub)—before GA, per **`phases/phase-06-hardening.md`** deliverable **P6-D16**
Requirements
rate limits aligned with **§10.6** defaults (tunable per environment)
tenant isolation
model fallback policies
queue prioritization
audit logs
Success metric
system meets **§10.6** concurrency, SLO, and reliability targets on reference staging; load-test artifact archived
9. Functional requirements
9.1 Project intake

User must be able to specify:

title
topic
target runtime
audience
tone
visual style
narration style
music / score preference or licensed bed selection (feeds compiler FFmpeg mix)
factual strictness
preferred providers (text, image, video, speech)
preferred speech provider when using self-hosted TTS (e.g. CosyVoice vs cloud)
budget sensitivity
9.2 Research

System must:

create source dossier
extract claims and timelines
summarize key themes
organize by chapter relevance
mark unsupported claims
9.3 Script generation

System must:

create a chapter structure
generate narration draft
generate transition text
allocate runtime by chapter
allow human edits before production
9.4 Scene planning

Each scene must include:

scene id
chapter id
purpose
planned duration
narration reference
visual type
prompt package
continuity notes
generation provider
status
9.5 Media generation

System must support:

text-to-image
image editing where provider supports it
text-to-video
image-to-video
text-to-speech for narration via `SpeechProvider` (cloud APIs where integrated, or self-hosted CosyVoice-class / Piper-class engines)
reruns with prompt variants
provider selection per scene
speech engine selection per project or chapter aligned with `preferred_speech_provider`

xAI documents image and video generation; fal documents image/video model APIs including image-to-video and text-to-video flows. Wan2.2 covers self-hosted T2V, I2V, TI2V, and optional S2V/Animate modes via [Wan-Video/Wan2.2](https://github.com/Wan-Video/Wan2.2). Local TTS stacks follow §5.7.

9.6 Critique and revision

System must:

critique every scene
issue structured revision feedback
maintain critic history
block low-quality scenes from editing
9.7 Editing and compilation

System must:

assemble approved scenes
align narration
add overlays and subtitles
export MP4 and subtitle files using **FFmpeg**-based pipelines that combine final video, **mixed narration + music** (and optional SFX), and caption tracks
produce manifest for reproducibility (inputs, filter graph or equivalent structured recipe, encode settings)
10. Non-functional requirements
10.1 Reliability
all jobs retriable
idempotent task execution
resumable project state
provider outage fallback where configured
10.2 Scalability
queue-based generation
per-tenant concurrency limits
independent worker pools for text and media jobs
optional GPU worker pool(s) for Wan2.2 inference, isolated from generic CPU/text workers
optional LLM inference pool(s) for Qwen-class text and optional GPU/CPU pools for CosyVoice-class or Piper-class speech
10.3 Observability
structured logs
trace by project, chapter, scene, asset, and provider job
metrics for cost, latency, failure rate, revision count
10.4 Security
encrypted secrets
no API keys in client
signed webhooks if callbacks used
per-tenant provider credentials optional
10.5 Cost control
project budgets
scene-level spend tracking
provider fallback rules
automatic downgrade for non-critical previews
10.6 Production SLOs and capacity targets (Phase 6)

The following are **initial GA targets**; tune per deployment. They define what “Phase 6 exit” means in measurable terms.

Availability and latency (API gateway + core orchestration API, excluding long-poll asset generation)

monthly availability **≥ 99.5%** (exclude scheduled maintenance windows if documented)
p95 latency **< 800 ms** for authenticated read APIs (`GET` project/chapter/scene lists and detail)
p95 latency **< 3 s** for mutation APIs that only enqueue work (`POST` research run, generate scene, enqueue media)
synchronous paths that block on an LLM must return within **60 s** or return **202** with a job id (no unbounded HTTP hold)

Queue and workers

p95 **job pickup time** **< 60 s** from enqueue to worker start under nominal load (queue depth below the per-tenant media concurrency cap)
stale job detection: tasks **no heartbeat / no progress** for **45 min** (configurable) marked failed or requeued with alert

Concurrency defaults (adjust with hardware)

**≥ 2** concurrent **end-to-end productions** (each: active project progressing through pipeline without manual infra fixes) on reference staging hardware
default **per-tenant** caps: **3** concurrent **media** jobs (image + video + speech generation), **2** concurrent **compile** (FFmpeg) jobs, **5** concurrent **text** agent jobs unless overridden by plan tier
default **global** cap: **20** concurrent media jobs across tenants (prevents GPU/queue starvation)

Rate limits (starting point)

**120** requests per minute per tenant on REST API (burst **30**); stricter limits on expensive endpoints (e.g. **6** compile/export starts per tenant per hour unless admin override)

Reliability of batch work

rolling **7-day** job **failure rate ≤ 8%** after automatic retries, **excluding** documented provider outages; **≤ 15%** including retries exhausted (triggers review)

Backup and recovery (staging drill before GA)

PostgreSQL: **RPO ≤ 24 h** with daily automated backup; **RTO ≤ 4 h** for full restore to new instance on documented procedure
local asset store: filesystem trash/version folder or snapshot policy so export manifests and assets remain recoverable for **≥ 30 days** after delete (or equivalent for optional S3-compatible local bucket); document path layout in [`docs/local-first-storage.md`](docs/local-first-storage.md)

11. Recommended technical architecture
11.1 Frontend
Next.js
TypeScript
Tailwind
studio interface with chapter/scene panels
timeline preview and asset status views
11.2 Backend (**Python**)
**Python** **FastAPI** application for orchestration and media pipeline (see §4.8)
**Local-first:** **SQLite** or **PostgreSQL** for metadata and manifests (Postgres preferred for multi-user / Phase 6 profile)
Redis for queues and caching (local)
Celery or Dramatiq workers
**Local filesystem asset storage** by default (optional **localhost MinIO** or other S3-compatible store behind the same `AssetStorage` interface; remote cloud only as an optional deployment profile)
FFmpeg for **final** compilation and rendering: timeline concat, transcodes, **multi-layer audio merge** (narration + music + optional SFX), subtitle mux/burn-in, and chapter/cut exports (implemented primarily in `packages/ffmpeg-pipelines` + compiler service)
optional Temporal for durable workflow orchestration
11.3 Service layout
api-gateway
orchestration-service
provider-adapter-service
research-service
script-service
scene-planner-service
media-generation-service
critic-service
narration-service
editor-service
compiler-service
billing-usage-service
12. Provider abstraction specification

Create a common interface:

interface LLMProvider {
  generateStructured<T>(input: StructuredGenerationRequest<T>): Promise<T>;
  generateText(input: TextGenerationRequest): Promise<TextGenerationResponse>;
}

interface ImageProvider {
  generateImage(input: ImageGenerationRequest): Promise<AssetJobResult>;
  editImage?(input: ImageEditRequest): Promise<AssetJobResult>;
}

interface VideoProvider {
  generateVideo(input: VideoGenerationRequest): Promise<AssetJobResult>;
  imageToVideo?(input: ImageToVideoRequest): Promise<AssetJobResult>;
}

interface SpeechProvider {
  synthesizeSpeech(input: SpeechSynthesisRequest): Promise<AssetJobResult>;
}
`SpeechSynthesisRequest` carries script text (or SSML subset if supported), voice reference / speaker id, language, and quality tier; the result writes narration audio to **asset storage** (default: local filesystem per §4.7) and exposes duration for alignment.

Wan2.2 implements the same VideoProvider contract via an internal adapter that schedules jobs to GPU workers (checkpoint path, task id such as `t2v-A14B` / `ti2v-5B`, resolution, optional prompt-extension flags) and returns `AssetJobResult` when outputs are written to **asset storage** (local-first per §4.7).

Self-hosted Qwen-class (or equivalent) models implement `LLMProvider` through an OpenAI-compatible or bespoke HTTP worker; structured outputs are enforced at validation boundaries as in §4.3.

CosyVoice-class (or Piper / XTTS when configured) engines implement `SpeechProvider` through TTS workers; long chapters may chunk text and concatenate with cross-fade handled in the narration or editor service.

Required adapters
OpenAIAdapter
GrokAdapter
FalAdapter
OpenRouterAdapter
Wan22VideoAdapter (worker-backed; no Wan imports in orchestration services)
LocalLlmAdapter (Qwen-class default; vLLM/Ollama/llama.cpp-compatible; worker-backed)
CosyVoiceSpeechAdapter (worker-backed; alternate `LocalSpeechAdapter` implementations share the same interface)
Routing rules
OpenAI default for structured planning/critique when using cloud
Grok optional for search-rich ideation and native xAI image/video flows
fal default for heavy media generation when using hosted APIs
Wan2.2 optional when self-hosted open-weights video is required (cost, latency, or model-specific quality)
OpenRouter for fallback text routing and cross-model experimentation
Local Qwen-class (or peers) optional for preview tiers, data residency, or cost-capped full runs when quality gates allow
CosyVoice-class (or peers) optional for narration when speech workers are provisioned

This routing strategy is grounded in the documented strengths of each provider’s current platform features and on open self-hosted video, text, and speech stacks where applicable.

13. Data model
Project
id
title
topic
status
target_runtime_minutes
audience
tone
visual_style
narration_style
factual_strictness
budget_limit
preferred_text_provider
preferred_image_provider
preferred_video_provider
preferred_speech_provider
created_at
updated_at
Source
id
project_id
url_or_reference
title
source_type
credibility_score
extracted_facts_json
notes
created_at
Chapter
id
project_id
order_index
title
summary
target_duration_sec
status
script_text
created_at
updated_at
Scene
id
chapter_id
order_index
purpose
planned_duration_sec
narration_text
visual_type
prompt_package_json
continuity_tags_json
status
critic_score
approved_at
Asset
id
scene_id
asset_type
provider
model_name
prompt_text
params_json
storage_url
preview_url
generation_status
cost_estimate
approved
created_at
NarrationTrack
id
chapter_id
scene_id_nullable
text
voice_config_json
audio_url
duration_sec
created_at
MusicBed
id
project_id
title
storage_url
license_or_source_ref
mix_config_json
created_at
CriticReport
id
target_type
target_id
score
issues_json
recommendations_json
pass
created_at
TimelineVersion
id
project_id
version_name
timeline_json
render_status
output_url
created_at
UsageRecord
id
project_id
provider
service_type
units
cost_estimate
request_id
created_at
14. API specification
Project APIs
POST /projects
GET /projects/:id
PATCH /projects/:id
POST /projects/:id/start
Research APIs
POST /projects/:id/research/run
GET /projects/:id/research
POST /projects/:id/research/approve
Script APIs
POST /projects/:id/script/generate-outline
POST /projects/:id/script/generate-chapters
PATCH /chapters/:id/script
Scene APIs
POST /chapters/:id/scenes/generate
GET /chapters/:id/scenes
PATCH /scenes/:id
Media APIs
POST /scenes/:id/generate-image
POST /scenes/:id/generate-video
POST /scenes/:id/retry
POST /assets/:id/approve
Critic APIs
POST /scenes/:id/critique
POST /chapters/:id/critique
GET /critic-reports/:id
Narration APIs
POST /chapters/:id/narration/generate
POST /projects/:id/subtitles/generate
Edit/Compile APIs
POST /projects/:id/rough-cut
POST /projects/:id/fine-cut
POST /projects/:id/final-cut
POST /projects/:id/export

Detailed conventions (versioned **`/v1/`** paths, idempotency, async **202** jobs, error envelope): [`docs/api-spec.md`](docs/api-spec.md). Stable error codes: [`docs/error-codes.md`](docs/error-codes.md). Webhooks: [`docs/webhooks.md`](docs/webhooks.md). Versioning: [`docs/versioning-policy.md`](docs/versioning-policy.md).

15. Prompting and schema rules
15.1 Prompt versioning

Store every prompt with:

prompt id
agent type
version
provider compatibility
expected schema
evaluation score
15.2 Schema validation

All agent outputs must validate before downstream use.

Examples:

documentary brief schema
chapter outline schema
scene card schema
critic report schema

OpenAI function-calling guidance and OpenRouter structured outputs both support this design; local `LLMProvider` implementations must still emit JSON that validates against the same schemas (see §4.3).

16. Quality and evaluation framework
Scene evaluation dimensions
script alignment
visual coherence
factual confidence
continuity consistency
emotional fit
pacing usefulness
technical quality
Chapter evaluation dimensions
narrative arc
chapter transitions
runtime fit
repetition control
source coverage
Final documentary evaluation dimensions
coherence across runtime
factual traceability
average scene quality
audio intelligibility
visual continuity
render completeness
17. Cost and compute strategy
Preview mode
low-cost text provider via OpenRouter and/or small local Qwen-class checkpoints when suitable
still images first
reduced clip duration
low-res drafts
Production mode
strongest selected writing provider
stronger media models
full-resolution exports
chapter-level batch rendering
Efficiency rule

Never attempt to generate all 30–45 minutes as raw AI motion.
Preferred composition:

50–70% still/animated still/document graphics
20–35% motion clips
10–20% transitions, overlays, maps, titles

This is a practical inference from current image/video generation workflows and queue-based media systems, including fal, xAI’s video/image offerings, and self-hosted stacks such as Wan2.2.

18. Cursor implementation instructions

Use this implementation guidance in Cursor:

Build order
scaffold monorepo
implement provider adapters (including Wan2.2 worker integration for VideoProvider, local LLM workers for Qwen-class text, and CosyVoice-class speech workers)
implement schemas and DB models
build orchestration workflows
add research and script pipeline
add scene planner
add media generation workers
add critic and revision loop
add narration and editor
add compiler and export flow (FFmpeg: video + narration + music merge and delivery encodes)
add observability, auth, cost controls
Required repo structure
/apps
  /web
  /api

/packages
  /schemas
  /prompt-registry
  /provider-adapters
  /workflow-engine
  /media-utils
  /ffmpeg-pipelines
  /shared-types

/services
  /orchestrator
  /research
  /script
  /scene-planner
  /media-generation
  /critic
  /narration
  /editor
  /compiler
  /usage-metering

/infrastructure
  /docker
  /migrations
  /terraform
Engineering standards
**Python 3.11+** for API, workers, adapters, `packages/ffmpeg-pipelines`, and backend tests (primary codebase)
TypeScript for **frontend** (Next.js) and optional generated types from schemas
JSON Schema or Pydantic validation everywhere
no provider SDK calls outside adapters
all long-running jobs async and resumable
every asset must be reproducible from stored params
19. MVP definition
MVP runtime

10–15 minutes

MVP providers
OpenAI for planning/script/critique
fal for image/video (default hosted path)
Wan2.2 optional on GPU workers for text-to-video / image-to-video via the same scene-level provider selection ([Wan-Video/Wan2.2](https://github.com/Wan-Video/Wan2.2))
OpenRouter optional fallback for writing
local Qwen-class text optional for drafts, previews, or residency-constrained runs
Grok optional second-pass creative generation
local CosyVoice-class (or Piper) speech optional for narration and retakes
MVP features
create project
generate research dossier
generate full script
generate scene cards
generate stills and selected video clips
generate narration
assemble rough cut
export watchable video
Not in MVP
45-minute fully automatic polished cinematic output
real-time collaborative editing
custom fine-tuned models
advanced native timeline editor
enterprise billing controls
20. Phase exit criteria
Phase 1 exit

System can create projects, call all configured providers through adapters (including Wan2.2 video, local text, and local speech jobs when worker infrastructure is enabled), and store validated outputs.

Phase 2 exit

System can generate a strong research dossier and complete long-form script.

Phase 3 exit

System can generate scene cards and create linked assets for at least one chapter.

Phase 4 exit

System can critique and revise scenes automatically with measurable score improvements.

Phase 5 exit

System can export a coherent 10–15 minute documentary.

Phase 6 exit

System sustains **≥ 2** concurrent end-to-end productions (§10.6), stays within **§10.6** API latency and availability targets on a defined measurement window, meets **§10.6** rolling job failure-rate caps, and exposes **cost** and **failure** monitoring on dashboards; staging **backup restore** drill completed per **§10.6**.

21. Reference documentation and operational policies

Normative product and technical rules remain in this file; the following **`docs/`** guides operationalize them (runbooks, API outline, governance). **`adr/`** records architecture decisions.

21.1 Documentation index

Master list: [`docs/README.md`](docs/README.md).

21.2 Human overrides and audit

Research/script gates, critic waivers, export acknowledgments: [`docs/human-overrides.md`](docs/human-overrides.md).

21.3 Roles and permissions

[`docs/roles-and-permissions.md`](docs/roles-and-permissions.md).

21.4 Failure UX and compile policy

Scene/chapter degradation, continue-anyway, compile gating: [`docs/failure-ux.md`](docs/failure-ux.md).

21.5 Preview vs production tiers

[`docs/preview-vs-production.md`](docs/preview-vs-production.md).

21.6 Music, SFX, and licensing (operator obligations)

[`docs/music-licensing.md`](docs/music-licensing.md).

21.7 Data retention, secrets, and threat model

[`docs/data-retention.md`](docs/data-retention.md), [`docs/secrets-and-keys.md`](docs/secrets-and-keys.md), [`docs/threat-model.md`](docs/threat-model.md).

21.8 Operations runbooks

[`docs/runbooks/provider-outage.md`](docs/runbooks/provider-outage.md), [`docs/runbooks/queue-stuck.md`](docs/runbooks/queue-stuck.md), [`docs/runbooks/worker-disk-and-oom.md`](docs/runbooks/worker-disk-and-oom.md).

21.9 Local-first storage

On-disk layout, `AssetStorage` backends, SQLite vs Postgres: [`docs/local-first-storage.md`](docs/local-first-storage.md).

21.10 Adapter smoke checklist (Phase 1)

[`docs/ADAPTER_SMOKE.md`](docs/ADAPTER_SMOKE.md)

21.11 FFmpeg baseline and contract testing

[`docs/ffmpeg-baseline.md`](docs/ffmpeg-baseline.md), [`docs/contract-testing.md`](docs/contract-testing.md).

21.12 Telemetry for Phase 6 (emit from Phase 1)

Log/metric fields so §10.6 dashboards do not require a retrofit: [`docs/phase-6-telemetry-fields.md`](docs/phase-6-telemetry-fields.md).

21.13 Architecture Decision Records

[`adr/README.md`](adr/README.md).

21.14 Repository scaffold

Root [`README.md`](README.md), [`docker-compose.yml`](docker-compose.yml), [`.env.example`](.env.example), [`Makefile`](Makefile), [`packages/schemas/`](packages/schemas/) (JSON Schema + [`fixtures/golden-project-minimal.json`](packages/schemas/fixtures/golden-project-minimal.json)), [`apps/api/`](apps/api/) (**Python** FastAPI package [`pyproject.toml`](apps/api/pyproject.toml) + `director_api`), [`apps/web/`](apps/web/) (TypeScript Next.js placeholder). Python tooling: [`docs/python-stack.md`](docs/python-stack.md).

22. One-line positioning

An AI documentary studio—**local-first** by default—that plans, writes, visualizes, critiques, edits, and compiles long-form documentary films through a governed multi-agent production pipeline.