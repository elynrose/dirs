"""CLI entry: `director-api` or `python -m director_api`."""

import os
import sys

import uvicorn


def main() -> None:
    host = os.environ.get("API_HOST", "0.0.0.0")
    port = int(os.environ.get("API_PORT", "8000"))
    # Default off: reload spawns extra processes; Windows restart scripts often do not export .env into the shell,
    # so API_RELOAD=0 in .env was ignored and multiple LISTEN on :8000 caused stale API versions.
    reload = os.environ.get("API_RELOAD", "0").strip().lower() in ("1", "true", "yes", "on")
    # Uvicorn reload = parent + child both binding :8000 → WinError 10048 and confusing half-restarts.
    if reload and sys.platform == "win32":
        print(
            "director_api: ignoring API_RELOAD on Windows (use WSL or Linux for uvicorn --reload); "
            "single-process API avoids port bind conflicts.",
            file=sys.stderr,
        )
        reload = False
    uvicorn.run(
        "director_api.main:app",
        host=host,
        port=port,
        reload=reload,
    )


if __name__ == "__main__":
    main()
