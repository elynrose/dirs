from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch
from uuid import uuid4

from director_api.config import Settings
from director_api.services.telegram_notify import (
    telegram_notify_after_agent_run,
    telegram_notify_phase_complete,
)


def test_telegram_notify_phase_complete_sends_when_enabled():
    settings = Settings()
    settings = settings.model_copy(
        update={
            "telegram_bot_token": "tok",
            "telegram_chat_id": "123",
            "telegram_notify_phase_completions": True,
        }
    )
    with patch("director_api.services.telegram_notify.telegram_send_message") as send:
        telegram_notify_phase_complete(
            settings,
            project_title="My Doc",
            agent_run_id="run-1",
            step="auto_images",
            generated=5,
        )
    send.assert_called_once()
    text = send.call_args[0][2]
    assert "Scene images" in text
    assert "My Doc" in text


def test_telegram_notify_phase_complete_skipped_when_disabled():
    settings = Settings()
    settings = settings.model_copy(
        update={
            "telegram_bot_token": "tok",
            "telegram_chat_id": "123",
            "telegram_notify_phase_completions": False,
        }
    )
    with patch("director_api.services.telegram_notify.telegram_send_message") as send:
        telegram_notify_phase_complete(
            settings,
            project_title="My Doc",
            agent_run_id="run-1",
            step="auto_images",
        )
    send.assert_not_called()


def test_telegram_notify_after_failed_run_uses_friendly_summary():
    run_id = uuid4()
    project_id = uuid4()
    run = MagicMock()
    run.id = run_id
    run.project_id = project_id
    run.tenant_id = "00000000-0000-0000-0000-000000000001"
    run.started_by_user_id = None
    run.status = "failed"
    run.error_message = "AUTO_TIMELINE_NO_VISUALS_AT_ALL: abc-def"
    run.completed_at = datetime.now(timezone.utc) - timedelta(seconds=5)
    run.pipeline_control_json = {}
    project = MagicMock()
    project.title = "Test Project"

    settings = Settings()
    settings = settings.model_copy(
        update={
            "telegram_bot_token": "tok",
            "telegram_chat_id": "123",
            "telegram_notify_pipeline_failures": True,
        }
    )

    with (
        patch("director_api.services.telegram_notify.SessionLocal") as session_cls,
        patch("director_api.services.telegram_notify.get_settings", return_value=Settings()),
        patch(
            "director_api.services.telegram_notify.resolve_runtime_settings",
            return_value=settings,
        ),
        patch("director_api.services.telegram_notify.telegram_send_message") as send,
    ):
        db = MagicMock()
        session_cls.return_value.__enter__.return_value = db

        def _get(model, pk):
            if str(pk) == str(run_id):
                return run
            if str(pk) == str(project_id):
                return project
            return None

        db.get.side_effect = _get

        telegram_notify_after_agent_run(str(run_id))

    send.assert_called_once()
    body = send.call_args[0][2]
    assert "ComfyUI" in body or "image or video" in body
    assert "AUTO_TIMELINE_NO_VISUALS_AT_ALL" not in body


def test_telegram_notify_phase_complete_includes_partial_failed_summary():
    settings = Settings()
    settings = settings.model_copy(
        update={
            "telegram_bot_token": "tok",
            "telegram_chat_id": "123",
            "telegram_notify_phase_completions": True,
        }
    )
    with patch("director_api.services.telegram_notify.telegram_send_message") as send:
        telegram_notify_phase_complete(
            settings,
            project_title="Doc",
            agent_run_id="run-2",
            step="auto_videos",
            generated=0,
            failure_reason_summary="No scene videos were generated.",
        )
    send.assert_called_once()
    body = send.call_args[0][2]
    assert "No scene videos" in body
