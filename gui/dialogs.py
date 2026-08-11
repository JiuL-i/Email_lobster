"""Dialog windows: startup prompt, mailbox form, first-run wizard."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont, QIcon
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QStackedWidget,
    QVBoxLayout,
    QWizard,
    QWizardPage,
)

import mailbox_admin

CHANNEL_LABELS = {
    "group": "群聊（group）",
    "private": "私聊（private）",
    "both": "群+私（both）",
}


def _styled_label(text: str, *, title: bool = False, subtitle: bool = False) -> QLabel:
    label = QLabel(text)
    if title:
        label.setObjectName("DialogTitle")
    elif subtitle:
        label.setObjectName("DialogSubtitle")
    font = QFont()
    font.setPointSize(14 if title else 11)
    font.setBold(title)
    label.setFont(font)
    return label


class StartupAddMailboxPrompt(QDialog):
    """Modal dialog shown every launcher start with two big actions."""

    PROCEED = 0
    ADD_MAILBOX = 1

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Mail Lobster — 新增监督邮箱？")
        self.setModal(True)
        self.setMinimumWidth(440)
        self.choice = self.PROCEED

        layout = QVBoxLayout(self)
        layout.setContentsMargins(28, 24, 28, 24)
        layout.setSpacing(14)

        title = _styled_label("是否新增监督邮箱？", title=True)
        layout.addWidget(title)

        subtitle = _styled_label(
            "每次启动都会询问，避免新增的邮箱被遗漏。也可以现在跳过，进入监控面板后随时新增。",
            subtitle=True,
        )
        subtitle.setWordWrap(True)
        layout.addWidget(subtitle)

        add_btn = QPushButton("➕  新增监督邮箱")
        add_btn.setObjectName("PrimaryAction")
        add_btn.setMinimumHeight(48)
        add_btn.clicked.connect(self._choose_add)
        layout.addWidget(add_btn)

        skip_btn = QPushButton("跳过，直接进入监控面板")
        skip_btn.setMinimumHeight(48)
        skip_btn.clicked.connect(self._choose_proceed)
        layout.addWidget(skip_btn)

        view_btn = QPushButton("查看现有监督邮箱")
        view_btn.setMinimumHeight(34)
        view_btn.clicked.connect(self._view_existing)
        layout.addWidget(view_btn)

    def _choose_add(self) -> None:
        self.choice = self.ADD_MAILBOX
        self.accept()

    def _choose_proceed(self) -> None:
        self.choice = self.PROCEED
        self.accept()

    def _view_existing(self) -> None:
        try:
            entries = mailbox_admin.list_mailboxes(self._load_config())
        except Exception as exc:  # noqa: BLE001 - surface any error to user
            QMessageBox.warning(self, "读取失败", str(exc))
            return
        dialog = QDialog(self)
        dialog.setWindowTitle("现有监督邮箱")
        dialog.resize(420, 320)
        layout = QVBoxLayout(dialog)
        listing = QListWidget(dialog)
        if not entries:
            listing.addItem("(暂无)")
        for entry in entries:
            listing.addItem(
                QListWidgetItem(
                    f"{entry.get('source_email','')} → {entry.get('owner','')} "
                    f"[{entry.get('notify_channel','')}]"
                )
            )
        layout.addWidget(listing)
        dialog.exec()

    @staticmethod
    def _load_config() -> dict:
        path = Path("config_imap_forward.json").resolve()
        if path.exists():
            return mailbox_admin.load_config(path)
        return {"mailbox_mapping": {"mailboxes": []}}


class MailboxFormDialog(QDialog):
    """Form to add a new supervised mailbox."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("新增监督邮箱")
        self.setModal(True)
        self.setMinimumWidth(420)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(12)

        layout.addWidget(_styled_label("新增监督邮箱", title=True))

        form = QFormLayout()
        form.setSpacing(8)

        self._source_input = QLineEdit()
        self._source_input.setPlaceholderText("store_001@outlook.com")
        form.addRow("源邮箱 *", self._source_input)

        self._owner_input = QLineEdit()
        self._owner_input.setPlaceholderText("Store_001_AMZ")
        form.addRow("负责人 *", self._owner_input)

        self._channel_input = QComboBox()
        self._channel_input.addItems(list(mailbox_admin.VALID_NOTIFY_CHANNELS))
        form.addRow("推送渠道", self._channel_input)

        self._prefix_input = QLineEdit()
        self._prefix_input.setPlaceholderText("(可选) 例如 [Store_001]")
        form.addRow("主题前缀", self._prefix_input)

        layout.addLayout(form)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self._submit)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _submit(self) -> None:
        entry = {
            "source_email": self._source_input.text().strip(),
            "owner": self._owner_input.text().strip(),
            "notify_channel": self._channel_input.currentText(),
            "prefix": self._prefix_input.text().strip(),
        }
        path = Path("config_imap_forward.json").resolve()
        if path.exists():
            config = mailbox_admin.load_config(path)
        else:
            config = {"mailbox_mapping": {"mailboxes": []}}
        try:
            mailbox_admin.add_mailbox(config, entry)
            mailbox_admin.save_config(path, config)
        except ValueError as exc:
            QMessageBox.warning(self, "保存失败", str(exc))
            return
        QMessageBox.information(self, "成功", f"已新增监督邮箱：{entry['source_email']}")
        self.accept()


def _make_wizard_page(title: str, description: str) -> QWizardPage:
    page = QWizardPage()
    page.setTitle(title)
    page.setSubTitle(description)
    layout = QVBoxLayout(page)
    layout.setContentsMargins(20, 20, 20, 20)
    return page, layout


