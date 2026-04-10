#Requires -Version 5.1
<#
  Create a Joshua / Jericho project: Phase 2 (scripts) + Phase 3 scene planning (per chapter).
  Requires: API + Celery worker (or CELERY_EAGER on the API process), DB/Redis up.
  Text LLM: OpenAI-compatible base URL (e.g. LM Studio) — same stack as Phase 2 (phase3_llm uses shared JSON chat).
  Web research: Tavily if set, else Wikipedia OpenSearch.
  Scene images/video are NOT run here; set project/image provider separately (e.g. FAL) or generate in Studio.

  Usage:
    powershell -ExecutionPolicy Bypass -File scripts\smoke-joshua-jericho.ps1
    powershell -ExecutionPolicy Bypass -File scripts\smoke-joshua-jericho.ps1 -SkipScenePlanning
    powershell -ExecutionPolicy Bypass -File scripts\smoke-joshua-jericho.ps1 -BaseUrl "http://127.0.0.1:8000/v1"
#>
[CmdletBinding()]
param(
    [string]$BaseUrl = "http://127.0.0.1:8000/v1",
    [switch]$SkipScenePlanning
)

$ErrorActionPreference = "Stop"

function Invoke-DirJson {
    param(
        [string]$Method,
        [string]$Path,
        [object]$Body = $null,
        [hashtable]$Headers = @{}
    )
    $uri = "$BaseUrl$Path"
    $h = @{ "Content-Type" = "application/json; charset=utf-8" } + $Headers
    $utf8 = New-Object System.Text.UTF8Encoding $false
    if ($null -eq $Body) {
        $r = Invoke-RestMethod -Uri $uri -Method $Method -Headers $h -TimeoutSec 600
    } elseif ($Body -is [string]) {
        $bytes = $utf8.GetBytes($Body)
        $r = Invoke-RestMethod -Uri $uri -Method $Method -Headers $h -Body $bytes -TimeoutSec 600
    } else {
        $json = $Body | ConvertTo-Json -Depth 20 -Compress
        $bytes = $utf8.GetBytes($json)
        $r = Invoke-RestMethod -Uri $uri -Method $Method -Headers $h -Body $bytes -TimeoutSec 600
    }
    return $r
}

function Wait-DirectorJob {
    param(
        [string]$JobId,
        [int]$TimeoutMinutes = 45
    )
    $deadline = (Get-Date).AddMinutes($TimeoutMinutes)
    while ((Get-Date) -lt $deadline) {
        $j = Invoke-DirJson -Method GET -Path "/jobs/$JobId"
        $st = $j.data.status
        Write-Host "  job $JobId -> $st"
        if ($st -eq "succeeded") { return $j.data }
        if ($st -eq "failed" -or $st -eq "cancelled") {
            $err = $j.data.error_message
            throw "Job $JobId ended with status=$st error=$err"
        }
        Start-Sleep -Seconds 3
    }
    throw "Timeout waiting for job $JobId"
}

Write-Host "=== Director: Joshua / Jericho smoke ===" -ForegroundColor Cyan

# Explicit JSON avoids PowerShell hashtable / here-string quirks with newlines in topic.
$createJson = @'
{
  "title": "Joshua and the Walls of Jericho",
  "topic": "A documentary-style program on the biblical account of Joshua, the Israelites at Jericho, the march around the city, and the fall of its walls - narrative, archaeological and historiographical perspectives, and how traditions interpret the story. Keep narration measured: separate attested archaeology from narrative and debate.",
  "target_runtime_minutes": 18,
  "audience": "General adult; curious about history and religion",
  "tone": "Measured, clear, documentary narration",
  "narration_style": "preset:narrative_documentary",
  "factual_strictness": "balanced",
  "research_min_sources": 2
}
'@

Write-Host "1) POST /projects" -ForegroundColor Yellow
$proj = Invoke-DirJson -Method POST -Path "/projects" -Body $createJson.Trim()
$projectId = $proj.data.id
Write-Host "   project_id=$projectId"

Write-Host "2) POST /projects/{id}/start (director pack + optional LLM enrich)" -ForegroundColor Yellow
Invoke-DirJson -Method POST -Path "/projects/$projectId/start" | Out-Null

$idk1 = [guid]::NewGuid().ToString()
Write-Host "3) POST /projects/{id}/research/run" -ForegroundColor Yellow
$res = Invoke-DirJson -Method POST -Path "/projects/$projectId/research/run" -Body @{} -Headers @{ "Idempotency-Key" = $idk1 }
$job1 = $res.job.id
Wait-DirectorJob -JobId $job1

Write-Host "4) POST /projects/{id}/research/approve" -ForegroundColor Yellow
Invoke-DirJson -Method POST -Path "/projects/$projectId/research/approve" -Body @{ notes = "smoke approve" } | Out-Null

$idk2 = [guid]::NewGuid().ToString()
Write-Host "5) POST /projects/{id}/script/generate-outline" -ForegroundColor Yellow
$res2 = Invoke-DirJson -Method POST -Path "/projects/$projectId/script/generate-outline" -Body @{} -Headers @{ "Idempotency-Key" = $idk2 }
$job2 = $res2.job.id
Wait-DirectorJob -JobId $job2

$idk3 = [guid]::NewGuid().ToString()
Write-Host "6) POST /projects/{id}/script/generate-chapters" -ForegroundColor Yellow
$res3 = Invoke-DirJson -Method POST -Path "/projects/$projectId/script/generate-chapters" -Body @{} -Headers @{ "Idempotency-Key" = $idk3 }
$job3 = $res3.job.id
Wait-DirectorJob -JobId $job3

Write-Host "7) GET /projects/{id}/chapters (summary)" -ForegroundColor Yellow
$ch = Invoke-DirJson -Method GET -Path "/projects/$projectId/chapters"
$chapterRows = @()
if ($ch.data -and $ch.data.chapters) { $chapterRows = @($ch.data.chapters) }
$n = $chapterRows.Count
Write-Host "   chapters count: $n"

if (-not $SkipScenePlanning) {
    Write-Host "8) POST /chapters/{id}/scenes/generate per chapter (local text LLM refines scene plan)" -ForegroundColor Yellow
    $sorted = $chapterRows | Sort-Object { [int]($_.order_index) }
    foreach ($row in $sorted) {
        $cid = $row.id
        Write-Host "   planning scenes for chapter $cid (order_index=$($row.order_index))..."
        $idkSc = [guid]::NewGuid().ToString()
        $resSc = Invoke-DirJson -Method POST -Path "/chapters/$cid/scenes/generate" `
            -Body @{ replace_existing_scenes = $false } `
            -Headers @{ "Idempotency-Key" = $idkSc }
        Wait-DirectorJob -JobId $resSc.job.id -TimeoutMinutes 25
    }

    Write-Host "9) GET /chapters/{id}/scenes — counts" -ForegroundColor Yellow
    $totalScenes = 0
    foreach ($row in $sorted) {
        $cid = $row.id
        $sc = Invoke-DirJson -Method GET -Path "/chapters/$cid/scenes"
        $nc = 0
        if ($sc.data -and $sc.data.scenes) { $nc = @($sc.data.scenes).Count }
        $totalScenes += $nc
        Write-Host "   chapter $cid -> $nc scenes"
    }
    Write-Host "   total scenes: $totalScenes"
}

Write-Host "=== Done. Open Studio with project id: $projectId ===" -ForegroundColor Green
