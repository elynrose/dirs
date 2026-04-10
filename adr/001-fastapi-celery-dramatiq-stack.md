# ADR 001: FastAPI + Redis queue workers for orchestration

- **Status:** accepted
- **Date:** 2025-03-22
- **Deciders:** product + engineering (initial spec)

## Context

The platform needs long-running agent and media jobs, provider adapters, and a unified API. `project.md` §11 recommends Python FastAPI, Redis, and Celery or Dramatiq.

## Decision

Use **Python** (**3.11+**) with **FastAPI** for the HTTP API and **Redis-backed** task queues with **Celery** or **Dramatiq** workers (pick one per deployment; document in repo root README when code exists). Optional **Temporal** remains an additive option for durable workflows later. This is the **canonical implementation language** for orchestration and workers per **`project.md` §4.8**.

## Consequences

- **Positive:** Aligns with spec; large ecosystem for OpenAI-style clients; Python fits ML/media tooling and FFmpeg subprocess control.
- **Negative:** Operational complexity of workers and queue monitoring; must implement idempotency and stale-job handling explicitly (§10.6).

## Alternatives considered

- **Node-only** — Rejected for primary orchestration: weaker ecosystem for heavy media subprocess control vs Python in this stack.
- **Temporal-first** — Deferred: higher learning curve; can wrap later without changing adapter interfaces.

## Links

- [`project.md`](../project.md) §11.2
- [`docs/api-spec.md`](../docs/api-spec.md)
