#Requires -Version 5.1
<#
  Creates a Desktop shortcut with a play-style icon that runs Launch.cmd.
  Run once:  powershell -ExecutionPolicy Bypass -File scripts\install-director-shortcut.ps1
#>
$ErrorActionPreference = "Stop"
$RepoRoot = Split-Path -Parent $PSScriptRoot
$Bat = Join-Path $RepoRoot "Launch.cmd"
if (-not (Test-Path -LiteralPath $Bat)) {
    throw "Missing Launch.cmd at $Bat"
}

# imageres.dll ~102: generic media/gallery-style glyph (varies by Windows build)
$Icon = "$env:SystemRoot\System32\imageres.dll,102"
$Desktop = [Environment]::GetFolderPath("Desktop")
$LnkPath = Join-Path $Desktop "Directely Studio.lnk"

$Wsh = New-Object -ComObject WScript.Shell
$Sc = $Wsh.CreateShortcut($LnkPath)
$Sc.TargetPath = $Bat
$Sc.WorkingDirectory = $RepoRoot
$Sc.Description = "Directely: Docker Compose, API, Celery, Vite web UI"
$Sc.IconLocation = $Icon
$Sc.Save()

Write-Host "Shortcut created: $LnkPath" -ForegroundColor Green
Write-Host "You can pin it to the taskbar or Start menu from the shortcut's context menu." -ForegroundColor Gray
