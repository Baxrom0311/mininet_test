"""Regression cases from the audit; no real SSH or Mininet privileges required."""

from concurrent.futures import ThreadPoolExecutor
import contextlib
import copy
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import types
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from lab.drivers.base import EventBus
from lab.drivers.demo_driver import DemoDriver
from lab.engines.manager import RunManager
from lab.model import empty_project
from lab.server import create_app
from lab.web_security import same_origin
from studio.api import RunManager as StudioRuns
from studio.remote import RemoteJob


class OriginTests(unittest.TestCase):
    def test_origin_normalization(self):
        self.assertTrue(same_origin("http://localhost:80", "ws://localhost/ws/events"))
        for origin in ("null", "https://untrusted.example", "http://localhost:8081", "http://user@localhost:8080"):
            self.assertFalse(same_origin(origin, "ws://localhost:8080/ws/events"), origin)

    def test_http_and_both_websockets_reject_foreign_origin(self):
        with TestClient(create_app("demo", token="local-test")) as client:
            client.cookies.set("netlab_token", "local-test")
            headers = {"origin": "https://untrusted.example"}
            self.assertEqual(client.post("/api/net/stop", headers=headers).status_code, 403)
            for endpoint in ("/ws/events", "/ws/term/pc1"):
                with self.assertRaises(WebSocketDisconnect) as caught:
                    with client.websocket_connect(endpoint, headers=headers):
                        pass
                self.assertEqual(caught.exception.code, 1008)
            with client.websocket_connect("/ws/events", headers={"origin": "http://testserver"}) as ws:
                self.assertEqual(ws.receive_json()["type"], "state")

    def test_local_cli_always_creates_token(self):
        from lab.netlab import main
        with patch.object(sys, "argv", ["netlab", "--driver", "demo"]), patch("uvicorn.run") as run, \
                contextlib.redirect_stdout(io.StringIO()):
            main()
        self.assertGreater(len(run.call_args.args[0].state.token), 20)


class RunTests(unittest.TestCase):
    def test_ten_simultaneous_starts_have_ten_results(self):
        p = empty_project("audit")
        p["scenarios"]["same"] = {"duration": 1, "apps": [], "events": []}
        with tempfile.TemporaryDirectory() as td, patch("lab.engines.manager.RESULTS_DIR", td), \
                patch("lab.engines.manager.time.strftime", return_value="20261106-000000"):
            manager = RunManager(EventBus(), DemoDriver(EventBus()))
            with ThreadPoolExecutor(max_workers=10) as pool:
                ids = list(pool.map(lambda _: manager.start(p, "same", "analytic")["run_id"], range(10)))
            deadline = time.monotonic() + 5
            while manager.active and time.monotonic() < deadline:
                time.sleep(0.01)
            self.assertEqual(len(set(ids)), 10)
            self.assertFalse(manager.active)
            for rid in ids:
                self.assertEqual(manager.get("audit", rid)["status"], "done")

    def test_expansion_failure_is_persisted_and_released(self):
        p = empty_project("audit")
        with tempfile.TemporaryDirectory() as td, patch("lab.engines.manager.RESULTS_DIR", td):
            manager = RunManager(EventBus(), DemoDriver(EventBus()))
            manager.active["expansion"] = {"project": "audit", "engine": "analytic"}
            with patch("lab.failures.expand", side_effect=ValueError("bad failure config")):
                manager._worker("expansion", "audit", p, "test", {"duration": 1}, "analytic")
            self.assertEqual(manager.get("audit", "expansion")["status"], "error")
            self.assertFalse(manager.active)


class SimulatorTests(unittest.TestCase):
    def test_sweep_records_actual_destinations_and_waits(self):
        from traffic_gen import TrafficGen
        with tempfile.TemporaryDirectory() as td:
            generator = object.__new__(TrafficGen)
            attacker = types.SimpleNamespace(name="attacker", IP=lambda: "192.0.2.10")
            target = types.SimpleNamespace(name="target", IP=lambda: "10.0.4.3")
            generator.clients, generator.servers = {"a": attacker}, {"b": target}
            generator.net = types.SimpleNamespace(hosts=[attacker, target])
            generator._running = True
            generator._get_load_factor = lambda: 0
            generator._anomaly_log = str(Path(td, "attacks.jsonl"))
            generator._sim_start = time.time()
            generator._log_event = lambda *a, **kw: None
            commands = []
            def execute(_host, command):
                commands.append(command)
                generator._running = False
            generator._safe_cmd = execute
            with patch("traffic_gen.ANOMALY_MIX", [("ping_sweep", 1, "icmp", (1, 1))]), \
                    patch("traffic_gen.random.random", return_value=0), patch("traffic_gen.time.sleep"):
                generator._anomaly_loop()
            entry = json.loads(Path(generator._anomaly_log).read_text())
            self.assertEqual(entry["target_ips"], ["10.0.4.1", "10.0.4.2", "10.0.4.254"])
            self.assertIn("timeout", commands[0])
            self.assertIn("wait", commands[0])
            self.assertGreaterEqual(entry["duration_sec"], 0)

    def test_startup_error_propagates_after_cleanup(self):
        import light_simulation as sim
        cli = types.ModuleType("mininet.cli"); cli.CLI = lambda net: None
        clean = types.ModuleType("mininet.clean"); clean.cleanup = lambda: None
        modules = {"mininet": types.ModuleType("mininet"), "mininet.cli": cli, "mininet.clean": clean}
        with tempfile.TemporaryDirectory() as td, patch.dict(sys.modules, modules), \
                patch.object(sim, "DATA_DIR", td), patch.object(sim.os, "geteuid", return_value=0), \
                patch.object(sim.signal, "signal"), patch.object(sys, "argv", ["light_simulation"]), \
                patch.object(sim, "start_controller", side_effect=RuntimeError("startup failed")), \
                patch.object(sim, "build_dataset") as build, \
                contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaisesRegex(RuntimeError, "startup failed"):
                sim.main()
            build.assert_not_called()

    def test_campaign_propagates_custom_data_directory(self):
        import os
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            binary = root / "bin"; binary.mkdir()
            fake = binary / "python3"
            fake.write_text('#!/bin/sh\nmkdir -p "$DATA_DIR/datasets"\nprintf "%s" "$DATA_DIR" > "$DATA_DIR/datasets/marker"\n')
            fake.chmod(0o755)
            data, results = root / "data", root / "results"
            r = subprocess.run(["bash", "run_campaign.sh", "--topologies", "three_as", "--modes", "ospf",
                                "--seeds", "0", "--data-dir", str(data), "--results-dir", str(results)],
                               env={**os.environ, "PATH": str(binary) + os.pathsep + os.environ["PATH"]},
                               capture_output=True, text=True, timeout=10)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertEqual((results / "three_as_ospf_seed0/datasets/marker").read_text(), str(data))