class FirstRunWizard(QWizard):
    """Guided setup wizard for first-run users.

    Mirrors the field names of setup_wizard.py so that existing
    knowledge transfers directly. The wizard writes a minimal config
    that is sufficient for the daemon to start; users can refine it
    later via the dashboard.
    """

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Mail Lobster — 首次运行向导")
        self.setWizardStyle(QWizard.WizardStyle.ModernStyle)
        self.setMinimumSize(560, 460)

        self.addPage(self._build_imap_page())
        self.addPage(self._build_mailbox_page())
        self.addPage(self._build_ai_page())
        self.addPage(self._build_feishu_page())

    # ---- pages ----
    def _build_imap_page(self):
        page, layout = _make_wizard_page(
            "汇总邮箱", "填写 IMAP 信息用于连接汇总邮箱。"
        )
        form = QFormLayout()
        form.setSpacing(10)
        self._imap_host = QComboBox()
        self._imap_host.setEditable(True)
        self._imap_host.addItems(["imap.qq.com", "imap.139.com", "imap.163.com", "imap.gmail.com"])
        form.addRow("IMAP 服务器", self._imap_host)
        self._imap_port = QSpinBox()
        self._imap_port.setRange(1, 65535)
        self._imap_port.setValue(993)
        form.addRow("端口", self._imap_port)
        self._imap_email = QLineEdit()
        self._imap_email.setMinimumWidth(280)
        form.addRow("邮箱账号 *", self._imap_email)
        self._imap_password = QLineEdit()
        self._imap_password.setEchoMode(QLineEdit.EchoMode.Password)
        self._imap_password.setMinimumWidth(280)
        form.addRow("授权码 *", self._imap_password)
        layout.addLayout(form)
        return page

    def _build_mailbox_page(self):
        page, layout = _make_wizard_page(
            "监督邮箱", "添加需要被监控的业务邮箱。可在面板里继续新增。"
        )
        form = QFormLayout()
        form.setSpacing(10)
        self._mb_source = QLineEdit()
        self._mb_source.setMinimumWidth(280)
        form.addRow("源邮箱", self._mb_source)
        self._mb_owner = QLineEdit()
        self._mb_owner.setMinimumWidth(280)
        form.addRow("负责人", self._mb_owner)
        self._mb_channel = QComboBox()
        self._mb_channel.addItems(list(mailbox_admin.VALID_NOTIFY_CHANNELS))
        form.addRow("推送渠道", self._mb_channel)
        layout.addLayout(form)
        return page

    def _build_ai_page(self):
        page, layout = _make_wizard_page(
            "AI 分析", "启用后可对邮件进行更精细的级别与平台判定。"
        )
        self._ai_enabled = QCheckBox("启用 AI 分析")
        layout.addWidget(self._ai_enabled)
        form = QFormLayout()
        form.setSpacing(10)
        self._ai_provider = QComboBox()
        self._ai_provider.addItems(["deepseek", "dashscope", "moonshot"])
        form.addRow("服务", self._ai_provider)
        self._ai_key = QLineEdit()
        self._ai_key.setEchoMode(QLineEdit.EchoMode.Password)
        self._ai_key.setMinimumWidth(280)
        form.addRow("API Key", self._ai_key)
        layout.addLayout(form)
        return page

    def _build_feishu_page(self):
        page, layout = _make_wizard_page(
            "飞书通知", "填写飞书群机器人 Webhook，私聊推送可后续在面板里开启。"
        )
        self._feishu_enabled = QCheckBox("启用飞书群机器人")
        layout.addWidget(self._feishu_enabled)
        form = QFormLayout()
        form.setSpacing(10)
        self._feishu_webhook = QLineEdit()
        self._feishu_webhook.setMinimumWidth(320)
        form.addRow("Webhook URL", self._feishu_webhook)
        layout.addLayout(form)
        return page

    # ---- final write ----
    def accept(self) -> None:
        config = {
            "auth_type": "imap_forward",
            "imap": {
                "host": self._imap_host.currentText().strip(),
                "port": int(self._imap_port.value()),
                "email": self._imap_email.text().strip(),
                "password": self._imap_password.text(),
            },
            "mailbox_mapping": {
                "mailboxes": [],
                "unknown_prefix_action": "default_owner",
                "default_channel": "user",
            },
            "ai": {
                "enabled": self._ai_enabled.isChecked(),
                "provider": self._ai_provider.currentText(),
                "api_key": self._ai_key.text(),
            },
            "feishu_bot": {
                "enabled": self._feishu_enabled.isChecked(),
                "webhook_url": self._feishu_webhook.text().strip(),
                "secret": "",
                "notify_levels": [0, 1, 2],
            },
            "feishu_app": {"enabled": False, "owner_mapping": {}, "notify_levels": [0, 1, 2]},
            "daemon": {"enabled": True, "check_interval_minutes": 5},
            "schedule": {"summaries": ["07:30", "11:30", "17:00", "21:00"]},
            "reports": {"cycle_dir": "reports/cycles", "summary_dir": "reports/summaries"},
            "settings": {"only_unread": True, "mark_as_read": True, "days_back": 1, "max_emails": 200, "default_owner": "未分配"},
        }
        mb_source = self._mb_source.text().strip()
        if mb_source:
            config["mailbox_mapping"]["mailboxes"].append(
                {
                    "source_email": mb_source,
                    "owner": self._mb_owner.text().strip() or "未分配",
                    "notify_channel": self._mb_channel.currentText(),
                }
            )
        path = Path("config_imap_forward.json").resolve()
        mailbox_admin.save_config(path, config)
        super().accept()


def show_info(parent, title: str, text: str) -> None:
    QMessageBox.information(parent, title, text)


def show_error(parent, title: str, text: str) -> None:
    QMessageBox.critical(parent, title, text)