#Requires -Version 5.1
<#
  Director — full local stack (repo root).

  - Bootstrap: Python venv + pip deps (unless -SkipBootstrap)
  - Docker Compose: Postgres, Redis, MinIO (unless -SkipDocker)
  - Alembic migrate (unless -SkipMigrate)
  - New windows: FastAPI, Celery worker, Celery beat (unless -SkipBeat), Vite (unless -SkipVite)
  - Opens the Studio in your default browser (unless -SkipBrowser or -SkipVite)

  Usage (Windows):
    .\Launch.ps1
    .\Launch.ps1 -SkipBrowser
    .\Launch.cmd
    powershell -NoProfile -ExecutionPolicy Bypass -File .\Launch.ps1

  macOS / Linux: use ./Launch.sh instead (this script is tuned for Windows Terminal windows).
#>
[CmdletBinding()]
param(
    [switch]$SkipDocker,
    [switch]$SkipMigrate,
    [switch]$SkipBrowser,
    [switch]$SkipBeat,
    [switch]$SkipBootstrap,
    [switch]$SkipVite,
    [int]$DockerWaitSec = 120
)

$ErrorActionPreference = "Stop"
$RepoRoot = $PSScriptRoot

. (Join-Path $RepoRoot "scripts/director-bootstrap.ps1")
if (-not $SkipBootstrap) {
    Invoke-DirectorBootstrap -RepoRoot $RepoRoot
}

function Test-DockerReady {
    $prev = $ErrorActionPreference
    try {
        $ErrorActionPreference = "SilentlyContinue"
        & docker info 2>$null | Out-Null
        return ($LASTEXITCODE -eq 0)
    } finally {
        $ErrorActionPreference = $prev
    }
}

function Start-DockerDesktop-Windows {
    $candidates = @(
        "${env:ProgramFiles}\Docker\Docker\Docker Desktop.exe",
        "${env:ProgramFiles(x86)}\Docker\Docker\Docker Desktop.exe"
    )
    foreach ($p in $candidates) {
        if (Test-Path -LiteralPath $p) {
            Write-Host "Starting Docker Desktop…" -ForegroundColor Cyan
            Start-Process -FilePath $p
            return $true
        }
    }
    return $false
}

function Wait-Docker {
    param([int]$TimeoutSec)
    $deadline = (Get-Date).AddSeconds($TimeoutSec)
    while ((Get-Date) -lt $deadline) {
        if (Test-DockerReady) { return }
        Start-Sleep -Seconds 2
    }
    throw "Docker Engine did not become ready within ${TimeoutSec}s. Start Docker Desktop manually and retry."
}

function Resolve-PythonExe {
    $winVenv = Join-Path $RepoRoot "apps\api\.venv-win\Scripts\python.exe"
    if (Test-Path -LiteralPath $winVenv) { return $winVenv }
    if ($env:OS -eq "Windows_NT") { return $null }
    $unixVenv = Join-Path $RepoRoot "apps\api\.venv\bin\python"
    if (Test-Path -LiteralPath $unixVenv) { return $unixVenv }
    return $null
}

if (-not $SkipDocker) {
    if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
        Write-Host "Docker CLI not found. Install Docker Desktop and ensure 'docker' is on PATH." -ForegroundColor Red
        exit 1
    }
    if (-not (Test-DockerReady)) {
        $dockerStarted = $false
        if ($env:OS -eq "Windows_NT") {
            $dockerStarted = Start-DockerDesktop-Windows
        } elseif ($PSVersionTable.PSVersion.Major -ge 6 -and $IsMacOS) {
            Write-Host "Starting Docker Desktop (macOS)…" -ForegroundColor Cyan
            Start-Process open -ArgumentList @("-a", "Docker")
            $dockerStarted = $true
        }
        if (-not $dockerStarted) {
            Write-Host "Docker is not running. Start Docker Desktop (or the Docker daemon on Linux), then run Launch again." -ForegroundColor Red
            exit 1
        }
        Wait-Docker -TimeoutSec $DockerWaitSec
    }
    Write-Host "Starting Compose stack (Postgres, Redis, MinIO)…" -ForegroundColor Cyan
    Push-Location $RepoRoot
    try {
        $prevEa = $ErrorActionPreference
        $ErrorActionPreference = "SilentlyContinue"
        try {
            docker compose up -d --wait 2>&1 | Out-Host
            if ($LASTEXITCODE -ne 0) {
                Write-Host "docker compose --wait failed or unsupported; retrying without --wait…" -ForegroundColor Yellow
                docker compose up -d 2>&1 | Out-Host
                if ($LASTEXITCODE -ne 0) { throw "docker compose exited with $LASTEXITCODE" }
                Start-Sleep -Seconds 8
            }
        } finally {
            $ErrorActionPreference = $prevEa
        }
    } finally {
        Pop-Location
    }
}

