"""The Anthropic client wrapper.

Everything that talks to Claude goes through :class:`LLMClient`. It owns four
concerns the rest of the package should not have to think about:

* **The key.** Read from ``ANTHROPIC_API_KEY`` only, never from a config file,
  never logged, never included in an error message or a report.
* **Structured output.** Every call declares a JSON schema via
  ``output_config.format``, so a response is either valid against the schema or
  an error -- there is no prose-parsing anywhere in this package.
* **The disk cache.** Identical (model, prompt, schema) triples are answered
  from disk, which is what makes a re-run of ``tia scan`` nearly free.
* **Degradation.** A dead network, an exhausted quota, or a refusal turns into
  :class:`~test_integrity_agent.errors.LLMError`; callers catch it and fall back
  to AST operators rather than crashing the run.
"""

from __future__ import annotations

import json
import os
import threading
from dataclasses import dataclass, field
from typing import Any

import anthropic

from .cache import ResponseCache, content_key
from .errors import LLMError, MissingAPIKeyError
from .logging_setup import get_logger

_log = get_logger("llm")

# Models that accept adaptive thinking and output_config.effort. Anything not
# listed here gets a plain request, because sending an unsupported parameter is
# a 400 at runtime rather than a warning.
_ADAPTIVE_MODELS = frozenset(
    {
        "claude-fable-5",
        "claude-mythos-5",
        "claude-opus-5",
        "claude-opus-4-8",
        "claude-opus-4-7",
        "claude-opus-4-6",
        "claude-sonnet-5",
        "claude-sonnet-4-6",
    }
)


@dataclass
class Usage:
    """Token counters accumulated across every call in a run."""

    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_input_tokens: int = 0
    cache_creation_input_tokens: int = 0
    calls: int = 0
    cached_calls: int = 0
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def add(self, usage: Any) -> None:
        with self._lock:
            self.calls += 1
            self.input_tokens += int(getattr(usage, "input_tokens", 0) or 0)
            self.output_tokens += int(getattr(usage, "output_tokens", 0) or 0)
            self.cache_read_input_tokens += int(getattr(usage, "cache_read_input_tokens", 0) or 0)
            self.cache_creation_input_tokens += int(
                getattr(usage, "cache_creation_input_tokens", 0) or 0
            )

    def add_cache_hit(self) -> None:
        with self._lock:
            self.cached_calls += 1

    def to_dict(self) -> dict[str, int]:
        with self._lock:
            return {
                "calls": self.calls,
                "cached_calls": self.cached_calls,
                "input_tokens": self.input_tokens,
                "output_tokens": self.output_tokens,
                "cache_read_input_tokens": self.cache_read_input_tokens,
                "cache_creation_input_tokens": self.cache_creation_input_tokens,
            }

    def cost_usd(self, price_in_per_mtok: float, price_out_per_mtok: float) -> float:
        """Estimate spend. Cache reads are billed at ~0.1x, writes at ~1.25x."""
        with self._lock:
            fresh_in = self.input_tokens / 1_000_000 * price_in_per_mtok
            cached_in = self.cache_read_input_tokens / 1_000_000 * price_in_per_mtok * 0.1
            written_in = (
                self.cache_creation_input_tokens / 1_000_000 * price_in_per_mtok * 1.25
            )
            out = self.output_tokens / 1_000_000 * price_out_per_mtok
        return fresh_in + cached_in + written_in + out


