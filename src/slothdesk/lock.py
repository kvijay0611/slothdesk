"""Single-instance guard so two runs never touch the same folder at once.

Uses an OS-level file lock (released automatically if the process crashes, so there
is no stale-lock problem to clean up).
"""

from __future__ import annotations

import os
from pathlib import Path
from types import TracebackType
from typing import IO


class AlreadyRunning(RuntimeError):
    """Another slothdesk process holds the lock for this folder."""


class SingleInstance:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._fh: IO[bytes] | None = None

    def __enter__(self) -> SingleInstance:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fh = open(self.path, "a+b")  # noqa: SIM115 - held open for the lock's lifetime
        try:
            fh.seek(0)
            if os.name == "nt":  # pragma: no cover - exercised on Windows only
                import msvcrt

                msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            fh.close()
            raise AlreadyRunning("another slothdesk run is already using this folder") from exc
        self._fh = fh
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        if self._fh is None:
            return
        try:
            if os.name == "nt":  # pragma: no cover
                import msvcrt

                self._fh.seek(0)
                msvcrt.locking(self._fh.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(self._fh.fileno(), fcntl.LOCK_UN)
        finally:
            self._fh.close()
            self._fh = None
