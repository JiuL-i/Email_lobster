import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

from single_instance import (
    AlreadyRunningError,
    SingleInstanceLock,
    requires_instance_lock,
)


class _LockBox:
    """Allocate a directory and close all locks before cleaning up on Windows."""

    def __enter__(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.directory = Path(self._tmp.name)
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        self._tmp.cleanup()

    def path(self, name: str = "mail.lock") -> Path:
        return self.directory / name


class SingleInstanceLockTests(unittest.TestCase):
    def test_acquire_release_and_metadata(self):
        with _LockBox() as box:
            path = box.path()
            lock = SingleInstanceLock(path, "test")
            lock.acquire()
            lock.release()
            metadata = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(metadata["pid"], os.getpid())
            self.assertEqual(metadata["mode"], "test")
            lock.release()
            with SingleInstanceLock(path, "again"):
                pass

    def test_second_lock_is_rejected(self):
        with _LockBox() as box:
            path = box.path()
            first = SingleInstanceLock(path, "first")
            first.acquire()
            try:
                with self.assertRaises(AlreadyRunningError):
                    SingleInstanceLock(path, "second").acquire()
            finally:
                first.release()

    def test_different_paths_are_independent(self):
        with _LockBox() as box:
            with SingleInstanceLock(box.path("one.lock"), "one"):
                with SingleInstanceLock(box.path("two.lock"), "two"):
                    pass

    def test_corrupt_metadata_does_not_change_arbitration(self):
        with _LockBox() as box:
            path = box.path()
            with SingleInstanceLock(path, "first"):
                pass
            with path.open("w", encoding="utf-8") as stream:
                stream.write("not json")
            with SingleInstanceLock(path, "second"):
                pass

    def test_cli_lock_policy(self):
        self.assertTrue(requires_instance_lock([]))
        self.assertTrue(requires_instance_lock(["--once"]))
        self.assertTrue(requires_instance_lock(["--summary", "17:00"]))
        self.assertFalse(requires_instance_lock(["--test-feishu"]))
        self.assertFalse(requires_instance_lock(["--test-feishu-app"]))
        self.assertFalse(requires_instance_lock(["--report-error"]))

    def test_real_subprocess_contention_and_crash_release(self):
        holder = (
            "import os, sys, time\n"
            "from single_instance import SingleInstanceLock\n"
            "l = SingleInstanceLock(sys.argv[1], 'child')\n"
            "l.acquire()\n"
            "sys.stdout.write(str(os.getpid()) + '\\n')\n"
            "sys.stdout.flush()\n"
            "time.sleep(30)\n"
        )
        contender = (
            "import sys\n"
            "from single_instance import AlreadyRunningError, LockUnavailableError, SingleInstanceLock\n"
            "l = SingleInstanceLock(sys.argv[1], 'rival')\n"
            "try:\n"
            "    l.acquire()\n"
            "except (AlreadyRunningError, LockUnavailableError):\n"
            "    sys.exit(2)\n"
            "finally:\n"
            "    l.release()\n"
            "sys.exit(0)\n"
        )
        with _LockBox() as box:
            lock_path = str(box.path())
            child = subprocess.Popen(
                [sys.executable, "-u", "-c", holder, lock_path],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            try:
                child_pid = int(child.stdout.readline().strip())
                rival = subprocess.run(
                    [sys.executable, "-u", "-c", contender, lock_path],
                    capture_output=True,
                    text=True,
                    timeout=10,
                )
                self.assertEqual(rival.returncode, 2)
            finally:
                child.kill()
                child.wait(timeout=5)

            recovered = False
            for _ in range(40):
                try:
                    with SingleInstanceLock(lock_path, "after-crash"):
                        recovered = True
                        break
                except AlreadyRunningError:
                    time.sleep(0.1)
            self.assertTrue(recovered, f"lock remained held by crashed PID {child_pid}")


if __name__ == "__main__":
    unittest.main()
