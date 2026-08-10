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
from pathlib import Path
from typing import Optional

from PySide6.QtCore import QObject, QTimer, Qt
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
ICON_PATH = PROJECT_ROOT / "assets" / "lobster.ico"


class Controller(QObject):
    """Bridge between the GUI window and the daemon subprocess."""

    def __init__(self) -> None:
        super().__init__()
        self._process: Optional[subprocess.Popen] = None
        self._lock: Optional[SingleInstanceLock] = None

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
        if self._process is None:
            return self._read_state_pid()
        return self._process.poll() is None

    def daemon_pid(self) -> Optional[int]:
        if self._process is not None:
            return self._process.pid
        return self._read_state_pid()

    def start_daemon(self) -> None:
        if self.daemon_running():
            return
        log = open(PROJECT_ROOT / "mail_lobster_imap.log", "ab", buffering=0)
        creationflags = 0
        if sys.platform == "win32":
            creationflags = subprocess.CREATE_NO_WINDOW  # type: ignore[attr-defined]
        self._process = subprocess.Popen(
            [sys.executable, "-X", "utf8", "mail_lobster_imap.py"],
            cwd=str(PROJECT_ROOT),
            stdout=log,
            stderr=log,
            stdin=subprocess.DEVNULL,
            creationflags=creationflags,
        )
        self._write_state()

    def stop_daemon(self) -> None:
        if self._process is not None:
            try:
                self._process.terminate()
                try:
                    self._process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    self._process.kill()
                    self._process.wait(timeout=5)
            finally:
                self._process = None
        else:
            pid = self._read_state_pid()
            if pid:
                self._terminate_pid(pid)
        self._clear_state()

    def shutdown(self) -> None:
        self.stop_daemon()
        self.release_launcher_lock()

    def _read_state_pid(self) -> Optional[int]:
        if not LAUNCHER_STATE.exists():
            return None
        try:
            data = json.loads(LAUNCHER_STATE.read_text(encoding="utf-8"))
            return int(data.get("daemon_pid") or 0) or None
        except (OSError, ValueError):
            return None

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

    controller = Controller()
    try:
        controller.acquire_launcher_lock()
    except RuntimeError as exc:
        QMessageBox.critical(None, "启动器已在运行", str(exc))
        return 2

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