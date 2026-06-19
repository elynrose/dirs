#Requires -Version 5.1
param(
    [Parameter(Mandatory = $true)][string]$ProjectId,
    [string]$ApiBase = "http://127.0.0.1:8000",
    [int]$IntervalSec = 15,
    [int]$MaxMinutes = 120
)
$log = Join-Path (Split-Path -Parent $PSScriptRoot) ".run\monitor-$ProjectId.log"
New-Item -ItemType Directory -Force -Path (Split-Path $log) | Out-Null
function Log($msg) {
    $line = "[$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')] $msg"
    Add-Content -Path $log -Value $line
    Write-Host $line
}
Log "monitor start project=$ProjectId"
$deadline = (Get-Date).AddMinutes($MaxMinutes)
$lastPhase = ""
while ((Get-Date) -lt $deadline) {
    try {
        $p = Invoke-RestMethod -Uri "$ApiBase/v1/projects/$ProjectId" -TimeoutSec 20
        $phase = $p.data.workflow_phase
        $pipe = Invoke-RestMethod -Uri "$ApiBase/v1/projects/$ProjectId/pipeline-status" -TimeoutSec 20
        $scenes = $pipe.data.scene_count
        $jobs = (Invoke-RestMethod -Uri "$ApiBase/v1/projects/$ProjectId/jobs/active" -TimeoutSec 20).data.count
        $runLine = "phase=$phase scenes=$scenes active_jobs=$jobs"
        if ($phase -ne $lastPhase) {
            Log "CHANGE $runLine"
            $lastPhase = $phase
        } else {
            Log $runLine
        }
        if ($phase -in @("final_video_ready", "critique_complete")) {
            Log "DONE pipeline reached $phase"
            break
        }
    } catch {
        Log "ERROR $($_.Exception.Message)"
    }
    Start-Sleep -Seconds $IntervalSec
}
Log "monitor end"
