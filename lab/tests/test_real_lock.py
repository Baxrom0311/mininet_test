"""Real kernel flock tests; no root, Mininet or network changes required."""

from contextlib import contextmanager, redirect_stderr
import importlib
import io
import os
from pathlib import Path
import select
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

from lab import real_lock
from lab.drivers.mininet_driver import MininetDriver
from lab.real_lock import RealNetworkBusy, real_network_lock, serial_real_test

PROBE = """
import sys
from lab.real_lock import RealNetworkBusy, real_network_lock
try:
    with real_network_lock(sys.argv[1]):
        pass
except RealNetworkBusy:
    sys.exit(23)
"""


@unittest.skipUnless(sys.platform == "linux" and shutil.which("flock"), "Linux/util-linux flock required")
class RealLockTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="netlab-lock-")
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name, "real.lock")

    def probe(self):
        r = subprocess.run([sys.executable, "-c", PROBE, str(self.path)], capture_output=True, text=True, timeout=10)
        self.assertIn(r.returncode, (0, 23), r.stderr)
        return r.returncode

    @contextmanager
    def holder(self):
        code = """
import sys
from lab.real_lock import real_network_lock
with real_network_lock(sys.argv[1]):
    print('ready', flush=True)
    sys.stdin.readline()
"""
        p = subprocess.Popen([sys.executable, "-c", code, str(self.path)],
                             stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            self.assertTrue(select.select([p.stdout], [], [], 5)[0], "lock holder did not become ready")
            self.assertEqual(p.stdout.readline().strip(), "ready")
            yield
        finally:
            if p.poll() is None:
                p.stdin.write("stop\n")
                p.stdin.flush()
            out, err = p.communicate(timeout=5)
            self.assertEqual(p.returncode, 0, out + err)

    def test_direct_lock_excludes_other_process_and_releases_on_exception(self):
        with self.assertRaisesRegex(RuntimeError, "fixture failure"):
            with real_network_lock(self.path):
                self.assertEqual(self.probe(), 23)
                raise RuntimeError("fixture failure")
        self.assertEqual(self.probe(), 0)

    def test_nested_context_does_not_release_outer_lock_or_change_inode(self):
        with real_network_lock(self.path):
            inode = self.path.stat().st_ino
            with real_network_lock(self.path):
                self.assertEqual(self.probe(), 23)
            self.assertEqual(self.probe(), 23)
        self.assertEqual(self.path.stat().st_ino, inode)
        self.assertEqual(self.probe(), 0)

    def test_unlocked_existing_descriptor_is_not_ownership(self):
        self.path.write_text("keep this content")
        with self.path.open() as unrelated, self.holder():
            with self.assertRaises(RealNetworkBusy):
                with real_network_lock(self.path):
                    self.fail("unlocked descriptor bypassed another process")
            self.assertEqual(unrelated.read(), "keep this content")
        self.assertEqual(self.probe(), 0)

    def test_other_thread_cannot_reuse_current_threads_descriptor(self):
        errors = []
        def attempt():
            try:
                with real_network_lock(self.path):
                    errors.append("entered")
            except RealNetworkBusy:
                errors.append("busy")
        with real_network_lock(self.path):
            t = threading.Thread(target=attempt)
            t.start()
            t.join(timeout=5)
            self.assertFalse(t.is_alive())
            self.assertEqual(errors, ["busy"])
        self.assertEqual(self.probe(), 0)

    def test_util_linux_flock_wrapper_and_no_fork_keep_outer_lock(self):
        code = """
import subprocess, sys
from lab.real_lock import real_network_lock
path, probe = sys.argv[1:]
def busy():
    assert subprocess.run([sys.executable, '-c', probe, path], timeout=5).returncode == 23
with real_network_lock(path):
    with real_network_lock(path):
        busy()
    busy()
# The external flock must remain held even after both Python contexts exit.
busy()
"""
        for options in ([], ["--no-fork"]):
            with self.subTest(options=options):
                r = subprocess.run(["flock", *options, str(self.path), sys.executable, "-c", code,
                                    str(self.path), PROBE], capture_output=True, text=True, timeout=10)
                self.assertEqual(r.returncode, 0, r.stderr)
                self.assertEqual(self.probe(), 0)

    def test_shared_and_closed_descriptor_wrappers_do_not_bypass_lock(self):
        for options in (["--shared"], ["--close"]):
            with self.subTest(options=options):
                r = subprocess.run(["flock", *options, str(self.path), sys.executable, "-c", PROBE, str(self.path)],
                                   capture_output=True, text=True, timeout=10)
                self.assertEqual(r.returncode, 23, r.stderr)
                self.assertEqual(self.probe(), 0)

    def test_fork_child_does_not_borrow_parents_internal_lock(self):
        # Fork only in a fresh, single-threaded process, even when the broader
        # backend suite has executor threads. Bound the whole fork test by timeout.
        code = """
import os, sys
from lab.real_lock import RealNetworkBusy, real_network_lock
with real_network_lock(sys.argv[1]):
    pid = os.fork()
    if pid == 0:
        try:
            with real_network_lock(sys.argv[1]):
                os._exit(3)
        except RealNetworkBusy:
            os._exit(0)
        except BaseException:
            os._exit(4)
    _, status = os.waitpid(pid, 0)
    assert os.waitstatus_to_exitcode(status) == 0
"""
        r = subprocess.run([sys.executable, "-c", code, str(self.path)],
                           capture_output=True, text=True, timeout=10)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.probe(), 0)

    def test_symlink_is_rejected_and_target_unchanged(self):
        target = Path(self.tmp.name, "target")
        target.write_text("untouched")
        self.path.symlink_to(target)
        with self.assertRaises(OSError):
            with real_network_lock(self.path):
                self.fail("symlink accepted")
        self.assertEqual(target.read_text(), "untouched")

    def test_all_real_entry_points_refuse_contention_before_starting_network(self):
        modules = ["lab.tests." + p.stem for p in Path(__file__).parent.glob("real_*.py")]
        modules += ["lab.benchmarks.real_scale", "lab.benchmarks.real_calibration"]
        entry_points = [(name, importlib.import_module(name).main) for name in modules]
        self.assertEqual(len(entry_points), 13)
        with self.holder(), patch.object(real_lock, "LOCK_PATH", self.path), \
                patch.object(os, "geteuid", return_value=0), \
                patch.object(MininetDriver, "__init__", side_effect=AssertionError("driver started before lock")):
            for name, main in entry_points:
                with self.subTest(module=name), redirect_stderr(io.StringIO()) as err:
                    self.assertEqual(main(), 1)
                    self.assertIn("real network busy", err.getvalue())
        self.assertEqual(self.probe(), 0)

    def test_non_root_prerequisite_exit_and_decorated_cleanup(self):
        @serial_real_test
        def missing():
            return 2
        with patch.object(os, "geteuid", return_value=1000), patch.object(real_lock, "real_network_lock") as acquire:
            self.assertEqual(missing(), 2)
            acquire.assert_not_called()
        @serial_real_test
        def run():
            self.assertEqual(self.probe(), 23)
            return 7
        with patch.object(os, "geteuid", return_value=0), patch.object(real_lock, "LOCK_PATH", self.path):
            self.assertEqual(run(), 7)
        self.assertEqual(self.probe(), 0)


if __name__ == "__main__":
    unittest.main()
