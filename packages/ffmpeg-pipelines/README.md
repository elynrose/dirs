# ffmpeg-pipelines

Builds FFmpeg command lines and export manifests for Directely Phase 5 compile jobs.

- **Image rough cut:** concat stills into H.264 MP4 (`compile_image_slideshow`).
- **Video rough cut:** concat video streams with a single re-encoded H.264 output (`compile_video_concat`).
- **Final mux:** `mux_video_with_narration_and_music` — copy video, add narration file or silence, optional looped music, **amix**, **loudnorm** −16 LUFS → stereo AAC (`ffprobe_duration_seconds` helper).

Install into the API venv (from `apps/api`):

```bash
pip install -e ../../packages/ffmpeg-pipelines
```

Requires **`ffmpeg`** on `PATH` for compile functions.
