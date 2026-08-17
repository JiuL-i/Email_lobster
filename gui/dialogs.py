"""Dialog windows: startup prompt, mailbox form, first-run wizard."""

from __future__ import annotations

from pathlib import Path


from PySide6.QtCore import Qt
from PySide6.QtGui import QFont, QCloseEvent, QKeyEvent
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
    QMessageBox,
    QPushButton,
    QSpinBox,
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
    """Modal dialog shown every launcher start with two big actions.

    Choice semantics:
      - ADD_MAILBOX: open the mailbox form, then enter dashboard
      - PROCEED:     skip and enter the dashboard
      - QUIT:        user closed the window (X) — quit immediately
    """

    PROCEED = 0
    ADD_MAILBOX = 1
    QUIT = 2

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
            "每次启动都会询问，避免新增的邮箱被遗漏。"
            "如点「跳过」会进入监控面板；直接关闭窗口会退出 Mail Lobster。",
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

    def reject(self) -> None:
        """User closed the window with the X button — treat as quit."""
        self.choice = self.QUIT
        super().reject()

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
        # Use the resolver so we find the config no matter where it was
        # dropped (EXE sibling, _internal/ bundle, cwd, source tree).
        path = mailbox_admin.resolve_config_path()
        if path.exists():
            return mailbox_admin.load_config(path)
        return {"mailbox_mapping": {"mailboxes": []}}


