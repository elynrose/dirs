<#
.SYNOPSIS
  Create the local-TTS sidecar venv used by Kokoro narration (Settings > Generation >
  Speech provider = Kokoro) when the worker runtime can't import torch/kokoro (e.g. Python 3.14).

.DESCRIPTION
  The Director worker runs on Python 3.14, which has no torch wheels, so Kokoro TTS runs
  out-of-process in a dedicated Python 3.11 venv with kokoro + soundfile (kokoro pulls a CPU
  build of torch, which is plenty for the 82M model). This script creates that venv and installs
  the dependencies, then prints the python.exe path to paste into Settings > API keys (advanced)
  as `tts_sidecar_python` (or the TTS_SIDECAR_PYTHON env).

.PARAMETER Cuda
  Optional CUDA wheel tag (e.g. cu124) to install a GPU torch build before kokoro. Empty = CPU
  torch (recommended; downloads far less and is fast enough for the 82M model).

.PARAMETER VenvPath
  Where to create the venv. Defaults to %APPDATA%\director-electron\tts-renderer-venv.
#>
param(
  [string]$Cuda = "",
  [string]$VenvPath = "$env:APPDATA\director-electron\tts-renderer-venv"
)

$ErrorActionPreference = "Stop"

function Find-Python311 {
  try {
    $v = (& py -3.11 -c "import sys;print(sys.version.split()[0])") 2>$null
    if ($LASTEXITCODE -eq 0 -and $v) { return @("py", "-3.11") }
  } catch {}
  foreach ($cand in @("python3.11", "python3.11.exe")) {
    $cmd = Get-Command $cand -ErrorAction SilentlyContinue
    if ($cmd) { return @($cmd.Source) }
  }
  return $null
}

$py = Find-Python311
if (-not $py) {
  Write-Error "Python 3.11 not found. Install it (e.g. 'winget install Python.Python.3.11') and re-run."
  exit 1
}

Write-Host "Using Python 3.11 launcher: $($py -join ' ')"
Write-Host "Creating venv at: $VenvPath"

$parent = Split-Path -Parent $VenvPath
if (-not (Test-Path $parent)) { New-Item -ItemType Directory -Force -Path $parent | Out-Null }

& $py[0] $py[1..($py.Count-1)] -m venv "$VenvPath"
if ($LASTEXITCODE -ne 0) { Write-Error "venv creation failed"; exit 1 }

$venvPy = Join-Path $VenvPath "Scripts\python.exe"
if (-not (Test-Path $venvPy)) { Write-Error "venv python not found at $venvPy"; exit 1 }

Write-Host "Upgrading pip..."
& $venvPy -m pip install --upgrade pip

if ($Cuda) {
  Write-Host "Installing CUDA torch ($Cuda) first... (this downloads ~2.5 GB)"
  & $venvPy -m pip install --index-url "https://download.pytorch.org/whl/$Cuda" torch
  if ($LASTEXITCODE -ne 0) { Write-Error "CUDA torch install failed for $Cuda"; exit 1 }
}

# Locate the pinned requirements file (repo checkout or installed wheel).
$req = @(
  (Join-Path $PSScriptRoot "..\apps\api\director_api\tts\requirements-tts.txt")
) | Where-Object { Test-Path $_ } | Select-Object -First 1

if ($req) {
  Write-Host "Installing from pinned requirements: $req (kokoro pulls torch if not present)"
  & $venvPy -m pip install -r $req
} else {
  Write-Host "Installing kokoro>=0.9.4 + soundfile>=0.12.1..."
  & $venvPy -m pip install "kokoro>=0.9.4" "soundfile>=0.12.1"
}
if ($LASTEXITCODE -ne 0) { Write-Error "kokoro/soundfile install failed"; exit 1 }

Write-Host "Verifying kokoro imports..."
& $venvPy -c "from kokoro import KPipeline; import soundfile; print('kokoro OK')"
if ($LASTEXITCODE -ne 0) { Write-Error "kokoro installed but not importable"; exit 1 }

Write-Host ""
Write-Host "=============================================================="
Write-Host " TTS sidecar venv ready."
Write-Host " Set this as 'tts_sidecar_python' in Settings (advanced),"
Write-Host " or export TTS_SIDECAR_PYTHON, then set Speech provider = Kokoro:"
Write-Host ""
Write-Host "   $venvPy"
Write-Host "=============================================================="
