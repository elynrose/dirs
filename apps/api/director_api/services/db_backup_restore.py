"""PostgreSQL backup/restore helpers for platform admin (pg_dump / psql)."""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from collections.abc import Iterator
from pathlib import Path
from urllib.parse import unquote_plus

import structlog
from sqlalchemy.engine.url import make_url

log = structlog.get_logger(__name__)


def _normalize_database_url(raw: str) -> str:
    s = (raw or "").strip()
    if not s:
        return ""
    # SQLAlchemy accepts postgresql+psycopg:// — make_url handles it for host/user/password/database.
    return s


def pg_dump_on_path() -> str | None:
    return shutil.which("pg_dump")


def psql_on_path() -> str | None:
    return shutil.which("psql")


def _pg_conn_parts(database_url: str) -> tuple[str, int, str, str, str]:
    """Return host, port, username, password, database_name."""
    url = _normalize_database_url(database_url)
    u = make_url(url)
    host = u.host or "127.0.0.1"
    port = int(u.port or 5432)
    user = u.username or "postgres"
    password = unquote_plus(u.password) if u.password else ""
    db = u.database or "postgres"
    return host, port, user, password, db


def run_pg_dump_to_tempfile(database_url: str, *, timeout_sec: float = 7200.0) -> Path:
    """Run pg_dump plain SQL to a temp file; returns path (caller must delete after streaming)."""
    exe = pg_dump_on_path()
    if not exe:
        raise RuntimeError("pg_dump not found on PATH")

    host, port, user, password, db = _pg_conn_parts(database_url)
    fd, path = tempfile.mkstemp(prefix="director_dump_", suffix=".sql")
    os.close(fd)
    out = Path(path)
    cmd = [
        exe,
        "-h",
        host,
        "-p",
        str(port),
        "-U",
        user,
        "-d",
        db,
        "--format=plain",
        "--no-owner",
        "--no-acl",
        "-f",
        str(out.resolve()),
    ]
    env = os.environ.copy()
    if password:
        env["PGPASSWORD"] = password
    try:
        proc = subprocess.run(cmd, env=env, capture_output=True, text=True, timeout=timeout_sec, check=False)
        if proc.returncode != 0:
            tail = (proc.stderr or proc.stdout or "")[-12000:]
            log.error("pg_dump_failed", returncode=proc.returncode, tail=tail)
            raise RuntimeError(f"pg_dump exited {proc.returncode}: {tail.strip() or 'no stderr'}")
        if not out.is_file() or out.stat().st_size < 1:
            raise RuntimeError("pg_dump produced an empty file")
        return out
    except Exception:
        out.unlink(missing_ok=True)
        raise


def iter_file_chunks_then_delete(path: Path, *, chunk_size: int = 1024 * 1024) -> Iterator[bytes]:
    """Stream a file in chunks and remove it when done (or on error)."""
    try:
        with path.open("rb") as f:
            while True:
                chunk = f.read(chunk_size)
                if not chunk:
                    break
                yield chunk
    finally:
        path.unlink(missing_ok=True)


def database_name_from_url(database_url: str) -> str:
    return _pg_conn_parts(database_url)[4]


def backup_filename_stem(database_name: str) -> str:
    safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in database_name)[:64] or "db"
    from datetime import datetime, timezone

    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"director_pg_{safe}_{ts}"


def run_psql_restore_file(database_url: str, sql_path: Path, *, timeout_sec: float = 7200.0) -> None:
    """Apply a plain-SQL dump with psql -v ON_ERROR_STOP=1 -f."""
    exe = psql_on_path()
    if not exe:
        raise RuntimeError("psql not found on PATH")

    host, port, user, password, db = _pg_conn_parts(database_url)
    cmd = [
        exe,
        "-h",
        host,
        "-p",
        str(port),
        "-U",
        user,
        "-d",
        db,
        "-v",
        "ON_ERROR_STOP=1",
        "-f",
        str(sql_path.resolve()),
    ]
    env = os.environ.copy()
    if password:
        env["PGPASSWORD"] = password

    proc = subprocess.run(
        cmd,
        env=env,
        capture_output=True,
        text=True,
        timeout=timeout_sec,
        check=False,
    )
    if proc.returncode != 0:
        tail = (proc.stderr or proc.stdout or "")[-12000:]
        log.error("psql_restore_failed", returncode=proc.returncode, tail=tail)
        raise RuntimeError(f"psql exited {proc.returncode}: {tail.strip() or 'no output'}")


def write_upload_to_temp(upload_bytes: bytes, *, max_bytes: int) -> Path:
    if len(upload_bytes) > max_bytes:
        raise ValueError(f"upload exceeds max size ({max_bytes} bytes)")
    fd, path = tempfile.mkstemp(prefix="director_restore_", suffix=".sql")
    os.close(fd)
    p = Path(path)
    p.write_bytes(upload_bytes)
    return p
