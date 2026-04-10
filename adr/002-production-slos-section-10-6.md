# ADR 002: Production SLOs and capacity targets (`project.md` §10.6)

- **Status:** accepted
- **Date:** 2025-03-22
- **Deciders:** product + engineering

## Context

Phase 6 exit criteria needed measurable thresholds for availability, latency, concurrency, rate limits, and job failure rates.

## Decision

Adopt **§10.6** in [`project.md`](../project.md) as the **initial GA targets**: e.g. **99.5%** monthly API availability, read p95 **< 800 ms**, enqueue p95 **< 3 s**, job pickup p95 **< 60 s**, per-tenant and global concurrency caps, rolling **7-day** failure-rate caps, and backup **RPO/RTO** with object-storage recoverability window.

## Consequences

- **Positive:** Phase 6 checklist and dashboards have explicit numbers; load tests can be scored pass/fail.
- **Negative:** Numbers may be wrong for first hardware; changes require PR updating both `project.md` §10.6 and [`phases/phase-06-hardening.md`](../phases/phase-06-hardening.md).

## Alternatives considered

- **TBD at GA** — Rejected: blocked operational clarity.
- **Stricter 99.9%** — Deferred until production traffic justifies higher ops cost.

## Links

- [`project.md`](../project.md) §10.6
- [`phases/phase-06-hardening.md`](../phases/phase-06-hardening.md)
- [`docs/phase-6-telemetry-fields.md`](../docs/phase-6-telemetry-fields.md)
