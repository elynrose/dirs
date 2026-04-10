# Build phases (trackable)

This folder splits **§8 Phase-based build plan** and **§20 Phase exit criteria** from [`../project.md`](../project.md). Each phase file includes **Notes (review)** with strengths, gaps addressed, and residual risks.

**Related spec:** [`../project.md`](../project.md) · **Docs hub:** [`../docs/README.md`](../docs/README.md) · **SLOs:** §10.6 · **ADRs:** [`../adr/README.md`](../adr/README.md)

**MVP rollup (2026-03):** All phase files **P1–P6** are set to **done** or **completed** for the **shipped codebase**. Remaining items in each file are **documented gaps** toward full GA (IdP, observability stack, load tests, real video/TTS, backup drills, etc.)—see **Notes** / unchecked **P*n*-D*** in phase-05 and phase-06 especially.

## How to track progress

1. Open the phase file (e.g. [`phase-01-foundation.md`](phase-01-foundation.md)).
2. Set **`status`** in the YAML frontmatter: `not_started` → `in_progress` → `done` (use `blocked` if waiting on dependencies).
3. Check off items: `- [ ]` → `- [x]` as work completes. IDs are stable (**P1-D01**, **P1-R01**, …) for commits, issues, and standups.
4. When **Exit criteria** for a phase is satisfied, check every **P*n*-X*** box and set `status` to `done`.
5. Use **`progress_percent`** as a coarse manual rollup (optional), or derive from checked deliverables.

## Phase index (with `project.md` §8 / §20 anchors)

| Phase | File | `project.md` §8 | §20 exit |
| ----- | ---- | --------------- | -------- |
| 1 | [phase-01-foundation.md](phase-01-foundation.md) | Phase 1 — Foundation | Phase 1 exit |
| 2 | [phase-02-research-writing.md](phase-02-research-writing.md) | Phase 2 — Research and writing | Phase 2 exit |
| 3 | [phase-03-scenes-media.md](phase-03-scenes-media.md) | Phase 3 — Scenes and media | Phase 3 exit |
| 4 | [phase-04-critique-continuity.md](phase-04-critique-continuity.md) | Phase 4 — Critique | Phase 4 exit |
| 5 | [phase-05-edit-compile.md](phase-05-edit-compile.md) | Phase 5 — Edit and compile | Phase 5 exit |
| 6 | [phase-06-hardening.md](phase-06-hardening.md) | Phase 6 — Hardening + **§10.6** | Phase 6 exit |

## Dependency matrix (strict path)

```mermaid
flowchart LR
  P1[Phase 1] --> P2[Phase 2]
  P2 --> P3[Phase 3]
  P3 --> P4[Phase 4]
  P4 --> P5[Phase 5]
  P5 --> P6[Phase 6]
```

| From | To | Notes |
| ---- | -- | ----- |
| P2 | P1 | Needs API, queue, schemas, adapters |
| P3 | P2 | Needs script + chapters |
| P4 | P3 | Needs scenes and assets to critique |
| P5 | P4 | **Strict:** critic gates; **slice:** see vertical-slice note in P5 / P4 files |
| P6 | P5 | Needs export path for load tests |

## Stable ID policy

- **Never renumber** existing **P*n*-** IDs; if a deliverable splits, **retire** the old ID in place with strikethrough in prose or keep one checkbox as parent and add **new higher numbers** only (e.g. append **P1-D20**).
- New work **appends** new IDs at the end of a section.
- Cross-reference **GitHub issues** as `Refs #123` in commits, not by reusing IDs.

## ID prefix legend

| Prefix | Meaning |
| ------ | ------- |
| **P*n*-D** | Deliverable |
| **P*n*-R** | Requirement |
| **P*n*-O** | Out of scope (informational; optional check when explicitly deferred) |
| **P*n*-M** | Success metric (phase goal) |
| **P*n*-MV** | MVP vertical-slice / integration checkpoint |
| **P*n*-X** | Exit criterion (gate to next phase) |

## MVP overlap

MVP (`project.md` §19) is mostly satisfied at **Phase 5** exit (10–15 minute export). **P5-MV*** items are an end-to-end punch list. Phase 2 success metric still targets **full vision** script depth; interim sign-off may use a **shorter MVP script** if documented in **Notes**.

## Dependency discipline

`depends_on` in frontmatter is the **strict** path. Some phases note an optional **vertical slice** (e.g. skipping strict P4 for an internal demo); that path should be explicit, time-boxed, and not mistaken for production exit.
