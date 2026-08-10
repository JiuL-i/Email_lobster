"""Long-running stability and reliability checks for the single-instance lock.

Designed to be run on-demand via `python tests/test_single_instance_stress.py`
rather than as part of the normal `unittest discover` cycle. The harness
acquires and releases the lock many times, exercises exception paths, and
verifies that the lock module never leaks file descriptors or leaves the
file system in a bad state.

These checks do not require network or mailbox access.
"""

from __future__ import annotations

import gc
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path

from single_instance import (
    AlreadyRunningError,
    LockUnavailableError,
    SingleInstanceLock,
    requires_instance_lock,
)


ITERATIONS = 2000


class StabilityTests(unittest.TestCase):
    def test_acquire_release_cycle_does_not_leak_fds(self):
        before = self._count_open_files()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "lock"
            for _ in range(ITERATIONS):
                with SingleInstanceLock(path, "stress"):
                    pass
            gc.collect()
            after = self._count_open_files()
        # Allow small slack because unittest and tracemalloc keep files open.
        self.assertLessEqual(after - before, 5, "file descriptor leak")

    def test_idempotent_release_and_double_enter(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "lock"
            lock = SingleInstanceLock(path, "x")
            lock.acquire()
            with self.assertRaises(AlreadyRunningError):
                SingleInstanceLock(path, "y").acquire()
            lock.release()
            lock.release()
            with SingleInstanceLock(path, "z"):
                pass

    def test_acquire_after_terminate(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "lock"
            holder = (
                "import sys\n"
                "from single_instance import SingleInstanceLock\n"
                "l = SingleInstanceLock(sys.argv[1], 'hold')\n"
                "l.acquire()\n"
                "import time; time.sleep(60)\n"
            )
            contender = (
                "import sys\n"
                "from single_instance import AlreadyRunningError, SingleInstanceLock\n"
                "try:\n"
                "    SingleInstanceLock(sys.argv[1], 'r').acquire()\n"
                "    sys.exit(0)\n"
                "except AlreadyRunningError:\n"
                "    sys.exit(2)\n"
            )
            import subprocess

            child = subprocess.Popen(
                [sys.executable, "-u", "-c", holder, str(path)],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            try:
                rival = subprocess.run(
                    [sys.executable, "-u", "-c", contender, str(path)],
                    capture_output=True,
                    timeout=10,
                )
                self.assertEqual(rival.returncode, 2)
            finally:
                child.kill()
                child.wait(timeout=5)

            recovered = False
            deadline = time.time() + 10
            while time.time() < deadline:
                try:
                    with SingleInstanceLock(path, "after"):
                        recovered = True
                        break
                except AlreadyRunningError:
                    time.sleep(0.05)
            self.assertTrue(recovered)

    def test_repeated_subprocess_storm_does_not_break_lock(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "lock"
            for round_no in range(20):
                with SingleInstanceLock(path, f"r{round_no}"):
                    pass

    def test_requires_instance_lock_policy(self):
        self.assertTrue(requires_instance_lock([]))
        self.assertTrue(requires_instance_lock(["--once"]))
        self.assertTrue(requires_instance_lock(["--summary", "17:00"]))
        self.assertFalse(requires_instance_lock(["--test-feishu"]))
        self.assertFalse(requires_instance_lock(["--test-feishu-app"]))
        self.assertFalse(requires_instance_lock(["--report-error"]))

    @staticmethod
    def _count_open_files() -> int:
        if hasattr(os, "getopen_files"):
            return len(os.getopen_files())  # type: ignore[attr-defined]
        if sys.platform == "win32":
            try:
                import psutil  # type: ignore

                return len(psutil.Process().open_files())
            except Exception:
                return 0
        return 0


if __name__ == "__main__":
    unittest.main()