"""On-disk cache for LLM responses, keyed by a hash of everything that shapes them.

Reruns of ``tia scan`` over an unchanged repository cost nothing: the key
includes the model, the prompt, the schema and a cache-format version, so any
change that could produce a different answer produces a different key.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import tempfile
import threading
from pathlib import Path
from typing import Any

from .logging_setup import get_logger

CACHE_VERSION = 1
"""Bump when the meaning of a cached payload changes, to invalidate old entries."""

_log = get_logger("cache")


def content_key(*parts: Any) -> str:
    """Stable hash over ``parts``. Dicts are serialised with sorted keys."""
    hasher = hashlib.sha256()
    hasher.update(str(CACHE_VERSION).encode("utf-8"))
    for part in parts:
        hasher.update(b"\x1e")
        if isinstance(part, (dict, list, tuple)):
            hasher.update(json.dumps(part, sort_keys=True, default=str).encode("utf-8"))
        else:
            hasher.update(str(part).encode("utf-8"))
    return hasher.hexdigest()


class ResponseCache:
    """A tiny content-addressed JSON store under ``root``.

    Thread-safe for concurrent ``get``/``set`` from a worker pool. Writes are
    atomic (temp file plus replace), so an interrupted run never leaves a
    half-written entry that would deserialise into garbage.
    """

    def __init__(self, root: Path, enabled: bool = True) -> None:
        self.root = Path(root)
        self.enabled = enabled
        self._lock = threading.Lock()
        self.hits = 0
        self.misses = 0
        self._ready = False

    def _ensure_dir(self) -> bool:
        if self._ready:
            return True
        try:
            self.root.mkdir(parents=True, exist_ok=True)
            gitignore = self.root / ".gitignore"
            if not gitignore.exists():
                gitignore.write_text("*\n", encoding="utf-8")
            self._ready = True
        except OSError as exc:
            _log.warning(
                "cache directory unusable, continuing without cache",
                extra={"context": {"path": str(self.root), "error": str(exc)}},
            )
            self.enabled = False
        return self._ready

    def _path_for(self, key: str) -> Path:
        return self.root / key[:2] / f"{key}.json"

    def get(self, key: str) -> Any | None:
        """Return the cached payload for ``key``, or ``None`` on any miss."""
        if not self.enabled or not self._ensure_dir():
            return None
        path = self._path_for(key)
        try:
            raw = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            with self._lock:
                self.misses += 1
            return None
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            _log.debug("discarding corrupt cache entry", extra={"context": {"key": key}})
            try:
                path.unlink()
            except OSError:
                pass
            with self._lock:
                self.misses += 1
            return None
        with self._lock:
            self.hits += 1
        return payload.get("value")

    def set(self, key: str, value: Any) -> None:
        """Store ``value`` under ``key``. Failures are logged, never raised."""
        if not self.enabled or not self._ensure_dir():
            return
        path = self._path_for(key)
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            fd, tmp = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as handle:
                    json.dump({"version": CACHE_VERSION, "value": value}, handle)
                os.replace(tmp, path)
            except BaseException:
                try:
                    os.unlink(tmp)
                except OSError:
                    pass
                raise
        except OSError as exc:
            _log.debug(
                "cache write failed",
                extra={"context": {"key": key, "error": str(exc)}},
            )

    def clear(self) -> int:
        """Delete every entry. Returns the number of files removed."""
        if not self.root.exists():
            return 0
        removed = 0
        for path in self.root.rglob("*.json"):
            try:
                path.unlink()
                removed += 1
            except OSError:
                pass
        return removed

    @property
    def stats(self) -> dict[str, int]:
        with self._lock:
            return {"hits": self.hits, "misses": self.misses}

    def log_summary(self, logger: logging.Logger | None = None) -> None:
        (logger or _log).debug(
            "cache summary",
            extra={"context": {**self.stats, "path": str(self.root), "enabled": self.enabled}},
        )
