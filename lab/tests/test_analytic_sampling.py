"""Fluid-model timing regressions, without packet simulation or privileged networks."""

import copy
import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from lab.engines import analytic
from lab.drivers.demo_driver import Net
from lab.topogen import generate
from lab.server import create_app


def fixture(version=4, duration=2, sample=.1):
    project = generate("ring", routers=3, hosts=1, flows=1, ipv6=True)
    scenario = {"duration": duration, "sample": sample, "ip_version": version,
                "apps": [{"id": "f", "type": "udp", "src": "h0x0", "dst": "h2x0",
                          "rate": "1Mbps", "start": 0, "stop": duration}], "events": []}
    return project, scenario


class AnalyticSamplingTests(unittest.TestCase):
    def test_regular_grid_fractional_tail_and_input_immutability(self):
        project, scenario = fixture(duration=1.1, sample=.2)
        before = copy.deepcopy((project, scenario))
        progress = []
        result = analytic.run(project, scenario, progress.append)
        self.assertEqual(result["sample_s"], .2)
        series = result["flows"]["f"]["series"]
        self.assertEqual(len(series), 6)
        self.assertEqual(series[0]["t_start"], 0)
        self.assertEqual(series[-1]["t_end"], 1.1)
        self.assertAlmostEqual(sum(p["t_end"] - p["t_start"] for p in series), 1.1)
        self.assertTrue(all(a["t_end"] == b["t_start"] for a, b in zip(series, series[1:])))
        grid = [(p["t_start"], p["t_end"]) for p in series]
        for values in (*result["nodes"].values(), *result["links"].values()):
            self.assertEqual([(p["t_start"], p["t_end"]) for p in values], grid)
        self.assertEqual(progress[-1], 1)
        self.assertEqual(progress, sorted(progress))
        self.assertEqual((project, scenario), before)

    def test_fractional_app_on_aligned_grid_is_not_lost(self):
        project, scenario = fixture(duration=1, sample=.1)
        scenario["apps"][0].update(start=.2, stop=.8)
        result = analytic.run(project, scenario)
        series = result["flows"]["f"]["series"]
        self.assertEqual(len(series), 6)
        self.assertAlmostEqual(series[0]["t_start"], .2)
        self.assertAlmostEqual(series[-1]["t_end"], .8)
        self.assertTrue(all(p["thr_bps"] == 1000000 for p in series))

    def test_summary_weights_short_final_outage_by_duration(self):
        project, scenario = fixture(duration=1.25, sample=1)
        edge = next(lid for lid, link in project["links"].items()
                    if "h0x0" in (link["a"]["dev"], link["b"]["dev"]))
        scenario["events"] = [{"t": 1, "action": "link_down", "link": edge}]
        flow = analytic.run(project, scenario)["flows"]["f"]
        self.assertEqual(flow["summary"]["avg_thr_bps"], 800000)
        self.assertEqual(flow["summary"]["loss_pct"], 20)

    def test_fractional_ospf_hold_and_actual_event_time_both_families(self):
        for version in (4, 6):
            with self.subTest(version=version):
                project, scenario = fixture(version)
                cut = next(lid for lid, link in project["links"].items()
                           if {link["a"]["dev"], link["b"]["dev"]} == {"r0", "r2"})
                scenario.update(ospf_convergence_s=.3, events=[{"t": .25, "action": "link_down", "link": cut}])
                result = analytic.run(project, scenario)
                points = {round(p["t"], 4): p for p in result["flows"]["f"]["series"]}
                self.assertEqual(result["events"][0]["t"], .25)
                self.assertAlmostEqual(result["events"][0]["actual_t"], .3)
                self.assertGreater(points[.3]["thr_bps"], 0)
                for t in (.4, .5, .6):
                    self.assertEqual(points[t]["thr_bps"], 0)
                self.assertGreater(points[.7]["thr_bps"], 0)
                self.assertTrue(any(e["dev"] == "r0" and e["t"] == .6 for e in result["route_events"]))

    def test_missing_sample_and_explicit_one_preserve_aligned_behavior(self):
        project, scenario = fixture(duration=3, sample=1)
        default = dict(scenario)
        default.pop("sample")
        a, b = analytic.run(project, default), analytic.run(project, scenario)
        for field in ("flows", "links", "nodes", "events", "route_events"):
            self.assertEqual(a[field], b[field])

    def test_rip_deadline_does_not_add_a_tick_for_float_roundoff(self):
        project, scenario = fixture(duration=1.2, sample=.1)
        for device in project["devices"].values():
            if device["type"] == "router":
                device["config"]["daemons"] = ["ripd"]
        cut = next(lid for lid, link in project["links"].items()
                   if {link["a"]["dev"], link["b"]["dev"]} == {"r0", "r2"})
        scenario.update(rip_convergence_s=.2, events=[{"t": .7, "action": "link_down", "link": cut}])
        result = analytic.run(project, scenario)
        points = {round(p["t"], 4): p for p in result["flows"]["f"]["series"]}
        self.assertEqual(points[.9]["thr_bps"], 0)
        self.assertGreater(points[1.0]["thr_bps"], 0)
        self.assertTrue(any(e["dev"] == "r0" and e["t"] == .9 for e in result["route_events"]))

    def test_bgp_restore_deadline_does_not_add_a_tick_for_float_roundoff(self):
        path = Path(__file__).resolve().parents[1] / "examples/bgp_lab.json"
        project = json.loads(path.read_text())
        project["links"]["l4"]["up"] = False
        scenario = {"duration": 1.2, "sample": .1, "bgp_convergence_s": .2,
                    "apps": [{"id": "f", "type": "udp", "src": "pc1", "dst": "srv1",
                              "rate": "1Mbps", "start": 0, "stop": 1.2}],
                    "events": [{"t": .7, "action": "link_up", "link": "l4"}]}
        result = analytic.run(project, scenario)
        points = {round(p["t"], 4): p for p in result["flows"]["f"]["series"]}
        self.assertEqual(points[.9]["thr_bps"], 0)
        self.assertGreater(points[1.0]["thr_bps"], 0)

    def test_lag_midpoint_release_uses_tolerant_deadline(self):
        path = Path(__file__).resolve().parents[1] / "examples/lag_lab.json"
        project = json.loads(path.read_text())
        net = Net(project)
        net.flow_key = ("h1", "h3", "f1")
        hops = net.forward("h1", net.ip_of("h3").ip)[3]
        cut = next(lid for lid, direction in hops if lid in ("l5", "l6"))
        scenario = {"duration": 1, "sample": .1, "bond_failover_s": .05,
                    "apps": [{"id": "f1", "type": "udp", "src": "h1", "dst": "h3",
                              "rate": "1Mbps", "start": 0, "stop": 1}],
                    "events": [{"t": .7, "action": "link_down", "link": cut}]}
        points = {round(p["t"], 4): p for p in analytic.run(project, scenario)["flows"]["f1"]["series"]}
        self.assertGreater(points[.8]["thr_bps"], 0)

    def test_bounded_tcp_transfer_conserves_bytes_across_sample_sizes(self):
        done = []
        for sample in (1, .2, .05):
            project, scenario = fixture(duration=2, sample=sample)
            scenario["apps"][0].update(type="tcp", rate="10Mbps", bytes=100000)
            flow = analytic.run(project, scenario)["flows"]["f"]
            self.assertIsNotNone(flow["done_t"])
            done.append(flow["done_t"])
            delivered = sum(p["thr_bps"] * (p["t_end"] - p["t_start"]) / 8 for p in flow["series"])
            self.assertAlmostEqual(delivered, 100000, delta=1)
        self.assertEqual(done, [done[0]] * len(done))

    def test_sampling_validation_blocks_unsupported_work(self):
        project, scenario = fixture()
        for sample in (0, -1, True, None, "0.1", float("nan"), float("inf"), .0001, 3601, 10**500):
            with self.subTest(sample=sample), self.assertRaises(ValueError):
                analytic.run(project, {**scenario, "sample": sample})
        with self.assertRaisesRegex(ValueError, "100000"):
            analytic.run(project, {**scenario, "sample": .001, "duration": 3600})

    def test_overload_drop_estimates_are_conserved_at_millisecond_steps(self):
        for sample in (1, .1, .01, .001):
            with self.subTest(sample=sample):
                project, scenario = fixture(duration=1, sample=sample)
                edge = next(lid for lid, link in project["links"].items()
                            if "h0x0" in (link["a"]["dev"], link["b"]["dev"]))
                project["links"][edge]["params"]["bw"] = 1
                scenario["apps"][0]["rate"] = "2Mbps"
                result = analytic.run(project, scenario)
                series = result["links"][edge]
                drops = sum(p["drops_ab"] + p["drops_ba"] for p in series)
                self.assertAlmostEqual(drops, 125)
                self.assertAlmostEqual(sum(p["discards"] for p in result["nodes"]["h0x0"]), 125)
                self.assertEqual(result["flows"]["f"]["summary"]["loss_pct"], 50)

    def test_roaming_outage_remains_one_second_at_fine_sampling(self):
        path = Path(__file__).resolve().parents[1] / "examples/wifi_roaming.json"
        project = json.loads(path.read_text())
        scenario = {"duration": 2, "sample": .1, "apps": [], "events": [
            {"t": .2, "action": "move", "device": "mob1", "x": 700, "y": 360, "duration": 0}]}
        events = analytic.run(project, scenario)["events"]
        roaming = [event for event in events if event["action"] == "roam"]
        self.assertTrue(roaming)
        self.assertAlmostEqual(roaming[0]["t"], 1.2)


