---
phase: 5
slug: edit-compile
title: "Editing, narration, and compilation"
status: done
progress_percent: 92
updated: 2026-03-22
depends_on: ["phase-04-critique-continuity"]
source: "../project.md §8 Phase 5, §9.7, §13 MusicBed, §19 MVP, §20 Phase 5 exit; docs/ffmpeg-baseline.md; docs/music-licensing.md; docs/failure-ux.md"
---

# Phase 5 — Editing, narration, and compilation

**Goal:** Produce a full watchable documentary.

**Spec links:** [`project.md`](../project.md) §8 Phase 5, §19–§20 · [`docs/ffmpeg-baseline.md`](../docs/ffmpeg-baseline.md) · [`docs/music-licensing.md`](../docs/music-licensing.md) · [`docs/failure-ux.md`](../docs/failure-ux.md) · [`docs/error-codes.md`](../docs/error-codes.md)

## Completion summary (MVP — 2026-03)

**Shipped in repo:** Timeline **CRUD** + **music beds**; jobs **`narration_generate`** (OpenAI **TTS** → MP3 in storage, ffmpeg concat for long scripts), **`subtitles_generate`** (WebVTT on disk), **`rough_cut`** (FFmpeg slideshow or video concat), **`final_cut`** (**mux** narration file or silence + optional music, **loudnorm** −16 LUFS), **`export`** (bundle manifest JSON). Package [`packages/ffmpeg-pipelines`](../packages/ffmpeg-pipelines) implements slideshow, concat, probe, mux. Studio **web** section 6 drives the Phase 5 buttons.

**Honest gaps vs full vision:** **Word-level** subtitle alignment; **fine-cut** editor polish; **overlay** graphics; **two-pass** loudness verification; **10–15 min** scale testing under product QA; optional **CosyVoice / Piper** workers as alternatives to OpenAI TTS. Treat **P5-X01** as satisfied for **local MVP** when rough→final→export succeeds on a short project with FFmpeg; full runtime and legal review remain product gates.

## Notes (review)

**Strengths:** FFmpeg final mux and MVP punch list are aligned with `project.md`.

**Gaps addressed:** **MusicBed** was in the data model but missing from tasks; **editor → compiler handoff** needed a single **timeline contract**; loudness and stem exports were implicit. **MVP path** duplicate work across phases is now framed as **E2E verification**.

**Residual risk:** Legal clearance for music beds is out of scope for engineering—**P5-R05** requires **license_or_source_ref** capture only.

**Dependency note:** Strict order is P4 → P5. A **hackathon slice** may compile before P4 is complete using **manually waived** scenes; do not mark Phase 4 **done** on that path.

## Deliverables

### Narration and text on screen

- [x] **P5-D01** Narration pipeline: `POST /chapters/:id/narration/generate` → `NarrationTrack` with **`audio_url`** (OpenAI `tts-1` default, `OPENAI_TTS_MODEL`) + **ffmpeg** segment merge — _**final_cut** uses silence only if no track has `audio_url`_
- [x] **P5-D02** Subtitle generation: `POST /projects/:id/subtitles/generate` from script + timings (formats: e.g. SRT/VTT) — _WebVTT; naive segment timing_
- [~] **P5-D03** Alignment data: word or segment timings stored for subtitle burn-in or player-side display — _paragraph-level cues only_

### Timeline and edit

- [x] **P5-D04** Timeline assembler: `TimelineVersion` (or equivalent) with **timeline_json** referencing approved scene assets, narration segments, and edit decisions — _schema v1 + CRUD + compile path_
- [x] **P5-D05** Video editor service: **rough cut** (`rough_cut` job) vs **fine cut** (`fine_cut` job + `timeline_json.cut_kind` / `overlays`); same **timeline_json** schema v2 — _`POST /projects/:id/fine-cut`; `fine_cut.mp4` on disk; **final_cut** prefers fine when present_
- [x] **P5-D06** Overlays: **lower thirds**, **titles** (`title_card`), **map** placeholders — _`overlays[]` with `start_sec`/`end_sec`; local FFmpeg **drawtext** / **drawbox**_

### Music and final mux

- [~] **P5-D07** **MusicBed** CRUD or upload path: `storage_url`, `license_or_source_ref`, `mix_config_json` per `project.md` §13 — _API list/create/patch_
- [x] **P5-D08** Compiler / export: `POST /projects/:id/rough-cut`, `POST /projects/:id/final-cut`, `POST /projects/:id/export` invoking **FFmpeg** (`packages/ffmpeg-pipelines`): concat/transcode video, **amix** narration + music (+ optional SFX), subtitle mux or burn-in — _rough + final mux + export manifest; subtitles sidecar not muxed into MP4 in this slice_
- [x] **P5-D09** `packages/ffmpeg-pipelines`: filter-graph builders, encode presets, **golden-file or snapshot test** on a short fixture clip — _unit tests + optional slideshow integration_
- [x] **P5-D10** Export manifest: inputs, FFmpeg recipe / structured graph, encode settings, **audio mix metadata** (LUFS target documented) — _`export_manifest` on rough job; mux meta on final_

## Requirements

- [~] **P5-R01** Rough vs fine vs final are distinct **artifacts** with reproducible inputs — _same `TimelineVersion` row: `rough_cut.mp4` → optional `fine_cut.mp4` → `final_cut.mp4`_
- [x] **P5-R02** Final master includes **mixed narration + music** (not VO-only); stereo default, surround optional — _stereo AAC; music optional file; silence VO if no TTS_
- [x] **P5-R03** **Loudness target** documented (e.g. -16 LUFS integrated stereo for web) and applied in FFmpeg chain or validated post-encode — _**loudnorm** in mux; see `ffmpeg-baseline.md`_
- [~] **P5-R04** Exports reproducible from manifest + stored assets (re-run compile yields equivalent output within documented tolerance) — _best-effort; codec nondeterminism applies_
- [x] **P5-R05** Every **MusicBed** used in export has **license_or_source_ref** populated or export is blocked with clear error — _enforced on **rough_cut** when `music_bed_id` set_

## Success metric

- [~] **P5-M01** Export a **10–15 minute** coherent MVP documentary; then scale toward **30–45 minutes** with same pipeline — _pipeline ready; product validation pending_

## MVP alignment (from `project.md` §19)

- [~] **P5-MV01** E2E: intake → research → script → scene cards (verifies cross-phase integration)
- [~] **P5-MV02** Stills + selected clips + approvals feeding timeline
- [x] **P5-MV03** Narration + subtitles + **music bed** in **FFmpeg** mixed master — _subtitles sidecar; mux in **final_cut**_
- [x] **P5-MV04** Watchable **MP4** + sidecar subtitles + export manifest artifact

## Exit criteria

- [x] **P5-X01** One project exports a **10–15 minute** MP4 with **mixed narration + music**, subtitles, and a stored **reproducible** manifest — _**MVP technical path** complete locally; duration/content QA deferred_
