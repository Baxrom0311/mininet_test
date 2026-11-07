"""Application inputs fail validation before conversion or engine expansion."""

import copy
import unittest

from lab.apps import expand, parse_bytes, postprocess, validate_app
from lab.mcast import receivers_of, validate_app as validate_multicast
from lab.model import validate_scenario
from lab.topogen import generate
from lab.validation_numbers import MAX_UINT64


class ByteCountTests(unittest.TestCase):
    def test_exact_decimal_units_and_integer_boundaries(self):
        cases = [(1, 1), (1.0, 1), ("1B", 1), (".5KB", 500), ("1.5KB", 1500),
                 ("1.5KiB", 1500), (" 2 mb ", 2000000), ("0.001KB", 1),
                 (MAX_UINT64, MAX_UINT64), (str(MAX_UINT64), MAX_UINT64),
                 ("18446744073709551.615KB", MAX_UINT64)]
        for value, expected in cases:
            with self.subTest(value=value):
                self.assertEqual(parse_bytes(value), expected)

    def test_invalid_counts_never_truncate_or_escape_as_conversion_errors(self):
        values = [0, -1, True, False, 1.5, float("nan"), float("inf"), float("-inf"),
                  None, [], {}, "", "..KB", "1e3", "0B", "-1KB", "0.0001KB",
                  MAX_UINT64 + 1, str(MAX_UINT64 + 1), 10**1000,
                  "18446744073709551614.999999999999999999B"]
        for index, value in enumerate(values):
            with self.subTest(index=index), self.assertRaises(ValueError):
                parse_bytes(value)


class HighLevelValidationTests(unittest.TestCase):
    def test_nonobjects_and_codec_shapes_are_reported(self):
        for value in (None, [], "voip", True):
            with self.subTest(value=value):
                self.assertTrue(validate_app(value, 10))
        for codec in ([], {}, True, 1, "unknown"):
            with self.subTest(codec=codec):
                self.assertTrue(validate_app({"type": "voip", "codec": codec}, 10))
        for codec in (None, "", "g711", "g729"):
            self.assertEqual(validate_app({"type": "voip", "codec": codec}, 10), [])

    def test_http_checks_page_and_think_independently(self):
        for value in (0, -1, True, 1.5, None, float("inf"), "0B"):
            with self.subTest(page=value):
                self.assertTrue(validate_app({"type": "http", "page": value}, 10))
        for value in (0, -1, True, None, "", float("nan"), float("inf"), 10**1000):
            with self.subTest(think_type=type(value).__name__):
                self.assertTrue(validate_app({"type": "http", "think": value}, 10))
        self.assertEqual(len(validate_app({"type": "http", "page": 0, "think": 0}, 10)), 2)
        self.assertEqual(validate_app({"type": "http", "page": "1.5KB", "think": ".25"}, 10), [])

    def test_ftp_checks_file_and_direction_independently(self):
        for value in (0, -1, True, 1.5, None, float("inf"), "0B"):
            with self.subTest(file=value):
                self.assertTrue(validate_app({"type": "ftp", "file": value}, 10))
        for direction in (None, [], {}, True, "sideways"):
            with self.subTest(direction=direction):
                self.assertTrue(validate_app({"type": "ftp", "direction": direction}, 10))
        self.assertEqual(len(validate_app({"type": "ftp", "file": 0, "direction": "bad"}, 10)), 2)
        for direction in ("upload", "download"):
            self.assertEqual(validate_app({"type": "ftp", "file": "1.5MB", "direction": direction}, 10), [])


class MulticastValidationTests(unittest.TestCase):
    def setUp(self):
        self.project = {"devices": {"srv": {"type": "server"}, "h1": {"type": "host"},
                                    "h2": {"type": "host"}, "r1": {"type": "router"}}}
        self.app = {"type": "mcast", "src": "srv", "dst": ["h1", "h2"],
                    "group": "239.1.1.1", "rate": "4Mbps", "size": 1000}

    def test_receiver_syntax_and_copy_preserve_valid_inputs(self):
        self.assertEqual(receivers_of({"dst": " h1, h2, "}), ["h1", "h2"])
        self.assertEqual(receivers_of({"receivers": ["h1"]}), ["h1"])
        self.assertEqual(receivers_of({"dst": None, "receivers": ["h2"]}), ["h2"])
        self.assertEqual(receivers_of({}), [])
        receivers_of(self.app).append("h3")
        self.assertEqual(self.app["dst"], ["h1", "h2"])
        self.assertEqual(validate_multicast(self.project, self.app), [])

    def test_invalid_receiver_shapes_return_errors_without_membership_crashes(self):
        for value in (0, False, {}, {"h1": True}, ("h1",), [None], [[]], [{}], [""], [" "]):
            with self.subTest(receivers=value):
                bad = {**self.app, "dst": value}
                with self.assertRaises(ValueError):
                    receivers_of(bad)
                self.assertTrue(validate_multicast(self.project, bad))
        for value in (None, [], "mcast", True):
            with self.subTest(app=value):
                with self.assertRaises(ValueError):
                    receivers_of(value)
                self.assertTrue(validate_multicast(self.project, value))
        for receivers in ([], "", ["nope"], ["r1"], ["srv"]):
            self.assertTrue(validate_multicast(self.project, {**self.app, "dst": receivers}))

    def test_group_shapes_and_unicast_or_link_local_groups_are_rejected(self):
        for group in (None, [], {}, True, 4009820417, "bad", "10.0.0.1", "224.0.0.5", "ff02::1"):
            with self.subTest(group=group):
                self.assertTrue(validate_multicast(self.project, {**self.app, "group": group}))
        for group in ("224.0.1.0", "239.255.255.255"):
            self.assertEqual(validate_multicast(self.project, {**self.app, "group": group}), [])

    def test_multicast_header_and_udp_payload_bounds(self):
        for size in (20, 20.0, "20", 65507):
            self.assertEqual(validate_multicast(self.project, {**self.app, "size": size}), [])
        for size in (0, 19, 65508, True, 20.5, None, float("inf"), float("nan")):
            with self.subTest(size=size):
                self.assertTrue(validate_multicast(self.project, {**self.app, "size": size}))
        for rate in (None, True, 0, -1, "0bps", ".5bps", float("nan"), float("inf")):
            with self.subTest(rate=rate):
                self.assertTrue(validate_multicast(self.project, {**self.app, "rate": rate}))
        self.assertEqual(validate_multicast(self.project, {**self.app, "rate": "1bps"}), [])

    def test_multicast_rate_fits_packet_timer_resolution(self):
        self.assertTrue(validate_multicast(self.project, {**self.app, "size": 20, "rate": "1000Gbps"}))
        self.assertEqual(validate_multicast(self.project, {**self.app, "size": 1000, "rate": "1000Gbps"}), [])
        self.assertEqual(validate_multicast(self.project, {**self.app, "size": 20, "rate": "160Gbps"}), [])


