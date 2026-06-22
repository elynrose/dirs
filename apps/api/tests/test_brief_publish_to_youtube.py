from director_api.api.schemas.project import ProjectCreate
from director_api.validation.brief import validate_documentary_brief


def test_project_create_brief_dict_excludes_publish_to_youtube_for_schema() -> None:
    b = ProjectCreate(
        title="Victorian Britain",
        topic="Queen Victoria documentary",
        target_runtime_minutes=12,
        publish_to_youtube=True,
    )
    d = b.brief_dict()
    assert "publish_to_youtube" not in d
    validate_documentary_brief(d)
    assert b.publish_to_youtube is True
