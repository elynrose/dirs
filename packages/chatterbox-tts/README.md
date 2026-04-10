# chatterbox-tts (vendored)

This directory is a **vendored copy** of [Resemble AI Chatterbox](https://github.com/resemble-ai/chatterbox) for use by Director. The upstream **LICENSE** applies.

Model **weights are not committed**; they are downloaded when the library runs (e.g. from Hugging Face).

To install into the API venv (optional stack):

```bash
cd apps/api
pip install -e "../../packages/chatterbox-tts"
```

Or use the `director-api` extra `[chatterbox]` once wired in `apps/api/pyproject.toml`.
