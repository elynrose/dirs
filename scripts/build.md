The Electron shell in apps/electron/ already handled the hard parts (spawning the Python API, Celery, running migrations, serving the frontend). What was missing was the orchestration scripts and a proper build config. Here's what was created:

scripts/build-exe.ps1 — Windows
.\scripts\build-exe.ps1                    # standard build
.\scripts\build-exe.ps1 -SkipWebBuild      # reuse existing dist/
.\scripts\build-exe.ps1 -Sign             # with code signing (needs CSC_LINK + CSC_KEY_PASSWORD)
.\scripts\build-exe.ps1 -Arch arm64       # ARM build

Produces: apps/electron/release/Director-<version>-win-x64.exe

scripts/build-dmg.sh — macOS
./scripts/build-dmg.sh                     # standard build (auto-detects x64/arm64)
./scripts/build-dmg.sh --skip-web-build    # reuse existing dist/
./scripts/build-dmg.sh --arch universal    # fat binary (x64 + arm64)
./scripts/build-dmg.sh --sign              # with signing + notarisation

Produces: apps/electron/release/Director-<version>-mac-arm64.dmg

apps/electron/package.json — build config improvements
Before	After
Default artifact naming	Director-0.1.0-win-x64.exe / Director-0.1.0-mac-arm64.dmg
No code exclusions beyond .venv	Also excludes .venv-win, *.pyc, *.pyo, tests/, *.egg-info, .ruff_cache
Single arch per platform	macOS builds both x64 + arm64 in one pass
Bare NSIS (one-click)	Custom install dir, Start Menu + Desktop shortcuts, no data wipe on uninstall
No DMG layout	540×380 window, file + Applications link, 100px icons
main.js only in bundle	Also includes lib/**/*
apps/electron/build-assets/
entitlements.mac.plist — Hardened Runtime entitlements for the app to spawn Python subprocesses, open network connections, and access user files (required for notarisation)
README.md — Instructions for generating icon.icns / icon.ico / icon.png from a master PNG
What end users need installed
The installer itself is fully self-contained (Python source + frontend assets bundled), but at first launch the app creates a Python venv and pip-installs itself, so the machine needs:

Docker Desktop — PostgreSQL + Redis run via docker compose
Python 3.11+ — for the venv bootstrap (Windows: py launcher, macOS: python3.11+)
FFmpeg — for video compilation (brew install ffmpeg / winget install ffmpeg, or set FFMPEG_BIN in .env)