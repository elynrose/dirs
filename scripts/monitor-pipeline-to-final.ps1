#Requires -Version 5.1
param(
    [Parameter(Mandatory = $true)][string]$ProjectId,
    [string]$RunId = "",
    [string]$ApiBase = "http://127.0.0.1:8000",
    [int]$IntervalSec = 30,
    [int]$MaxMinutes = 120
)
$log = Join-Path (Split-Path -Parent $PSScriptRoot) ".run\monitor-final-$ProjectId.log"
New-Item -ItemType Directory -Force -Path (Split-Path $log) | Out-Null
function Log($msg) {
    $line = "[$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')] $msg"
    Add-Content -Path $log -Value $line
    Write-Host $line
}
Log "monitor-to-final-cut project=$ProjectId run=$RunId"
$deadline = (Get-Date).AddMinutes($MaxMinutes)
$lastLine = ""
while ((Get-Date) -lt $deadline) {
    try {
        $pipe = Invoke-RestMethod -Uri "$ApiBase/v1/projects/$ProjectId/pipeline-status" -TimeoutSec 30
        $d = $pipe.data
        $finalStep = ($d.steps | Where-Object { $_.id -eq "final_cut" } | Select-Object -First 1)
        $roughStep = ($d.steps | Where-Object { $_.id -eq "rough_cut" } | Select-Object -First 1)
        $runLine = ""
        if ($RunId) {
            $run = Invoke-RestMethod -Uri "$ApiBase/v1/agent-runs/$RunId" -TimeoutSec 20
            $ar = $run.data
            $runLine = " run=$($ar.status)/$($ar.current_step)"
        }
        $jobs = (Invoke-RestMethod -Uri "$ApiBase/v1/projects/$ProjectId/jobs/active" -TimeoutSec 20).data.count
        $line = "phase=$($d.workflow_phase) scenes=$($d.scene_count) jobs=$jobs rough=$($roughStep.status) final=$($finalStep.status)$runLine"
        if ($line -ne $lastLine) { Log $line; $lastLine = $line }
        if ($finalStep.status -eq "done") {
            Log "DONE final_cut step complete phase=$($d.workflow_phase)"
            break
        }
        if ($RunId -and $run.data.status -in @("succeeded","failed","cancelled","blocked")) {
            Log "agent_run terminal status=$($run.data.status) step=$($run.data.current_step)"
            if ($run.data.status -ne "succeeded") { break }
        }
    } catch {
        Log "ERROR $($_.Exception.Message)"
    }
    Start-Sleep -Seconds $IntervalSec
}
Log "monitor end"
