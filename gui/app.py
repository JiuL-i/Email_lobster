"""Main dashboard window for the Mail Lobster launcher."""

from __future__ import annotations

import json
import os
import time
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional

from PySide6.QtCore import (
    QObject,
    QSize,
    QTimer,
    Qt,
    Signal,
    Slot,
)
from PySide6.QtGui import QAction, QCloseEvent, QIcon, QKeySequence
from PySide6.QtWidgets import (
    QApplication,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QSplitter,
    QStatusBar,
    QTabWidget,
    QToolBar,
    QVBoxLayout,
    QWidget,
)

import mailbox_admin
from gui.theme import theme_qss
from gui.widgets import (
    COLOR_OK,
    DaemonControls,
    LogTailer,
    LogViewer,
    MailboxesTable,
    ReportsTable,
    StatusCard,
    _human_size,
    level_color,
)


SUPPORTED_THEMES = ("dark", "light")
DEFAULT_THEME = "dark"


PROJECT_ROOT = Path(__file__).resolve().parent.parent
LOG_PATH = PROJECT_ROOT / "mail_lobster_imap.log"
HEARTBEAT_PATH = PROJECT_ROOT / "state" / "heartbeat"
LAUNCHER_LOCK_PATH = PROJECT_ROOT / "state" / "mail_lobster.launcher.lock"
DAEMON_LOCK_PATH = PROJECT_ROOT / "state" / "mail_lobster.lock"
LAUNCHER_STATE = PROJECT_ROOT / "state" / "launcher.state.json"


class DashboardSignals(QObject):
    refresh_status = Signal()
    show_message = Signal(str, str, str)  # level, title, text


