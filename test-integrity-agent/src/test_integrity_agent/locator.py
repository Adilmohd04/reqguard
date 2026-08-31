"""Map each requirement to the source spans that implement it.

Two passes, in this order:

1. **AST + lexical.** Index every function, method, and class under the
   configured source paths, then rank them against the requirement text by
   token overlap across the symbol name, its docstring, and its path. This pass
   is free, deterministic, and good enough on its own for well-named code.
2. **LLM.** Hand the top-ranked candidates to Claude with the requirement and
   ask which ones actually implement it. The model sees signatures, docstrings,
   and bodies -- it catches the cases where naming lies.

The second pass is cached on disk by content hash, so it runs once per
(requirement, code) pair no matter how often you scan.
"""

from __future__ import annotations

import ast
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .cache import ResponseCache
from .errors import LLMError, RepositoryError
from .llm import LLMClient
from .logging_setup import get_logger
from .models import Requirement, SourceSpan

_log = get_logger("locator")

MAX_CANDIDATES_TO_LLM = 12
MAX_SPANS_PER_REQUIREMENT = 3
MAX_BODY_CHARS = 1800

_STOPWORDS = frozenset(
    """
    a an the and or of to in on for with that this these those is are be been being was were
    it its as at by from not no must shall should never always may can cannot will would
    when if then than so such any all each every other same both more most some only just
    into over under after before during while about above below up down out off again
    system user users value values data code test tests case cases return returns
    """.split()
)

_TOKEN_RE = re.compile(r"[A-Za-z][A-Za-z0-9_]*")


def tokenize(text: str) -> set[str]:
    """Lowercase word tokens with snake_case and camelCase split apart."""
    tokens: set[str] = set()
    for match in _TOKEN_RE.finditer(text):
        word = match.group(0)
        parts = re.split(r"_+", word)
        for part in parts:
            for piece in re.findall(r"[A-Z]+(?![a-z])|[A-Z][a-z]+|[a-z]+|\d+", part) or [part]:
                lowered = piece.lower()
                if len(lowered) > 2 and lowered not in _STOPWORDS:
                    tokens.add(lowered)
    return tokens


@dataclass(frozen=True)
class Symbol:
    """One indexed function, method, or class."""

    path: str
    qualname: str
    kind: str
    start_line: int
    end_line: int
    signature: str
    docstring: str
    source: str

    @property
    def location(self) -> str:
        return f"{self.path}:{self.start_line}-{self.end_line}"

    def to_span(self, confidence: float, reason: str) -> SourceSpan:
        return SourceSpan(
            path=self.path,
            start_line=self.start_line,
            end_line=self.end_line,
            symbol=self.qualname,
            confidence=confidence,
            reason=reason,
        )


def _signature(node: ast.AST) -> str:
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        args = [a.arg for a in node.args.posonlyargs + node.args.args]
        if node.args.vararg:
            args.append("*" + node.args.vararg.arg)
        args.extend(a.arg for a in node.args.kwonlyargs)
        if node.args.kwarg:
            args.append("**" + node.args.kwarg.arg)
        prefix = "async def" if isinstance(node, ast.AsyncFunctionDef) else "def"
        return f"{prefix} {node.name}({', '.join(args)})"
    if isinstance(node, ast.ClassDef):
        return f"class {node.name}"
    return ""


def index_repository(
    repo: Path,
    source_paths: list[Path],
    exclude_dirs: frozenset[str],
) -> list[Symbol]:
    """Build a symbol index over every Python file under ``source_paths``."""
    symbols: list[Symbol] = []
    files: list[Path] = []
    for root in source_paths:
        if root.is_file() and root.suffix == ".py":
            files.append(root)
        elif root.is_dir():
            for candidate in sorted(root.rglob("*.py")):
                if any(part in exclude_dirs for part in candidate.parts):
                    continue
                files.append(candidate)

    if not files:
        raise RepositoryError(
            f"No Python files found under: {', '.join(str(p) for p in source_paths)}",
            hint="Check `source_paths` in tia.yaml -- it should point at your package.",
        )

    for path in files:
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            _log.warning(
                "skipping unreadable file",
                extra={"context": {"path": str(path), "error": str(exc)}},
            )
            continue
        try:
            tree = ast.parse(text)
        except SyntaxError as exc:
            _log.warning(
                "skipping file that does not parse",
                extra={"context": {"path": str(path), "error": str(exc)}},
            )
            continue

        lines = text.splitlines()
        rel = str(path.relative_to(repo)).replace("\\", "/")

        def visit(
            node: ast.AST, prefix: str, rel: str = rel, lines: list[str] = lines
        ) -> None:
            for child in ast.iter_child_nodes(node):
                if not isinstance(
                    child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)
                ):
                    continue
                qualname = f"{prefix}.{child.name}" if prefix else child.name
                start = child.lineno
                end = getattr(child, "end_lineno", None) or child.lineno
                body = "\n".join(lines[start - 1 : end])
                symbols.append(
                    Symbol(
                        path=rel,
                        qualname=qualname,
                        kind="class" if isinstance(child, ast.ClassDef) else "function",
                        start_line=start,
                        end_line=end,
                        signature=_signature(child),
                        docstring=(ast.get_docstring(child) or "").strip(),
                        source=body,
                    )
                )
                if isinstance(child, ast.ClassDef):
                    visit(child, qualname, rel, lines)

        visit(tree, "")

    _log.debug(
        "indexed repository",
        extra={"context": {"files": len(files), "symbols": len(symbols)}},
    )
    return symbols


