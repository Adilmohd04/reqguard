# C6 independent review — reporting outputs

Repository: `/mnt/c/Users/Ashi/Desktop/Adil/ReqGuard`

Review exactly these uncommitted C6 paths:

- `/mnt/c/Users/Ashi/Desktop/Adil/ReqGuard/src/reqguard/reporting/formatter.py`
- `/mnt/c/Users/Ashi/Desktop/Adil/ReqGuard/src/reqguard/reporting/report.py`
- `/mnt/c/Users/Ashi/Desktop/Adil/ReqGuard/src/reqguard/reporting/templates/summary.html.j2`
- `/mnt/c/Users/Ashi/Desktop/Adil/ReqGuard/tests/unit/test_report.py`

Governing requirements:

- `/mnt/c/Users/Ashi/Desktop/Adil/ReqGuard/docs/03-implementation-manual.md`, sections 12 and 13.2
- `/mnt/c/Users/Ashi/Desktop/Adil/ReqGuard/crew/tasks.yaml`, task C6
- `/mnt/c/Users/Ashi/Desktop/Adil/ReqGuard/AGENTS.md`
- Frozen models/errors contracts; do not edit them

Check terminal formatting including an explicit Inconclusive count even when zero; findings.json, mutations.json, optional metrics.json, trajectory.jsonl preservation; exact per-requirement `mutations/<id>.diff` and unapplied `repairs/<id>.py` written only beneath the report directory; self-contained summary HTML with no external assets; safe handling of requirement IDs as output filenames; deterministic serialization; and absence of Anthropic/model-stage imports.

The supervisor will independently run the C6 gate. Do not trust green tests as a substitute for source review. Do not edit, stage, commit, or run branch-changing commands.

Return `APPROVED` or `NEEDS_CHANGES` with exact file/line findings and required tests.

---

## Verdict: NEEDS_CHANGES

Reviewer: Kiro (independent review, source-only). Gate not re-run here (pytest
not installed in this session); supervisor reports 4 passed, 1 benign
PytestCollectionWarning — corroborated below.

### What is correct

- **No forbidden imports.** `grep -R "import anthropic|from anthropic|reqguard.agent"`
  over `src/reqguard/reporting/` returns zero matches. Import rules for Track C hold.
- **Files owned by C only.** All four reviewed paths are in `owned_by_C`
  (`crew/ownership.yaml`). No frozen contract (`models.py`/`errors.py`) touched.
- **Terminal Inconclusive-at-zero.** `formatter.py:71` emits
  `f"Inconclusive:          {s.inconclusive}"` unconditionally; test at
  `tests/unit/test_report.py:71-74` asserts `"Inconclusive:          0"`. Matches
  Doc 3 §13.2. ✓
- **Report file set.** `report.py` writes `findings.json` (one `Verdict` each),
  `mutations.json`, optional `metrics.json` (guarded by `if metrics is not None`),
  and copies `trajectory.jsonl` only when the source is a real file and not the
  same path (`report.py` — `shutil.copyfile` guarded by `.is_file()` and a
  `resolve()` inequality check). Matches Doc 3 §12 / blueprint §30. ✓
- **Self-contained HTML.** `summary.html.j2` has inline `<style>`, no `http`/`cdn`
  references; `autoescape=select_autoescape(["html","j2"])` escapes verdict/diff
  content. Test at `test_report.py` asserts absence of `http://`, `https://`,
  `cdn`. ✓
- **Repairs stay in the report dir.** `report.py` writes `repairs/<rid>.py` under
  `out`, and `test_write_report_does_not_write_into_any_source_tree` confirms the
  user repo is untouched. ✓ (subject to the finding below)
- **Deterministic serialization.** All `json.dumps(..., indent=2)`; verdict order
  preserved by list iteration. ✓
- **Benign warning explained.** `TestOutcome` (a Pydantic model, not a test) is
  imported into the test module; pytest's collector matches the `Test*` prefix and
  emits a single `PytestCollectionWarning`. Harmless — consistent with the
  supervisor's report.

### Blocking finding (must fix before done)

**F1 — Requirement IDs are used as filesystem paths without sanitization
(path-traversal / write-escape).**

`src/reqguard/reporting/report.py`:
```
rid = v.requirement.id
...
(diff_dir / f"{rid}.diff").write_text(...)      # mutations/<rid>.diff
(repair_dir / f"{rid}.py").write_text(...)      # repairs/<rid>.py
```
`Requirement.id` is an unconstrained `str` (`models.py:84` — `id: str`, no
pattern/validator). A requirement id such as `../../evil`, `/etc/foo`, or one
containing a path separator makes `out / "repairs" / f"{rid}.py"` resolve
**outside** the report directory — the exact write-escape the report layer is
supposed to prevent, and the brief's explicit check ("safe handling of
requirement IDs as output filenames").

The whole product guarantees the user repo is read-only and repairs land only in
the report dir; an unsanitized id defeats that guarantee at the last step.
AGENTS.md is explicit: over-rejecting is recoverable and visible, under-rejecting
corrupts silently — so this must be closed, not documented.

Note this is upstream of the contract: since `Requirement.id` cannot be
constrained here (frozen model), the reporting layer must defend itself.

**Required fix (Track C, no contract change needed):** before using `rid` as a
path component, reject or sanitize it. Minimum: assert the id is a single safe
path segment — e.g. reject if `rid` contains `/`, `\\`, `os.sep`, `..`, or is
absolute; or slugify to `[A-Za-z0-9._-]` and verify
`(base / name).resolve()` is still within `out.resolve()` (the same containment
idea as `safe_resolve`). Surface a clear error on violation rather than writing.

**Required tests (add to `tests/unit/test_report.py`):**
1. `write_report` with a verdict whose `requirement.id` is `"../escape"` (and one
   containing a path separator) does **not** create any file outside the report
   dir, and either raises or writes a sanitized name inside `out`.
2. Assert the resolved diff/repair path is a child of `out.resolve()`.

### Non-blocking observations (not required for approval)

- `REPORT_FILES` omits `metrics.json` and `trajectory.jsonl`; those are
  conditional, so the constant is defensibly the always-written set. Consider a
  docstring note to avoid a future reader assuming it is the full manifest.
- `summary.txt` is written but not listed in Doc 3 §12; harmless addition, mirrors
  the terminal block. Fine to keep.

Re-review only F1 and its tests; everything else is approvable as-is.
