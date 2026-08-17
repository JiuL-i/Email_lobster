"""Cross-platform single-instance lock.

Implementation note: this module deliberately uses *only* a PID-based
existence check on a sentinel file. We avoid OS-level file locks
(msvcrt.locking / fcntl.flock) because PyInstaller-frozen EXEs do not
reliably release them on abnormal exit, which causes the well-known
"stale lock" problem ("another instance is running" when no such
process exists). PID-based checks are also robust against `kill -9`,
taskkill /F, and crashes.

The lock file still records diagnostic metadata (PID, hostname, started_at,
mode, argv) so the user can see *who* holds it.
"""

from __future__ import annotations

import json
import os
import socket
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional


class LockError(RuntimeError):
    """Base class for single-instance lock errors."""


class AlreadyRunningError(LockError):
    """Raised when another live process owns the lock."""

    def __init__(self, metadata: Optional[Dict[str, Any]] = None):
        self.metadata = metadata or {}
        owner = self.metadata.get("pid")
        suffix = f" (PID {owner})" if owner else ""
        super().__init__(f"another instance owns the lock{suffix}")


class LockUnavailableError(LockError):
    """Raised when the lock cannot be used due to an I/O or permission error."""


def _pid_alive(pid: int) -> bool:
    """Best-effort check whether a process is still running."""
    if pid <= 0:
        return False
    if sys.platform == "win32":
        import subprocess

        # tasklist.exe is a console subsystem program — without
        # CREATE_NO_WINDOW every spawn allocates (and immediately frees)
        # a console window. MainWindow._refresh_status calls
        # external_daemon_alive() every 2s, which calls _pid_alive()
        # here, which spawns tasklist — so we were flashing 30 console
        # windows per minute for no reason.
        kwargs: dict = {}
        if hasattr(subprocess, "CREATE_NO_WINDOW"):
            kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW  # type: ignore[attr-defined]
        try:
            out = subprocess.run(
                ["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"],
                capture_output=True,
                text=True,
                timeout=3,
                **kwargs,
            )
            # out.stdout can be None if the reader thread died mid-decode
            # (e.g. tasklist wrote GBK bytes under PYTHONUTF8=1). Treat the
            # unknown case as "alive" to stay conservative.
            return bool(out.stdout) and f'"{pid}"' in out.stdout
        except (OSError, subprocess.SubprocessError, TypeError, AttributeError):
            # Can't query — be conservative and assume alive so we don't
            # accidentally steal someone else's lock.
            return True
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return True


def _read_metadata(path: Path) -> Optional[Dict[str, Any]]:
    try:
        with path.open("r", encoding="utf-8") as stream:
            value = json.load(stream)
        return value if isinstance(value, dict) else None
    except (OSError, ValueError):
        return None


def _write_metadata(path: Path, metadata: Dict[str, Any]) -> None:
    """Atomically replace the lock file with the given metadata.

    Uses a temp file + os.replace so a crashed write can never leave the
    lock in a half-written state that another process might misread.
    """
    tmp = path.with_suffix(path.suffix + ".tmp")
    try:
        with tmp.open("w", encoding="utf-8") as stream:
            json.dump(metadata, stream, ensure_ascii=False)
            stream.write("\n")
        os.replace(tmp, path)
    except OSError:
        try:
            tmp.unlink()
        except OSError:
            pass
        raise


class SingleInstanceLock:
    """A PID-based single-instance lock.

    Unlike the previous OS-lock implementation, this does not block the
    file at the OS level. Instead it relies on liveness checks of the
    recorded PID. If the recorded PID is no longer alive, the lock is
    considered stale and the caller takes it over. If a second process
    starts while the first is still alive, the second will see the live
    PID and raise AlreadyRunningError.

    This trades a tiny race window (a process that dies *between* our
    liveness check and our metadata write) for the much more common
    case of being able to recover from abnormal exits.
    """

    def __init__(self, path: os.PathLike[str] | str, mode: str):
        self.path = Path(path).resolve()
        self.mode = mode
        self._held = False
        self._hold_pid: Optional[int] = None

    @property
    def held(self) -> bool:
        return self._held

    def acquire(self) -> "SingleInstanceLock":
        if self._held:
            return self

        # Reap any stale lock whose PID is no longer alive.
        self._reap_stale_lock()

        # Check whether anyone else is now alive and holding the lock.
        prior = _read_metadata(self.path) if self.path.exists() else None
        prior_pid = (prior or {}).get("pid") if prior else None
        if prior_pid and int(prior_pid) != os.getpid() and _pid_alive(int(prior_pid)):
            raise AlreadyRunningError(prior or {})

        # Write our own metadata atomically.
        self.path.parent.mkdir(parents=True, exist_ok=True)
        metadata = {
            "pid": os.getpid(),
            "hostname": socket.gethostname(),
            "started_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "mode": self.mode,
            "argv": sys.argv,
        }
        try:
            _write_metadata(self.path, metadata)
        except OSError as exc:
            raise LockUnavailableError(f"cannot write lock {self.path}: {exc}") from exc
        self._held = True
        self._hold_pid = os.getpid()
        return self

    def release(self) -> None:
        # Only delete the file if it still belongs to us. This protects
        # against a race where we released, another instance took over,
        # and we then delete *their* lock file.
        if not self._held:
            return
        try:
            prior = _read_metadata(self.path)
            if prior and prior.get("pid") == self._hold_pid:
                try:
                    self.path.unlink()
                except FileNotFoundError:
                    pass
                except OSError:
                    pass
        finally:
            self._held = False
            self._hold_pid = None

    def _reap_stale_lock(self) -> None:
        """If the lock exists but its recorded PID is dead, delete it."""
        if not self.path.exists():
            return
        prior = _read_metadata(self.path)
        if not prior:
            # Corrupt/empty file — remove so we can write a clean one.
            try:
                self.path.unlink()
            except OSError:
                pass
            return
        prior_pid = prior.get("pid")
        try:
            prior_pid_int = int(prior_pid) if prior_pid is not None else 0
        except (TypeError, ValueError):
            prior_pid_int = 0
        if prior_pid_int and not _pid_alive(prior_pid_int):
            try:
                self.path.unlink()
            except FileNotFoundError:
                pass
            except OSError:
                pass

    def __enter__(self) -> "SingleInstanceLock":
        return self.acquire()

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        self.release()


def lock_path_for_script(script_file: os.PathLike[str] | str) -> Path:
    """Return the installation-local lock path, independent of current directory.

    When the caller is a PyInstaller bundle (`sys.frozen` is True), the lock
    must live alongside the EXE so it survives across launches. Otherwise
    we anchor on the script's parent directory.
    """
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent / "state" / "mail_lobster.lock"
    return Path(script_file).resolve().parent / "state" / "mail_lobster.lock"


def requires_instance_lock(args: list[str]) -> bool:
    """Whether CLI arguments can touch shared mail/report state."""
    return not any(flag in args for flag in ("--test-feishu", "--test-feishu-app", "--report-error"))
