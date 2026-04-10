#!/usr/bin/env pwsh
<#
.SYNOPSIS
    Build the Directely desktop app as a Windows NSIS installer (.exe).

.DESCRIPTION
    Orchestrates the full build pipeline:
      1. Prerequisite checks (Node 18+, npm)
      2. Installs web + Electron npm dependencies
      3. Builds the React frontend (Vite production build)
      4. Runs electron-builder to produce Directely Setup <version>.exe

    The finished installer is written to:
      apps\electron\release\Directely Setup <version>.exe

    The installed app requires Docker Desktop at runtime (for PostgreSQL + Redis).
    FFmpeg must be on PATH or configured via FFMPEG_BIN in the app's .env file.

.PARAMETER SkipWebInstall
    Skip `npm install` in apps/web (use when node_modules already up to date).

.PARAMETER SkipWebBuild
    Skip the Vite production build entirely (use an existing apps/web/dist/).

.PARAMETER SkipElectronInstall
    Skip `npm install` in apps/electron.

.PARAMETER Sign
    Attempt code-signing.  Requires environment variables:
      CSC_LINK              Path or URL to the .pfx certificate
      CSC_KEY_PASSWORD      Password for the certificate

.PARAMETER Arch
    Target architecture: x64 (default) or ia32 or arm64.

.EXAMPLE
    .\scripts\build-exe.ps1
    .\scripts\build-exe.ps1 -SkipWebBuild -Sign
    .\scripts\build-exe.ps1 -Arch arm64
#>

