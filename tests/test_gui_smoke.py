"""Pure-function helpers and an offscreen Qt smoke test for the dashboard."""

import json
import os
import time
import unittest
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication


class PureHelpersTests(unittest.TestCase):
    """Validate the helpers used by the dashboard without instantiating widgets."""

    def test_format_duration_short(self):
        from gui.app import _format_duration

        self.assertEqual(_format_duration(0), "00:00")
        self.assertEqual(_format_duration(59), "00:59")
        self.assertEqual(_format_duration(60), "01:00")
        self.assertEqual(_format_duration(3661), "01:01:01")

    def test_format_duration_negative_clamps(self):
        from gui.app import _format_duration

        self.assertEqual(_format_duration(-5), "00:00")

    def test_heartbeat_age_missing(self):
        from gui.app import _heartbeat_age_seconds

        # Default project root: heartbeat is missing in a clean checkout.
        if Path("state/heartbeat").exists():
            self.skipTest("heartbeat file present in checkout")
        self.assertIsNone(_heartbeat_age_seconds())

    def test_heartbeat_hint_thresholds(self):
        from gui.app import _heartbeat_hint

        self.assertEqual(_heartbeat_hint(10), "心跳新鲜")
        self.assertEqual(_heartbeat_hint(60), "略慢")
        self.assertEqual(_heartbeat_hint(150), "心跳偏慢，请关注")
        self.assertEqual(_heartbeat_hint(600), "心跳异常，守护可能卡死")

    def test_theme_qss_distinct(self):
        from gui.theme import theme_qss

        light = theme_qss("light")
        dark = theme_qss("dark")
        self.assertIn("#f1f5f9", light)  # light bg token
        self.assertIn("#0b1220", dark)   # dark bg token
        self.assertNotEqual(light, dark)

    def test_supported_themes_constant(self):
        from gui.app import SUPPORTED_THEMES, DEFAULT_THEME

        self.assertIn(DEFAULT_THEME, SUPPORTED_THEMES)


@unittest.skipUnless(
    os.environ.get("QT_QPA_PLATFORM") in {"offscreen", "minimal", None},
    "offscreen platform required for GUI smoke test",
)
class DashboardSmokeTest(unittest.TestCase):
    def test_mainwindow_starts_and_stops(self):
        from gui.app import MainWindow

        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        app = QApplication.instance() or QApplication([])
        window = MainWindow(controller=_NullController(), start_log_tailer=False)
        window.show()
        QTimer.singleShot(500, window.close)
        QTimer.singleShot(1500, app.quit)
        rc = app.exec()
        self.assertIn(rc, (0, None))


class _NullController:
    """Stub that satisfies the MainWindow surface used in the smoke test."""

    def daemon_pid(self):
        return None

    def daemon_running(self):
        return False

    def launcher_lock_held(self):
        return False


if __name__ == "__main__":
    unittest.main()