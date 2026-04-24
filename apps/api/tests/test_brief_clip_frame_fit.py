from director_api.validation.brief import validate_documentary_brief


def test_documentary_brief_accepts_clip_frame_fit_letterbox() -> None:
    validate_documentary_brief(
        {
            "title": "T",
            "topic": "x" * 20,
            "target_runtime_minutes": 10,
            "clip_frame_fit": "letterbox",
        }
    )


def test_documentary_brief_rejects_invalid_clip_frame_fit() -> None:
    import jsonschema

    try:
        validate_documentary_brief(
            {
                "title": "T",
                "topic": "x" * 20,
                "target_runtime_minutes": 10,
                "clip_frame_fit": "panorama",
            }
        )
    except jsonschema.ValidationError:
        return
    raise AssertionError("expected ValidationError")
