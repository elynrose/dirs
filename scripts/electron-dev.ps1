#Requires -Version 5.1
<#
  Build the Studio web app and launch the Electron desktop shell (dev mode from repo).

  Electron starts its own Docker stack + API + Celery on port 8000. Stop the browser dev
  stack first if you use restart-local.ps1 (same port). Dev Electron uses repo data/storage
  for media (same as browser Studio); packaged installs use %APPDATA%\director-electron\storage
  unless LOCAL_STORAGE_ROOT is set in the app .env.

  Usage:
    .\scripts\electron-dev.ps1
    .\scripts\electron-dev.ps1 -SkipWebBuild
    .\scripts\electron-dev.ps1 -StopLocalFirst
    .\scripts\electron-dev.ps1 -ResetBackend   # force pip reinstall in %APPDATA%\director-electron\backend-venv

  Packaged installer: .\scripts\build-exe.ps1
#>
[CmdletBinding()]
param(
    [switch]$SkipWebBuild,
    [switch]$StopLocalFirst,
    [switch]$ResetBackend
)

$ErrorActionPreference = "Stop"
$RepoRoot = Split-Path -Parent $PSScriptRoot
$WebDir = Join-Path $RepoRoot "apps\web"
$ElectronDir = Join-Path $RepoRoot "apps\electron"

if ($StopLocalFirst) {
    & (Join-Path $RepoRoot "scripts\restart-local.ps1") -StopOnly
}

if ($ResetBackend) {
    $userData = Join-Path $env:APPDATA "director-electron"
    Write-Host "electron-dev: quit Directely first if backend-venv cannot be deleted."
    foreach ($rel in @(".director-backend-bootstrap", "backend-venv")) {
        $p = Join-Path $userData $rel
        if (Test-Path -LiteralPath $p) {
            Write-Host "electron-dev: removing $p"
            Remove-Item -LiteralPath $p -Recurse -Force
        }
    }
}

if (-not $SkipWebBuild) {
    Write-Host "electron-dev: building apps/web (Vite production)..."
    Push-Location $WebDir
    try {
        npm run build
        if ($LASTEXITCODE -ne 0) { throw "web build failed (exit $LASTEXITCODE)" }
    } finally {
        Pop-Location
    }
}

$distIndex = Join-Path $WebDir "dist\index.html"
if (-not (Test-Path -LiteralPath $distIndex)) {
    throw "Missing $distIndex. Run without -SkipWebBuild, or: cd apps\web; npm run build"
}

Write-Host "electron-dev: installing Electron deps (if needed)..."
Push-Location $ElectronDir
try {
    if (-not (Test-Path -LiteralPath (Join-Path $ElectronDir "node_modules\electron"))) {
        npm install
        if ($LASTEXITCODE -ne 0) { throw "npm install failed in apps/electron" }
    }
    Write-Host "electron-dev: starting Electron (Docker + API bootstrap on first run may take a few minutes)..."
    npm start
} finally {
    Pop-Location
}
