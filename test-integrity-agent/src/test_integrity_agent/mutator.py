"""Generate mutations that violate a specific requirement.

This is what separates the tool from blind mutation testing. The LLM is shown
one requirement and the code that implements it, and asked for a *minimal,
plausible* edit that breaks the requirement while leaving the code syntactically
valid and readable -- the kind of change that gets written by accident during a
refactor and slips through review.

Every candidate is validated before it is allowed near the test suite:

* the mutated file must parse;
* the mutated span must differ from the original in its AST, not just its text
  (so reformatting, comment edits, and docstring rewrites are rejected);
* duplicates are collapsed by content hash.

When the LLM is unavailable, :func:`ast_mutations` produces mechanical operators
over the same spans. They are not requirement-aware -- a surviving AST mutant is
weaker evidence than a surviving LLM mutant -- but they keep the pipeline
producing a real, executed result instead of an error.
"""

from __future__ import annotations

import ast
import copy
import difflib
import textwrap
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from .errors import LLMError
from .llm import LLMClient
from .logging_setup import get_logger
from .models import Mutation, MutationSource, Requirement, SourceSpan

_log = get_logger("mutator")

MAX_SPAN_CHARS = 6000


# ---------------------------------------------------------------------------
# Validation helpers
# ---------------------------------------------------------------------------


def _strip_docstrings(tree: ast.AST) -> ast.AST:
    """Remove docstrings so a docstring rewrite does not read as a code change."""
    for node in ast.walk(tree):
        if not isinstance(
            node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)
        ):
            continue
        body = getattr(node, "body", None)
        if not body:
            continue
        first = body[0]
        if (
            isinstance(first, ast.Expr)
            and isinstance(first.value, ast.Constant)
            and isinstance(first.value.value, str)
        ):
            node.body = body[1:] or [ast.Pass()]
    return tree


def _ast_fingerprint(source: str) -> str | None:
    """A structural fingerprint of ``source``, or ``None`` if it does not parse."""
    try:
        tree = ast.parse(textwrap.dedent(source))
    except (SyntaxError, ValueError):
        return None
    return ast.dump(_strip_docstrings(tree), include_attributes=False)


def is_cosmetic(original: str, mutated: str) -> bool:
    """True when ``mutated`` changes no executable behaviour of ``original``."""
    if original.strip() == mutated.strip():
        return True
    left = _ast_fingerprint(original)
    right = _ast_fingerprint(mutated)
    if left is None or right is None:
        return False
    return left == right


def parses(source: str) -> bool:
    try:
        ast.parse(source)
    except (SyntaxError, ValueError):
        return False
    return True


def make_diff(mutation: Mutation, context: int = 3) -> str:
    """Unified diff from the original file to the mutated one."""
    diff = difflib.unified_diff(
        mutation.original_source.splitlines(keepends=True),
        mutation.mutated_source.splitlines(keepends=True),
        fromfile=f"a/{mutation.path}",
        tofile=f"b/{mutation.path}",
        n=context,
    )
    return "".join(diff)


# ---------------------------------------------------------------------------
# Span splicing
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SpanText:
    """The exact text of a span plus what is needed to put a replacement back."""

    file_source: str
    lines: list[str]
    start_index: int
    end_index: int
    indent: str

    @property
    def text(self) -> str:
        return "\n".join(self.lines[self.start_index : self.end_index])

    def splice(self, replacement: str) -> str | None:
        """Rebuild the file with ``replacement`` in place of the span.

        Returns ``None`` when no indentation of the replacement yields a file
        that parses.
        """
        cleaned = replacement.replace("\r\n", "\n").rstrip()
        if not cleaned.strip():
            return None
        candidates = [cleaned]
        dedented = textwrap.dedent(cleaned)
        if self.indent:
            candidates.append(textwrap.indent(dedented, self.indent))
        for candidate in candidates:
            head = self.lines[: self.start_index]
            tail = self.lines[self.end_index :]
            rebuilt = "\n".join(head + candidate.split("\n") + tail)
            if self.file_source.endswith("\n") and not rebuilt.endswith("\n"):
                rebuilt += "\n"
            if parses(rebuilt):
                return rebuilt
        return None


def read_span(file_source: str, span: SourceSpan) -> SpanText | None:
    """Slice ``span`` out of ``file_source``. ``None`` when the range is invalid."""
    lines = file_source.replace("\r\n", "\n").split("\n")
    start = span.start_line - 1
    end = span.end_line
    if start < 0 or end > len(lines) or start >= end:
        return None
    first = lines[start]
    indent = first[: len(first) - len(first.lstrip())]
    return SpanText(
        file_source=file_source,
        lines=lines,
        start_index=start,
        end_index=end,
        indent=indent,
    )


# ---------------------------------------------------------------------------
# LLM mutations
# ---------------------------------------------------------------------------

