"""Root pytest configuration.

`pyproject.toml` already sets `pythonpath = ["src"]`; this file makes the same
guarantee when pytest is invoked from a subdirectory or without the ini, and
keeps a stray `REQGUARD_REPLAY` in the ambient environment from silently
changing what the agent-stage tests exercise.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

_SRC = Path(__file__).parent / "src"
if _SRC.is_dir() and str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))


@pytest.fixture(autouse=True)
def _no_ambient_replay(monkeypatch):
    """Tests opt into replay explicitly; the environment must not do it for them."""
    monkeypatch.delenv("REQGUARD_REPLAY", raising=False)
