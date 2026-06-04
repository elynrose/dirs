"""Agent-run control flow exceptions (pause / stop / policy block)."""

from __future__ import annotations

from typing import Any


class AgentRunBlocked(Exception):
    def __init__(self, code: str, message: str, detail: dict[str, Any] | None = None) -> None:
        self.code = code
        self.message = message
        self.detail = detail if isinstance(detail, dict) else {}
        super().__init__(message)


class AgentRunPausedYield(Exception):
    """Raised when an agent run is paused so the Celery task can exit and re-queue."""


class AgentRunStopRequested(Exception):
    """Raised when checkpoint sees stop_requested on the agent run."""
