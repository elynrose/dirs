# Dot-source only — venv + Python deps for Director (Windows + Unix venv layouts).
# Used by Launch.ps1 (repo root) when -SkipBootstrap is not set.

function Test-DirectorPythonDeps {
    param(
        [Parameter(Mandatory = $true)]
        [string]$PythonExe
    )
    $prev = $ErrorActionPreference
    try {
        $ErrorActionPreference = "SilentlyContinue"
        & $PythonExe -c "from ffmpeg_pipelines.audio_slot import normalize_audio_to_duration; import director_api" 2>$null | Out-Null
        return ($LASTEXITCODE -eq 0)
    } finally {
        $ErrorActionPreference = $prev
    }
}

function Resolve-DirectorPythonLauncher {
    $prev = $ErrorActionPreference
    try {
        $ErrorActionPreference = "SilentlyContinue"
        if (Get-Command py -ErrorAction SilentlyContinue) {
            py -3.11 -c "import sys; assert sys.version_info >= (3, 11)" 2>$null | Out-Null
            if ($LASTEXITCODE -eq 0) { return @{ Kind = "py311"; Exe = "py"; Args = @("-3.11") } }
        }
        if (Get-Command python -ErrorAction SilentlyContinue) {
            python -c "import sys; assert sys.version_info >= (3, 11)" 2>$null | Out-Null
            if ($LASTEXITCODE -eq 0) { return @{ Kind = "python"; Exe = "python"; Args = @() } }
        }
    } finally {
        $ErrorActionPreference = $prev
    }
    return $null
}

function Invoke-DirectorBootstrap {
    <#
    .SYNOPSIS
      Ensure apps/api/.venv-win (Windows) or apps/api/.venv (Unix) exists; install ffmpeg-pipelines + director-api if imports fail.
    #>
    param(
        [Parameter(Mandatory = $true)]
        [string]$RepoRoot
    )

    $ApiDir = Join-Path $RepoRoot "apps/api"
    $isWin = $env:OS -eq "Windows_NT"
    $venvName = if ($isWin) { ".venv-win" } else { ".venv" }
    $venvPy = if ($isWin) {
        Join-Path $RepoRoot "apps/api/.venv-win/Scripts/python.exe"
    } else {
        Join-Path $RepoRoot "apps/api/.venv/bin/python"
    }

    if (-not (Test-Path -LiteralPath $venvPy)) {
        $launcher = Resolve-DirectorPythonLauncher
        if (-not $launcher) {
            Write-Host "Need Python 3.11+ (python.org or Windows 'py' launcher). Then re-run Launch.ps1." -ForegroundColor Red
            exit 1
        }
        Write-Host "Creating Python venv ($venvName)…" -ForegroundColor Cyan
        Push-Location $ApiDir
        try {
            if ($launcher.Kind -eq "py311") {
                & $launcher.Exe @($launcher.Args + @("-m", "venv", $venvName))
            } else {
                & $launcher.Exe -m venv $venvName
            }
            if ($LASTEXITCODE -ne 0) { throw "venv creation failed with exit $LASTEXITCODE" }
        } finally {
            Pop-Location
        }
    }

    if (-not (Test-Path -LiteralPath $venvPy)) {
        Write-Host "venv python missing: $venvPy" -ForegroundColor Red
        exit 1
    }

    $pipDir = Split-Path -Parent $venvPy
    $pip = Join-Path $pipDir $(if ($isWin) { "pip.exe" } else { "pip" })
    if (-not (Test-Path -LiteralPath $pip)) {
        Write-Host "pip not found next to venv python" -ForegroundColor Red
        exit 1
    }

    if (-not (Test-DirectorPythonDeps -PythonExe $venvPy)) {
        Write-Host "Installing / repairing Python packages (ffmpeg-pipelines + director-api)…" -ForegroundColor Cyan
        $ffRoot = Join-Path $RepoRoot "packages/ffmpeg-pipelines"
        & $pip install --upgrade pip -q
        if ($LASTEXITCODE -ne 0) { Write-Host "pip upgrade warning (continuing)" -ForegroundColor Yellow }
        & $pip install --force-reinstall --no-cache-dir $ffRoot
        if ($LASTEXITCODE -ne 0) { throw "pip install ffmpeg-pipelines failed" }
        Push-Location $ApiDir
        try {
            & $pip install -e ".[dev]"
            if ($LASTEXITCODE -ne 0) { throw "pip install -e .[dev] failed" }
        } finally {
            Pop-Location
        }
    }

    if (-not (Test-DirectorPythonDeps -PythonExe $venvPy)) {
        Write-Host "Python deps still not importable after install." -ForegroundColor Red
        exit 1
    }

    Write-Host "Python venv and packages OK." -ForegroundColor Green
}
