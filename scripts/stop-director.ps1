#Requires -Version 5.1
<#
  Stop all local Directely dev processes: API (default port from repo .env or 8000), Celery worker/beat,
  and Vite dev/preview listeners (5173, 4173).

  Usage:
    powershell -ExecutionPolicy Bypass -File scripts\stop-director.ps1
#>
$ErrorActionPreference = "Stop"
$RepoRoot = Split-Path -Parent $PSScriptRoot
. (Join-Path $PSScriptRoot "director-stop-common.ps1")

$port = Read-DirectorApiPortFromRepoEnv -RepoRoot $RepoRoot
Write-Host "stop-director.ps1: stopping API port $port, Celery, Vite (5173/4173)..." -ForegroundColor Cyan
Stop-DirectorLocalDev -RepoRoot $RepoRoot -ApiPort $port -IncludeVite
Write-Host "stop-director.ps1: done." -ForegroundColor Green
