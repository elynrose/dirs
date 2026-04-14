"""FastMCP server: Chat Studio + agent run tools for OpenClaw."""

from __future__ import annotations

import json
import os
from typing import Any

from mcp.server.fastmcp import FastMCP

from director_mcp.api_client import DirectorApiClient, DirectorApiError

_WORKFLOW_MD = """# Directely documentary workflow (MCP)

Use these tools with the Directely API to shape a **unique documentary** via chat, then queue the full pipeline.

## 1) Iterate on the brief (Chat Studio)

Call **`documentary_chat_turn`** with a `messages` array (same as Studio Chat): alternating `user` / `assistant`, last message **must** be `user`. Optionally pass `current_brief` fields (`title`, `topic`, `target_runtime_minutes`, `frame_aspect_ratio` `16:9` or `9:16`, styles, etc.) and `project_id` if refining an existing project.

The API returns `reply` (assistant text) and `brief_patch` — merge patches client-side or send accumulated fields in `current_brief` on the next turn.

## 2) Queue autonomous generation

When the brief is ready, call **`documentary_queue_agent_run`** with a `brief` object:

- Required: `title`, `topic`, `target_runtime_minutes` (2–120), `frame_aspect_ratio` (`16:9` or `9:16`).
- Optional: `audience`, `tone`, `visual_style`, `narration_style`, `factual_strictness`, `music_preference`, provider hints, `research_min_sources`.

Optional `pipeline_options` (see API `AgentRunCreate`), e.g. `{ "through": "full_video", "unattended": true }`.

Returns `agent_run`, `project`, and `poll_url`.

## 3) Poll / stop

- **`documentary_get_agent_run`**: status, `current_step`, `error_message`.
- **`documentary_stop_agent_run`**: request stop at phase boundaries.

## Auth

Set env `DIRECTOR_API_BASE_URL` (e.g. `http://127.0.0.1:8000`). If the API uses SaaS auth: `DIRECTOR_API_TOKEN` (JWT) and `DIRECTOR_TENANT_ID` (workspace id).
"""


def _client() -> DirectorApiClient:
    return DirectorApiClient.from_env()


def _json_ok(payload: dict[str, Any]) -> str:
    return json.dumps(payload, indent=2, default=str)


def _err(e: Exception) -> str:
    if isinstance(e, DirectorApiError):
        return _json_ok({"ok": False, "error": str(e), "status_code": e.status_code, "body": e.body[:4000]})
    return _json_ok({"ok": False, "error": str(e)})


def build_mcp() -> FastMCP:
    instructions = (
        "Directely (Director) — create documentary projects via the same Chat Studio and agent-run APIs as the web app. "
        "Use documentary_chat_turn to refine the brief through conversation, then documentary_queue_agent_run to start the pipeline."
    )
    mcp = FastMCP("Directely", instructions=instructions)

    @mcp.tool()
    def documentary_chat_turn(
        messages: list[dict[str, Any]],
        current_brief: dict[str, Any] | None = None,
        project_id: str | None = None,
    ) -> str:
        """
        One turn of Chat Studio setup guide: sends user/assistant messages to the LLM and returns reply + brief_patch.

        Args:
            messages: List of {"role": "user"|"assistant", "content": "..."} (min 1; last must be user).
            current_brief: Optional partial brief fields to merge (title, topic, frame_aspect_ratio, etc.).
            project_id: Optional UUID string to load existing project fields from the server.
        """
        try:
            body: dict[str, Any] = {"messages": messages}
            if current_brief:
                body["current_brief"] = current_brief
            if project_id and str(project_id).strip():
                body["project_id"] = str(project_id).strip()
            data = _client().post_v1("/chat-studio/setup-guide", body)
            return _json_ok({"ok": True, **data})
        except Exception as exc:
            return _err(exc)

    @mcp.tool()
    def documentary_queue_agent_run(
        brief: dict[str, Any],
        pipeline_options: dict[str, Any] | None = None,
    ) -> str:
        """
        Create a project from the brief and enqueue a full agent run (same as Studio Automate).

        Args:
            brief: ProjectCreate fields — at least title, topic, target_runtime_minutes, frame_aspect_ratio.
            pipeline_options: Optional dict (through, unattended, continue_from_existing, etc.).
        """
        try:
            body: dict[str, Any] = {"brief": brief}
            if pipeline_options:
                body["pipeline_options"] = pipeline_options
            data = _client().post_v1("/agent-runs", body)
            return _json_ok({"ok": True, **data})
        except Exception as exc:
            return _err(exc)

    @mcp.tool()
    def documentary_get_agent_run(agent_run_id: str) -> str:
        """Fetch agent run status, steps, and errors (GET /v1/agent-runs/{id})."""
        try:
            rid = str(agent_run_id).strip()
            data = _client().get_v1(f"/agent-runs/{rid}")
            return _json_ok({"ok": True, **data})
        except Exception as exc:
            return _err(exc)

    @mcp.tool()
    def documentary_stop_agent_run(agent_run_id: str) -> str:
        """Request pipeline stop (honored at phase boundaries)."""
        try:
            rid = str(agent_run_id).strip()
            data = _client().post_v1(f"/agent-runs/{rid}/control", {"action": "stop"})
            return _json_ok({"ok": True, **data})
        except Exception as exc:
            return _err(exc)

    @mcp.resource("director://documentary-workflow")
    def documentary_workflow() -> str:
        return _WORKFLOW_MD

    return mcp


def run_stdio() -> None:
    """Entry point for OpenClaw / Cursor stdio MCP."""
    transport = (os.environ.get("DIRECTOR_MCP_TRANSPORT") or "stdio").strip().lower()
    mcp = build_mcp()
    if transport == "stdio":
        mcp.run(transport="stdio")
    else:
        mcp.run(transport=transport)
