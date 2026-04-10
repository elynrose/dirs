# Dot-source only — shared helpers to stop local Director API, Celery, and Vite listeners.
# Used by restart-local.ps1 and stop-director.ps1.

function Invoke-TaskKillTree {
    param([int]$ProcessId)
    # Avoid NativeCommandError when the PID is already gone (race with port scan / parent exit).
    $null = cmd.exe /c "taskkill /F /T /PID $ProcessId >nul 2>&1"
}

function Stop-AllListenersOnPort {
    param([int]$Port)
    for ($round = 0; $round -lt 16; $round++) {
        $pids = @()
        try {
            $conns = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
            foreach ($c in $conns) {
                if ($c.OwningProcess -and $c.OwningProcess -ne 0) { $pids += [int]$c.OwningProcess }
            }
        } catch { }
        $pids = $pids | Sort-Object -Unique
        if ($pids.Count -eq 0) { break }
        foreach ($procId in $pids) {
            Write-Host "director-stop: stopping PID $procId (tree) on port $Port"
            # /T terminates child processes (node/vite/esbuild under PowerShell, etc.)
            Invoke-TaskKillTree -ProcessId $procId
            Start-Sleep -Milliseconds 350
        }
        Start-Sleep -Milliseconds 450
    }
}

function Stop-DirectorApiPythonProcesses {
    try {
        $list = @(Get-CimInstance Win32_Process -ErrorAction SilentlyContinue |
            Where-Object {
                $_.CommandLine -and (
                    ($_.CommandLine -match "python(\.exe)?\s+-m\s+director_api") -or
                    ($_.CommandLine -match "uvicorn.*director_api\.main:app")
                )
            })
    } catch {
        Write-Host "director-stop: could not enumerate API python processes (continuing)" -ForegroundColor Yellow
        return
    }
    foreach ($p in $list) {
        Write-Host "director-stop: stopping Director API python PID $($p.ProcessId) (tree)"
        Invoke-TaskKillTree -ProcessId $p.ProcessId
    }
}

function Stop-CeleryDirectorWorkers {
    try {
        $list = @(Get-CimInstance Win32_Process -ErrorAction SilentlyContinue |
            Where-Object { $_.CommandLine -and ($_.CommandLine -match "director_api\.tasks\.celery_app") })
    } catch {
        Write-Host "director-stop: could not enumerate Celery processes (continuing)" -ForegroundColor Yellow
        return
    }
    foreach ($p in $list) {
        Write-Host "director-stop: stopping Celery PID $($p.ProcessId) (tree)"
        Invoke-TaskKillTree -ProcessId $p.ProcessId
    }
}

function Stop-CeleryDirectorWorkerProcessOnly {
    <#
      Stop the Celery *worker* process only (not beat, not PowerShell launcher windows).
      Launcher shells embed "worker" in -Command text and must not be matched alone.
    #>
    try {
        $list = @(Get-CimInstance Win32_Process -ErrorAction SilentlyContinue |
            Where-Object {
                $_.CommandLine -and
                ($_.Name -match '^(python|celery)\.exe$') -and
                ($_.CommandLine -match "director_api\.tasks\.celery_app") -and
                ($_.CommandLine -match "\sworker\s") -and
                ($_.CommandLine -notmatch "\sbeat\s")
            })
    } catch {
        Write-Host "director-stop: could not enumerate Celery worker processes (continuing)" -ForegroundColor Yellow
        return
    }
    foreach ($p in $list) {
        Write-Host "director-stop: stopping Celery worker PID $($p.ProcessId) (tree)"
        Invoke-TaskKillTree -ProcessId $p.ProcessId
    }
}

function Stop-ViteNodeProcesses {
    <#
      Vite often shows as node.exe; port kill may miss orphans or leave children.
      Match dev server invocations under apps/web.
    #>
    try {
        $list = @(Get-CimInstance Win32_Process -ErrorAction SilentlyContinue |
            Where-Object {
                $_.CommandLine -and (
                    ($_.Name -match '^node\.exe$' -and $_.CommandLine -match "vite") -or
                    ($_.CommandLine -match "apps[\\/]web" -and $_.CommandLine -match "npm(\.cmd)?\s+run\s+dev")
                )
            })
    } catch {
        Write-Host "director-stop: could not enumerate Vite node processes (continuing)" -ForegroundColor Yellow
        return
    }
    foreach ($p in $list) {
        Write-Host "director-stop: stopping Vite/node PID $($p.ProcessId) (tree)"
        Invoke-TaskKillTree -ProcessId $p.ProcessId
    }
}

function Stop-DirectorLauncherShells {
    <#
      Minimized PowerShell windows from Launch.ps1 / restart-local wrap celery & API;
      killing only the worker PID can leave the parent shell or a stale wrapper.
    #>
    try {
        $list = @(Get-CimInstance Win32_Process -ErrorAction SilentlyContinue |
            Where-Object {
                $_.CommandLine -and (
                    ($_.Name -match '^(powershell|pwsh)\.exe$') -and (
                        ($_.CommandLine -match "celery\s+-A\s+director_api\.tasks\.celery_app") -or
                        ($_.CommandLine -match "python(\.exe)?\s+-m\s+director_api") -or
                        ($_.CommandLine -match "npm(\.cmd)?\s+run\s+dev" -and $_.CommandLine -match "apps[\\/]web")
                    )
                )
            })
    } catch {
        Write-Host "director-stop: could not enumerate launcher shells (continuing)" -ForegroundColor Yellow
        return
    }
    foreach ($p in $list) {
        Write-Host "director-stop: stopping launcher shell PID $($p.ProcessId) (tree)"
        Invoke-TaskKillTree -ProcessId $p.ProcessId
    }
}

function Stop-DirectorLocalDev {
    param(
        [string]$RepoRoot,
        [int]$ApiPort = 8000,
        [switch]$IncludeVite
    )
    if ($IncludeVite) {
        Stop-ViteNodeProcesses
        Stop-AllListenersOnPort -Port 5173
        Stop-AllListenersOnPort -Port 4173
    }
    Stop-DirectorLauncherShells
    Stop-AllListenersOnPort -Port $ApiPort
    Stop-DirectorApiPythonProcesses
    Stop-CeleryDirectorWorkers
    Stop-AllListenersOnPort -Port $ApiPort
    Stop-CeleryDirectorWorkers
    Stop-DirectorApiPythonProcesses
    if ($IncludeVite) {
        Stop-ViteNodeProcesses
        Stop-AllListenersOnPort -Port 5173
        Stop-AllListenersOnPort -Port 4173
    }
    Start-Sleep -Seconds 1
}

function Read-DirectorApiPortFromRepoEnv {
    param([string]$RepoRoot)
    $port = 8000
    if ($env:API_PORT) {
        try { $port = [int]$env:API_PORT } catch { }
        return $port
    }
    $ef = Join-Path $RepoRoot ".env"
    if (-not (Test-Path -LiteralPath $ef)) { return $port }
    foreach ($line in Get-Content -LiteralPath $ef -ErrorAction SilentlyContinue) {
        if ($line -match '^\s*API_PORT\s*=\s*(\d+)\s*$') {
            try { $port = [int]$Matches[1] } catch { }
            break
        }
    }
    return $port
}
