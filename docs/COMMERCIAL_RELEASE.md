# Commercial release checklist

Use this before you **sell**, **ship an installer**, or **sign an enterprise deal**. Items under **Formal legal review** are the ones that typically need an attorney when you retail software or take enterprise money; the rest are engineering and operations.

## What you should decide (business / product)

1. **Legal entity** — Exact name on contracts and copyright (e.g. `Directely LLC`). Replace “Directely” in `LICENSE` if the entity differs.
2. **Governing law & venue** — `LICENSE` defaults to Delaware, USA; change only if your contracts require it.
3. **Product form** — On-prem desktop only vs SaaS vs hybrid (affects privacy policy, DPA, subprocessors).
4. **Trademark** — Confirm you have rights to the product name and logo; register if you need nationwide protection.
5. **Tax & payments** — Stripe (or other) merchant of record, VAT/GST, invoices.

## Repository artifacts (engineering)

| Item | Location / action |
|------|-------------------|
| Proprietary license | `LICENSE` (root) |
| OSS attributions | `THIRD_PARTY_NOTICES.md` (root); refresh before each major release |
| Version alignment | `.\scripts\sync-release-version.ps1 -Version x.y.z` (requires **Python 3.11+** on `PATH`; runs `scripts/sync_release_version.py`) |
| Desktop build | `.\scripts\build-exe.ps1` (Windows); see `README.md` → Distribution |
| Secrets | Never ship `.env`, API keys, or `*firebase-adminsdk*.json`; ship `.env.example` only |
| Telemetry | Document if you collect analytics; add Privacy Policy URL in-app if required |

## Formal legal review (typical triggers)

- `LICENSE` — scope of grant, liability cap, warranty disclaimer, export control clause if you sell internationally.
- Terms of Service / Privacy Policy — especially if you operate hosted auth, billing, or cloud APIs.
- **AGPL / copyleft** — Read `THIRD_PARTY_NOTICES.md` → MinIO section. If your **distribution model** includes conveying AGPL-covered server code (or modified AGPL services) to customers, map your obligations; replacing MinIO with managed S3 or another store is a common mitigation.
- **FFmpeg** — Distribution and patent stance for the FFmpeg build you ship or require.
- Reseller / OEM addendum if partners redistribute the installer.

## After edits

- Update `LICENSE` and keep `THIRD_PARTY_NOTICES.md` technically accurate.
- Point NSIS / DMG installers at the final license file (`apps/electron/package.json` → `build.nsis.license`).
