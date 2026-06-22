"""Tests for Telegram ops action dispatch (no live Telegram / LLM)."""

from __future__ import annotations

import uuid
from unittest.mock import MagicMock, patch

from director_api.services.telegram_ops_actions import execute_telegram_action


def test_execute_unknown_action_returns_message() -> None:
    row = MagicMock()
    out = execute_telegram_action(
        MagicMock(),
        MagicMock(),
        tenant_id="t1",
        row=row,
        action="not_real",
        args={},
    )
    assert out is not None
    assert "Unknown action" in out


def test_execute_none_returns_none() -> None:
    out = execute_telegram_action(
        MagicMock(),
        MagicMock(),
        tenant_id="t1",
        row=MagicMock(),
        action="none",
        args={},
    )
    assert out is None


def test_execute_list_projects_empty() -> None:
    db = MagicMock()
    db.scalars.return_value.all.return_value = []
    out = execute_telegram_action(
        db,
        MagicMock(),
        tenant_id="t1",
        row=MagicMock(),
        action="list_projects",
        args={},
    )
    assert out is not None
    assert "No projects yet" in out
