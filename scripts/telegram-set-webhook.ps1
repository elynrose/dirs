# Point Telegram at your public Director API webhook (HTTPS required).
#
# Usage (from repo root):
#   $env:TELEGRAM_BOT_TOKEN = "123:ABC..."
#   $env:TELEGRAM_WEBHOOK_SECRET = "same-long-secret-as-Studio-settings"
#   .\scripts\telegram-set-webhook.ps1 https://YOUR_SUBDOMAIN.ngrok-free.app
#   # production:
#   .\scripts\telegram-set-webhook.ps1 https://directely.com

param(
    [Parameter(Mandatory = $true, Position = 0)]
    [string]$PublicBase
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$EnvFile = Join-Path $Root ".env"
if (Test-Path -LiteralPath $EnvFile) {
    Get-Content -LiteralPath $EnvFile | ForEach-Object {
        if ($_ -match '^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)$' -and $_ -notmatch '^\s*#') {
            $name = $Matches[1]
            $val = $Matches[2].Trim().Trim('"').Trim("'")
            if (-not [string]::IsNullOrWhiteSpace($val)) { Set-Item -Path "env:$name" -Value $val }
        }
    }
}

$Token = $env:TELEGRAM_BOT_TOKEN
$Secret = $env:TELEGRAM_WEBHOOK_SECRET
if (-not $Token) { throw "Set TELEGRAM_BOT_TOKEN (Studio bot token or env)" }
if (-not $Secret) { throw "Set TELEGRAM_WEBHOOK_SECRET (Studio webhook secret or env)" }

$Base = $PublicBase.TrimEnd("/")
$Url = "$Base/v1/integrations/telegram/webhook"
Write-Host "Setting webhook: $Url"

$body = @{
    url          = $Url
    secret_token = $Secret
}
$r = Invoke-RestMethod -Method Post -Uri "https://api.telegram.org/bot$Token/setWebhook" -Body $body
$r | ConvertTo-Json -Compress
