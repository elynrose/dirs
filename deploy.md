# Server deploy (major updates)

Run **on the Linux host** where the API, Celery, and nginx run (SSH into the server, `cd` to the repo checkout).

Full setup (Docker, venv, systemd units, nginx + static root) is in [`INSTALLATION.md`](INSTALLATION.md) (especially §4–7).

## One-shot update (after `git` changes)

From the repository root (e.g. `/opt/director` or `/root/director`):

```bash
chmod +x scripts/server-major-update.sh   # once per clone
./scripts/server-major-update.sh
```

This pulls `main`, reinstalls the API venv, runs migrations, builds the web app, rsyncs `apps/web/dist/` to the static site directory, and restarts `director-api`, `director-worker`, `director-beat`, and `nginx`.

## Environment (optional)

| Variable | Default | Meaning |
| -------- | ------- | ------- |
| `DIRECTOR_REPO` | parent of `scripts/` | Repo path if the script is not run from the checkout |
| `DIRECTOR_STATIC_WEB_ROOT` | `/var/www/directely` | Where static `dist/` is published (match nginx `root`) |
| `GIT_BRANCH` | `main` | Branch for `git pull` |

## Common flags

```bash
./scripts/server-major-update.sh --help
```

Examples:

- `--no-git` — code already updated (e.g. you pulled manually).
- `--no-web` / `--no-rsync` — backend-only or build without publishing static files.
- `--no-systemd` / `--no-nginx` — restart only what you need.
- `--with-vite` — also restart `director-vite` if you use that unit instead of static nginx.

## Related

- [`scripts/server-major-update.sh`](scripts/server-major-update.sh) — script source and inline comments.
- [`INSTALLATION.md`](INSTALLATION.md) — first-time install, TLS, Firebase domains, Telegram webhook.
