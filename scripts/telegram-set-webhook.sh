#!/usr/bin/env bash
# Point Telegram at your public Director API webhook. Requires HTTPS (production or tunnel).
#
# Usage:
#   export TELEGRAM_BOT_TOKEN="123:ABC..."
#   export TELEGRAM_WEBHOOK_SECRET="same-long-secret-as-Studio-settings"
#   ./scripts/telegram-set-webhook.sh https://directely.com
#   # or (local dev):
#   ./scripts/telegram-set-webhook.sh https://YOUR_SUBDOMAIN.ngrok-free.app
#
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
if [[ -f "$ROOT/.env" ]]; then
  set -a
  # shellcheck disable=SC1091
  source "$ROOT/.env"
  set +a
fi
BASE="${1:?Usage: $0 https://YOUR_PUBLIC_HOST (no trailing slash)}"
TOKEN="${TELEGRAM_BOT_TOKEN:?Set TELEGRAM_BOT_TOKEN}"
SECRET="${TELEGRAM_WEBHOOK_SECRET:?Set TELEGRAM_WEBHOOK_SECRET}"
BASE="${BASE%/}"
URL="${BASE}/v1/integrations/telegram/webhook"
echo "Setting webhook: $URL"
curl -sS -X POST "https://api.telegram.org/bot${TOKEN}/setWebhook" \
  --data-urlencode "url=${URL}" \
  --data-urlencode "secret_token=${SECRET}"
echo
