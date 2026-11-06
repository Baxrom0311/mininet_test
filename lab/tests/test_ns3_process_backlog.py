"""A slow log consumer must not turn drainable output into a pipe-lifecycle error."""

import json
import os
import signal
import subprocess
import sys
import time
import unittest
from unittest.mock import patch

from lab.engines import ns3_process


class BacklogTests(unittest.TestCase):
    def test_successful_child_with_slow_log_callback_drains_all_output(self):
        original_popen = subprocess.Popen
        children, logs = [], []
        expected = {"engine": "ns3", "flows": {}, "duration": 1}
        script = """
import json, sys
from pathlib import Path
Path(sys.argv[1]).write_text(%r)
for index in range(100):
    print(json.dumps({'log': str(index) + ':' + 'x' * 100}), flush=True)
""" % json.dumps(expected)

        def spawn(command, **kwargs):
            process = original_popen([sys.executable, "-u", "-c", script, command[-1]], **kwargs)
            children.append(process)
            return process

        def consume(message):
            logs.append(message)
            time.sleep(.005)

        try:
            with patch.object(ns3_process.subprocess, "Popen", side_effect=spawn), \
                    patch.object(ns3_process, "PIPE_TIMEOUT", .03), \
                    patch.object(ns3_process, "POLL_INTERVAL", .005), \
                    patch.object(ns3_process, "READ_BYTES", 128):
                result = ns3_process.run_ns3({}, {"duration": 1}, lambda value: None, consume)
            self.assertEqual(result, expected)
            self.assertEqual([message.split(":", 1)[0] for message in logs], [str(i) for i in range(100)])
            self.assertEqual(len(children), 1)
            self.assertEqual(children[0].returncode, 0)
            self.assertTrue(children[0].stdout.closed)
        finally:
            for process in children:
                if process.poll() is None:
                    os.killpg(process.pid, signal.SIGKILL)
                process.wait(timeout=3)
                process.stdout.close()


if __name__ == "__main__":
    unittest.main()
