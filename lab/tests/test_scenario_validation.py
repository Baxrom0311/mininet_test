import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from lab.apps import expand
from lab.model import parse_rate, validate_scenario
from lab.server import create_app
from lab.topogen import generate


def project_with_scenario():
    project = generate("ring", routers=3, hosts=1, flows=0, ipv6=True)
    project["scenarios"] = {"test": {"duration": 3, "sample": 1, "events": [], "apps": [
        {"id": "flow", "type": "udp", "src": "h0x0", "dst": "h2x0", "rate": "1Mbps",
         "size": 1000, "start": 0, "stop": 3}
    ]}}
    return project


class ScenarioValidationTests(unittest.TestCase):
    def setUp(self):
        self.project = project_with_scenario()
        self.scenario = self.project["scenarios"]["test"]

    def assert_invalid(self, scenario):
        errors = validate_scenario(self.project, scenario)
        self.assertIsInstance(errors, list)
        self.assertTrue(errors, "invalid input was accepted")
        self.assertTrue(all(isinstance(error, str) for error in errors))

    def app_with(self, **changes):
        scenario = copy.deepcopy(self.scenario)
        scenario["apps"][0].update(changes)
        return scenario

    def test_scenario_and_collection_shapes_report_errors(self):
        for value in (None, [], "scenario", 1, True):
            with self.subTest(scenario=value):
                self.assert_invalid(value)
        for field in ("apps", "events"):
            for value in ({}, {"unexpected": []}, "items", 1, True, [None], [1], ["item"], [[]]):
                with self.subTest(field=field, value=value):
                    self.assert_invalid({**self.scenario, field: value})

    def test_application_ids_and_endpoints_do_not_require_hashable_input(self):
        for field in ("id", "src", "dst"):
            for value in ([], {}, 1, True):
                with self.subTest(field=field, value=value):
                    self.assert_invalid(self.app_with(**{field: value}))
        for field in ("src", "dst"):
            self.assert_invalid(self.app_with(**{field: "missing-device"}))

    def test_automatic_ids_are_distinct_and_collisions_are_rejected(self):
        scenario = copy.deepcopy(self.scenario)
        scenario["apps"][0].pop("id")
        scenario["apps"].append(copy.deepcopy(scenario["apps"][0]))
        self.assertEqual(validate_scenario(self.project, scenario), [])
        scenario["apps"][1]["id"] = "a1"
        self.assert_invalid(scenario)
        scenario["apps"][0]["id"] = "duplicate"
        scenario["apps"][1]["id"] = "duplicate"
        self.assert_invalid(scenario)

    def test_high_level_expansion_reserves_generated_ids(self):
        voice = {**self.scenario["apps"][0], "id": "v", "type": "voip"}
        raw = {**self.scenario["apps"][0], "id": "v.up"}
        self.assert_invalid({**self.scenario, "apps": [voice, raw]})
        multicast = {**self.scenario["apps"][0], "id": "tv", "type": "mcast",
                     "group": "239.1.1.1", "dst": ["h1x0", "h1x0"]}
        self.assert_invalid({**self.scenario, "apps": [multicast]})

    def test_expanded_application_budget_counts_http_pages(self):
        http = {**self.scenario["apps"][0], "type": "http", "think": 1}
        with patch("lab.scenario_validation.MAX_EXPANDED_APPS", 2):
            self.assert_invalid({**self.scenario, "apps": [http]})
            self.assertEqual(validate_scenario(self.project, {**self.scenario, "apps": [{**http, "think": 1.5}]}), [])
            apps = [self.scenario["apps"][0], {**self.scenario["apps"][0], "id": "other"}]
            self.assertEqual(validate_scenario(self.project, {**self.scenario, "apps": apps}), [])

    def test_expanded_multicast_metadata_is_validated_before_native_use(self):
        multicast = {**self.scenario["apps"][0], "id": "tv", "type": "mcast",
                     "group": "239.1.1.1", "dst": ["h1x0", "h2x0"]}
        expanded = expand({**self.scenario, "apps": [multicast]})
        self.assertEqual(validate_scenario(self.project, expanded), [])
        for field, values in (("mcast", ([], {}, 1, True, "")),
                              ("mgroup", ([], {}, "10.0.0.1", "ff02::1"))):
            for value in values:
                bad = copy.deepcopy(expanded)
                bad["apps"][0][field] = value
                with self.subTest(field=field, value=value):
                    self.assert_invalid(bad)
        self.assert_invalid({**expanded, "ip_version": 6})

    def test_duration_sample_and_random_streams_are_bounded_numbers(self):
        for field, values in (
            ("duration", (True, False, 0, 3601, float("nan"), float("inf"), 10 ** 1000)),
            ("sample", (True, 0, float("nan"), float("inf"), 10 ** 1000)),
            ("seed", (True, 0, 1.5, "1", 2147483648)),
            ("rng_run", (False, 0, 1.5, "1", 2147483648)),
        ):
            for value in values:
                with self.subTest(field=field, value=value):
                    self.assert_invalid({**self.scenario, field: value})

    def test_application_windows_are_finite_and_start_during_scenario(self):
        for field in ("start", "stop"):
            for value in (True, float("nan"), float("inf"), float("-inf"), 10 ** 1000):
                with self.subTest(field=field, value=value):
                    self.assert_invalid(self.app_with(**{field: value}))
        for changes in ({"start": 3, "stop": 4}, {"start": -1}, {"start": 1, "stop": 1}, {"stop": 0}):
            with self.subTest(changes=changes):
                self.assert_invalid(self.app_with(**changes))
        # Engines clamp a finite stop beyond the scenario end; this remains valid.
        self.assertEqual(validate_scenario(self.project, self.app_with(start="0.5", stop="10")), [])

    def test_packet_sizes_are_positive_exact_integers_with_portable_limit(self):
        for kind in ("udp", "tcp", "ping"):
            for size in (0, -1, True, 1.5, float("nan"), float("inf"), 10 ** 1000, 65508):
                with self.subTest(kind=kind, size=size):
                    self.assert_invalid(self.app_with(type=kind, size=size))
            for size in (1, 65507, "128", 128.0):
                with self.subTest(kind=kind, valid_size=size):
                    self.assertEqual(validate_scenario(self.project, self.app_with(type=kind, size=size)), [])

    def test_ping_interval_is_positive_and_finite(self):
        for value in (0, -1, True, 0.5e-9, 9223372037, float("nan"), float("inf"),
                      10 ** 1000, "nan", "inf"):
            with self.subTest(interval=value):
                self.assert_invalid(self.app_with(type="ping", interval=value))
        for value in (1e-9, .2, "0.2", 9223372036):
            self.assertEqual(validate_scenario(self.project, self.app_with(type="ping", interval=value)), [])

    def test_byte_counts_dscp_and_ports_are_exact_bounded_integers(self):
        for field, bad, good in (
            ("bytes", (0, -1, True, 1.5, float("nan"), float("inf"), 2 ** 64), (1, "4096", 2 ** 64 - 1)),
            ("dscp", (-1, 64, True, 46.5, float("nan"), float("inf")), (0, "46", 63)),
            ("dport", (0, 65536, True, 80.5, float("nan"), float("inf")), (1, "443", 65535)),
        ):
            for value in bad:
                with self.subTest(field=field, invalid=value):
                    self.assert_invalid(self.app_with(**{field: value}))
            for value in good:
                with self.subTest(field=field, valid=value):
                    self.assertEqual(validate_scenario(self.project, self.app_with(**{field: value})), [])

    def test_specified_application_rates_are_positive_and_finite(self):
        for kind in ("udp", "tcp"):
            for value in (0, "0Mbps", -1, True, float("nan"), float("inf"), 10 ** 1000, "9" * 400 + "Gbps"):
                with self.subTest(kind=kind, rate=value):
                    self.assert_invalid(self.app_with(type=kind, rate=value))

    def test_paced_rates_fit_integer_bit_rates_and_nanosecond_packet_timers(self):
        for kind in ("udp", "tcp"):
            for size, good, bad in ((1, "8000000000bps", "8000000001bps"),
                                    (1000, "8000Gbps", "8001Gbps")):
                with self.subTest(kind=kind, size=size):
                    self.assertEqual(validate_scenario(self.project, self.app_with(type=kind, size=size, rate=good)), [])
                    self.assert_invalid(self.app_with(type=kind, size=size, rate=bad))
            for rate in ("0.5bps", "8001Gbps"):
                scenario = self.app_with(type=kind, rate=rate)
                scenario["apps"][0].pop("size")
                with self.subTest(kind=kind, default_size_rate=rate):
                    self.assert_invalid(scenario)
            scenario = self.app_with(type=kind, rate="8000Gbps")
            scenario["apps"][0].pop("size")
            self.assertEqual(validate_scenario(self.project, scenario), [])
            self.assertEqual(validate_scenario(self.project, self.app_with(type=kind, rate="1bps")), [])

    def test_rate_parser_preserves_units_and_normalizes_bad_numbers(self):
        for value, expected in ((0, 0), ("0Mbps", 0), (5, 5e6), ("5", 5e6), ("5Mbps", 5e6), ("500kbps", 5e5),
                                ("1.5Gbps", 1.5e9), ("5bit/s", 5), ("5b/s", 5)):
            with self.subTest(value=value):
                self.assertEqual(parse_rate(value), expected)
        for value in (-1, "-1Mbps", True, False, None, [], {}, float("nan"), float("inf"),
                      float("-inf"), 2 ** 64, "18446744073709551616bps", 10 ** 1000,
                      "9" * 400 + "Gbps", "nan", "inf"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                parse_rate(value)

    def test_event_times_coordinates_and_duration_are_finite(self):
        event = {"action": "move", "device": "h0x0", "t": 1, "x": 0, "y": 0, "duration": 0}
        self.assertEqual(validate_scenario(self.project, {**self.scenario, "events": [event]}), [])
        for field in ("t", "x", "y", "duration"):
            for value in (True, float("nan"), float("inf"), 10 ** 1000, [], {}):
                with self.subTest(field=field, value=value):
                    self.assert_invalid({**self.scenario, "events": [{**event, field: value}]})
        for field, value in (("t", -1), ("t", 4), ("duration", -1)):
            self.assert_invalid({**self.scenario, "events": [{**event, field: value}]})

    def test_event_identifiers_and_link_parameters_have_safe_shapes(self):
        link = next(iter(self.project["links"]))
        for action, field in (("move", "device"), ("link_down", "link")):
            for value in ([], {}, 1, True):
                with self.subTest(action=action, value=value):
                    event = {"action": action, field: value, "t": 1, "x": 0, "y": 0}
                    self.assert_invalid({**self.scenario, "events": [event]})
        for value in ([], {}, True, 1):
            self.assert_invalid({**self.scenario, "events": [{"action": value, "link": link, "t": 1}]})
        for params in ([1], "params", True, 1):
            self.assert_invalid({**self.scenario, "events": [
                {"action": "link_params", "link": link, "t": 1, "params": params}
            ]})

    def test_event_link_delays_fit_native_timer_limits_in_each_supported_unit(self):
        link = next(iter(self.project["links"]))
        event = {"action": "link_params", "link": link, "t": 1}
        limit = 9223372036 - self.scenario["duration"]
        for field in ("delay", "jitter"):
            for value in ("9" * 400 + "ms", "nanms"):
                with self.subTest(field=field, invalid=value):
                    self.assert_invalid({**self.scenario, "events": [{**event, "params": {field: value}}]})
            for unit, factor in (("s", 1), ("ms", 1000), ("us", 1000000)):
                with self.subTest(field=field, unit=unit):
                    valid = {**event, "params": {field: f"{limit * factor}{unit}"}}
                    invalid = {**event, "params": {field: f"{(limit + 1) * factor}{unit}"}}
                    self.assertEqual(validate_scenario(self.project, {**self.scenario, "events": [valid]}), [])
                    self.assert_invalid({**self.scenario, "events": [invalid]})

    def test_move_coordinates_leave_room_for_interpolation_and_squared_distance(self):
        from lab.mobility import positions_at
        from lab.scenario_validation import MAX_POSITION
        events = [
            {"action": "move", "device": "h0x0", "t": 0, "x": MAX_POSITION, "y": 0, "duration": 0},
            {"action": "move", "device": "h0x0", "t": 1, "x": -MAX_POSITION, "y": 0, "duration": 1},
        ]
        scenario = {**self.scenario, "events": events}
        self.assertEqual(validate_scenario(self.project, scenario), [])
        self.assertEqual(positions_at(self.project, scenario, 1.5)["h0x0"], (0, 0))
        for coordinate in (1e308, -1e308):
            self.assert_invalid({**scenario, "events": [{**events[0], "x": coordinate}]})
        scaled = copy.deepcopy(self.project)
        scaled["meters_per_px"] = 10
        self.assertTrue(validate_scenario(scaled, scenario))

    def test_multicast_expansion_budget_is_checked_before_materializing_flows(self):
        from lab.apps import expand
        app = {"type": "mcast", "src": "h0x0", "dst": ["h2x0"] * 3,
               "group": "239.1.1.1", "rate": "1Mbps", "start": 0}
        with patch("lab.scenario_validation.MAX_EXPANDED_APPS", 2), patch("lab.apps.expand", wraps=expand) as expand_app:
            self.assert_invalid({**self.scenario, "apps": [app]})
            expand_app.assert_not_called()

    def test_validation_preserves_caller_data_and_accepts_examples(self):
        before = copy.deepcopy(self.project)
        self.assertEqual(validate_scenario(self.project, self.scenario), [])
        self.assertEqual(self.project, before)
        invalid = self.app_with(size=1.5)
        invalid_before = copy.deepcopy(invalid)
        self.assert_invalid(invalid)
        self.assertEqual(invalid, invalid_before)
        examples = Path(__file__).resolve().parents[1] / "examples"
        for path in sorted(examples.glob("*.json")):
            project = json.loads(path.read_text())
            original = copy.deepcopy(project)
            for name, scenario in project.get("scenarios", {}).items():
                with self.subTest(example=path.name, scenario=name):
                    self.assertEqual(validate_scenario(project, scenario), [])
            self.assertEqual(project, original)


class ScenarioApiValidationTests(unittest.TestCase):
    def test_invalid_scenarios_never_reach_engines_or_admission_storage(self):
        project = project_with_scenario()
        scenario = project["scenarios"]["test"]
        link = next(iter(project["links"]))
        cases = [
            {**scenario, "apps": [None]},
            {**scenario, "events": [None]},
            {**scenario, "duration": float("nan")},
            {**scenario, "duration": 10 ** 1000},
            {**scenario, "apps": [{**scenario["apps"][0], "id": []}]},
            {**scenario, "apps": [{**scenario["apps"][0], "size": -1}]},
            {**scenario, "apps": [{**scenario["apps"][0], "type": "ping", "interval": 0}]},
            {**scenario, "apps": [{**scenario["apps"][0], "stop": float("inf")}]},
            {**scenario, "apps": [{**scenario["apps"][0], "mcast": [], "mgroup": "239.1.1.1"}]},
            {**scenario, "apps": [{**scenario["apps"][0], "id": "v", "type": "voip"},
                                  {**scenario["apps"][0], "id": "v.up"}]},
            [scenario],
        ]
        with tempfile.TemporaryDirectory() as directory, patch("lab.engines.manager.RESULTS_DIR", directory), \
                patch("lab.engines.analytic.run") as engine, TestClient(create_app("demo", token="test")) as client:
            client.cookies.set("netlab_token", "test")
            for bad in cases:
                changed = copy.deepcopy(project)
                changed["scenarios"]["test"] = bad
                body = {"project": changed, "scenario": "test", "engine": "analytic", "repetitions": 2,
                        "param": {"kind": "link", "link": link, "field": "bw"}, "values": [2]}
                # The raw JSON path also exercises nonstandard NaN/Infinity accepted by the JSON decoder.
                content = json.dumps(body, allow_nan=True)
                for route in ("/api/runs", "/api/experiments", "/api/campaigns"):
                    with self.subTest(route=route, scenario=bad):
                        response = client.post(route, content=content, headers={"Content-Type": "application/json"})
                        self.assertEqual(response.status_code, 400, response.text)
                        self.assertFalse(client.app.state.runs.active)
                        self.assertEqual(client.app.state.runs.store.list(project["name"]), [])
                        self.assertEqual(client.app.state.runs.campaigns.store.list(project["name"])["total"], 0)
            engine.assert_not_called()


if __name__ == "__main__":
    unittest.main()
