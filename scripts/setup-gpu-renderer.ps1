<#
.SYNOPSIS
  Create the CUDA sidecar venv used by the "GPU" Ken Burns renderer (Settings > Generation >
  Ken Burns motion = GPU).

.DESCRIPTION
  The Director worker runs on Python 3.14, which has no CUDA PyTorch wheels, so GPU frame warping
  runs out-of-process in a dedicated Python 3.11 venv with torch (CUDA) + numpy. This script
  creates that venv and installs the dependencies, then prints the python.exe path to paste into
  Settings > API keys (advanced) as `gpu_still_motion_python` (or the GPU_STILL_MOTION_PYTHON env).

.PARAMETER Cuda
  CUDA wheel tag for the torch index, e.g. cu124 (default) or cu121.

.PARAMETER VenvPath
  Where to create the venv. Defaults to %APPDATA%\director-electron\gpu-renderer-venv.
#>
param(
  [string]$Cuda = "cu124",
  [string]$VenvPath = "$env:APPDATA\director-electron\gpu-renderer-venv"
)

$ErrorActionPreference = "Stop"

function Find-Python311 {
  # Prefer the Windows launcher, then a bare python3.11 on PATH.
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

# Locate the pinned requirements file (repo checkout or installed wheel).
$reqCandidates = @(
  (Join-Path $PSScriptRoot "..\apps\api\director_api\gpu\requirements-gpu.txt")
)
$req = $reqCandidates | Where-Object { Test-Path $_ } | Select-Object -First 1
$torchPin = "torch"
$numpyPin = "numpy"
if ($req) {
  Write-Host "Using pinned requirements: $req"
  $lines = Get-Content $req | Where-Object { $_ -and -not $_.TrimStart().StartsWith("#") }
  $t = $lines | Where-Object { $_ -match '^\s*torch' } | Select-Object -First 1
  $n = $lines | Where-Object { $_ -match '^\s*numpy' } | Select-Object -First 1
  if ($t) { $torchPin = $t.Trim() }
  if ($n) { $numpyPin = $n.Trim() }
}

Write-Host "Installing $torchPin ($Cuda) + $numpyPin... (this downloads ~2.5 GB)"
& $venvPy -m pip install --index-url "https://download.pytorch.org/whl/$Cuda" $torchPin
if ($LASTEXITCODE -ne 0) { Write-Error "torch install failed for $Cuda"; exit 1 }
& $venvPy -m pip install $numpyPin
if ($LASTEXITCODE -ne 0) { Write-Error "numpy install failed"; exit 1 }

Write-Host "Verifying CUDA is visible to torch..."
& $venvPy -c "import torch;assert torch.cuda.is_available(), 'CUDA not available'; print('CUDA OK:', torch.cuda.get_device_name(0))"
if ($LASTEXITCODE -ne 0) {
  Write-Warning "torch installed but CUDA is not available. Check your NVIDIA driver."
}

Write-Host ""
Write-Host "=============================================================="
Write-Host " GPU renderer venv ready."
Write-Host " Set this as 'gpu_still_motion_python' in Settings (advanced),"
Write-Host " or export GPU_STILL_MOTION_PYTHON, then set Ken Burns motion = GPU:"
Write-Host ""
Write-Host "   $venvPy"
Write-Host "=============================================================="
