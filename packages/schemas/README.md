# packages/schemas

Single source of truth for **JSON Schema** (and future codegen to TypeScript / Pydantic).

## Layout

```
json/           # Schema files ($id URIs inside each file)
fixtures/       # Golden JSON for CI validation
```

## Validation in CI

```bash
# When Node is available (install ajv-cli in CI):
# npx ajv-cli validate -s json/documentary-brief.schema.json -d fixtures/documentary-brief-valid.json
```

`fixtures/golden-project-minimal.json` is a **nested** golden project for E2E narratives; validate its `project` object against `documentary-brief.schema.json` in application tests as needed.

**Phase 5:** `json/timeline-version.schema.json` — `TimelineVersion.timeline_json` contract (v1: ordered **clips** with `source.kind=asset`).

**FFmpeg helpers:** sibling package [`../ffmpeg-pipelines`](../ffmpeg-pipelines) (installed as a dependency of `apps/api`).

## Conventions

- **`$id`:** `https://director.local/schemas/<name>/<version>`
- **Breaking changes:** bump version in path or filename; support N-1 per [versioning-policy.md](../../docs/versioning-policy.md)

## Related

- [`docs/contract-testing.md`](../../docs/contract-testing.md)
- [`docs/phase-6-telemetry-fields.md`](../../docs/phase-6-telemetry-fields.md)
