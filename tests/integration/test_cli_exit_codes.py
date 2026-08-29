"""reqguard.cli.main.main() -- the actual console entry point's exit code.

CliRunner.invoke() (used in test_apply.py etc.) has its own correct exit-code
capture that is entirely independent of main(). It cannot catch a bug in
main() itself, because it never calls main() -- it calls the Typer app
directly. That gap is exactly how the bug this file guards against went
unnoticed: `standalone_mode=False` converts an explicitly `raise
typer.Exit(N)` inside a command into main()'s own IGNORED return value, not
a propagated exception, so every failing command reported exit code 0 to any
real calling shell or CI system -- including `reqguard doctor`, this
project's own go/no-go gate (Doc 3 section 20). Only invoking main() itself,
the way the installed console script and `python -m` actually do, exercises
this path.

Gate: pytest tests/integration/test_cli_exit_codes.py -q
"""

from __future__ import annotations

from reqguard.cli.main import main


def test_success_exits_zero():
    assert main(["--help"]) == 0


def test_an_explicit_typer_exit_propagates_its_real_code(tmp_path):
    # evaluate --results <missing dir> hits `raise typer.Exit(2)`.
    assert main(["evaluate", "--results", str(tmp_path / "does-not-exist")]) == 2


def test_doctor_not_ready_exits_nonzero(monkeypatch, tmp_path):
    """The project's own go/no-go gate must fail loudly to a real caller.

    Forces run_doctor's underlying checklist to report failure without
    needing a live model call or a broken fixture -- monkeypatches the
    checklist function directly to isolate exit-code propagation from
    doctor's own pass/fail logic (that logic has its own coverage).

    `orchestrator` is imported with `from reqguard.agent import orchestrator`
    inside run_doctor() itself, binding the module object -- patching the
    module's own `analyze` attribute (not a nonexistent `_runner.orchestrator`)
    is what actually reaches that call site.
    """
    from reqguard.agent import orchestrator
    from reqguard.cli import _runner

    monkeypatch.setattr(_runner, "_doctor_checklist", lambda verdicts: False)
    monkeypatch.setattr(orchestrator, "analyze", lambda *a, **k: [])

    rc = main([
        "doctor", "--case", str(tmp_path),
        "--replay", str(tmp_path),  # any path; analyze() is monkeypatched above
    ])
    assert rc == 1


def test_doctor_ready_exits_zero(monkeypatch, tmp_path):
    from reqguard.agent import orchestrator
    from reqguard.cli import _runner

    monkeypatch.setattr(_runner, "_doctor_checklist", lambda verdicts: True)
    monkeypatch.setattr(orchestrator, "analyze", lambda *a, **k: [])

    rc = main(["doctor", "--case", str(tmp_path), "--replay", str(tmp_path)])
    assert rc == 0


def test_unrecognized_option_exits_nonzero():
    assert main(["analyze", "--this-flag-does-not-exist"]) != 0