class LLMClient:
    """A small, synchronous, thread-safe facade over ``anthropic.Anthropic``."""

    def __init__(
        self,
        model: str,
        cache: ResponseCache | None = None,
        max_retries: int = 3,
        timeout: float = 120.0,
    ) -> None:
        key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
        if not key:
            raise MissingAPIKeyError()
        self.model = model
        self.cache = cache
        self.usage = Usage()
        # The key is handed to the SDK and dropped; it is never kept on self.
        self._client = anthropic.Anthropic(
            api_key=key,
            max_retries=max_retries,
            timeout=timeout,
        )
        self._degraded_reason: str | None = None
        self._lock = threading.Lock()

    # ---- health -------------------------------------------------------

    @property
    def degraded(self) -> bool:
        """True once a call has failed in a way that makes further calls pointless."""
        with self._lock:
            return self._degraded_reason is not None

    @property
    def degraded_reason(self) -> str:
        with self._lock:
            return self._degraded_reason or ""

    def _mark_degraded(self, reason: str) -> None:
        with self._lock:
            if self._degraded_reason is None:
                self._degraded_reason = reason
                _log.warning(
                    "falling back to AST mutation operators for the rest of this run",
                    extra={"context": {"reason": reason}},
                )

    # ---- the one call -------------------------------------------------

    def structured(
        self,
        *,
        system: str,
        prompt: str,
        schema: dict[str, Any],
        max_tokens: int = 8000,
        effort: str = "medium",
        cache_scope: str = "generic",
    ) -> dict[str, Any]:
        """Ask the model for JSON matching ``schema``.

        Returns the decoded object. Raises :class:`LLMError` if the request
        fails, is refused, or the model is unreachable.
        """
        key = content_key(cache_scope, self.model, system, prompt, schema)
        if self.cache is not None:
            hit = self.cache.get(key)
            if hit is not None:
                self.usage.add_cache_hit()
                _log.debug("cache hit", extra={"context": {"scope": cache_scope}})
                return dict(hit)

        if self.degraded:
            raise LLMError(f"LLM unavailable: {self.degraded_reason}")

        params: dict[str, Any] = {
            "model": self.model,
            "max_tokens": max_tokens,
            "system": [
                {
                    "type": "text",
                    "text": system,
                    "cache_control": {"type": "ephemeral"},
                }
            ],
            "messages": [{"role": "user", "content": prompt}],
            "output_config": {"format": {"type": "json_schema", "schema": schema}},
        }
        if self.model in _ADAPTIVE_MODELS:
            params["thinking"] = {"type": "adaptive"}
            params["output_config"]["effort"] = effort

        try:
            response = self._client.messages.create(**params)
        except anthropic.AuthenticationError as exc:
            self._mark_degraded("the API key was rejected")
            raise LLMError(
                "Anthropic rejected the API key.",
                hint="Check that ANTHROPIC_API_KEY holds a current, active key.",
            ) from exc
        except anthropic.PermissionDeniedError as exc:
            self._mark_degraded("the API key lacks permission for this model")
            raise LLMError(
                f"The API key does not have access to model {self.model!r}.",
                hint="Pick a model your key can use with --model, e.g. claude-haiku-4-5.",
            ) from exc
        except anthropic.NotFoundError as exc:
            self._mark_degraded(f"model {self.model!r} not found")
            raise LLMError(
                f"Model {self.model!r} does not exist.",
                hint="Use a current model id, e.g. claude-sonnet-4-6 or claude-opus-5.",
            ) from exc
        except anthropic.RateLimitError as exc:
            raise LLMError(
                "Rate limited by the Anthropic API after retries.",
                hint="Lower --workers, or wait and re-run -- cached work is not repeated.",
            ) from exc
        except anthropic.BadRequestError as exc:
            raise LLMError(f"The Anthropic API rejected the request: {exc}") from exc
        except anthropic.APIConnectionError as exc:
            self._mark_degraded("could not reach the Anthropic API")
            raise LLMError(
                "Could not reach the Anthropic API.",
                hint="Check network access, then re-run. Or use --offline for AST-only mode.",
            ) from exc
        except anthropic.APIStatusError as exc:
            if exc.status_code >= 500:
                raise LLMError(f"Anthropic server error ({exc.status_code}).") from exc
            raise LLMError(f"Anthropic API error ({exc.status_code}): {exc}") from exc

        self.usage.add(response.usage)

        if getattr(response, "stop_reason", None) == "refusal":
            details = getattr(response, "stop_details", None)
            category = getattr(details, "category", None) if details else None
            raise LLMError(f"The model declined to answer (category: {category or 'unknown'}).")

        text = next((b.text for b in response.content if b.type == "text"), "")
        if not text.strip():
            raise LLMError("The model returned an empty response.")
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError as exc:  # pragma: no cover - schema makes this unreachable
            raise LLMError(f"The model returned text that is not valid JSON: {exc}") from exc
        if not isinstance(parsed, dict):
            raise LLMError("The model returned JSON that is not an object.")

        if self.cache is not None:
            self.cache.set(key, parsed)
        return parsed


def probe_api_key() -> None:
    """Raise :class:`MissingAPIKeyError` when no key is set. Never returns the key."""
    if not os.environ.get("ANTHROPIC_API_KEY", "").strip():
        raise MissingAPIKeyError()
