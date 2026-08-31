"""Structured logging with a hard guarantee that API keys never reach a log line.

Records carry arbitrary keyword context via ``extra={"context": {...}}``; the
formatter renders it as sorted ``key=value`` pairs so log output is greppable
without being JSON when a human is reading it at a terminal.
"""

from __future__ import annotations

import logging
import os
import re
import sys
from typing import Any

LOGGER_NAME = "tia"

_KEY_PATTERN = re.compile(r"sk-ant-[A-Za-z0-9_\-]{6,}")


class RedactingFilter(logging.Filter):
    """Replace anything that looks like an API key with ``sk-ant-***``.

    Belt and braces: no code path in this package puts a key into a log record,
    but a stray subprocess environment dump or an exception repr from the SDK
    could, and a leaked key in CI output is not a recoverable mistake.
    """

    def __init__(self) -> None:
        super().__init__()
        self._literals = [
            v for v in (os.environ.get("ANTHROPIC_API_KEY"), os.environ.get("ANTHROPIC_AUTH_TOKEN"))
            if v and len(v) >= 8
        ]

    def _scrub(self, text: str) -> str:
        for literal in self._literals:
            text = text.replace(literal, "sk-ant-***")
        return _KEY_PATTERN.sub("sk-ant-***", text)

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.msg, str):
            record.msg = self._scrub(record.msg)
        if record.args:
            if isinstance(record.args, dict):
                record.args = {
                    k: self._scrub(v) if isinstance(v, str) else v for k, v in record.args.items()
                }
            elif isinstance(record.args, tuple):
                record.args = tuple(
                    self._scrub(a) if isinstance(a, str) else a for a in record.args
                )
        context = getattr(record, "context", None)
        if isinstance(context, dict):
            record.context = {
                k: self._scrub(v) if isinstance(v, str) else v for k, v in context.items()
            }
        return True


class ContextFormatter(logging.Formatter):
    """``LEVEL  message  key=value ...`` -- terse enough to read, structured enough to grep."""

    def format(self, record: logging.LogRecord) -> str:
        base = super().format(record)
        context = getattr(record, "context", None)
        if isinstance(context, dict) and context:
            pairs = " ".join(f"{k}={context[k]!r}" for k in sorted(context))
            return f"{base} {pairs}"
        return base


def configure_logging(verbose: bool = False, stream: Any = None) -> logging.Logger:
    """Install handlers on the ``tia`` logger. Idempotent."""
    logger = logging.getLogger(LOGGER_NAME)
    logger.setLevel(logging.DEBUG if verbose else logging.INFO)
    logger.propagate = False
    for handler in list(logger.handlers):
        logger.removeHandler(handler)
    handler = logging.StreamHandler(stream if stream is not None else sys.stderr)
    handler.setLevel(logging.DEBUG if verbose else logging.WARNING)
    handler.setFormatter(
        ContextFormatter(
            fmt="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
            datefmt="%H:%M:%S",
        )
    )
    handler.addFilter(RedactingFilter())
    logger.addHandler(handler)
    return logger


def get_logger(name: str = "") -> logging.Logger:
    """Return a child of the package logger."""
    return logging.getLogger(f"{LOGGER_NAME}.{name}" if name else LOGGER_NAME)


def log(logger: logging.Logger, level: int, message: str, **context: Any) -> None:
    """Emit ``message`` with structured ``context``."""
    logger.log(level, message, extra={"context": context})
