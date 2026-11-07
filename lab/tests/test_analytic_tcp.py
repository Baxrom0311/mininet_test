"""Public fluid-engine regressions for many flows and finite TCP transfers."""

import copy
import unittest

from lab.engines import analytic
from lab.topogen import generate


def project(bandwidth=100):
    result = generate("ring", routers=3, hosts=1, flows=0)
    for link in result["links"].values():
        link["params"]["bw"] = bandwidth
    return result


def app(identifier, **values):
    return {"id": identifier, "type": "tcp", "src": "h0x0", "dst": "h2x0",
            "start": 0, "stop": 1, **values}


def run(network, applications):
    return analytic.run(network, {"duration": 1, "apps": applications, "events": []})


def received_bytes(flow):
    return sum(point["thr_bps"] * (point["t_end"] - point["t_start"]) / 8
               for point in flow["series"])


class AnalyticTcpTests(unittest.TestCase):
    def test_more_than_32_finite_transfers_finish_within_one_bin(self):
        applications = [app(f"f{i}", bytes=1000 * i) for i in range(1, 65)]
        flows = run(project(), applications)["flows"]
        self.assertEqual(sum(flow["done_t"] is not None for flow in flows.values()), 64)
        for application in applications:
            flow = flows[application["id"]]
            self.assertLess(flow["done_t"], 1)
            self.assertAlmostEqual(received_bytes(flow), application["bytes"], delta=.25)

    def test_more_than_64_rate_cap_levels_use_available_capacity(self):
        # The sum of all requested rates is below the 10 Gbps path capacity.
        applications = [app(f"f{i}", rate=f"{i}Mbps") for i in range(1, 101)]
        flows = run(project(10000), applications)["flows"]
        for i in range(1, 101):
            self.assertEqual(flows[f"f{i}"]["summary"]["avg_thr_bps"], i * 1000000)
        self.assertEqual(sum(flow["summary"]["avg_thr_bps"] for flow in flows.values()), 5050000000)

    def test_micro_transfers_never_deliver_more_than_the_requested_bytes(self):
        flows = run(project(10000), [app("one", bytes=1), app("two", bytes=2)])["flows"]
        for identifier, size in (("one", 1), ("two", 2)):
            flow = flows[identifier]
            self.assertIsNotNone(flow["done_t"])
            self.assertAlmostEqual(received_bytes(flow), size, delta=.25)

    def test_application_order_does_not_change_allocations_or_completions(self):
        network = project()
        applications = [app(f"f{i}", bytes=5000 * i, rate=f"{i}Mbps") for i in range(1, 9)] + [app("bulk")]
        before = copy.deepcopy((network, applications))
        forward = run(network, applications)["flows"]
        reverse = run(network, list(reversed(applications)))["flows"]
        for identifier in forward:
            self.assertAlmostEqual(received_bytes(forward[identifier]), received_bytes(reverse[identifier]), delta=.25)
            self.assertEqual(forward[identifier].get("done_t"), reverse[identifier].get("done_t"))
        self.assertEqual((network, applications), before)

    def test_tcp_shares_only_the_capacity_left_by_udp(self):
        network = project(10)
        applications = [app("udp", type="udp", rate="4Mbps"), app("a"), app("b"), app("c")]
        result = run(network, applications)
        self.assertEqual(result["flows"]["udp"]["summary"]["avg_thr_bps"], 4000000)
        for identifier in ("a", "b", "c"):
            self.assertEqual(result["flows"][identifier]["summary"]["avg_thr_bps"], 2000000)
        edge = next(lid for lid, link in network["links"].items()
                    if "h0x0" in (link["a"]["dev"], link["b"]["dev"]))
        for point in result["links"][edge]:
            self.assertLessEqual(max(point["ab_bps"], point["ba_bps"]), 10000000)

    def test_completed_file_releases_its_share_to_an_ongoing_stream(self):
        network = project(10)
        result = run(network, [app("file", bytes=100000), app("bulk")])
        file, stream = result["flows"]["file"], result["flows"]["bulk"]
        self.assertIsNotNone(file["done_t"])
        self.assertLess(file["done_t"], 1)
        self.assertAlmostEqual(received_bytes(file), 100000, delta=.25)
        self.assertGreater(stream["summary"]["avg_thr_bps"], 5000000)
        self.assertLessEqual(stream["summary"]["avg_thr_bps"], 10000000)

    def test_saturated_udp_does_not_create_tcp_bytes_or_completion(self):
        result = run(project(10), [app("udp", type="udp", rate="12Mbps"), app("file", bytes=100000)])
        self.assertEqual(result["flows"]["udp"]["summary"]["avg_thr_bps"], 10000000)
        self.assertEqual(received_bytes(result["flows"]["file"]), 0)
        self.assertIsNone(result["flows"]["file"]["done_t"])


if __name__ == "__main__":
    unittest.main()
