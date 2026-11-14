"""Controller startup regressions; optional real OpenFlow 1.3 handshake.

NETLAB_RYU_PYTHON=/path/to/osken/venv/bin/python python -m unittest \
    lab.tests.test_controller_startup -v
"""

import io
import os
import socket
import struct
import subprocess
import sys
import tempfile
from types import ModuleType
import unittest
from unittest.mock import Mock, mock_open, patch

import controller
from lab.drivers.base import DriverError
from lab.drivers.mininet_driver import MininetDriver, _wait_port


class ControllerStartupTests(unittest.TestCase):
    def test_dead_process_cannot_be_ready_even_if_another_listener_exists(self):
        process = Mock(stdout=io.BytesIO(b"broken controller app\n"), returncode=1)
        process.poll.return_value = 1
        with tempfile.TemporaryDirectory() as directory, patch.object(controller, "DATA_DIR", directory), \
                patch.object(controller, "check_controller_health", return_value=True) as health:
            with self.assertRaisesRegex(RuntimeError, "exit=1.*\n.*broken controller app"):
                controller._drain_proc(process, 16653)
            health.assert_not_called()
            self.assertTrue(process.stdout.closed)

    def test_startup_timeout_kills_and_reaps_stubborn_controller(self):
        process = Mock(stdout=io.BytesIO())
        process.poll.return_value = None
        process.wait.side_effect = [subprocess.TimeoutExpired("fixture", 5), 0]
        with tempfile.TemporaryDirectory() as directory, patch.object(controller, "DATA_DIR", directory), \
                patch.object(controller.time, "monotonic", side_effect=[0, 31]):
            with self.assertRaisesRegex(RuntimeError, "30s"):
                controller._drain_proc(process, 16653)
        process.terminate.assert_called_once()
        process.kill.assert_called_once()
        self.assertEqual(process.wait.call_count, 2)
        self.assertTrue(process.stdout.closed)

    def test_lab_port_probe_rejects_process_that_dies_during_connect(self):
        process = Mock()
        process.poll.side_effect = [None, 1]
        with patch('lab.drivers.mininet_driver.socket.create_connection'):
            self.assertFalse(_wait_port(16653, proc=process))

    def test_root_uses_discovered_manager_and_interpreter(self):
        for manager in (["/custom/bin/python", "-m", "os_ken.cmd.manager"], ["/custom/ryu-manager"]):
            with self.subTest(manager=manager), patch.object(controller, "find_ryu", return_value=manager), \
                    patch("builtins.open", mock_open()), patch.object(controller, "_drain_proc") as wait, \
                    patch.object(controller.subprocess, "Popen") as popen:
                result = controller.start_controller("ryu", ip="127.0.0.2", port=16653)
                self.assertEqual(popen.call_args.args[0], [*manager, "--ofp-listen-host", "127.0.0.2",
                    "--ofp-tcp-listen-port", "16653", "/tmp/transport_monitor.py"])
                self.assertIs(result, popen.return_value)
                wait.assert_called_once_with(result, 16653, "Ryu/os-ken", ip="127.0.0.2")

    def test_lab_failed_local_controller_is_cleaned_up_and_not_attached(self):
        self.lab_start(ready=False)

    def test_lab_ready_controller_is_attached(self):
        self.lab_start(ready=True)

    def lab_start(self, ready):
        # Stub only Mininet's controller registration; no namespace operations.
        node = ModuleType("mininet.node")
        node.RemoteController = Mock()
        with tempfile.TemporaryDirectory() as directory, \
                patch.dict(sys.modules, {"mininet": ModuleType("mininet"), "mininet.node": node}), \
                patch("lab.drivers.mininet_driver.RUN_DIR", directory), \
                patch("lab.drivers.mininet_driver.controller_cmd", return_value=["osken-manager"]), \
                patch("lab.drivers.mininet_driver._wait_port", return_value=ready), \
                patch("lab.drivers.mininet_driver.subprocess.Popen") as popen:
            driver = MininetDriver(Mock())
            driver.project = {"controllers": {"c1": {"type": "ryu", "port": 16653, "code": ""}}}
            driver.net = Mock()
            with patch.object(driver, "_write_ctrl_configs"), \
                    patch.object(driver, "controller_log", return_value="ImportError: broken app"), \
                    patch.object(driver, "_kill") as kill:
                if ready:
                    driver._ctrl_start("c1")
                    self.assertIs(driver._ctrl_procs["c1"], popen.return_value)
                    driver.net.addController.assert_called_once()
                    kill.assert_not_called()
                else:
                    with self.assertRaisesRegex(DriverError, "c1.*16653.*\nImportError: broken app"):
                        driver._ctrl_start("c1")
                    kill.assert_called_once_with(popen.return_value)
                    self.assertNotIn("c1", driver._ctrl_procs)
                    driver.net.addController.assert_not_called()


@unittest.skipUnless(os.environ.get("NETLAB_RYU_PYTHON"), "set NETLAB_RYU_PYTHON for real controller handshake")
class RealControllerHandshakeTests(unittest.TestCase):
    def test_root_manager_negotiates_openflow_and_installs_table_miss(self):
        manager = [os.environ["NETLAB_RYU_PYTHON"], "-m", "os_ken.cmd.manager"]
        with socket.socket() as reservation:
            reservation.bind(("127.0.0.1", 0))
            port = reservation.getsockname()[1]
        with tempfile.TemporaryDirectory() as directory, \
                patch.object(controller, "DATA_DIR", directory), \
                patch.object(controller, "find_ryu", return_value=manager):
            process = controller.start_controller("os-ken", port=port)
            try:
                with socket.create_connection(("127.0.0.1", port), timeout=5) as connection:
                    connection.sendall(struct.pack("!BBHI", 4, 0, 8, 1))  # OF1.3 hello
                    for _ in range(20):
                        version, kind, xid, body = self.receive(connection)
                        self.assertEqual(version, 4)
                        if kind == 2:  # echo request
                            connection.sendall(struct.pack("!BBHI", 4, 3, 8 + len(body), xid) + body)
                        elif kind == 5:  # switch features request
                            features = struct.pack("!QIBB2xII", 1, 256, 1, 0, 0, 0)
                            connection.sendall(struct.pack("!BBHI", 4, 6, 8 + len(features), xid) + features)
                        elif kind == 14:  # table-miss flow mod
                            self.assertEqual(struct.unpack_from("!H", body, 22)[0], 0)
                            self.assertEqual(struct.unpack_from("!HHIH", body, len(body) - 16),
                                             (0, 16, 0xfffffffd, 0xffff))
                            break
                        elif kind == 1:
                            self.fail(f"OpenFlow error: {body.hex()}")
                    else:
                        self.fail("controller did not install table-miss flow")
                    self.assertIsNone(process.poll())
            finally:
                process.terminate()
                process.wait(timeout=5)
                process.stdout.close()

    @staticmethod
    def receive(connection):
        def exact(length):
            data = b""
            while len(data) < length:
                part = connection.recv(length - len(data))
                if not part:
                    raise AssertionError("controller closed OpenFlow connection")
                data += part
            return data
        version, kind, length, xid = struct.unpack("!BBHI", exact(8))
        if length < 8:
            raise AssertionError("invalid OpenFlow message length")
        return version, kind, xid, exact(length - 8)


if __name__ == "__main__":
    unittest.main()
