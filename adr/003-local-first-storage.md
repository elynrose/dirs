# ADR 003: Local-first storage and optional cloud profile

- **Status:** accepted
- **Date:** 2025-03-22
- **Deciders:** product (spec update)

## Context

Early drafts assumed generic “object storage,” which reads as cloud S3. Operators may run the studio entirely on a workstation with large local disks and no remote asset bucket.

## Decision

Adopt **local-first** defaults per **`project.md` §4.7**: **filesystem-backed `AssetStorage`** under `LOCAL_STORAGE_ROOT`, **SQLite** allowed for single-user metadata, **PostgreSQL + optional MinIO** on localhost for heavier or S3-shaped workflows. **Remote S3** is a **deployment profile**, not a requirement to ship core features.

## Consequences

- **Positive:** Simpler onboarding; works offline for non-provider steps; predictable costs; easy backup via directory copy.
- **Negative:** SaaS operators must explicitly configure remote storage and backups; horizontal multi-node media workers need shared filesystem or object store.

## Alternatives considered

- **Cloud S3 only** — Rejected as default; conflicts with local-first goal.
- **Embedded object DB** — Deferred; filesystem + SQLite/Postgres is enough for v1.

## Links

- [`project.md`](../project.md) §4.7, §11.2
- [`docs/local-first-storage.md`](../docs/local-first-storage.md)
