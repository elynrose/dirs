---
phase: 4
slug: critique-continuity
title: "Critique, revision, and continuity"
status: done
progress_percent: 88
updated: 2026-03-22
depends_on: ["phase-03-scenes-media"]
source: "../project.md §8 Phase 4, §9.6, §14, §16, §20 Phase 4 exit; docs/human-overrides.md; docs/failure-ux.md"
---

# Phase 4 — Critique, revision, and continuity

**Goal:** Make output usable and coherent.

**Spec links:** [`project.md`](../project.md) §8 Phase 4, §16, §20 · [`docs/human-overrides.md`](../docs/human-overrides.md) (waivers) · [`docs/failure-ux.md`](../docs/failure-ux.md) · [`docs/roles-and-permissions.md`](../docs/roles-and-permissions.md)

## Notes (review)

**Strengths:** Gates before edit and capped revision loops match “critique before publish.”

**Gaps addressed:** “Edit stage” was ambiguous (FFmpeg vs timeline assembly)—now defined as **handoff to Phase 5 timeline/editor**. “Measurable improvement” had no definition; added **metric** and **baseline** capture on `CriticReport`. Added **APIs** from §14.

**Residual risk:** Auto-revision can oscillate; **P4-R04** caps and human override must be product-visible.

**Implemented slice (2026-03):** DB **`critic_reports`** + **`revision_issues`**; **Agentic path:** `run_agent_run` continues after script chapters with **`scenes`** → **`scene_critique`** → **`chapter_critique`** (`steps_json` events), `project.workflow_phase` **`critique_complete`** or **`critique_review`** when blocked; reports include `meta_json.source: "agent_run"`. **Manual path:** Celery jobs **`scene_critique`** / **`chapter_critique`** for ad-hoc runs; deterministic **continuity** + optional **OpenAI** dimensions; **`chapters.critic_gate_status`**; env **`CRITIC_*`** / **`CHAPTER_MIN_SCENE_PASS_RATIO`**; studio + **GET critic report**.

**Implemented (2026-03, continuation):** **Waiver** POSTs (`/scenes/.../critic/waive`, `/chapters/.../critic-gate/waive`), **PATCH** `/revision-issues/{id}`, **`scene_critic_revision`** job (LLM + deterministic fallback), **`projects.critic_policy_json`** merged in **`critic_policy.effective_policy`**, worker/agent loops respect per-project caps and skip waived chapter gates. **GET** `/projects/{id}/phase5-readiness` summarizes gate + scene waiver state for Phase 5 handoff.

**Still missing for exit:** **P4-M01** full before/after metric capture across automated revision on the same scene set; **P4-X01** golden-chapter proof with measurable improvement or structured blocked outcome; optional **enforce** readiness on Phase 5 mutating routes (today advisory + client discipline).

**Optional slice:** For an internal demo only, you may run Phase 5 on **manually approved** scenes while P4 is stubbed—document as **non-exit**. Production exit requires **P4-X01**.

## Deliverables

- [x] **P4-D01** Scene Critic Agent: structured report (dimensions from `project.md` §16 where applicable), **score**, **issues_json**, **recommendations_json**, **pass** boolean — _worker + `CriticReport`; LLM fills dimensions when `OPENAI_API_KEY` set, else heuristic merge_
- [x] **P4-D02** Continuity validator: cross-scene checks (tags, recurring visuals, tone) + chapter-level rollup — _`services/phase4.py` findings + `continuity_json` on report_
- [x] **P4-D03** Revision queue: issues linked to scene/asset/script lines; status (open / in progress / resolved / waived) — _`revision_issues` + **PATCH** `/revision-issues/{id}`_
- [x] **P4-D04** Chapter-level quality gate: aggregate scene passes + chapter dimensions before **Phase 5 handoff** — _`chapter_critique` job sets `chapters.critic_gate_status` **passed** \| **blocked**_
- [x] **P4-D05** Scene scorecards in UI and/or API (`GET /critic-reports/:id`) — _API + studio panel; scenes list shows score / pass / revision count_
- [x] **P4-D06** APIs: `POST /scenes/:id/critique`, `POST /chapters/:id/critique` — _202 + jobs; idempotency_

## Requirements

- [~] **P4-R01** No scene enters **Phase 5 timeline assembly** until it meets configured thresholds (or explicit **waiver** with user + reason) — _`GET /projects/{id}/phase5-readiness` + waiver columns; Phase 5 **POST** routes do not hard-block yet_
- [x] **P4-R02** Continuity issues stored with **references** (scene ids, asset ids, tag names); visible in UI — _`refs_json` / report `continuity_json`_
- [x] **P4-R03** Maximum revision iterations per scene/chapter **enforced**; UI shows count and stop reason — _`CRITIC_MAX_REVISION_CYCLES_PER_SCENE` + **409**; `critic_revision_count` on scene_
- [x] **P4-R04** Waivers and auto-retry decisions are **auditable** (who/when/why) — _waiver POSTs + DB columns on `scenes` / `chapters`; revision-issue **waived** requires actor + reason_
- [x] **P4-R05** Thresholds and dimension weights are **configurable per project** (defaults documented) — _`critic_policy_json` on **PATCH** `/projects/{id}`; merge in `services/critic_policy.py`; env fallbacks in `docs/api-spec.md`_

## Measurement (for exit)

- [ ] **P4-M01** Define **primary metric** (e.g. weighted critic score or pass rate) and store **before** and **after** revision on the same scene set — _baseline_score / prior_report_id stored on scene critic; no automated revision loop yet_
- [~] **P4-M02** On a fixed **golden chapter** (test fixture), show **measurable** improvement after one automated revision cycle **or** document why no improvement was possible (blocked issue) — _`tests/test_phase4_golden.py` covers aggregate + merge helpers; full revision-cycle metric test still open_

## Exit criteria

- [ ] **P4-X01** Automated critique → revision loop improves the primary metric on the golden chapter **or** produces a structured **blocked** outcome with human-readable explanation
- [ ] **P4-X02** All scenes in an MVP-length project can pass chapter QA under default thresholds **or** explicit waivers with audit trail
