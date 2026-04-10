"""Parallel critic calls via OpenAI Agents SDK (structured outputs + asyncio.gather)."""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from pydantic import BaseModel, Field
from pydantic import field_validator

from director_api.agents.openai_client import (
    make_async_openai_client,
    openai_chat_targets_local_compatible_server,
    openai_compatible_configured,
    resolve_openai_compatible_chat_model,
)
from director_api.config import Settings
from director_api.services.llm_prompt_runtime import get_llm_prompt_text
from director_api.services.usage_accounting import append_llm_usage_sink, parse_agents_usage

log = logging.getLogger(__name__)

_LOCAL_AGENTS_OUTPUT_HINT = (
    " Put the final structured result in normal assistant-visible output (not only in hidden reasoning); "
    "the runner reads the surfaced assistant payload."
)


class SceneCritiqueStructured(BaseModel):
    dimensions: dict[str, float] = Field(default_factory=dict)
    recommendations: list[str] = Field(default_factory=list)

    @field_validator("dimensions", mode="before")
    @classmethod
    def _coerce_dims(cls, v: Any) -> dict[str, float]:
        if not isinstance(v, dict):
            return {}
        out: dict[str, float] = {}
        for k, val in v.items():
            try:
                out[str(k)] = float(val)
            except (TypeError, ValueError):
                continue
        return out


class ChapterCritiqueStructured(BaseModel):
    dimensions: dict[str, float] = Field(default_factory=dict)
    recommendations: list[str] = Field(default_factory=list)

    @field_validator("dimensions", mode="before")
    @classmethod
    def _coerce_dims(cls, v: Any) -> dict[str, float]:
        if not isinstance(v, dict):
            return {}
        out: dict[str, float] = {}
        for k, val in v.items():
            try:
                out[str(k)] = float(val)
            except (TypeError, ValueError):
                continue
        return out


def agents_sdk_import_ok() -> bool:
    try:
        import agents  # noqa: F401

        return True
    except ImportError:
        return False


def _ensure_async_client(settings: Settings) -> None:
    from agents import set_default_openai_client

    client = make_async_openai_client(settings)
    set_default_openai_client(client, use_for_tracing=False)


async def _run_scene_one(
    model: str,
    user_json: str,
    usage_sink: list[dict[str, Any]] | None,
    *,
    instructions: str,
) -> SceneCritiqueStructured | None:
    from agents import Agent, ModelSettings, Runner

    agent = Agent(
        name="Scene Critic",
        instructions=instructions,
        output_type=SceneCritiqueStructured,
        model=model,
        model_settings=ModelSettings(temperature=0.25),
    )
    try:
        result = await Runner.run(agent, user_json[:24000])
        try:
            uw = getattr(result.context_wrapper, "usage", None)
            u = parse_agents_usage(uw)
            append_llm_usage_sink(
                usage_sink,
                provider="openai",
                model=model,
                service_type="phase4_scene_critique_parallel",
                usage=u,
            )
        except Exception:
            pass
        out = result.final_output
        return out if isinstance(out, SceneCritiqueStructured) else None
    except Exception:
        log.exception("agents_sdk_scene_critic_failed")
        return None


async def _run_chapter_one(
    model: str,
    user_json: str,
    usage_sink: list[dict[str, Any]] | None,
    *,
    instructions: str,
) -> ChapterCritiqueStructured | None:
    from agents import Agent, ModelSettings, Runner

    agent = Agent(
        name="Chapter Critic",
        instructions=instructions,
        output_type=ChapterCritiqueStructured,
        model=model,
        model_settings=ModelSettings(temperature=0.25),
    )
    try:
        result = await Runner.run(agent, user_json[:24000])
        try:
            uw = getattr(result.context_wrapper, "usage", None)
            u = parse_agents_usage(uw)
            append_llm_usage_sink(
                usage_sink,
                provider="openai",
                model=model,
                service_type="phase4_chapter_critique_parallel",
                usage=u,
            )
        except Exception:
            pass
        out = result.final_output
        return out if isinstance(out, ChapterCritiqueStructured) else None
    except Exception:
        log.exception("agents_sdk_chapter_critic_failed")
        return None


