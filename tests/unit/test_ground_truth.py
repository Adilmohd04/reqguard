"""Unit tests for ground truth schema and loader.

Owned by Track E (Benchmark Corpus).
"""

from __future__ import annotations

import json
from pathlib import Path
import pytest
from pydantic import ValidationError

from reqguard.models import Category, MutationType, Severity
from reqguard.evaluation.ground_truth import (
    GroundTruth,
    GroundTruthRequirement,
    load_ground_truth,
    load_all_ground_truths,
)


def test_ground_truth_requirement_valid():
    req = GroundTruthRequirement(
        id="REQ-01",
        statement="Cancelled orders cannot be shipped.",
        severity=Severity.HIGH,
        protected=False,
        expected_mutation=MutationType.REMOVE_GUARD,
        expected="survive",
        rationale="No test constructs a cancelled order.",
    )
    assert req.id == "REQ-01"
    assert req.protected is False
    assert req.rationale == "No test constructs a cancelled order."


def test_ground_truth_requirement_missing_rationale_when_unprotected():
    with pytest.raises(ValidationError) as excinfo:
        GroundTruthRequirement(
            id="REQ-01",
            statement="Cancelled orders cannot be shipped.",
            severity=Severity.HIGH,
            protected=False,
            expected_mutation=MutationType.REMOVE_GUARD,
            expected="survive",
            rationale=None,
        )
    assert "rationale" in str(excinfo.value)


def test_ground_truth_valid_case(tmp_path: Path):
    gt_file = tmp_path / "ground_truth.json"
    data = {
        "case_id": "case01-test",
        "category": "business_rule",
        "difficulty": "medium",
        "clean": False,
        "requirements": [
            {
                "id": "REQ-01",
                "statement": "Cancelled orders cannot be shipped.",
                "severity": "high",
                "protected": False,
                "expected_mutation": "remove_guard",
                "expected": "survive",
                "rationale": "Missing test case.",
            }
        ],
    }
    gt_file.write_text(json.dumps(data), encoding="utf-8")
    gt = load_ground_truth(gt_file)
    assert gt.case_id == "case01-test"
    assert gt.category == Category.BUSINESS_RULE
    assert len(gt.requirements) == 1
    assert gt.requirements[0].protected is False


def test_ground_truth_clean_case_with_unprotected_fails(tmp_path: Path):
    gt_file = tmp_path / "ground_truth.json"
    data = {
        "case_id": "case01-test",
        "category": "business_rule",
        "difficulty": "easy",
        "clean": True,
        "requirements": [
            {
                "id": "REQ-01",
                "statement": "Some requirement.",
                "severity": "high",
                "protected": False,
                "rationale": "Defect",
            }
        ],
    }
    gt_file.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(ValidationError) as excinfo:
        load_ground_truth(gt_file)
    assert "clean=True" in str(excinfo.value)


def test_load_all_ground_truths(tmp_path: Path):
    case_dir = tmp_path / "case01-test"
    case_dir.mkdir()
    gt_file = case_dir / "ground_truth.json"
    data = {
        "case_id": "case01-test",
        "category": "business_rule",
        "difficulty": "easy",
        "clean": True,
        "requirements": [
            {
                "id": "REQ-01",
                "statement": "Some requirement.",
                "severity": "high",
                "protected": True,
            }
        ],
    }
    gt_file.write_text(json.dumps(data), encoding="utf-8")

    loaded = load_all_ground_truths(tmp_path)
    assert "case01-test" in loaded
    assert loaded["case01-test"].clean is True


def test_load_all_ground_truths_mismatched_directory(tmp_path: Path):
    case_dir = tmp_path / "case01-wrong-dir"
    case_dir.mkdir()
    gt_file = case_dir / "ground_truth.json"
    data = {
        "case_id": "case01-other-name",
        "category": "business_rule",
        "difficulty": "easy",
        "clean": True,
        "requirements": [
            {
                "id": "REQ-01",
                "statement": "Some requirement.",
                "severity": "high",
                "protected": True,
            }
        ],
    }
    gt_file.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(ValueError) as excinfo:
        load_all_ground_truths(tmp_path)
    assert "does not match directory name" in str(excinfo.value)