_MUTATOR_SYSTEM = """\
You write deliberately incorrect code, for the purpose of testing whether a test \
suite would catch the mistake.

You are given one requirement and the exact source of the code that implements \
it. Produce edits of that code that each VIOLATE the requirement.

Every edit must be:
- Minimal. Change as little as possible -- ideally one operator, one boundary, \
one branch, one default. Do not rewrite the function.
- Plausible. It must look like something a competent engineer could write by \
accident during a refactor, not like sabotage. No raise, no exit, no obviously \
broken code, no comments announcing the bug.
- Behaviour-changing. Renaming variables, editing comments or docstrings, and \
reformatting are worthless -- they change nothing that a test can observe.
- Syntactically valid Python that keeps the same public signature, the same \
function and class names, and the same indentation as the code you were shown.
- Distinct from the other edits you return. Each one should break the \
requirement in a different way (a boundary, a branch, an inverted condition, a \
dropped guard, a wrong default).

Return the COMPLETE replacement text for the code you were shown -- the whole \
span, not a diff and not a fragment -- with the same leading indentation it \
already has. Do not include decorator lines; they are outside the span.

If the code shown genuinely cannot be made to violate the requirement, return \
an empty list rather than inventing an unrelated change."""

_MUTATOR_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "mutations": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "replacement": {"type": "string"},
                    "explanation": {"type": "string"},
                    "violation": {"type": "string"},
                },
                "required": ["replacement", "explanation", "violation"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["mutations"],
    "additionalProperties": False,
}


def llm_mutations(
    llm: LLMClient,
    requirement: Requirement,
    span: SourceSpan,
    file_source: str,
    count: int,
) -> list[Mutation]:
    """Ask the model for up to ``count`` requirement-violating edits of ``span``.

    Raises :class:`LLMError` if the call fails; callers decide whether to fall
    back to AST operators.
    """
    piece = read_span(file_source, span)
    if piece is None:
        _log.warning(
            "span is outside the file; skipping",
            extra={"context": {"requirement": requirement.id, "span": span.location}},
        )
        return []

    span_text = piece.text
    if len(span_text) > MAX_SPAN_CHARS:
        _log.debug(
            "span is large; the model will see it in full but the prompt is big",
            extra={"context": {"span": span.location, "chars": len(span_text)}},
        )

    prompt = (
        f"Requirement {requirement.id}:\n{requirement.text}\n\n"
        f"Implemented at {span.location}"
        + (f" in {span.symbol}" if span.symbol else "")
        + ":\n\n```python\n"
        + span_text
        + "\n```\n\n"
        f"Produce {count} distinct minimal edits that each violate requirement "
        f"{requirement.id}."
    )

    payload = llm.structured(
        system=_MUTATOR_SYSTEM,
        prompt=prompt,
        schema=_MUTATOR_SCHEMA,
        max_tokens=8000,
        effort="medium",
        cache_scope="mutate",
    )

    mutations: list[Mutation] = []
    seen: set[str] = set()
    for item in payload.get("mutations", []):
        if not isinstance(item, dict):
            continue
        replacement = item.get("replacement")
        if not isinstance(replacement, str) or not replacement.strip():
            continue
        if is_cosmetic(span_text, replacement):
            _log.debug(
                "rejected cosmetic mutation",
                extra={"context": {"requirement": requirement.id, "span": span.location}},
            )
            continue
        mutated_file = piece.splice(replacement)
        if mutated_file is None:
            _log.debug(
                "rejected mutation that does not parse",
                extra={"context": {"requirement": requirement.id, "span": span.location}},
            )
            continue
        if mutated_file == file_source:
            continue
        if mutated_file in seen:
            continue
        seen.add(mutated_file)
        explanation = str(item.get("explanation", "")).strip()
        violation = str(item.get("violation", "")).strip()
        detail = f"{explanation} ({violation})" if violation else explanation
        mutations.append(
            Mutation(
                id=Mutation.make_id(requirement.id, span.path, mutated_file),
                requirement_id=requirement.id,
                path=span.path,
                original_source=file_source,
                mutated_source=mutated_file,
                explanation=detail or "requirement-directed mutation",
                origin=MutationSource.LLM,
                span=span,
            )
        )
        if len(mutations) >= count:
            break

    return mutations


# ---------------------------------------------------------------------------
# AST fallback operators
# ---------------------------------------------------------------------------

_COMPARE_FLIPS: dict[type[ast.cmpop], type[ast.cmpop]] = {
    ast.Lt: ast.LtE,
    ast.LtE: ast.Lt,
    ast.Gt: ast.GtE,
    ast.GtE: ast.Gt,
    ast.Eq: ast.NotEq,
    ast.NotEq: ast.Eq,
    ast.Is: ast.IsNot,
    ast.IsNot: ast.Is,
    ast.In: ast.NotIn,
    ast.NotIn: ast.In,
}


