"""Parse requirements out of markdown, YAML, or Python docstrings.

Three formats, one output: a list of :class:`~test_integrity_agent.models.Requirement`
with stable ids.

**Markdown.** If a line carries an explicit id (``REQ-003``, ``SEC-12``, either
bare or in brackets), that id is used verbatim and survives reordering of the
file. Otherwise every bullet and every requirement-shaped heading becomes a
requirement, numbered ``REQ-001``, ``REQ-002``, ... in document order. Explicit
ids are worth the small effort: auto-numbered ids shift when you insert a
requirement in the middle, which resets that requirement's cache entry.

**YAML.** A list of strings, a list of ``{id, text, tags}`` mappings, or a
mapping with a ``requirements:`` key holding either of those.

**Python.** Docstrings are scanned for ``Requirement:`` lines and for explicit
ids; the enclosing function or class name becomes part of the source label.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import yaml

from .errors import NoRequirementsError, RequirementParseError
from .logging_setup import get_logger
from .models import Requirement

_log = get_logger("requirements")

ID_PATTERN = re.compile(r"^\[?\s*([A-Z][A-Z0-9]{1,9}-\d{1,4})\s*\]?\s*[:.\)-]?\s+(.*)$")
INLINE_ID_PATTERN = re.compile(r"\b([A-Z][A-Z0-9]{1,9}-\d{1,4})\b")
BULLET_PATTERN = re.compile(r"^\s*(?:[-*+]|\d+[.)])\s+(.*)$")
HEADING_PATTERN = re.compile(r"^\s*#{1,6}\s+(.*)$")
DOCSTRING_REQ_PATTERN = re.compile(r"^\s*Requirements?\s*:\s*(.*)$", re.IGNORECASE)

MODAL_WORDS = (
    "must",
    "must not",
    "shall",
    "should",
    "never",
    "always",
    "cannot",
    "may not",
    "is required",
    "are required",
    "guarantee",
)

MIN_REQUIREMENT_CHARS = 12


def looks_like_requirement(text: str) -> bool:
    """Heuristic: is this sentence stating a rule, rather than being prose?"""
    lowered = text.lower()
    return any(word in lowered for word in MODAL_WORDS)


def _clean(text: str) -> str:
    text = text.strip()
    text = re.sub(r"\*\*(.+?)\*\*", r"\1", text)
    text = re.sub(r"`([^`]+)`", r"\1", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip(" .;")


class _IdAllocator:
    """Hands out unique ids, preferring explicit ones."""

    def __init__(self, prefix: str = "REQ") -> None:
        self.prefix = prefix
        self._counter = 0
        self._seen: set[str] = set()

    def take(self, explicit: str | None) -> str:
        if explicit:
            candidate = explicit
        else:
            self._counter += 1
            candidate = f"{self.prefix}-{self._counter:03d}"
        unique = candidate
        suffix = 2
        while unique in self._seen:
            unique = f"{candidate}-{suffix}"
            suffix += 1
        self._seen.add(unique)
        return unique


def parse_markdown(path: Path, text: str, allocator: _IdAllocator) -> list[Requirement]:
    """Extract requirements from markdown source."""
    requirements: list[Requirement] = []
    lines = text.splitlines()
    in_code_fence = False
    index = 0

    while index < len(lines):
        raw = lines[index]
        lineno = index + 1
        index += 1
        stripped = raw.strip()
        if stripped.startswith("```") or stripped.startswith("~~~"):
            in_code_fence = not in_code_fence
            continue
        if in_code_fence or not stripped:
            continue

        body: str | None = None
        heading = HEADING_PATTERN.match(raw)
        bullet = BULLET_PATTERN.match(raw)
        if bullet:
            body = bullet.group(1)
        elif heading:
            body = heading.group(1)
        else:
            # A bare line only counts when it carries an explicit id.
            match = ID_PATTERN.match(stripped)
            if match:
                body = stripped

        if body is None:
            continue

        # A requirement wrapped over several lines is one requirement. Absorb
        # the indented continuation lines that follow a bullet.
        if bullet:
            marker_indent = len(raw) - len(raw.lstrip())
            while index < len(lines):
                follow = lines[index]
                follow_stripped = follow.strip()
                if not follow_stripped:
                    break
                if BULLET_PATTERN.match(follow) or HEADING_PATTERN.match(follow):
                    break
                if follow_stripped.startswith("```") or follow_stripped.startswith("~~~"):
                    break
                if len(follow) - len(follow.lstrip()) <= marker_indent:
                    break
                body = f"{body} {follow_stripped}"
                index += 1

        explicit: str | None = None
        match = ID_PATTERN.match(body.strip())
        if match:
            explicit, body = match.group(1), match.group(2)
        else:
            inline = INLINE_ID_PATTERN.search(body)
            if inline and body.strip().startswith(inline.group(1)):
                explicit = inline.group(1)
                body = body[inline.end() :]

        cleaned = _clean(body)
        if len(cleaned) < MIN_REQUIREMENT_CHARS:
            continue
        if heading and not explicit and not looks_like_requirement(cleaned):
            # Section titles ("Requirements", "Overview") are not requirements.
            continue

        requirements.append(
            Requirement(
                id=allocator.take(explicit),
                text=cleaned,
                source=f"{path.name}:{lineno}",
            )
        )

    return requirements


def parse_yaml(path: Path, text: str, allocator: _IdAllocator) -> list[Requirement]:
    """Extract requirements from a YAML file."""
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise RequirementParseError(f"{path} is not valid YAML: {exc}") from exc

    if isinstance(data, dict):
        if "requirements" not in data:
            raise RequirementParseError(
                f"{path} is a YAML mapping without a `requirements` key.",
                hint="Either use a top-level list, or a mapping with `requirements:`.",
            )
        data = data["requirements"]

    if not isinstance(data, list):
        raise RequirementParseError(
            f"{path} must contain a list of requirements (strings or mappings)."
        )

    requirements: list[Requirement] = []
    for index, item in enumerate(data, start=1):
        if isinstance(item, str):
            cleaned = _clean(item)
            if len(cleaned) < MIN_REQUIREMENT_CHARS:
                continue
            requirements.append(
                Requirement(
                    id=allocator.take(None),
                    text=cleaned,
                    source=f"{path.name}:[{index}]",
                )
            )
        elif isinstance(item, dict):
            raw_text = item.get("text") or item.get("requirement") or item.get("description")
            if not isinstance(raw_text, str) or not raw_text.strip():
                raise RequirementParseError(
                    f"{path}: requirement #{index} has no `text` field."
                )
            explicit = item.get("id")
            if explicit is not None and not isinstance(explicit, str):
                raise RequirementParseError(f"{path}: requirement #{index} has a non-string `id`.")
            tags = item.get("tags") or []
            if not isinstance(tags, list) or not all(isinstance(t, str) for t in tags):
                raise RequirementParseError(
                    f"{path}: requirement #{index} has `tags` that are not a list of strings."
                )
            requirements.append(
                Requirement(
                    id=allocator.take(explicit),
                    text=_clean(raw_text),
                    source=f"{path.name}:[{index}]",
                    tags=tuple(tags),
                )
            )
        else:
            raise RequirementParseError(
                f"{path}: requirement #{index} must be a string or a mapping, "
                f"got {type(item).__name__}."
            )
    return requirements


def _docstring_requirements(doc: str) -> list[tuple[str | None, str]]:
    """Pull ``Requirement:`` lines and explicit-id lines out of one docstring."""
    found: list[tuple[str | None, str]] = []
    lines = doc.splitlines()
    for index, raw in enumerate(lines):
        stripped = raw.strip()
        if not stripped:
            continue
        marker = DOCSTRING_REQ_PATTERN.match(stripped)
        if marker:
            body = marker.group(1).strip()
            if not body:
                # "Requirements:" header -- take the indented bullets beneath it.
                for follow in lines[index + 1 :]:
                    follow_stripped = follow.strip()
                    if not follow_stripped:
                        break
                    bullet = BULLET_PATTERN.match(follow)
                    text = bullet.group(1) if bullet else follow_stripped
                    match = ID_PATTERN.match(text)
                    if match:
                        found.append((match.group(1), _clean(match.group(2))))
                    else:
                        found.append((None, _clean(text)))
                continue
            match = ID_PATTERN.match(body)
            if match:
                found.append((match.group(1), _clean(match.group(2))))
            else:
                found.append((None, _clean(body)))
            continue
        match = ID_PATTERN.match(stripped)
        if match:
            found.append((match.group(1), _clean(match.group(2))))
    return [(rid, text) for rid, text in found if len(text) >= MIN_REQUIREMENT_CHARS]


def parse_python(path: Path, text: str, allocator: _IdAllocator, label: str) -> list[Requirement]:
    """Extract requirements from docstrings in a Python module."""
    try:
        tree = ast.parse(text)
    except SyntaxError as exc:
        raise RequirementParseError(f"{path} could not be parsed as Python: {exc}") from exc

    requirements: list[Requirement] = []

    def visit(node: ast.AST, qualname: str) -> None:
        doc = ast.get_docstring(node) if isinstance(
            node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)
        ) else None
        if doc:
            for explicit, body in _docstring_requirements(doc):
                requirements.append(
                    Requirement(
                        id=allocator.take(explicit),
                        text=body,
                        source=f"{label}:{qualname}" if qualname else label,
                    )
                )
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
                child_name = f"{qualname}.{child.name}" if qualname else child.name
                visit(child, child_name)

    visit(tree, "")
    return requirements


def parse_file(path: Path, allocator: _IdAllocator, root: Path | None = None) -> list[Requirement]:
    """Parse a single requirements file, dispatching on its suffix."""
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise RequirementParseError(f"Could not read {path}: {exc}") from exc

    label = str(path.relative_to(root)).replace("\\", "/") if root else path.name
    suffix = path.suffix.lower()
    if suffix in {".md", ".markdown", ".txt", ".rst"}:
        return parse_markdown(path, text, allocator)
    if suffix in {".yaml", ".yml"}:
        return parse_yaml(path, text, allocator)
    if suffix == ".py":
        return parse_python(path, text, allocator, label)
    raise RequirementParseError(
        f"Unsupported requirements file type: {path.name}",
        hint="Supported: .md, .markdown, .txt, .rst, .yaml, .yml, .py",
    )


def load_requirements(
    paths: list[Path],
    root: Path | None = None,
    exclude_dirs: frozenset[str] = frozenset(),
) -> list[Requirement]:
    """Load requirements from files and/or directories.

    Directories are walked for ``.md`` and ``.py`` files. Raises
    :class:`NoRequirementsError` when nothing at all is found, because a scan of
    zero requirements is a silent success that means nothing.
    """
    allocator = _IdAllocator()
    requirements: list[Requirement] = []
    files: list[Path] = []

    for path in paths:
        if path.is_dir():
            for candidate in sorted(path.rglob("*")):
                if not candidate.is_file():
                    continue
                if any(part in exclude_dirs for part in candidate.parts):
                    continue
                if candidate.suffix.lower() in {".md", ".markdown", ".py", ".yaml", ".yml"}:
                    files.append(candidate)
        else:
            files.append(path)

    for path in files:
        parsed = parse_file(path, allocator, root)
        _log.debug(
            "parsed requirements file",
            extra={"context": {"path": str(path), "count": len(parsed)}},
        )
        requirements.extend(parsed)

    if not requirements:
        listed = ", ".join(str(p) for p in paths) or "(nothing)"
        raise NoRequirementsError(
            f"No requirements were found in: {listed}",
            hint=(
                "In markdown, requirements are bullets or headings -- for example:\n"
                "  - REQ-001: A partially fulfilled order must never be reported "
                "as completed.\n"
                "In YAML, use a list of strings or of {id, text} mappings."
            ),
        )
    return requirements


def select(requirements: list[Requirement], wanted: list[str]) -> list[Requirement]:
    """Filter ``requirements`` down to the ids in ``wanted`` (case-insensitive)."""
    if not wanted:
        return requirements
    index = {r.id.lower(): r for r in requirements}
    chosen: list[Requirement] = []
    missing: list[str] = []
    for want in wanted:
        found = index.get(want.lower().strip())
        if found is None:
            missing.append(want)
        else:
            chosen.append(found)
    if missing:
        available = ", ".join(r.id for r in requirements[:20])
        more = " ..." if len(requirements) > 20 else ""
        raise RequirementParseError(
            f"No such requirement id: {', '.join(missing)}",
            hint=f"Known ids: {available}{more}",
        )
    return chosen
