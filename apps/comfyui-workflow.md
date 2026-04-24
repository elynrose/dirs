Here is how Director’s ComfyUI settings line up with video_wan2_2_14B_t2v.json, based on generate_scene_video_comfyui and _apply_duration_sec_to_comfyui_video_workflow in media_comfyui.py.

Workflow file and server
Director / .env	Value for this WAN 2.2 T2V graph
COMFYUI_VIDEO_WORKFLOW_JSON_PATH
Path to this file, e.g. data/comfyui_workflows/video_wan2_2_14B_t2v.json (repo-relative works).
COMFYUI_BASE_URL
Your ComfyUI HTTP root (unchanged).
COMFYUI_API_FLAVOR
oss (default) or cloud if you use Comfy Cloud.
COMFYUI_API_KEY / COMFY_CLOUD_API_KEY
If your flavor needs a key.
COMFYUI_VIDEO_TIMEOUT_SEC
Long runs: default 1800s is reasonable for 14B video.
Prompts (CLIPTextEncode)
Workflow node	Role	Director setting
89
Positive text
COMFYUI_VIDEO_PROMPT_NODE_ID=89 (recommended).
72
Negative text
COMFYUI_VIDEO_NEGATIVE_NODE_ID=72 (recommended).
inputs.text
Field name
COMFYUI_VIDEO_PROMPT_INPUT_KEY=text (default if unset falls back to COMFYUI_PROMPT_INPUT_KEY, default text).
Negative text body comes from COMFYUI_VIDEO_DEFAULT_NEGATIVE_PROMPT if set, else COMFYUI_DEFAULT_NEGATIVE_PROMPT. Scene-level negative from the pipeline is not merged into video the way stills are; only this env string is injected when the negative node is targeted.

Important: If you leave COMFYUI_VIDEO_PROMPT_NODE_ID empty, Director auto-picks the first CLIPTextEncode by numeric node id. Here that is 72, which is your negative node — the scene prompt would go to the wrong place. For this JSON you should set 89 and 72 explicitly as above.

Image conditioning (this workflow is T2V, no LoadImage)
Director setting	For this JSON
COMFYUI_VIDEO_USE_SCENE_IMAGE
false — there is no LoadImage node; still is not part of this graph.
COMFYUI_VIDEO_LOAD_IMAGE_NODE_ID
Leave empty (ignored when USE_SCENE_IMAGE is false).
If you left COMFYUI_VIDEO_USE_SCENE_IMAGE=true (Director default), comfyui_wan would try to upload a scene still and require a load node — wrong for this T2V workflow.

Duration / frames (Director mutates the JSON at runtime)
Workflow	Director behavior
Node 74 EmptyHunyuanLatentVideo → length
COMFYUI does not read width/height from env. It only adjusts length from workspace clip length: length = round(scene_clip_duration_sec × fps), clamped 8–512, where fps is read from the first CreateVideo node (88 → 16 in your file). So e.g. 5s → 80, 10s → 160 frames.
Node 88 CreateVideo → fps
Used only to compute length; Director does not change fps from settings.
width / height (640×640 in 74) stay whatever is in the saved JSON unless you edit the file or extend Director.

Seeds, samplers, models in the JSON
Items in JSON (UNet, LoRA, KSampler, seeds, etc.)	Director
All loader / sampler / LoRA / ModelSamplingSD3 / two-stage KSampler values
Not mapped to env vars — whatever is in video_wan2_2_14B_t2v.json is sent as-is.
noise_seed on nodes 81 / 78
Not overwritten by Director today.
Metadata / still workflow (separate from this file)
Setting	Use
COMFYUI_VIDEO_MODEL_NAME
Label for assets / params (e.g. wan-2.2-t2v-14b-comfyui); does not change ComfyUI.
COMFYUI_WORKFLOW_JSON_PATH
Still image workflow when comfyui_wan needs a scene image first; unrelated to this video JSON.
Example .env snippet for this workflow
COMFYUI_VIDEO_WORKFLOW_JSON_PATH=data/comfyui_workflows/video_wan2_2_14B_t2v.json
COMFYUI_VIDEO_USE_SCENE_IMAGE=false
COMFYUI_VIDEO_PROMPT_NODE_ID=89
COMFYUI_VIDEO_NEGATIVE_NODE_ID=72
# Optional: COMFYUI_VIDEO_PROMPT_INPUT_KEY=text
# Optional: COMFYUI_VIDEO_DEFAULT_NEGATIVE_PROMPT=...  (your negative block)
COMFYUI_VIDEO_MODEL_NAME=wan-2.2-t2v-14b-comfyui
If you want different resolution or fixed frame count, edit node 74 (and optionally 88 fps) in the JSON; Director only rewrites length on that latent node class for duration.