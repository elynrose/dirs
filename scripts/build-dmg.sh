#!/usr/bin/env bash
# =============================================================================
# build-dmg.sh — Build Director desktop app as a macOS DMG
# =============================================================================
#
# USAGE
#   ./scripts/build-dmg.sh [options]
#
# OPTIONS
#   --skip-web-install      Skip `npm install` in apps/web
#   --skip-web-build        Skip the Vite build (reuse existing apps/web/dist/)
#   --skip-electron-install Skip `npm install` in apps/electron
#   --sign                  Enable code-signing + notarisation.
#                           Requires env vars:
#                             APPLE_ID                  your@apple.id
#                             APPLE_APP_SPECIFIC_PASSWORD  app-specific password
#                             APPLE_TEAM_ID             10-char team ID
#                             CSC_LINK                  path to .p12 cert (or base64)
#                             CSC_KEY_PASSWORD          .p12 password
#   --arch <arch>           x64 | arm64 | universal  (default: current machine arch)
#   -h, --help              Show this help
#
# OUTPUT
#   apps/electron/release/Director-<version>.dmg
#   apps/electron/release/Director-<version>-mac.zip
#
# RUNTIME REQUIREMENTS (for end users)
#   • Docker Desktop  https://www.docker.com/products/docker-desktop/
#   • Python 3.11+    https://www.python.org/downloads/  (or via Homebrew: brew install python@3.12)
#   • FFmpeg          brew install ffmpeg  (or set FFMPEG_BIN in the app's .env)
# =============================================================================

set -euo pipefail

# ── Colour helpers ─────────────────────────────────────────────────────────────
CYAN='\033[0;36m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; RED='\033[0;31m'; NC='\033[0m'
step()  { echo -e "\n${CYAN}▶  $*${NC}"; }
ok()    { echo -e "   ${GREEN}✓  $*${NC}"; }
warn()  { echo -e "   ${YELLOW}⚠  $*${NC}"; }
fail()  { echo -e "\n${RED}✗  $*${NC}"; exit 1; }

# ── Parse arguments ───────────────────────────────────────────────────────────
SKIP_WEB_INSTALL=false
SKIP_WEB_BUILD=false
SKIP_ELECTRON_INSTALL=false
SIGN=false
ARCH=""

while [[ $# -gt 0 ]]; do
    case "$1" in
        --skip-web-install)      SKIP_WEB_INSTALL=true ;;
        --skip-web-build)        SKIP_WEB_BUILD=true ;;
        --skip-electron-install) SKIP_ELECTRON_INSTALL=true ;;
        --sign)                  SIGN=true ;;
        --arch)                  ARCH="$2"; shift ;;
        -h|--help)
            sed -n '3,40p' "$0" | sed 's/^# \{0,1\}//'
            exit 0
            ;;
        *) fail "Unknown option: $1 (run with --help)" ;;
    esac
    shift
done

# Default arch to the current machine
if [[ -z "$ARCH" ]]; then
    _hw=$(uname -m)
    if [[ "$_hw" == "arm64" ]]; then ARCH="arm64"
    else ARCH="x64"; fi
fi

# ── Paths ─────────────────────────────────────────────────────────────────────
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
WEB_DIR="$REPO_ROOT/apps/web"
ELECTRON_DIR="$REPO_ROOT/apps/electron"
WEB_DIST="$WEB_DIR/dist"
RELEASE_DIR="$ELECTRON_DIR/release"

# ── 1. Prerequisite checks ────────────────────────────────────────────────────
step "Checking prerequisites"

if ! command -v node &>/dev/null; then
    fail "node not found. Install Node.js 18+ from https://nodejs.org/ or via: brew install node"
fi
NODE_MAJOR=$(node --version | grep -oE '[0-9]+' | head -1)
if (( NODE_MAJOR < 18 )); then
    fail "Node.js 18+ required (found $(node --version)). Upgrade from https://nodejs.org/"
fi
ok "Node.js $(node --version)"

if ! command -v npm &>/dev/null; then
    fail "npm not found — reinstall Node.js."
fi
ok "npm $(npm --version)"

if [[ "$OSTYPE" != "darwin"* ]]; then
    warn "This script targets macOS (darwin). You are on: $OSTYPE"
    warn "DMG creation requires macOS. Continuing anyway — electron-builder will report errors."
fi

if [[ "$SIGN" == true ]]; then
    for var in APPLE_ID APPLE_APP_SPECIFIC_PASSWORD APPLE_TEAM_ID CSC_LINK CSC_KEY_PASSWORD; do
        [[ -n "${!var:-}" ]] || fail "--sign requires \$$var to be set"
    done
    ok "Code-signing credentials found"
else
    warn "Building without code signing (Gatekeeper will flag the app). Pass --sign to enable."
fi

# ── 2. Web — npm install ───────────────────────────────────────────────────────
if [[ "$SKIP_WEB_INSTALL" == false ]]; then
    step "Installing web dependencies (apps/web)"
    (cd "$WEB_DIR" && npm install --prefer-offline)
    ok "Web dependencies installed"
else
    warn "Skipping web npm install (--skip-web-install)"
fi

# ── 3. Web — Vite build ────────────────────────────────────────────────────────
if [[ "$SKIP_WEB_BUILD" == false ]]; then
    step "Building React frontend (Vite production build)"
    (cd "$WEB_DIR" && npm run build)
    [[ -f "$WEB_DIST/index.html" ]] || fail "Vite build completed but dist/index.html not found."
    ok "Frontend built → $WEB_DIST"
else
    [[ -f "$WEB_DIST/index.html" ]] || \
        fail "dist/index.html not found and --skip-web-build is set. Run without that flag first."
    warn "Skipping Vite build (--skip-web-build), using existing $WEB_DIST"
fi

# ── 4. Electron — npm install ─────────────────────────────────────────────────
if [[ "$SKIP_ELECTRON_INSTALL" == false ]]; then
    step "Installing Electron dependencies (apps/electron)"
    (cd "$ELECTRON_DIR" && npm install --prefer-offline)
    ok "Electron dependencies installed"
else
    warn "Skipping Electron npm install (--skip-electron-install)"
fi

# ── 5. electron-builder ────────────────────────────────────────────────────────
step "Running electron-builder (target: macOS DMG + ZIP, arch: $ARCH)"

cd "$ELECTRON_DIR"

if [[ "$SIGN" == false ]]; then
    export CSC_IDENTITY_AUTO_DISCOVERY=false
fi

BUILDER_ARGS=("--mac" "--$ARCH")
node ./node_modules/electron-builder/cli.js "${BUILDER_ARGS[@]}"

# ── 6. Report output ──────────────────────────────────────────────────────────
step "Build complete"

DMG_FILES=()
while IFS= read -r -d '' f; do
    DMG_FILES+=("$f")
done < <(find "$RELEASE_DIR" -maxdepth 1 -name "*.dmg" -print0 2>/dev/null)

if [[ ${#DMG_FILES[@]} -eq 0 ]]; then
    warn "No .dmg found in $RELEASE_DIR — check electron-builder output above."
else
    for f in "${DMG_FILES[@]}"; do
        SIZE=$(du -sh "$f" 2>/dev/null | cut -f1)
        ok "$f  ($SIZE)"
    done
fi

echo ""
echo "Runtime requirements for end users:"
echo "  • Docker Desktop  https://www.docker.com/products/docker-desktop/"
echo "  • Python 3.11+    brew install python@3.12"
echo "  • FFmpeg          brew install ffmpeg  (or set FFMPEG_BIN in the app's .env)"
echo ""
