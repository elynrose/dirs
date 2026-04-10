#!/usr/bin/env python3
"""
Probe LM Studio / OpenAI-compatible chat using **saved workspace** settings (GET /v1/settings),
then optionally run one **character bible** LLM call (same path as Characters → Generate from story).

Usage (Windows venv):

  cd apps\\api
  .\\.venv-win\\Scripts\\python.exe ..\\..\\scripts\\test_lm_studio_text.py
  .\\.venv-win\\Scripts\\python.exe ..\\..\\scripts\\test_lm_studio_text.py --character-bible

Studio: set **Generation → Text provider** to **lm_studio**, fill **LM Studio** base URL + model, **Save settings**,
start LM Studio local server, load a model, then run this script.
"""

from __future__ import annotations

import argparse
import os
import sys


def main() -> int:
    parser = argparse.ArgumentParser(description="Test LM Studio / text chat + optional character bible.")
    parser.add_argument(
        "--character-bible",
        action="store_true",
        help="Run one character-bible JSON generation (same stack as project character agent).",
    )
    args = parser.parse_args()

    api_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "apps", "api"))
    if api_dir not in sys.path:
        sys.path.insert(0, api_dir)
    os.chdir(api_dir)

    from director_api.agents.openai_client import (
        active_text_provider_is_lm_studio,
        effective_openai_compatible_base_url,
        resolve_openai_compatible_chat_model,
    )
    from director_api.config import get_settings
    from director_api.db.session import SessionLocal
    from director_api.providers import run_adapter_smoke
    from director_api.services.runtime_settings import resolve_runtime_settings

    base = get_settings()
    with SessionLocal() as db:
        rt = resolve_runtime_settings(db, base)

    print("active_text_provider:", getattr(rt, "active_text_provider", ""))
    print("openai_compatible_text_source:", getattr(rt, "openai_compatible_text_source", ""))
    print("effective chat base_url:", effective_openai_compatible_base_url(rt))
    print("chat_model:", resolve_openai_compatible_chat_model(rt))
    print("lm_studio_api_base_url (raw):", (getattr(rt, "lm_studio_api_base_url", None) or "").strip() or "(empty)")

    prov = "lm_studio" if active_text_provider_is_lm_studio(rt) else "openai"
    print(f"\nAdapter smoke (provider={prov!r}) …")
    try:
        out = run_adapter_smoke(prov, rt)
    except Exception as e:  # noqa: BLE001
        print("SMOKE FAILED:", type(e).__name__, e)
        return 1
    print("result:", out)
    if not out.get("configured"):
        return 1
    out_text = (out.get("output") or "").strip()
    if out_text:
        print("model reply:", repr(out_text[:200]))

    if args.character_bible:
        from director_api.agents import phase2_llm

        director = {
            "schema_id": "director-pack/v1",
            "title": "LM Studio probe",
            "topic": "A short factual piece about two colleagues.",
            "narrative_arc": ["Setup", "Conversation", "Wrap-up"],
            "style_notes": {},
            "production_constraints": {},
        }
        chapters = [
            {
                "order_index": 0,
                "title": "Chapter 1",
                "script_excerpt": "Alice and Bob discuss the project timeline under an oak tree in the park.",
            },
        ]
        sink: list[dict] = []
        raw, err = phase2_llm.generate_character_bible(
            director=director,
            chapters_context=chapters,
            project_title="Probe",
            project_topic="Test",
            dossier_summary=None,
            settings=rt,
            usage_sink=sink,
        )
        if err:
            print("\nCHARACTER_BIBLE FAILED:", err)
            return 1
        rows = (raw or {}).get("characters") or []
        print(f"\ncharacter_bible ok: {len(rows)} character(s)")
        for c in rows[:16]:
            name = c.get("name")
            role = (c.get("role_in_story") or "")[:100]
            print(f"  - {name}: {role}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
