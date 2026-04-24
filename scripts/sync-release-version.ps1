#Requires -Version 5.1
<#
.SYNOPSIS
  Set the same semantic version on API (pyproject.toml), web, and Electron package.json.

.PARAMETER Version
  e.g. 1.0.1

.EXAMPLE
  .\scripts\sync-release-version.ps1 -Version 1.0.1
#>
param(
    [Parameter(Mandatory = $true)]
    [ValidatePattern('^\d+\.\d+\.\d+(-[a-zA-Z0-9.-]+)?$')]
    [string]$Version
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$Py = Join-Path $PSScriptRoot "sync_release_version.py"

& python $Py $Version $Root
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
Write-Host "Done." -ForegroundColor Green
