"""Counter sampling regressions without root or kernel tracing."""

import unittest
from unittest.mock import patch

from lab.telemetry.ebpf_tracker import EBPFTracker


class KernelCounterTests(unittest.TestCase):
    def setUp(self):
        with patch.object(EBPFTracker, '_check_ebpf_support', return_value=False):
            self.tracker = EBPFTracker()

    def sample(self, node, timeouts, retransmits):
        with patch.object(self.tracker, '_read_netstat', return_value={
                'TCPTimeouts': timeouts, 'TCPFastRetrans': retransmits}):
            return self.tracker.collect_kernel_drops(node_name=node)

    def test_interleaved_nodes_do_not_create_false_events(self):
        for _ in range(3):
            self.assertEqual(self.sample('h1', 100, 200), [])
            self.assertEqual(self.sample('h2', 10, 20), [])
        self.assertEqual(self.tracker.get_recent_drops(), [])

    def test_real_increment_is_not_hidden_by_other_nodes_high_counters(self):
        self.sample('h1', 1, 2)
        self.sample('h2', 100, 200)
        events = self.sample('h1', 2, 3)
        self.assertEqual([e['event_type'] for e in events], ['tcp_drop', 'tcp_retransmit'])
        self.assertEqual(events[0]['drop_reason'], 'RTO_TIMEOUT')
        self.assertEqual(self.sample('h2', 100, 200), [])
        self.assertEqual(self.sample('h1', 2, 3), [])

    def test_reset_rebases_only_affected_node(self):
        self.sample('h1', 100, 100)
        self.sample('h2', 5, 5)
        self.assertEqual(self.sample('h1', 0, 0), [])
        self.assertEqual(len(self.sample('h2', 6, 5)), 1)
        self.assertEqual(len(self.sample('h1', 0, 1)), 1)

    def test_failed_read_preserves_last_successful_baseline(self):
        self.sample('h1', 2, 3)
        with patch.object(self.tracker, '_read_netstat', return_value={}):
            self.assertEqual(self.tracker.collect_kernel_drops(node_name='h1'), [])
        self.assertEqual(self.sample('h1', 2, 3), [])
        self.assertEqual(len(self.sample('h1', 3, 3)), 1)

    def test_new_counter_is_not_mistaken_for_an_increment_from_zero(self):
        with patch.object(self.tracker, '_read_netstat', return_value={'TCPTimeouts': 2}):
            self.tracker.collect_kernel_drops(node_name='h1')
        self.assertEqual(self.sample('h1', 2, 100), [])
        self.assertEqual(len(self.sample('h1', 2, 101)), 1)


if __name__ == '__main__':
    unittest.main()
