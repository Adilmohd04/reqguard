"""The five stage prompts load, are non-trivial, and carry no interpolation.

Owned by Track B. Not a task gate — hygiene that keeps a broken prompt file from
reaching a paid call.
"""

from __future__ import annotations

import pytest

from reqguard.agent import prompts


def test_all_five_names_present() -> None:
    assert set(prompts.NAMES) == {
        "analyze",
        "investigate",
        "plan",
        "repair",
        "baseline",
    }


@pytest.mark.parametrize("name", prompts.NAMES)
def test_each_prompt_loads_and_is_substantial(name: str) -> None:
    text = prompts.load(name)
    assert text.strip(), f"{name} is empty"
    # baseline is deliberately short (manual 5.6); the rest are real instructions.
    minimum = 120 if name == "baseline" else 400
    assert len(text) >= minimum, f"{name} looks truncated ({len(text)} chars)"


@pytest.mark.parametrize("name", prompts.NAMES)
def test_no_format_placeholders(name: str) -> None:
    """A prompt goes into the cached system block verbatim. A stray `{...}` or
    `$(...)` is a sign someone meant to interpolate — which would break the
    cache and defeat the point of a frozen system prompt."""
    text = prompts.load(name)
    for marker in ("{", "}", "$(", "%s", "{{", "}}"):
        assert marker not in text, f"{name} contains {marker!r} — no interpolation allowed"


def test_unknown_prompt_raises() -> None:
    with pytest.raises(KeyError):
        prompts.load("does-not-exist")


def test_all_prompts_returns_every_name() -> None:
    everything = prompts.all_prompts()
    assert set(everything) == set(prompts.NAMES)
    assert all(v.strip() for v in everything.values())


def test_plan_prompt_names_the_semantic_change_escape_hatch() -> None:
    """The `semantic_change` type and its written-justification rule are what
    make the killer case possible without becoming the default (manual 5.4)."""
    plan = prompts.load("plan")
    assert "semantic_change" in plan
    assert "rationale" in plan


def test_repair_prompt_states_both_conditions() -> None:
    repair = prompts.load("repair")
    assert "PASSES" in repair and "FAILS" in repair
