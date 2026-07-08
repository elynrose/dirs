"""Local-TTS sidecar assets.

``kokoro_render.py`` is a *standalone* script executed by a separate venv
(Python 3.11 + torch + kokoro). It is never imported by the Director worker
(Python 3.14, no torch); the worker only resolves its file path and runs it as a
subprocess. This package exists so the script ships in the installed wheel.
"""
