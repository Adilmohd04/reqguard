"""Access to the bundled example repository.

The example is a small order-fulfilment module with a written requirements file
and a test suite that passes while leaving two of its five requirements
completely undefended. It ships inside the wheel so ``tia example`` works from a
``pipx`` install with nothing else on disk.
"""

from __future__ import annotations

import shutil
from pathlib import Path

EXAMPLE_NAME = "tia-example"

_IGNORED = shutil.ignore_patterns("__pycache__", "*.pyc", ".pytest_cache", ".tia_cache")


def example_root() -> Path:
    """Absolute path to the bundled example repository inside the package."""
    return Path(__file__).parent / "example_repo"


def copy_example(dest: Path) -> Path:
    """Copy the example repository to ``dest``. The destination must not exist."""
    source = example_root()
    if not source.is_dir():  # pragma: no cover - packaging failure
        raise FileNotFoundError(
            f"The bundled example repository is missing from the installation: {source}"
        )
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(source, dest, ignore=_IGNORED)
    return dest
