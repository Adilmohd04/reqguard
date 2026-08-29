"""Path containment — the guard everything else rests on.

Every read tool (tools/repository.py) and every mutation call
(tools/mutation.py) resolves its target through safe_resolve before touching
the filesystem. If this is wrong, mutations escape the sandbox into the user's
real repository.

Reference: docs/03-implementation-manual.md section 4.2.
"""
from __future__ import annotations

from pathlib import Path

from reqguard.errors import PathEscape

__all__ = ["safe_resolve"]


def safe_resolve(root: Path, candidate: str) -> Path:
    """Resolve ``candidate`` under ``root``, or raise PathEscape.

    Blocks:
      - ``..`` traversal that climbs above the root
      - absolute paths outside the root
      - symlink escapes (interior symlinks pointing outside the root)

    Symlinks are resolved via Path.resolve(), so a symlink whose target stays
    inside the root is allowed; one pointing outside is not. The returned
    path is absolute and symlink-resolved. The path does not need to exist —
    containment is about location, not existence (mutation targets and new
    tool outputs are resolved before they are written).
    """
    resolved_root = Path(root).resolve()
    target = (resolved_root / candidate).resolve()
    if target != resolved_root and resolved_root not in target.parents:
        raise PathEscape(f"{candidate!r} resolves outside the sandbox root")
    return target