class MainWindow(QMainWindow):
    def __init__(self, controller, parent: Optional[QWidget] = None, start_log_tailer: bool = True, theme: str = DEFAULT_THEME) -> None:
        super().__init__(parent)
        self.controller = controller
        self.setWindowTitle("🦞 Mail Lobster 监控面板")
        self.setMinimumSize(1080, 720)

        self._started_at = datetime.now()
        self._signals = DashboardSignals()
        self._theme = theme if theme in SUPPORTED_THEMES else DEFAULT_THEME

        self._build_layout()
        self._wire_signals()
        self._start_timers()
        self._refresh_status()
        self.apply_theme(self._theme)

        self._log_tailer: Optional[LogTailer] = None
        if start_log_tailer:
            self._log_tailer = LogTailer(LOG_PATH, poll_interval=1.0)
            self._log_tailer.new_lines.connect(self._on_log_lines)
            self._log_tailer.start()

    # ---------------- layout ----------------
    def _build_layout(self) -> None:
        central = QWidget(self)
        outer = QVBoxLayout(central)
        outer.setContentsMargins(16, 16, 16, 16)
        outer.setSpacing(12)

        # Status bar with metrics
        grid = QGridLayout()
        grid.setSpacing(12)
        self._status_card = StatusCard("监控状态")
        self._runtime_card = StatusCard("运行时长")
        self._heartbeat_card = StatusCard("距离上一次心跳")
        self._started_card = StatusCard("本次开始运行时间")
        for col, card in enumerate(
            (self._status_card, self._runtime_card, self._heartbeat_card, self._started_card)
        ):
            grid.addWidget(card, 0, col)
        outer.addLayout(grid)

        # Theme toggle bar
        theme_row = QHBoxLayout()
        theme_row.setContentsMargins(0, 0, 0, 0)
        theme_row.addStretch(1)
        self._theme_button = QPushButton()
        self._theme_button.setObjectName("ThemeToggle")
        self._theme_button.setMinimumWidth(140)
        self._theme_button.setMinimumHeight(32)
        self._theme_button.clicked.connect(self._toggle_theme)
        theme_row.addWidget(self._theme_button)
        outer.addLayout(theme_row)

        # Tabs: log, mailboxes, reports
        splitter = QSplitter(Qt.Orientation.Horizontal)
        self._log_viewer = LogViewer()
        right_tabs = QTabWidget()
        right_tabs.setMinimumWidth(420)

        right_panel = QWidget()
        right_layout = QVBoxLayout(right_panel)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.setSpacing(8)

        self._daemon_controls = DaemonControls()
        right_layout.addWidget(self._daemon_controls)

        self._mailboxes_table = MailboxesTable()
        self._reports_table = ReportsTable()
        right_tabs.addTab(self._mailboxes_table, "📬 监督邮箱")
        right_tabs.addTab(self._reports_table, "📑 报表")
        right_layout.addWidget(right_tabs, 1)

        splitter.addWidget(self._log_viewer)
        splitter.addWidget(right_panel)
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 2)
        outer.addWidget(splitter, 1)

        self.setCentralWidget(central)

        # Status bar
        sb = QStatusBar(self)
        self._last_heartbeat_label = QLabel("⏰ 上次心跳: --")
        self._lock_label = QLabel("🔒 单实例锁: --")
        sb.addPermanentWidget(self._last_heartbeat_label, 1)
        sb.addPermanentWidget(self._lock_label, 0)
        self.setStatusBar(sb)

    def _wire_signals(self) -> None:
        self._daemon_controls.start_clicked.connect(self._on_start)
        self._daemon_controls.stop_clicked.connect(self._on_stop)
        self._daemon_controls.restart_clicked.connect(self._on_restart)
        self._daemon_controls.add_mailbox_clicked.connect(self._on_add_mailbox)

    def _start_timers(self) -> None:
        self._second_timer = QTimer(self)
        self._second_timer.setInterval(1000)
        self._second_timer.timeout.connect(self._tick_seconds)
        self._second_timer.start()

        self._slow_timer = QTimer(self)
        self._slow_timer.setInterval(2000)
        self._slow_timer.timeout.connect(self._tick_slow)
        self._slow_timer.start()

    # ---------------- timers ----------------
    @Slot()
    def _tick_seconds(self) -> None:
        elapsed = datetime.now() - self._started_at
        seconds = int(elapsed.total_seconds())
        self._runtime_card.update_value(_format_duration(seconds), "info", f"开始于 {self._started_at:%H:%M:%S}")
        self._started_card.update_value(self._started_at.strftime("%Y-%m-%d %H:%M:%S"), "ok", "本次启动时间")

        hb_age = _heartbeat_age_seconds()
        if hb_age is None:
            if not HEARTBEAT_PATH.exists():
                hint = "守护尚未运行，或未在 config 中启用 heartbeat.enabled"
            else:
                hint = "心跳文件存在但读取失败"
            self._heartbeat_card.update_value("无心跳", "warn", hint)
        else:
            level = "ok" if hb_age < 120 else ("warn" if hb_age < 300 else "error")
            self._heartbeat_card.update_value(_format_duration(hb_age), level, _heartbeat_hint(hb_age))
        self._last_heartbeat_label.setText(f"⏰ 上次心跳: {_last_heartbeat_text()}")

    @Slot()
    def _tick_slow(self) -> None:
        self._refresh_status()
        self._refresh_mailboxes()
        self._refresh_reports()

    @Slot(list)
    def _on_log_lines(self, lines) -> None:
        self._log_viewer.append_lines(lines)

    @Slot()
    def _refresh_status(self) -> None:
        running = self.controller.daemon_running()
        ext_pid = self.controller.external_daemon_alive()
        pid = self.controller.daemon_pid()
        if running:
            self._status_card.update_value("运行中", "ok", f"守护 PID {pid}")
        elif ext_pid is not None:
            self._status_card.update_value(
                "外部运行", "info", f"检测到外部守护 PID {ext_pid}（非本启动器启动）"
            )
        else:
            err = self.controller.last_error()
            hint = err or "守护进程尚未启动或已停止"
            self._status_card.update_value("未运行", "warn", hint)
        self._daemon_controls.set_running(running)
        self._lock_label.setText(
            "🔒 单实例锁: 已持有" if self.controller.launcher_lock_held() else "🔓 单实例锁: 已释放"
        )

    @Slot()
    def _refresh_mailboxes(self) -> None:
        path = PROJECT_ROOT / "config_imap_forward.json"
        if not path.exists():
            self._mailboxes_table.populate([])
            return
        try:
            config = mailbox_admin.load_config(path)
            self._mailboxes_table.populate(mailbox_admin.list_mailboxes(config))
        except Exception:  # noqa: BLE001 - non-fatal UI refresh
            self._mailboxes_table.populate([])

    @Slot()
    def _refresh_reports(self) -> None:
        rows: list[tuple[Path, str, float, int]] = []
        for sub, kind in (("reports/cycles", "周期报表"), ("reports/summaries", "总结报表")):
            directory = PROJECT_ROOT / sub
            if not directory.exists():
                continue
            for entry in directory.iterdir():
                if entry.suffix.lower() == ".xlsx":
                    stat = entry.stat()
                    rows.append((entry, kind, stat.st_mtime, stat.st_size))
        rows.sort(key=lambda r: r[2], reverse=True)
        self._reports_table.populate(rows[:200])

    # ---------------- daemon control ----------------
    @Slot()
    def _on_start(self) -> None:
        try:
            self.controller.start_daemon()
        except RuntimeError as exc:
            QMessageBox.warning(self, "启动失败", str(exc))
        self._refresh_status()

    @Slot()
    def _on_stop(self) -> None:
        self.controller.stop_daemon()
        err = self.controller.last_error()
        if err:
            QMessageBox.information(self, "停止", err)
        self._refresh_status()

    @Slot()
    def _on_restart(self) -> None:
        self.controller.stop_daemon()
        try:
            self.controller.start_daemon()
        except RuntimeError as exc:
            QMessageBox.warning(self, "重启失败", str(exc))
        self._refresh_status()

    @Slot()
    def _on_add_mailbox(self) -> None:
        from gui.dialogs import MailboxFormDialog

        dialog = MailboxFormDialog(self)
        dialog.exec()
        self._refresh_mailboxes()

    # ---------------- theme ----------------
    def apply_theme(self, theme: str) -> None:
        """Apply the given theme to the main window and re-style all child widgets."""
        self._theme = theme if theme in SUPPORTED_THEMES else DEFAULT_THEME
        app = QApplication.instance()
        if app is not None:
            app.setStyleSheet(theme_qss(self._theme))
        if self._theme_button is not None:
            self._theme_button.setText("🌞 切换浅色" if self._theme == "dark" else "🌙 切换深色")
        # Force a polish cycle so dynamic properties (level=...) pick up the new palette.
        for widget in self.findChildren(QWidget):
            widget.style().unpolish(widget)
            widget.style().polish(widget)
        # Status cards need an extra refresh because their level colours depend on the new palette.
        self._status_card.update_value(self._status_card._value_label.text(), "info")
        self._runtime_card.update_value(self._runtime_card._value_label.text(), "info")
        self._heartbeat_card.update_value(self._heartbeat_card._value_label.text(), "info")
        self._started_card.update_value(self._started_card._value_label.text(), "info")

    @Slot()
    def _toggle_theme(self) -> None:
        self.apply_theme("light" if self._theme == "dark" else "dark")

    # ---------------- shutdown ----------------
    def closeEvent(self, event: QCloseEvent) -> None:  # noqa: D401
        self._second_timer.stop()
        self._slow_timer.stop()
        if self._log_tailer is not None:
            self._log_tailer.stop()
            self._log_tailer.wait(2000)
        super().closeEvent(event)


def _format_duration(seconds: int) -> str:
    seconds = max(0, int(seconds))
    hours, rem = divmod(seconds, 3600)
    minutes, secs = divmod(rem, 60)
    if hours:
        return f"{hours:02d}:{minutes:02d}:{secs:02d}"
    return f"{minutes:02d}:{secs:02d}"


def _heartbeat_age_seconds() -> Optional[int]:
    if not HEARTBEAT_PATH.exists():
        return None
    age = time.time() - HEARTBEAT_PATH.stat().st_mtime
    return int(age)


def _last_heartbeat_text() -> str:
    if not HEARTBEAT_PATH.exists():
        return "(无)"
    mtime = HEARTBEAT_PATH.stat().st_mtime
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(mtime))


def _heartbeat_hint(age: int) -> str:
    if age < 60:
        return "心跳新鲜"
    if age < 120:
        return "略慢（检查间隔 5 分钟时正常）"
    if age < 300:
        return "心跳偏慢，查看日志/检查 IMAP"
    return "心跳异常：守护可能未运行或卡死"