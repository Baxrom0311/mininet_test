"""Native ns-3 smoke for preflight's empty topology and effective fast settings."""
import importlib.util
import os
import tempfile
import time
import unittest
from unittest.mock import patch
from fastapi.testclient import TestClient
from lab.model import empty_project
from lab.server import create_app
from lab.topogen import generate

@unittest.skipIf(os.environ.get('NETLAB_SKIP_NS3') == '1' or not (importlib.util.find_spec('ns') or importlib.util.find_spec('ns3')), 'ns-3 unavailable/disabled')
class NativePreflight(unittest.TestCase):
    def execute(self, project, scenario):
        with tempfile.TemporaryDirectory() as directory, patch('lab.engines.manager.RESULTS_DIR', directory), TestClient(create_app('demo')) as client:
            body = {'project': project, 'scenario': scenario, 'engine': 'ns3'}
            preview = client.post('/api/scenario/preflight', json=body)
            self.assertEqual(preview.status_code, 200, preview.text)
            self.assertTrue(preview.json()['ready'], preview.text)
            response = client.post('/api/runs', json=body)
            self.assertEqual(response.status_code, 200, response.text)
            rid = response.json()['run_id']
            deadline = time.monotonic() + 100
            while time.monotonic() < deadline:
                result = client.get(f"/api/runs/{project['name']}/{rid}").json()
                if result['status'] == 'done':
                    return preview.json(), result
                self.assertNotIn(result['status'], ('error','cancelled','interrupted'), result)
                time.sleep(.1)
            self.fail('native preflight run timed out')

    def test_empty_topology_executes_without_native_node_max_error(self):
        p = empty_project('empty_native')
        p['scenarios']['empty'] = {'duration': 1, 'apps': [], 'events': []}
        preview, result = self.execute(p, 'empty')
        self.assertEqual(preview['counts']['devices'], 0)
        self.assertEqual(result['flows'], {})

    def test_fast_ipv6_report_matches_executed_mode(self):
        p = generate('ring', routers=3, hosts=1, flows=1, ipv6=True)
        sc = p['scenarios']['asosiy_ipv6']
        sc.update(duration=3.5, ns3_fast=True)
        for app in sc['apps']:
            app.update(start=.5, stop=3.5)
        preview, result = self.execute(p, 'asosiy_ipv6')
        self.assertEqual(preview['effective']['mtu'], 9000)
        self.assertTrue(result['ns3_fast'])
        self.assertTrue(all(flow['summary']['avg_thr_bps'] > 0 for flow in result['flows'].values()))
