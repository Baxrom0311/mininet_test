"""Prepare isolated parametric-study jobs without launching engines or networks."""

import copy
import tempfile
import unittest
from unittest.mock import Mock, patch

from lab.drivers.base import EventBus
from lab.drivers.demo_driver import DemoDriver
from lab.engines.manager import RunManager
from lab.engines.studies import prepare_study
from lab.topogen import generate


class StudyPreparationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        paths = patch("lab.engines.manager.RESULTS_DIR", self.temp.name)
        paths.start()
        self.addCleanup(paths.stop)
        bus = EventBus()
        self.manager = RunManager(bus, DemoDriver(bus))
        self.project = generate("ring", routers=3, hosts=1, flows=1)
        self.project["scenarios"] = {"test": {"duration": 1, "events": [], "apps": [
            {"id": "call", "type": "voip", "src": "h0x0", "dst": "h2x0", "codec": "g711", "start": 0, "stop": 1}]}}
        self.codec = {"kind": "app", "app": "call", "field": "codec"}
        self.link = next(iter(self.project["links"]))
        self.bandwidth = {"kind": "link", "link": self.link, "field": "bw"}

    def prepare(self, param=None, values=None, **kwargs):
        return prepare_study(self.manager, self.project, "test", "analytic",
                             self.codec if param is None else param,
                             ["g711", "g729"] if values is None else values, **kwargs)

    def test_late_invalid_codec_rejects_entire_plan_before_launch(self):
        with patch.object(self.manager, "_launch_worker") as launch, patch.object(self.manager, "_study_worker") as worker:
            with self.assertRaisesRegex(ValueError, "kodek"):
                self.prepare(values=["g711", "invalid-codec"])
            launch.assert_not_called()
            worker.assert_not_called()
        self.assertEqual(self.manager.active, {})

    def test_2d_variants_are_reattached_validated_and_isolated(self):
        original = copy.deepcopy(self.project)
        plan = self.prepare(param2=self.bandwidth, values2=[2, 4])
        self.assertEqual(plan.response()["runs"], 4)
        self.assertEqual(plan.response()["run_ids"], [job.run_id for job in plan.jobs])
        expected = [(0, 0, "g711", 2), (1, 0, "g729", 2), (0, 1, "g711", 4), (1, 1, "g729", 4)]
        for k, (job, (i, j, codec, bandwidth)) in enumerate(zip(plan.jobs, expected)):
            self.assertEqual(job.scenario["apps"][0]["codec"], codec)
            self.assertIs(job.project["scenarios"]["test"], job.scenario)
            self.assertEqual(job.project["links"][self.link]["params"]["bw"], bandwidth)
            self.assertEqual((job.extra["study_index"], job.extra["study_index2"]), (i, j))
            self.assertEqual((job.extra["study_position"], job.extra["study_total"]), (k, 4))
            self.assertEqual(job.extra["study_value"], codec)
            self.assertEqual(job.extra["study_value2"], bandwidth)
            self.assertEqual(job.resource_group, plan.study_id)
            self.assertEqual(job.resource_parallelism, 1)
            self.assertTrue(job.run_id.endswith(f"-v{k + 1:02d}"))
        plan.jobs[0].scenario["apps"][0]["codec"] = "changed"
        plan.jobs[0].extra["study_param"]["label"] = "changed"
        self.assertEqual(plan.jobs[1].scenario["apps"][0]["codec"], "g729")
        self.assertNotEqual(plan.jobs[1].extra["study_param"]["label"], "changed")
        self.assertEqual(self.project, original)

    def test_failure_mutations_are_in_recorded_scenario(self):
        self.project["scenarios"]["test"]["failures"] = {
            "seed": 1, "links": {"targets": [self.link], "mtbf_s": 10, "mttr_s": 1}}
        plan = self.prepare(param={"kind": "failure", "group": "links", "field": "mttr_s"}, values=[2, 3])
        for value, job in zip((2, 3), plan.jobs):
            self.assertEqual(job.scenario["failures"]["links"]["mttr_s"], value)
            self.assertEqual(job.project["scenarios"]["test"]["failures"]["links"]["mttr_s"], value)

    def test_late_invalid_router_timer_rejects_entire_plan(self):
        with self.assertRaisesRegex(ValueError, "rip_timers"):
            self.prepare(param={"kind": "router", "router": "r0", "field": "rip_timeout"}, values=[180, 1])

    def test_all_mutated_variants_pass_through_prepare(self):
        original = self.manager._prepare
        codecs = []
        def validate(project, name, engine):
            codecs.append(project["scenarios"][name]["apps"][0]["codec"])
            return original(project, name, engine)
        with patch.object(self.manager, "_prepare", side_effect=validate):
            self.prepare()
        self.assertEqual(codecs, ["g711", "g711", "g729"])

    def test_ns3_group_limit_is_selected_synchronously(self):
        with patch("lab.engines.manager._ns3_available", return_value=True), \
                patch.object(self.manager, "study_parallelism", return_value=3) as parallel:
            plan = prepare_study(self.manager, self.project, "test", "ns3", self.codec, ["g711", "g729"])
        parallel.assert_called_once_with("ns3")
        self.assertEqual({job.resource_parallelism for job in plan.jobs}, {3})

    def test_axis_bounds_and_cartesian_limit(self):
        invalid = ([], ["g711"], ["g711"] * 26, ("g711", "g729"), "g711,g729", {"a": 1})
        for values in invalid:
            with self.subTest(values=values), self.assertRaises(ValueError):
                self.prepare(values=values)
        plan = self.prepare(self.bandwidth, [1] * 25)
        self.assertEqual(len(plan.jobs), 25)
        with self.assertRaisesRegex(ValueError, "49"):
            self.prepare(self.bandwidth, [1] * 7, param2=self.codec, values2=["g711"] * 8)
        for values2 in (None, [1], [1] * 26, "1,2"):
            with self.subTest(values2=values2), self.assertRaises(ValueError):
                self.prepare(param2=self.bandwidth, values2=values2)
        with self.assertRaisesRegex(ValueError, "bir xil"):
            self.prepare(param2=self.codec, values2=["g711", "g729"])
        with self.assertRaisesRegex(ValueError, "requires"):
            self.prepare(values2=[1, 2])

    def test_bad_scalar_values_fail_before_parameter_conversion(self):
        for value in (True, False, None, {}, [], float("nan"), float("inf"), 10**500, "NaN", "inf", "1e999", " "):
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.prepare(self.bandwidth, [1, value])

    def test_study_packet_size_requires_positive_integer_without_truncation(self):
        app = self.project["scenarios"]["test"]["apps"][0]
        app.update(type="udp", rate="1Mbps")
        param = {"kind": "app", "app": "call", "field": "size"}
        for size in (0, -1, 1.5, "0", "-1", "1.5", "64B"):
            with self.subTest(size=size), self.assertRaisesRegex(ValueError, "positive integer"):
                self.prepare(param, [64, size])
        plan = self.prepare(param, ["64", "128.0", 256])
        self.assertEqual([job.scenario["apps"][0]["size"] for job in plan.jobs], [64, 128, 256])
        self.assertEqual([job.extra["study_value"] for job in plan.jobs], ["64", "128.0", 256])
        with self.assertRaisesRegex(ValueError, "positive integer"):
            self.prepare(self.bandwidth, [1, 2], param2=param, values2=[64, -1])

    def test_malformed_or_unknown_parameters_are_value_errors(self):
        invalid = ("link", [], True, {}, {"kind": "link", "field": "bw"},
                   {"kind": [], "field": "bw"}, {"kind": "unknown", "field": "bw"},
                   {**self.bandwidth, "field": "unknown"}, {**self.bandwidth, "typo": 1},
                   {"kind": "router", "router": "r0", "field": "rip_update", "all": "yes"})
        for param in invalid:
            with self.subTest(param=param), self.assertRaises(ValueError):
                self.prepare(param, [1, 2])

    def test_malformed_project_and_scenario_are_value_errors(self):
        for project, name, engine in (([1], "test", "analytic"), ({}, "test", "analytic"),
                                      (self.project, [], "analytic"), (self.project, "test", 123)):
            with self.subTest(project=project, name=name), self.assertRaises(ValueError):
                prepare_study(self.manager, project, name, engine, self.codec, ["g711", "g729"])

    def test_legacy_runner_preserves_start_done_events_and_response(self):
        class ImmediateThread:
            def __init__(self, target, **kwargs):
                self.target = target
            def start(self):
                self.target()
        def worker(*args):
            self.manager.active.pop(args[0], None)
        with patch("lab.engines.manager.threading.Thread", ImmediateThread), \
                patch.object(self.manager, "_study_worker", side_effect=worker) as run, \
                patch.object(self.manager.bus, "publish") as publish:
            response = self.manager.start_study(self.project, "test", "analytic", self.codec, ["g711", "g729"])
        self.assertEqual(response["runs"], 2)
        self.assertEqual(len(response["run_ids"]), 2)
        self.assertEqual(run.call_count, 2)
        events = [call.args[0] for call in publish.call_args_list]
        self.assertEqual([event["index"] for event in events if "index" in event], [0, 1])
        self.assertTrue(events[-1]["done"])
        self.assertEqual(events[-1]["total"], 2)
        self.assertTrue(all(event["study_id"] == response["study_id"] for event in events))
        self.assertEqual(self.manager.active, {})


if __name__ == "__main__":
    unittest.main()