$Py = Resolve-PythonExe
if (-not $Py) {
    Write-Host @"
No Python venv found. From the repo root, run:

  Windows:
    cd apps\api
    py -3.11 -m venv .venv-win
    .\.venv-win\Scripts\pip install -e ".[dev]"

  macOS / Linux:
    cd apps/api && python3.11 -m venv .venv && . .venv/bin/activate && pip install -e ".[dev]"

Then run Launch again (or use ./Launch.sh on Unix).
"@ -ForegroundColor Yellow
    exit 1
}

$ApiDir = Join-Path $RepoRoot "apps/api"
$WebDir = Join-Path $RepoRoot "apps/web"

if (-not $SkipVite) {
    if (-not (Get-Command node -ErrorAction SilentlyContinue) -or -not (Get-Command npm -ErrorAction SilentlyContinue)) {
        Write-Host "Node.js / npm not found. Install Node LTS from https://nodejs.org/ or use -SkipVite." -ForegroundColor Red
        exit 1
    }
    if (-not (Test-Path -LiteralPath (Join-Path $WebDir "node_modules"))) {
        Write-Host "Web: installing npm dependencies (first run)…" -ForegroundColor Cyan
        Push-Location $WebDir
        try {
            npm install
            if ($LASTEXITCODE -ne 0) { throw "npm install failed with exit $LASTEXITCODE" }
        } finally {
            Pop-Location
        }
    }
}

if (-not $SkipMigrate) {
    Write-Host "Running database migrations…" -ForegroundColor Cyan
    Push-Location $ApiDir
    try {
        & $Py -m alembic upgrade head
        if ($LASTEXITCODE -ne 0) { throw "alembic failed with exit $LASTEXITCODE" }
    } finally {
        Pop-Location
    }
}

$startMsg = "Starting API, Celery worker"
if (-not $SkipBeat) { $startMsg += ", Celery beat" }
if (-not $SkipVite) { $startMsg += ", and Vite" }
Write-Host "$startMsg (new windows)…" -ForegroundColor Cyan
$apiCmd = "Set-Location -LiteralPath '$ApiDir'; `$env:API_RELOAD='0'; & '$Py' -m director_api"
Start-Process powershell -WorkingDirectory $ApiDir -ArgumentList @("-NoExit", "-NoProfile", "-Command", $apiCmd)

$winPool = if ($env:OS -eq "Windows_NT") { " --pool=solo" } else { "" }
$celeryExe = Join-Path (Split-Path -Parent $Py) "celery.exe"
$RunDir = Join-Path $RepoRoot ".run"
$null = New-Item -ItemType Directory -Force -Path $RunDir
$WorkerLog = (Resolve-Path $RunDir).Path + "\director-worker.log"
if (Test-Path -LiteralPath $celeryExe) {
    $celeryWorkerCmd = "Set-Location -LiteralPath '$ApiDir'; & '$celeryExe' -A director_api.tasks.celery_app worker -l info$winPool 2>&1 | Tee-Object -FilePath '$WorkerLog'"
    $celeryBeatCmd = "Set-Location -LiteralPath '$ApiDir'; & '$celeryExe' -A director_api.tasks.celery_app beat -l info"
} else {
    $celeryWorkerCmd = "Set-Location -LiteralPath '$ApiDir'; & '$Py' -m celery -A director_api.tasks.celery_app worker -l info$winPool 2>&1 | Tee-Object -FilePath '$WorkerLog'"
    $celeryBeatCmd = "Set-Location -LiteralPath '$ApiDir'; & '$Py' -m celery -A director_api.tasks.celery_app beat -l info"
}
Start-Process powershell -WorkingDirectory $ApiDir -ArgumentList @("-NoExit", "-NoProfile", "-Command", $celeryWorkerCmd)

if (-not $SkipBeat) {
    Start-Process powershell -WorkingDirectory $ApiDir -ArgumentList @("-NoExit", "-NoProfile", "-Command", $celeryBeatCmd)
}

if (-not $SkipVite) {
    $viteCmd = "Set-Location -LiteralPath '$WebDir'; npm run dev"
    Start-Process powershell -WorkingDirectory $WebDir -ArgumentList @("-NoExit", "-NoProfile", "-Command", $viteCmd)
}

if (-not $SkipBrowser -and -not $SkipVite) {
    Start-Sleep -Seconds 2
    Start-Process "http://localhost:5173/"
}

Write-Host ""
Write-Host "Director is starting." -ForegroundColor Green
if (-not $SkipVite) {
    Write-Host "  Web UI:  http://localhost:5173/" -ForegroundColor Green
}
Write-Host "  API:     http://127.0.0.1:8000/v1/health" -ForegroundColor Green
Write-Host "  Stack:   Docker Compose (Postgres :5433, Redis :6379, MinIO :9000)" -ForegroundColor Green
$winCount = 2
if (-not $SkipBeat) { $winCount++ }
if (-not $SkipVite) { $winCount++ }
$parts = @("API", "Celery worker")
if (-not $SkipBeat) { $parts += "Celery beat" }
if (-not $SkipVite) { $parts += "Vite" }
Write-Host "Leave $winCount PowerShell window(s) open ($($parts -join ', ')). Close them to stop those processes." -ForegroundColor Gray
