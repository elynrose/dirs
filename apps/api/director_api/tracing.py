"""Tracing hooks — structlog context for request/job correlation; OpenTelemetry optional later."""

from contextlib import contextmanager
from typing import Generator

import structlog


@contextmanager
def span(name: str, **attrs: object) -> Generator[None, None, None]:
    structlog.contextvars.bind_contextvars(span_name=name, **{k: str(v) for k, v in attrs.items()})
    try:
        yield
    finally:
        for k in ("span_name", *attrs):
            structlog.contextvars.unbind_contextvars(k)
