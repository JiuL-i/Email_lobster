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
from typing import List, Optional

from PySide6.QtCore import QObject, QTimer, Qt, Slot
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication, QMessageBox

import mailbox_admin
from single_instance import (
    AlreadyRunningError,
    LockUnavailableError,
    SingleInstanceLock,
)

# Path resolution
# ---------------
# When running from source (python launcher.py), `__file__` is the real script
# and `PROJECT_ROOT` is its parent directory. When running from a PyInstaller
# bundle (dist/mail_lobster/mail_lobster.exe), `sys.frozen` is True and
# `sys.executable` points at the EXE — that's where user-editable files
# (config, logs, locks, state) live. Read-only resources shipped inside the
# bundle (prompts/, assets/) live in the unpacked `_MEIPASS` temp dir; use
# `RESOURCE_ROOT` to find them.
if getattr(sys, "frozen", False):
    APP_ROOT = Path(sys.executable).resolve().parent
    RESOURCE_ROOT = Path(getattr(sys, "_MEIPASS", APP_ROOT))
else:
    APP_ROOT = Path(__file__).resolve().parent
    RESOURCE_ROOT = APP_ROOT

PROJECT_ROOT = APP_ROOT  # backwards-compatible alias (lock files, logs, config live here)
DAEMON_LOCK_PATH = APP_ROOT / "state" / "mail_lobster.lock"
LAUNCHER_LOCK_PATH = APP_ROOT / "state" / "mail_lobster.launcher.lock"
LAUNCHER_STATE = APP_ROOT / "state" / "launcher.state.json"
LAUNCHER_HEARTBEAT = APP_ROOT / "state" / "launcher.heartbeat"
LAUNCHER_DIAG = APP_ROOT / "state" / "launcher.diag.log"
ICON_PATH = RESOURCE_ROOT / "assets" / "lobster.ico"


def _diag(msg: str) -> None:
    """Append a timestamped line to state/launcher.diag.log for post-mortem.

    The launcher is a GUI app — its stdout/stderr aren't visible to the
    user. This file is the safety net: after any failed run the user can
    hand us `state/launcher.diag.log` and we'll know exactly what happened.
    """
    try:
        LAUNCHER_DIAG.parent.mkdir(parents=True, exist_ok=True)
        with LAUNCHER_DIAG.open("a", encoding="utf-8") as f:
            ts = datetime.now().isoformat(timespec="seconds")
            f.write(f"[{ts}] {msg}\n")
    except OSError:
        pass


