# services/

Long-running or independently deployable workers (Phase 1+). Names align with `project.md` §11.3.

Planned directories (scaffold as code lands):

- `orchestrator/`, `research/`, `script/`, `scene-planner/`, `media-generation/`, `critic/`, `narration/`, `editor/`, `compiler/`, `usage-metering/`

Until split out, a single **`apps/api`** + **`worker`** process is acceptable if boundaries match the spec interfaces.
