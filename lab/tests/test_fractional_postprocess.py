"""Fractional application/availability intervals represent time, not sample counts."""

import copy
import unittest

from lab.apps import _done_time, mos_from, postprocess
from lab.failures import flow_availability


def point(start, end, rate, *, delay=20., loss=0.):
    return {"t": end, "t_start": start, "t_end": end, "thr_bps": rate,
            "delay_ms": delay, "jitter_ms": 0., "loss_pct": loss}


class AvailabilityTests(unittest.TestCase):
    def flow(self, points, start=0, stop=1.25):
        return {"type": "udp", "start": start, "stop": stop, "series": points}

    def test_fractional_tail_is_weighted_by_seconds(self):
        availability, outages, observed = flow_availability(self.flow([point(0, 1, 100), point(1, 1.25, 0)]))
        self.assertEqual(availability, 80)
        self.assertEqual(observed, 1.25)
        self.assertEqual(outages, [{"start": 1, "end": 1.25, "duration_s": .25}])

    def test_active_time_clips_both_sample_edges(self):
        availability, outages, observed = flow_availability(self.flow(
            [point(0, .4, 0), point(.4, .8, 100), point(.8, 1.2, 0)], start=.2, stop=1))
        self.assertEqual(availability, 50)
        self.assertAlmostEqual(observed, .8)
        self.assertEqual([(o["start"], o["end"]) for o in outages], [(.2, .4), (.8, 1)])

    def test_gaps_are_unobserved_and_do_not_join_outages(self):
        availability, outages, observed = flow_availability(self.flow(
            [point(0, .25, 0), point(.5, .75, 0)], stop=1))
        self.assertEqual((availability, observed), (0, .5))
        self.assertEqual([o["duration_s"] for o in outages], [.25, .25])
        adjacent = flow_availability(self.flow([point(0, .25, 0), point(.25, .5, 0)], stop=1))
        self.assertEqual(adjacent[1], [{"start": 0, "end": .5, "duration_s": .5}])

    def test_legacy_timestamp_only_path_is_unchanged(self):
        flow = self.flow([{"t": 1, "thr_bps": 100}, {"t": 2, "thr_bps": 0}, {"t": 3, "thr_bps": 100}], stop=3)
        self.assertEqual(flow_availability(flow), (66.667, [{"start": 1, "end": 2, "duration_s": 1}], 3))

    def test_malformed_mixed_duplicate_and_overlapping_intervals_are_rejected(self):
        invalid = [[point(0, .5, 100), point(0, .5, 100)],
                   [point(0, .5, 100), point(.25, .75, 100)],
                   [point(0, .5, 100), {"t": 1, "thr_bps": 100}],
                   [{**point(0, .5, 100), "t_end": None}],
                   [point(0, float("nan"), 100)], [point(False, .5, 100)]]
        for points in invalid:
            with self.subTest(points=points), self.assertRaises(ValueError):
                flow_availability(self.flow(points))


class ApplicationTests(unittest.TestCase):
    def test_transfer_completion_does_not_allocate_bytes_to_missing_time(self):
        flow = {"start": 0, "stop": 3, "series": [point(0, .25, 800), point(2, 3, 800)]}
        # 98% of 100 bytes requires .98 s of observed 800 bit/s: .25 + .73 s.
        self.assertAlmostEqual(_done_time(flow, 100), 2.73)
        flow["series"] = [point(1, 2, 800)]
        flow.update(start=1.5, stop=2)
        self.assertIsNone(_done_time(flow, 100))

    def test_explicit_ftp_completion_is_propagated_to_app_summary(self):
        flow = {"group": "download", "start": 0, "stop": 3,
                "series": [point(0, .25, 800), point(2, 3, 800)]}
        scenario = {"apps": [{"id": "download", "type": "ftp", "src": "h0", "dst": "h1", "file": 100,
                              "start": 0, "stop": 3}]}
        result = postprocess({"duration": 3, "flows": {"download.xfer": flow}}, scenario)
        self.assertEqual(result["apps"]["download"]["transfer_s"], 2.73)
        self.assertTrue(result["apps"]["download"]["completed"])

    def test_legacy_transfer_delta_and_engine_completion_are_preserved(self):
        self.assertAlmostEqual(_done_time({"start": 0, "series": [{"t": 1, "thr_bps": 800}]}, 100), .98)
        self.assertEqual(_done_time({"done_t": .5, "series": []}, 100), .5)

    def voip(self, explicit=True):
        points = [point(0, 1, 100, delay=20, loss=0), point(1, 1.25, 0, delay=200, loss=100)]
        if not explicit:
            points = [{k: v for k, v in p.items() if k not in ("t_start", "t_end")} for p in points]
        flow = {"group": "call", "start": 0, "stop": 1.25, "series": points}
        sc = {"apps": [{"id": "call", "type": "voip", "src": "h0", "dst": "h1", "start": 0, "stop": 1.25}]}
        result = {"duration": 1.25, "flows": {"call.up": copy.deepcopy(flow), "call.down": copy.deepcopy(flow)}}
        return postprocess(result, sc)["apps"]["call"]

    def test_voip_summaries_weight_fractional_mos_delay_loss_and_throughput(self):
        summary = self.voip()
        first, tail = mos_from(20, 0, 0)[1], mos_from(200, 0, 100)[1]
        self.assertEqual(summary["avg_mos"], round(first * .8 + tail * .2, 2))
        self.assertEqual(summary["avg_delay_ms"], 56)
        self.assertEqual(summary["loss_pct"], 20)
        self.assertEqual(summary["avg_thr_bps"], 160)

    def test_legacy_voip_retains_arithmetic_sample_averages(self):
        summary = self.voip(False)
        first, tail = mos_from(20, 0, 0)[1], mos_from(200, 0, 100)[1]
        self.assertEqual(summary["avg_mos"], round((first + tail) / 2, 2))
        self.assertEqual(summary["avg_delay_ms"], 110)
        self.assertEqual(summary["loss_pct"], 50)
        self.assertEqual(summary["avg_thr_bps"], 100)

    def test_multicast_per_receiver_summaries_use_actual_intervals(self):
        flow = {"group": "stream", "series": [point(0, 1, 100), point(1, 1.25, 0)],
                "summary": {"avg_thr_bps": 999, "loss_pct": 0}}
        scenario = {"apps": [{"id": "stream", "type": "mcast", "src": "h0", "dst": "h1", "rate": "100bps"}]}
        result = postprocess({"duration": 1.25, "flows": {"stream.h1": flow}}, scenario)
        self.assertEqual(result["apps"]["stream"]["per_receiver"]["h1"]["avg_thr_bps"], 80)
        self.assertEqual(result["apps"]["stream"]["avg_thr_bps"], 80)

    def test_duplicate_transfer_intervals_cannot_complete_early(self):
        with self.assertRaises(ValueError):
            _done_time({"start": 0, "series": [point(0, .5, 800), point(0, .5, 800)]}, 100)


if __name__ == "__main__":
    unittest.main()
