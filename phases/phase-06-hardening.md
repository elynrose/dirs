---
phase: 6
slug: hardening
title: "Production hardening"
status: done
progress_percent: 55
updated: 2026-03-22
depends_on: ["phase-05-edit-compile"]
source: "../project.md §8 Phase 6, §10 (incl. §10.6), §20 Phase 6 exit, §21.7–§21.12; adr/002-production-slos-section-10-6.md; adr/003-local-first-storage.md"
---

# Phase 6 — Production hardening

**Goal:** Make the system commercially deployable.

**Spec links:** [`project.md`](../project.md) §8 Phase 6, **§10.6**, §20, **§21** (ops docs) · [`adr/002-production-slos-section-10-6.md`](../adr/002-production-slos-section-10-6.md) · [`adr/003-local-first-storage.md`](../adr/003-local-first-storage.md) · [`docs/data-retention.md`](../docs/data-retention.md) · [`docs/threat-model.md`](../docs/threat-model.md) · [`docs/runbooks/`](../docs/runbooks/) · [`docs/phase-6-telemetry-fields.md`](../docs/phase-6-telemetry-fields.md)

## Foundation slice shipped (2026-03)

**In code:** **`audit_events`** table + **`GET /v1/projects/{id}/audit-events`** (project/chapter/scene scope); audit rows on **critic waivers**. **Job concurrency caps** per §10.6 defaults (**media 3**, **compile 2**, **text 5**, **global media 20**) enforced at enqueue time. **Sliding-window rate limit** (per client IP + tenant bucket; default and env **`API_RATE_LIMIT_PER_MINUTE`** in `config.py`) on `/v1/*` except health/ready/metrics/docs. **`GET /v1/metrics`** — job counts + cap config. **`director.reap_stale_jobs`** Celery task + **beat** schedule every **15 min** (requires `celery -A director_api.tasks.celery_app beat`). Env: **`JOB_CAP_*`**, **`STALE_JOB_MINUTES`**, **`API_RATE_LIMIT_PER_MINUTE`**, **`RATE_LIMIT_ENABLED`**.

**Still for true GA:** managed **IdP/RBAC**, **Prometheus/Grafana**, **K6/Locust** artifacts, **S3/versioned** backup drills, **admin dashboard**, **provider budget** automation, **video E2E** with real encoder (**P6-D16**), rolling **failure-rate** reporting. Those are **tracked below** as unchecked; this phase is marked **done** for **MVP engineering foundation** with explicit **follow-up** list.

## §10.6 summary (keep in sync with `project.md`)

| Area | Initial GA target |
| ---- | ----------------- |
| API availability | **≥ 99.5%** / month (excl. documented maintenance) |
| Read API p95 | **< 800 ms** (`GET` project/chapter/scene) |
| Enqueue mutation p95 | **< 3 s** (`POST` research, generate scene, enqueue media) |
| LLM-blocking HTTP | **≤ 60 s** or **202 + job id** |
| Job pickup p95 | **< 60 s** (nominal queue depth) |
| Stale job handling | **45 min** no heartbeat/progress → fail or requeue + alert |
| E2E concurrency (staging) | **≥ 2** concurrent productions |
| Per-tenant caps | **3** media, **2** compile, **5** text jobs |
| Global media cap | **20** concurrent media jobs |
| API rate | **120** req/min/tenant, burst **30** |
| Compile/export rate | **6** starts/tenant/hour (baseline) |
| 7-day job failure rate | **≤ 8%** after retries (excl. provider outages); **≤ 15%** incl. exhausted retries → review |
| DB backup | **RPO ≤ 24 h**, **RTO ≤ 4 h** |
| Object storage | recoverability **≥ 30 days** after delete (bucket policy) |

## Deliverables

### Tenancy, security, compliance

- [ ] **P6-D01** Multi-tenant **authentication** (IdP or managed auth) and **authorization** (RBAC or scoped tokens)
- [ ] **P6-D02** Tenant isolation tests: automated checks that cross-tenant resource access is denied
- [x] **P6-D03** Audit logs for sensitive actions (script approve, waiver, export, provider key usage pattern if applicable) — _`audit_events` + waiver writes_

### Cost and product ops

