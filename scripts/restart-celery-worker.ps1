#Requires -Version 5.1
<#
  Stop only the Celery worker (not beat), then start a new worker.
  Uses apps\api\.venv-win if present, else .venv. Windows: --pool=solo.
#>
$ErrorActionPreference = "Stop"
$RepoRoot = Split-Path -Parent $PSScriptRoot
$RunDir = Join-Path $RepoRoot ".run"
$ApiDir = Join-Path $RepoRoot "apps\api"

. (Join-Path $RepoRoot "scripts/director-stop-common.ps1")
Stop-CeleryDirectorWorkerProcessOnly

Start-Sleep -Seconds 1

$Py = Join-Path $RepoRoot "apps\api\.venv-win\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $Py)) {
    $Py = Join-Path $RepoRoot "apps\api\.venv\Scripts\python.exe"
}
if (-not (Test-Path -LiteralPath $Py)) {
    Write-Host "No python at .venv-win or .venv" -ForegroundColor Red
    exit 1
}

New-Item -ItemType Directory -Force -Path $RunDir | Out-Null
$winPool = if ($IsWindows -or $env:OS -eq "Windows_NT") { " --pool=solo" } else { "" }
$celeryExe = Join-Path (Split-Path -Parent $Py) "celery.exe"
if (Test-Path -LiteralPath $celeryExe) {
    $celeryCmd = "Set-Location -LiteralPath '$ApiDir'; & '$celeryExe' -A director_api.tasks.celery_app worker -l info$winPool 2>&1 | Tee-Object -FilePath '$RunDir\director-worker.log' -Append"
} else {
    $celeryCmd = "Set-Location -LiteralPath '$ApiDir'; & '$Py' -m celery -A director_api.tasks.celery_app worker -l info$winPool 2>&1 | Tee-Object -FilePath '$RunDir\director-worker.log' -Append"
}

Write-Host "restart-celery-worker: starting worker (minimized window)..." -ForegroundColor Cyan
Start-Process powershell -WorkingDirectory $ApiDir -WindowStyle Minimized `
    -ArgumentList @("-NoProfile", "-Command", $celeryCmd)

Write-Host "restart-celery-worker: log $RunDir\director-worker.log" -ForegroundColor Gray
