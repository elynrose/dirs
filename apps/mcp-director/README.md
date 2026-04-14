# Directely MCP server (OpenClaw & MCP clients)

Model Context Protocol (MCP) bridge so **OpenClaw**, Claude Code, Cursor, or any MCP host can:

1. **Chat** with the same **Chat Studio setup guide** used in the web app (`documentary_chat_turn`).
2. **Queue** a **full documentary agent run** from a structured brief (`documentary_queue_agent_run`).
3. **Poll** or **stop** runs (`documentary_get_agent_run`, `documentary_stop_agent_run`).

The server talks to your existing **Directely API** over HTTP; it does not open database ports.

## Install

From this directory:

```bash
cd apps/mcp-director
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
```

Or with [uv](https://docs.astral.sh/uv/):

```bash
cd apps/mcp-director
uv sync
```

## Environment

| Variable | Description |
|----------|-------------|
| `DIRECTOR_API_BASE_URL` | API root without `/v1` (default `http://127.0.0.1:8000`) |
| `DIRECTOR_API_TOKEN` | JWT **Bearer** token when `DIRECTOR_AUTH_ENABLED` (same as Studio login) |
| `DIRECTOR_TENANT_ID` | Workspace id — required header when auth is enabled |
| `DIRECTOR_HTTP_TIMEOUT_SEC` | Optional HTTP timeout (default `120`) |

Legacy single-tenant: leave token and tenant unset if the API runs with auth disabled.

## Run (stdio — typical for OpenClaw)

```bash
export DIRECTOR_API_BASE_URL=http://127.0.0.1:8000
# If SaaS auth:
# export DIRECTOR_API_TOKEN=eyJ...
# export DIRECTOR_TENANT_ID=your-workspace-id

director-mcp
# or: python -m director_mcp
```

## OpenClaw

OpenClaw registers MCP servers as a command + stdio. Example (paths and env yours):

```bash
openclaw mcp add directely \
  --command python3 \
  --args -m,director_mcp \
  --cwd /absolute/path/to/director/apps/mcp-director \
  --env DIRECTOR_API_BASE_URL=https://api.example.com \
  --env DIRECTOR_API_TOKEN=YOUR_JWT \
  --env DIRECTOR_TENANT_ID=YOUR_TENANT_ID
```

If your OpenClaw build uses a JSON config instead, add a server block with the same `command`, `args`, `cwd`, and `env`.

## Cursor / Claude Desktop

Add an MCP server entry pointing at:

- **Command:** `python3` (or the venv’s `python`)
- **Args:** `-m`, `director_mcp`
- **Cwd:** `.../apps/mcp-director`
- **Env:** as above

## Tools

| Tool | Purpose |
|------|--------|
| `documentary_chat_turn` | POST `/v1/chat-studio/setup-guide` — conversational brief shaping |
| `documentary_queue_agent_run` | POST `/v1/agent-runs` — start pipeline from `brief` + optional `pipeline_options` |
| `documentary_get_agent_run` | GET `/v1/agent-runs/{id}` |
| `documentary_stop_agent_run` | POST `/v1/agent-runs/{id}/control` with `action: stop` |

## Resource

- `director://documentary-workflow` — short workflow markdown for the model.

## Security

Treat `DIRECTOR_API_TOKEN` like a password. Prefer a machine-local OpenClaw agent and HTTPS for remote APIs.
