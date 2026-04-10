"""Local-first filesystem asset storage (`project.md` §4.7)."""

from pathlib import Path

from director_api.config import get_settings


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
