---
phase: 2
slug: research-writing
title: "Research and writing studio"
status: completed
progress_percent: 95
updated: 2026-03-22
depends_on: ["phase-01-foundation"]
source: "../project.md §8 Phase 2, §9.1–9.3, §14, §20 Phase 2 exit; docs/human-overrides.md; docs/roles-and-permissions.md"
---

# Phase 2 — Research and writing studio

**Goal:** Produce a strong documentary plan and script.

**Spec links:** [`project.md`](../project.md) §8 Phase 2, §20 · [`docs/api-spec.md`](../docs/api-spec.md) (research/script routes) · [`docs/human-overrides.md`](../docs/human-overrides.md) · [`docs/roles-and-permissions.md`](../docs/roles-and-permissions.md)

## Notes (review)

**Strengths:** Research-before-script and claim tracking match the product’s trust model.

**Gaps addressed:** Missing **API wiring** (§14), **schema contracts** for brief/outline/chapter, and **enforcement** of research gating (not just “should”). Added explicit **approve** path and **audit** for overrides. Clarified tension between **30–45 min vision** (success metric) and **MVP 10–15 min** (Phase 5): interim sign-off allowed if documented.

**Residual risk:** “Strong dossier” is subjective; mitigate with **P2-R06** checklist (minimum sources, disputed claims flagged) and human review for v1. **P2-M01** (30–45 min reference quality) remains a manual QA / content sign-off, not automated.

## Deliverables

### UI

- [x] **P2-D01** Project intake UI (title, topic, runtime, audience, tone, visual style, narration style, factual strictness, preferred providers, speech, music preference, budget) — _minimal Vite studio at `apps/web` (subset of fields); full brief parity via API_
- [x] **P2-D02** Outline viewer/editor (chapter list, summaries, runtime targets) — _list + summaries + `target_duration_sec` via `GET /v1/projects/:id/chapters`; edit title/summary/target via `PATCH /v1/chapters/:id`_
- [x] **P2-D03** Chapter script editor (per-chapter narration script, save/revert) — _per-chapter script edit + save (`PATCH /v1/chapters/:id`); revert = browser back / manual reload_

### Agents and pipelines

- [x] **P2-D04** Director Agent: project brief JSON, style guide, narrative structure, production constraints (validated schema) — _deterministic pack + optional OpenAI `enrich_director_pack` before `validate_director_pack`_
- [x] **P2-D05** Research Agent: source manifest, fact graph / timeline, chapter evidence packs (validated schema) — _Tavily + extraction + optional `enrich_research_dossier_body`; validated `research-dossier/v1`_
- [x] **P2-D06** Script Writer Agent: outline, chapter scripts, transitions (validated schema) — _optional `generate_outline_batch` / `generate_scripts_batch` with schema validation; fallback to deterministic outline + stub scripts if no key or invalid LLM output_

### Data and APIs

- [x] **P2-D07** Source and claim persistence with **source references**, confidence, and disputed flags (`Source` / related models per `project.md` §13)
- [x] **P2-D08** Schemas in `packages/schemas` for: documentary brief, research dossier summary, chapter outline, chapter script (versioned)
- [x] **P2-D09** APIs: `POST /projects/:id/research/run`, `GET /projects/:id/research`, `POST /projects/:id/research/approve`
- [x] **P2-D10** APIs: `POST /projects/:id/script/generate-outline`, `POST /projects/:id/script/generate-chapters`, `PATCH /chapters/:id/script`
- [x] **P2-D12** APIs: `GET /projects/:id/chapters` (incl. `pacing_warning`), `PATCH /chapters/:id` (title, summary, `target_duration_sec`, `script_text`)
- [x] **P2-D11** `POST /projects/:id/start` (or equivalent) defines clear state machine entry from brief → research → script

_Added `POST /projects/:id/research/override` per [`docs/human-overrides.md`](../docs/human-overrides.md). Research/script jobs use Celery (`director.run_phase2_job`, soft limit 600s). API allows CORS from Vite dev (`localhost:5173`)._

## Requirements

- [x] **P2-R01** User can create and edit a documentary brief end-to-end (UI + API) — _create + workflow via studio; full field editing can use `PATCH /v1/projects/:id`_
- [x] **P2-R02** Script outline/chapter generation **cannot run** until research is **approved** OR an **explicit override** is recorded (user, reason, timestamp)
- [x] **P2-R03** Claims without adequate sourcing are flagged; unsupported claims do not silently present as facts in generated script (surface in UI or metadata) — _LLM chapter generation uses `allowed_claims` / `disputed_claims`; research GET exposes claim flags_
- [x] **P2-R04** Chapter **target_duration_sec** enforced in generation prompts and visible in editors; violations surfaced as warnings — _`pacing_warning` on `GET /v1/projects/:id/chapters` and chapter PATCH response; prompts cite ~130 wpm_
- [x] **P2-R05** Director output is validated and stored before research job is eligible to run
- [x] **P2-R06** Research dossier meets minimum bar: ≥ N user-configurable sources (default documented), timeline present, disputed items flagged — _`research_min_sources` on project; Tavily-backed sources + timeline in dossier body; disputed claims flagged_

## Success metric

- [ ] **P2-M01** For a reference topic, produce a **30–45 minute** outline and chapter scripts that respect runtime targets **or** document an approved **MVP subset** (10–15 min equivalent chapters) with traceability to the same research dossier — _mechanism in place; quality sign-off still human_

## Exit criteria

- [x] **P2-X01** For one end-to-end project: approved research dossier with sources and claims, then generated **full** outline + chapter scripts, all validated against schemas and editable via UI — _`apps/web` + API path; LLM steps optional; schema validation on pack/dossier/batches when LLM used_
