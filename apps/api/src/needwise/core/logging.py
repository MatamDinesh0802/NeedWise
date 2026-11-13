"""Structured logging with structlog.

Application logs and third-party (stdlib) logs share one pipeline, so every line carries
the same fields (timestamp, level, logger, request_id) in console or JSON format. When tracing
is on (core/telemetry.py) and a span is active, a line also carries its `trace_id` and
`span_id`, so a log line leads to its trace.
"""

import logging
import sys

import structlog
from opentelemetry import trace

from needwise.core.config import Settings


def add_trace_ids(
    _logger: object, _method: str, event: structlog.types.EventDict
) -> structlog.types.EventDict:
    """The active span's ids (hex, as trace backends show them), when one is recording."""
    context = trace.get_current_span().get_span_context()
    if context.is_valid:
        event.setdefault("trace_id", format(context.trace_id, "032x"))
        event.setdefault("span_id", format(context.span_id, "016x"))
    return event


def configure_logging(settings: Settings) -> None:
    shared_processors: list[structlog.types.Processor] = [
        structlog.contextvars.merge_contextvars,
        add_trace_ids,
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
    ]

    renderer: structlog.types.Processor
    final_processors: list[structlog.types.Processor] = [
        structlog.stdlib.ProcessorFormatter.remove_processors_meta,
    ]
    if settings.log_format == "json":
        final_processors.append(structlog.processors.dict_tracebacks)
        renderer = structlog.processors.JSONRenderer()
    else:
        renderer = structlog.dev.ConsoleRenderer()
    final_processors.append(renderer)

    structlog.configure(
        processors=[*shared_processors, structlog.stdlib.ProcessorFormatter.wrap_for_formatter],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(
        structlog.stdlib.ProcessorFormatter(
            foreign_pre_chain=shared_processors,
            processors=final_processors,
        )
    )
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(settings.log_level.upper())

    # Route uvicorn through our handler; our middleware replaces its access log.
    for name in ("uvicorn", "uvicorn.error"):
        uvicorn_logger = logging.getLogger(name)
        uvicorn_logger.handlers.clear()
        uvicorn_logger.propagate = True
    logging.getLogger("uvicorn.access").disabled = True
