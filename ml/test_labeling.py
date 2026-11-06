import unittest

import pandas as pd

from ml.labeling import label_events


class LabelTests(unittest.TestCase):
    def test_sweep_targets_run_boundaries_and_replies(self):
        events = pd.DataFrame([
            {"ts": 10, "ip_src": "a", "ip_dst": "subnet.1", "run_id": "one", "routing": "spf"},
            {"ts": 11, "ip_src": "subnet.2", "ip_dst": "a", "run_id": "one", "routing": "spf"},
            {"ts": 10, "ip_src": "a", "ip_dst": "subnet.1", "run_id": "other", "routing": "spf"},
            {"ts": 12, "ip_src": "a", "ip_dst": "subnet.1", "run_id": "one", "routing": "spf"},
            {"ts": 10, "ip_src": "a", "ip_dst": "unrelated", "run_id": "one", "routing": "spf"}])
        attacks = pd.DataFrame([{"ts": 10, "duration_sec": 1, "attacker_ip": "a", "target_ip": "subnet.3",
                                 "target_ips": '["subnet.1", "subnet.2"]', "type": "ping_sweep",
                                 "routing": "spf", "run_id": "one"}])
        self.assertEqual(label_events(events, attacks)["label"].tolist(),
                         ["ping_sweep", "ping_sweep", "normal", "normal", "normal"])

    def test_legacy_single_target(self):
        events = pd.DataFrame([{"ts": 1, "ip_src": "a", "ip_dst": "b"}])
        attacks = pd.DataFrame([{"ts": 1, "duration_sec": 0.1, "attacker_ip": "a", "target_ip": "b", "type": "scan"}])
        self.assertEqual(label_events(events, attacks).iloc[0]["label"], "scan")


if __name__ == "__main__":
    unittest.main()