Matcher = Callable[[ast.AST], bool]
Rewriter = Callable[[ast.AST], ast.AST]


class _PreorderCounter(ast.NodeVisitor):
    """Count nodes matching ``matcher`` in depth-first pre-order."""

    def __init__(self, matcher: Matcher) -> None:
        self.matcher = matcher
        self.count = 0

    def visit(self, node: ast.AST) -> None:
        if self.matcher(node):
            self.count += 1
        self.generic_visit(node)


class _NthTransformer(ast.NodeTransformer):
    """Rewrite exactly the ``target``-th matching node, in the same pre-order.

    One mutant changes one thing. Rewriting every match at once would produce a
    mutant so broken that killing it proves nothing about any single behaviour.
    """

    def __init__(self, matcher: Matcher, rewrite: Rewriter, target: int) -> None:
        self.matcher = matcher
        self.rewrite = rewrite
        self.target = target
        self.seen = 0
        self.applied = False

    def visit(self, node: ast.AST) -> ast.AST:
        if self.matcher(node):
            index = self.seen
            self.seen += 1
            if index == self.target:
                self.applied = True
                return ast.fix_missing_locations(self.rewrite(node))
        return self.generic_visit(node)


@dataclass(frozen=True)
class _Operator:
    """A mechanical mutation operator: what it matches and how it rewrites."""

    name: str
    matcher: Matcher
    rewrite: Rewriter
    describe: Callable[[ast.AST], str]


def _is_flippable_compare(node: ast.AST) -> bool:
    return isinstance(node, ast.Compare) and any(
        type(op) in _COMPARE_FLIPS for op in node.ops
    )


def _flip_compare(node: ast.AST) -> ast.AST:
    assert isinstance(node, ast.Compare)
    clone = copy.deepcopy(node)
    for index, op in enumerate(clone.ops):
        flip = _COMPARE_FLIPS.get(type(op))
        if flip is not None:
            clone.ops[index] = flip()
            break
    return clone


def _describe_compare(node: ast.AST) -> str:
    assert isinstance(node, ast.Compare)
    for op in node.ops:
        flip = _COMPARE_FLIPS.get(type(op))
        if flip is not None:
            return f"loosen the {type(op).__name__} comparison to {flip.__name__}"
    return "flip a comparison"


def _swap_boolop(node: ast.AST) -> ast.AST:
    assert isinstance(node, ast.BoolOp)
    clone = copy.deepcopy(node)
    clone.op = ast.Or() if isinstance(node.op, ast.And) else ast.And()
    return clone


def _is_not(node: ast.AST) -> bool:
    return isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Not)


def _drop_not(node: ast.AST) -> ast.AST:
    assert isinstance(node, ast.UnaryOp)
    return copy.deepcopy(node.operand)


def _is_mutable_constant(node: ast.AST) -> bool:
    return isinstance(node, ast.Constant) and isinstance(node.value, (bool, int, float))


def _shift_constant(node: ast.AST) -> ast.AST:
    assert isinstance(node, ast.Constant)
    value = node.value
    if isinstance(value, bool):
        return ast.Constant(value=not value)
    if isinstance(value, int):
        return ast.Constant(value=value + 1)
    if isinstance(value, float):
        return ast.Constant(value=value + 1.0)
    raise TypeError(f"not a shiftable constant: {value!r}")  # pragma: no cover


def _describe_constant(node: ast.AST) -> str:
    assert isinstance(node, ast.Constant)
    new = _shift_constant(node)
    assert isinstance(new, ast.Constant)
    return f"change the literal {node.value!r} to {new.value!r}"


def _is_guard_if(node: ast.AST) -> bool:
    """An ``if`` that guards a raise/return -- dropping it removes a check."""
    return isinstance(node, ast.If) and any(
        isinstance(stmt, (ast.Raise, ast.Return)) for stmt in node.body
    )


def _neuter_guard(node: ast.AST) -> ast.AST:
    assert isinstance(node, ast.If)
    clone = copy.deepcopy(node)
    clone.test = ast.Constant(value=False)
    return clone


OPERATORS: tuple[_Operator, ...] = (
    _Operator(
        name="comparison-boundary",
        matcher=_is_flippable_compare,
        rewrite=_flip_compare,
        describe=_describe_compare,
    ),
    _Operator(
        name="guard-removed",
        matcher=_is_guard_if,
        rewrite=_neuter_guard,
        describe=lambda node: "make a guarding `if` unreachable",
    ),
    _Operator(
        name="boolean-operator",
        matcher=lambda node: isinstance(node, ast.BoolOp),
        rewrite=_swap_boolop,
        describe=lambda node: "swap `and` for `or` (or the reverse) in a condition",
    ),
    _Operator(
        name="negation-removed",
        matcher=_is_not,
        rewrite=_drop_not,
        describe=lambda node: "drop a `not` from a condition",
    ),
    _Operator(
        name="constant-boundary",
        matcher=_is_mutable_constant,
        rewrite=_shift_constant,
        describe=_describe_constant,
    ),
)


