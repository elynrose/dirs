cd apps/mcp-director && python3 -m venv .venv && source .venv/bin/activate && pip install -e .
export DIRECTOR_API_BASE_URL=http://127.0.0.1:8000
python -m director_mcp