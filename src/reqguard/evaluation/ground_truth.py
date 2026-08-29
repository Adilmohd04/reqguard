"""Ground truth schemas and loader for ReqGuard benchmarks.

Owned by Track E (Benchmark Corpus).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, model_validator

from reqguard.models import Category, MutationType, Severity


class GroundTruthRequirement(BaseModel):
    id: str
    statement: str
    severity: Severity = Severity.HIGH
    protected: bool
    expected_mutation: MutationType | str | None = None
    expected: Literal["survive", "killed"] | str | None = None
    rationale: str | None = None

    @model_validator(mode="after")
    def validate_rationale(self) -> GroundTruthRequirement:
        if not self.protected and not (self.rationale and self.rationale.strip()):
            raise ValueError(
                f"Requirement {self.id} is marked protected=False but has no rationale explaining the gap."
            )
        return self


class GroundTruth(BaseModel):
    case_id: str
    category: Category
    difficulty: Literal["easy", "medium", "hard"] | str
    clean: bool = False
    requirements: list[GroundTruthRequirement] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_clean_and_requirements(self) -> GroundTruth:
        if not self.requirements:
            raise ValueError(f"Case {self.case_id} has no requirements.")
        if self.clean:
            for req in self.requirements:
                if not req.protected:
                    raise ValueError(
                        f"Case {self.case_id} is marked clean=True but requirement {req.id} is marked protected=False."
                    )
        return self


def load_ground_truth(path: Path | str) -> GroundTruth:
    """Load and validate a ground_truth.json file."""
    p = Path(path)
    if not p.is_file():
        raise FileNotFoundError(f"Ground truth file not found: {p}")
    data = json.loads(p.read_text(encoding="utf-8"))
    return GroundTruth.model_validate(data)


def load_all_ground_truths(benchmarks_dir: Path | str) -> dict[str, GroundTruth]:
    """Discover and load all ground_truth.json files in a benchmarks directory."""
    root = Path(benchmarks_dir)
    if not root.is_dir():
        raise FileNotFoundError(f"Benchmarks directory not found: {root}")
    results: dict[str, GroundTruth] = {}
    for gt_path in sorted(root.glob("*/ground_truth.json")):
        gt = load_ground_truth(gt_path)
        case_dir_name = gt_path.parent.name
        if gt.case_id != case_dir_name:
            raise ValueError(
                f"Case ID '{gt.case_id}' in {gt_path} does not match directory name '{case_dir_name}'"
            )
        results[gt.case_id] = gt
    return results
