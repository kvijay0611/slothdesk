"""Decide where a file goes and move it without ever overwriting anything."""

from __future__ import annotations

import errno
import os
import re
import shutil
from datetime import datetime
from pathlib import Path

from .classify import Decision
from .config import Config

IMAGES = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp", ".heic"}
MEDIA = {".mp3", ".mp4", ".mkv", ".wav", ".mov", ".m4a", ".avi", ".flac"}
INSTALLERS = {".exe", ".msi", ".dmg", ".pkg", ".deb", ".apk"}
ARCHIVES = {".zip", ".rar", ".7z", ".tar", ".gz"}
RULE_FOLDERS: dict[str, str] = {
    **dict.fromkeys(IMAGES, "Images"),
    **dict.fromkeys(MEDIA, "Media"),
    **dict.fromkeys(INSTALLERS, "Installers"),
    **dict.fromkeys(ARCHIVES, "Archives"),
}
_EXT_OK = re.compile(r"^\.[a-z0-9]{1,10}$")
MAX_PATH = 240  # stay below the classic Windows 260-character limit


def rule_decision(path: Path) -> Decision | None:
    """Cheap, model-free routing by extension. Saves compute on a weak laptop."""
    folder = RULE_FOLDERS.get(path.suffix.lower())
    if folder is None:
        return None
    return Decision(folder, None, 1.0, f"extension {path.suffix.lower()}", "rule")


# File-name hints: deterministic, free, and they work even when a scanned file has no readable
# text. Only obvious words are listed; anything else still goes to the model.
NAME_RULE_EXT = frozenset(
    {".pdf", ".doc", ".docx", ".txt", ".md", ".pptx", ".xlsx", ".csv"} | IMAGES - {".gif", ".heic"}
)
_NAME_RULES: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("Identity", re.compile(
        r"aadhaar|aadhar|passport|pan[ _-]?card|voter|driving[ _-]?licen[cs]e|"
        r"birth[ _-]?certificate|transfer[ _-]?certificate|community[ _-]?(cert|\d)|\btc\b",
        re.IGNORECASE)),
    ("Finance", re.compile(
        r"invoice|rec[ei]+pt|bank[ _-]?statement|pay[ _-]?slip|salary[ _-]?slip",
        re.IGNORECASE)),
    ("Study", re.compile(
        r"assignment|worksheet|lecture|syllabus|semester|\bsem[ _-]?\d|mark[ _-]?sheet|"
        r"transcript|grade[ _-]?(history|card|sheet)|question[ _-]?paper|lab[ _-]?(record|manual)|"
        r"\bmodule[ _-]?\d|\bunit[ _-]?\d",
        re.IGNORECASE)),
)


def name_rule(path: Path, categories: tuple[str, ...]) -> Decision | None:
    """Route by obvious words in the file name (e.g. 'Assignment 8', 'Degree Mark Sheet')."""
    if path.suffix.lower() not in NAME_RULE_EXT:
        return None
    for category, pattern in _NAME_RULES:
        if category in categories and pattern.search(path.stem):
            return Decision(category, None, 0.95, "file name suggests " + category, "rule")
    return None


def is_inside(parent: Path, child: Path) -> bool:
    try:
        return child.resolve(strict=False).is_relative_to(parent.resolve(strict=False))
    except (OSError, RuntimeError):
        return False


def destination_for(
    cfg: Config, src: Path, decision: Decision, taken: set[str], mtime: float
) -> Path:
    ext = src.suffix.lower()
    if not _EXT_OK.match(ext):
        ext = ""
    if decision.title:
        base = f"{datetime.fromtimestamp(mtime):%Y-%m-%d}_{decision.title}"
    else:
        base = src.stem or "file"
    folder = cfg.sorted_dir / decision.category
    base = base[: max(20, MAX_PATH - len(str(folder)) - len(ext) - 8)]
    n = 1
    while True:
        tag = "" if n == 1 else f" ({n})"
        cand = folder / f"{base}{tag}{ext}"
        if str(cand).lower() not in taken and not cand.exists():
            break
        n += 1
        if n > 9999:
            raise FileExistsError("too many name collisions")
    if not is_inside(cfg.sorted_dir, cand):
        raise ValueError("destination escapes the Sorted folder")
    taken.add(str(cand).lower())
    return cand


def safe_move(src: Path, dest: Path) -> None:
    """Move ``src`` to ``dest``; raise FileExistsError instead of overwriting."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    if os.path.lexists(dest):
        raise FileExistsError(str(dest))
    if os.name == "nt":
        os.rename(src, dest)  # Windows refuses to overwrite
        return
    try:
        os.link(src, dest)  # atomic no-clobber on POSIX
    except FileExistsError:
        raise
    except OSError as exc:
        if exc.errno not in (errno.EPERM, errno.EXDEV, errno.ENOTSUP, errno.EMLINK):
            raise
        shutil.move(str(src), str(dest))
    else:
        os.unlink(src)