class MailboxFormDialog(QDialog):
    """Form to add a new supervised mailbox.

    Captures `feishu_contact` so the dashboard can aggregate it into
    `feishu_app.owner_mapping` for private-message routing.
    """

    def __init__(self, parent=None, config_path: str | Path | None = None) -> None:
        super().__init__(parent)
        # Absolute path; if not supplied, use the resolver so we find the
        # config no matter where it was dropped (EXE sibling, _internal/
        # bundle, cwd, source tree).
        self._config_path = (
            Path(config_path).resolve() if config_path
            else mailbox_admin.resolve_config_path()
        )
        self.setWindowTitle("新增监督邮箱")
        self.setModal(True)
        self.setMinimumWidth(440)

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

        self._feishu_input = QLineEdit()
        self._feishu_input.setPlaceholderText("owner@feishu.cn 或 +8613800001111")
        self._feishu_input.setMinimumWidth(280)
        form.addRow("飞书邮箱 / 手机号", self._feishu_input)

        self._channel_input = QComboBox()
        self._channel_input.addItems(list(mailbox_admin.VALID_NOTIFY_CHANNELS))
        form.addRow("推送渠道", self._channel_input)

        self._prefix_input = QLineEdit()
        self._prefix_input.setPlaceholderText("(可选) 例如 [Store_001]")
        form.addRow("主题前缀", self._prefix_input)

        layout.addLayout(form)

        # Hint explaining what feishu_contact does
        hint = _styled_label(
            "📮 飞书邮箱 / 手机号：用于私聊推送。提交后系统会自动加入 "
            "feishu_app.owner_mapping，负责人即可收到飞书私聊通知。",
            subtitle=True,
        )
        hint.setWordWrap(True)
        layout.addWidget(hint)

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
            "feishu_contact": self._feishu_input.text().strip(),
            "notify_channel": self._channel_input.currentText(),
            "prefix": self._prefix_input.text().strip(),
        }
        path = self._config_path
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
        # Aggregate feishu_contact from ALL mailboxes into feishu_app.owner_mapping
        # so the dashboard's notify path picks it up immediately. This also
        # repairs any owner_mapping that fell out of sync after previous
        # incremental edits.
        owner = entry["owner"]
        contact = entry["feishu_contact"]
        if owner and contact:
            app_cfg = config.setdefault("feishu_app", {})
            mapping = app_cfg.setdefault("owner_mapping", {})
            if isinstance(mapping, dict):
                # First write the owner we just added.
                mapping[owner] = contact
                # Then sync every other mailbox that has a feishu_contact
                # but is missing from owner_mapping. We only fill missing
                # entries — never overwrite an existing mapping that the
                # user has explicitly set elsewhere.
                for mb in config.get("mailbox_mapping", {}).get("mailboxes", []):
                    mb_owner = (mb.get("owner") or "").strip()
                    mb_contact = (mb.get("feishu_contact") or "").strip()
                    if mb_owner and mb_contact and mb_owner not in mapping:
                        mapping[mb_owner] = mb_contact
                mailbox_admin.save_config(path, config)
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
    knowledge transfers directly. The wizard writes a complete config
    covering all six required sections:

      1. IMAP（汇总邮箱连接）
      2. 监督邮箱（可添加多个）
      3. AI 分析
      4. 飞书群聊机器人（Webhook）
      5. 飞书企业自建应用（App_id / App_secret + owner_mapping）
      6. 双平台分流配对（sister_owner_mapping）

    The wizard detects an existing config and pre-fills every page so the
    user only edits what needs changing. The user must finish every step
    before the dashboard opens; pages may be revisited via the wizard's
    Back/Next buttons.

    Closing behaviour: X / ESC / Cancel button all go through a
    confirmation prompt. If the user confirms, the wizard exits as
    Rejected and the configuration is *not* saved — they have to relaunch
    the EXE to retry. Without this guard, a wizard that's hidden behind
    other windows (Windows foreground-lock race) can be silently killed
    by an ESC keypress the user wasn't even aware they sent, leaving the
    EXE with no UI and the user with no obvious next step.
    """

    def __init__(self, parent=None, prefill_path: str | Path | None = None) -> None:
        super().__init__(parent)
        # EARLY diag: log immediately on construction so we know the EXE
        # actually entered the wizard path even if the rest of the UI is
        # never reached.
        try:
            _wlog(self, f"FirstRunWizard.__init__ start prefill_path={prefill_path}")
        except Exception as exc:
            # Failure here is itself diagnostic — log to a hard-coded path.
            try:
                import os, tempfile
                with open(os.path.join(tempfile.gettempdir(), "wizard_diag.log"), "a", encoding="utf-8") as f:
                    f.write(f"[{__import__('datetime').datetime.now().isoformat(timespec='seconds')}] _wlog failed in __init__: {exc}\n")
            except Exception:
                pass
        self.setWindowTitle("Mail Lobster — 首次运行向导")
        self.setWizardStyle(QWizard.WizardStyle.ModernStyle)
        self.setMinimumSize(680, 560)
        # Stay on top of other windows until the user actively engages,
        # so the wizard never sits invisibly behind the email client.
        self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, True)
        # Disable ESC → reject() (the default mapping swallows keystrokes
        # even when the wizard isn't visually focused).
        self.setShortcutEnabled(Qt.Key.Key_Escape, False)
        for btn_id in (
            QWizard.WizardButton.CancelButton,
        ):
            try:
                btn = self.button(btn_id)
                if btn is not None:
                    btn.setText("取消（需确认）")
            except Exception:
                pass
        try:
            _wlog(self, "FirstRunWizard.__init__ complete")
        except Exception:
            pass

        # Pending entries collected on pages 2/7; flushed in accept().
        # Owner → feishu_contact is now embedded in each mailbox entry on
        # page 2, so there is no separate owner_mapping list.
        self._pending_mailboxes: list[dict] = []
        self._pending_sister_mapping: list[dict] = []  # [{"business":..., "owners":{"AMZ":..., "WMT":...}}]

        # Pre-fill from existing config (if any). This makes the wizard
        # idempotent — re-running it on a populated config lets the user
        # edit / add entries instead of starting from scratch.
        self._prefill: dict = {}
        self._prefill_path: Path | None = None
        if prefill_path is None:
            # Use the resolver so the wizard finds it under EXE sibling,
            # PyInstaller _internal/, cwd, or the source tree alike.
            candidate = mailbox_admin.resolve_config_path()
            if candidate.exists():
                prefill_path = candidate
        if prefill_path is not None and Path(prefill_path).exists():
            try:
                self._prefill = mailbox_admin.load_config(Path(prefill_path))
                self._prefill_path = Path(prefill_path)
            except Exception:
                self._prefill = {}

        self.addPage(self._build_imap_page())
        self.addPage(self._build_mailbox_page())
        self.addPage(self._build_ai_page())
        self.addPage(self._build_feishu_bot_page())
        self.addPage(self._build_feishu_app_page())
        self.addPage(self._build_sister_page())
        self.addPage(self._build_other_platform_page())

        # Apply prefill ONCE after all pages are built. We can't rely on
        # overriding QWizardPage.initializePage() because that virtual is
        # called via C++ and Python attribute assignment doesn't intercept it.
        self._apply_prefill()

        # Keep the owner dropdowns on pages 6 & 7 in sync with whatever the
        # user has typed into page 2's mailbox form. Without this hook, an
        # owner added on page 2 wouldn't appear in the sister / other-platform
        # combos until the wizard is reopened.
        self.currentIdChanged.connect(self._on_page_changed)

    def _apply_prefill(self) -> None:
        """Populate every wizard field from `self._prefill` if present."""
        if not self._prefill:
            return

        # Page 1: IMAP
        imap = self._prefill.get("imap", {}) or {}
        host = imap.get("host", "")
        if host:
            self._imap_host.setEditText(host)
        self._imap_port.setValue(int(imap.get("port", 993) or 993))
        self._imap_email.setText(imap.get("email", "") or "")
        self._imap_password.setText(imap.get("password", "") or "")

        # Page 2: supervised mailboxes — backfill feishu_contact from legacy
        # owner_mapping if not already present on each entry.
        owner_map = (self._prefill.get("feishu_app", {}) or {}).get("owner_mapping", {}) or {}
        for entry in self._prefill.get("mailbox_mapping", {}).get("mailboxes", []) or []:
            if "feishu_contact" not in entry:
                entry["feishu_contact"] = owner_map.get(entry.get("owner", ""), "")
            self._pending_mailboxes.append(dict(entry))
        if hasattr(self, "_mb_list"):
            for entry in self._pending_mailboxes:
                feishu = entry.get("feishu_contact", "")
                tail = f" → 飞书: {feishu}" if feishu else ""
                self._mb_list.addItem(
                    f"{entry['source_email']}  →  {entry['owner']}  "
                    f"[{entry['notify_channel']}]{tail}"
                )

        # Page 3: AI
        ai = self._prefill.get("ai", {}) or {}
        self._ai_enabled.setChecked(bool(ai.get("enabled", True)))
        provider = ai.get("provider", "deepseek")
        if provider in ("deepseek", "dashscope", "moonshot"):
            self._ai_provider.setCurrentText(provider)
        self._ai_key.setText(ai.get("api_key", "") or "")

        # Page 4: feishu bot
        bot = self._prefill.get("feishu_bot", {}) or {}
        self._feishu_bot_enabled.setChecked(bool(bot.get("enabled", True)))
        self._feishu_webhook.setText(bot.get("webhook_url", "") or "")

        # Page 5: feishu app (id/secret only; owner list is read-only summary)
        app = self._prefill.get("feishu_app", {}) or {}
        # Default enabled=True if owner_mapping has entries (the user clearly
        # wants it on). Fall back to the stored value, then to False.
        prior_mapping = app.get("owner_mapping", {}) or {}
        default_enabled = (
            bool(app.get("enabled", False))
            or bool(prior_mapping)
        )
        self._feishu_app_enabled.setChecked(default_enabled)
        self._feishu_app_id.setText(app.get("app_id", "") or "")
        self._feishu_app_secret.setText(app.get("app_secret", "") or "")
        self._refresh_owner_mapping_list()

        # Page 6: sister mapping
        self._pending_sister_mapping.extend(self._prefill.get("sister_owner_mapping", []) or [])
        self._populate_owner_dropdowns()
        self._refresh_sister_list()

        # Page 7: other_platform router
        other = self._prefill.get("other_platform", {}) or {}
        # Page 6 used to define `owners` inline; the helper hoisted it. Re-derive
        # locally for page 7's combo so the page-7 prefill doesn't blow up with
        # NameError when there's no existing config.
        page7_owners = sorted({
            mb.get("owner", "").strip()
            for mb in self._pending_mailboxes
            if mb.get("owner", "").strip()
        })
        if hasattr(self, "_other_enabled"):
            self._other_enabled.setChecked(bool(other.get("enabled", False)))
            prior_owner = (other.get("owner", "") or "").strip()
            if hasattr(self, "_other_owner"):
                self._other_owner.clear()
                self._other_owner.addItem("")
                self._other_owner.addItems(page7_owners)
                if prior_owner and prior_owner not in page7_owners:
                    self._other_owner.addItem(prior_owner)
                if prior_owner:
                    self._other_owner.setCurrentText(prior_owner)
            # Prefill handler feishu contact. If other_platform.owner is set,
            # try to derive it from existing owner_mapping first; fall back
            # to whatever's stored alongside (rare).
            if hasattr(self, "_other_feishu"):
                handler_contact = ""
                handler_owner = prior_owner
                prior_mapping = (self._prefill.get("feishu_app", {}) or {}).get("owner_mapping", {}) or {}
                if handler_owner and handler_owner in prior_mapping:
                    handler_contact = prior_mapping[handler_owner]
                else:
                    # legacy: some configs stored feishu_contact on the
                    # other_platform block itself
                    handler_contact = other.get("feishu_contact", "") or ""
                if handler_contact:
                    self._other_feishu.setText(handler_contact)
            if hasattr(self, "_other_notify"):
                levels = other.get("notify_levels", [0, 1, 2]) or [0, 1, 2]
                self._other_notify.setText(",".join(str(int(x)) for x in levels))

    # ---- page 1: IMAP ----
    def _build_imap_page(self):
        page, layout = _make_wizard_page(
            "1/7 · 汇总邮箱",
            "填写 IMAP 信息用于连接汇总邮箱（接收所有业务邮箱转发邮件的邮箱）。",
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
        self._imap_email.setMinimumWidth(300)
        self._imap_email.setPlaceholderText("summary@139.com")
        form.addRow("邮箱账号 *", self._imap_email)
        self._imap_password = QLineEdit()
        self._imap_password.setEchoMode(QLineEdit.EchoMode.Password)
        self._imap_password.setMinimumWidth(300)
        self._imap_password.setPlaceholderText("IMAP 授权码（非登录密码）")
        form.addRow("授权码 *", self._imap_password)
        layout.addLayout(form)

        # Pre-fill from existing config on first show.
        # Prefill handled centrally in __init__ via _apply_prefill().
        return page

    # ---- page 2: supervised mailboxes (multi-entry) ----
    def _build_mailbox_page(self):
        page, layout = _make_wizard_page(
            "2/7 · 监督邮箱（可多个）",
            "添加需要被监控的业务邮箱。每条同时配置「飞书邮箱 / 手机号」用于私聊推送。\n"
            "填好后点「➕ 添加到列表」，可继续添加多个。完成后点下一步。",
        )
        # input row + add button
        input_row = QFormLayout()
        input_row.setSpacing(8)
        self._mb_source = QLineEdit()
        self._mb_source.setPlaceholderText("store_001@outlook.com")
        input_row.addRow("源邮箱", self._mb_source)
        self._mb_owner = QLineEdit()
        self._mb_owner.setPlaceholderText("Store_001_AMZ")
        input_row.addRow("负责人", self._mb_owner)
        self._mb_feishu = QLineEdit()
        self._mb_feishu.setPlaceholderText("owner@feishu.cn 或 +8613800001111")
        input_row.addRow("飞书邮箱 / 手机号", self._mb_feishu)
        self._mb_channel = QComboBox()
        self._mb_channel.addItems(list(mailbox_admin.VALID_NOTIFY_CHANNELS))
        input_row.addRow("推送渠道", self._mb_channel)
        layout.addLayout(input_row)

        add_btn = QPushButton("➕  添加到列表")
        add_btn.setMinimumHeight(36)
        add_btn.clicked.connect(self._on_add_mailbox)
        layout.addWidget(add_btn)

        # existing list
        layout.addWidget(_styled_label("已添加的监督邮箱：", subtitle=True))
        self._mb_list = QListWidget()
        self._mb_list.setMinimumHeight(160)
        layout.addWidget(self._mb_list, 1)

        remove_btn = QPushButton("🗑  删除选中")
        remove_btn.setMinimumHeight(32)
        remove_btn.clicked.connect(self._on_remove_mailbox)
        layout.addWidget(remove_btn)

        # Note: QWizardPage.initializePage is a C++ virtual; we cannot
        # override it from Python via attribute assignment. Prefill happens
        # in __init__ via _apply_prefill() instead.
        return page

    def _on_add_mailbox(self) -> None:
        source = self._mb_source.text().strip()
        owner = self._mb_owner.text().strip()
        feishu = self._mb_feishu.text().strip()
        if not source:
            QMessageBox.warning(self, "缺少源邮箱", "请先填写「源邮箱」。")
            return
        if not owner:
            QMessageBox.warning(self, "缺少负责人", "请先填写「负责人」。")
            return
        if any(e["source_email"].lower() == source.lower() for e in self._pending_mailboxes):
            QMessageBox.warning(self, "重复邮箱", f"{source} 已在列表中。")
            return
        entry = {
            "source_email": source,
            "owner": owner,
            "feishu_contact": feishu,
            "notify_channel": self._mb_channel.currentText(),
        }
        self._pending_mailboxes.append(entry)
        tail = f" → 飞书: {feishu}" if feishu else ""
        self._mb_list.addItem(
            f"{source}  →  {owner}  [{entry['notify_channel']}]{tail}"
        )
        # clear inputs for next entry
        self._mb_source.clear()
        self._mb_owner.clear()
        self._mb_feishu.clear()
        self._mb_source.setFocus()
        # Refresh the read-only summary on page 5.
        if hasattr(self, "_refresh_owner_mapping_list"):
            self._refresh_owner_mapping_list()
        # New owner might need to appear in page 6 / page 7 dropdowns.
        self._populate_owner_dropdowns()

    def _on_remove_mailbox(self) -> None:
        row = self._mb_list.currentRow()
        if row < 0:
            return
        self._mb_list.takeItem(row)
        del self._pending_mailboxes[row]
        if hasattr(self, "_refresh_owner_mapping_list"):
            self._refresh_owner_mapping_list()
        # Owner list on page 6 / page 7 may need to drop an entry.
        self._populate_owner_dropdowns()

    # ---- page 3: AI ----
    def _build_ai_page(self):
        page, layout = _make_wizard_page(
            "3/7 · AI 分析",
            "启用后可对邮件进行更精细的级别与平台判定（DeepSeek / 通义千问 / Moonshot 任选其一）。",
        )
        self._ai_enabled = QCheckBox("启用 AI 分析")
        self._ai_enabled.setChecked(True)
        layout.addWidget(self._ai_enabled)
        form = QFormLayout()
        form.setSpacing(10)
        self._ai_provider = QComboBox()
        self._ai_provider.addItems(["deepseek", "dashscope", "moonshot"])
        form.addRow("服务", self._ai_provider)
        self._ai_key = QLineEdit()
        self._ai_key.setEchoMode(QLineEdit.EchoMode.Password)
        self._ai_key.setMinimumWidth(300)
        self._ai_key.setPlaceholderText("sk-...")
        form.addRow("API Key", self._ai_key)
        layout.addLayout(form)

        def _refresh():
            if not self._prefill:
                return
            ai = self._prefill.get("ai", {}) or {}
            self._ai_enabled.setChecked(bool(ai.get("enabled", True)))
            provider = ai.get("provider", "deepseek")
            if provider in ("deepseek", "dashscope", "moonshot"):
                self._ai_provider.setCurrentText(provider)
            self._ai_key.setText(ai.get("api_key", "") or "")
        # Prefill handled centrally in __init__ via _apply_prefill().
        return page
    # ---- page 4: feishu bot (group webhook) ----
    def _build_feishu_bot_page(self):
        page, layout = _make_wizard_page(
            "4/7 · 飞书群聊机器人",
            "创建一个群机器人，把 Webhook 地址粘贴进来即可。0/1/2 级邮件会自动推送到该群。",
        )
        self._feishu_bot_enabled = QCheckBox("启用飞书群机器人")
        self._feishu_bot_enabled.setChecked(True)
        layout.addWidget(self._feishu_bot_enabled)
        form = QFormLayout()
        form.setSpacing(10)
        self._feishu_webhook = QLineEdit()
        self._feishu_webhook.setMinimumWidth(360)
        self._feishu_webhook.setPlaceholderText("https://open.feishu.cn/open-apis/bot/v2/hook/...")
        form.addRow("Webhook URL", self._feishu_webhook)
        layout.addLayout(form)

        def _refresh():
            if not self._prefill:
                return
            bot = self._prefill.get("feishu_bot", {}) or {}
            self._feishu_bot_enabled.setChecked(bool(bot.get("enabled", True)))
            self._feishu_webhook.setText(bot.get("webhook_url", "") or "")
        # Prefill handled centrally in __init__ via _apply_prefill().
        return page

    # ---- page 5: feishu app (private message) ----
    def _build_feishu_app_page(self):
        page, layout = _make_wizard_page(
            "5/7 · 飞书企业自建应用",
            "如需私聊推送负责人，请填写飞书自建应用凭据，并配置 owner → 飞书邮箱 映射。\n"
            "不确定可暂不开启，进入监控面板后随时在「设置」里补充。",
        )
        self._feishu_app_enabled = QCheckBox("启用飞书自建应用（私聊推送）")
        layout.addWidget(self._feishu_app_enabled)
        cred_form = QFormLayout()
        cred_form.setSpacing(10)
        self._feishu_app_id = QLineEdit()
        self._feishu_app_id.setMinimumWidth(360)
        self._feishu_app_id.setPlaceholderText("cli_xxxxxxxxxxxx")
        cred_form.addRow("App ID", self._feishu_app_id)
        self._feishu_app_secret = QLineEdit()
        self._feishu_app_secret.setEchoMode(QLineEdit.EchoMode.Password)
        self._feishu_app_secret.setMinimumWidth(360)
        self._feishu_app_secret.setPlaceholderText("应用密钥")
        cred_form.addRow("App Secret", self._feishu_app_secret)
        layout.addLayout(cred_form)

        # Read-only summary of owner_mapping derived from page 2 mailboxes.
        layout.addWidget(_styled_label(
            "私聊推送目标（自动从第 2 步「监督邮箱」的飞书邮箱 / 手机号字段汇总）：",
            subtitle=True,
        ))
        self._om_list = QListWidget()
        self._om_list.setMinimumHeight(160)
        layout.addWidget(self._om_list, 1)

        def _refresh():
            # Prefill app_id / app_secret (only once)
            if not self._feishu_app_id.text() and self._prefill:
                app = self._prefill.get("feishu_app", {}) or {}
                self._feishu_app_enabled.setChecked(bool(app.get("enabled", False)))
                self._feishu_app_id.setText(app.get("app_id", "") or "")
                self._feishu_app_secret.setText(app.get("app_secret", "") or "")
            self._refresh_owner_mapping_list()
        # Prefill handled centrally in __init__ via _apply_prefill().
        return page

    def _refresh_owner_mapping_list(self) -> None:
        """Rebuild the read-only summary from _pending_mailboxes."""
        self._om_list.clear()
        # First row: a header explaining where the data comes from.
        seen: set[str] = set()
        for entry in self._pending_mailboxes:
            owner = entry.get("owner", "").strip()
            feishu = entry.get("feishu_contact", "").strip()
            if not owner or not feishu or owner in seen:
                continue
            seen.add(owner)
            self._om_list.addItem(f"{owner}  →  {feishu}")
        if not seen:
            self._om_list.addItem("(尚未配置 — 请回到第 2 步为各监督邮箱填写「飞书邮箱 / 手机号」)")

    # ---- page 6: sister / cross-platform pairing ----
    def _build_sister_page(self):
        page, layout = _make_wizard_page(
            "6/7 · 双平台分流配对",
            "如果同一企业同时运营 Amazon 与 Walmart，可在此配对两个 owner。\n"
            "同一封邮件来自任一邮箱时，系统会按发件人平台自动转给对应负责人。\n"
            "没有跨平台业务可跳过此步。",
        )

        biz_form = QFormLayout()
        biz_form.setSpacing(8)
        self._sm_business = QLineEdit()
        self._sm_business.setPlaceholderText("业务/企业名，如睿宝")
        biz_form.addRow("业务名称", self._sm_business)
        layout.addLayout(biz_form)

        owner_form = QFormLayout()
        owner_form.setSpacing(8)
        self._sm_amz = QComboBox()
        self._sm_amz.setMinimumWidth(240)
        owner_form.addRow("Amazon 端负责人", self._sm_amz)
        self._sm_wms = QComboBox()
        self._sm_wms.setMinimumWidth(240)
        owner_form.addRow("Walmart 端负责人", self._sm_wms)
        layout.addLayout(owner_form)

        sm_btn_row = QHBoxLayout()
        add_sm_btn = QPushButton("➕  添加配对")
        add_sm_btn.setMinimumHeight(32)
        add_sm_btn.clicked.connect(self._on_add_sister)
        sm_btn_row.addWidget(add_sm_btn)
        remove_sm_btn = QPushButton("🗑  删除选中")
        remove_sm_btn.setMinimumHeight(32)
        remove_sm_btn.clicked.connect(self._on_remove_sister)
        sm_btn_row.addWidget(remove_sm_btn)
        sm_btn_row.addStretch(1)
        layout.addLayout(sm_btn_row)

        layout.addWidget(_styled_label("已添加的双平台配对：", subtitle=True))
        self._sm_list = QListWidget()
        self._sm_list.setMinimumHeight(140)
        layout.addWidget(self._sm_list, 1)

        # Populate owner dropdowns + seed list from existing config on first show.
        # Prefill handled centrally in __init__ via _apply_prefill().
        return page

    def _refresh_sister_list(self) -> None:
        self._sm_list.clear()
        for entry in self._pending_sister_mapping:
            biz = entry.get("business", "")
            owners = entry.get("owners", {})
            # Show whichever keys are present; main code standardizes to WMS.
            amz = owners.get("AMZ", "?")
            wms = owners.get("WMS", owners.get("WMT", "?"))
            self._sm_list.addItem(f"{biz}: AMZ → {amz}, WMS → {wms}")

    def _populate_owner_dropdowns(self) -> None:
        """Refresh the owner <select> widgets on pages 6 & 7 from the current
        ``_pending_mailboxes`` list.

        The sister-page combos (``_sm_amz``/``_sm_wms``) are read-only — they
        strictly mirror the mailbox owners, so removing a mailbox should drop
        its owner from the list. The other-platform combo on page 7 is
        editable, so a free-text handler the user already typed there should
        survive a round-trip through page 2.
        """
        owners = sorted({
            mb.get("owner", "").strip()
            for mb in self._pending_mailboxes
            if mb.get("owner", "").strip()
        })

        # Page 6: sister-owner combos (non-editable; strictly mirror mailboxes).
        for combo_name in ("_sm_amz", "_sm_wms"):
            combo = getattr(self, combo_name, None)
            if combo is None:
                continue
            combo.blockSignals(True)
            combo.clear()
            combo.addItem("")  # blank = "no selection"
            combo.addItems(owners)
            combo.blockSignals(False)

        # Page 7: other-platform handler combo (editable; keep any text the
        # user has already typed, even if it's not in the mailbox list).
        other_combo = getattr(self, "_other_owner", None)
        if other_combo is not None:
            current = other_combo.currentText()
            other_combo.blockSignals(True)
            other_combo.clear()
            other_combo.addItem("")
            other_combo.addItems(owners)
            if current and other_combo.findText(current) < 0:
                other_combo.addItem(current)
            if current:
                other_combo.setCurrentText(current)
            other_combo.blockSignals(False)

    def _on_page_changed(self, new_id: int) -> None:
        """Whenever the user lands on page 6 or 7, re-fill the owner
        dropdowns from whatever's in ``_pending_mailboxes`` so any new owner
        added on page 2 is selectable. Without this hook, going
        forward → back → forward would leave the combos stale."""
        page_index = self.pageIds().index(new_id) if new_id in self.pageIds() else -1
        if page_index in (5, 6):  # zero-based: page 6 = index 5, page 7 = 6
            self._populate_owner_dropdowns()

    def _on_add_sister(self) -> None:
        biz = self._sm_business.text().strip()
        amz = self._sm_amz.currentText().strip()
        wms = self._sm_wms.currentText().strip()
        if not biz:
            QMessageBox.warning(self, "缺少业务名称", "请先填写「业务名称」。")
            return
        if not amz or not wms:
            QMessageBox.warning(self, "缺少负责人", "请同时选择 Amazon 与 Walmart 端的负责人。")
            return
        if amz == wms:
            QMessageBox.warning(self, "无效配对", "两端负责人不能相同。")
            return
        if any(e.get("business") == biz for e in self._pending_sister_mapping):
            QMessageBox.warning(self, "重复业务", f"{biz} 已在配对列表中。")
            return
        self._pending_sister_mapping.append(
            {"business": biz, "owners": {"AMZ": amz, "WMS": wms}}
        )
        self._refresh_sister_list()
        self._sm_business.clear()

    def _on_remove_sister(self) -> None:
        row = self._sm_list.currentRow()
        if row < 0:
            return
        self._sm_list.takeItem(row)
        del self._pending_sister_mapping[row]

    # ---- page 7: other-platform router (OZON / Temu / Shopee / TikTok / ...) ----
    def _build_other_platform_page(self):
        page, layout = _make_wizard_page(
            "7/7 · 其他平台路由",
            "如果同时运营 Amazon / Walmart 之外的电商平台（如 OZON、Temu、Shopee、TikTok、"
            "eBay、Lazada 等），可在此指定一个统一 handler 接收这些平台的邮件。\n"
            "启用后，系统会把这些平台的邮件自动转给该负责人；物流/服务商邮件（如云途、"
            "DHL、顺丰、4PX）不会被误判，会保留给原始负责人。",
        )

        self._other_enabled = QCheckBox("启用其他平台统一路由（OZON / Temu / Shopee / TikTok 等）")
        layout.addWidget(self._other_enabled)

        form = QFormLayout()
        form.setSpacing(10)
        self._other_owner = QComboBox()
        self._other_owner.setEditable(True)
        self._other_owner.setMinimumWidth(280)
        self._other_owner.setPlaceholderText("如 中转仓 / Other_Default")
        form.addRow("统一负责人 *", self._other_owner)
        self._other_feishu = QLineEdit()
        self._other_feishu.setPlaceholderText("handler@feishu.cn 或 +8613800001111")
        self._other_feishu.setMinimumWidth(280)
        form.addRow("handler 飞书邮箱 / 手机号", self._other_feishu)
        self._other_notify = QLineEdit()
        self._other_notify.setText("0,1,2")
        self._other_notify.setPlaceholderText("逗号分隔，如 0,1,2")
        form.addRow("触发推送的级别", self._other_notify)
        layout.addLayout(form)

        # Refresh owner dropdown with mailbox owners from page 2
        self._other_owner.addItems(
            sorted({mb.get("owner", "").strip() for mb in self._pending_mailboxes}
                   - {""})
        )

        # Auto-fill feishu field when user picks an owner that already has a
        # feishu_contact defined in mailboxes, so handler config stays in sync.
        self._other_owner.currentTextChanged.connect(self._sync_other_feishu)

        hint = _styled_label(
            "📌 哪些邮件会被路由：发件人属于真正的电商竞品（如 OZON、Temu、Shopee、TikTok、"
            "eBay、Lazada 等）。物流/服务商/培训推广邮件不会被路由。",
            subtitle=True,
        )
        hint.setWordWrap(True)
        layout.addWidget(hint)

        channel_hint = _styled_label(
            "📮 私信渠道：handler 需要在此填入飞书邮箱 / 手机号才能收到私聊推送。"
            "如已选负责人在第 2 步配置过飞书联系方式，下方会自动同步；"
            "也可手动覆盖。",
            subtitle=True,
        )
        channel_hint.setWordWrap(True)
        layout.addWidget(channel_hint)
        layout.addStretch(1)
        return page

    def _sync_other_feishu(self, owner: str) -> None:
        """When the user selects an owner that already has a feishu_contact
        on page 2, mirror it into the handler feishu field (unless the user
        has manually edited it)."""
        if not hasattr(self, "_other_feishu"):
            return
        current = self._other_feishu.text().strip()
        if current:
            return  # User typed something; don't overwrite.
        for mb in self._pending_mailboxes:
            if mb.get("owner", "").strip() == owner.strip():
                contact = mb.get("feishu_contact", "").strip()
                if contact:
                    self._other_feishu.setText(contact)
                return

    # ---- final write ----
    def accept(self) -> None:
        # Aggregate owner_mapping from the per-mailbox feishu_contact field
        # (one owner may appear on multiple mailboxes; the first non-empty
        # contact wins).
        owner_mapping: dict[str, str] = {}
        for entry in self._pending_mailboxes:
            owner = entry.get("owner", "").strip()
            contact = entry.get("feishu_contact", "").strip()
            if owner and contact and owner not in owner_mapping:
                owner_mapping[owner] = contact

        # Merge in the other_platform handler's feishu_contact (if provided
        # on page 7). This lets the wizard configure handler contacts that
        # aren't tied to any specific supervised mailbox.
        other_owner = (self._other_owner.currentText().strip()
                        if hasattr(self, "_other_owner") else "")
        other_contact = (self._other_feishu.text().strip()
                         if hasattr(self, "_other_feishu") else "")
        if other_owner and other_contact:
            owner_mapping[other_owner] = other_contact

        # Preserve existing owner_mapping entries for owners that are NOT
        # covered by the current mailbox list (e.g. owner exists in config
        # but user removed all their mailboxes during this wizard run).
        if self._prefill:
            prior = (self._prefill.get("feishu_app", {}) or {}).get("owner_mapping", {}) or {}
            for owner, contact in prior.items():
                owner_mapping.setdefault(owner, contact)

        config = {
            "auth_type": "imap_forward",
            "imap": {
                "host": self._imap_host.currentText().strip(),
                "port": int(self._imap_port.value()),
                "email": self._imap_email.text().strip(),
                "password": self._imap_password.text(),
            },
            "mailbox_mapping": {
                "mailboxes": list(self._pending_mailboxes),
                "unknown_prefix_action": "default_owner",
                "default_channel": "private",
            },
            "ai": {
                "enabled": self._ai_enabled.isChecked(),
                "provider": self._ai_provider.currentText(),
                "api_key": self._ai_key.text().strip(),
            },
            "feishu_bot": {
                "enabled": self._feishu_bot_enabled.isChecked(),
                "webhook_url": self._feishu_webhook.text().strip(),
                "secret": "",
                "notify_levels": [0, 1, 2],
            },
            "feishu_app": {
                "enabled": self._feishu_app_enabled.isChecked(),
                "app_id": self._feishu_app_id.text().strip(),
                "app_secret": self._feishu_app_secret.text().strip(),
                "owner_mapping": owner_mapping,
                "notify_levels": [0, 1, 2],
            },
            "sister_owner_mapping": list(self._pending_sister_mapping),
            "other_platform": self._build_other_platform_config(),
            "daemon": {"enabled": True, "check_interval_minutes": 5},
            "schedule": {"summaries": ["07:30", "11:30", "17:00", "21:00"]},
            "reports": {"cycle_dir": "reports/cycles", "summary_dir": "reports/summaries"},
            "settings": {"only_unread": True, "mark_as_read": True, "days_back": 1, "max_emails": 200, "default_owner": "未分配"},
        }
        # Write to original location if pre-filling, otherwise default path
        # Use the resolver so the wizard writes to the canonical location.
        path = self._prefill_path or mailbox_admin.resolve_config_path()
        mailbox_admin.save_config(path, config)
        super().accept()

    # ---- forced confirmation on close ----
    def closeEvent(self, event: QCloseEvent) -> None:
        """X button: ask before letting the user abandon a half-filled config.

        The user might press X by reflex, or the system might fire it from
        a stray click. We refuse the close unless they confirm.
        """
        try:
            from pathlib import Path as _P
            _P(_P(__file__).parent / "wizard_diag.log" if "__file__" in dir() else "wizard_diag.log")
        except Exception:
            pass
        try:
            _wlog(self, f"closeEvent FIRED — asking confirmation (spontaneous={event.spontaneous()})")
        except Exception:
            pass
        event.ignore()  # unconditionally refuse first
        self._confirm_then(super().closeEvent, event, "关闭首次运行向导")

    def keyPressEvent(self, event: QKeyEvent) -> None:
        """Swallow ESC at the wizard level — Qt's default mapping routes
        ESC to reject(), which silently kills the wizard without any save.
        Without this override a stray ESC keypress (foreground-lock race,
        remote-desktop hiccup, etc.) is enough to make the EXE exit with
        no UI and no console output."""
        if event.key() == Qt.Key.Key_Escape:
            try:
                _wlog(self, "keyPressEvent ESC — asking confirmation")
            except Exception:
                pass
            # Promote to the same confirmation flow as X.
            self._confirm_then(super().keyPressEvent, event, "取消首次运行向导")
            return
        super().keyPressEvent(event)

    def reject(self) -> None:
        """Wrap the default Cancel button so it also asks for confirmation."""
        try:
            _wlog(self, "reject FIRED — asking confirmation")
        except Exception:
            pass
        self._confirm_then(super().reject, None, "取消首次运行向导")

    def accept(self) -> None:
        """Log user-clicked-accept so we can see if the wizard really did
        accept (vs. the user thinking it never appeared)."""
        try:
            _wlog(self, "accept FIRED — saving config")
        except Exception:
            pass
        super().accept()

    def _confirm_then(self, action, event, label: str) -> None:
        """Show a Yes/No dialog; only run ``action`` if the user confirms."""
        try:
            _wlog(self, f"_confirm_then label={label!r}")
        except Exception:
            pass
        try:
            r = QMessageBox.question(
                self,
                label,
                "当前向导尚未保存配置。\n"
                "确定要退出吗？\n\n"
                "• 是 — 退出（不会写入任何配置）\n"
                "• 否 — 留在向导继续填写",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
        except Exception as exc:
            try:
                _wlog(self, f"_confirm_then QMessageBox.question FAILED: {exc}")
            except Exception:
                pass
            r = QMessageBox.StandardButton.No
        try:
            _wlog(self, f"_confirm_then QMessageBox returned: {r}")
        except Exception:
            pass
        if r == QMessageBox.StandardButton.Yes:
            try:
                if event is not None:
                    event.accept()  # un-ignore so super's handler can close
                    action(event)
                else:
                    action()
            except TypeError:
                # Some QDialog APIs ignore the event arg; call without it.
                try:
                    action()
                except Exception:
                    pass


def _wlog(wizard, msg: str) -> None:
    """Append a timestamped line next to the wizard for post-mortem.

    Falls back to %TEMP%/wizard_diag.log when the wizard's path isn't writable.
    """
    import os, tempfile
    try:
        if wizard is not None and getattr(wizard, "_wizard_diag_path", None):
            path = wizard._wizard_diag_path
        else:
            path = os.path.join(tempfile.gettempdir(), "wizard_diag.log")
    except Exception:
        path = os.path.join(tempfile.gettempdir(), "wizard_diag.log")
    try:
        from datetime import datetime as _dt
        with open(path, "a", encoding="utf-8") as f:
            f.write(f"[{_dt.now().isoformat(timespec='seconds')}] {msg}\n")
    except OSError:
        pass

    def _build_other_platform_config(self) -> dict:
        """Build the other_platform section from page 7's widgets."""
        if not hasattr(self, "_other_enabled"):
            # Defensive fallback if page 7 wasn't built for some reason.
            return (self._prefill.get("other_platform", {}) or {})
        enabled = self._other_enabled.isChecked()
        owner = self._other_owner.currentText().strip()
        notify_text = self._other_notify.text().strip() or "0,1,2"
        try:
            notify_levels = [int(x.strip()) for x in notify_text.split(",") if x.strip()]
        except ValueError:
            notify_levels = [0, 1, 2]
        cfg = {
            "enabled": enabled,
            "owner": owner,
            "notify_levels": notify_levels,
        }
        # Preserve any user-set comment if present in prefill.
        if self._prefill:
            existing = (self._prefill.get("other_platform", {}) or {})
            if "_comment" in existing:
                cfg["_comment"] = existing["_comment"]
        return cfg


def show_info(parent, title: str, text: str) -> None:
    QMessageBox.information(parent, title, text)


def show_error(parent, title: str, text: str) -> None:
    QMessageBox.critical(parent, title, text)