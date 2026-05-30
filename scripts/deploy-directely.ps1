# Deploy latest main to directely.com VPS (run from repo root on Windows).
# Prereq: SSH works, e.g.  ssh directely  "hostname"
#
# Usage:
#   $env:DIRECTOR_DEPLOY_HOST = "directely"   # SSH config Host name, or user@ip
#   .\scripts\deploy-directely.ps1
#
# Optional:
#   $env:DIRECTOR_REPO = "/root/director"     # default /root/director

param(
  [string]$Host = $env:DIRECTOR_DEPLOY_HOST,
  [string]$Repo = $(if ($env:DIRECTOR_REPO) { $env:DIRECTOR_REPO } else { "/root/director" })
)

if (-not $Host) {
  Write-Error @"
Set DIRECTOR_DEPLOY_HOST to your SSH target (SSH config Host name or user@server).

Example ~/.ssh/config:

  Host directely
    HostName 187.77.16.73
    User root
    IdentityFile ~/.ssh/id_ed25519

Then: `$env:DIRECTOR_DEPLOY_HOST = 'directely'; .\scripts\deploy-directely.ps1
"@
  exit 1
}

$cmd = "cd $(($Repo -replace '"','\"')) && ./scripts/server-major-update.sh"
Write-Host "==> SSH $Host : $cmd"
ssh $Host $cmd
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
Write-Host "==> Done. Check https://directely.com (view source: new index-*.js bundle)."
