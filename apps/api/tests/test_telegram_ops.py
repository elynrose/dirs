"""Tests for Telegram ops command routing (no live Telegram API)."""

from __future__ import annotations

from director_api.services.telegram_ops import classify_ops_intent


def test_classify_ops_slash_commands() -> None:
    assert classify_ops_intent("/status") == ("status", "")
    assert classify_ops_intent("/stop") == ("stop", "")
    assert classify_ops_intent("/pause") == ("pause", "")
    assert classify_ops_intent("/resume") == ("resume", "")
    assert classify_ops_intent("/projects") == ("projects", "")
    assert classify_ops_intent("/scenes") == ("scenes", "")
    assert classify_ops_intent("/rough") == ("rough", "")
    assert classify_ops_intent("/use cfb6f288") == ("use", "cfb6f288")
    assert classify_ops_intent("/retry") == ("retry", "")


def test_classify_ops_natural_language() -> None:
    assert classify_ops_intent("status") == ("status", "")
    assert classify_ops_intent("where are we?") == ("status", "")
    assert classify_ops_intent("stop pipeline") == ("stop", "")
    assert classify_ops_intent("list projects") == ("projects", "")
    assert classify_ops_intent("retry run") == ("retry", "")


def test_classify_ops_not_setup_chat() -> None:
    assert classify_ops_intent("10-minute film about bees") is None
    assert classify_ops_intent("RUN") is None
    assert classify_ops_intent("16:9") is None
