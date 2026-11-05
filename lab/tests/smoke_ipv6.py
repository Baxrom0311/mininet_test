"""IPv6 model + real ns-3 packets (no host IPv6 needed).

python -m unittest lab.tests.smoke_ipv6 -v
NETLAB_SKIP_NS3=1 skips only packet simulations, never the model tests.
"""

import copy
import importlib.util
import ipaddress
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from lab.addressing import project_for_family
from lab.benchmarks.real_scale import process_usage
from lab.drivers.base import EventBus
from lab.drivers.demo_driver import DemoDriver, FakeShell, Net
from lab.engines import analytic
from lab.model import validate_project, validate_scenario
from lab.topogen import generate


def fixture():
    p = generate("ring", routers=3, hosts=1, flows=1, ipv6=True)
    source, dest = "h0x0", "h2x0"
    cut = next(lid for lid, l in p["links"].items() if source in (l["a"]["dev"], l["b"]["dev"]))
    sc = {"duration": 12, "ip_version": 6, "apps": [
        {"id": "video", "type": "udp", "src": source, "dst": dest, "rate": "500kbps", "start": 2, "stop": 12},
        {"id": "bulk", "type": "tcp", "src": source, "dst": dest, "rate": "1Mbps", "start": 2, "stop": 12},
        {"id": "echo", "type": "ping", "src": dest, "dst": source, "start": 2, "stop": 12}],
        "events": [{"t": 5, "action": "link_down", "link": cut}, {"t": 8, "action": "link_up", "link": cut}]}
    return p, sc


class ModelTests(unittest.TestCase):
    def setUp(self):
        self.project, self.sc = fixture()

    def test_dual_stack_validation_and_selection(self):
        before = copy.deepcopy(self.project)
        self.assertEqual(validate_project(self.project), [])
        self.assertEqual(validate_scenario(self.project, self.sc), [])
        for version in (4, 6):
            n = Net(self.project, ip_version=version)
            ip = n.ip_of("h2x0").ip
            self.assertEqual(ip.version, version)
            ok, why, hops, *_ = n.forward("h0x0", ip)
            self.assertTrue(ok, why)
            self.assertTrue(n.reply("h0x0", n.ip_of("h0x0").ip, "h2x0", ip, hops)[0])
        self.assertEqual(before, self.project)

    def test_static_ipv6_routes(self):
        p = project_for_family(self.project, 6)
        n = Net(p)
        routes = {name: n.routes(name) for name in p["devices"]}
        for name, d in p["devices"].items():
            d["config"]["daemons"] = []
            d["config"]["routes"] = [{"prefix": str(net), "via": str(via)}
                                      for net, via, _ifn, _proto in routes[name] if via]
        n = Net(p)
        self.assertTrue(n.forward("h0x0", n.ip_of("h2x0").ip)[0])
        for d in p["devices"].values():
            if d["type"] == "router":
                d["config"]["routes"] = []
        n = Net(p)
        self.assertFalse(n.forward("h0x0", n.ip_of("h2x0").ip)[0])

    def test_ipv4_ospf_does_not_supply_ipv6_routes(self):
        for d in self.project["devices"].values():
            if d["type"] == "router":
                d["config"]["daemons"] = ["ospfd"]
        n = Net(self.project, ip_version=6)
        self.assertFalse(n.forward("h0x0", n.ip_of("h2x0").ip)[0])

    def test_link_failure_and_recovery(self):
        before = copy.deepcopy(self.project)
        result = analytic.run(self.project, self.sc)
        points = {p["t"]: p for p in result["flows"]["video"]["series"]}
        self.assertGreater(points[4]["thr_bps"], 400000)
        self.assertEqual(points[7]["thr_bps"], 0)
        self.assertGreater(points[11]["thr_bps"], 400000)
        self.assertTrue(result["route_events"])
        self.assertEqual(before, self.project)

    def test_invalid_ipv6_and_mixed_route_families(self):
        cfg = self.project["devices"]["h0x0"]["config"]
        cfg["interfaces"]["h0x0-eth1"]["ip6"] = "10.0.0.1/24"
        cfg["gateway6"] = "192.0.2.1"
        cfg["routes"] = [{"prefix": "fd00::/64", "via": "192.0.2.1"}]
        self.assertGreaterEqual(len(validate_project(self.project)), 3)

    def test_missing_ipv6_endpoint_rejected(self):
        for ic in self.project["devices"]["h0x0"]["config"]["interfaces"].values():
            ic.pop("ip6")
        self.assertTrue(any("IPv6" in e for e in validate_scenario(self.project, self.sc)))

    def test_explicit_destination_family_rejected(self):
        self.sc["apps"][0]["dst_ip"] = "192.0.2.1"
        self.assertTrue(any("dst_ip" in e for e in validate_scenario(self.project, self.sc)))

    def test_model_crosses_more_than_sixteen_routers(self):
        # A chain avoids ring shortcuts and checks that the former 16-hop cap is gone.
        p = generate("ring", routers=20, hosts=1, flows=1, ipv6=True)
        for lid, link in list(p["links"].items()):
            a, b = link["a"]["dev"], link["b"]["dev"]
            if a.startswith("r") and b.startswith("r") and abs(int(a[1:]) - int(b[1:])) != 1:
                del p["links"][lid]
        n = Net(p, ip_version=6)
        hosts = [k for k, d in p["devices"].items() if d["type"] == "host"]
        ok, why, hops, *_ = n.forward(hosts[0], n.ip_of(hosts[-1]).ip)
        self.assertTrue(ok, why)
        self.assertGreaterEqual(len(hops), 20)

    def test_resource_sampler_reads_current_process(self):
        usage, ticks = process_usage({os.getpid()})
        self.assertGreater(usage["rss_bytes"], 0)
        self.assertGreater(usage["threads"], 0)
        self.assertEqual(len(ticks), 1)

    def test_demo_ipv6_route_command(self):
        driver = DemoDriver(EventBus())
        driver._set_project(self.project)
        shell = FakeShell(driver, "h0x0", "default")
        text = driver.shell_exec(shell, "h0x0", "default", "ip -6 route")
        self.assertIn("::/0 via fd00:", text)
        self.assertNotIn("0.0.0.0/0", text)

    def test_motion_keeps_original_coordinates(self):
        path = Path(__file__).resolve().parents[1] / "examples/wifi_kampus.json"
        p = json.loads(path.read_text())
        before = copy.deepcopy(p)
        result = analytic.run(p, p["scenarios"]["yurish"])
        points = {int(x["t"]): x["thr_bps"] / 1e6 for x in result["flows"]["m"]["series"]}
        self.assertGreater(points[4], 50)
        self.assertGreater(points[22], 5)
        self.assertLess(points[22], 50)
        self.assertLess(points[38], 0.1)
        self.assertEqual(p, before)