class StudioTests(unittest.TestCase):
    def setUp(self):
        self.cfg = {"remote_repo": "/repo with spaces", "wsl_distro": "Ubuntu"}
        self.manager = StudioRuns(lambda: self.cfg)
        self.rid = "run_" + "a" * 32
        self.manager._runs[self.rid] = {"duration": 1, "topology": "three_as", "routing": "ospf", "seed": 1,
                                         "done": False, "_cfg": self.cfg}

    def test_old_dataset_marker_cannot_complete_new_run(self):
        responses = [("STUDIO_PID=123", ""),
                     ("DATASET_READY\nSTUDIO_STATUS=pending\nSTUDIO_RUNNING=1\nSTUDIO_DATASET=0", ""),
                     ("STUDIO_STATUS=0\nSTUDIO_RUNNING=0\nSTUDIO_DATASET=1", "")]
        with patch.object(self.manager, "_exec_script", side_effect=responses) as execute, patch("studio.api.time.sleep"):
            self.manager._worker(self.rid, {}, self.cfg)
        self.assertEqual(execute.call_count, 3)
        self.assertEqual(self.manager._runs[self.rid]["state"], "done")
        self.assertNotIn("/data/datasets", RemoteJob(self.rid).poll(123).replace(RemoteJob(self.rid).data, ""))

    def test_nonzero_exit_is_error_even_with_dataset(self):
        with patch.object(self.manager, "_exec_script", side_effect=[("STUDIO_PID=123", ""),
                ("STUDIO_STATUS=7\nSTUDIO_RUNNING=0\nSTUDIO_DATASET=1", "")]), patch("studio.api.time.sleep"):
            self.manager._worker(self.rid, {}, self.cfg)
        self.assertEqual(self.manager._runs[self.rid]["state"], "error")

    def test_stop_sends_process_group_signal(self):
        self.manager._runs[self.rid].update(_pid=123, state="running")
        with patch.object(self.manager, "_exec_script", return_value=("", "")) as execute:
            self.assertTrue(self.manager.stop(self.rid)["ok"])
        self.assertIn("kill -TERM -- -123", execute.call_args.args[1])
        self.assertEqual(self.manager._runs[self.rid]["state"], "cancelling")

    def test_generated_shell_records_its_own_exit_code(self):
        import os
        import shutil
        import uuid
        job = RemoteJob("run_" + uuid.uuid4().hex)
        try:
            with tempfile.TemporaryDirectory(prefix="studio repo ") as td:
                binary = Path(td, "bin"); binary.mkdir()
                fake = binary / "python3"
                fake.write_text('#!/bin/sh\nmkdir -p "$DATA_DIR/datasets"\necho "{}" > "$DATA_DIR/datasets/metadata.json"\nexit 7\n')
                fake.chmod(0o755)
                launch = subprocess.run(["bash", "-c", job.launch(td, "three_as", "spf", 1, 1)],
                                        env={**os.environ, "PATH": str(binary) + os.pathsep + os.environ["PATH"]},
                                        capture_output=True, text=True, timeout=5)
                pid = int(launch.stdout.strip().split("STUDIO_PID=")[-1])
                deadline = time.monotonic() + 5
                while time.monotonic() < deadline:
                    poll = subprocess.run(["bash", "-c", job.poll(pid)], capture_output=True, text=True, timeout=5)
                    status = job.parse(poll.stdout)
                    if status["exit_code"] is not None:
                        break
                    time.sleep(0.02)
                self.assertEqual(status["exit_code"], 7)
                self.assertTrue(status["dataset"])
        finally:
            shutil.rmtree(job.root, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