class AnalyticSamplingApiTests(unittest.TestCase):
    def test_queued_replicas_and_window_reports_use_actual_fine_grid(self):
        project, scenario = fixture(duration=1.1, sample=.25)
        project["scenarios"] = {"fine": scenario}
        with tempfile.TemporaryDirectory() as directory, patch("lab.engines.manager.RESULTS_DIR", directory), \
                TestClient(create_app("demo", token="sampling")) as client:
            client.cookies.set("netlab_token", "sampling")
            response = client.post("/api/experiments", json={"project": project, "scenario": "fine",
                                                            "engine": "analytic", "repetitions": 2})
            self.assertEqual(response.status_code, 200, response.text)
            batch = response.json()
            base = f"/api/experiments/{project['name']}/{batch['experiment_id']}"
            deadline = time.monotonic() + 10
            while not client.get(base).json()["complete"]:
                self.assertLess(time.monotonic(), deadline)
                time.sleep(.01)
            response = client.get(base + "?start_s=.25&end_s=1.1")
            self.assertEqual(response.status_code, 200, response.text)
            summary = response.json()
            for window in summary["run_windows"]:
                self.assertEqual(window["sample_s"], .25)
                flow = window["flows"]["f"]
                self.assertEqual(flow["expected_bins"], 4)
                self.assertAlmostEqual(flow["expected_s"], .85)
                self.assertTrue(flow["metrics"]["avg_thr_bps"]["complete"])
            metric = next(m for m in summary["metrics"] if m["metric"] == "avg_thr_bps")
            self.assertEqual(metric["mean"], 1000000)
            self.assertIsNotNone(metric["ci95"])
            rid = batch["run_ids"][0]
            run = client.get(f"/api/runs/{project['name']}/{rid}").json()
            self.assertEqual(run["sample_s"], .25)
            self.assertEqual(len(run["flows"]["f"]["series"]), 5)


if __name__ == "__main__":
    unittest.main()
