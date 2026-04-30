"""Discover intake/ folders on the NAS.

We walk NAS_ROOT up to INTAKE_DEPTH_MAX levels deep and yield every dir
literally named `intake`. The dir's parent is the *filing scope* — all
files dropped in `intake/` get filed under that parent.
"""
from __future__ import annotations

import logging
from pathlib import Path

import config

logger = logging.getLogger(__name__)

# Don't descend into these — they aren't user content
_SKIP_DIRS = frozenset({
    "@eaDir",       # Synology indexing
    "#recycle",     # Synology recycle bin
    ".Trashes",     # macOS
    ".Spotlight-V100",
    ".fseventsd",
    "_processed", "_quarantine", "_review",  # nas-intake's own subfolders
})


def _skip(name: str) -> bool:
    return name.startswith(".") or name.startswith("._") or name in _SKIP_DIRS


def find_intakes(root: Path | None = None, max_depth: int = config.INTAKE_DEPTH_MAX) -> list[Path]:
    """Return list of intake/ directories under `root`. Each entry's parent
    is the filing scope.
    """
    base = root if root is not None else config.NAS_ROOT
    if not base.exists() or not base.is_dir():
        logger.debug("find_intakes: root %s does not exist", base)
        return []

    found: list[Path] = []

    def _walk(d: Path, depth: int) -> None:
        if depth > max_depth:
            return
        try:
            entries = list(d.iterdir())
        except (PermissionError, OSError) as exc:
            logger.warning("find_intakes: cannot list %s: %s", d, exc)
            return
        for entry in entries:
            if not entry.is_dir():
                continue
            if _skip(entry.name):
                continue
            if entry.name.lower() == "intake":
                found.append(entry)
                # don't recurse INTO an intake/ — its subdirs are processing buckets
                continue
            _walk(entry, depth + 1)

    _walk(base, depth=1)
    return found
