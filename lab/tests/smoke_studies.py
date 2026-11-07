"""Actual queued ns-3 parameter studies: IPv4 2D and IPv6 1D, six packet runs."""

import copy
import importlib.util
import json
import os
import tempfile
import time
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from lab.server import create_app
from lab.topogen import generate


@unittest.skipIf(os.environ.get("NETLAB_SKIP_NS3") == "1" or
                 not (importlib.util.find_spec("ns") or importlib.util.find_spec("ns3")), "ns-3 unavailable/disabled")
class PacketStudies(unittest.TestCase):
    def fixture(self, version):
        project = generate("ring", routers=3, hosts=1, flows=1, ipv6=True)
        edge = next(lid for lid, link in project["links"].items()
                    if "h0x0" in (link["a"]["dev"], link["b"]["dev"]))
        project["scenarios"] = {"study": {"duration": 3.5, "sample": .4, "ip_version": version,
            "apps": [{"id": "video", "type": "udp", "src": "h0x0", "dst": "h2x0", "rate": "1Mbps",
                      "start": .5, "stop": 3.5}], "events": []}}
        return project, edge

    def records(self, client, project, ids):
        records = []
        for rid in ids:
            response = client.get(f"/api/runs/{project}/{rid}")
            self.assertEqual(response.status_code, 200, response.text)
            records.append(response.json())
        return records

    def wait(self, client, project, plan):
        base = f"/api/studies/{project}/{plan['study_id']}"
        deadline = time.monotonic() + 120
        state = None
        while time.monotonic() < deadline:
            response = client.get(base)
            self.assertEqual(response.status_code, 200, response.text)
            state = response.json()
            self.assertEqual(state["planned_runs"], plan["runs"], state)
            self.assertEqual(state["observed_runs"], plan["runs"], state)
            self.assertEqual(state["missing_run_ids"], [], state)
            self.assertEqual(state["invalid_run_ids"], [], state)
            if state["complete"]:
                return state
            if state["status"] in ("failed", "cancelled", "interrupted", "incomplete"):
                break
            time.sleep(.1)
        records = self.records(client, project, plan["run_ids"])
        details = [{key: record.get(key) for key in ("run_id", "status", "error", "error_notes", "study_position")}
                   for record in records]
        self.fail("packet study did not complete: " + json.dumps({"state": state, "runs": details}, ensure_ascii=False))

    def assert_flow(self, run, version, plan, position):
        self.assertEqual(run["status"], "done", run)
        self.assertEqual(run["ip_version"], version)
        self.assertEqual(run["study_id"], plan["study_id"])
        self.assertEqual(run["study_position"], position)
        self.assertEqual(run["study_total"], plan["runs"])
        flow = run["flows"]["video"]
        self.assertGreater(flow["summary"]["avg_thr_bps"], 100000)
        self.assertEqual(len(flow["series"]), 9)
        self.assertEqual(flow["series"][-1]["t_end"], 3.5)
        self.assertTrue(all(0 <= point["t_start"] < point["t_end"] <= 3.5 for point in flow["series"]))

    def test_ipv4_two_dimensional_bandwidth_and_rate_study(self):
        project, edge = self.fixture(4)
        before = copy.deepcopy(project)
        bandwidths, rates = [.2, 2], [.3, .6]
        body = {"project": project, "scenario": "study", "engine": "ns3",
                "param": {"kind": "link", "link": edge, "field": "bw"}, "values": bandwidths,
                "param2": {"kind": "app", "app": "video", "field": "rate"}, "values2": rates}
        with tempfile.TemporaryDirectory() as directory, patch("lab.engines.manager.RESULTS_DIR", directory), \
                TestClient(create_app("demo")) as client:
            response = client.post("/api/studies", json=body)
            self.assertEqual(response.status_code, 200, response.text)
            plan = response.json()
            self.assertEqual(plan["runs"], 4)
            self.assertEqual(len(set(plan["run_ids"])), 4)
            state = self.wait(client, project["name"], plan)
            self.assertTrue(state["settled"], state)
            self.assertEqual(state["statuses"], {"done": 4})
            runs = self.records(client, project["name"], plan["run_ids"])
            measured = {}
            for position, run in enumerate(runs):
                self.assert_flow(run, 4, plan, position)
                i, j = position % 2, position // 2
                self.assertEqual((run["study_index"], run["study_index2"]), (i, j))
                self.assertEqual((run["study_value"], run["study_value2"]), (bandwidths[i], rates[j]))
                self.assertEqual(run["scenario"]["apps"][0]["rate"], f"{rates[j]}Mbps")
                captured = run["reproducibility"]["project"]
                self.assertEqual(captured["links"][edge]["params"]["bw"], bandwidths[i])
                self.assertEqual(captured["scenarios"]["study"]["apps"][0]["rate"], f"{rates[j]}Mbps")
                measured[i, j] = run["flows"]["video"]["summary"]["avg_thr_bps"]
            self.assertGreater(measured[1, 1], measured[0, 1] + 200000, measured)
            self.assertGreater(measured[1, 1], measured[1, 0] + 100000, measured)
            response = client.get(f"/api/studies/{project['name']}/{plan['study_id']}/report.html")
            self.assertEqual(response.status_code, 200, response.text)
            self.assertIn('class="heatmap"', response.text)
            self.assertIn("Issiqlik xaritalari", response.text)
        self.assertEqual(project, before)

    def test_ipv6_one_dimensional_rate_study(self):
        project, edge = self.fixture(6)
        values = ["300kbps", "600kbps"]
        with tempfile.TemporaryDirectory() as directory, patch("lab.engines.manager.RESULTS_DIR", directory), \
                TestClient(create_app("demo")) as client:
            response = client.post("/api/studies", json={"project": project, "scenario": "study", "engine": "ns3",
                "param": {"kind": "app", "app": "video", "field": "rate"}, "values": values})
            self.assertEqual(response.status_code, 200, response.text)
            plan = response.json()
            self.assertEqual(plan["runs"], 2)
            self.assertEqual(len(set(plan["run_ids"])), 2)
            state = self.wait(client, project["name"], plan)
            self.assertEqual(state["statuses"], {"done": 2})
            runs = self.records(client, project["name"], plan["run_ids"])
            measured = []
            for position, run in enumerate(runs):
                self.assert_flow(run, 6, plan, position)
                self.assertEqual(run["study_index"], position)
                self.assertEqual(run["study_value"], values[position])
                self.assertEqual(run["scenario"]["apps"][0]["rate"], values[position])
                self.assertEqual(run["reproducibility"]["project"]["links"][edge]["params"]["bw"],
                                 project["links"][edge]["params"]["bw"])
                measured.append(run["flows"]["video"]["summary"]["avg_thr_bps"])
            self.assertGreater(measured[1], measured[0] + 100000, measured)
            response = client.get(f"/api/studies/{project['name']}/{plan['study_id']}/report.html")
            self.assertEqual(response.status_code, 200, response.text)
            self.assertIn("polyline", response.text)


if __name__ == "__main__":
    unittest.main()