- [ ] **P6-D04** Usage accounting UI or exports: per tenant / project / provider (**UsageRecord** surfaced)
- [ ] **P6-D05** Provider cost controls: project budgets, caps, **automatic downgrade** for previews per `project.md` §17
- [ ] **P6-D06** Admin dashboard: queue depth, failure rate, stuck jobs, cost estimates, tenant list, **SLO panels** (availability, p95, pickup, failure %)

### Reliability and operations

- [ ] **P6-D07** Observability: metrics (Prometheus or cloud equivalent), log aggregation, trace dashboards
- [ ] **P6-D08** **Resumable renders** and durable workflow recovery (retry compile from last good **TimelineVersion**)
- [ ] **P6-D09** Backup and restore **runbook** executed once on staging: Postgres + critical object storage prefixes + manifest recovery path (**§10.6:** RPO ≤ 24 h, RTO ≤ 4 h)
- [~] **P6-D10** **Rate limits** implemented per §10.6: **120** req/min/tenant (burst **30**); **6** compile/export starts/tenant/hour (admin override documented) — _per-IP minute window + job caps; **hourly compile** limit not coded_
- [ ] **P6-D11** Evaluation suite: golden projects (research/script/scene/critic) with **thresholds**; runs in CI or scheduled job
- [ ] **P6-D12** Load test: documented scenario (**K6** / **Locust**) proving **≥ 2** concurrent end-to-end productions on **reference staging hardware**, with concurrent media jobs within **§10.6** caps—**results archived** with definition of “nominal load” for pickup SLO
- [x] **P6-D13** **Concurrency caps** per §10.6: per tenant **3** media, **2** compile, **5** text; global **20** media jobs
- [~] **P6-D14** **Queue behavior:** monitor job pickup p95 vs **< 60 s** target; **45 min** stale-task policy (configurable) with alerts — _**reap** task; no alert integration_
- [ ] **P6-D15** **API SLO instrumentation:** measure monthly availability **≥ 99.5%**, read p95 **< 800 ms**, enqueue mutation p95 **< 3 s**; enforce LLM path **60 s** or **202 + job id**
- [~] **P6-D16** **Video generation E2E:** Local **FFmpeg** path is wired (`local_ffmpeg` still→MP4). **Manual / staging checklist:** `generate-image` → approve image → `generate-video` → `GET …/phase3-summary` shows `linked_video_count` / succeeded video asset → approve video → `approved_video_count` ≥ 1; poll job + optional webhook. **Pre-GA gate** until run on staging with real data unless waived in an ADR.

## Requirements

- [~] **P6-R01** Production SLOs match **`project.md` §10.6** unless a written **ADR** documents intentional deviation and owner sign-off — _subset implemented in app; full SLO measurement external_
- [ ] **P6-R02** Model and provider **fallback** policies enforced in router (not only documented)
- [ ] **P6-R03** Queue **prioritization** policy implemented (tier, fairness, or SLA-based)
- [ ] **P6-R04** Secrets rotation procedure documented; no long-lived keys in application logs
- [ ] **P6-R05** Incident response: on-call or escalation path + runbook link in README
- [ ] **P6-R06** Rolling **7-day** job failure rates reported vs §10.6 thresholds (**≤ 8%** / **≤ 15%**); breach triggers review workflow

## Success metric

- [ ] **P6-M01** **P6-D12** load-test artifact shows **≥ 2** concurrent E2E productions, respects **§10.6** caps, and **7-day** failure rates stay within **§10.6** under the test window (or document exception with owner)

## Exit criteria

- [~] **P6-X01** **≥ 2** concurrent end-to-end productions on staging without manual infra intervention; **cost** and **failure** visible on dashboards — _concurrency caps support; **dashboards** external_
- [ ] **P6-X02** Within a defined measurement window, **§10.6** **availability** and **p95 latency** targets met (or ADR waiver); **job pickup** and **stale-job** behavior operational
- [ ] **P6-X03** Rolling **7-day** job failure rates within **§10.6** caps (or review opened with mitigation owner)
- [ ] **P6-X04** **P6-D09** restore drill completed: **RPO ≤ 24 h**, **RTO ≤ 4 h** validated; object storage **≥ 30-day** recoverability policy in place per §10.6
- [ ] **P6-X05** Evaluation suite **green** on main (or explicitly waived failures tracked as blockers with owners)