def _nth_match(tree: ast.AST, matcher: Matcher, index: int) -> ast.AST | None:
    """The ``index``-th matching node, using the transformer's traversal order."""
    found: list[ast.AST] = []

    class _Collector(ast.NodeVisitor):
        def visit(self, node: ast.AST) -> None:
            if matcher(node):
                found.append(node)
            self.generic_visit(node)

    _Collector().visit(tree)
    return found[index] if index < len(found) else None


def ast_mutations(
    requirement: Requirement,
    span: SourceSpan,
    file_source: str,
    count: int,
) -> list[Mutation]:
    """Mechanical, requirement-agnostic mutations inside ``span``.

    Used when the LLM is unavailable. The mutations are real and executable, but
    they were not chosen to violate the requirement -- the report says so.
    """
    piece = read_span(file_source, span)
    if piece is None:
        return []
    span_text = piece.text
    try:
        span_tree = ast.parse(textwrap.dedent(span_text))
    except (SyntaxError, ValueError):
        return []

    mutations: list[Mutation] = []
    seen: set[str] = set()

    # Round-robin across operators so a function with fifty integer literals
    # does not spend the whole budget on constant tweaks.
    plans: list[tuple[_Operator, int]] = []
    for operator in OPERATORS:
        counter = _PreorderCounter(operator.matcher)
        counter.visit(span_tree)
        plans.extend((operator, index) for index in range(counter.count))
    plans.sort(key=lambda plan: (plan[1], OPERATORS.index(plan[0])))

    for operator, index in plans:
        if len(mutations) >= count:
            break
        original_node = _nth_match(span_tree, operator.matcher, index)
        if original_node is None:
            continue
        working = copy.deepcopy(span_tree)
        transformer = _NthTransformer(operator.matcher, operator.rewrite, index)
        try:
            mutated_tree = transformer.visit(working)
            if not transformer.applied:
                continue
            mutated_span = ast.unparse(ast.fix_missing_locations(mutated_tree))
        except (AttributeError, IndexError, ValueError, TypeError, RecursionError):
            continue
        if is_cosmetic(span_text, mutated_span):
            continue
        indented = textwrap.indent(mutated_span, piece.indent) if piece.indent else mutated_span
        mutated_file = piece.splice(indented)
        if mutated_file is None or mutated_file == file_source or mutated_file in seen:
            continue
        seen.add(mutated_file)
        mutations.append(
            Mutation(
                id=Mutation.make_id(requirement.id, span.path, mutated_file),
                requirement_id=requirement.id,
                path=span.path,
                original_source=file_source,
                mutated_source=mutated_file,
                explanation=f"AST operator: {operator.describe(original_node)}",
                origin=MutationSource.AST,
                operator=operator.name,
                span=span,
            )
        )
    return mutations


def generate_mutations(
    requirement: Requirement,
    spans: list[SourceSpan],
    read_file: Any,
    count: int,
    llm: LLMClient | None = None,
) -> tuple[list[Mutation], str]:
    """Produce up to ``count`` mutations for ``requirement`` across ``spans``.

    ``read_file`` maps a repo-relative path to its text. Returns the mutations
    and a note explaining any degradation (empty when the LLM was used).
    """
    mutations: list[Mutation] = []
    note = ""
    seen: set[str] = set()

    for span in spans:
        if len(mutations) >= count:
            break
        try:
            file_source = read_file(span.path)
        except OSError as exc:
            _log.warning(
                "could not read source file",
                extra={"context": {"path": span.path, "error": str(exc)}},
            )
            continue

        remaining = count - len(mutations)
        produced: list[Mutation] = []
        if llm is not None and not llm.degraded:
            try:
                produced = llm_mutations(llm, requirement, span, file_source, remaining)
            except LLMError as exc:
                note = f"AST operators used: {exc.message}"
                _log.warning(
                    "mutation generation fell back to AST operators",
                    extra={"context": {"requirement": requirement.id, "error": str(exc)}},
                )
        elif llm is not None and llm.degraded:
            note = f"AST operators used: {llm.degraded_reason}"

        if not produced:
            if llm is None and not note:
                note = "AST operators used: running without an LLM"
            produced = ast_mutations(requirement, span, file_source, remaining)

        for mutation in produced:
            if mutation.mutated_source in seen:
                continue
            seen.add(mutation.mutated_source)
            mutations.append(mutation)

    return mutations[:count], note
