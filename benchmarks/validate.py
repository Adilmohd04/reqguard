#!/usr/bin/env python3
"""ReqGuard benchmark corpus validator.

Runs in CI with NO API calls.
Verifies:
1. Every case has requirements.md, pyproject.toml, ground_truth.json, and runnable tests.
2. Every ground_truth.json parses and adheres to the GroundTruth schema.
3. Every requirement has an explicit `protected` label.
4. Every `protected: false` requirement has a non-empty rationale.
5. Clean cases have clean=True, all requirements protected=True, and no deliberate defect.
6. Case IDs are unique and exactly match their directory names.
7. Total case count is 18 (15 defect + 3 clean) matching the composition specification.
8. Every case test suite passes (green on unmutated code) with pytest exit code 0.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from reqguard.evaluation.ground_truth import GroundTruth, load_ground_truth
from reqguard.models import Category

BENCHMARKS_DIR = ROOT / "benchmarks"

EXPECTED_COMPOSITION = {
    Category.AUTHORIZATION: {"defect": 3, "clean": 1},
    Category.STATE_TRANSITION: {"defect": 3, "clean": 0},
    Category.BUSINESS_RULE: {"defect": 3, "clean": 1},
    Category.VALIDATION: {"defect": 2, "clean": 0},
    Category.API_ERROR: {"defect": 2, "clean": 1},
    Category.SEMANTIC: {"defect": 2, "clean": 0},
}


def validate_all() -> int:
    case_dirs = sorted([d for d in BENCHMARKS_DIR.iterdir() if d.is_dir() and d.name.startswith("case")])
    if not case_dirs:
        print(f"ERROR: No benchmark cases found in {BENCHMARKS_DIR}")
        return 1

    print(f"Validating {len(case_dirs)} benchmark cases in {BENCHMARKS_DIR}...\n")
    
    counts: dict[Category, dict[str, int]] = {
        cat: {"defect": 0, "clean": 0} for cat in Category
    }
    
    seen_case_ids: set[str] = set()
    errors: list[str] = []

    for case_dir in case_dirs:
        case_name = case_dir.name
        gt_path = case_dir / "ground_truth.json"
        req_path = case_dir / "requirements.md"
        pyproject_path = case_dir / "pyproject.toml"

        # 1. Structural checks
        if not req_path.exists():
            errors.append(f"[{case_name}] missing requirements.md")
        if not pyproject_path.exists():
            errors.append(f"[{case_name}] missing pyproject.toml")
        if not gt_path.exists():
            errors.append(f"[{case_name}] missing ground_truth.json")
            continue

        # 2. Ground truth schema validation
        try:
            gt = load_ground_truth(gt_path)
        except Exception as e:
            errors.append(f"[{case_name}] ground_truth.json schema error: {e}")
            continue

        # 3. ID and directory match
        if gt.case_id != case_name:
            errors.append(f"[{case_name}] case_id '{gt.case_id}' does not match directory name '{case_name}'")
        if gt.case_id in seen_case_ids:
            errors.append(f"[{case_name}] duplicate case_id '{gt.case_id}'")
        seen_case_ids.add(gt.case_id)

        # 4. Clean vs defect tracking
        if gt.clean:
            counts[gt.category]["clean"] += 1
            for req in gt.requirements:
                if not req.protected:
                    errors.append(f"[{case_name}] clean case has unprotected requirement {req.id}")
        else:
            counts[gt.category]["defect"] += 1
            has_defect = any(not req.protected for req in gt.requirements)
            if not has_defect:
                errors.append(f"[{case_name}] defect case has no unprotected requirement")

        for req in gt.requirements:
            if not req.protected and not (req.rationale and req.rationale.strip()):
                errors.append(f"[{case_name}] requirement {req.id} is unprotected but missing rationale")

        # 5. Execute unmutated test suite — must be green (exit code 0)
        env = dict(os.environ)
        env["PYTHONPATH"] = str(case_dir / "src")
        res = subprocess.run(
            [sys.executable, "-m", "pytest", "-q"],
            cwd=case_dir,
            env=env,
            capture_output=True,
            text=True,
        )
        if res.returncode != 0:
            errors.append(
                f"[{case_name}] pytest failed on unmutated code (exit {res.returncode}):\n{res.stdout}\n{res.stderr}"
            )
        else:
            passed_summary = res.stdout.strip().splitlines()[-1] if res.stdout.strip() else "passed"
            print(f"  ✓ {case_name:<40} [{gt.category.value}] {'CLEAN' if gt.clean else 'DEFECT':<6} ({passed_summary})")

    print("\n" + "=" * 60)
    print("Composition check:")
    composition_ok = True
    total_defect = 0
    total_clean = 0
    for cat, expected in EXPECTED_COMPOSITION.items():
        actual = counts[cat]
        total_defect += actual["defect"]
        total_clean += actual["clean"]
        status = "✓" if actual == expected else "✗"
        if actual != expected:
            composition_ok = False
        print(f"  {status} {cat.value:<20} Defect: {actual['defect']}/{expected['defect']}  Clean: {actual['clean']}/{expected['clean']}")

    print(f"\nTotal cases: {len(seen_case_ids)} (Defect: {total_defect}/15, Clean: {total_clean}/3)")

    if len(seen_case_ids) != 18 or not composition_ok:
        errors.append(f"Composition mismatch: expected 18 cases (15 defect, 3 clean), got {len(seen_case_ids)} (Defect: {total_defect}, Clean: {total_clean})")

    if errors:
        print(f"\n\033[31mVALIDATION FAILED with {len(errors)} error(s):\033[0m")
        for err in errors:
            print("  -", err)
        return 1

    print("\n\033[32mALL BENCHMARK CASES VALIDATED SUCCESSFULLY.\033[0m\n")
    return 0


if __name__ == "__main__":
    sys.exit(validate_all())
