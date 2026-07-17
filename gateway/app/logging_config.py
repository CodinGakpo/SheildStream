"""Structured logging — Week 10.

Every log line, whether emitted via a `structlog.get_logger()` call or an
existing plain `logging.getLogger(...)` call elsewhere in the codebase (Week
0-9 code is full of these), is rendered as one JSON object per line via the
same `ProcessorFormatter`. `merge_contextvars` is what makes `request_id`
(bound once per request in `middleware/request_id.py`) show up on every log
line emitted during that request's lifetime, including from code that has
no idea a request_id exists — that's the whole point: correlation without
threading an id through every function signature.
"""

import logging

import structlog

_SHARED_PROCESSORS = [
    structlog.contextvars.merge_contextvars,
    structlog.stdlib.add_log_level,
    structlog.stdlib.add_logger_name,
    structlog.stdlib.ExtraAdder(),  # picks up logging.getLogger(...).info(msg, extra={...})
    structlog.processors.TimeStamper(fmt="iso"),
]


def configure_logging(log_level: str) -> None:
    level = getattr(logging, log_level.upper(), logging.INFO)

    structlog.configure(
        processors=_SHARED_PROCESSORS + [structlog.stdlib.ProcessorFormatter.wrap_for_formatter],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )

    formatter = structlog.stdlib.ProcessorFormatter(
        processor=structlog.processors.JSONRenderer(),
        foreign_pre_chain=_SHARED_PROCESSORS,
    )
    handler = logging.StreamHandler()
    handler.setFormatter(formatter)

    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(level)
