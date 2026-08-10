"""Cross-platform single-instance OS file lock."""

from __future__ import annotations

import json
import os
import socket
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional


class LockError(RuntimeError):
    """Base class for single-instance lock errors."""


class AlreadyRunningError(LockError):
    """Raised when another process owns the lock."""

    def __init__(self, metadata: Optional[Dict[str, Any]] = None):
        self.metadata = metadata or {}
        owner = self.metadata.get("pid")
        suffix = f" (PID {owner})" if owner else ""
        super().__init__(f"another instance owns the lock{suffix}")


class LockUnavailableError(LockError):
    """Raised when the lock cannot be used due to an I/O or permission error."""


class SingleInstanceLock:
    """An OS-owned, non-blocking lock whose file is safe to retain after exit."""

    def __init__(self, path: os.PathLike[str] | str, mode: str):
        self.path = Path(path).resolve()
        self.mode = mode
        self._file = None
        self._locked = False

    def _lock(self) -> None:
        if sys.platform == "win32":
            import msvcrt

            self._file.seek(0)
            msvcrt.locking(self._file.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(self._file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)

    def _unlock(self) -> None:
        if sys.platform == "win32":
            import msvcrt

            self._file.seek(0)
            msvcrt.locking(self._file.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            fcntl.flock(self._file.fileno(), fcntl.LOCK_UN)

    def _read_metadata(self) -> Optional[Dict[str, Any]]:
        try:
            with self.path.open("r", encoding="utf-8") as stream:
                value = json.load(stream)
            return value if isinstance(value, dict) else None
        except (OSError, ValueError):
            return None

    def acquire(self) -> "SingleInstanceLock":
        if self._locked:
            return self
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self._file = self.path.open("a+b")
            if self._file.seek(0, os.SEEK_END) == 0:
                self._file.write(b" ")
                self._file.flush()
            self._lock()
        except (BlockingIOError, PermissionError, OSError) as exc:
            metadata = self._read_metadata()
            if self._file is not None:
                self._file.close()
                self._file = None
            if isinstance(exc, BlockingIOError) or (
                sys.platform == "win32" and isinstance(exc, PermissionError)
            ) or getattr(exc, "winerror", None) in (33, 36):
                raise AlreadyRunningError(metadata) from exc
            raise LockUnavailableError(f"cannot acquire lock {self.path}: {exc}") from exc

        self._locked = True
        metadata = {
            "pid": os.getpid(),
            "hostname": socket.gethostname(),
            "started_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "mode": self.mode,
            "argv": sys.argv,
        }
        try:
            self._file.seek(0)
            self._file.truncate()
            self._file.write(json.dumps(metadata, ensure_ascii=False).encode("utf-8"))
            self._file.flush()
        except OSError:
            # Lock ownership remains valid even if diagnostics cannot be written.
            pass
        return self

    def release(self) -> None:
        if self._file is None:
            return
        try:
            if self._locked:
                self._unlock()
        finally:
            self._locked = False
            self._file.close()
            self._file = None

    def __enter__(self) -> "SingleInstanceLock":
        return self.acquire()

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        self.release()


def lock_path_for_script(script_file: os.PathLike[str] | str) -> Path:
    """Return the installation-local lock path, independent of current directory."""
    return Path(script_file).resolve().parent / "state" / "mail_lobster.lock"


def requires_instance_lock(args: list[str]) -> bool:
    """Whether CLI arguments can touch shared mail/report state."""
    return not any(flag in args for flag in ("--test-feishu", "--test-feishu-app", "--report-error"))