class ExpandedApplicationValidationTests(unittest.TestCase):
    def test_omitted_model_ids_match_expansion_and_postprocessing(self):
        project = generate("ring", routers=3, hosts=1, flows=0)
        app = {"src": "h0x0", "dst": "h2x0", "start": 0}
        scenario = {"duration": 4, "events": [], "apps": [
            {**app, "id": "base", "type": "udp", "rate": "500kbps"},
            {**app, "type": "voip"}, {**app, "type": "http"}, {**app, "type": "ftp"},
            {**app, "type": "mcast", "group": "239.1.1.1", "rate": "500kbps"}]}
        self.assertEqual(validate_scenario(project, scenario), [])
        primitive = expand(scenario)
        flows = {a["id"]: {**a, "series": [], "summary": {"avg_thr_bps": 500000}}
                 for a in primitive["apps"]}
        result = postprocess({"duration": 4, "flows": flows}, scenario)
        self.assertEqual(set(result["apps"]), {"a2", "a3", "a4", "a5"})
        for app_id in result["apps"]:
            self.assertEqual(result["flows"][app_id]["id"], app_id)
            self.assertTrue(all(part.startswith(app_id + ".") for part in result["flows"][app_id]["parts"]))

    def test_valid_models_expand_to_valid_primitive_scenarios(self):
        project = generate("ring", routers=3, hosts=1, flows=0)
        scenario = {"duration": 4, "events": [], "apps": [
            {"id": "voice", "type": "voip", "codec": "g729", "src": "h0x0", "dst": "h2x0", "start": 0, "stop": 5},
            {"id": "web", "type": "http", "page": "1.5KB", "think": ".5", "src": "h0x0", "dst": "h2x0", "start": 0},
            {"id": "file", "type": "ftp", "file": "0.001MB", "direction": "upload", "src": "h0x0", "dst": "h2x0", "start": 0},
            {"id": "tv", "type": "mcast", "group": "239.1.1.1", "rate": "500kbps", "size": "20",
             "src": "h0x0", "dst": "h1x0, h2x0", "start": 0}]}
        before = copy.deepcopy(scenario)
        self.assertEqual(validate_scenario(project, scenario), [])
        primitive = expand(scenario)
        self.assertEqual(validate_scenario(project, primitive), [])
        self.assertEqual(scenario, before)
        self.assertEqual(sum(a["type"] == "tcp" for a in primitive["apps"]), 9)
        self.assertEqual({a["size"] for a in primitive["apps"] if a.get("mcast")}, {20})
        self.assertTrue(all(a["stop"] == 4 for a in primitive["apps"]))

    def test_multicast_receivers_alias_expands_and_postprocesses(self):
        project = generate("ring", routers=3, hosts=1, flows=0)
        app = {"id": "tv", "type": "mcast", "group": "239.1.1.1", "rate": "500kbps",
               "src": "h0x0", "receivers": ["h1x0", "h2x0"], "start": 0}
        for explicit_null in (False, True):
            with self.subTest(explicit_null=explicit_null):
                scenario = {"duration": 4, "events": [], "apps": [dict(app)]}
                if explicit_null:
                    scenario["apps"][0]["dst"] = None
                self.assertEqual(validate_scenario(project, scenario), [])
                primitive = expand(scenario)
                flows = {a["id"]: {**a, "series": [], "summary": {"avg_thr_bps": 500000}}
                         for a in primitive["apps"]}
                result = postprocess({"duration": 4, "flows": flows}, scenario)
                self.assertEqual(result["flows"]["tv"]["dst"], app["receivers"])
                self.assertEqual(result["apps"]["tv"]["reached_list"], app["receivers"])


if __name__ == "__main__":
    unittest.main()
