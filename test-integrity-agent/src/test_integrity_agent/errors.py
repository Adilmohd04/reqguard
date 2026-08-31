"""Exception hierarchy for test-integrity-agent.

Every error a user can trigger from the CLI is a subclass of :class:`TIAError`
and carries a message written to be read by a human at a terminal: what went
wrong, and what to do about it.
"""

from __future__ import annotations


class TIAError(Exception):
    """Base class for every error raised by this package.

    Attributes:
        message: The human-readable explanation.
        hint: An optional actionable next step shown after the message.
    """

    exit_code: int = 1

    def __init__(self, message: str, hint: str | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.hint = hint

    def __str__(self) -> str:  # pragma: no cover - trivial
        if self.hint:
            return f"{self.message}\n\nHint: {self.hint}"
        return self.message


class ConfigError(TIAError):
    """The tia.yaml file is missing, unreadable, or contains invalid values."""

    exit_code = 2


class RequirementParseError(TIAError):
    """A requirements source could not be parsed into requirements."""

    exit_code = 2


class NoRequirementsError(TIAError):
    """Parsing succeeded but produced zero requirements."""

    exit_code = 2


class RepositoryError(TIAError):
    """The target repository is missing, empty, or not usable."""

    exit_code = 2


class MissingAPIKeyError(TIAError):
    """ANTHROPIC_API_KEY is not set and offline mode was not requested."""

    exit_code = 3

    def __init__(self) -> None:
        super().__init__(
            "ANTHROPIC_API_KEY is not set, so requirement-directed mutations "
            "cannot be generated.",
            hint=(
                "Set the key for this shell and re-run:\n"
                '  macOS/Linux:  export ANTHROPIC_API_KEY="sk-ant-..."\n'
                '  PowerShell:   $env:ANTHROPIC_API_KEY = "sk-ant-..."\n'
                "  cmd.exe:      set ANTHROPIC_API_KEY=sk-ant-...\n\n"
                "Or run without an LLM using AST-only mutation operators:\n"
                "  tia scan --offline"
            ),
        )


class LLMError(TIAError):
    """The Anthropic API call failed in a way the caller should know about."""

    exit_code = 4


class BaselineFailureError(TIAError):
    """The unmutated test suite does not pass, so mutation results are meaningless."""

    exit_code = 5

    def __init__(self, command: str, output: str) -> None:
        tail = "\n".join(output.strip().splitlines()[-25:])
        super().__init__(
            f"The test suite fails before any mutation is applied, so a failing "
            f"mutant would prove nothing.\n\n"
            f"Command: {command}\n\n"
            f"Last lines of output:\n{tail}",
            hint=(
                "Get the suite green first, or point tia at a subset that is "
                "green via --test-command (for example: "
                '--test-command "pytest tests/unit -q").'
            ),
        )


class SuiteCommandError(TIAError):
    """The configured test command could not be executed at all."""

    exit_code = 5


def split_command(command: str) -> list[str]:
    """Split a test command into argv, correctly on Windows as well as POSIX.

    ``shlex.split`` in its default POSIX mode treats a backslash as an escape,
    which silently destroys any Windows path in a test command: the backslashes
    vanish and the run fails with a confusing "not found". On Windows we split
    in non-POSIX mode, which preserves backslashes, and strip the surrounding
    quotes ourselves.
    """
    import os
    import shlex

    if os.name == "nt":
        parts = shlex.split(command, posix=False)
        return [
            part[1:-1] if len(part) >= 2 and part[0] == part[-1] == '"' else part
            for part in parts
        ]
    return shlex.split(command)
