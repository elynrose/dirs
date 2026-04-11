# Linux server installation

This note covers deploying Directely on a **Linux** host with **Docker** (Postgres, Redis, MinIO), a **Python 3.11+** virtualenv for the API and Celery, and optional **systemd** units so everything survives reboots.

For day-to-day commands (`make up`, `make api`, etc.), see the root [`README.md`](README.md).

## Requirements

- **Docker** and **Docker Compose** plugin (`docker compose`)
- **Python 3.11+** (3.12 is fine) and `python3-venv` (Debian/Ubuntu: `apt install python3.12-venv`)
- **FFmpeg** and **ffprobe** on the host (compile/export jobs)
- **`make`** if you use the Makefile targets
- **Node.js 18+** and **npm** if you use the optional **Vite** systemd service (`director-vite.service`)
- Enough disk for Postgres/Redis volumes and `LOCAL_STORAGE_ROOT` (defaults under `data/storage` in the repo)

## 1. Get the code on the server

Clone or copy the repository to a fixed path (examples below use `/opt/director`; adjust if you use another directory).

```bash
sudo mkdir -p /opt && sudo chown "$USER:$USER" /opt   # if needed
git clone <your-repo-url> /opt/director
cd /opt/director
```

If you copy from Windows, ensure shell scripts use **LF** line endings (not CRLF), or systemd may fail with status `203/EXEC`.

If you copy `apps/web/node_modules` from Windows, **delete it on the server** and reinstall so Rollup/Esbuild fetch **Linux** native binaries:

```bash
cd apps/web && rm -rf node_modules && npm install && cd ../..
```

## 2. Environment file

```bash
cp .env.example .env
```

Edit `.env`:

- Set **`DATABASE_URL`**, **`REDIS_URL`**, and ports to match `docker-compose.yml` (default Postgres is mapped to host port **5433**).
- Do **not** use angle brackets in values (e.g. `<jwt>`) — they break `bash` when sourcing `.env`. Use real tokens or plain placeholders like `replace_with_jwt`.
- Never commit real secrets. Use strong **`DIRECTOR_JWT_SECRET`** in production.

## 3. Infrastructure (Docker)

```bash
docker compose up -d
```

Postgres (**5433**), Redis (**6379**), MinIO (**9000** / console **9001**) should be running. The compose file sets `restart: unless-stopped` so these containers come back after a host reboot.

## 4. Python virtualenv and migrations

Remove any **Windows** virtualenv under `apps/api` (folders named `Scripts` / `Lib`); recreate on Linux:

```bash
cd apps/api
rm -rf .venv
python3 -m venv .venv
.venv/bin/pip install -U pip wheel
.venv/bin/pip install -e ".[dev]"
cd ../..
make migrate
```

## 5. Smoke test (manual processes)

From the repo root:

```bash
make api       # terminal 1 — API on API_PORT (default 8000)
make worker    # terminal 2
# terminal 3 — Celery beat (needed for scheduled tasks such as stale-job reaping):
cd apps/api && .venv/bin/celery -A director_api.tasks.celery_app beat -l info
```

Check health:

```bash
curl -sS "http://127.0.0.1:${API_PORT:-8000}/v1/health"
```

Stop manual runs with Ctrl+C when done.

## 6. systemd (production-style, boot persistence)

The repo ships helpers under [`scripts/systemd/`](scripts/systemd/):

- **`env.sh`**, **`wait-for-postgres.sh`**, **`run-api.sh`**, **`run-worker.sh`**, **`run-beat.sh`**, **`run-vite.sh`**
- Unit templates: **`director-infra.service`**, **`director-api.service`**, **`director-worker.service`**, **`director-beat.service`**, **`director-vite.service`** (paths default to **`/root/director`** and **`User=root`**)

If the repo lives elsewhere (e.g. `/opt/director`), substitute the path when copying units:

```bash
cd /opt/director
REPO=/opt/director
for f in director-infra director-api director-worker director-beat director-vite; do
  sed "s|/root/director|$REPO|g" "scripts/systemd/$f.service" | sudo tee "/etc/systemd/system/$f.service" >/dev/null
done
```

Install when the repo is already at `/root/director`:

```bash
cd /root/director
sudo cp scripts/systemd/director-*.service /etc/systemd/system/
sudo chmod +x scripts/systemd/*.sh
sudo systemctl daemon-reload
sudo systemctl enable director-infra.service director-api.service director-worker.service director-beat.service director-vite.service
sudo systemctl start director-infra.service
sudo systemctl start director-api.service director-worker.service director-beat.service director-vite.service
```

Ensure **`apps/web/node_modules`** was installed on Linux (see above) before enabling **`director-vite`**.

Units:

| Unit | Role |
| ---- | ---- |
| `director-infra.service` | `docker compose up -d` (Postgres, Redis, MinIO) |
| `director-api.service` | FastAPI / Uvicorn |
| `director-worker.service` | Celery worker |
| `director-beat.service` | Celery beat |
| `director-vite.service` | Web UI — Vite dev server on **0.0.0.0:5173** (proxies `/v1` to the API) |

Logs:

```bash
journalctl -u director-api -f
journalctl -u director-worker -f
journalctl -u director-beat -f
journalctl -u director-vite -f
```

Restart after code or `.env` changes:

```bash
sudo systemctl restart director-api director-worker director-beat
# After changing only the web app:
sudo systemctl restart director-vite
```

The infra unit does **not** run `docker compose down` on stop, so volumes are preserved. To tear down containers:

