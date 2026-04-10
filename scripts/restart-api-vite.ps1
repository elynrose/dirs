#Requires -Version 5.1
# Restart only FastAPI + Vite (leave Celery worker/beat running).
$ErrorActionPreference = "Stop"
$RepoRoot = Split-Path -Parent $PSScriptRoot
. (Join-Path $RepoRoot "scripts/director-stop-common.ps1")

$port = Read-DirectorApiPortFromRepoEnv -RepoRoot $RepoRoot
Write-Host "restart-api-vite.ps1: stopping Vite (5173) and API (port $port)…" -ForegroundColor Cyan
Stop-ViteNodeProcesses
Stop-AllListenersOnPort -Port 5173
Stop-AllListenersOnPort -Port $port
Stop-DirectorApiPythonProcesses
Start-Sleep -Seconds 1

$ApiDir = Join-Path $RepoRoot "apps/api"
$WebDir = Join-Path $RepoRoot "apps/web"
$Py = Join-Path $RepoRoot "apps/api/.venv-win/Scripts/python.exe"
if (-not (Test-Path -LiteralPath $Py)) {
    throw "Missing venv python: $Py"
}
$apiCmd = "Set-Location -LiteralPath '$ApiDir'; `$env:API_RELOAD='0'; & '$Py' -m director_api"
$viteCmd = "Set-Location -LiteralPath '$WebDir'; npm run dev"
Write-Host "restart-api-vite.ps1: starting API and Vite (new windows)…" -ForegroundColor Cyan
Start-Process powershell -WorkingDirectory $ApiDir -ArgumentList @("-NoExit", "-NoProfile", "-Command", $apiCmd)
Start-Process powershell -WorkingDirectory $WebDir -ArgumentList @("-NoExit", "-NoProfile", "-Command", $viteCmd)
Write-Host "restart-api-vite.ps1: done. Close stale PowerShell tabs if an old API/Vite window is left open." -ForegroundColor Green
