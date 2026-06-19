from director_api.services.agent_run_failure_copy import summarize_agent_run_failure


def test_summarize_no_visuals_at_all():
    raw = "AUTO_TIMELINE_NO_VISUALS_AT_ALL: " + ",".join(["159fe15e-cac8-4c17-bca4-3828bba036ce"] * 10)
    msg = summarize_agent_run_failure(raw)
    assert "ComfyUI" in msg or "image or video" in msg
    assert "159fe15e" not in msg


def test_summarize_connection_refused():
    msg = summarize_agent_run_failure("request_failed: [WinError 10061] No connection could be made")
    assert "could not be reached" in msg.lower() or "connection refused" in msg.lower()


def test_summarize_missing_visual_scene():
    msg = summarize_agent_run_failure("AUTO_TIMELINE_MISSING_VISUAL_abc-123")
    assert "at least one scene" in msg.lower()
