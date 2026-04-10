from pathlib import Path

from director_api.services.chatterbox_voice_ref import (
    safe_tenant_slug,
    voice_ref_absolute_path,
    voice_ref_storage_key,
)


def test_voice_ref_storage_key_stable():
    assert voice_ref_storage_key("00000000-0000-0000-0000-000000000001") == (
        "voice_refs/00000000-0000-0000-0000-000000000001/reference.wav"
    )


def test_safe_tenant_slug_sanitizes():
    assert safe_tenant_slug("a/b:c") == "a_b_c"


def test_voice_ref_absolute_path(tmp_path):
    root = tmp_path / "storage"
    root.mkdir()
    p = voice_ref_absolute_path(storage_root=root, tenant_id="t1")
    assert p == (root / "voice_refs" / "t1" / "reference.wav").resolve()
