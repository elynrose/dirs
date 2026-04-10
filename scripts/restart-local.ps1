#Requires -Version 5.1
<#
  Stop then start local Director API + Celery worker + beat (Windows; uses apps\api\.venv-win).

  Usage:
    powershell -ExecutionPolicy Bypass -File scripts\restart-local.ps1
    .\scripts\restart-local.ps1 -StopOnly

  Logs: .run\director-api.log, .run\director-worker.log, .run\director-beat.log
#>
[CmdletBinding()]
param(
    [switch]$StopOnly
)

$ErrorActionPreference = "Stop"
$RepoRoot = Split-Path -Parent $PSScriptRoot
$RunDir = Join-Path $RepoRoot ".run"
$ApiDir = Join-Path $RepoRoot "apps\api"

. (Join-Path $RepoRoot "scripts\director-stop-common.ps1")

function Resolve-PythonExe {
    $winVenv = Join-Path $RepoRoot "apps\api\.venv-win\Scripts\python.exe"
    if (Test-Path -LiteralPath $winVenv) { return $winVenv }
    if ($env:OS -eq "Windows_NT") { return $null }
    $unixVenv = Join-Path $RepoRoot "apps\api\.venv\bin\python"
    if (Test-Path -LiteralPath $unixVenv) { return $unixVenv }
    return $null
}

$Py = Resolve-PythonExe
if (-not $Py) {
    Write-Host @"
No Python venv found. From the repo root:

  cd apps\api
  py -3.11 -m venv .venv-win
  .\.venv-win\Scripts\pip install -e ".[dev]"

Or set CELERY_EAGER=true in .env and run only the API (no separate worker).
"@ -ForegroundColor Yellow
    exit 1
}

$port = Read-DirectorApiPortFromRepoEnv -RepoRoot $RepoRoot
# Include Vite so a full restart does not leave an old Studio dev server on :5173
Stop-DirectorLocalDev -RepoRoot $RepoRoot -ApiPort $port -IncludeVite

if ($StopOnly) {
    Write-Host "restart-local.ps1: stopped (did not start)"
    exit 0
}

New-Item -ItemType Directory -Force -Path $RunDir | Out-Null

$winPool = if ($IsWindows -or $env:OS -eq "Windows_NT") { " --pool=solo" } else { "" }
$celeryExe = Join-Path (Split-Path -Parent $Py) "celery.exe"
if (Test-Path -LiteralPath $celeryExe) {
    $celeryCmd = "Set-Location -LiteralPath '$ApiDir'; & '$celeryExe' -A director_api.tasks.celery_app worker -l info$winPool 2>&1 | Tee-Object -FilePath '$RunDir\director-worker.log' -Append"
    $beatCmd = "Set-Location -LiteralPath '$ApiDir'; & '$celeryExe' -A director_api.tasks.celery_app beat -l info 2>&1 | Tee-Object -FilePath '$RunDir\director-beat.log' -Append"
} else {
    $celeryCmd = "Set-Location -LiteralPath '$ApiDir'; & '$Py' -m celery -A director_api.tasks.celery_app worker -l info$winPool 2>&1 | Tee-Object -FilePath '$RunDir\director-worker.log' -Append"
    $beatCmd = "Set-Location -LiteralPath '$ApiDir'; & '$Py' -m celery -A director_api.tasks.celery_app beat -l info 2>&1 | Tee-Object -FilePath '$RunDir\director-beat.log' -Append"
}

# Force off so a machine-level API_RELOAD=1 cannot enable WatchFiles reload (WinError 10048 on :8000).
$apiCmd = "Set-Location -LiteralPath '$ApiDir'; `$env:API_RELOAD='0'; & '$Py' -m director_api 2>&1 | Tee-Object -FilePath '$RunDir\director-api.log' -Append"

Write-Host "restart-local.ps1: starting Celery worker (minimized window)..." -ForegroundColor Cyan
Start-Process powershell -WorkingDirectory $ApiDir -WindowStyle Minimized `
    -ArgumentList @("-NoProfile", "-Command", $celeryCmd)

Write-Host "restart-local.ps1: starting Celery beat (minimized window)..." -ForegroundColor Cyan
Start-Process powershell -WorkingDirectory $ApiDir -WindowStyle Minimized `
    -ArgumentList @("-NoProfile", "-Command", $beatCmd)

Write-Host "restart-local.ps1: starting API (minimized window)..." -ForegroundColor Cyan
Start-Process powershell -WorkingDirectory $ApiDir -WindowStyle Minimized `
    -ArgumentList @("-NoProfile", "-Command", $apiCmd)

$healthOk = $false
for ($i = 0; $i -lt 15; $i++) {
    Start-Sleep -Seconds 2
    try {
        $r = Invoke-RestMethod -Uri "http://127.0.0.1:$port/v1/health" -Method GET -TimeoutSec 8
        if ($r.data.status -eq "ok") {
            Write-Host "restart-local.ps1: GET /v1/health ok (port $port)" -ForegroundColor Green
            $healthOk = $true
            break
        }
    } catch {
        # Retry until Postgres/Docker is reachable or attempts exhausted.
    }
}
if (-not $healthOk) {
    Write-Host "restart-local.ps1: API not ready after ~30s - check $RunDir\director-api.log (Docker/Postgres/Redis must be up)" -ForegroundColor Yellow
    exit 1
}

Write-Host "Logs: $RunDir\director-api.log, director-worker.log, director-beat.log - leave the three minimized windows open." -ForegroundColor Gray
