# C6 reporting recovery assignment

Adopt the pre-existing uncommitted C6 reporting WIP in the shared checkout and complete it against `crew/tasks.yaml` C6 and Doc 3 §12.

Edit only:
- `src/reqguard/reporting/report.py`
- `src/reqguard/reporting/formatter.py`
- `src/reqguard/reporting/templates/**`
- `tests/unit/test_report.py`

Required: summary.html self-contained with inline CSS/no external assets; findings.json, mutations.json, metrics.json, trajectory.jsonl; repairs only under report output; terminal output includes Inconclusive even at zero. No Anthropic/model imports.

C5 is concurrently under revision. Do not edit C1/C5 files, do not run role-C stage, and do not commit yet. Run the C6 gate, fix only C6 files, and report exact changed paths plus gate output. The supervisor will authorize staging after C5 is accepted.
