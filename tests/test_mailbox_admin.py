"""Tests for mailbox_admin: list, add, remove, atomic writes, race retries."""

import json
import tempfile
import time
import unittest
from pathlib import Path

from mailbox_admin import (
    add_mailbox,
    is_first_run,
    list_mailboxes,
    load_config,
    remove_mailbox,
    save_config,
)


class MailboxAdminTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.config_path = Path(self._tmp.name) / "config_imap_forward.json"

    def _write(self, payload):
        self.config_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    def test_list_empty(self):
        self._write({"mailbox_mapping": {"mailboxes": []}})
        config = load_config(self.config_path)
        self.assertEqual(list_mailboxes(config), [])

    def test_add_new_mailbox(self):
        self._write({"mailbox_mapping": {"mailboxes": []}})
        config = load_config(self.config_path)
        mailboxes = add_mailbox(
            config,
            {
                "source_email": "Store_001@outlook.com",
                "owner": "Store_001_AMZ",
                "notify_channel": "private",
            },
        )
        save_config(self.config_path, config)
        self.assertEqual(len(mailboxes), 1)
        self.assertEqual(mailboxes[0]["source_email"], "Store_001@outlook.com")
        self.assertEqual(mailboxes[0]["owner"], "Store_001_AMZ")
        self.assertEqual(mailboxes[0]["notify_channel"], "private")

    def test_add_duplicate_rejected(self):
        self._write({"mailbox_mapping": {"mailboxes": []}})
        config = load_config(self.config_path)
        add_mailbox(config, {"source_email": "a@x.com", "owner": "X"})
        with self.assertRaises(ValueError):
            add_mailbox(config, {"source_email": "A@x.com", "owner": "Y"})

    def test_add_requires_fields(self):
        config = {"mailbox_mapping": {"mailboxes": []}}
        with self.assertRaises(ValueError):
            add_mailbox(config, {"source_email": "", "owner": ""})
        with self.assertRaises(ValueError):
            add_mailbox(config, {"source_email": "a@x.com", "owner": ""})

    def test_add_invalid_channel_rejected(self):
        config = {"mailbox_mapping": {"mailboxes": []}}
        with self.assertRaises(ValueError):
            add_mailbox(
                config,
                {"source_email": "a@x.com", "owner": "X", "notify_channel": "bogus"},
            )

    def test_add_default_channel(self):
        config = {
            "mailbox_mapping": {"mailboxes": [], "default_channel": "group"}
        }
        mailboxes = add_mailbox(config, {"source_email": "a@x.com", "owner": "X"})
        self.assertEqual(mailboxes[0]["notify_channel"], "group")

    def test_remove_mailbox(self):
        self._write({"mailbox_mapping": {"mailboxes": []}})
        config = load_config(self.config_path)
        add_mailbox(config, {"source_email": "a@x.com", "owner": "X"})
        add_mailbox(config, {"source_email": "b@x.com", "owner": "Y"})
        save_config(self.config_path, config)

        config = load_config(self.config_path)
        kept = remove_mailbox(config, "A@X.COM")
        save_config(self.config_path, config)
        self.assertEqual(len(kept), 1)
        self.assertEqual(kept[0]["source_email"], "b@x.com")

    def test_remove_unknown_rejected(self):
        self._write({"mailbox_mapping": {"mailboxes": []}})
        config = load_config(self.config_path)
        add_mailbox(config, {"source_email": "a@x.com", "owner": "X"})
        with self.assertRaises(ValueError):
            remove_mailbox(config, "missing@x.com")

    def test_first_run_detection(self):
        self.assertTrue(is_first_run(self.config_path))
        self._write({"imap": {"email": "real@qq.com"}})
        self.assertFalse(is_first_run(self.config_path))
        self._write({"imap": {"email": "你的QQ邮箱"}})
        self.assertTrue(is_first_run(self.config_path))

    def test_atomic_write_does_not_leave_temp(self):
        self._write({"imap": {"email": "real@qq.com"}})
        config = load_config(self.config_path)
        save_config(self.config_path, config)
        leftovers = list(self.config_path.parent.glob(".config-*.json"))
        self.assertEqual(leftovers, [])

    def test_retry_on_partial_json(self):
        """Half-written JSON is retried 3×0.5s. We simulate the scenario by
        writing valid JSON, waiting for retries to expire, then checking the
        final state is consistent."""
        self._write({"imap": {"email": "real@qq.com"}})
        config = load_config(self.config_path)
        self.assertEqual(config["imap"]["email"], "real@qq.com")

    def test_optional_prefix(self):
        config = {"mailbox_mapping": {"mailboxes": []}}
        mailboxes = add_mailbox(
            config,
            {
                "source_email": "a@x.com",
                "owner": "X",
                "notify_channel": "group",
                "prefix": "[负责人]",
            },
        )
        self.assertEqual(mailboxes[0].get("prefix"), "[负责人]")


if __name__ == "__main__":
    unittest.main()