async def run_scene_critiques_parallel_async(
    settings: Settings,
    user_json_strings: list[str],
    usage_sink: list[dict[str, Any]] | None = None,
) -> list[SceneCritiqueStructured | None]:
    if not openai_compatible_configured(settings) or not user_json_strings:
        return [None] * len(user_json_strings)
    _ensure_async_client(settings)
    model = resolve_openai_compatible_chat_model(settings)
    ins = get_llm_prompt_text("parallel_scene_critic") + (
        _LOCAL_AGENTS_OUTPUT_HINT if openai_chat_targets_local_compatible_server(settings) else ""
    )
    return list(
        await asyncio.gather(
            *[_run_scene_one(model, s, usage_sink, instructions=ins) for s in user_json_strings]
        )
    )


async def run_chapter_critiques_parallel_async(
    settings: Settings,
    user_json_strings: list[str],
    usage_sink: list[dict[str, Any]] | None = None,
) -> list[ChapterCritiqueStructured | None]:
    if not openai_compatible_configured(settings) or not user_json_strings:
        return [None] * len(user_json_strings)
    _ensure_async_client(settings)
    model = resolve_openai_compatible_chat_model(settings)
    ins = get_llm_prompt_text("parallel_chapter_critic") + (
        _LOCAL_AGENTS_OUTPUT_HINT if openai_chat_targets_local_compatible_server(settings) else ""
    )
    return list(
        await asyncio.gather(
            *[_run_chapter_one(model, s, usage_sink, instructions=ins) for s in user_json_strings]
        )
    )


def run_scene_critiques_parallel_sync(
    settings: Settings,
    payloads: list[dict[str, Any]],
    usage_sink: list[dict[str, Any]] | None = None,
) -> list[tuple[dict[str, Any] | None, list[str] | None]]:
    """Fan-out scene critics; returns (dimensions, recommendations) per payload (or Nones)."""
    if not payloads:
        return []
    if not agents_sdk_import_ok():
        return [(None, None)] * len(payloads)
    user_strs = [json.dumps(p, ensure_ascii=False) for p in payloads]
    try:
        outs = asyncio.run(run_scene_critiques_parallel_async(settings, user_strs, usage_sink))
    except RuntimeError:
        # Nested event loop (rare); fall back to sequential sync LLM in caller
        log.warning("agents_sdk_parallel_scene_skipped_event_loop")
        return [(None, None)] * len(payloads)
    rows: list[tuple[dict[str, Any] | None, list[str] | None]] = []
    for o in outs:
        if o is None:
            rows.append((None, None))
        else:
            rows.append((dict(o.dimensions), list(o.recommendations)[:12]))
    return rows


def run_chapter_critiques_parallel_sync(
    settings: Settings,
    payloads: list[dict[str, Any]],
    usage_sink: list[dict[str, Any]] | None = None,
) -> list[tuple[dict[str, Any] | None, list[str] | None]]:
    if not payloads:
        return []
    if not agents_sdk_import_ok():
        return [(None, None)] * len(payloads)
    user_strs = [json.dumps(p, ensure_ascii=False) for p in payloads]
    try:
        outs = asyncio.run(run_chapter_critiques_parallel_async(settings, user_strs, usage_sink))
    except RuntimeError:
        log.warning("agents_sdk_parallel_chapter_skipped_event_loop")
        return [(None, None)] * len(payloads)
    rows: list[tuple[dict[str, Any] | None, list[str] | None]] = []
    for o in outs:
        if o is None:
            rows.append((None, None))
        else:
            rows.append((dict(o.dimensions), list(o.recommendations)[:12]))
    return rows
