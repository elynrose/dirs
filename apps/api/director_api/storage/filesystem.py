"""Local-first filesystem asset storage (`project.md` §4.7)."""

from pathlib import Path

from director_api.config import get_settings


def _legacy_and_tenant_scoped_keys(key: str, tenant_id: str | None) -> list[str]:
    """Return storage keys to try: primary first, then tenant-scoped / legacy alternates."""
    safe = key.lstrip("/").replace("..", "")
    if not safe.startswith("assets/") or not tenant_id:
        return [safe]
    parts = safe.split("/")
    keys = [safe]
    # legacy assets/<project>/… → tenant assets/<tenant>/<project>/…
    if len(parts) >= 3 and parts[1] != tenant_id:
        keys.append(f"assets/{tenant_id}/{'/'.join(parts[1:])}")
    # tenant-scoped → legacy
    elif len(parts) >= 4 and parts[1] == tenant_id:
        keys.append(f"assets/{'/'.join(parts[2:])}")
    return keys


def resolve_storage_path(
    storage: "FilesystemStorage",
    key: str,
    *,
    tenant_id: str | None = None,
) -> Path | None:
    """Return first existing on-disk path for ``key``, dual-reading legacy vs tenant-scoped layouts."""
    for candidate in _legacy_and_tenant_scoped_keys(key, tenant_id):
        path = storage.get_path(candidate)
        if path.is_file():
            return path
    return None


class FilesystemStorage:
    def __init__(self, root: str | None = None) -> None:
        self.root = Path(root or get_settings().local_storage_root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, key: str) -> Path:
        safe = key.lstrip("/").replace("..", "")
        path = self.root / safe
        path.parent.mkdir(parents=True, exist_ok=True)
        return path

    def put_bytes(self, key: str, data: bytes, *, content_type: str | None = None) -> str:
        path = self._path(key)
        path.write_bytes(data)
        _ = content_type
        # RFC 8089 file URI (forward slashes). Raw f"file://{path}" breaks urlparse on Windows
        # (drive ends up in netloc, path empty) and breaks GET /v1/assets/{id}/content resolution.
        return path.resolve().as_uri()

    def get_path(self, key: str) -> Path:
        return self._path(key)
