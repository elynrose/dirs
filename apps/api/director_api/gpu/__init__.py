"""GPU sidecar assets for the Ken Burns renderer.

``ken_burns_render.py`` is a *standalone* script executed by a separate CUDA venv
(Python 3.11 + torch). It is never imported by the Director worker (Python 3.14, no CUDA
torch); the worker only resolves its file path. This package exists so the script and its
``requirements-gpu.txt`` are shipped in the installed wheel.
"""
