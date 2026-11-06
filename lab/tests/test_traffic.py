import copy
import math
import time
import unittest

from fastapi.testclient import TestClient

from lab.engines.analytic import run
from lab.model import parse_rate
from lab.server import create_app
from lab.topogen import generate
from lab.traffic import import_matrix


class TrafficTests(unittest.TestCase):
    def setUp(self):
        self.project = generate("ring", routers=3, hosts=1, flows=1, ipv6=True)

    def test_import_and_run_1000_flows(self):
        text = "src,dst,rate\n" + "h0x0,h2x0,10kbps\n" * 1000
        before = copy.deepcopy(self.project)
        start = time.perf_counter()
        matrix = import_matrix(self.project, {"csv": text, "duration": 2})
        result = run(self.project, matrix["scenario"])
        elapsed = time.perf_counter() - start
        self.assertEqual(len(result["flows"]), 1000)
        self.assertTrue(all(f["summary"]["avg_thr_bps"] > 9000 for f in result["flows"].values()))
        self.assertEqual(self.project, before)
        self.assertLess(elapsed, 5, f"1000-flow 2-second scenario took {elapsed:.3f}s")
        print(f"1000-flow import + analytic: {elapsed:.3f}s", flush=True)

    def test_gravity_conserves_total_and_weight_ratio(self):
        opts = {"mode": "gravity", "total_mbps": 30, "weights": {"h0x0": 1, "h1x0": 2, "h2x0": 3}}
        matrix = import_matrix(self.project, opts)
        self.assertEqual(matrix, import_matrix(self.project, opts))
        self.assertEqual(matrix["count"], 6)
        self.assertTrue(math.isclose(matrix["offered_bps"], 30e6))
        rates = {(a["src"], a["dst"]): parse_rate(a["rate"]) for a in matrix["scenario"]["apps"]}
        self.assertTrue(math.isclose(rates["h0x0", "h2x0"] / rates["h0x0", "h1x0"], 1.5))

    def test_bad_input_does_not_create_partial_matrix(self):
        for text in ("src,dst,rate\nh0x0,h2x0,nan\n", "src,dst,rate\nh0x0,unknown,1Mbps\n",
                     "src,dst,rate,id\nh0x0,h2x0,1Mbps,a\nh0x0,h2x0,1Mbps,a\n",
                     "src,dst,rate\nh0x0,h2x0,-1\n", "src,dst,rate\nh0x0,h2x0\n"):
            with self.subTest(text=text), self.assertRaises(ValueError):
                import_matrix(self.project, {"csv": text})

    def test_large_rate_round_trip(self):
        result = import_matrix(self.project, {"csv": "src,dst,rate\nh0x0,h2x0,100Mbps\n"})
        self.assertEqual(parse_rate(result["scenario"]["apps"][0]["rate"]), 100e6)

    def test_api_ipv6_and_invalid_gravity(self):
        with TestClient(create_app("demo")) as client:
            response = client.post("/api/traffic/import", json={"project": self.project, "mode": "gravity", "ip_version": 6})
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()["scenario"]["ip_version"], 6)
            response = client.post("/api/traffic/import", json={"project": self.project, "mode": "gravity", "weights": {"x": 1}})
            self.assertEqual(response.status_code, 400)


if __name__ == "__main__":
    unittest.main()
