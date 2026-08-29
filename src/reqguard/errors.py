"""ReqGuard exception hierarchy.

FROZEN INTERFACE -- owned by the Supervisor (crew role S). See models.py.

Track A raises MutationRejected and PathEscape.
Track B raises AgentError and ReqGuardInternalError.
Track C catches ReqGuardError to record a case as inconclusive, never to hide it.
"""

from __future__ import annotations

__all__ = [
    "ReqGuardError",
    "ConfigError",
    "PathEscape",
    "MutationRejected",
    "EnvironmentError_",
    "AgentError",
    "ReqGuardInternalError",
]


class ReqGuardError(Exception):
    """Base for everything ReqGuard raises deliberately."""


class ConfigError(ReqGuardError):
    """reqguard.yaml is missing, malformed, or names an unsupported language."""


class PathEscape(ReqGuardError):
    """A path resolved outside the sandbox root.

    Raised by verification.safety.safe_resolve. Never caught and retried --
    it means a tool or a mutation tried to leave the sandbox.
    """


class MutationRejected(ReqGuardError):
    """A MutationSpec failed one of the six gates (Doc 3 section 6.4).

    The `gate` attribute names which one, so the planner can be re-prompted
    with a specific failure rather than a generic one.
    """

    def __init__(self, gate: str, detail: str = "") -> None:
        self.gate = gate
        self.detail = detail
        super().__init__(f"{gate}: {detail}" if detail else gate)


class EnvironmentError_(ReqGuardError):
    """The test environment failed -- missing dependency, collection error,
    timeout. Maps to RunStatus.ENVIRONMENT_ERROR.

    Trailing underscore avoids shadowing the builtin. Import as:
        from reqguard.errors import EnvironmentError_ as EnvError
    """


class AgentError(ReqGuardError):
    """A model call returned something unusable after retries."""


class ReqGuardInternalError(ReqGuardError):
    """An invariant we believe cannot be violated was violated.

    Deliberately loud. Example: stop_reason == "pause_turn" when no server-side
    tools are declared (Doc 3 section 10.3).
    """
