"""Requirement parsing across markdown, YAML, and Python docstrings."""

from __future__ import annotations

from pathlib import Path

import pytest

from test_integrity_agent.errors import NoRequirementsError, RequirementParseError
from test_integrity_agent.requirements import load_requirements, select


def write(tmp_path: Path, name: str, text: str) -> Path:
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return path


def test_markdown_explicit_ids_are_preserved(tmp_path: Path) -> None:
    path = write(
        tmp_path,
        "REQUIREMENTS.md",
        "# Rules\n\n"
        "- REQ-001: Orders must never complete before shipping.\n"
        "- SEC-42: Passwords must never be written to a log.\n",
    )
    parsed = load_requirements([path], tmp_path)
    assert [r.id for r in parsed] == ["REQ-001", "SEC-42"]
    assert parsed[0].text == "Orders must never complete before shipping"
    assert parsed[0].source.startswith("REQUIREMENTS.md:")


def test_markdown_without_ids_is_autonumbered(tmp_path: Path) -> None:
    path = write(
        tmp_path,
        "rules.md",
        "* A refund must never exceed the amount paid.\n"
        "* Shipping must always be free above one hundred.\n",
    )
    parsed = load_requirements([path], tmp_path)
    assert [r.id for r in parsed] == ["REQ-001", "REQ-002"]


def test_markdown_ignores_code_fences_and_short_lines(tmp_path: Path) -> None:
    path = write(
        tmp_path,
        "rules.md",
        "# Requirements\n\n"
        "```\n- this must never be parsed as a requirement\n```\n\n"
        "- ok\n"
        "- A discount must never exceed half the subtotal.\n",
    )
    parsed = load_requirements([path], tmp_path)
    assert len(parsed) == 1
    assert "discount" in parsed[0].text


def test_markdown_headings_only_count_when_they_state_a_rule(tmp_path: Path) -> None:
    path = write(
        tmp_path,
        "rules.md",
        "# Requirements\n\n## Overview of the system\n\n"
        "### A partial order must never be reported as completed\n",
    )
    parsed = load_requirements([path], tmp_path)
    assert len(parsed) == 1
    assert parsed[0].text.startswith("A partial order")


def test_duplicate_ids_are_made_unique(tmp_path: Path) -> None:
    path = write(
        tmp_path,
        "rules.md",
        "- REQ-001: A refund must never exceed the amount paid.\n"
        "- REQ-001: A discount must never exceed the subtotal.\n",
    )
    parsed = load_requirements([path], tmp_path)
    assert [r.id for r in parsed] == ["REQ-001", "REQ-001-2"]


def test_yaml_list_of_strings(tmp_path: Path) -> None:
    path = write(
        tmp_path,
        "reqs.yaml",
        "- A refund must never exceed the amount paid.\n"
        "- An empty order must be rejected.\n",
    )
    parsed = load_requirements([path], tmp_path)
    assert len(parsed) == 2
    assert parsed[0].id == "REQ-001"


def test_yaml_mappings_carry_ids_and_tags(tmp_path: Path) -> None:
    path = write(
        tmp_path,
        "reqs.yaml",
        "requirements:\n"
        "  - id: PAY-7\n"
        "    text: A refund must never exceed the amount paid.\n"
        "    tags: [payments, critical]\n",
    )
    parsed = load_requirements([path], tmp_path)
    assert parsed[0].id == "PAY-7"
    assert parsed[0].tags == ("payments", "critical")


def test_yaml_missing_text_is_an_error(tmp_path: Path) -> None:
    path = write(tmp_path, "reqs.yaml", "- id: PAY-7\n  note: nothing useful\n")
    with pytest.raises(RequirementParseError, match="no `text` field"):
        load_requirements([path], tmp_path)


def test_invalid_yaml_reports_the_file(tmp_path: Path) -> None:
    path = write(tmp_path, "reqs.yaml", "requirements: [unclosed\n")
    with pytest.raises(RequirementParseError, match="not valid YAML"):
        load_requirements([path], tmp_path)


def test_python_docstrings(tmp_path: Path) -> None:
    path = write(
        tmp_path,
        "service.py",
        '"""Module.\n\nRequirement: PAY-1: A refund must never exceed the amount paid.\n"""\n\n\n'
        "def charge():\n"
        '    """Charge a card.\n\n    REQ-009: A charge must never be applied twice.\n    """\n'
        "    return None\n",
    )
    parsed = load_requirements([path], tmp_path)
    ids = [r.id for r in parsed]
    assert "PAY-1" in ids
    assert "REQ-009" in ids
    assert any(r.source.endswith("charge") for r in parsed)


def test_empty_source_raises_with_a_usable_hint(tmp_path: Path) -> None:
    path = write(tmp_path, "rules.md", "# Nothing here\n\nJust prose, no bullets.\n")
    with pytest.raises(NoRequirementsError) as exc:
        load_requirements([path], tmp_path)
    assert "bullets or headings" in (exc.value.hint or "")


def test_unsupported_extension(tmp_path: Path) -> None:
    path = write(tmp_path, "rules.docx", "irrelevant")
    with pytest.raises(RequirementParseError, match="Unsupported requirements file type"):
        load_requirements([path], tmp_path)


def test_directory_is_walked(tmp_path: Path) -> None:
    (tmp_path / "docs").mkdir()
    write(tmp_path / "docs", "a.md", "- A refund must never exceed the amount paid.\n")
    write(tmp_path / "docs", "b.md", "- An empty order must be rejected outright.\n")
    parsed = load_requirements([tmp_path / "docs"], tmp_path)
    assert len(parsed) == 2


def test_select_filters_and_reports_unknown_ids(tmp_path: Path) -> None:
    path = write(
        tmp_path,
        "rules.md",
        "- REQ-001: A refund must never exceed the amount paid.\n"
        "- REQ-002: An empty order must be rejected.\n",
    )
    parsed = load_requirements([path], tmp_path)
    assert [r.id for r in select(parsed, ["req-002"])] == ["REQ-002"]
    with pytest.raises(RequirementParseError, match="No such requirement id"):
        select(parsed, ["REQ-999"])


def test_wrapped_bullets_are_one_requirement(tmp_path: Path) -> None:
    path = write(
        tmp_path,
        "rules.md",
        "- REQ-001: An order must never be reported as completed unless every\n"
        "  line item is fully shipped.\n"
        "- REQ-002: A discount must never exceed half the subtotal.\n",
    )
    parsed = load_requirements([path], tmp_path)
    assert len(parsed) == 2
    assert parsed[0].text.endswith("every line item is fully shipped")
    assert parsed[1].id == "REQ-002"


def test_a_wrapped_bullet_stops_at_the_next_bullet_or_blank_line(tmp_path: Path) -> None:
    path = write(
        tmp_path,
        "rules.md",
        "- REQ-001: A refund must never exceed the amount\n"
        "  actually paid.\n"
        "\n"
        "Some prose that must not be absorbed into the requirement.\n",
    )
    parsed = load_requirements([path], tmp_path)
    assert len(parsed) == 1
    assert "prose" not in parsed[0].text