param(
    [switch]$SkipWebInstall,
    [switch]$SkipWebBuild,
    [switch]$SkipElectronInstall,
    [switch]$Sign,
    [ValidateSet("x64", "ia32", "arm64")]
    [string]$Arch = "x64"
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

# ── Helpers ──────────────────────────────────────────────────────────────────

function Write-Step([string]$msg) {
    Write-Host ""
    Write-Host "▶  $msg" -ForegroundColor Cyan
}

function Write-Ok([string]$msg) {
    Write-Host "   ✓ $msg" -ForegroundColor Green
}

function Write-Warn([string]$msg) {
    Write-Host "   ⚠ $msg" -ForegroundColor Yellow
}

function Fail([string]$msg) {
    Write-Host ""
    Write-Host "✗  $msg" -ForegroundColor Red
    exit 1
}

function Require-Command([string]$cmd, [string]$hint) {
    if (-not (Get-Command $cmd -ErrorAction SilentlyContinue)) {
        Fail "$cmd not found on PATH. $hint"
    }
    Write-Ok "$cmd found: $((Get-Command $cmd).Source)"
}

function Get-NodeMajor {
    $v = node --version 2>$null
    if ($v -match "^v(\d+)") { return [int]$Matches[1] }
    return 0
}

# ── Paths ─────────────────────────────────────────────────────────────────────

$repoRoot    = Split-Path $PSScriptRoot -Parent
$webDir      = Join-Path $repoRoot "apps\web"
$electronDir = Join-Path $repoRoot "apps\electron"
$webDist     = Join-Path $webDir "dist"
$releaseDir  = Join-Path $electronDir "release"

# ── 1. Prerequisite checks ────────────────────────────────────────────────────

Write-Step "Checking prerequisites"

Require-Command "node" "Install Node.js 18+ from https://nodejs.org/"
$nodeMajor = Get-NodeMajor
if ($nodeMajor -lt 18) {
    Fail "Node.js 18+ required (found v$nodeMajor). Download from https://nodejs.org/"
}
Write-Ok "Node.js v$nodeMajor"

Require-Command "npm" "npm is bundled with Node.js — reinstall Node."

if ($Sign) {
    if (-not $env:CSC_LINK) {
        Fail "-Sign requires CSC_LINK env var (path or URL to .pfx certificate)."
    }
    if (-not $env:CSC_KEY_PASSWORD) {
        Fail "-Sign requires CSC_KEY_PASSWORD env var."
    }
    Write-Ok "Code-signing credentials found"
} else {
    Write-Warn "Building without code signing (SmartScreen may warn). Pass -Sign to enable."
}

# ── 2. Web — npm install ───────────────────────────────────────────────────────

if (-not $SkipWebInstall) {
    Write-Step "Installing web dependencies (apps/web)"
    Push-Location $webDir
    try {
        npm install --prefer-offline
        if ($LASTEXITCODE -ne 0) { Fail "npm install failed in apps/web" }
    } finally {
        Pop-Location
    }
    Write-Ok "Web dependencies installed"
} else {
    Write-Warn "Skipping web npm install (-SkipWebInstall)"
}

# ── 3. Web — Vite build ────────────────────────────────────────────────────────

if (-not $SkipWebBuild) {
    Write-Step "Building React frontend (Vite production build)"
    Push-Location $webDir
    try {
        npm run build
        if ($LASTEXITCODE -ne 0) { Fail "Vite build failed" }
    } finally {
        Pop-Location
    }
    if (-not (Test-Path (Join-Path $webDist "index.html"))) {
        Fail "Vite build completed but dist/index.html not found — check Vite config."
    }
    Write-Ok "Frontend built → $webDist"
} else {
    if (-not (Test-Path (Join-Path $webDist "index.html"))) {
        Fail "dist/index.html not found and -SkipWebBuild is set. Run without -SkipWebBuild first."
    }
    Write-Warn "Skipping Vite build (-SkipWebBuild), using existing $webDist"
}

# ── 4. Electron — npm install ─────────────────────────────────────────────────

if (-not $SkipElectronInstall) {
    Write-Step "Installing Electron dependencies (apps/electron)"
    Push-Location $electronDir
    try {
        npm install --prefer-offline
        if ($LASTEXITCODE -ne 0) { Fail "npm install failed in apps/electron" }
    } finally {
        Pop-Location
    }
    Write-Ok "Electron dependencies installed"
} else {
    Write-Warn "Skipping Electron npm install (-SkipElectronInstall)"
}

# ── 5. electron-builder ────────────────────────────────────────────────────────

Write-Step "Running electron-builder (target: Windows NSIS + ZIP, arch: $Arch)"

Push-Location $electronDir
try {
    $builderArgs = @("--win", "--$Arch")
    if (-not $Sign) {
        # Disable signing even if CSC_LINK/CSC_KEY_PASSWORD happen to be set in env
        $env:CSC_IDENTITY_AUTO_DISCOVERY = "false"
    }
    node ./node_modules/electron-builder/cli.js @builderArgs
    if ($LASTEXITCODE -ne 0) { Fail "electron-builder failed (exit $LASTEXITCODE)" }
} finally {
    Pop-Location
}

# ── 6. Report output ──────────────────────────────────────────────────────────

Write-Step "Build complete"
$exeFiles = Get-ChildItem -Path $releaseDir -Filter "*.exe" -Recurse -ErrorAction SilentlyContinue |
    Where-Object { $_.Name -notlike "*unpack*" } |
    Sort-Object LastWriteTime -Descending

if ($exeFiles.Count -eq 0) {
    Write-Warn "No .exe found in $releaseDir — check electron-builder output above."
} else {
    foreach ($f in $exeFiles) {
        $sizeMb = [math]::Round($f.Length / 1MB, 1)
        Write-Ok "$($f.FullName)  ($sizeMb MB)"
    }
}

Write-Host ""
Write-Host "Runtime requirements for end users:" -ForegroundColor White
Write-Host "  • Docker Desktop  https://www.docker.com/products/docker-desktop/" -ForegroundColor Gray
Write-Host "  • FFmpeg on PATH  https://ffmpeg.org/download.html  (or set FFMPEG_BIN in .env)" -ForegroundColor Gray
Write-Host "  • Python 3.11+    https://www.python.org/downloads/" -ForegroundColor Gray
Write-Host ""
