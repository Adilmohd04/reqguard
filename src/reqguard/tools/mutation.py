"""Mutation engine — apply a MutationSpec and the six gates. Owned by Track A.

Reference: docs/03-implementation-manual.md sections 6.3, 6.4, 6.5.

Layered design (section 6.1): the ``mutation_type`` comes from the catalogue and
drives metrics + a cheap plausibility check; the anchored ``original_snippet →
mutated_snippet`` pair makes *applying* the change mechanical and reproducible.

**The rule the whole result rests on (section 6.5): a mutation may never modify a
test file.** Gate 3 enforces it here, a standing unit test enforces it again, and
the evaluator re-checks the recorded diff at scoring time.

stdlib + the frozen contracts only — no Anthropic import.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Callable

from reqguard.errors import MutationRejected, PathEscape
from reqguard.models import MutationSpec, MutationType
from reqguard.tools.diff import diff_line_count, unified_diff
from reqguard.verification.safety import safe_resolve

__all__ = ["is_test_path", "apply", "apply_with_gates", "plausibility_note", "reanchor", "GATES"]

DEFAULT_MAX_DIFF_LINES = 15


def is_test_path(path: str) -> bool:
    """True when ``path`` names a test file — anything the mutation engine must
    never touch (section 6.4)."""
    p = PurePosixPath(path.replace("\\", "/"))
    return (
        p.name.startswith("test_")
        or p.name.endswith("_test.py")
        or p.name == "conftest.py"
        or "tests" in p.parts
    )


def _parses(source: str) -> bool:
    try:
        ast.parse(source)
        return True
    except SyntaxError:
        return False


def _applied(spec: MutationSpec, source: str) -> str:
    return source.replace(spec.original_snippet, spec.mutated_snippet)


def _norm_block(text: str) -> str:
    """Whitespace-normalized form of a code block for tolerant matching:
    strip each line's leading/trailing whitespace and drop blank lines. Two
    blocks that differ only in indentation (the common model mistake) or in
    trailing spaces normalize to the same string."""
    lines = [ln.strip() for ln in text.splitlines()]
    return "\n".join(ln for ln in lines if ln)


def reanchor(spec: MutationSpec, source: str) -> MutationSpec | None:
    """When ``spec.original_snippet`` does not byte-match ``source`` but differs
    only in indentation/blank-lines, recover the *exact* source text of the
    matching block and re-indent the mutated snippet to match, returning a new
    spec whose ``original_snippet`` is copy-exact.

    Safety is preserved by construction:
    - Returns ``None`` unless the normalized block matches **exactly once** in
      the normalized source (same uniqueness bar the anchor_unique gate has),
      so an ambiguous or absent anchor is never silently applied.
    - The recovered ``original_snippet`` is a verbatim substring of the real
      source, so all downstream gates (parses, small_diff, not_a_noop) run on
      the true text, not the model's approximation.
    """
    want = _norm_block(spec.original_snippet)
    if not want:
        return None

    src_lines = source.splitlines(keepends=True)
    want_line_count = len([ln for ln in spec.original_snippet.splitlines() if ln.strip()])
    if want_line_count == 0:
        return None

    # Slide a window over non-normalized source lines, comparing the
    # normalized form of each contiguous run whose non-blank line count equals
    # the target's. Collect every window that matches; require exactly one.
    matches: list[str] = []
    n = len(src_lines)
    for i in range(n):
        # Grow a window from i until it contains want_line_count non-blank lines.
        nonblank = 0
        j = i
        while j < n and nonblank < want_line_count:
            if src_lines[j].strip():
                nonblank += 1
            j += 1
        if nonblank < want_line_count:
            break  # not enough lines left
        window = "".join(src_lines[i:j])
        if _norm_block(window) == want:
            # Trim leading/trailing blank lines from the captured window so the
            # recovered original is tight, but keep interior text verbatim.
            matches.append(window)

    # Deduplicate identical captured windows (overlapping starts can re-capture
    # the same block); uniqueness is about distinct source locations.
    unique = {m for m in matches}
    if len(unique) != 1:
        return None
    recovered_original = matches[0]
    # Strip a single trailing newline the window may have grabbed so the anchor
    # is the block itself; replace() semantics are unaffected either way.
    recovered_original = recovered_original.rstrip("\n")
    if recovered_original not in source or source.count(recovered_original) != 1:
        return None

    # Re-indent the mutated snippet to the recovered block's base indentation,
    # so the replacement lands with correct indentation. Compute the indent
    # delta from the first non-blank line of each.
    def _first_indent(text: str) -> str:
        for ln in text.splitlines():
            if ln.strip():
                return ln[: len(ln) - len(ln.lstrip())]
        return ""

    orig_indent = _first_indent(recovered_original)
    model_indent = _first_indent(spec.original_snippet)
    mutated = spec.mutated_snippet
    if orig_indent != model_indent:
        # Reformat mutated: strip the model's assumed indent, apply the real one.
        new_lines = []
        for ln in mutated.splitlines():
            stripped = ln[len(model_indent):] if ln.startswith(model_indent) else ln.lstrip()
            new_lines.append(orig_indent + stripped if ln.strip() else ln)
        mutated = "\n".join(new_lines)

    return spec.model_copy(update={"original_snippet": recovered_original,
                                   "mutated_snippet": mutated})


def _contained(sandbox, target_file: str) -> bool:
    try:
        safe_resolve(sandbox, target_file)
        return True
    except PathEscape:
        return False


@dataclass(frozen=True)
class _Gate:
    name: str
    check: Callable[[MutationSpec, str, "_GateCtx"], bool]


@dataclass(frozen=True)
class _GateCtx:
    sandbox: object
    max_diff_lines: int


GATES: list[_Gate] = [
    _Gate("anchor_unique", lambda s, src, c: src.count(s.original_snippet) == 1),
    _Gate("parses", lambda s, src, c: _parses(_applied(s, src))),
    _Gate("not_a_test_file", lambda s, src, c: not is_test_path(s.target_file)),
    _Gate("in_sandbox", lambda s, src, c: _contained(c.sandbox, s.target_file)),
    _Gate(
        "small_diff",
        lambda s, src, c: diff_line_count(src, _applied(s, src)) <= c.max_diff_lines,
    ),
    _Gate("not_a_noop", lambda s, src, c: s.original_snippet != s.mutated_snippet),
]


# ── per-type plausibility check (section 6.2) ─────────────────────────────────

_COMPARISON_OPS = {"==", "!=", "<", "<=", ">", ">=", "is", "in"}


def plausibility_note(spec: MutationSpec) -> str | None:
    """A cheap, deterministic sanity check on the (original, mutated) pair.

    Returns ``None`` when the pair looks consistent with ``mutation_type``, or a
    short ``"type_mismatch: ..."`` note otherwise. A mismatch is **not fatal** —
    it is logged so a mislabelled mutation does not silently pollute the
    per-category metrics; the mutation still runs.
    """
    o, m = spec.original_snippet, spec.mutated_snippet
    t = spec.mutation_type

    if t is MutationType.SEMANTIC_CHANGE:
        if not spec.rationale.strip():
            return "type_mismatch: semantic_change requires a non-empty rationale"
        return None
    if t in (MutationType.REMOVE_GUARD, MutationType.REMOVE_VALIDATION):
        shrank = len(m) < len(o)
        lost_control = ("raise" in o and "raise" not in m) or (
            "return" in o and m.count("return") < o.count("return")
        )
        if shrank or lost_control:
            return None
        return f"type_mismatch: {t.value} but the mutant is not shorter and keeps its raise/return"
    if t is MutationType.BOOLEAN_INVERSION:
        if o.count(" not ") != m.count(" not ") or ("and" in o) != ("and" in m) or (
            "or" in o
        ) != ("or" in m):
            return None
        return "type_mismatch: boolean_inversion but no not/and/or flip is visible"
    if t in (MutationType.COMPARISON_CHANGE, MutationType.BOUNDARY_SHIFT):
        if any(op in o for op in _COMPARISON_OPS) and o != m:
            return None
        return f"type_mismatch: {t.value} but no comparison operator is present"
    if t is MutationType.ALLOW_TRANSITION:
        if len(m) <= len(o):
            return None
        return "type_mismatch: allow_transition but the guard grew rather than weakened"
    return None


# ── application ──────────────────────────────────────────────────────────────

def apply(spec: MutationSpec, sandbox) -> str:
    """Apply the anchored replacement in the sandbox and return a unified diff.

    Assumes the gates have already passed. Raises :class:`MutationRejected`
    (``anchor_not_found`` / ``anchor_ambiguous``) if the anchor is not unique —
    the gates catch this first, but apply is defensive.
    """
    target = safe_resolve(sandbox, spec.target_file)
    source = target.read_text(encoding="utf-8")

    n = source.count(spec.original_snippet)
    if n == 0:
        raise MutationRejected("anchor_not_found")
    if n > 1:
        raise MutationRejected("anchor_ambiguous", f"{n} matches")

    mutated = source.replace(spec.original_snippet, spec.mutated_snippet)
    target.write_text(mutated, encoding="utf-8")
    return unified_diff(source, mutated, spec.target_file)


def apply_with_gates(
    spec: MutationSpec,
    sandbox,
    *,
    max_diff_lines: int = DEFAULT_MAX_DIFF_LINES,
) -> str:
    """Run all six gates, then apply. Every gate must pass before the mutant is
    written. On the first failure, raise :class:`MutationRejected` naming the
    gate so the planner can be re-prompted with a specific reason (section 6.4).
    """
    target_file = spec.target_file

    # in_sandbox / anchor gates need the file; resolve defensively first.
    if is_test_path(target_file):
        raise MutationRejected("not_a_test_file", target_file)
    if not _contained(sandbox, target_file):
        raise MutationRejected("in_sandbox", target_file)

    source = safe_resolve(sandbox, target_file).read_text(encoding="utf-8")
    ctx = _GateCtx(sandbox=sandbox, max_diff_lines=max_diff_lines)

    # Whitespace-tolerant re-anchoring (recovery, not a relaxation of safety):
    # if the model's original_snippet does not byte-match the source uniquely
    # but differs only in indentation/blank lines, recover the exact source
    # block and re-indent the mutation. reanchor() itself enforces the same
    # single-unique-match bar as the anchor_unique gate, so an ambiguous or
    # absent anchor still falls through to a clean rejection below. Exact
    # matches are left untouched — this only runs when the exact anchor missed.
    if source.count(spec.original_snippet) != 1:
        recovered = reanchor(spec, source)
        if recovered is not None:
            spec = recovered

    for gate in GATES:
        try:
            ok = gate.check(spec, source, ctx)
        except Exception as exc:  # a gate that errors is a rejection, not a crash
            raise MutationRejected(gate.name, f"gate raised {exc!r}") from exc
        if not ok:
            detail = {
                "anchor_unique": f"{source.count(spec.original_snippet)} matches for original_snippet",
                "parses": "mutated source does not ast.parse",
                "small_diff": f"diff exceeds {max_diff_lines} lines",
                "not_a_noop": "original_snippet == mutated_snippet",
            }.get(gate.name, "")
            raise MutationRejected(gate.name, detail)

    return apply(spec, sandbox)
