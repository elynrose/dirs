<#
.SYNOPSIS
  Queue a ComfyUI workflow via POST /prompt (API-format workflow JSON on disk).

.PARAMETER WorkflowPath
  Path to JSON exported from ComfyUI as the API graph only (the object whose keys are node ids like "3","5",...).

.PARAMETER BaseUrl
  ComfyUI root URL, default http://127.0.0.1:8188

.PARAMETER PollHistory
  After queueing, GET /history/{prompt_id} once and print.

.EXAMPLE
  .\comfyui-queue-workflow.ps1 -WorkflowPath "C:\temp\text_to_video_wan_api.json"
#>
param(
  [Parameter(Mandatory = $true)]
  [string] $WorkflowPath,
  [string] $BaseUrl = "http://127.0.0.1:8188",
  [switch] $PollHistory
)

$BaseUrl = $BaseUrl.TrimEnd("/")
if (-not (Test-Path -LiteralPath $WorkflowPath)) {
  throw "Workflow file not found: $WorkflowPath"
}

$wf = Get-Content -LiteralPath $WorkflowPath -Raw -Encoding UTF8
if (-not $wf.Trim()) { throw "Workflow file is empty." }

# Validate JSON (optional sanity check)
try {
  $null = $wf | ConvertFrom-Json
} catch {
  throw "Workflow file is not valid JSON: $_"
}

$clientId = [guid]::NewGuid().ToString()
# Body: { "client_id": "...", "prompt": <contents of file> }
$body = "{`"client_id`":`"$clientId`",`"prompt`":$wf}"

try {
  $resp = Invoke-RestMethod -Uri "$BaseUrl/prompt" -Method Post -Body $body -ContentType "application/json; charset=utf-8"
} catch {
  Write-Error "POST /prompt failed: $_"
  if ($_.Exception.Response) {
    $reader = [System.IO.StreamReader]::new($_.Exception.Response.GetResponseStream())
    Write-Host ($reader.ReadToEnd())
  }
  exit 1
}

Write-Host "client_id: $clientId"
$resp | Format-List

$promptId = $resp.prompt_id
if (-not $promptId) {
  Write-Warning "No prompt_id in response (check node_errors in response object)."
  exit 2
}

if ($PollHistory) {
  Start-Sleep -Seconds 2
  try {
    $hist = Invoke-RestMethod -Uri "$BaseUrl/history/$promptId" -Method Get
    $hist | ConvertTo-Json -Depth 20
  } catch {
    Write-Warning "GET /history failed (run may still be in queue): $_"
  }
}