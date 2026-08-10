"""Custom Qt widgets for the dashboard."""

from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Callable, Iterable, Optional

from PySide6.QtCore import (
    QObject,
    QSize,
    Qt,
    QThread,
    QTimer,
    QUrl,
    Signal,
    Slot,
)
from PySide6.QtGui import (
    QAction,
    QColor,
    QDesktopServices,
    QFont,
    QSyntaxHighlighter,
    QTextCharFormat,
    QTextCursor,
)
from PySide6.QtWidgets import (
    QAbstractItemView,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QSizePolicy,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)


COLOR_OK = QColor("#16a34a")
COLOR_WARN = QColor("#d97706")
COLOR_ERROR = QColor("#dc2626")
COLOR_INFO = QColor("#0ea5e9")
COLOR_NEUTRAL = QColor("#64748b")


def level_color(level: str) -> QColor:
    mapping = {
        "ok": COLOR_OK,
        "warn": COLOR_WARN,
        "error": COLOR_ERROR,
        "info": COLOR_INFO,
    }
    return mapping.get(level, COLOR_NEUTRAL)


class StatusCard(QFrame):
    """Coloured card displaying a metric that updates every timer tick."""

    def __init__(self, title: str, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setFrameShape(QFrame.Shape.StyledPanel)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setMinimumHeight(96)
        self._default_style()

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 12, 16, 12)
        layout.setSpacing(6)

        self._title_label = QLabel(title)
        self._title_label.setStyleSheet("color: #94a3b8; font-size: 12px;")
        layout.addWidget(self._title_label)

        self._value_label = QLabel("--")
        font = QFont()
        font.setPointSize(20)
        font.setBold(True)
        self._value_label.setFont(font)
        self._value_label.setStyleSheet("color: #0f172a;")
        layout.addWidget(self._value_label)

        self._hint_label = QLabel("")
        self._hint_label.setStyleSheet("color: #475569; font-size: 11px;")
        layout.addWidget(self._hint_label)

    def _default_style(self) -> None:
        self.setStyleSheet(
            "StatusCard { background: white; border-radius: 10px;"
            " border: 1px solid #e2e8f0; }"
        )

    @Slot(str, str, str)
    def update_value(self, value: str, level: str = "ok", hint: str = "") -> None:
        self._value_label.setText(value)
        color = level_color(level)
        self._value_label.setStyleSheet(f"color: {color.name()};")
        self._hint_label.setText(hint)
        border = color.name()
        self.setStyleSheet(
            f"StatusCard {{ background: white; border-radius: 10px;"
            f" border: 1px solid {border}; }}"
        )


class LogHighlighter(QSyntaxHighlighter):
    def __init__(self, document) -> None:
        super().__init__(document)
        self._rules: list[tuple[str, QColor]] = [
            ("✅", COLOR_OK),
            ("❌", COLOR_ERROR),
            ("⚠️", COLOR_WARN),
            ("📊", COLOR_INFO),
            ("📧", COLOR_INFO),
            ("🔌", COLOR_NEUTRAL),
            ("🔒", COLOR_NEUTRAL),
            ("🔓", COLOR_NEUTRAL),
            ("⛔", COLOR_ERROR),
            ("🦞", COLOR_INFO),
            ("🤖", COLOR_INFO),
            ("🔀", COLOR_INFO),
            ("📭", COLOR_NEUTRAL),
            ("📬", COLOR_INFO),
        ]

    def highlightBlock(self, text: str) -> None:  # noqa: D401 - Qt override
        for marker, color in self._rules:
            idx = text.find(marker)
            while idx >= 0:
                fmt = QTextCharFormat()
                fmt.setForeground(color)
                self.setFormat(idx, len(marker), fmt)
                idx = text.find(marker, idx + len(marker))


class LogViewer(QPlainTextEdit):
    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setReadOnly(True)
        font = QFont("Consolas, Menlo, Courier New")
        font.setStyleHint(QFont.StyleHint.Monospace)
        font.setPointSize(10)
        self.setFont(font)
        self.setMaximumBlockCount(2000)
        self._highlighter = LogHighlighter(self.document())

    def append_lines(self, lines: Iterable[str]) -> None:
        cursor = self.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        for line in lines:
            cursor.insertText(line.rstrip("\n") + "\n")
        sb = self.verticalScrollBar()
        sb.setValue(sb.maximum())


