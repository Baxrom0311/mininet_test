"""Own actual local subprocesses; no ns-3 imports, packets or namespaces needed."""

import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

from lab.engines import ns3_process

REAL_POPEN = subprocess.Popen
RESULT = {"engine": "ns3", "flows": {}, "duration": 1}


class Cancelled(RuntimeError):
    pass


class Ns3ProcessTests(unittest.TestCase):
    def setUp(self):
        self.children = []
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.addCleanup(self.reap_children)
        for name, value in (("TERM_TIMEOUT", .1), ("EXIT_TIMEOUT", .4), ("PIPE_TIMEOUT", .2),
                            ("POLL_INTERVAL", .01), ("MAX_LINE_BYTES", 1024)):
            patched = patch.object(ns3_process, name, value)
            patched.start()
            self.addCleanup(patched.stop)

    def reap_children(self):
        for process in self.children:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            if process.poll() is None:
                process.kill()
            process.wait(timeout=3)
            if process.stdout is not None:
                process.stdout.close()

    def run_script(self, script, progress=lambda value: None, log=lambda message: None, *, check_cancel=None):
        def spawn(command, **kwargs):
            self.assertIn("lab.engines.ns3_runner", command)
            self.assertTrue(kwargs.get("start_new_session"), "runner must own its subprocess group")
            process = REAL_POPEN([sys.executable, "-u", "-c", script, str(command[-1])], **kwargs)
            self.children.append(process)
            return process
        with patch.object(ns3_process.subprocess, "Popen", side_effect=spawn):
            result = ns3_process.run_ns3({}, {"duration": 1}, progress, log, check_cancel=check_cancel)
        self.assert_owned_children_reaped()
        return result

    def assert_owned_children_reaped(self):
        self.assertTrue(self.children, "test did not spawn a real child")
        for child in self.children:
            self.assertIsNotNone(child.poll(), "direct simulation child remains alive")
            self.assertTrue(child.stdout.closed, "simulation pipe remains open")

    @staticmethod
    def output_script(prefix="", result=RESULT):
        return prefix + "\nfrom pathlib import Path\nimport sys\nPath(sys.argv[1]).write_text(" + repr(json.dumps(result)) + ")\n"

    def cancellation_after(self, seconds=.08):
        deadline = time.monotonic() + seconds
        def check():
            if time.monotonic() >= deadline:
                raise Cancelled("requested cancellation")
        return check

    def test_successful_messages_and_nonobject_stdout_do_not_change_result(self):
        progress, logs = [], []
        script = self.output_script("""
import json
for message in (None, 1, [], 'plain JSON string', {'progress': .25}, {'log': 'ready'}, {'progress': 1.0}):
    print(json.dumps(message), flush=True)
""")
        self.assertEqual(self.run_script(script, progress.append, logs.append), RESULT)
        self.assertEqual(progress, [.25, 1.])
        self.assertEqual(logs, ["ready"])

    def test_callback_failure_reaps_running_child_and_preserves_primary_error(self):
        script = "import time; print('{\"progress\":0.25}', flush=True); time.sleep(30)"
        def fail(value):
            raise ValueError("callback failed")
        with self.assertRaisesRegex(ValueError, "callback failed"):
            self.run_script(script, fail)
        self.assert_owned_children_reaped()

    def test_silent_startup_can_be_cancelled(self):
        started = time.monotonic()
        with self.assertRaisesRegex(Cancelled, "requested cancellation"):
            self.run_script("import time; time.sleep(30)", check_cancel=self.cancellation_after())
        self.assertLess(time.monotonic() - started, 2)
        self.assert_owned_children_reaped()

    def test_stdout_eof_does_not_disable_cancellation(self):
        script = "import os,time; os.close(1); os.close(2); time.sleep(30)"
        started = time.monotonic()
        with self.assertRaisesRegex(Cancelled, "requested cancellation"):
            self.run_script(script, check_cancel=self.cancellation_after())
        self.assertLess(time.monotonic() - started, 2)
        self.assert_owned_children_reaped()

    def test_stdout_eof_without_exit_times_out_and_reaps_child(self):
        script = "import os,time; os.close(1); os.close(2); time.sleep(30)"
        started = time.monotonic()
        with self.assertRaisesRegex(TimeoutError, "stdout.*exit"):
            self.run_script(script)
        self.assertLess(time.monotonic() - started, 2)
        self.assert_owned_children_reaped()

    def test_selector_registration_failure_reaps_child_and_closes_pipe(self):
        with patch.object(ns3_process.selectors.DefaultSelector, "register", side_effect=OSError("register failed")), \
                self.assertRaisesRegex(OSError, "register failed"):
            self.run_script("import time; time.sleep(30)")
        self.assert_owned_children_reaped()

    def test_stdout_read_error_propagates_and_reaps_child(self):
        real_read = os.read
        def read(fd, size):
            for child in self.children:
                if child.stdout is not None and not child.stdout.closed and fd == child.stdout.fileno():
                    raise OSError("stdout read failed")
            return real_read(fd, size)  # Popen also reads a separate startup-error pipe.
        with patch.object(ns3_process.os, "read", side_effect=read), \
                self.assertRaisesRegex(OSError, "stdout read failed"):
            self.run_script("import time; print('ready', flush=True); time.sleep(30)")
        self.assert_owned_children_reaped()

    def test_sigterm_ignoring_child_is_killed_and_waited(self):
        script = """
import signal,time
signal.signal(signal.SIGTERM, signal.SIG_IGN)
print('{"progress":0.25}', flush=True)
time.sleep(30)
"""
        def cancel(value):
            raise Cancelled("cancel after handler is installed")
        with self.assertRaisesRegex(Cancelled, "handler is installed"):
            self.run_script(script, cancel)
        self.assert_owned_children_reaped()
        self.assertEqual(self.children[-1].returncode, -signal.SIGKILL)

    def test_nonzero_exit_is_failure_even_when_output_exists(self):
        script = self.output_script() + "raise SystemExit(7)\n"
        with self.assertRaises(RuntimeError) as error:
            self.run_script(script)
        self.assertIn("7", str(error.exception))
        self.assert_owned_children_reaped()

    def test_cleanup_group_signal_error_is_not_the_primary_callback_error(self):
        real_killpg = os.killpg
        def group_signal(pid, signum):
            if signum == signal.SIGTERM:
                raise PermissionError("group TERM denied")
            return real_killpg(pid, signum)
        def fail(value):
            raise ValueError("primary callback failure")
        with patch.object(ns3_process.os, "killpg", side_effect=group_signal), \
                self.assertRaisesRegex(ValueError, "primary callback failure") as error:
            self.run_script("import time; print('{\"progress\":0.25}', flush=True); time.sleep(30)", fail)
        self.assertTrue(any("group TERM denied" in note for note in error.exception.__notes__))
        self.assert_owned_children_reaped()
        self.assertEqual(self.children[-1].returncode, -signal.SIGKILL)

    def test_missing_and_malformed_result_are_failures(self):
        scripts = ["pass", self.output_script(result=[]), self.output_script(result=None),
                   "from pathlib import Path; import sys; Path(sys.argv[1]).write_text('not JSON')"]
        for script in scripts:
            with self.subTest(script=script), self.assertRaises((RuntimeError, ValueError)):
                self.run_script(script)
            self.assert_owned_children_reaped()

    def test_invalid_progress_and_log_fields_cannot_reach_callbacks(self):
        for message in ({"progress": None}, {"progress": True}, {"progress": float("nan")},
                        {"progress": -.1}, {"progress": 1.1}, {"progress": "bad"}, {"log": 123}):
            progress, logs = [], []
            script = self.output_script("print(" + repr(json.dumps(message)) + ", flush=True)")
            with self.subTest(message=message), self.assertRaises((RuntimeError, ValueError)):
                self.run_script(script, progress.append, logs.append)
            self.assertEqual(progress, [])
            self.assertEqual(logs, [])
            self.assert_owned_children_reaped()

    def test_nonfinite_result_constants_are_not_accepted(self):
        for value in (float("nan"), float("inf"), float("-inf")):
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, "nonfinite"):
                self.run_script(self.output_script(result={**RESULT, "value": value}))
            self.assert_owned_children_reaped()

    def test_noisy_output_with_slow_log_callback_remains_cancellable(self):
        logs = []
        def log(message):
            logs.append(message)
            time.sleep(.01)
        def cancel():
            if len(logs) >= 3:
                raise Cancelled("cancel during noisy output")
        script = """
import json
line = json.dumps({'log': 'noisy'})
while True:
    print(line, flush=True)
"""
        started = time.monotonic()
        with self.assertRaisesRegex(Cancelled, "noisy output"):
            self.run_script(script, log=log, check_cancel=cancel)
        self.assertLess(time.monotonic() - started, 2)
        self.assertEqual(logs, ["noisy"] * 3)
        self.assert_owned_children_reaped()

    def test_oversized_lines_and_invalid_utf8_preserve_following_messages(self):
        script = self.output_script(r"""
import sys,json
print(json.dumps({'log': 'y' * 8192}), flush=True)
sys.stdout.buffer.write(b'x' * 32768 + b'\n\xff\xfe noise\n')
sys.stdout.buffer.flush()
print('{"log":"after noise"}', flush=True)
""")
        logs = []
        self.assertEqual(self.run_script(script, log=logs.append), RESULT)
        self.assertEqual(logs, ["after noise"])

    def test_unterminated_oversized_diagnostic_is_bounded_on_error(self):
        script = "import sys; sys.stdout.buffer.write(b'x' * 262144 + b'\\xff'); sys.stdout.buffer.flush(); raise SystemExit(9)"
        with self.assertRaises(RuntimeError) as error:
            self.run_script(script)
        self.assertLess(len(str(error.exception)), 16384)
        self.assert_owned_children_reaped()

    def test_descendant_holding_stdout_cannot_hang_group_cleanup(self):
        pidfile = Path(self.temp.name) / "descendant.pid"
        script = self.output_script("""
import subprocess,sys
from pathlib import Path
descendant = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)'])
Path(""" + repr(str(pidfile)) + """).write_text(str(descendant.pid))
""")
        started = time.monotonic()
        try:
            self.run_script(script)
        except RuntimeError:
            pass  # A held-open pipe may be reported as an incomplete child lifecycle.
        self.assertLess(time.monotonic() - started, 2)
        self.assert_owned_children_reaped()
        self.assertTrue(pidfile.exists(), "descendant did not start")
        descendant = int(pidfile.read_text())
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            try:
                state = Path(f"/proc/{descendant}/stat").read_text().rsplit(")", 1)[1].split()[0]
            except FileNotFoundError:
                break
            if state == "Z":
                break  # Reparented zombies are stopped; only the new parent can reap them.
            time.sleep(.01)
        else:
            self.fail("descendant survives simulation process-group cleanup")


if __name__ == "__main__":
    unittest.main()
