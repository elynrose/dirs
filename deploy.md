chmod +x scripts/server-major-update.sh   # once
./scripts/server-major-update.sh

**Desktop build (Windows):** from repo root, `.\scripts\build-exe.ps1` → **`apps/electron/release/`** contains both the **NSIS installer** (`Directely Setup … .exe`) and a **portable ZIP** (`Directely-<version>-win-x64.zip`) you can host for “download without installer.” See [README.md](README.md) § Distribution.

**Version bump (API + web + Electron):** `.\scripts\sync-release-version.ps1 -Version 1.0.1`

**Legal:** root [LICENSE](LICENSE), [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md), [docs/COMMERCIAL_RELEASE.md](docs/COMMERCIAL_RELEASE.md).