class LogTailer(QThread):
    """Background worker that tails the rotated log files."""

    new_lines = Signal(list)

    def __init__(self, log_path: Path, poll_interval: float = 1.0) -> None:
        super().__init__()
        self._log_path = Path(log_path)
        self._poll_interval = poll_interval
        self._stop = False

    def stop(self) -> None:
        self._stop = True

    def _active_file(self) -> Optional[Path]:
        if self._log_path.exists():
            return self._log_path
        for suffix in (".1", ".2", ".3"):
            candidate = self._log_path.with_suffix(self._log_path.suffix + suffix)
            if candidate.exists():
                return candidate
        return None

    def run(self) -> None:  # noqa: D401 - QThread override
        active = self._active_file()
        if active is None:
            self.new_lines.emit(["⚠️ 日志文件尚未生成，等待守护进程启动..."])
            return
        with active.open("r", encoding="utf-8", errors="replace") as handle:
            handle.seek(0, os.SEEK_END)
            current = active
            while not self._stop:
                if current != self._active_file():
                    current = self._active_file()
                    if current is None:
                        time.sleep(self._poll_interval)
                        continue
                    with current.open("r", encoding="utf-8", errors="replace") as new_handle:
                        new_handle.seek(0, os.SEEK_END)
                        handle = new_handle
                line = handle.readline()
                if line:
                    self.new_lines.emit([line])
                else:
                    time.sleep(self._poll_interval)


class MailboxesTable(QTableWidget):
    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(0, 4, parent)
        self.setHorizontalHeaderLabels(["源邮箱", "负责人", "渠道", "前缀"])
        self.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.verticalHeader().setVisible(False)
        self.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)

    def populate(self, entries: list[dict]) -> None:
        self.setRowCount(len(entries))
        for row, entry in enumerate(entries):
            self.setItem(row, 0, QTableWidgetItem(str(entry.get("source_email", ""))))
            self.setItem(row, 1, QTableWidgetItem(str(entry.get("owner", ""))))
            self.setItem(row, 2, QTableWidgetItem(str(entry.get("notify_channel", ""))))
            self.setItem(row, 3, QTableWidgetItem(str(entry.get("prefix", ""))))


class ReportsTable(QTableWidget):
    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(0, 4, parent)
        self.setHorizontalHeaderLabels(["文件名", "类型", "修改时间", "大小"])
        self.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.verticalHeader().setVisible(False)
        self.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.doubleClicked.connect(self._open_file)

    def _open_file(self, index) -> None:
        path = self.item(index.row(), 0).data(Qt.ItemDataRole.UserRole)
        if path:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))

    def populate(self, files: list[tuple[Path, str, float, int]]) -> None:
        self.setRowCount(len(files))
        for row, (path, kind, mtime, size) in enumerate(files):
            name_item = QTableWidgetItem(path.name)
            name_item.setData(Qt.ItemDataRole.UserRole, str(path))
            self.setItem(row, 0, name_item)
            self.setItem(row, 1, QTableWidgetItem(kind))
            self.setItem(row, 2, QTableWidgetItem(time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(mtime))))
            self.setItem(row, 3, QTableWidgetItem(_human_size(size)))


def _human_size(num_bytes: int) -> str:
    units = ["B", "KB", "MB", "GB"]
    size = float(num_bytes)
    for unit in units:
        if size < 1024.0:
            return f"{size:.1f} {unit}"
        size /= 1024.0
    return f"{size:.1f} TB"


class DaemonControls(QFrame):
    start_clicked = Signal()
    stop_clicked = Signal()
    restart_clicked = Signal()
    add_mailbox_clicked = Signal()

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setFrameShape(QFrame.Shape.StyledPanel)
        self.setStyleSheet(
            "DaemonControls { background: white; border-radius: 10px;"
            " border: 1px solid #e2e8f0; }"
        )

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 12, 16, 12)
        layout.setSpacing(8)

        title = QLabel("守护进程控制")
        title.setStyleSheet("color: #0f172a; font-weight: bold; font-size: 13px;")
        layout.addWidget(title)

        buttons = QGridLayout()
        buttons.setContentsMargins(0, 0, 0, 0)
        buttons.setSpacing(8)

        self._start_btn = QPushButton("▶ 启动")
        self._stop_btn = QPushButton("⏹ 停止")
        self._restart_btn = QPushButton("🔄 重启")
        self._add_mailbox_btn = QPushButton("➕ 新增监督邮箱")

        self._start_btn.clicked.connect(self.start_clicked)
        self._stop_btn.clicked.connect(self.stop_clicked)
        self._restart_btn.clicked.connect(self.restart_clicked)
        self._add_mailbox_btn.clicked.connect(self.add_mailbox_clicked)

        for btn in (
            self._start_btn,
            self._stop_btn,
            self._restart_btn,
            self._add_mailbox_btn,
        ):
            btn.setMinimumHeight(36)

        buttons.addWidget(self._start_btn, 0, 0)
        buttons.addWidget(self._stop_btn, 0, 1)
        buttons.addWidget(self._restart_btn, 1, 0)
        buttons.addWidget(self._add_mailbox_btn, 1, 1)
        layout.addLayout(buttons)

    def set_running(self, running: bool) -> None:
        self._start_btn.setEnabled(not running)
        self._stop_btn.setEnabled(running)
        self._restart_btn.setEnabled(running)