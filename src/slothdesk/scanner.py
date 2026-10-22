"""Find files worth looking at, and skip everything risky."""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from pathlib import Path

from .config import SORTED_DIR, STATE_DIR, Config
from .models import Item

log = logging.getLogger("slothdesk.scan")
PARTIAL_EXT = frozenset({".crdownload", ".part", ".tmp", ".download", ".partial", ".opdownload"})


@dataclass
class ScanResult:
    candidates: list[Path] = field(default_factory=list)
    skipped: list[Item] = field(default_factory=list)
    deferred: int = 0


def scan_folder(cfg: Config, now: float) -> ScanResult:
    """Non-recursive scan of the top level only. Oldest files first."""
    result = ScanResult()
    found: list[tuple[float, Path]] = []
    try:
        entries = list(os.scandir(cfg.root))
    except OSError:
        log.exception("cannot read the folder")
        return result
    for entry in entries:
        name = entry.name
        if name.startswith(".") or name in (SORTED_DIR, STATE_DIR):
            continue
        rel = name
        try:
            if entry.is_symlink():
                result.skipped.append(Item(rel, "skipped", "symlink"))
                continue
            if not entry.is_file(follow_symlinks=False):
                continue
            st = entry.stat(follow_symlinks=False)
        except OSError:
            continue
        if Path(name).suffix.lower() in PARTIAL_EXT:
            result.skipped.append(Item(rel, "skipped", "partial_download"))
        elif st.st_size > cfg.max_file_mb * 1024 * 1024:
            result.skipped.append(Item(rel, "skipped", "too_large"))
        elif now - st.st_mtime < cfg.min_age_s:
            result.skipped.append(Item(rel, "skipped", "too_new"))
        else:
            found.append((st.st_mtime, Path(entry.path)))
    found.sort(key=lambda t: (t[0], t[1].name))
    paths = [p for _, p in found]
    result.candidates = paths[: cfg.max_files_per_run]
    result.deferred = max(0, len(paths) - cfg.max_files_per_run)
    return result
