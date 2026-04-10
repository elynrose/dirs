# GitHub setup

## 1. One-time: identity on this machine

```bash
git config --global user.name "Your Name"
git config --global user.email "you@users.noreply.github.com"
```

Use the email tied to your GitHub account (or GitHub’s private noreply address from **Settings → Emails**).

## 2. Create an empty repository on GitHub

On [github.com/new](https://github.com/new): choose a name (e.g. `director`), **do not** add README / `.gitignore` / license if you already have a local repo (avoids merge noise).

## 3. Connect this folder and push

From the repository root:

```bash
cd /path/to/director
git remote add origin https://github.com/YOUR_USERNAME/director.git
git branch -M main
git push -u origin main
```

**SSH** (if you use keys):

```bash
git remote add origin git@github.com:YOUR_USERNAME/director.git
```

When prompted over HTTPS, use a **Personal Access Token** (classic: `repo` scope), not your GitHub password.

## 4. What must never be committed

These are listed in `.gitignore`; do not force-add them:

- `.env` — API keys, DB URLs, JWT secret
- `*firebase-adminsdk*.json` — Firebase Admin credentials
- `node_modules/`, `apps/api/.venv/`, `apps/api/.venv-win/`
- `data/`, build outputs, `.run/` logs

If you ever committed a secret, **rotate** the key in the provider (OpenAI, Firebase, etc.) and use `git filter-repo` or GitHub support to purge history.

## 5. Clone on another machine or server

```bash
git clone https://github.com/YOUR_USERNAME/director.git
cd director
cp .env.example .env
# edit .env, then follow INSTALLATION.md
```