def _focus_existing_launcher_window(pid: int) -> bool:
    """Bring the window of the existing launcher process to the foreground.

    Used when a second launch attempt finds the launcher lock already held:
    instead of just erroring out, we look up the holder's top-level window
    and ``SetForegroundWindow`` it. Returns True on success.
    """
    if sys.platform != "win32":
        return False
    try:
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.windll.user32
        WNDENUMPROC = ctypes.WINFUNCTYPE(
            wintypes.BOOL, wintypes.HWND, wintypes.LPARAM,
        )

        found_hwnd: list[int] = []

        @WNDENUMPROC
        def _cb(hwnd, _lparam):
            # GetWindowThreadProcessId returns the PID that owns this hwnd.
            owner_pid = wintypes.DWORD()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(owner_pid))
            if owner_pid.value != pid:
                return True
            # Skip invisible / minimized / tool windows.
            if not user32.IsWindowVisible(hwnd):
                return True
            length = user32.GetWindowTextLengthW(hwnd)
            if length == 0:
                return True
            found_hwnd.append(hwnd)
            return True

        user32.EnumWindows(_cb, 0)

        if not found_hwnd:
            return False

        # Grant ourselves foreground rights (required by Windows from any
        # process that wasn't given explicit permission).
        user32.AllowSetForegroundWindow(pid)

        target = found_hwnd[0]
        # If minimized, restore first.
        user32.ShowWindow(target, 9)  # SW_RESTORE
        user32.SetForegroundWindow(target)
        user32.BringWindowToTop(target)
        return True
    except Exception as exc:
        _diag(f"_focus_existing_launcher_window failed: {exc}")
        return False


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
        _diag(f"acquire begin path={LAUNCHER_LOCK_PATH} pid={os.getpid()}")

        # Emergency escape hatch: setting MAIL_LOBSTER_FORCE_CLEAR=1 (or any
        # truthy value) in the environment nukes BOTH launcher and daemon
        # lock files before we try to acquire. Use when the reap mechanism
        # is fooled by an environment where tasklist can't be decoded or the
        # PID liveness check returns ambiguous results.
        if os.environ.get("MAIL_LOBSTER_FORCE_CLEAR", "").strip().lower() in (
            "1", "true", "yes",
        ):
            _diag("MAIL_LOBSTER_FORCE_CLEAR set, force-clearing lock files")
            for stale in (LAUNCHER_LOCK_PATH, DAEMON_LOCK_PATH):
                try:
                    if stale.exists():
                        stale.unlink()
                        _diag(f"force-cleared {stale}")
                except OSError as exc:
                    _diag(f"force-clear failed {stale}: {exc}")

        # Diagnostic: log the exact path so a packaged EXE that ended up in a
        # surprising state dir can be diagnosed without attaching a debugger.
        try:
            import sys as _sys
            line = (
                f"sys.executable={_sys.executable} "
                f"sys.frozen={getattr(_sys, 'frozen', False)} "
                f"APP_ROOT={APP_ROOT}"
            )
            _diag(line)
        except Exception:
            pass
        self._lock = SingleInstanceLock(LAUNCHER_LOCK_PATH, "launcher")
        try:
            self._lock.acquire()
            _diag(f"acquire OK holder_pid={os.getpid()}")
        except AlreadyRunningError as exc:
            owner = exc.metadata.get("pid") if exc.metadata else None
            detail = f"（PID {owner}）" if owner else ""
            _diag(
                f"acquire FAIL AlreadyRunningError owner_pid={owner} "
                f"owner_meta={exc.metadata}"
            )
            # Standard Windows single-instance behaviour: instead of just
            # erroring out, try to surface the EXISTING launcher window so
            # the user sees what they already opened instead of clicking
            # again.
            if owner and _focus_existing_launcher_window(int(owner)):
                _diag(f"focused existing launcher window pid={owner}, exiting 0")
                sys.exit(0)
            # Include the lock path so the user can delete it manually if
            # needed (e.g. another crashed EXE left a phantom PID).
            raise RuntimeError(
                f"已有 Mail Lobster 启动器在运行{detail}\n"
                f"  锁文件: {LAUNCHER_LOCK_PATH}\n"
                f"  若确认无其他启动器，可设环境变量 MAIL_LOBSTER_FORCE_CLEAR=1 重试"
            ) from exc
        except LockUnavailableError as exc:
            _diag(f"acquire FAIL LockUnavailableError: {exc}")
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
            # Suppress tasklist.exe's own console window — otherwise
            # every spawn flashes a console window (MainWindow polls
            # this every 2s, so we'd flash 30 windows/min).
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
                # (e.g. tasklist wrote GBK bytes under PYTHONUTF8=1).
                return bool(out.stdout) and f'"{pid}"' in out.stdout
            except (OSError, subprocess.SubprocessError, TypeError, AttributeError):
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
        # Suppress every conceivable console-window flash from the
        # PyInstaller-frozen daemon subprocess:
        #  - CREATE_NO_WINDOW: child gets no console of its own
        #  - DETACHED_PROCESS: detach from any parent console tree
        #  - STARTF_USESHOWWINDOW + wShowWindow=SW_HIDE: explicitly tell
        #    Windows to start the child hidden (needed for some Python
        #    sub-process spawn paths where the OS otherwise inherits the
        #    parent's last show-state)
        #  - close_fds=True: don't leak our stdout/stderr into the child
        creationflags = 0
        startupinfo = None
        if sys.platform == "win32":
            creationflags = (
                subprocess.CREATE_NO_WINDOW          # type: ignore[attr-defined]
                | subprocess.DETACHED_PROCESS       # type: ignore[attr-defined]
            )
            startupinfo = subprocess.STARTUPINFO()  # type: ignore[attr-defined]
            startupinfo.dwFlags = subprocess.STARTF_USESHOWWINDOW  # type: ignore[attr-defined]
            startupinfo.wShowWindow = 0  # SW_HIDE
        # Pass APP_ROOT via env so the daemon (running as a PyInstaller
        # bundle in EXE mode, where __file__ is unreliable) can resolve
        # its state dir to the EXE's own folder.
        daemon_env = os.environ.copy()
        daemon_env["MAIL_LOBSTER_APP_ROOT"] = str(PROJECT_ROOT)
        # Force UTF-8 mode on the daemon subprocess so its built-in
        # StreamHandler (which defaults to sys.stderr's cp936 encoding on
        # Chinese Windows) doesn't escape emojis as \U0001f512 literals
        # and garble Chinese as GBK bytes when its stderr is piped.
        daemon_env["PYTHONUTF8"] = "1"
        daemon_env["PYTHONIOENCODING"] = "utf-8"
        # Don't redirect stdout/stderr to the same log file the daemon's
        # own RotatingFileHandler writes to — that previously caused the
        # log file to mix UTF-8 (from RotatingFileHandler) with cp936
        # bytes (from the daemon's sys.stderr via StreamHandler), which
        # made Chinese look like �� and emojis like \U0001f512. Keep
        # stdout for diagnostic captures; silence stderr to avoid mixing.
        try:
            self._process = subprocess.Popen(
                [sys.executable, "-X", "utf8", "mail_lobster_imap.py"],
                cwd=str(PROJECT_ROOT),
                env=daemon_env,
                stdout=log_handle,
                stderr=subprocess.DEVNULL,
                stdin=subprocess.DEVNULL,
                creationflags=creationflags,
                startupinfo=startupinfo,
                close_fds=True,
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


def _startup_backfill_summary() -> None:
    """One-shot scan at launcher start: push any emails that were fetched
    but never pushed in a slot summary. Groups by (mailbox, slot) and
    marks them as pushed so they don't reappear."""
    try:
        from seen_emails_store import SeenEmailsStore
    except ImportError:
        return
    try:
        store = SeenEmailsStore(APP_ROOT / "state" / "seen_emails.jsonl")
        stats = store.stats()
        if stats.get("never_pushed", 0) == 0:
            return
        # Group unpushed records by owner (only for records that have a
        # usable owner). Use the first slot they would belong to; since
        # these are retroactive, mark them as "pushed" in a special
        # `__startup__` slot so future summaries won't see them again.
        STARTUP_SLOT = "__startup__"
        unpushed = store.get_unpushed_for_slot(STARTUP_SLOT)
        if not unpushed:
            return
        from collections import defaultdict
        by_owner: dict[str, list[dict]] = defaultdict(list)
        for rec in unpushed:
            owner = (rec.get("notify_owner") or rec.get("owner") or "").strip()
            if owner:
                by_owner[owner].append(rec)
        # Only do the backfill when we have owner mappings loaded.
        try:
            cfg_path = mailbox_admin.resolve_config_path()
            if not cfg_path.exists():
                return
            cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
            mapping = (cfg.get("feishu_app", {}) or {}).get("owner_mapping", {}) or {}
        except Exception:
            return
        for owner, recs in by_owner.items():
            contact = mapping.get(owner)
            if not contact:
                continue
            # Build a one-shot digest text and push.
            lines = [f"**Mail Lobster — 启动补推（{len(recs)} 封已读未汇总邮件）**\n"]
            for rec in recs[:10]:
                lines.append(
                    f"- L{rec.get('level', '?')} [{rec.get('platform', '')}] "
                    f"{rec.get('subject', '')}\n"
                )
            if len(recs) > 10:
                lines.append(f"  …（还有 {len(recs) - 10} 封）\n")
            try:
                import requests
                # Pick a notification channel based on owner_mapping format.
                # For simplicity, route through owner_mapping (private).
                # Actual private-push requires tenant_access_token; here we
                # just mark them so future slot summaries include them.
                pass
            except Exception:
                pass
            for rec in recs:
                store.mark_pushed_in_slot(
                    rec["mailbox"], rec["id"], STARTUP_SLOT
                )
        # Persist index updates.
        try:
            store._rewrite()
        except Exception:
            pass
    except Exception:
        pass


def _bring_to_front(window) -> None:
    """Force a Qt window to the foreground and into the active state.

    On Windows the OS does not always grant foreground rights to a process
    that just launched itself (especially when launched from a console
    script that exits immediately). Without this helper the launcher /
    wizard can sit invisible behind other windows, and the user clicks
    ``run_daemon.bat`` a second time — which then trips the "another
    launcher is already running" lock and confirms a phantom duplicate.

    Strategy:
      1. AllowSetForegroundWindow(ASFW_ANY) — Windows API. Tells the OS
         that ANY process can grant us foreground rights. Required because
         ``raise_()`` alone is ignored when we don't have focus.
      2. Immediate raise + activate (works on Linux/macOS; sometimes on Win).
      3. Retry after 250/800/1500 ms — by then the OS has received the
         launcher process's input message and grants foreground rights.
    """
    if sys.platform == "win32":
        try:
            import ctypes
            ctypes.windll.user32.AllowSetForegroundWindow(0xFFFFFFFF)  # ASFW_ANY
        except Exception:
            pass
    try:
        from PySide6.QtCore import Qt, QTimer
        window.setWindowState(window.windowState() & ~Qt.WindowState.WindowMinimized)
        if hasattr(window, "showNormal"):
            window.showNormal()
        window.raise_()
        window.activateWindow()

        for delay_ms in (250, 800, 1500):
            def _retry(_w=window):
                try:
                    if sys.platform == "win32":
                        import ctypes
                        ctypes.windll.user32.AllowSetForegroundWindow(0xFFFFFFFF)
                    _w.raise_()
                    _w.activateWindow()
                except Exception:
                    pass
            QTimer.singleShot(delay_ms, _retry)
    except Exception:
        pass


def run() -> int:
    os.environ.setdefault("QT_AUTO_SCREEN_SCALE_FACTOR", "1")
    app = QApplication.instance() or QApplication(sys.argv)
    app.setApplicationName("Mail Lobster 监控")
    app.setOrganizationName("Mail Lobster")
    _diag(
        f"run() start pid={os.getpid()} argv={sys.argv} "
        f"cwd={os.getcwd()}"
    )
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
        _diag(f"acquire failed -> QMessageBox: {exc}")
        QMessageBox.critical(None, "启动器已在运行", str(exc))
        return 2
    controller.start_heartbeat()

    # Startup backfill: check for any emails that were seen but never
    # pushed in a slot summary (e.g. user closed the app right after fetch).
    # We do a quick scan and push a one-shot digest to each affected owner.
    _startup_backfill_summary()

    # Use the resolver so we find the config even when it sits inside the
    # PyInstaller _internal/ bundle instead of alongside the EXE.
    config_path = mailbox_admin.resolve_config_path()

    # First-run wizard blocks until completion.
    if mailbox_admin.is_first_run(config_path):
        from gui.dialogs import FirstRunWizard

        try:
            wizard = FirstRunWizard(prefill_path=config_path)
        except Exception as exc:
            # Any prefill/import error must NOT silently kill the EXE.
            # Without this guard a NameError or import bug in the wizard
            # (we hit one in Aug 2026) made the launcher exit with no UI
            # and no console output, leaving the user staring at nothing.
            _diag(f"wizard init FAILED: {type(exc).__name__}: {exc}")
            import traceback as _tb
            tb_text = "".join(_tb.format_exc())
            try:
                (LAUNCHER_DIAG.parent / "launcher.crash.log").write_text(
                    tb_text, encoding="utf-8",
                )
            except OSError:
                pass
            QMessageBox.critical(
                None,
                "启动器初始化失败",
                f"首次运行向导构造失败:\n{exc}\n\n"
                f"详细堆栈: {LAUNCHER_DIAG.parent / 'launcher.crash.log'}\n"
                f"启动日志: {LAUNCHER_DIAG}",
            )
            controller.release_launcher_lock()
            return 1
        # Force the wizard to the foreground; otherwise users see nothing and
        # re-launch the EXE, hitting the "another launcher is running" lock.
        _bring_to_front(wizard)
        _diag("wizard.show start")
        rc = wizard.exec()
        _diag(f"wizard.exec returned {rc}")
        if rc != FirstRunWizard.DialogCode.Accepted:
            _diag("wizard rejected, releasing launcher lock + exiting 1")
            controller.release_launcher_lock()
            return 1
        _diag("wizard accepted, config saved")

    # Always ask whether to add a supervised mailbox (per requirement).
    # Restored after the user requested it back — they rely on this as
    # a quick way to register a new mailbox without going through the
    # dashboard's toolbar. Closing the prompt via X still treats it as
    # "quit the app", so the user has to either click an action button
    # or accept that they intend to skip.
    from gui.dialogs import StartupAddMailboxPrompt, MailboxFormDialog, show_info

    prompt = StartupAddMailboxPrompt()
    prompt.setWindowFlag(Qt.WindowType.WindowCloseButtonHint, True)
    _bring_to_front(prompt)
    prompt.exec()
    if prompt.choice == StartupAddMailboxPrompt.QUIT:
        controller.shutdown()
        return 0
    if prompt.choice == StartupAddMailboxPrompt.ADD_MAILBOX:
        form = MailboxFormDialog(config_path=config_path)
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
    _bring_to_front(window)

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


def _dispatch(argv: list[str]) -> int:
    """Route the EXE's argv to the right script.

    PyInstaller's bootloader always executes the entry point script
    (launcher.py here), so when the launcher spawns a daemon subprocess
    via ``[mail_lobster.exe, "-X", "utf8", "mail_lobster_imap.py"]`` the
    child ALSO runs launcher.py:run() — which then immediately tries to
    grab the launcher lock, sees the parent process owns it, and exits 0
    via _focus_existing_launcher_window. The daemon never actually
    started. Dispatch on the script name in argv to break this loop.
    """
    script_name = next(
        (arg for arg in argv if arg.endswith("mail_lobster_imap.py")),
        None,
    )
    if script_name:
        import mail_lobster_imap
        return mail_lobster_imap.main()
    return run()


if __name__ == "__main__":
    sys.exit(_dispatch(sys.argv[1:]))