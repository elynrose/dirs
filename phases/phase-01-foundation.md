---
phase: 1
slug: foundation
title: "Foundation and orchestration"
status: done
progress_percent: 85
updated: 2026-03-22
depends_on: []
source: "../project.md §8 Phase 1, §12–§14, §20 Phase 1 exit; ../project.md §21.14; §4.7; docs/api-spec.md; docs/local-first-storage.md; docs/phase-6-telemetry-fields.md; docs/ADAPTER_SMOKE.md"
---

# Phase 1 — Foundation and orchestration

**Goal:** Create a stable backend and agent control plane.

**Spec links:** [`project.md`](../project.md) §8 Phase 1, §4.7, §20 · [`docs/local-first-storage.md`](../docs/local-first-storage.md) · [`docs/api-spec.md`](../docs/api-spec.md) · [`docs/phase-6-telemetry-fields.md`](../docs/phase-6-telemetry-fields.md) · [`adr/001-fastapi-celery-dramatiq-stack.md`](../adr/001-fastapi-celery-dramatiq-stack.md) · [`adr/003-local-first-storage.md`](../adr/003-local-first-storage.md)

## Notes (review)

**Strengths:** Clear adapter list aligned with `project.md`; exit criterion matches “validated outputs + configured providers.”

**Gaps addressed:** Phase 1 originally lacked a minimal **HTTP API**, **local dev stack**, **migration path**, and **tiering** for adapters—so Phase 2 had nothing concrete to attach to. Added explicit **Projects CRUD**, **compose stack**, **shared schemas in CI**, and **Tier A / B** adapter rollout so you can exit early on cloud-only while keeping the full interface.

**Residual risk:** “Every configured provider” is environment-dependent; use **P1-X02** to record which adapters were actually smoke-tested. Self-hosted workers may lag cloud adapters; that is acceptable if gates and docs say so.

## Deliverables

### Core platform

- [ ] **P1-D01** Monorepo scaffold (`apps/web`, `apps/api`, `packages/*`, `services/*` per `project.md` §18)
- [ ] **P1-D02** Database migrations and ORM models for **Project**, **Chapter**, and **Scene** per `project.md` §13 (minimal columns acceptable; script/media fields may stay null until later phases)
- [ ] **P1-D03** Local dev stack documented (e.g. Docker Compose: Postgres, Redis, S3-compatible object storage)
- [ ] **P1-D04** **Asset storage** per [`docs/local-first-storage.md`](../docs/local-first-storage.md): default **local filesystem** (`LOCAL_STORAGE_ROOT`); optional MinIO/S3 profile behind same interface; artifact URL or `storage_key` persistence in DB
- [ ] **P1-D05** Job queue wired (Redis + Celery, Dramatiq, or equivalent) with worker process(es) runnable locally
- [ ] **P1-D06** Structured logging (JSON) with **correlation id** propagated API → queue → provider calls
- [ ] **P1-D07** Distributed tracing hooks (OpenTelemetry or equivalent) for project / job / asset spans
- [ ] **P1-D08** Prompt and version registry (DB + storage or repo pointer; metadata for agent type and schema id)

### Schemas and validation

- [ ] **P1-D09** `packages/schemas` (JSON Schema and/or Pydantic models) for at least one end-to-end **agent output** type (smoke schema)
- [ ] **P1-D10** CI step: validate schemas and fail on breaking changes (lightweight gate)

### HTTP API (minimal, aligns with §14)

- [ ] **P1-D11** `POST /projects`, `GET /projects/:id`, `PATCH /projects/:id` (create/read/update brief fields)
- [ ] **P1-D12** Health/readiness endpoints for API and workers

### Provider adapters (implement `LLMProvider` / `ImageProvider` / `VideoProvider` / `SpeechProvider` as applicable)

**Tier A — ship first (cloud MVP path)**

- [ ] **P1-D13** OpenAI adapter (`LLMProvider`: structured + text)
- [ ] **P1-D14** fal adapter (`ImageProvider` and/or `VideoProvider`)
- [ ] **P1-D15** OpenRouter adapter (`LLMProvider`)

**Tier B — ship when staging/prod needs full matrix**

- [ ] **P1-D16** Grok (xAI) adapter
- [ ] **P1-D17** Wan2.2 worker-backed `VideoProvider` adapter
- [ ] **P1-D18** Local / Qwen-class worker-backed `LLMProvider` adapter
- [ ] **P1-D19** CosyVoice-class (or alternate) worker-backed `SpeechProvider` adapter
- [ ] **P1-D20** Log and metric **field names** per [`docs/phase-6-telemetry-fields.md`](../docs/phase-6-telemetry-fields.md) (nullable OK until features exist; avoids Phase 6 retrofit)

## Requirements

- [ ] **P1-R01** No provider SDK imports in domain or orchestration services—only inside adapter implementations
- [ ] **P1-R02** Agent-facing responses validated against registered schemas before persistence or handoff
- [ ] **P1-R03** Jobs support **retry**, **timeout**, and explicit **status** (queued / running / succeeded / failed / cancelled)
- [ ] **P1-R04** Generation jobs persist **artifact lineage** (project id, provider, model, params hash or `params_json`, storage URL)
- [ ] **P1-R05** Secrets via environment or secret manager; sample `.env.example` only—no real keys in repo
- [ ] **P1-R06** Idempotency or dedupe strategy for enqueue (e.g. idempotency key header) to avoid double spend on retries
- [ ] **P1-R07** Health/readiness checks cover DB + Redis (and object storage probe optional) for §10.6 synthetic probes

## Out of scope (for this phase)

- [ ] **P1-O01** Final polished editing (deferred)
- [ ] **P1-O02** Full 45-minute automatic output (deferred)
- [ ] **P1-O03** Advanced real-time multi-user collaboration (deferred)

## Exit criteria

- [ ] **P1-X01** Create a project via API, enqueue a **smoke job** per **implemented** adapter type (at minimum Tier A), persist **validated** structured output or asset metadata, and verify artifact in storage where applicable
- [ ] **P1-X02** Document in repo which adapters are enabled in dev/staging and which have passed smoke (checklist or ADR snippet)
- [ ] **P1-X03** One developer can follow README: bring up stack, run API + worker, complete **P1-X01** flow without undocumented steps
