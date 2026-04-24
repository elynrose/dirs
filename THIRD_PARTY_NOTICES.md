# Third-party notices

Directely incorporates or depends on open-source and third-party materials. **This list is a good-faith summary, not exhaustive.** For complete dependency graphs and exact versions, use:

- **Python:** `apps/api/pyproject.toml` and your locked environment (e.g. `uv.lock` / `pip freeze` after install).
- **JavaScript:** `apps/web/package.json`, `apps/electron/package.json`, and lockfiles if you generate them (`package-lock.json` / `pnpm-lock.yaml`).

Runtime services (Docker images, FFmpeg binaries) are **your** responsibility to license and attribute per their upstream projects. The notes below describe how this repository is **intended** to be used so that common deployment paths stay compatible with upstream licenses.

---

## Using third-party components “in product” (practical summary)

1. **Directely proprietary code** — Governed by `LICENSE` at the repository root (or the license file shipped with your installer).
2. **Python and npm dependencies** — Each package retains its own license. Preserve required copyright notices in distributions as mandated by those licenses (many permissive licenses require notice preservation in **source** distributions; some require it for **binary** distributions—follow each license).
3. **FFmpeg / ffprobe** — Directely invokes these as **external programs**. You must supply a build that is legal for your jurisdiction and distribution channel (build flags determine LGPL vs GPL implications). Directely does not ship FFmpeg binaries in the core web/API repositories; the Electron/desktop packaging path must document where FFmpeg comes from if you bundle it.
4. **Docker Compose stack** — `docker-compose.yml` references container images (PostgreSQL, Redis, MinIO). Pulling and running an image means **that image’s license** applies to the running container. The Compose file itself is configuration; compliance turns on **what you distribute** (e.g. whether you redistribute image layers, or only document `docker pull` for customers).
5. **Optional cloud / API vendors** (OpenAI, Stripe, Pexels, Storyblocks, Fal, etc.) — Subject to **their** terms and your agreements. Directely does not grant rights in third-party APIs or data.

---

## AGPL and MinIO (object storage)

The optional **MinIO** server image (`minio/minio`, pinned in `docker-compose.yml`) is licensed under **GNU AGPL-3.0**.

**What AGPL generally cares about (high level):** If you **convey** AGPL-covered object code to others (for example, you redistribute the MinIO binary or a modified MinIO), AGPL requires (among other things) that recipients can get corresponding **source** under AGPL, and certain **network** use cases can trigger obligations for modified versions offered as a service. Unmodified use on your own infrastructure is a common pattern; **your** packaging, SaaS offering, or OEM delivery determines what applies.

**This repository’s posture:** MinIO is **optional** (`STORAGE_BACKEND` defaults to filesystem in `.env.example`). Typical local development runs Postgres/Redis/MinIO via Compose; production may use managed S3 instead. If you **remove MinIO** from your shipped topology or replace it with a non-AGPL object store, AGPL obligations tied to MinIO distribution may not arise—**but** you must still satisfy licenses for everything else you ship.

**Source for MinIO:** https://github.com/minio/minio (match the image tag in `docker-compose.yml` when documenting versions).

MinIO, Inc. offers **commercial** support and products; that is optional and separate from the open-source AGPL build.

---

## Application stack (representative)

| Component | SPDX / typical license | Notes |
|-----------|-------------------------|--------|
| **FastAPI** | MIT | Python API framework |
| **Starlette** | MIT | ASGI stack (via FastAPI) |
| **Uvicorn** | BSD-3-Clause | ASGI server |
| **SQLAlchemy** | MIT | ORM |
| **Alembic** | MIT | DB migrations |
| **Pydantic** | MIT | Data validation |
| **Celery** | BSD-3-Clause | Task queue |
| **Redis (client)** | MIT | `redis` PyPI package |
| **httpx** | BSD-3-Clause | HTTP client |
| **structlog** | MIT / Apache-2.0 (dual) | Structured logging |
| **PostgreSQL** (image) | PostgreSQL License | Database server when using Docker Compose |
| **Redis** (image) | BSD-3-Clause | Broker when using Docker Compose |
| **MinIO** (image) | **AGPL-3.0** | Optional object storage; see section above |
| **React** | MIT | Web UI |
| **React DOM** | MIT | Web UI |
| **Vite** | MIT | Web build tool |
| **@vitejs/plugin-react** | MIT | Vite React plugin |
| **Electron** | MIT | Desktop shell |
| **electron-builder** | MIT | Packaging |
| **Express** | MIT | Local proxy in Electron shell |
| **http-proxy-middleware** | MIT | Proxy middleware |
| **Font Awesome Free** | SIL OFL 1.1 / MIT (icons); see [Font Awesome license](https://fontawesome.com/license/free) | If you ship FA fonts/icons, retain upstream attribution and license notices as required by the OFL/MIT terms you use |
| **Playwright** | Apache-2.0 | Dev / E2E only (not shipped in production web `dist` unless you add it) |

## API / optional integrations

Packages such as **OpenAI**, **Stripe**, **Firebase Admin**, **Fal**, **BeautifulSoup**, **Tavily**, **Pexels**, **Storyblocks**, etc., are subject to **their** licenses and **your** agreements with those vendors. Directely does not grant rights in third-party APIs or data.

## FFmpeg

Pipeline and export features invoke **FFmpeg** / **ffprobe**. Binaries may be **GPL** or **LGPL** depending on how they are built. **You** are responsible for FFmpeg licensing and patent compliance in your jurisdiction and distribution channel.

## In-repo packages

- **`packages/ffmpeg-pipelines`** — **MIT** (see `packages/ffmpeg-pipelines/LICENSE`). Same terms apply when installed as a Python dependency.
- **`packages/chatterbox-tts`** — includes `LICENSE` in that directory; honor it if you ship that optional stack.

---

If you redistribute a **modified** open-source component, preserve copyright notices and license texts as required by that component’s license.