```bash
cd /path/to/director && docker compose down
```

## 7. Firewall and HTTPS

- Prefer **not** exposing Postgres, Redis, MinIO, or the raw API port to the public internet.
- Put **nginx** or **Caddy** in front on **80/443**, proxy to `127.0.0.1:8000`, and use TLS (e.g. Let’s Encrypt).

### Custom domain (e.g. `directely.com`)

1. **DNS at your registrar (Hostinger)**  
   Point the domain to **this server’s public IPv4**:
   - **A** record `@` → `YOUR_SERVER_IP`
   - **A** or **CNAME** for `www` → same host (or CNAME `www` → `directely.com.` depending on the panel)  
   Wait until `dig +short directely.com A` shows that IP (propagation can take minutes to hours).

2. **Web build** (same-origin `/v1` — do **not** set `VITE_API_BASE_URL` unless the API is on another hostname):

   ```bash
   cd apps/web && npm ci && npm run build
   ```

3. **Publish static files** — nginx runs as `www-data` and **cannot read** paths under `/root` (you would get HTTP 500). Copy the build to e.g. `/var/www/directely`:

   ```bash
   sudo mkdir -p /var/www/directely
   sudo rsync -a --delete apps/web/dist/ /var/www/directely/
   sudo chown -R www-data:www-data /var/www/directely
   ```

4. **nginx** — example site file: [`scripts/nginx/directely.com.conf`](scripts/nginx/directely.com.conf). It serves `/var/www/directely` and proxies `/v1/` to `127.0.0.1:8000`.  
   Install: copy to `/etc/nginx/sites-available/`, enable under `sites-enabled`, `sudo nginx -t && sudo systemctl restart nginx` (use **restart** after changing `root`, not only `reload`, if workers keep old config).

5. **TLS** (after DNS is correct):

   ```bash
   sudo certbot --nginx -d directely.com -d www.directely.com
   ```

6. **Optional:** In repo `.env`, `CORS_EXTRA_ORIGINS` can include `https://directely.com` and `https://www.directely.com` if the UI ever calls the API from another origin. Same-origin via nginx usually does not need extra CORS.

7. **Firebase / Google sign-in:** Add `directely.com` (and `www` if used) under Firebase **Authorized domains**.

## 8. Web UI (Vite)

The Studio lives in **`apps/web`** (Vite + React). With **`director-vite.service`** enabled, the dev server listens on **port 5173** on all interfaces (`--host 0.0.0.0`). Open **`http://YOUR_SERVER:5173`** in a browser (open the port in your firewall if needed).

For a static production build behind nginx/Caddy, use `npm run build` in `apps/web` and serve `dist/`; you will need to proxy **`/v1`** to the API (see `vite.config.js` for dev proxy behaviour).

## 9. Telegram bot (webhook)

Directely receives Bot API updates at **`POST /v1/integrations/telegram/webhook`**. **Telegram does not send inbound traffic until you call `setWebhook`** with a public **HTTPS** URL. Saving credentials in Studio only stores them in the database; it does not register the webhook.

### Studio (per workspace)

1. Create a bot with [@BotFather](https://t.me/BotFather) and copy the **bot token**.
2. In **Settings → Telegram**, paste **Bot token** and **Chat ID** (your user id for DMs, or the group/channel id if the bot is added there).
3. Click **Generate** next to **Webhook secret** (or paste your own). The value must be acceptable to Telegram as `secret_token` (avoid characters Telegram rejects; the built-in generator produces hex).
4. Click **Save settings** on the main Settings page so `app_settings` is updated.

### Register `setWebhook` with Telegram

Run this **once** after the first save, and **again** whenever you change the webhook secret or the public API URL.

**Option A — repo script** (from the repository root on Linux/macOS):

```bash
export TELEGRAM_BOT_TOKEN='123456789:…'        # from BotFather
export TELEGRAM_WEBHOOK_SECRET='…'             # exact same string as “Webhook secret” in Studio
./scripts/telegram-set-webhook.sh https://YOUR_PUBLIC_HOST
```

Use your real site origin with **no trailing slash** (example: `https://directely.com`). The script sets `url` to `https://YOUR_PUBLIC_HOST/v1/integrations/telegram/webhook`.

If you copied the repo from Windows, ensure shell scripts use **LF** line endings (see **§1. Get the code on the server** above); otherwise `set -o pipefail` may fail.

**Option B — curl**:

```bash
curl -sS -X POST "https://api.telegram.org/bot<TELEGRAM_BOT_TOKEN>/setWebhook" \
  --data-urlencode "url=https://YOUR_PUBLIC_HOST/v1/integrations/telegram/webhook" \
  --data-urlencode "secret_token=<same as webhook secret in Studio>"
```

### Verify

```bash
curl -sS "https://api.telegram.org/bot<TELEGRAM_BOT_TOKEN>/getWebhookInfo"
```

The JSON should show a non-empty **`url`** pointing at your host. Use **Test Telegram connection** in Studio: it checks the token, optional test message, and whether Telegram has registered a webhook URL.

### Troubleshooting

- **`url` empty in `getWebhookInfo`**: Directely will not receive messages; run `setWebhook` as above.
- **Changed the secret in Studio**: run `setWebhook` again with the new `secret_token`.
- **Local dev**: expose the API with a tunnel (ngrok, Cloudflare Tunnel, …) and use `https://YOUR-TUNNEL-HOST/v1/integrations/telegram/webhook` as `url`.
