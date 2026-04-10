from director_api.services import pipeline_oversight as po


def test_parse_force_pipeline_steps_empty() -> None:
    assert po.parse_force_pipeline_steps(None) == frozenset()
    assert po.parse_force_pipeline_steps({}) == frozenset()
    assert po.parse_force_pipeline_steps({"force_pipeline_steps": "nope"}) == frozenset()


def test_parse_force_pipeline_steps_canonical() -> None:
    raw = {"force_pipeline_steps": ["director", "AUTO_IMAGES", "narration", "unknown"]}
    assert po.parse_force_pipeline_steps(raw) == frozenset({"director", "auto_images", "auto_narration"})


def test_parse_force_pipeline_steps_character_aliases() -> None:
    raw = {
        "force_pipeline_steps": [
            "CHARACTERS",
            "character_bible",
            "auto_characters",
            "AUTO_IMAGES",
        ]
    }
    assert po.parse_force_pipeline_steps(raw) == frozenset({"auto_characters", "auto_images"})


def test_effective_resume_skip_with_force_overrides_continue() -> None:
    # Would skip on continue, but director is forced → do not skip
    assert (
        po.effective_resume_skip_with_force(
            True,
            None,
            "director",
            True,
            frozenset({"director"}),
        )
        is False
    )


def test_tail_should_run_with_force() -> None:
    assert po.tail_should_run_with_force("auto_narration", "auto_narration", frozenset()) is True
    assert po.tail_should_run_with_force("auto_images", "auto_narration", frozenset()) is False
    assert po.tail_should_run_with_force("auto_images", "auto_narration", frozenset({"auto_images"})) is True
    assert po.tail_should_run_with_force("auto_characters", "auto_images", frozenset()) is False
    assert po.tail_should_run_with_force("auto_characters", "auto_images", frozenset({"auto_characters"})) is True