@unittest.skipUnless(importlib.util.find_spec("ns3") and os.environ.get("NETLAB_SKIP_NS3") != "1",
                     "ns-3 unavailable or explicitly skipped")
class PacketTests(unittest.TestCase):
    def test_ipv6_and_ipv4_packets_and_recovery(self):
        p, sc = fixture()
        for version in (6, 4):
            with self.subTest(ip_version=version), tempfile.TemporaryDirectory(prefix="netlab-ipv6-") as td:
                inp, out = Path(td, "in.json"), Path(td, "out.json")
                inp.write_text(json.dumps({"project": p, "scenario": {**sc, "ip_version": version}}))
                r = subprocess.run([sys.executable, "-m", "lab.engines.ns3_runner", str(inp), str(out)],
                                   capture_output=True, text=True, timeout=120)
                self.assertEqual(r.returncode, 0, (r.stdout + r.stderr)[-3000:])
                result = json.loads(out.read_text())
                self.assertEqual(result["ip_version"], version)
                for name in ("video", "bulk"):
                    self.assertGreater(result["flows"][name]["summary"]["rx_pkts"], 0)
                self.assertIsNotNone(result["flows"]["echo"]["summary"]["avg_rtt_ms"])
                points = {p["t"]: p for p in result["flows"]["video"]["series"]}
                self.assertGreater(points[4]["thr_bps"], 300000)
                self.assertEqual(points[7]["thr_bps"], 0)
                self.assertGreater(points[11]["thr_bps"], 300000)
                self.assertTrue(result["route_events"])
                self.assertTrue(any(point["fwd_pps"] > 0 for name, series in result["nodes"].items()
                                    if name.startswith("r") for point in series))


if __name__ == "__main__":
    unittest.main()