def rank_candidates(requirement: Requirement, symbols: list[Symbol]) -> list[tuple[float, Symbol]]:
    """Score every symbol against the requirement. Highest first."""
    want = tokenize(requirement.text)
    if not want:
        return [(0.0, s) for s in symbols]

    scored: list[tuple[float, Symbol]] = []
    for symbol in symbols:
        name_tokens = tokenize(symbol.qualname)
        doc_tokens = tokenize(symbol.docstring)
        path_tokens = tokenize(symbol.path)
        body_tokens = tokenize(symbol.source)

        score = (
            3.0 * len(want & name_tokens)
            + 2.0 * len(want & doc_tokens)
            + 1.0 * len(want & path_tokens)
            + 0.5 * len(want & body_tokens)
        ) / len(want)
        # Prefer functions: a class span is usually too coarse to mutate well.
        if symbol.kind == "function":
            score *= 1.15
        scored.append((score, symbol))

    scored.sort(key=lambda pair: (-pair[0], pair[1].path, pair[1].start_line))
    return scored


_LOCATOR_SYSTEM = """\
You map written software requirements onto the code that implements them.

You will be given one requirement and a list of candidate code symbols from a \
repository. Decide which symbols contain the logic that would have to change \
for the requirement to be violated.

Rules:
- Select only symbols whose code actually enforces or implements the requirement. \
A symbol that merely mentions related words is not a match.
- Prefer the narrowest symbol that contains the deciding logic (a function over \
the class that holds it).
- Select at most 3 symbols. Selecting none is a valid and useful answer when the \
requirement is not implemented in the candidates shown.
- confidence is your probability that changing this symbol could violate the \
requirement: 1.0 means certain, 0.5 means plausible, below 0.3 means do not select it.
- reason must be one short clause naming the specific logic, not a restatement \
of the requirement."""

_LOCATOR_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "selections": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "index": {"type": "integer"},
                    "confidence": {"type": "number"},
                    "reason": {"type": "string"},
                },
                "required": ["index", "confidence", "reason"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["selections"],
    "additionalProperties": False,
}


def _candidate_block(index: int, symbol: Symbol) -> str:
    body = symbol.source
    if len(body) > MAX_BODY_CHARS:
        body = body[:MAX_BODY_CHARS] + "\n    # ... truncated"
    doc = f"\ndocstring: {symbol.docstring.splitlines()[0]}" if symbol.docstring else ""
    return (
        f"[{index}] {symbol.path}:{symbol.start_line}-{symbol.end_line} "
        f"({symbol.qualname}){doc}\n```python\n{body}\n```"
    )


class Locator:
    """Resolve requirements to source spans, with a cached LLM pass."""

    def __init__(
        self,
        symbols: list[Symbol],
        llm: LLMClient | None = None,
        cache: ResponseCache | None = None,
        max_spans: int = MAX_SPANS_PER_REQUIREMENT,
    ) -> None:
        self.symbols = symbols
        self.llm = llm
        self.cache = cache
        self.max_spans = max_spans

    def locate(self, requirement: Requirement) -> list[SourceSpan]:
        """Return the spans implementing ``requirement``, best first.

        Never raises for LLM problems: a failed model call degrades to the
        lexical ranking, which is worse but still produces mutable spans.
        """
        ranked = rank_candidates(requirement, self.symbols)
        if not ranked:
            return []

        shortlist = [s for score, s in ranked[:MAX_CANDIDATES_TO_LLM] if score > 0]
        if not shortlist:
            shortlist = [s for _, s in ranked[:MAX_CANDIDATES_TO_LLM]]

        if self.llm is not None and not self.llm.degraded:
            try:
                spans = self._locate_with_llm(requirement, shortlist)
                if spans:
                    return spans[: self.max_spans]
                _log.debug(
                    "model selected no symbols; falling back to lexical ranking",
                    extra={"context": {"requirement": requirement.id}},
                )
            except LLMError as exc:
                _log.warning(
                    "locator LLM pass failed; using lexical ranking",
                    extra={"context": {"requirement": requirement.id, "error": str(exc)}},
                )

        return [
            symbol.to_span(
                confidence=min(1.0, round(score, 3)),
                reason="lexical match on names and docstrings",
            )
            for score, symbol in ranked[: self.max_spans]
            if score > 0
        ] or [ranked[0][1].to_span(0.1, "best available lexical match")]

    def _locate_with_llm(
        self, requirement: Requirement, shortlist: list[Symbol]
    ) -> list[SourceSpan]:
        assert self.llm is not None
        blocks = "\n\n".join(_candidate_block(i, s) for i, s in enumerate(shortlist))
        prompt = (
            f"Requirement {requirement.id}:\n{requirement.text}\n\n"
            f"Candidate symbols:\n\n{blocks}\n\n"
            f"Which candidates implement this requirement? Answer with their indices."
        )
        payload = self.llm.structured(
            system=_LOCATOR_SYSTEM,
            prompt=prompt,
            schema=_LOCATOR_SCHEMA,
            max_tokens=2000,
            effort="low",
            cache_scope="locate",
        )

        spans: list[SourceSpan] = []
        for selection in payload.get("selections", []):
            if not isinstance(selection, dict):
                continue
            index = selection.get("index")
            if not isinstance(index, int) or not 0 <= index < len(shortlist):
                continue
            try:
                confidence = float(selection.get("confidence", 0.0))
            except (TypeError, ValueError):
                confidence = 0.0
            if confidence < 0.3:
                continue
            reason = str(selection.get("reason", "")).strip()[:200]
            spans.append(shortlist[index].to_span(min(1.0, confidence), reason))

        spans.sort(key=lambda s: -s.confidence)
        return spans
