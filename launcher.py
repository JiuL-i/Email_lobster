"""Launcher entrypoint.

- Acquires a dedicated OS lock (state/mail_lobster.launcher.lock) so only one
  instance of the GUI runs at a time.
- Detects first run; shows the wizard when needed.
- Always shows the "新增监督邮箱？" prompt (per user requirement).
- Spawns the daemon as a subprocess and supervises it via the existing
  state/mail_lobster.lock metadata.
- Exposes a Controller API consumed by gui.app.MainWindow.
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from PySide6.QtCore import QObject, QTimer, Qt, Slot
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication, QMessageBox

import mailbox_admin
from single_instance import (
    AlreadyRunningError,
    LockUnavailableError,
    SingleInstanceLock,
)

PROJECT_ROOT = Path(__file__).resolve().parent
DAEMON_LOCK_PATH = PROJECT_ROOT / "state" / "mail_lobster.lock"
LAUNCHER_LOCK_PATH = PROJECT_ROOT / "state" / "mail_lobster.launcher.lock"
LAUNCHER_STATE = PROJECT_ROOT / "state" / "launcher.state.json"
LAUNCHER_HEARTBEAT = PROJECT_ROOT / "state" / "launcher.heartbeat"
ICON_PATH = PROJECT_ROOT / "assets" / "lobster.ico"


class Controller(QObject):
    """Bridge between the GUI window and the daemon subprocess."""

    def __init__(self) -> None:
        super().__init__()
        self._process: Optional[subprocess.Popen] = None
        self._lock: Optional[SingleInstanceLock] = None
        self._last_error: Optional[str] = None
        self._heartbeat_timer: Optional[QTimer] = None

    def acquire_launcher_lock(self) -> None:
        LAUNCHER_LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
        self._lock = SingleInstanceLock(LAUNCHER_LOCK_PATH, "launcher")
        try:
            self._lock.acquire()
        except AlreadyRunningError as exc:
            owner = exc.metadata.get("pid") if exc.metadata else None
            detail = f"（PID {owner}）" if owner else ""
            raise RuntimeError(f"已有 Mail Lobster 启动器在运行{detail}") from exc
        except LockUnavailableError as exc:
            raise RuntimeError(f"启动器锁不可用: {exc}") from exc

    def release_launcher_lock(self) -> None:
        if self._lock is not None:
            self._lock.release()
            self._lock = None

    def launcher_lock_held(self) -> bool:
        return self._lock is not None

    # ---- daemon supervision ----
    def daemon_running(self) -> bool:
        """True iff the controller has a live handle to the daemon.

        We deliberately do NOT trust state-file PIDs (they go stale), so the
        dashboard only ever claims "running" when we ourselves spawned it.
        """
        if self._process is None:
            return False
        return self._process.poll() is None

    def external_daemon_alive(self) -> Optional[int]:
        """Detect a daemon started outside of this launcher.

        Returns its PID if `state/mail_lobster.lock` exists and points at a
        live process; otherwise None. This lets the status card distinguish
        "外部启动的守护在跑" from "没有守护".
        """
        if not DAEMON_LOCK_PATH.exists():
            return None
        try:
            text = DAEMON_LOCK_PATH.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return None
        # The lock file may contain diagnostic JSON ({"pid": ..., "mode": ...})
        # or be empty (just used for the OS-level flock). Try to parse JSON
        # for a PID first; fall back to None if we cannot.
        try:
            data = json.loads(text or "{}")
            pid = int(data.get("pid") or 0)
        except (ValueError, json.JSONDecodeError):
            return None
        if pid <= 0:
            return None
        return pid if self._pid_alive(pid) else None

    @staticmethod
    def _pid_alive(pid: int) -> bool:
        if sys.platform == "win32":
            try:
                out = subprocess.run(
                    ["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"],
                    capture_output=True,
                    text=True,
                    timeout=3,
                )
                return f'"{pid}"' in out.stdout
            except (OSError, subprocess.SubprocessError):
                return False
        try:
            os.kill(pid, 0)
            return True
        except ProcessLookupError:
            return False
        except PermissionError:
            return True

    def daemon_pid(self) -> Optional[int]:
        if self._process is not None and self._process.poll() is None:
            return self._process.pid
        return self.external_daemon_alive()

    def last_error(self) -> Optional[str]:
        return self._last_error

    def start_daemon(self) -> None:
        self._last_error = None
        if self.daemon_running():
            return
        try:
            log_handle = open(PROJECT_ROOT / "mail_lobster_imap.log", "ab", buffering=0)
        except OSError as exc:
            self._last_error = f"无法打开日志文件: {exc}"
            raise RuntimeError(self._last_error) from exc
        creationflags = 0
        if sys.platform == "win32":
            creationflags = subprocess.CREATE_NO_WINDOW  # type: ignore[attr-defined]
        try:
            self._process = subprocess.Popen(
                [sys.executable, "-X", "utf8", "mail_lobster_imap.py"],
                cwd=str(PROJECT_ROOT),
                stdout=log_handle,
                stderr=log_handle,
                stdin=subprocess.DEVNULL,
                creationflags=creationflags,
            )
        except OSError as exc:
            log_handle.close()
            self._last_error = f"subprocess.Popen 失败: {exc}"
            raise RuntimeError(self._last_error) from exc
        self._write_state()

    def stop_daemon(self) -> None:
        self._last_error = None
        if self._process is not None:
            try:
                self._process.terminate()
                try:
                    self._process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    self._process.kill()
                    self._process.wait(timeout=5)
            except OSError as exc:
                self._last_error = f"终止守护失败: {exc}"
            finally:
                self._process = None
            self._clear_state()
            return
        # No handle we own — try the externally started daemon.
        ext_pid = self.external_daemon_alive()
        if ext_pid is None:
            self._last_error = "没有找到 launcher 启动的守护进程，也没有外部守护在运行。"
            return
        self._terminate_pid(ext_pid)
        self._clear_state()

    def shutdown(self) -> None:
        self._stop_heartbeat()
        self.stop_daemon()
        self.release_launcher_lock()

    # ---- launcher heartbeat (independent of daemon config) ----
    def start_heartbeat(self) -> None:
        """Write a small JSON heartbeat every second as long as the launcher is up.

        This file (`state/launcher.heartbeat`) is owned by the GUI launcher
        and is updated regardless of whether the daemon has its own
        heartbeat.enabled configured. The dashboard reads this file first,
        so the panel always shows a live signal even when the daemon is
        not configured to write one. The daemon-side `state/heartbeat`
        remains for OpenClaw watchdogs.
        """
        if self._heartbeat_timer is not None:
            return
        self._heartbeat_timer = QTimer()
        self._heartbeat_timer.setInterval(1000)
        self._heartbeat_timer.timeout.connect(self._write_launcher_heartbeat)
        self._heartbeat_timer.start()
        self._write_launcher_heartbeat()  # immediate first write

    def _stop_heartbeat(self) -> None:
        if self._heartbeat_timer is not None:
            self._heartbeat_timer.stop()
            self._heartbeat_timer = None

    @Slot()
    def _write_launcher_heartbeat(self) -> None:
        try:
            LAUNCHER_HEARTBEAT.parent.mkdir(parents=True, exist_ok=True)
            payload = {
                "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "launcher_pid": os.getpid(),
                "daemon_pid": self._process.pid if self._process and self._process.poll() is None else None,
                "daemon_alive": self.daemon_running(),
            }
            tmp = LAUNCHER_HEARTBEAT.with_suffix(".heartbeat.tmp")
            tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
            os.replace(tmp, LAUNCHER_HEARTBEAT)
        except OSError:
            # Heartbeat failures must never crash the launcher.
            pass

    def _write_state(self) -> None:
        LAUNCHER_STATE.parent.mkdir(parents=True, exist_ok=True)
        pid = self._process.pid if self._process else None
        LAUNCHER_STATE.write_text(
            json.dumps({"daemon_pid": pid, "started_at": time.time()}, ensure_ascii=False),
            encoding="utf-8",
        )

    def _clear_state(self) -> None:
        try:
            LAUNCHER_STATE.unlink()
        except FileNotFoundError:
            pass
        except OSError:
            pass

    @staticmethod
    def _terminate_pid(pid: int) -> None:
        if sys.platform == "win32":
            subprocess.run(
                ["taskkill", "/PID", str(pid), "/T", "/F"],
                check=False,
                capture_output=True,
            )
        else:
            try:
                os.kill(pid, signal.SIGTERM)
            except ProcessLookupError:
                pass


# ---------------- Qt integration ----------------
def _ensure_icon() -> None:
    if ICON_PATH.exists():
        return
    try:
        from gui.icon import save_ico

        save_ico(ICON_PATH)
    except Exception:
        pass


def run() -> int:
    os.environ.setdefault("QT_AUTO_SCREEN_SCALE_FACTOR", "1")
    app = QApplication.instance() or QApplication(sys.argv)
    app.setApplicationName("Mail Lobster 监控")
    app.setOrganizationName("Mail Lobster")
    _ensure_icon()
    if ICON_PATH.exists():
        app.setWindowIcon(QIcon(str(ICON_PATH)))

    # Apply dark theme by default before any window is constructed.
    from gui.theme import theme_qss, default_font

    app.setFont(default_font())
    app.setStyleSheet(theme_qss("dark"))

    controller = Controller()
    try:
        controller.acquire_launcher_lock()
    except RuntimeError as exc:
        QMessageBox.critical(None, "启动器已在运行", str(exc))
        return 2
    controller.start_heartbeat()

    config_path = PROJECT_ROOT / "config_imap_forward.json"

    # First-run wizard blocks until completion.
    if mailbox_admin.is_first_run(config_path):
        from gui.dialogs import FirstRunWizard

        wizard = FirstRunWizard()
        if wizard.exec() != FirstRunWizard.DialogCode.Accepted:
            controller.release_launcher_lock()
            return 1

    # Always ask whether to add a supervised mailbox (per requirement).
    from gui.dialogs import StartupAddMailboxPrompt, MailboxFormDialog, show_info

    prompt = StartupAddMailboxPrompt()
    prompt.exec()
    if prompt.choice == StartupAddMailboxPrompt.ADD_MAILBOX:
        form = MailboxFormDialog()
        form.exec()

    # Try to start the daemon (best-effort; user can start it manually from the dashboard).
    try:
        controller.start_daemon()
    except Exception as exc:  # noqa: BLE001
        show_info(None, "守护未自动启动", f"守护进程启动失败：{exc}。可在面板中手动启动。")

    from gui.app import MainWindow

    window = MainWindow(controller)
    if ICON_PATH.exists():
        window.setWindowIcon(QIcon(str(ICON_PATH)))
    window.show()

    # Watch for daemon exit to update the status card quickly.
    watcher = QTimer()
    watcher.setInterval(2000)
    watcher.timeout.connect(window._refresh_status)  # noqa: SLF001 - internal hook
    watcher.start()

    def _shutdown() -> None:
        watcher.stop()
        window.close()
        controller.shutdown()

    app.aboutToQuit.connect(_shutdown)
    signal.signal(signal.SIGINT, lambda *_: app.quit())

    rc = app.exec()
    controller.shutdown()
    return rc


if __name__ == "__main__":
    sys.exit(run())